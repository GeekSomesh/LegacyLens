"""LegacyLens command line.

    python -m legacylens rules    PROGRAM.cbl [-I COPYDIR] [--json]
    python -m legacylens findings PROGRAM.cbl [-I COPYDIR]
    python -m legacylens dmn      PROGRAM.cbl [-o FILE.dmn]
    python -m legacylens verify   PROGRAM.cbl RECORDS.txt          (programs that ACCEPT one record per line)
    python -m legacylens impact   OLD.cbl NEW.cbl [-I COPYDIR]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import cobol as C
from . import dmn, impact, lint, smt
from . import rules as R


def _load(path, dirs):
    prog = C.parse_program(path, dirs)
    for d in prog.diagnostics:
        print(f"note: {d}", file=sys.stderr)
    return prog


def cmd_rules(a):
    prog = _load(a.program, a.I)
    ex, raw = R.extract(prog)
    report, kept, analysis = smt.analyse(prog, raw, pairwise=not ex.modular)
    if a.json:
        out = []
        for r in kept:
            d = r.to_dict(prog)
            d["z3"] = report["rules"][r.id]
            out.append(d)
        print(json.dumps(out, indent=2, default=str))
        return 0
    print(f"{prog.name}: {len(raw)} paths, {len(report['infeasible'])} impossible (z3), {len(kept)} rules"
          f"{' (per decision statement)' if ex.modular else ''}")
    for r in kept:
        z = report["rules"][r.id]
        acts = ", ".join(f"{k} = {str(v).strip()}" for k, v in r.actions.items())
        print(f"\n{r.id}: {acts}   [lines {', '.join(map(str, r.lines))}]")
        for text in z["kept"]:
            print(f"    when {text}")
        if z["removed"]:
            print(f"    ({len(z['removed'])} implied conditions removed, {'proved' if z['equivalent'] else 'not proved'} equivalent)")
    return 0


def cmd_findings(a):
    prog = _load(a.program, a.I)
    ex, raw = R.extract(prog)
    _, kept, analysis = smt.analyse(prog, raw, pairwise=False)
    found = lint.findings(prog, kept)
    seen = set()
    for r in kept:
        for risk in analysis.store_risks(r.id):
            key = (risk["kind"], risk["field"], risk["line"])
            if key not in seen:
                seen.add(key)
                found.append({"kind": risk["kind"], "lines": [risk["line"]],
                              "message": f"{risk['field']} at line {risk['line']}, e.g. "
                                         + ", ".join(f"{k}={v}" for k, v in (risk["example"] or {}).items())})
    for f in found:
        print(f"{f['kind']}: {f['message']}")
    print(f"{len(found)} findings")
    return 1 if found else 0


def cmd_dmn(a):
    prog = _load(a.program, a.I)
    _, raw = R.extract(prog)
    report, kept, _ = smt.analyse(prog, raw, pairwise=False)
    xml = dmn.export(prog, kept, {rid: v["kept_index"] for rid, v in report["rules"].items()})
    if a.o:
        Path(a.o).write_text(xml)
        print(f"wrote {a.o}")
    else:
        print(xml)
    return 0


def cmd_verify(a):
    from .verify import verify
    records = [l.rstrip("\n") for l in Path(a.records).read_text().splitlines() if l.strip()]
    rep = verify(a.program, {"records": records})
    print(f"{rep['program']}: {rep['sets']['records']['agree']} of {rep['records']} records give the same "
          f"output from the rules and the compiled program")
    for ex in rep["examples"][:5]:
        print("  differs:", ex)
    return 0 if rep["agreement"] == 1 else 1


def cmd_impact(a):
    old, new = _load(a.old, a.I), _load(a.new, a.I)
    rep = impact.compare(old, new)
    rep.pop("_rules")
    print(f"rules: {len(rep['unchanged'])} unchanged, {len(rep['changed'])} changed, "
          f"{len(rep['added'])} added, {len(rep['removed'])} removed")
    for reg in rep["regions"]:
        ex = ", ".join(f"{k}={v}" for k, v in (reg["example"] or {}).items())
        print(f"  {reg['old_rule']} {reg['old']} -> {reg['new_rule']} {reg['new']}   e.g. {ex}")
    print(f"{len(rep['regions'])} regions where the decision changes (z3)")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="legacylens", description="COBOL decision rules, proved and checked")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("rules", cmd_rules), ("findings", cmd_findings), ("dmn", cmd_dmn)):
        s = sub.add_parser(name)
        s.add_argument("program")
        s.add_argument("-I", action="append", default=[], help="copybook directory")
        if name == "rules":
            s.add_argument("--json", action="store_true")
        if name == "dmn":
            s.add_argument("-o")
        s.set_defaults(fn=fn)
    s = sub.add_parser("verify")
    s.add_argument("program")
    s.add_argument("records")
    s.set_defaults(fn=cmd_verify, I=[])
    s = sub.add_parser("impact")
    s.add_argument("old")
    s.add_argument("new")
    s.add_argument("-I", action="append", default=[])
    s.set_defaults(fn=cmd_impact)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
