"""Check the extracted rules against the compiled COBOL program.

The same input records go to the GnuCOBOL executable and to the rule engine.
The engine renders the program's DISPLAY line from the state its matching
rule leaves behind, so the whole output line is compared byte for byte.
"""
from __future__ import annotations

import platform
import time
from collections import Counter
from decimal import Decimal

import numpy as np

from . import cobol as C
from . import rules as R
from .runner import compile_program, run_batch


def input_layout(prog: C.Program, rules: list):
    """The record ACCEPTed by the decision paragraph and its numeric fields."""
    for kind, *rest in rules[0].steps:
        if kind == "accept":
            group = prog.fields[rest[0]]
            kids = [prog.fields[c] for c in group.children] if group.is_group else [group]
            return group, kids
    raise ValueError("decision paragraph does not ACCEPT a record")


def _put(rec: str, f: C.Field, base: int, value: int) -> str:
    text = str(value).rjust(f.length, "0")[-f.length:]
    a = f.offset - base
    return rec[:a] + text + rec[a + f.length:]


def _get(rec: str, f: C.Field, base: int) -> int:
    a = f.offset - base
    return int(rec[a:a + f.length])


def _cond_holds(prog, rule, upto: int, group, rec: str):
    st = R.State(prog)
    st.set(group.name, rec)
    for kind, *rest in rule.steps[:upto]:
        if kind == "exec":
            st.execute([rest[0]])
    c = rule.steps[upto][1]
    return st.holds(c)


def boundary_records(prog, rules, bases: list, rng, per_cond: int = 12) -> list:
    """For every condition, move one input field across the point where the
    condition changes outcome (binary search), keeping the values just
    below, at and above it."""
    group, kids = input_layout(prog, rules)
    numeric = [f for f in kids if f.pic is not None and f.pic.is_numeric][1:]  # skip the key
    seen, out = set(), []
    for rule in rules:
        for k, (kind, *rest) in enumerate(rule.steps):
            if kind != "cond":
                continue
            key = R.cond_text(rest[0])
            if key in seen:
                continue
            seen.add(key)
            for base in rng.choice(bases, size=min(per_cond, len(bases)), replace=False):
                for f in numeric:
                    hi = 10 ** f.length - 1
                    try:
                        at_lo = _cond_holds(prog, rule, k, group, _put(base, f, group.offset, 0))
                        at_hi = _cond_holds(prog, rule, k, group, _put(base, f, group.offset, hi))
                    except R.SizeError:
                        continue
                    if at_lo == at_hi:
                        continue
                    lo_v, hi_v = 0, hi
                    while hi_v - lo_v > 1:
                        mid = (lo_v + hi_v) // 2
                        try:
                            m = _cond_holds(prog, rule, k, group, _put(base, f, group.offset, mid))
                        except R.SizeError:
                            break
                        if m == at_lo:
                            lo_v = mid
                        else:
                            hi_v = mid
                    for v in {lo_v - 1, lo_v, hi_v, hi_v + 1}:
                        if 0 <= v <= hi:
                            out.append(_put(base, f, group.offset, v))
    return out


def random_records(prog, rules, n: int, rng) -> list:
    """Random records: each numeric field log-uniform over its range, with zeros and maxima."""
    group, kids = input_layout(prog, rules)
    out = []
    for i in range(n):
        rec = ""
        for j, f in enumerate(kids):
            hi = 10 ** f.length - 1
            if j == 0:
                v = 900000 + i % 100000 if f.length >= 6 else i % (hi + 1)
            else:
                r = rng.random()
                if r < 0.05:
                    v = 0
                elif r < 0.08:
                    v = hi
                else:
                    v = int(10 ** rng.uniform(0, f.length)) % (hi + 1)
            rec += str(v).rjust(f.length, "0")
        out.append(rec)
    return out


def verify(source, sets: dict, keep_examples: int = 20) -> dict:
    """sets: name -> list of records. Returns the agreement report."""
    prog = C.parse_program(source)
    _, rules = R.extract(prog)
    engine = R.Engine(prog, rules)
    exe = compile_program(source)
    report = {"program": prog.name, "rules": len(rules), "platform": platform.machine(),
              "node": platform.node(), "sets": {}, "per_rule": Counter(), "examples": []}
    total_ok = total = 0
    for name, records in sets.items():
        t0 = time.perf_counter()
        cobol_out = run_batch(exe, records)
        t1 = time.perf_counter()
        ok = multi = none = 0
        for rec, line in zip(records, cobol_out):
            rule, _, st, n = engine.decide(rec)
            if n == 0:
                none += 1
                mine = None
            else:
                multi += n > 1
                report["per_rule"][rule.id] += 1
                mine = engine.output(rule, st)[0]
            if mine == line:
                ok += 1
            elif len(report["examples"]) < keep_examples:
                report["examples"].append({"set": name, "record": rec, "cobol": line,
                                           "rules": mine, "rule": rule.id if rule else None})
        t2 = time.perf_counter()
        report["sets"][name] = {"records": len(records), "agree": ok, "no_rule": none,
                                "several_rules": multi, "cobol_seconds": round(t1 - t0, 3),
                                "engine_seconds": round(t2 - t1, 3)}
        total_ok += ok
        total += len(records)
    report["agreement"] = total_ok / total if total else None
    report["records"] = total
    report["per_rule"] = {r.id: report["per_rule"].get(r.id, 0) for r in rules}
    report["never_hit"] = [k for k, v in report["per_rule"].items() if v == 0]
    return report
