"""Decision rules from a parsed COBOL program, and an engine that runs them.

A rule is one path through the decision logic: the conditions tested on the
way (with the outcome each must have) and the values written to the decision
fields. Conditional code that only computes working fields (no decision field
written) is kept as a derivation, not split into separate rules.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from . import cobol as C
from .numeric import display, store

MAX_PATHS = 5000

# ---------------------------------------------------------------- text


def expr_text(e) -> str:
    if isinstance(e, C.Lit):
        return e.text
    if isinstance(e, C.Ref):
        return e.name
    if isinstance(e, C.Neg):
        return f"-{expr_text(e.expr)}"
    if isinstance(e, C.BinOp):
        def side(x):
            t = expr_text(x)
            return f"({t})" if isinstance(x, C.BinOp) and x.op in "+-" and e.op in "*/" else t
        return f"{side(e.left)} {e.op} {side(e.right)}"
    return "?"


NEG_OP = {"=": "<>", "<>": "=", "<": ">=", ">": "<=", "<=": ">", ">=": "<"}


def negate(c):
    if isinstance(c, C.Rel):
        return C.Rel(NEG_OP[c.op], c.left, c.right)
    if isinstance(c, C.Not):
        return c.cond
    if isinstance(c, C.And):
        return C.Or(negate(c.left), negate(c.right))
    if isinstance(c, C.Or):
        return C.And(negate(c.left), negate(c.right))
    return C.Not(c)


def cond_text(c, top: bool = True) -> str:
    if isinstance(c, C.Rel):
        return f"{expr_text(c.left)} {c.op} {expr_text(c.right)}"
    if isinstance(c, C.CondRef):
        return c.name
    if isinstance(c, C.Not):
        return f"NOT {cond_text(c.cond, False)}"
    if isinstance(c, (C.And, C.Or)):
        word = "AND" if isinstance(c, C.And) else "OR"
        def side(x):
            return cond_text(x, type(x) is type(c))
        t = f"{side(c.left)} {word} {side(c.right)}"
        return t if top or isinstance(c, C.And) else f"({t})"
    return "?"


def stmt_text(s) -> str:
    if isinstance(s, C.Move):
        return "; ".join(f"{t} = {expr_text(s.source)}" for t in s.targets)
    if isinstance(s, C.Compute):
        return "; ".join(f"{t} = {expr_text(s.expr)}" for t in s.targets)
    if isinstance(s, C.If):
        then = "; ".join(stmt_text(x) for x in s.then)
        out = f"if {cond_text(s.cond)}: {then}"
        if s.else_:
            out += " else: " + "; ".join(stmt_text(x) for x in s.else_)
        return out
    if isinstance(s, C.Evaluate):
        parts = [f"when {cond_text(w.cond)}: " + "; ".join(stmt_text(x) for x in w.stmts)
                 for w in s.whens]
        if s.other:
            parts.append("otherwise: " + "; ".join(stmt_text(x) for x in s.other))
        return " | ".join(parts)
    return type(s).__name__


# ---------------------------------------------------------------- analysis helpers


def walk_stmts(stmts):
    for s in stmts:
        yield s
        if isinstance(s, C.If):
            yield from walk_stmts(s.then)
            yield from walk_stmts(s.else_)
        elif isinstance(s, C.Evaluate):
            for w in s.whens:
                yield from walk_stmts(w.stmts)
            if s.other:
                yield from walk_stmts(s.other)
        elif isinstance(s, C.Perform):
            yield from walk_stmts(s.body)


def refs_in(node) -> set:
    out = set()
    if isinstance(node, C.Ref):
        out.add(node.name)
    elif isinstance(node, C.CondRef):
        out.add(node.name)
    elif isinstance(node, (C.BinOp, C.Rel, C.And, C.Or)):
        out |= refs_in(node.left) | refs_in(node.right)
    elif isinstance(node, (C.Neg,)):
        out |= refs_in(node.expr)
    elif isinstance(node, C.Not):
        out |= refs_in(node.cond)
    return out


def decision_fields(prog: C.Program) -> list:
    """Fields given a literal inside a conditional branch and never read by a
    condition or a computation: the program's decisions."""
    assigned, read = {}, set()
    for p in prog.paragraphs:
        for s in walk_stmts(p.stmts):
            if isinstance(s, (C.If, C.Evaluate)):
                branches = [s.then, s.else_] if isinstance(s, C.If) else \
                    [w.stmts for w in s.whens] + [s.other or []]
                for b in branches:
                    for x in walk_stmts(b):
                        if isinstance(x, C.Move) and isinstance(x.source, C.Lit):
                            for t in x.targets:
                                assigned.setdefault(t, x.line)
                if isinstance(s, C.If):
                    read |= refs_in(s.cond)
                else:
                    for w in s.whens:
                        read |= refs_in(w.cond)
            elif isinstance(s, C.Compute):
                read |= refs_in(s.expr)
            elif isinstance(s, C.Move):
                read |= refs_in(s.source)
            elif isinstance(s, C.Perform) and s.until is not None:
                read |= refs_in(s.until)
    for name in list(read):
        if name in prog.conds:
            read.add(prog.conds[name].field)
    return sorted((f for f in assigned if f not in read), key=lambda f: assigned[f])


def writes_decision(stmts, prog, dec: set) -> bool:
    for s in walk_stmts(stmts):
        if isinstance(s, (C.Move, C.Compute)) and dec & set(s.targets):
            return True
        if isinstance(s, C.Perform) and s.target and \
                writes_decision(perform_body(prog, s), prog, dec):
            return True
    return False


def perform_body(prog: C.Program, s: C.Perform) -> list:
    if s.target is None:
        return s.body
    names = [p.name for p in prog.paragraphs]
    i = names.index(s.target)
    j = names.index(s.thru) if s.thru else i
    out = []
    for p in prog.paragraphs[i:j + 1]:
        out.extend(p.stmts)
    return out


def find_entry(prog: C.Program, dec: set) -> str:
    """The paragraph run once per record: the target of a looping PERFORM that
    reaches the decision logic; otherwise the first paragraph."""
    for p in prog.paragraphs:
        for s in walk_stmts(p.stmts):
            if isinstance(s, C.Perform) and s.target and (s.times is not None or s.until is not None):
                if writes_decision(perform_body(prog, s), prog, dec):
                    return s.target
    return prog.paragraphs[0].name


# ---------------------------------------------------------------- rules


@dataclass
class Rule:
    id: str
    steps: list                     # ("accept", name) | ("exec", stmt) | ("cond", cond, bool, line)
    actions: dict                   # decision field -> value
    action_lines: list
    notes: list = field(default_factory=list)

    @property
    def conditions(self) -> list:
        return [(c, holds, line) for kind, *rest in self.steps if kind == "cond"
                for c, holds, line in [rest]]

    @property
    def lines(self) -> list:
        return sorted({line for _, _, line in self.conditions} | set(self.action_lines))

    def condition_texts(self) -> list:
        return [cond_text(c if holds else negate(c)) for c, holds, _ in self.conditions]

    def to_dict(self, prog: C.Program) -> dict:
        derived = []
        for kind, *rest in self.steps:
            if kind == "exec" and not (set(getattr(rest[0], "targets", [])) & set(self.actions)):
                s = rest[0]
                derived.append({"text": stmt_text(s), "line": s.line})
        conds = []
        for c, holds, line in self.conditions:
            shown = c if holds else negate(c)
            entry = {"text": cond_text(shown), "line": line, "tested": cond_text(c), "outcome": holds}
            names = refs_in(c) & set(prog.conds)
            if names:
                entry["meaning"] = "; ".join(cond_name_text(prog.conds[n]) for n in sorted(names))
            conds.append(entry)
        return {"id": self.id, "actions": {k: str(v).strip() for k, v in self.actions.items()},
                "conditions": conds, "derived": derived, "lines": self.lines,
                "notes": self.notes}


def cond_name_text(cn: C.CondName) -> str:
    parts = []
    for lo, hi in cn.values:
        parts.append(f"{lo}" if lo == hi else f"{lo} THRU {hi}")
    return f"{cn.name} means {cn.field} is " + " or ".join(parts)


@dataclass
class _Path:
    steps: list
    actions: dict
    action_lines: list
    notes: list
    stopped: bool = False

    def extend(self, step=None, **kw):
        p = _Path(self.steps + ([step] if step else []), dict(self.actions),
                  list(self.action_lines), list(self.notes), self.stopped)
        for k, v in kw.items():
            setattr(p, k, v)
        return p


class Extractor:
    def __init__(self, prog: C.Program):
        self.prog = prog
        self.dec = set(decision_fields(prog))
        self.entry = find_entry(prog, self.dec)

    def run(self) -> list:
        paths = self.walk(self.prog.paragraph(self.entry).stmts, [_Path([], {}, [], [])], depth=0)
        rules = []
        for p in paths:
            if not p.actions:
                continue
            rules.append(Rule(f"RULE-{len(rules) + 1:02d}", p.steps, p.actions, p.action_lines, p.notes))
        return rules

    def walk(self, stmts, paths, depth):
        for s in stmts:
            nxt = []
            for p in paths:
                nxt.extend([p] if p.stopped else self.expand(s, p, depth))
            paths = nxt
            if len(paths) > MAX_PATHS:
                raise RuntimeError(f"more than {MAX_PATHS} paths")
        return paths

    def expand(self, s, p, depth):
        if isinstance(s, C.Accept):
            return [p.extend(("accept", s.target))]
        if isinstance(s, C.Display):
            return [p.extend(("display", s))]
        if isinstance(s, C.Continue):
            return [p]
        if isinstance(s, C.Stop):
            return [p.extend(stopped=True)]
        if isinstance(s, C.Unsupported):
            q = p.extend(("unsupported", s.text, s.line))
            q.notes.append(f"line {s.line}: unsupported statement, rule needs review")
            return [q]
        if isinstance(s, (C.Move, C.Compute)):
            q = p.extend(("exec", s))
            hits = self.dec & set(s.targets)
            if hits:
                for t in hits:
                    f = self.prog.fields[t]
                    if isinstance(s, C.Move) and isinstance(s.source, C.Lit):
                        q.actions[t] = store(f.pic, s.source.value)
                    else:
                        q.actions[t] = f"<{stmt_text(s)}>"
                q.action_lines.append(s.line)
            return [q]
        if isinstance(s, C.Perform):
            if depth > 50:
                raise RuntimeError("PERFORM nesting too deep")
            body = perform_body(self.prog, s)
            if s.times is not None or s.until is not None:
                q = p.extend()
                q.notes.append(f"line {s.line}: loop body followed once")
                return self.walk(body, [q], depth + 1)
            return self.walk(body, [p], depth + 1)
        if isinstance(s, C.If):
            if not writes_decision([s], self.prog, self.dec):
                return [p.extend(("exec", s))]
            out = self.walk(s.then, [p.extend(("cond", s.cond, True, s.line))], depth)
            out += self.walk(s.else_, [p.extend(("cond", s.cond, False, s.line))], depth)
            return out
        if isinstance(s, C.Evaluate):
            if not writes_decision([s], self.prog, self.dec):
                return [p.extend(("exec", s))]
            out, prior = [], []
            for w in s.whens:
                q = p
                for c, line in prior:
                    q = q.extend(("cond", c, False, line))
                q = q.extend(("cond", w.cond, True, w.line))
                out += self.walk(w.stmts, [q], depth)
                prior.append((w.cond, w.line))
            q = p
            for c, line in prior:
                q = q.extend(("cond", c, False, line))
            out += self.walk(s.other or [], [q], depth)
            return out
        return [p]


def extract(prog: C.Program):
    ex = Extractor(prog)
    return ex, ex.run()


# ---------------------------------------------------------------- interpreter


class SizeError(Exception):
    pass


class StopRun(Exception):
    pass


class State:
    """Field values held the way COBOL holds them (Decimal or padded str)."""

    def __init__(self, prog: C.Program):
        self.prog = prog
        self.v = {}
        for f in prog.fields.values():
            if f.pic is None:
                continue
            init = f.value if f.value is not None else (Decimal(0) if f.pic.is_numeric else "")
            self.v[f.name] = store(f.pic, init)

    def copy(self):
        s = State.__new__(State)
        s.prog = self.prog
        s.v = dict(self.v)
        return s

    def get(self, name):
        f = self.prog.fields[name]
        if f.pic is not None:
            return self.v[name]
        return "".join(self.text(c) for c in f.children)

    def text(self, name) -> str:
        f = self.prog.fields[name]
        if f.pic is None:
            return self.get(name)
        val = self.v[name]
        return display(f.pic, val) if f.pic.is_numeric and isinstance(val, Decimal) else str(val)

    def set(self, name, value):
        f = self.prog.fields[name]
        if f.pic is not None:
            self.v[name] = store(f.pic, value)
            return
        raw = str(value).ljust(f.length)[: f.length]
        for c in f.children:
            cf = self.prog.fields[c]
            part = raw[cf.offset - f.offset: cf.offset - f.offset + cf.length]
            if cf.pic is not None and cf.pic.is_numeric:
                if not part.isdigit():
                    raise ValueError(f"{c}: non-numeric data {part!r}")
                self.v[c] = Decimal(int(part)).scaleb(-cf.pic.scale)
            else:
                self.set(c, part)

    # evaluation
    def value(self, e):
        if isinstance(e, C.Lit):
            return e.value
        if isinstance(e, C.Ref):
            return self.get(e.name)
        if isinstance(e, C.Neg):
            return -self.value(e.expr)
        if isinstance(e, C.BinOp):
            a, b = self.value(e.left), self.value(e.right)
            try:
                if e.op == "+":
                    return a + b
                if e.op == "-":
                    return a - b
                if e.op == "*":
                    return a * b
                if e.op == "/":
                    if b == 0:
                        raise SizeError("division by zero")
                    return a / b
                if e.op == "**":
                    return a ** b
            except (TypeError, InvalidOperation) as exc:
                raise SizeError(str(exc))
        raise ValueError(f"cannot evaluate {e!r}")

    def compare(self, op, a, b) -> bool:
        if isinstance(a, Decimal) and isinstance(b, Decimal):
            pass
        else:
            a, b = str(a), str(b)
            n = max(len(a), len(b))
            a, b = a.ljust(n), b.ljust(n)
        return {"=": a == b, "<>": a != b, "<": a < b, ">": a > b,
                "<=": a <= b, ">=": a >= b}[op]

    def holds(self, c) -> bool:
        if isinstance(c, C.Rel):
            return self.compare(c.op, self.value(c.left), self.value(c.right))
        if isinstance(c, C.CondRef):
            cn = self.prog.conds[c.name]
            v = self.get(cn.field)
            return any(self.compare(">=", v, lo) and self.compare("<=", v, hi) for lo, hi in cn.values)
        if isinstance(c, C.Not):
            return not self.holds(c.cond)
        if isinstance(c, C.And):
            return self.holds(c.left) and self.holds(c.right)
        if isinstance(c, C.Or):
            return self.holds(c.left) or self.holds(c.right)
        raise ValueError(f"cannot evaluate {c!r}")

    def execute(self, stmts, inputs=None, out=None):
        """Run statements. inputs: iterator of lines for ACCEPT; out: list for DISPLAY."""
        for s in stmts:
            if isinstance(s, C.Move):
                val = self.value(s.source)
                for t in s.targets:
                    self.set(t, val)
            elif isinstance(s, C.Compute):
                val = self.value(s.expr)
                for t in s.targets:
                    self.set(t, val)
            elif isinstance(s, C.If):
                self.execute(s.then if self.holds(s.cond) else s.else_, inputs, out)
            elif isinstance(s, C.Evaluate):
                for w in s.whens:
                    if self.holds(w.cond):
                        self.execute(w.stmts, inputs, out)
                        break
                else:
                    self.execute(s.other or [], inputs, out)
            elif isinstance(s, C.Accept):
                self.set(s.target, next(inputs))
            elif isinstance(s, C.Display):
                if out is not None:
                    out.append("".join(self.text(i.name) if isinstance(i, C.Ref) else str(i.value)
                                       for i in s.items))
            elif isinstance(s, C.Perform):
                body = perform_body(self.prog, s)
                if s.times is not None:
                    for _ in range(int(self.value(s.times))):
                        self.execute(body, inputs, out)
                elif s.until is not None:
                    while not self.holds(s.until):
                        self.execute(body, inputs, out)
                else:
                    self.execute(body, inputs, out)
            elif isinstance(s, C.Stop):
                raise StopRun
            elif isinstance(s, C.Unsupported):
                raise ValueError(f"line {s.line}: unsupported statement")


# ---------------------------------------------------------------- rule engine


class Engine:
    """Runs the extracted rules (not the program) on an input record."""

    def __init__(self, prog: C.Program, rules: list):
        self.prog = prog
        self.rules = rules
        self.base = State(prog)

    def decide(self, record: str):
        """Return (rule, actions, state) for the first rule whose conditions all hold,
        plus the number of rules that matched (exactly one if the rules partition inputs)."""
        matched = []
        for rule in self.rules:
            st = self.base.copy()
            ok = True
            try:
                for kind, *rest in rule.steps:
                    if kind == "accept":
                        st.set(rest[0], record)
                    elif kind == "exec":
                        st.execute([rest[0]])
                    elif kind == "cond":
                        c, want, _ = rest
                        if st.holds(c) != want:
                            ok = False
                            break
                    elif kind == "unsupported":
                        ok = False
                        break
            except SizeError:
                ok = False
            if ok:
                matched.append((rule, st))
        if not matched:
            return None, {}, None, 0
        rule, st = matched[0]
        return rule, {k: str(v).strip() for k, v in rule.actions.items()}, st, len(matched)

    def output(self, rule, st) -> list:
        """The DISPLAY lines the rule's path produces, rendered from the engine state."""
        out = []
        for kind, *rest in rule.steps:
            if kind == "display":
                st.execute([rest[0]], out=out)
        return out
