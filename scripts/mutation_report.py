"""Summarise results/mutation.json: detection by the tests or by z3, and why
re-extracted rules differ from a mutant where they do.

    python scripts/mutation_report.py      # adds "report" to results/mutation.json
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from legacylens import cobol as C, rules as R  # noqa: E402

IBM = ROOT / "samples" / "ibm-zopeneditor"


def reason_for_gap(path: Path, dirs) -> str:
    """Why rules re-extracted from a mutant cannot reproduce it record by record."""
    prog = C.parse_program(path, dirs)
    _, rules = R.extract(prog)
    if any("keeps the value left by an earlier" in n for r in rules for n in r.notes):
        return "flagged: a decision field is left unset on some path (result depends on history)"
    for p in prog.paragraphs:
        for s in R.walk_stmts(p.stmts):
            if isinstance(s, C.Compute) and "/" in R.expr_text(s.expr):
                return "division whose divisor can be zero: COBOL leaves the field unchanged (history)"
    return "unexplained"


def main():
    f = ROOT / "results" / "mutation.json"
    d = json.loads(f.read_text())
    report = {}
    for prog_key, rows in (("credrev", d.get("credrev", [])), ("sam2", d.get("sam2", []))):
        live = [r for r in rows if r["status"] != "does not compile"]
        tests = sum(r["status"] == "detected" for r in live)
        z3_only = sum(r["status"] != "detected" and r.get("z3_regions", 0) > 0 for r in live)
        proved_eq = sum(r["status"] == "equivalent" for r in live)
        missed = sum(r["status"] == "missed" and not r.get("z3_regions") for r in live)
        gaps = []
        for k, r in enumerate(rows):
            if r["status"] == "does not compile" or "re_extracted_agree" not in r or r["re_extracted_agree"] == r["inputs"]:
                continue
            src = ROOT / "build" / "mutants" / (f"CREDREV_m{k}.cbl" if prog_key == "credrev" else f"sam2_m{k}/SAM2.cbl")
            why = reason_for_gap(src, [] if prog_key == "credrev" else [IBM / "COPYBOOK"]) if src.exists() else "file gone"
            gaps.append({"mutant": r["mutant"], "agree": r["re_extracted_agree"], "inputs": r["inputs"], "why": why})
        report[prog_key] = {
            "mutants": len(rows), "compiled": len(live),
            "detected_by_tests": tests, "found_by_z3_only": z3_only,
            "detected_by_tests_or_z3": tests + z3_only, "proved_equivalent": proved_eq, "missed_by_both": missed,
            "re_extraction_exact": sum(1 for r in live if r.get("re_extracted_agree") == r.get("inputs")),
            "re_extraction_gaps": gaps,
        }
        print(prog_key, {k: v for k, v in report[prog_key].items() if k != "re_extraction_gaps"})
        for g in gaps:
            print("   ", g["mutant"], f"{g['agree']}/{g['inputs']}", "-", g["why"])
    d["report"] = report
    f.write_text(json.dumps(d, indent=2))


if __name__ == "__main__":
    main()
