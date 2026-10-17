"""Symbolic execution of rule paths with the z3 SMT solver.

Every rule path is executed over symbols instead of values: input fields are
z3 variables, assignments produce terms, and each tested condition becomes a
formula over the inputs. With that, z3:

  * drops paths that no input can follow (infeasible rules),
  * removes conditions implied by the rest of the rule and proves the shorter
    rule equivalent to the original,
  * checks that no two rules overlap and that together they cover every input,
  * produces a concrete example input (a witness) for every rule.

The encoding over-approximates where COBOL is hard to model exactly (writes
through REDEFINES or reference modification, loops, CALLs, file input): the
affected fields become fresh unknowns. So "infeasible", "implied" and
"equivalent" are proofs; a rule kept or a condition kept may still be
redundant. Alphanumeric fields are compared through the literals the program
uses (each literal is a number); ordering tests on text become unknowns.
"""
from __future__ import annotations

import itertools
from decimal import Decimal

import z3

from . import cobol as C
from .numeric import NotNumeric, decode, is_numeric_data
from .rules import Rule, State, cond_text, negate, targets_of

TIMEOUT_MS = 3000


class Unknown(Exception):
    pass


class Encoder:
    """Shared tables for one program: literal codes and field overlaps."""

    def __init__(self, prog: C.Program, codes: dict | None = None):
        self.prog = prog
        self.codes = {} if codes is None else codes     # shared when comparing two programs
        self.fresh = itertools.count()
        self.by_record = {}
        for f in prog.fields.values():
            self.by_record.setdefault(f.record, []).append(f)
        # initial values of storage the program owns, taken from the interpreter
        self.initial, self.initial_num = {}, {}
        st = State(prog)
        for f in prog.fields.values():
            if f.pic is None or f.dims or f.section in ("linkage", "file") or f.record not in st.mem:
                continue
            raw = st.raw(C.Ref(f.key))
            self.initial_num[f.key] = z3.BoolVal(is_numeric_data(f.pic, raw))
            if f.pic.kind == "num":
                try:
                    self.initial[f.key] = ("num", z3.RealVal(str(decode(f.pic, raw))))
                except NotNumeric:
                    pass
            else:
                self.initial[f.key] = ("txt", z3.IntVal(self.code(raw.decode("latin-1"))))

    def code(self, text: str) -> int:
        return self.codes.setdefault(text, len(self.codes) + 1)

    def overlapping(self, f: C.Field, off: int | None = None, length: int | None = None) -> list:
        off = f.offset if off is None else off
        end = off + (f.length * _span(f) if length is None else length)
        return [g for g in self.by_record.get(f.record, [])
                if g.offset < end and off < g.offset + g.length * _span(g)]


def _span(f: C.Field) -> int:
    n = 1
    for _, count in f.dims:
        n *= count
    return n


class Sym:
    """Symbolic field values along one path."""

    def __init__(self, enc: Encoder, initial: dict | None = None):
        self.enc = enc
        self.prog = enc.prog
        self.vals = dict(enc.initial if initial is None else initial)   # key -> ("num"|"txt", term)
        self.numflags = dict(enc.initial_num) if initial is None else {}  # key -> Bool for NUMERIC
        self.defs = []                      # defining equations for computed fields
        self.side = []                      # facts that must hold for the path to be defined
        self.inputs = {}                    # field key -> symbol of an input value
        self.at_end = {}

    def copy(self):
        s = Sym(self.enc, self.vals)
        s.numflags = dict(self.numflags)
        s.defs, s.side, s.inputs, s.at_end = list(self.defs), list(self.side), self.inputs, dict(self.at_end)
        return s

    # symbols
    def _new(self, f: C.Field, tag: str):
        n = next(self.enc.fresh)
        if f.pic is not None and f.pic.kind == "num":
            v = z3.Real(f"{f.key}_{tag}{n}")
            lim = Decimal(10) ** f.pic.digits
            self.side.append(v < z3.RealVal(str(lim)))
            self.side.append(v > z3.RealVal(str(-lim)) if f.pic.signed else v >= 0)
            scale = 10 ** f.pic.scale
            self.side.append(z3.IsInt(v * scale))
            return ("num", v)
        return ("txt", z3.Int(f"{f.key}_{tag}{n}"))

    def read(self, ref: C.Ref):
        f = self.prog.fields[ref.name]
        if ref.subs or ref.refmod or f.dims or f.pic is None:
            raise Unknown(ref.name)
        if f.key not in self.vals:
            self.vals[f.key] = self._new(f, "in")
            self.inputs.setdefault(f.key, self.vals[f.key])
        return self.vals[f.key]

    def invalidate(self, f: C.Field, keep: str | None = None, off=None, length=None):
        for g in self.enc.overlapping(f, off, length):
            if g.key == keep:
                continue
            if g.pic is not None and not g.dims:
                self.vals[g.key] = self._new(g, "w")
                self.numflags.pop(g.key, None)
            else:
                self.vals.pop(g.key, None)

    def write(self, ref: C.Ref, val):
        """val: ("num", term) | ("txt", term) | None for unknown."""
        f = self.prog.fields[ref.name]
        if ref.subs or ref.refmod or f.dims or f.pic is None or val is None:
            self.invalidate(f)
            return
        self.invalidate(f, keep=f.key)
        if f.pic.kind == "num":
            if val[0] != "num":
                self.vals[f.key] = self._new(f, "w")
                return
            v = self._new(f, "c")[1]
            self.defs.append(v == self._truncate(f, val[1]))
            self.vals[f.key] = ("num", v)
            self.numflags[f.key] = z3.BoolVal(True)
        else:
            self.vals[f.key] = val if val[0] == "txt" else self._new(f, "w")
            self.numflags.pop(f.key, None)

    def _truncate(self, f: C.Field, e):
        scale = 10 ** f.pic.scale
        mod = 10 ** (f.pic.digits + f.pic.scale)
        x = e * scale
        mag = z3.If(x >= 0, z3.ToInt(x), z3.ToInt(-x))
        mag = mag % mod
        signed = z3.If(x >= 0, z3.ToReal(mag), -z3.ToReal(mag)) if f.pic.signed else z3.ToReal(mag)
        return signed / scale

    # expressions
    def literal(self, lit: C.Lit, like: C.Field | None):
        v = lit.value
        if isinstance(v, C.Fig):
            if like is not None and like.pic is not None and like.pic.kind == "num" and v.kind == "ZERO":
                return ("num", z3.RealVal(0))
            n = like.length if like is not None else 1
            return ("txt", z3.IntVal(self.enc.code(v.fill(n))))
        if isinstance(v, Decimal):
            if like is not None and like.pic is not None and like.pic.kind != "num":
                return ("txt", z3.IntVal(self.enc.code(lit.text.lstrip("+-").ljust(like.length)[: like.length])))
            return ("num", z3.RealVal(str(v)))
        n = like.length if like is not None else len(v)
        return ("txt", z3.IntVal(self.enc.code(v[:n].ljust(n))))

    def term(self, e, like: C.Field | None = None):
        if isinstance(e, C.Lit):
            return self.literal(e, like)
        if isinstance(e, C.Ref):
            return self.read(e)
        if isinstance(e, C.Neg):
            k, t = self.term(e.expr)
            if k != "num":
                raise Unknown("negated text")
            return ("num", -t)
        if isinstance(e, C.BinOp):
            (ka, a), (kb, b) = self.term(e.left), self.term(e.right)
            if ka != "num" or kb != "num":
                raise Unknown("arithmetic on text")
            if e.op == "+":
                return ("num", a + b)
            if e.op == "-":
                return ("num", a - b)
            if e.op == "*":
                return ("num", a * b)
            if e.op == "/":
                self.side.append(b != 0)
                return ("num", a / b)
            raise Unknown(e.op)
        raise Unknown(type(e).__name__)

    def _field_of(self, e):
        return self.prog.fields.get(e.name) if isinstance(e, C.Ref) else None

    def formula(self, c):
        """A z3 Bool for condition c in the current state (fresh Bool when unknown)."""
        try:
            return self._formula(c)
        except Unknown:
            return z3.Bool(f"u{next(self.enc.fresh)}")

    def _formula(self, c):
        if isinstance(c, C.Rel):
            fl, fr = self._field_of(c.left), self._field_of(c.right)
            a = self.term(c.left, like=fr)
            b = self.term(c.right, like=fl)
            if a[0] == "num" and b[0] == "num":
                return {"=": a[1] == b[1], "<>": a[1] != b[1], "<": a[1] < b[1], ">": a[1] > b[1],
                        "<=": a[1] <= b[1], ">=": a[1] >= b[1]}[c.op]
            if a[0] == "txt" and b[0] == "txt" and c.op in ("=", "<>"):
                if fl is not None and fr is not None and fl.length != fr.length:
                    raise Unknown("text of different lengths")
                return a[1] == b[1] if c.op == "=" else a[1] != b[1]
            raise Unknown("ordering on text")
        if isinstance(c, C.CondRef):
            cn = self.prog.conds[c.name]
            ref = C.Ref(cn.field)
            parts = []
            for lo, hi in cn.values:
                lo_l = C.Lit(lo, str(lo)) if not isinstance(lo, C.Fig) else C.Lit(lo, lo.kind)
                hi_l = C.Lit(hi, str(hi)) if not isinstance(hi, C.Fig) else C.Lit(hi, hi.kind)
                if lo == hi:
                    parts.append(self._formula(C.Rel("=", ref, lo_l)))
                else:
                    parts.append(z3.And(self._formula(C.Rel(">=", ref, lo_l)), self._formula(C.Rel("<=", ref, hi_l))))
            return z3.Or(*parts) if parts else z3.BoolVal(False)
        if isinstance(c, C.ClassCond):
            if c.kind != "NUMERIC" or not isinstance(c.operand, C.Ref):
                raise Unknown(c.kind)
            f = self.prog.fields[c.operand.name]
            if f.key not in self.numflags:
                if f.key in self.vals and self.vals[f.key][0] == "txt":
                    raise Unknown("NUMERIC on computed text")
                self.numflags[f.key] = z3.Bool(f"{f.key}_isnum{next(self.enc.fresh)}")
                self.inputs.setdefault(f.key + "#numeric", self.numflags[f.key])
            return self.numflags[f.key]
        if isinstance(c, C.AtEnd):
            if c.file not in self.at_end:
                raise Unknown("end of file")
            return self.at_end[c.file]
        if isinstance(c, C.Not):
            return z3.Not(self._formula(c.cond))
        if isinstance(c, C.And):
            return z3.And(self._formula(c.left), self._formula(c.right))
        if isinstance(c, C.Or):
            return z3.Or(self._formula(c.left), self._formula(c.right))
        raise Unknown(type(c).__name__)

    # statements
    def execute(self, stmts):
        for s in stmts:
            if isinstance(s, C.Move):
                for t in s.targets:
                    f = self.prog.fields[t.name]
                    try:
                        val = self.term(s.source, like=f)
                        sf = self._field_of(s.source)
                        if val[0] == "txt" and sf is not None and sf.length != f.length:
                            val = None
                    except Unknown:
                        val = None
                    self.write(t, val)
            elif isinstance(s, C.Compute):
                try:
                    val = self.term(s.expr)
                except Unknown:
                    val = None
                for t in s.targets:
                    # ROUNDED results are left unknown (the encoding models truncation)
                    self.write(t, None if t.name in s.rounded else val)
            elif isinstance(s, C.If):
                c = self.formula(s.cond)
                self._branch([(c, s.then)], s.else_)
            elif isinstance(s, C.Evaluate):
                arms, prior = [], []
                for w in s.whens:
                    c = self.formula(w.cond)
                    arms.append((z3.And(c, *[z3.Not(p) for p in prior]), w.stmts))
                    prior.append(c)
                self._branch(arms, s.other or [])
            elif isinstance(s, C.Accept):
                f = self.prog.fields[s.target.name]
                self.invalidate(f)
                for g in self.enc.overlapping(f):
                    if g.pic is not None and not g.dims:
                        self.inputs.setdefault(g.key, self.vals[g.key])
            elif isinstance(s, (C.Display, C.Continue)):
                pass
            else:
                for name in self._written(s):
                    self.invalidate(self.prog.fields[name])
                if isinstance(s, C.Read):
                    self.at_end[s.file] = z3.Bool(f"eof_{s.file}_{next(self.enc.fresh)}")

    def _written(self, s) -> set:
        out = set()
        stmts = [s]
        seen = set()
        while stmts:
            x = stmts.pop()
            out |= set(targets_of(x))
            if isinstance(x, C.If):
                stmts += x.then + x.else_
            elif isinstance(x, C.Evaluate):
                stmts += [y for w in x.whens for y in w.stmts] + (x.other or [])
            elif isinstance(x, C.Perform):
                stmts += x.body
                if x.varying:
                    out.add(x.varying[0].name)
                if x.target:
                    i = self.prog.para_index(x.target)
                    j = self.prog.para_index(x.thru) if x.thru else i
                    for k in range(i, j + 1):
                        if k not in seen:
                            seen.add(k)
                            stmts += self.prog.paragraphs[k].stmts
            elif isinstance(x, C.Call):
                out |= {a.name for a in x.args if isinstance(a, C.Ref)}
            elif isinstance(x, C.Read):
                recs = self.prog.files.get(x.file, {}).get("records", [])
                out |= set(recs)
                st = self.prog.files.get(x.file, {}).get("status")
                if st:
                    out.add(st)
                if x.into is not None:
                    out.add(x.into.name)
                stmts += x.at_end + x.not_at_end
            elif isinstance(x, C.Write):
                out.add(x.record.name)
                fname = next((n for n, d in self.prog.files.items() if x.record.name in d["records"]), None)
                if fname and self.prog.files[fname].get("status"):
                    out.add(self.prog.files[fname]["status"])
            elif isinstance(x, (C.Open, C.Close)):
                for item in x.files:
                    fname = item[1] if isinstance(item, tuple) else item
                    st = self.prog.files.get(fname, {}).get("status")
                    if st:
                        out.add(st)
            elif isinstance(x, C.SetTrue):
                out |= {self.prog.conds[n].field for n in x.names}
        return {n for n in out if n in self.prog.fields}

    def _branch(self, arms, other):
        """Execute every arm and merge the results with if-then-else terms."""
        base = self.copy()
        results = []
        for c, stmts in arms:
            r = base.copy()
            r.execute(stmts)
            results.append((c, r))
        rest = base.copy()
        rest.execute(other)

        def val(r, k):
            return r.vals.get(k) or self.inputs.get(k) or self.enc.initial.get(k)

        def flag(r, k):
            if k in r.numflags:
                return r.numflags[k]
            v = self.inputs.get(k + "#numeric")
            return v if v is not None else self.enc.initial_num.get(k)

        keys = set(rest.vals).union(*[set(r.vals) for _, r in results])
        merged = {}
        for k in keys:
            cur = val(rest, k)
            for c, r in reversed(results):
                v = val(r, k)
                if cur is None or v is None or v[0] != cur[0]:
                    cur = None
                    break
                cur = cur if v[1] is cur[1] else (v[0], z3.If(c, v[1], cur[1]))
            if cur is None:
                f = self.prog.fields.get(k)
                if f is not None and f.pic is not None and not f.dims:
                    merged[k] = self._new(f, "m")
            else:
                merged[k] = cur
        flags = {}
        for k in set(rest.numflags).union(*[set(r.numflags) for _, r in results]):
            cur = flag(rest, k)
            for c, r in reversed(results):
                v = flag(r, k)
                if cur is None or v is None:
                    cur = None
                    break
                cur = cur if v is cur else z3.If(c, v, cur)
            if cur is not None:
                flags[k] = cur
        self.vals, self.numflags = merged, flags
        for r in [r for _, r in results] + [rest]:
            self.defs += r.defs[len(base.defs):]
            self.side += r.side[len(base.side):]
        for k, v in rest.at_end.items():
            self.at_end[k] = v


def path_formula(enc: Encoder, rule: Rule):
    """Run a rule's steps symbolically: (list of (cond formula, wanted, step), Sym)."""
    sym = Sym(enc)
    conds = []
    for k, (kind, *rest) in enumerate(rule.steps):
        if kind == "accept":
            sym.execute([rest[0]])
        elif kind == "exec":
            sym.execute([rest[0]])
        elif kind == "cond":
            f = sym.formula(rest[0])
            conds.append((f if rest[1] else z3.Not(f), k))
    return conds, sym


def _check(formulas, extra=(), timeout=TIMEOUT_MS):
    s = z3.Solver()
    s.set("timeout", timeout)
    s.add(*formulas, *extra)
    return s.check(), s


def sat_status(base: list, defs: list, goal: list):
    """'unsat' if base+goal is impossible (proved without, then with, the
    definitions of computed fields), else 'sat' or 'unknown'."""
    r, _ = _check(base + goal)
    if r == z3.unsat:
        return "unsat", None
    r, s = _check(base + defs + goal)
    return ("unsat" if r == z3.unsat else "sat" if r == z3.sat else "unknown"), s


def model_values(m, inputs: dict, codes: dict) -> dict:
    """Input field key -> value (Decimal, text, or bool for '#numeric') from a z3 model."""
    inv = {v: k for k, v in codes.items()}
    out = {}
    for key, sym_v in inputs.items():
        if key.endswith("#numeric"):
            val = m.eval(sym_v, model_completion=False)
            if z3.is_true(val) or z3.is_false(val):
                out[key] = z3.is_true(val)
            continue
        kind, t = sym_v
        val = m.eval(t, model_completion=False)
        if not (z3.is_rational_value(val) or z3.is_int_value(val)):
            continue
        if kind == "num":
            out[key] = Decimal(val.numerator_as_long()) / Decimal(val.denominator_as_long())
        else:
            out[key] = inv.get(val.as_long())       # None: any text other than the program's literals
    return out


def tie_inputs(ia: dict, ib: dict) -> list:
    """Equalities that make two paths read the same input values."""
    eqs = []
    for k in set(ia) & set(ib):
        x, y = ia[k], ib[k]
        if isinstance(x, tuple):
            if x[0] == y[0]:
                eqs.append(x[1] == y[1])
        else:
            eqs.append(x == y)
    return eqs


class Analysis:
    def __init__(self, prog: C.Program, rules: list, codes: dict | None = None):
        self.prog = prog
        self.enc = Encoder(prog, codes)
        self.rules = rules
        self.paths = {r.id: path_formula(self.enc, r) for r in rules}

    def _parts(self, rule_id):
        conds, sym = self.paths[rule_id]
        return [c for c, _ in conds], sym.defs, sym.side, sym

    def feasible(self, rule_id) -> str:
        conds, defs, side, _ = self._parts(rule_id)
        status, _ = sat_status(side, defs, conds)
        return status

    def simplify(self, rule_id) -> dict:
        """Drop conditions implied by the others; prove the result equivalent."""
        conds, defs, side, _ = self._parts(rule_id)
        keep = list(range(len(conds)))
        for i in range(len(conds)):
            others = [conds[j] for j in keep if j != i]
            status, _ = sat_status(side + others, defs, [z3.Not(conds[i])])
            if status == "unsat":
                keep.remove(i)
        full = z3.And(*conds) if conds else z3.BoolVal(True)
        short = z3.And(*[conds[j] for j in keep]) if keep else z3.BoolVal(True)
        status, _ = sat_status(side, defs, [z3.Xor(full, short)])
        return {"kept": keep, "removed": [i for i in range(len(conds)) if i not in keep],
                "equivalent": status == "unsat"}

    def witness(self, rule_id):
        """A model of the rule's path: input field key -> value (Decimal, str or bool)."""
        conds, defs, side, sym = self._parts(rule_id)
        r, s = _check(side + defs + conds, timeout=TIMEOUT_MS * 2)
        if r != z3.sat:
            r, s = _check(side + conds)
            if r != z3.sat:
                return None
        return model_values(s.model(), sym.inputs, self.enc.codes)

    def overlaps(self) -> list:
        """Pairs of rules that some input satisfies together (should be empty)."""
        out = []
        ids = [r.id for r in self.rules]
        for a, b in itertools.combinations(ids, 2):
            ca, da, sa, _ = self._parts(a)
            cb, db, sb, _ = self._parts(b)
            # each path has its own symbols; tie them through the shared input symbols
            status, _ = sat_status(sa + sb + self._same_inputs(a, b), da + db, ca + cb)
            if status != "unsat":
                out.append((a, b, status))
        return out

    def _same_inputs(self, a, b) -> list:
        return tie_inputs(self.paths[a][1].inputs, self.paths[b][1].inputs)


def analyse(prog: C.Program, rules: list, pairwise: bool = True) -> dict:
    """Prune infeasible rules and simplify the rest. Returns the report and the kept rules."""
    a = Analysis(prog, rules)
    report = {"rules_in": len(rules), "infeasible": [], "unknown": [], "rules": {}}
    kept = []
    for r in rules:
        status = a.feasible(r.id)
        if status == "unsat":
            report["infeasible"].append(r.id)
            continue
        if status == "unknown":
            report["unknown"].append(r.id)
        kept.append(r)
    for r in kept:
        simp = a.simplify(r.id)
        conds = r.conditions
        report["rules"][r.id] = {
            "conditions": len(conds),
            "kept": [cond_text(c if h else negate(c)) for i, (c, h, _) in enumerate(conds) if i in simp["kept"]],
            "kept_index": simp["kept"],
            "removed": [cond_text(c if h else negate(c)) for i, (c, h, _) in enumerate(conds) if i in simp["removed"]],
            "equivalent": simp["equivalent"],
        }
    a.rules = kept
    report["overlaps"] = a.overlaps() if pairwise else None
    return report, kept, a
