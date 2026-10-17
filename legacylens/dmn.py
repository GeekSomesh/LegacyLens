"""Export extracted rules as a DMN 1.3 decision model, and evaluate it.

The model has one input data element per input field, one decision per
derived field (a FEEL literal expression that keeps COBOL's truncation), and
one decision table for the program's decisions (hit policy FIRST, one row per
disjunct of each rule, conditions as boolean input columns).

The evaluator below implements the FEEL subset this exporter writes, so the
exported file itself can be run against the compiled program.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from decimal import ROUND_DOWN, Decimal

from . import cobol as C
from . import rules as R

NS = "https://www.omg.org/spec/DMN/20191111/MODEL/"
FEEL_NS = "https://www.omg.org/spec/DMN/20191111/FEEL/"


def feel_name(name: str) -> str:
    return name.replace("-", "_")


class Unexportable(Exception):
    pass


# ---------------------------------------------------------------- COBOL -> FEEL

def fexpr(e, prog) -> str:
    if isinstance(e, C.Lit):
        v = e.value
        if isinstance(v, C.Fig):
            if v.kind == "ZERO":
                return "0"
            if v.kind == "SPACE":
                return '""'
            raise Unexportable(v.kind)
        if isinstance(v, Decimal):
            return str(v)
        return '"' + str(v).rstrip().replace('"', '\\"') + '"'
    if isinstance(e, C.Ref):
        if e.subs or e.refmod:
            raise Unexportable("subscripts")
        return feel_name(e.name)
    if isinstance(e, C.Neg):
        return f"-({fexpr(e.expr, prog)})"
    if isinstance(e, C.BinOp):
        if e.op == "**":
            raise Unexportable("power")
        return f"({fexpr(e.left, prog)} {e.op} {fexpr(e.right, prog)})"
    raise Unexportable(type(e).__name__)


OPS = {"=": "=", "<>": "!=", "<": "<", ">": ">", "<=": "<=", ">=": ">="}


def fcond(c, prog) -> str:
    if isinstance(c, C.Rel):
        return f"{fexpr(c.left, prog)} {OPS[c.op]} {fexpr(c.right, prog)}"
    if isinstance(c, C.CondRef):
        cn = prog.conds[c.name]
        f = feel_name(cn.field)
        parts = []
        for lo, hi in cn.values:
            lo_t = fexpr(C.Lit(lo, str(lo)), prog)
            hi_t = fexpr(C.Lit(hi, str(hi)), prog)
            parts.append(f"{f} = {lo_t}" if lo == hi else f"({f} >= {lo_t} and {f} <= {hi_t})")
        return "(" + " or ".join(parts) + ")"
    if isinstance(c, C.Not):
        return f"not({fcond(c.cond, prog)})"
    if isinstance(c, C.And):
        return f"({fcond(c.left, prog)} and {fcond(c.right, prog)})"
    if isinstance(c, C.Or):
        return f"({fcond(c.left, prog)} or {fcond(c.right, prog)})"
    raise Unexportable(type(c).__name__)


def store_expr(f: C.Field, expr: str) -> str:
    """FEEL for COBOL's store: truncate the fraction, drop high-order digits, drop the sign if unsigned."""
    pic = f.pic
    scale = 10 ** pic.scale
    mod = 10 ** pic.digits
    inner = f"({expr}) * {scale}" if pic.scale else f"({expr})"
    trunc = f"(if {inner} >= 0 then floor({inner}) else ceiling({inner}))"
    mag = f"modulo(abs({trunc}), {mod * scale})"
    if pic.signed:
        val = f"(if {inner} >= 0 then {mag} else -{mag})"
    else:
        val = mag
    return f"({val}) / {scale}" if pic.scale else val


def derivation(s, prog) -> list:
    """[(field, FEEL expression)] for one derivation statement, in order."""
    if isinstance(s, (C.Move, C.Compute)):
        src = s.source if isinstance(s, C.Move) else s.expr
        out = []
        for t in s.targets:
            f = prog.fields[t.name]
            if f.pic is None or t.subs or t.refmod:
                raise Unexportable("group or subscripted target")
            e = fexpr(src, prog)
            out.append((t.name, store_expr(f, e) if f.pic.kind == "num" else e))
        return out
    if isinstance(s, C.If):
        then = {k: v for x in s.then for k, v in derivation(x, prog)}
        els = {k: v for x in s.else_ for k, v in derivation(x, prog)}
        cond = fcond(s.cond, prog)
        out = []
        for k in list(dict.fromkeys(list(then) + list(els))):
            out.append((k, f"if {cond} then {then.get(k, feel_name(k))} else {els.get(k, feel_name(k))}"))
        return out
    raise Unexportable(type(s).__name__)


def dnf(c) -> list:
    """Disjunctive normal form: list of conjunctions of (atom, polarity)."""
    if isinstance(c, C.And):
        return [a + b for a in dnf(c.left) for b in dnf(c.right)]
    if isinstance(c, C.Or):
        return dnf(c.left) + dnf(c.right)
    if isinstance(c, C.Not):
        inner = c.cond
        if isinstance(inner, (C.And, C.Or, C.Not)):
            return dnf(R.negate(inner))
        return [[(inner, False)]]
    return [[(c, True)]]


# ---------------------------------------------------------------- export

def export(prog: C.Program, rules: list, kept_index: dict | None = None) -> str:
    """DMN 1.3 XML for a whole-program rule set. kept_index: rule id -> indexes of
    the conditions to keep (z3 simplification); defaults to all conditions."""
    dec_fields = sorted({k for r in rules for k in r.actions})
    inputs, derived, seen = [], [], set()
    for r in rules:
        for kind, *rest in r.steps:
            if kind == "accept":
                g = prog.fields[rest[0].target.name]
                for k in R.descendants(prog, g.key):
                    f = prog.fields[k]
                    if f.pic is not None and k not in inputs:
                        inputs.append(k)
            elif kind == "exec":
                for name, e in derivation(rest[0], prog):
                    if name not in dec_fields and (name, e) not in seen:
                        seen.add((name, e))
                        derived.append((name, e))
    for r in rules:                      # input fields read but never accepted (parameters)
        for c, _, _ in r.conditions:
            for n in R.refs_in(c):
                f = prog.fields.get(n) or prog.fields.get(prog.conds[n].field if n in prog.conds else "")
                if f is not None and f.key not in inputs and all(f.key != d for d, _ in derived):
                    if f.section in ("linkage", "file") or f.value is None:
                        inputs.append(f.key)
    constants = []
    for r in rules:
        for c, _, _ in r.conditions:
            for n in R.refs_in(c):
                f = prog.fields.get(n)
                if f is not None and f.value is not None and f.key not in inputs and f.key not in constants \
                        and all(f.key != d for d, _ in derived):
                    constants.append(f.key)

    ET.register_namespace("", NS)
    root = ET.Element(f"{{{NS}}}definitions", {"id": f"defs_{feel_name(prog.name)}", "name": prog.name,
                                               "namespace": f"https://legacylens.example/{prog.name}",
                                               "expressionLanguage": FEEL_NS})
    def el(parent, tag, **attrs):
        return ET.SubElement(parent, f"{{{NS}}}{tag}", attrs)

    def typ(k):
        f = prog.fields[k]
        return "number" if f.pic is not None and f.pic.kind == "num" else "string"

    for k in inputs:
        n = feel_name(k)
        d = el(root, "inputData", id=f"in_{n}", name=n)
        el(d, "variable", id=f"in_{n}_v", name=n, typeRef=typ(k))
    for k in constants:
        n = feel_name(k)
        d = el(root, "decision", id=f"c_{n}", name=n)
        el(d, "variable", id=f"c_{n}_v", name=n, typeRef=typ(k))
        le = el(d, "literalExpression", id=f"c_{n}_e")
        ET.SubElement(le, f"{{{NS}}}text").text = fexpr(C.Lit(prog.fields[k].value, str(prog.fields[k].value)), prog)
    # derived decisions; repeated names (sequential assignments) get numbered versions
    version, order = {}, []
    for name, e in derived:
        n = feel_name(name)
        version[n] = version.get(n, 0) + 1
        order.append((n, e))
    for idx, (n, e) in enumerate(order):
        d = el(root, "decision", id=f"d_{n}_{idx}", name=n)
        el(d, "variable", id=f"d_{n}_{idx}_v", name=n, typeRef="number")
        for ref in sorted(set(re.findall(r"[A-Za-z][A-Za-z0-9_]*", e)) - {"if", "then", "else", "and", "or", "not",
                                                                          "floor", "ceiling", "modulo", "abs"}):
            kind = "requiredInput" if any(feel_name(k) == ref for k in inputs) else "requiredDecision"
            prefix = "in" if kind == "requiredInput" else ("c" if any(feel_name(k) == ref for k in constants) else "d")
            if prefix == "d":
                target = next((f"d_{n2}_{j}" for j, (n2, _) in enumerate(order) if n2 == ref and j < idx), None)
                if target is None:
                    continue
            else:
                target = f"{prefix}_{ref}"
            ir = el(d, "informationRequirement", id=f"ir_{n}_{idx}_{ref}")
            el(ir, kind, href=f"#{target}")
        le = el(d, "literalExpression", id=f"d_{n}_{idx}_e")
        ET.SubElement(le, f"{{{NS}}}text").text = e

    dec = el(root, "decision", id="policy", name=f"{prog.name} decision")
    for k in inputs:
        ir = el(dec, "informationRequirement", id=f"ir_policy_{feel_name(k)}")
        el(ir, "requiredInput", href=f"#in_{feel_name(k)}")
    last = {}
    for idx, (n, _) in enumerate(order):
        last[n] = idx
    for n, idx in last.items():
        ir = el(dec, "informationRequirement", id=f"ir_policy_d_{n}")
        el(ir, "requiredDecision", href=f"#d_{n}_{idx}")
    for k in constants:
        ir = el(dec, "informationRequirement", id=f"ir_policy_c_{feel_name(k)}")
        el(ir, "requiredDecision", href=f"#c_{feel_name(k)}")
    table = el(dec, "decisionTable", id="policy_table", hitPolicy="FIRST")
    atoms, rows = [], []
    for r in rules:
        conds = r.conditions
        keep = kept_index.get(r.id, range(len(conds))) if kept_index else range(len(conds))
        conj_c = None
        for i in keep:
            c, holds, _ = conds[i]
            c = c if holds else R.negate(c)
            conj_c = c if conj_c is None else C.And(conj_c, c)
        disjuncts = dnf(conj_c) if conj_c is not None else [[]]
        for conj in disjuncts:
            entry = {}
            for atom, pol in conj:
                text = fcond(atom, prog)
                if text not in atoms:
                    atoms.append(text)
                entry[text] = pol
            rows.append((r, entry))
    for i, text in enumerate(atoms):
        inp = el(table, "input", id=f"col{i}", label=text)
        ie = el(inp, "inputExpression", id=f"col{i}_e", typeRef="boolean")
        ET.SubElement(ie, f"{{{NS}}}text").text = text
    for f in dec_fields:
        el(table, "output", id=f"out_{feel_name(f)}", name=feel_name(f), typeRef=typ(f))
    for j, (r, entry) in enumerate(rows):
        row = el(table, "rule", id=f"row{j}")
        ET.SubElement(row, f"{{{NS}}}description").text = f"{r.id} (COBOL lines {', '.join(map(str, r.lines))})"
        for i, text in enumerate(atoms):
            ie = el(row, "inputEntry", id=f"row{j}_in{i}")
            ET.SubElement(ie, f"{{{NS}}}text").text = "-" if text not in entry else ("true" if entry[text] else "false")
        for f in dec_fields:
            oe = el(row, "outputEntry", id=f"row{j}_out_{feel_name(f)}")
            v = str(r.actions.get(f, "")).strip()
            ET.SubElement(oe, f"{{{NS}}}text").text = v if typ(f) == "number" else '"' + v + '"'
    ET.indent(root)
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


# ---------------------------------------------------------------- FEEL subset evaluator

TOK = re.compile(r"\s*(?:(\d+(?:\.\d+)?)|(\"(?:[^\"\\\\]|\\\\.)*\")|([A-Za-z_][A-Za-z0-9_]*)|(!=|<=|>=|[-+*/()=<>,]))")


class Feel:
    def __init__(self, text: str, env: dict):
        self.toks = []
        pos = 0
        text = text.strip()
        while pos < len(text):
            m = TOK.match(text, pos)
            if not m or m.end() == pos:
                raise ValueError(f"FEEL: cannot read {text[pos:pos + 20]!r}")
            num, s, name, op = m.groups()
            self.toks.append(("num", Decimal(num)) if num else ("str", s[1:-1]) if s else
                             ("name", name) if name else ("op", op))
            pos = m.end()
        self.i, self.env = 0, env

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else (None, None)

    def take(self, value=None):
        t = self.peek()
        if value is not None and t[1] != value:
            raise ValueError(f"FEEL: expected {value}, found {t[1]}")
        self.i += 1
        return t

    def run(self):
        v = self.expr()
        if self.i != len(self.toks):
            raise ValueError("FEEL: trailing input")
        return v

    def expr(self):
        if self.peek() == ("name", "if"):
            self.take()
            c = self.expr()
            self.take("then")
            a = self.expr()
            self.take("else")
            b = self.expr()
            return a if c is True else b
        return self.disj()

    def disj(self):
        v = self.conj()
        while self.peek() == ("name", "or"):
            self.take()
            w = self.conj()
            v = v is True or w is True
        return v

    def conj(self):
        v = self.comp()
        while self.peek() == ("name", "and"):
            self.take()
            w = self.comp()
            v = v is True and w is True
        return v

    def comp(self):
        a = self.add()
        t = self.peek()
        if t[0] == "op" and t[1] in ("=", "!=", "<", ">", "<=", ">="):
            self.take()
            b = self.add()
            if a is None or b is None:
                return None
            return {"=": a == b, "!=": a != b, "<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b}[t[1]]
        return a

    def add(self):
        v = self.mul()
        while self.peek() in (("op", "+"), ("op", "-")):
            op = self.take()[1]
            w = self.mul()
            v = None if v is None or w is None else (v + w if op == "+" else v - w)
        return v

    def mul(self):
        v = self.unary()
        while self.peek() in (("op", "*"), ("op", "/")):
            op = self.take()[1]
            w = self.unary()
            if v is None or w is None or (op == "/" and w == 0):
                v = None
            else:
                v = v * w if op == "*" else v / w
        return v

    def unary(self):
        if self.peek() == ("op", "-"):
            self.take()
            v = self.unary()
            return None if v is None else -v
        return self.primary()

    def primary(self):
        kind, val = self.take()
        if kind == "num" or kind == "str":
            return val
        if kind == "op" and val == "(":
            v = self.expr()
            self.take(")")
            return v
        if kind == "name":
            if val in ("true", "false"):
                return val == "true"
            if val == "not":
                self.take("(")
                v = self.expr()
                self.take(")")
                return not v
            if self.peek() == ("op", "("):
                self.take()
                args = [self.expr()]
                while self.peek() == ("op", ","):
                    self.take()
                    args.append(self.expr())
                self.take(")")
                if any(x is None for x in args):
                    return None
                if val == "floor":
                    return args[0].to_integral_value(rounding="ROUND_FLOOR")
                if val == "ceiling":
                    return args[0].to_integral_value(rounding="ROUND_CEILING")
                if val == "abs":
                    return abs(args[0])
                if val == "modulo":
                    a, b = args
                    return a - b * (a / b).to_integral_value(rounding="ROUND_FLOOR")
                raise ValueError(f"FEEL: unknown function {val}")
            return self.env[val]
        raise ValueError(f"FEEL: unexpected {val}")


def evaluate(xml_text: str, inputs: dict) -> dict:
    """Run the exported model: inputs are FEEL names -> values; returns the table's outputs."""
    root = ET.fromstring(xml_text)
    q = lambda tag: f"{{{NS}}}{tag}"
    env = dict(inputs)
    for d in root.findall(q("decision")):
        le = d.find(q("literalExpression"))
        if le is not None:
            env[d.get("name")] = Feel(le.find(q("text")).text, env).run()
    table = root.find(f"{q('decision')}/{q('decisionTable')}")
    cols = [Feel(i.find(f"{q('inputExpression')}/{q('text')}").text, env).run() for i in table.findall(q("input"))]
    outs = [o.get("name") for o in table.findall(q("output"))]
    for row in table.findall(q("rule")):
        ok = True
        for col, entry in zip(cols, row.findall(q("inputEntry"))):
            t = entry.find(q("text")).text.strip()
            if t != "-" and (col is True) != (t == "true"):
                ok = False
                break
        if ok:
            return {name: Feel(o.find(q("text")).text, env).run()
                    for name, o in zip(outs, row.findall(q("outputEntry")))}
    return {}
