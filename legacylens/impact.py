"""Change impact: what a new version of a COBOL program decides differently.

Three views of a change, from the cheapest to the strongest:
  1. rule diff - which rules are unchanged, added or removed;
  2. decision regions - for every pair (old rule, new rule) with different
     decisions, z3 decides whether some input reaches both; if so it is a
     region whose decision changes, with an example input from the model;
  3. confirmation - the example inputs are run through both compiled
     programs, and (for record-driven programs) a whole data set is run
     through both to count how many cases change, and from what to what.
"""
from __future__ import annotations

import difflib
from collections import Counter
from decimal import Decimal
from pathlib import Path

from . import cobol as C
from . import rules as R
from . import smt


def _actions(rule: R.Rule, fields: list) -> tuple:
    return tuple(str(rule.actions.get(f, "")).strip() for f in fields)


def rule_key(rule: R.Rule) -> tuple:
    return (tuple(sorted(rule.condition_texts())),
            tuple(sorted((k, str(v).strip()) for k, v in rule.actions.items())))


def source_diff(old: C.Program, new: C.Program) -> list:
    a = [t for _, t in old.lines]
    b = [t for _, t in new.lines]
    return list(difflib.unified_diff(a, b, Path(old.path).name, Path(new.path).name, lineterm="", n=1))


def compare(old: C.Program, new: C.Program, old_rules: list | None = None) -> dict:
    if old_rules is None:
        _, raw_old = R.extract(old)
        _, old_rules, _ = smt.analyse(old, raw_old, pairwise=False)
    _, raw_new = R.extract(new)
    _, new_rules, _ = smt.analyse(new, raw_new, pairwise=False)
    codes: dict = {}
    a_old = smt.Analysis(old, old_rules, codes)
    a_new = smt.Analysis(new, new_rules, codes)
    dec = sorted(set(R.decision_fields(old)) | set(R.decision_fields(new)))

    # pair rules by what they decide; same decision with different tests = changed
    def by_action(rules):
        out = {}
        for r in rules:
            out.setdefault(_actions(r, dec), []).append(r)
        return out
    old_by, new_by = by_action(old_rules), by_action(new_rules)
    unchanged, changed, removed, added = [], [], [], []
    for act in list(old_by) + [a for a in new_by if a not in old_by]:
        olds, news = old_by.get(act, []), new_by.get(act, [])
        for ro, rn in zip(olds, news):
            if rule_key(ro) == rule_key(rn):
                unchanged.append((ro.id, rn.id))
            else:
                before, after = set(ro.condition_texts()), set(rn.condition_texts())
                changed.append({"old": ro.id, "new": rn.id, "decision": dict(zip(dec, act)),
                                "tests_removed": sorted(before - after), "tests_added": sorted(after - before)})
        removed += [r.id for r in olds[len(news):]]
        added += [{"new": r.id, "decision": dict(zip(dec, act)), "tests": r.condition_texts()} for r in news[len(olds):]]

    regions = []
    for ro in old_rules:
        for rn in new_rules:
            if _actions(ro, dec) == _actions(rn, dec):
                continue
            co, do, so, so_sym = a_old._parts(ro.id)
            cn, dn, sn, sn_sym = a_new._parts(rn.id)
            tie = smt.tie_inputs(so_sym.inputs, sn_sym.inputs)
            status, solver = smt.sat_status(so + sn + tie, do + dn, co + cn)
            if status == "unsat":
                continue
            example = None
            if status == "sat" and solver is not None:
                m = solver.model()
                example = smt.model_values(m, sn_sym.inputs, codes)
                example.update(smt.model_values(m, so_sym.inputs, codes))
            regions.append({
                "old_rule": ro.id, "new_rule": rn.id, "status": status,
                "old": dict(zip(dec, _actions(ro, dec))), "new": dict(zip(dec, _actions(rn, dec))),
                "old_conditions": ro.condition_texts(), "new_conditions": rn.condition_texts(),
                "example": example,
            })
    return {"old": old.name + " " + Path(old.path).name, "new": new.name + " " + Path(new.path).name,
            "decision_fields": dec, "diff": source_diff(old, new),
            "rules_old": len(old_rules), "rules_new": len(new_rules),
            "unchanged": unchanged, "changed": changed, "removed": removed, "added": added,
            "regions": regions, "_rules": (old_rules, new_rules)}


def record_from_values(prog: C.Program, group: str, values: dict) -> str:
    """An input record for an ACCEPT-driven program: zeros/spaces, then the values."""
    st = R.State(prog)
    g = prog.fields[group]
    for k in [g.key] + R.descendants(prog, g.key)[1:]:
        f = prog.fields[k]
        if f.pic is not None and not f.redefines:
            st.set(C.Ref(k), Decimal(0) if f.pic.kind == "num" else C.Fig("SPACE"))
    for key, v in values.items():
        f = prog.fields.get(key.split("#")[0])
        if f is None or f.record != g.record or key.endswith("#numeric"):
            continue
        if v is None:
            st.set(C.Ref(f.key), "Z" * f.length)
        else:
            st.set(C.Ref(f.key), Decimal(v) if f.pic.kind == "num" else str(v))
    return st.raw(C.Ref(g.key)).decode("latin-1")


def transitions(old_out: list, new_out: list, pick=lambda line: line.split()[1]) -> dict:
    """old decision -> new decision -> count, for the cases whose decision changed."""
    moves = Counter()
    for a, b in zip(old_out, new_out):
        da, db = pick(a), pick(b)
        if da != db:
            moves[(da, db)] += 1
    return moves
