"""Analyse every program LegacyLens ships: rules, z3 report, example inputs and findings.

    python scripts/analyse_programs.py      # results/programs/<NAME>.json and index.json
"""
import json
import re
import sys
import time
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from legacylens import cobol as C, glossary, lint, rules as R, smt  # noqa: E402

IBM = ROOT / "samples" / "ibm-zopeneditor"
PROGRAMS = [
    {"name": "CREDREV", "source": ROOT / "cobol" / "CREDREV.cbl", "copy": [],
     "origin": "Credit line review policy written for LegacyLens", "verification": "verification.json"},
    {"name": "SAM2", "source": IBM / "COBOL" / "SAM2.cbl", "copy": [IBM / "COPYBOOK"],
     "origin": "IBM zopeneditor-sample (Apache-2.0): customer update subroutine", "verification": "ibm_sam2.json"},
    {"name": "SAM1", "source": IBM / "COBOL" / "SAM1.cbl", "copy": [IBM / "COPYBOOK"],
     "origin": "IBM zopeneditor-sample (Apache-2.0): batch customer file update", "verification": "ibm_sam1.json",
     "kind": "program model on whole file runs"},
]


def plain(v):
    if isinstance(v, Decimal):
        return str(v.normalize()) if v == v.to_integral() else str(v)
    return v


def main():
    out_dir = ROOT / "results" / "programs"
    out_dir.mkdir(parents=True, exist_ok=True)
    index = []
    for spec in PROGRAMS:
        t0 = time.perf_counter()
        prog = C.parse_program(spec["source"], spec["copy"])
        ex, raw = R.extract(prog)
        report, kept, analysis = smt.analyse(prog, raw, pairwise=not ex.modular)
        names = glossary.load(spec["source"])
        rules = []
        for r in kept:
            d = r.to_dict(prog, names)
            z = report["rules"][r.id]
            for i, c in enumerate(d["conditions"]):
                c["implied"] = i not in z["kept_index"]
            d["z3"] = {"removed": len(z["removed"]), "equivalent": z["equivalent"]}
            w = analysis.witness(r.id)
            if w:
                d["example"] = {k: ("(any other text)" if v is None else plain(v)) for k, v in w.items()}
            rules.append(d)
        found = lint.findings(prog, kept)
        verification = None
        vf = ROOT / "results" / spec["verification"]
        if vf.exists():
            v = json.loads(vf.read_text())
            sets = v.get("sets", {})
            total = sum(s.get("records", s.get("cases", 0)) for s in sets.values())
            agree = sum(s.get("agree", 0) for s in sets.values())
            verification = {"cases": total, "agree": agree, "sets": sets, "platform": v.get("platform"),
                            "kind": spec.get("kind", "rules")}
            for msg, n in v.get("runtime_findings", {}).items():
                line = re.match(r"line (\d+):", msg)
                found.append({"kind": "found while running", "lines": [int(line.group(1))] if line else [],
                              "message": f"{msg} ({n} of {v.get('runs', n)} runs)"})
        data = {
            "name": prog.name, "origin": spec["origin"], "path": str(Path(spec["source"]).relative_to(ROOT)),
            "lines": [{"no": n, "text": t} for n, t in prog.lines],
            "copybooks": sorted({f for f, _ in prog.locs.values()}),
            "paragraphs": len(prog.paragraphs), "fields": len(prog.fields),
            "diagnostics": prog.diagnostics, "using": prog.using,
            "mode": "per decision statement" if ex.modular else "whole program",
            "entry": ex.entry, "decision_fields": sorted(ex.dec),
            "raw_paths": len(raw), "rules": rules,
            "z3": {"infeasible": len(report["infeasible"]), "unknown": len(report["unknown"]),
                   "conditions_removed": sum(len(v["removed"]) for v in report["rules"].values()),
                   "all_proved": all(v["equivalent"] for v in report["rules"].values()),
                   "overlaps": report["overlaps"]},
            "findings": found, "verification": verification,
            "seconds": round(time.perf_counter() - t0, 2),
        }
        (out_dir / f"{prog.name}.json").write_text(json.dumps(data, indent=1, default=str))
        index.append({k: data[k] for k in ("name", "origin", "path", "raw_paths", "mode", "z3", "seconds")}
                     | {"rules": len(rules), "findings": len(found),
                        "lines_of_code": sum(1 for _ in C.read_fixed(Path(spec["source"]).read_text(errors="replace"))),
                        "verified": verification and {"cases": verification["cases"], "agree": verification["agree"]}})
        print(prog.name, len(raw), "->", len(rules), "rules;", len(found), "findings;", data["z3"], f"{data['seconds']} s")
        for f in found:
            print("   ", f["kind"], "-", f["message"])
    (out_dir / "index.json").write_text(json.dumps(index, indent=1, default=str))


if __name__ == "__main__":
    main()
