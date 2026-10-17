"""Decision rules from a parsed COBOL program, an interpreter that keeps data
the way the machine does (bytes), and an engine that runs the extracted rules.

A rule is one path through the decision logic: the conditions tested on the
way (with the outcome each must have) and the values written to the decision
fields. Conditional code that only computes working fields (no decision field
written) stays a derivation instead of splitting the rules.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from . import cobol as C
from .numeric import NotNumeric, decode, display, encode, format_edited, is_numeric_data, store

MAX_PATHS = 4000

# ---------------------------------------------------------------- text


def expr_text(e) -> str:
    if isinstance(e, C.Lit):
        return e.text
    if isinstance(e, C.Ref):
        t = e.name
        if e.subs:
            t += "(" + " ".join(expr_text(s) for s in e.subs) + ")"
        if e.refmod:
            t += f"({expr_text(e.refmod[0])}:{expr_text(e.refmod[1]) if e.refmod[1] else ''})"
        return t
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
    if isinstance(c, C.ClassCond):
        return f"{expr_text(c.operand)} IS {c.kind}"
    if isinstance(c, C.AtEnd):
        return f"END OF FILE {c.file}"
    if isinstance(c, C.Not):
        inner = c.cond
        if isinstance(inner, C.ClassCond):
            return f"{expr_text(inner.operand)} IS NOT {inner.kind}"
        return f"NOT {cond_text(inner, False)}"
    if isinstance(c, (C.And, C.Or)):
        word = "AND" if isinstance(c, C.And) else "OR"

        def side(x):
            return cond_text(x, type(x) is type(c))
        t = f"{side(c.left)} {word} {side(c.right)}"
        return t if top or isinstance(c, C.And) else f"({t})"
    return "?"


def stmt_text(s) -> str:
    if isinstance(s, C.Move):
        return "; ".join(f"{expr_text(t)} = {expr_text(s.source)}" for t in s.targets)
    if isinstance(s, C.Compute):
        return "; ".join(f"{expr_text(t)} = {expr_text(s.expr)}" for t in s.targets)
    if isinstance(s, C.If):
        then = "; ".join(stmt_text(x) for x in s.then)
        out = f"if {cond_text(s.cond)}: {then}"
        if s.else_:
            out += " else: " + "; ".join(stmt_text(x) for x in s.else_)
        return out
    if isinstance(s, C.Evaluate):
        parts = [f"when {cond_text(w.cond)}: " + "; ".join(stmt_text(x) for x in w.stmts) for w in s.whens]
        if s.other:
            parts.append("otherwise: " + "; ".join(stmt_text(x) for x in s.other))
        return " | ".join(parts)
    if isinstance(s, C.Perform):
        loop = f"until {cond_text(s.until)}" if s.until is not None else (
            f"{expr_text(s.times)} times" if s.times is not None else "")
        what = s.target or "; ".join(stmt_text(x) for x in s.body)
        return f"repeat {what} {loop}".strip()
    if isinstance(s, C.Call):
        return f"CALL {expr_text(s.target)} USING " + ", ".join(expr_text(a) for a in s.args)
    if isinstance(s, C.Read):
        return f"READ {s.file}"
    if isinstance(s, C.Write):
        return f"WRITE {expr_text(s.record)}" + (f" FROM {expr_text(s.source)}" if s.source else "")
    if isinstance(s, C.Open):
        return "OPEN " + ", ".join(f"{m} {f}" for m, f in s.files)
    if isinstance(s, C.Close):
        return "CLOSE " + ", ".join(s.files)
    if isinstance(s, C.Accept):
        return f"ACCEPT {expr_text(s.target)}" + (f" FROM {s.source}" if s.source else "")
    if isinstance(s, C.Initialize):
        return "INITIALIZE " + ", ".join(expr_text(t) for t in s.targets)
    if isinstance(s, C.SetTrue):
        return "SET " + ", ".join(s.names) + " TO TRUE"
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
        elif isinstance(s, C.Read):
            yield from walk_stmts(s.at_end)
            yield from walk_stmts(s.not_at_end)


def refs_in(node) -> set:
    out = set()
    if isinstance(node, C.Ref):
        out.add(node.name)
        for s in node.subs:
            out |= refs_in(s)
    elif isinstance(node, C.CondRef):
        out.add(node.name)
    elif isinstance(node, (C.BinOp, C.Rel, C.And, C.Or)):
        out |= refs_in(node.left) | refs_in(node.right)
    elif isinstance(node, C.Neg):
        out |= refs_in(node.expr)
    elif isinstance(node, C.Not):
        out |= refs_in(node.cond)
    elif isinstance(node, C.ClassCond):
        out |= refs_in(node.operand)
    return out


def targets_of(s) -> list:
    if isinstance(s, (C.Move, C.Compute)):
        return [t.name for t in s.targets]
    if isinstance(s, C.Accept):
        return [s.target.name]
    if isinstance(s, C.Initialize):
        return [t.name for t in s.targets]
    return []


def descendants(prog: C.Program, key: str) -> list:
    out = [key]
    for ck in prog.fields[key].children:
        out += descendants(prog, ck)
    return out


def decision_fields(prog: C.Program) -> list:
    """The program's decisions:
    - fields given a literal inside a conditional branch and never read; and
    - for a CALLed program, its USING parameters and their subfields that are
      written in a branch (subfields only if no condition tests them)."""
    lit_assigned, branch_written, cond_read, read = {}, {}, set(), set()
    for p in prog.paragraphs:
        for s in walk_stmts(p.stmts):
            branches = []
            if isinstance(s, C.If):
                branches = [s.then, s.else_]
                cond_read |= refs_in(s.cond)
            elif isinstance(s, C.Evaluate):
                branches = [w.stmts for w in s.whens] + [s.other or []]
                for w in s.whens:
                    cond_read |= refs_in(w.cond)
            elif isinstance(s, C.Read):
                branches = [s.at_end, s.not_at_end]
            elif isinstance(s, C.Perform) and s.until is not None:
                cond_read |= refs_in(s.until)
            for b in branches:
                for x in walk_stmts(b):
                    for t in targets_of(x):
                        branch_written.setdefault(t, x.line)
                    if isinstance(x, C.Move) and isinstance(x.source, C.Lit):
                        for t in x.targets:
                            lit_assigned.setdefault(t.name, x.line)
            if isinstance(s, C.Compute):
                read |= refs_in(s.expr)
            elif isinstance(s, C.Move):
                read |= refs_in(s.source)
            elif isinstance(s, C.Write) and s.source is not None:
                read |= refs_in(s.source)
            elif isinstance(s, C.Call):
                for a in s.args:
                    read |= refs_in(a)
    for name in list(cond_read):
        if name in prog.conds:
            cond_read.add(prog.conds[name].field)
    read |= cond_read
    dec = {f: line for f, line in lit_assigned.items() if f not in read}
    params = set(prog.using)
    sub = {d for u in prog.using if u in prog.fields for d in descendants(prog, u)} - params
    for f, line in branch_written.items():
        if f in params or (f in sub and f not in cond_read):
            dec.setdefault(f, line)
    return sorted(dec, key=lambda f: dec[f])


def para_range(prog: C.Program, s: C.Perform) -> tuple:
    i = prog.para_index(s.target)
    j = prog.para_index(s.thru) if s.thru else i
    return i, j


def writes_decision(stmts, prog, dec: set, seen=None) -> bool:
    seen = set() if seen is None else seen
    for s in walk_stmts(stmts):
        if dec & set(targets_of(s)):
            return True
        if isinstance(s, C.Perform) and s.target:
            i, j = para_range(prog, s)
            for k in range(i, j + 1):
                if k not in seen:
                    seen.add(k)
                    if writes_decision(prog.paragraphs[k].stmts, prog, dec, seen):
                        return True
        if isinstance(s, C.GoTo) and s.target:
            k = prog.para_index(s.target)
            if k not in seen:
                seen.add(k)
                if writes_decision(prog.paragraphs[k].stmts, prog, dec, seen):
                    return True
    return False


def find_entry(prog: C.Program, dec: set):
    """(first, last) paragraph range run once per record: the target of a looping
    PERFORM that reaches the decision logic; otherwise the whole program."""
    for p in prog.paragraphs:
        for s in walk_stmts(p.stmts):
            if isinstance(s, C.Perform) and s.target and (s.times is not None or s.until is not None):
                i, j = para_range(prog, s)
                body = [x for k in range(i, j + 1) for x in prog.paragraphs[k].stmts]
                if writes_decision(body, prog, dec):
                    return i, j
    return 0, len(prog.paragraphs) - 1


# ---------------------------------------------------------------- rules


@dataclass
class Rule:
    id: str
    steps: list            # ("accept", stmt) | ("exec", stmt) | ("cond", cond, bool, line) | ("display", stmt)
    actions: dict          # decision field -> value (or "<expression>")
    action_lines: list
    notes: list = field(default_factory=list)

    @property
    def conditions(self) -> list:
        return [(rest[0], rest[1], rest[2]) for kind, *rest in self.steps if kind == "cond"]

    @property
    def lines(self) -> list:
        return sorted({line for _, _, line in self.conditions} | set(self.action_lines))

    def condition_texts(self) -> list:
        return [cond_text(c if holds else negate(c)) for c, holds, _ in self.conditions]

    def to_dict(self, prog: C.Program, names: dict | None = None) -> dict:
        from .glossary import english
        names = names or {}
        derived = []
        for kind, *rest in self.steps:
            if kind == "exec" and not (set(targets_of(rest[0])) & set(self.actions)):
                derived.append({"text": stmt_text(rest[0]), "line": rest[0].line})
        conds = []
        for c, holds, line in self.conditions:
            shown = c if holds else negate(c)
            entry = {"text": cond_text(shown), "line": line, "where": prog.where(line),
                     "tested": cond_text(c), "outcome": holds, "english": english(shown, names)}
            cond_names = refs_in(c) & set(prog.conds)
            if cond_names:
                entry["meaning"] = "; ".join(cond_name_text(prog.conds[n]) for n in sorted(cond_names))
            conds.append(entry)
        return {"id": self.id, "actions": {k: str(v).strip() for k, v in self.actions.items()},
                "conditions": conds, "derived": derived, "lines": self.lines, "notes": self.notes}


def cond_name_text(cn: C.CondName) -> str:
    parts = [f"{lo}" if lo == hi else f"{lo} THRU {hi}" for lo, hi in cn.values]
    return f"{cn.name} means {cn.field} is " + " or ".join(parts)


@dataclass
class _Path:
    steps: list
    actions: dict
    action_lines: list
    notes: list
    stopped: bool = False
    jump: str | None = None

    def extend(self, step=None, **kw):
        p = _Path(self.steps + ([step] if step else []), dict(self.actions),
                  list(self.action_lines), list(self.notes), self.stopped, self.jump)
        for k, v in kw.items():
            setattr(p, k, v)
        return p


PERFORM_ACTION = "PERFORMS"


class Extractor:
    """Whole-program rules (one rule per path from the entry), or, when that
    explodes or is asked for, modular rules: one rule set per paragraph, where
    a PERFORM inside a branch is an action ("routes to ...")."""

    def __init__(self, prog: C.Program, decision: list | None = None, modular: bool = False):
        self.prog = prog
        self.dec = set(decision or decision_fields(prog))
        self.range = find_entry(prog, self.dec)
        self.entry = prog.paragraphs[self.range[0]].name
        self.modular = modular
        self.local = False

    def run(self) -> list:
        if not self.modular:
            try:
                paths = self.walk_range(*self.range, [_Path([], {}, [], [])], depth=0)
                return self._rules(paths, "RULE")
            except RuntimeError:
                self.modular = True
        self.local = True
        rules = []
        for para in self.prog.paragraphs:
            d = 0
            for i, st in enumerate(para.stmts):
                if not self._local_decisions([st]):
                    continue
                d += 1
                prefix = [("exec", x) for x in para.stmts[:i]
                          if not isinstance(x, (C.GoTo, C.Stop, C.Unsupported, C.Display))]
                paths = self.walk([st], [_Path(prefix, {}, [], [])], depth=0)
                rules += self._rules(paths, f"{para.name}/{d}")
        return rules

    def _rules(self, paths, prefix):
        out = []
        literal = {k for p in paths for k, v in p.actions.items()
                   if k not in (PERFORM_ACTION, "CALLS") and not str(v).startswith("<")}
        for p in paths:
            if p.actions and prefix == "RULE":
                for f in sorted(literal - set(p.actions)):
                    p.notes.append(f"{f} is not set on this path: it keeps the value left by an earlier "
                                   f"record or call, so this decision depends on history")
            if p.actions:
                p.actions = {k: (", ".join(v) if isinstance(v, list) else v) for k, v in p.actions.items()}
                out.append(Rule(f"{prefix}-{len(out) + 1:02d}" if prefix == "RULE" else f"{prefix}.{len(out) + 1}",
                                p.steps, p.actions, p.action_lines, p.notes))
        return out

    def _local_decisions(self, stmts) -> bool:
        for s in stmts:
            if isinstance(s, (C.If, C.Evaluate)) or (isinstance(s, C.Read) and (s.at_end or s.not_at_end)):
                if self._writes(s):
                    return True
        return False

    def _writes(self, s) -> bool:
        if self.local:
            for x in walk_stmts([s]):
                if self.dec & set(targets_of(x)) or (isinstance(x, C.Perform) and x.target) or isinstance(x, C.Call):
                    return True
            return False
        return writes_decision([s], self.prog, self.dec)

    def walk_range(self, first: int, last: int, paths: list, depth: int) -> list:
        """Run paragraphs first..last with fall-through; GO TO inside the range jumps."""
        if depth > 60:
            raise RuntimeError("PERFORM nesting too deep")
        done, work = [], [(p, first) for p in paths]
        while work:
            p, k = work.pop(0)
            for q in self.walk(self.prog.paragraphs[k].stmts, [p], depth):
                if q.stopped:
                    done.append(q)
                elif q.jump:
                    t = self.prog.para_index(q.jump)
                    q = q.extend(jump=None)
                    if first <= t <= last and len(work) + len(done) < MAX_PATHS:
                        work.append((q, t))
                    else:
                        q.notes.append(f"GO TO {self.prog.paragraphs[t].name} leaves the performed range")
                        done.append(q.extend(stopped=True))
                elif k < last:
                    work.append((q, k + 1))
                else:
                    done.append(q)
            if len(work) + len(done) > MAX_PATHS:
                raise RuntimeError(f"more than {MAX_PATHS} paths")
        return done

    def walk(self, stmts, paths, depth):
        for s in stmts:
            nxt = []
            for p in paths:
                nxt.extend([p] if (p.stopped or p.jump) else self.expand(s, p, depth))
            paths = nxt
            if len(paths) > MAX_PATHS:
                raise RuntimeError(f"more than {MAX_PATHS} paths")
        return paths

    def expand(self, s, p, depth):
        if isinstance(s, C.Accept):
            return [p.extend(("accept", s))]
        if isinstance(s, C.Display):
            return [p.extend(("display", s))]
        if isinstance(s, C.Continue):
            return [p]
        if isinstance(s, C.Stop):
            return [p.extend(stopped=True)]
        if isinstance(s, C.GoTo):
            return [p.extend(jump=s.target)]
        if isinstance(s, C.Unsupported):
            q = p.extend(("unsupported", s.text, s.line))
            q.notes.append(f"line {s.line}: unsupported statement, rule needs review")
            return [q]
        if isinstance(s, (C.Move, C.Compute, C.Initialize)):
            q = p.extend(("exec", s))
            hits = self.dec & set(targets_of(s))
            if hits:
                for t in hits:
                    f = self.prog.fields[t]
                    if isinstance(s, C.Move) and isinstance(s.source, C.Lit) and f.pic is not None \
                            and not isinstance(s.source.value, C.Fig):
                        q.actions[t] = store(f.pic, s.source.value)
                    elif isinstance(s, C.Move) and isinstance(s.source, C.Lit):
                        q.actions[t] = s.source.text
                    else:
                        q.actions[t] = f"<{stmt_text(s)}>"
                q.action_lines.append(s.line)
            return [q]
        if isinstance(s, (C.Call, C.Write, C.Open, C.Close, C.SetTrue)):
            return [p.extend(("exec", s))]
        if isinstance(s, C.Read):
            bare = C.Read(s.file, s.into, [], [], s.line)
            q = p.extend(("exec", bare))
            if not any(self._writes(x) for x in s.at_end + s.not_at_end) and                     not (self.local and (s.at_end or s.not_at_end) and self._local_decisions(
                        [C.If(C.AtEnd(s.file), s.at_end, s.not_at_end, s.line)])):
                return [q.extend(("exec", s))] if (s.at_end or s.not_at_end) else [q]
            out = self.walk(s.at_end, [q.extend(("cond", C.AtEnd(s.file), True, s.line))], depth)
            out += self.walk(s.not_at_end, [q.extend(("cond", C.AtEnd(s.file), False, s.line))], depth)
            return out
        if isinstance(s, C.Perform) and self.local:
            q = p.extend(("exec", s))
            if s.target:
                q.actions.setdefault(PERFORM_ACTION, [])
                q.actions[PERFORM_ACTION] = q.actions[PERFORM_ACTION] + [s.target]
                q.action_lines.append(s.line)
            return [q]
        if isinstance(s, C.Call) and self.local:
            q = p.extend(("exec", s))
            q.actions["CALLS"] = expr_text(s.target)
            q.action_lines.append(s.line)
            return [q]
        if isinstance(s, C.Perform):
            looping = s.times is not None or s.until is not None
            body_writes = writes_decision([s], self.prog, self.dec)
            if looping and not body_writes:
                return [p.extend(("exec", s))]
            q = p.extend()
            if looping:
                q.notes.append(f"line {s.line}: loop body followed once")
            if s.target:
                i, j = para_range(self.prog, s)
                return self.walk_range(i, j, [q], depth + 1)
            return self.walk(s.body, [q], depth + 1)
        if isinstance(s, C.If):
            if not self._writes(s):
                return [p.extend(("exec", s))]
            out = self.walk(s.then, [p.extend(("cond", s.cond, True, s.line))], depth)
            out += self.walk(s.else_, [p.extend(("cond", s.cond, False, s.line))], depth)
            return out
        if isinstance(s, C.Evaluate):
            if not self._writes(s):
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


def extract(prog: C.Program, decision: list | None = None, modular: bool = False):
    ex = Extractor(prog, decision, modular)
    return ex, ex.run()


# ---------------------------------------------------------------- interpreter


class SizeError(Exception):
    pass


class StopRun(Exception):
    pass


class GoToSignal(Exception):
    def __init__(self, target: str):
        self.target = target


class State:
    """Field storage as bytes, record by record, so REDEFINES, OCCURS and
    group moves behave as on the machine."""

    def __init__(self, prog: C.Program, callees: dict | None = None, files: dict | None = None):
        self.prog = prog
        self.callees = callees or {}
        self.mem = {}
        for f in prog.fields.values():
            if f.parent is None and f.section != "linkage" and f.record == f.key:
                self.mem[f.key] = bytearray(f.length * (f.occurs or 1))
        for f in prog.fields.values():
            if f.parent is None and f.section != "linkage" and not f.redefines:
                self._init(f)
        self.at_end = {}
        self.warnings = []
        self.events = []        # (kind, line, field, value, stored) for stores that lose digits or a sign
        self.line = 0
        self.files = {}
        for name, recs in (files or {}).items():
            self.files[name] = {"in": list(recs), "pos": 0, "out": []}

    def _init(self, f: C.Field):
        if f.value is not None:
            for ref in self._occurrences(f):
                self.set(ref, f.value)
            return
        if f.pic is not None:
            if f.section == "file":
                return
            default = C.Fig("SPACE") if f.pic.kind in ("alnum", "edited") else Decimal(0)
            for ref in self._occurrences(f):
                self.set(ref, default)
            return
        for ck in f.children:
            c = self.prog.fields[ck]
            if not c.redefines:
                self._init(c)

    def _occurrences(self, f: C.Field):
        if not f.dims:
            return [C.Ref(f.key)]
        idx = [[]]
        for _, n in f.dims:
            idx = [i + [k] for i in idx for k in range(1, n + 1)]
        return [C.Ref(f.key, tuple(C.Lit(Decimal(k), str(k)) for k in i)) for i in idx]

    def copy(self):
        s = State.__new__(State)
        s.prog, s.callees = self.prog, self.callees
        s.mem = {k: bytearray(v) for k, v in self.mem.items()}
        s.at_end = dict(self.at_end)
        s.warnings, s.line, s.events = [], self.line, []
        s.files = {k: {"in": v["in"], "pos": v["pos"], "out": list(v["out"])} for k, v in self.files.items()}
        return s

    def bind(self, record: str, buf):
        self.mem[record] = buf

    # addressing
    def locate(self, ref: C.Ref):
        f = self.prog.fields.get(ref.name)
        if f is None:
            raise KeyError(f"unknown data name {ref.name}")
        off = f.offset
        if ref.subs:
            if len(ref.subs) != len(f.dims):
                raise SizeError(f"{ref.name}: {len(f.dims)} subscripts needed")
            out_of_range = []
            for (stride, n), sub in zip(f.dims, ref.subs):
                k = int(self.value(sub))
                if not 1 <= k <= n:
                    out_of_range.append((k, n))
                off += (k - 1) * stride
            if out_of_range:
                # GnuCOBOL does not check subscripts by default: the access lands at the
                # computed offset. Allowed while it stays inside the record; reported.
                buf = self.mem.get(f.record)
                if buf is None or off < 0 or off + f.length > len(buf):
                    raise SizeError(f"{ref.name}: subscript {out_of_range[0][0]} outside the record")
                msg = (f"line {self.line}: {ref.name}({out_of_range[0][0]}) is outside the table "
                       f"(1-{out_of_range[0][1]}); bytes of the next field are used")
                if msg not in self.warnings:
                    self.warnings.append(msg)
        length = f.length
        if ref.refmod:
            start = int(self.value(ref.refmod[0]))
            ln = int(self.value(ref.refmod[1])) if ref.refmod[1] is not None else length - start + 1
            if start < 1 or ln < 1 or start - 1 + ln > length:
                raise SizeError(f"{ref.name}: reference modification out of range")
            off, length = off + start - 1, ln
        buf = self.mem.get(f.record)
        if buf is None:
            raise KeyError(f"{f.record} is not bound (LINKAGE parameter)")
        return buf, off, length, f

    def raw(self, ref: C.Ref) -> bytes:
        buf, off, ln, _ = self.locate(ref)
        return bytes(buf[off:off + ln])

    def get(self, ref: C.Ref):
        buf, off, ln, f = self.locate(ref)
        data = bytes(buf[off:off + ln])
        if f.pic is None or ref.refmod:
            return data.decode("latin-1")
        return decode(f.pic, data)

    def text(self, name) -> str:
        """DISPLAY form of a field (accepts a name or a Ref)."""
        ref = name if isinstance(name, C.Ref) else C.Ref(name)
        buf, off, ln, f = self.locate(ref)
        data = bytes(buf[off:off + ln])
        if f.pic is None or ref.refmod or f.pic.kind != "num":
            return data.decode("latin-1")
        return display(f.pic, decode(f.pic, data))

    def set(self, ref: C.Ref, value, src=None, rounded: bool = False):
        buf, off, ln, f = self.locate(ref)
        if isinstance(value, C.Fig):
            if f.pic is not None and f.pic.kind == "num" and not ref.refmod and value.kind == "ZERO":
                data = encode(f.pic, Decimal(0))
            else:
                data = value.fill(ln).encode("latin-1")
        elif f.pic is not None and not ref.refmod and f.pic.kind == "num":
            if isinstance(value, str):
                digits = value.strip()
                if not digits.isdigit():
                    raise NotNumeric(f"MOVE {value!r} to numeric {f.name}")
                value = Decimal(int(digits))
            kept = store(f.pic, value, rounded)
            if isinstance(value, Decimal):
                if abs(value) >= Decimal(10) ** f.pic.digits:
                    self.events.append(("silent overflow", self.line, f.name, value, kept))
                if value < 0 and not f.pic.signed and kept != 0:
                    self.events.append(("sign lost", self.line, f.name, value, kept))
            data = encode(f.pic, kept)
        elif f.pic is not None and not ref.refmod and f.pic.kind == "edited":
            if isinstance(value, str):
                value = Decimal(int(value.strip() or "0"))
            data = format_edited(f.pic, store(f.pic, value, rounded)).encode("latin-1")
        else:
            if isinstance(value, Decimal):
                value = src if isinstance(src, str) else str(abs(value)).replace(".", "")
            data = str(value).encode("latin-1", "replace")[:ln].ljust(ln, b" ")
        buf[off:off + ln] = data

    def move_text(self, source) -> str | None:
        """The characters a numeric source contributes to an alphanumeric MOVE."""
        if isinstance(source, C.Lit) and isinstance(source.value, Decimal):
            return source.text.lstrip("+-").replace(".", "")
        if isinstance(source, C.Ref):
            f = self.prog.fields.get(source.name)
            if f is not None and f.pic is not None and f.pic.kind == "num" and not source.refmod:
                return display(f.pic, self.get(source)).lstrip("+-").replace(".", "")
        return None

    # evaluation
    def value(self, e):
        if isinstance(e, C.Lit):
            return e.value
        if isinstance(e, C.Ref):
            return self.get(e)
        if isinstance(e, C.Neg):
            return -self._num(self.value(e.expr))
        if isinstance(e, C.BinOp):
            a, b = self._num(self.value(e.left)), self._num(self.value(e.right))
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
            except InvalidOperation as exc:
                raise SizeError(str(exc))
        raise ValueError(f"cannot evaluate {e!r}")

    @staticmethod
    def _num(v):
        if isinstance(v, Decimal):
            return v
        if isinstance(v, C.Fig) and v.kind == "ZERO":
            return Decimal(0)
        if isinstance(v, str) and v.strip().isdigit():
            return Decimal(int(v.strip()))
        raise NotNumeric(f"{v!r} in arithmetic")

    def compare(self, op, a, b) -> bool:
        if isinstance(a, C.Fig) and isinstance(b, C.Fig):
            a, b = a.fill(1), b.fill(1)
        elif isinstance(a, C.Fig):
            a = Decimal(0) if isinstance(b, Decimal) and a.kind == "ZERO" else a.fill(len(str(b)))
        elif isinstance(b, C.Fig):
            b = Decimal(0) if isinstance(a, Decimal) and b.kind == "ZERO" else b.fill(len(str(a)))
        if not (isinstance(a, Decimal) and isinstance(b, Decimal)):
            a = str(abs(a)) if isinstance(a, Decimal) else str(a)
            b = str(abs(b)) if isinstance(b, Decimal) else str(b)
            n = max(len(a), len(b))
            a, b = a.ljust(n), b.ljust(n)
        return {"=": a == b, "<>": a != b, "<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b}[op]

    def holds(self, c) -> bool:
        if isinstance(c, C.Rel):
            return self.compare(c.op, self.value(c.left), self.value(c.right))
        if isinstance(c, C.CondRef):
            cn = self.prog.conds[c.name]
            v = self.get(C.Ref(cn.field))
            return any(self.compare(">=", v, lo) and self.compare("<=", v, hi) for lo, hi in cn.values)
        if isinstance(c, C.ClassCond):
            ref = c.operand
            data = self.raw(ref)
            f = self.prog.fields.get(ref.name)
            if c.kind == "NUMERIC":
                return is_numeric_data(None if ref.refmod or f is None else f.pic, data)
            text = data.decode("latin-1")
            if c.kind == "ALPHABETIC":
                return all(ch.isalpha() or ch == " " for ch in text)
            if c.kind == "ALPHABETIC-LOWER":
                return all(ch.islower() or ch == " " for ch in text)
            return all(ch.isupper() or ch == " " for ch in text)
        if isinstance(c, C.AtEnd):
            return self.at_end.get(c.file, False)
        if isinstance(c, C.Not):
            return not self.holds(c.cond)
        if isinstance(c, C.And):
            return self.holds(c.left) and self.holds(c.right)
        if isinstance(c, C.Or):
            return self.holds(c.left) or self.holds(c.right)
        raise ValueError(f"cannot evaluate {c!r}")

    # execution
    def run_range(self, first: int, last: int, inputs=None, out=None):
        k = first
        while k <= last:
            try:
                self.execute(self.prog.paragraphs[k].stmts, inputs, out)
                k += 1
            except GoToSignal as g:
                t = self.prog.para_index(g.target)
                if not first <= t <= last:
                    raise
                k = t

    def run_program(self, inputs=None, out=None):
        try:
            self.run_range(0, len(self.prog.paragraphs) - 1, inputs, out)
        except StopRun:
            pass

    def execute(self, stmts, inputs=None, out=None):
        for s in stmts:
            self.line = getattr(s, "line", self.line)
            if isinstance(s, C.Move):
                val = self.value(s.source)
                txt = self.move_text(s.source)
                for t in s.targets:
                    self.set(t, val, txt)
            elif isinstance(s, C.Compute):
                val = self.value(s.expr)
                for t in s.targets:
                    self.set(t, val, rounded=t.name in s.rounded)
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
                if s.source in ("DATE", "TIME", "DAY", "DAY-OF-WEEK"):
                    now = datetime.datetime.now()
                    val = {"DATE": now.strftime("%y%m%d"), "TIME": now.strftime("%H%M%S00"),
                           "DAY": now.strftime("%y%j"), "DAY-OF-WEEK": str(now.isoweekday())}[s.source]
                    self.set(s.target, val)
                else:
                    self._accept(s.target, next(inputs))
            elif isinstance(s, C.Display):
                if out is not None:
                    out.append("".join(self.text(i) if isinstance(i, C.Ref) else
                                       (i.value.fill(1) if isinstance(i.value, C.Fig) else
                                        (i.text if isinstance(i.value, Decimal) else str(i.value)))
                                       for i in s.items))
            elif isinstance(s, C.Perform):
                self._perform(s, inputs, out)
            elif isinstance(s, C.GoTo):
                raise GoToSignal(s.target)
            elif isinstance(s, C.Stop):
                raise StopRun
            elif isinstance(s, C.Continue):
                pass
            elif isinstance(s, C.Call):
                self._call(s, inputs, out)
            elif isinstance(s, C.Initialize):
                for t in s.targets:
                    self._initialize(self.prog.fields[t.name], t)
            elif isinstance(s, C.SetTrue):
                for n in s.names:
                    cn = self.prog.conds[n]
                    self.set(C.Ref(cn.field), cn.values[0][0])
            elif isinstance(s, C.Open):
                for mode, f in s.files:
                    st = self.prog.files.get(f, {}).get("status")
                    if mode in ("OUTPUT", "EXTEND") or f in self.files:
                        entry = self.files.setdefault(f, {"in": [], "pos": 0, "out": []})
                        entry["pos"] = 0
                        if mode == "OUTPUT":
                            entry["out"] = []
                        self.at_end[f] = False
                        if st:
                            self.set(C.Ref(st), "00")
                    elif st:
                        self.set(C.Ref(st), "35")
            elif isinstance(s, C.Close):
                pass
            elif isinstance(s, C.Read):
                self._read(s, inputs, out)
            elif isinstance(s, C.Write):
                if s.source is not None:
                    self.set(s.record, self.value(s.source), self.move_text(s.source))
                fname = next((n for n, d in self.prog.files.items() if s.record.name in d["records"]), None)
                if fname is not None:
                    self.files.setdefault(fname, {"in": [], "pos": 0, "out": []})["out"].append(self.raw(s.record))
                    st = self.prog.files[fname].get("status")
                    if st:
                        self.set(C.Ref(st), "00")
            elif isinstance(s, C.Unsupported):
                raise ValueError(f"line {s.line}: unsupported statement")

    def _accept(self, ref: C.Ref, line: str):
        """ACCEPT from the console as GnuCOBOL does: a numeric field takes the
        line cut to its width and converted as a number (sign allowed)."""
        f = self.prog.fields[ref.name]
        if f.pic is None or ref.refmod or f.pic.kind != "num":
            self.set(ref, line)
            return
        text = line[: f.pic.digits + f.pic.scale].strip()
        sign = -1 if text[:1] == "-" or text[-1:] == "-" else 1
        body = text.strip("+-").strip()
        if not body:
            self.set(ref, Decimal(0))
            return
        try:
            value = Decimal(body)
        except InvalidOperation:
            raise NotNumeric(f"ACCEPT {line!r} into {f.name}")
        self.set(ref, sign * value)

    def _perform(self, s: C.Perform, inputs, out):
        def body():
            if s.target:
                i, j = para_range(self.prog, s)
                self.run_range(i, j, inputs, out)
            else:
                self.execute(s.body, inputs, out)
        if s.varying is not None:
            var, start, step = s.varying
            self.set(var, self.value(start))
            guard = 0
            while True:
                if not s.test_after and self.holds(s.until):
                    break
                body()
                if s.test_after and self.holds(s.until):
                    break
                self.set(var, self.value(var) + self.value(step))
                guard += 1
                if guard > 1_000_000:
                    raise SizeError("loop did not end")
        elif s.times is not None:
            for _ in range(int(self.value(s.times))):
                body()
        elif s.until is not None:
            guard = 0
            if s.test_after:
                body()
            while not self.holds(s.until):
                body()
                guard += 1
                if guard > 1_000_000:
                    raise SizeError("loop did not end")
        else:
            body()

    def _call(self, s: C.Call, inputs, out):
        name = str(self.value(s.target)).strip()
        callee = self.callees.get(name)
        if callee is None:
            raise ValueError(f"line {s.line}: CALL {name}: program not available")
        # a called program keeps its WORKING-STORAGE between calls (until CANCEL)
        loaded = self.__dict__.setdefault("_loaded", {})
        st = loaded.get(name) or loaded.setdefault(name, State(callee, self.callees))
        for param, arg in zip(callee.using, s.args):
            buf, off, ln, _ = self.locate(arg)
            st.bind(callee.fields[param].record, memoryview(buf)[off:off + ln])
        st.run_program(inputs, out)

    def _read(self, s: C.Read, inputs, out):
        f = self.files.get(s.file)
        st = self.prog.files.get(s.file, {}).get("status")
        recs = self.prog.files.get(s.file, {}).get("records", [])
        if f is None or f["pos"] >= len(f["in"]):
            self.at_end[s.file] = True
            if st:
                self.set(C.Ref(st), "10")
            self.execute(s.at_end, inputs, out)
            return
        data = f["in"][f["pos"]]
        f["pos"] += 1
        self.at_end[s.file] = False
        rec = self.prog.fields[recs[0]]
        buf = self.mem[rec.record]
        n = min(len(data), rec.length)
        buf[rec.offset:rec.offset + n] = data[:n]
        if s.into is not None:
            self.set(s.into, data.decode("latin-1"))
        if st:
            self.set(C.Ref(st), "00")
        self.execute(s.not_at_end, inputs, out)

    def _initialize(self, f: C.Field, ref: C.Ref):
        if f.pic is not None:
            self.set(ref, C.Fig("SPACE") if f.pic.kind != "num" else Decimal(0))
            return
        for ck in f.children:
            c = self.prog.fields[ck]
            if c.redefines or c.name == "FILLER":
                continue
            for r in self._occurrences(c) if not ref.subs else [C.Ref(c.key, ref.subs)]:
                self._initialize(c, r)


# ---------------------------------------------------------------- rule engine


class Engine:
    """Runs the extracted rules (not the program) on one input.

    The input is a record for a program that ACCEPTs its data, or a dict of
    USING parameter name -> bytes for a CALLed program."""

    def __init__(self, prog: C.Program, rules: list, callees: dict | None = None):
        self.prog = prog
        self.rules = rules
        self.base = State(prog, callees)

    def _fresh(self, inputs):
        st = self.base.copy()
        if isinstance(inputs, dict):
            for name, data in inputs.items():
                st.bind(self.prog.fields[name].record, bytearray(data))
        return st

    def decide(self, inputs):
        """(rule, actions, state, number of matching rules); undefined results
        (invalid numeric data, bad subscripts) count as no match."""
        matched, undefined = [], False
        for rule in self.rules:
            st = self._fresh(inputs)
            ok = True
            try:
                for kind, *rest in rule.steps:
                    if kind == "accept":
                        st.set(rest[0].target, inputs)
                    elif kind == "exec":
                        st.execute([rest[0]])
                    elif kind == "cond":
                        if st.holds(rest[0]) != rest[1]:
                            ok = False
                            break
                    elif kind == "unsupported":
                        ok = False
                        break
            except (SizeError, NotNumeric):
                ok, undefined = False, True
            except (StopRun, GoToSignal):
                ok = False
            if ok:
                matched.append((rule, st))
        if not matched:
            return None, {"undefined": undefined}, None, 0
        rule, st = matched[0]
        return rule, {k: str(v).strip() for k, v in rule.actions.items()}, st, len(matched)

    def output(self, rule, st) -> list:
        """What the program hands back: DISPLAY lines, or the USING parameters."""
        if self.prog.using:
            return [bytes(st.mem[self.prog.fields[u].record]).decode("latin-1") for u in self.prog.using]
        out = []
        for kind, *rest in rule.steps:
            if kind == "display":
                st.execute([rest[0]], out=out)
        return out
