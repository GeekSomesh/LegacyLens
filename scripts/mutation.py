"""Fault injection: plant small bugs in CREDREV and IBM's SAM2 and measure what
LegacyLens catches.

For every mutant (one operator, literal, connector, arithmetic sign or
statement changed in the PROCEDURE DIVISION):
  - detected:   the rules extracted from the ORIGINAL program disagree with the
                compiled mutant on the test inputs (the drift a change causes);
  - equivalent: z3 proves no input changes any decision (change impact finds no
                region), so the change cannot be detected by any test;
  - missed:     neither.
Then the rules are re-extracted from the mutant and checked against the
compiled mutant: the extractor should be exact on every variant.

    python scripts/mutation.py [max mutants per program]     # results/mutation.json
"""
import json
import random
import re
import shutil
import subprocess
import sys
import time
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from legacylens import cobol as C, data, impact, rules as R, subprog  # noqa: E402
from legacylens.runner import run_batch  # noqa: E402
from legacylens.verify import random_records  # noqa: E402

OUT = ROOT / "build" / "mutants"
IBM = ROOT / "samples" / "ibm-zopeneditor"
REL = [">=", "<=", "<", ">", "="]


def mutants(path: Path):
    lines = path.read_text().splitlines()
    start = next(i for i, l in enumerate(lines) if "PROCEDURE DIVISION" in l.upper())
    out = []
    for i, line in enumerate(lines):
        if len(line) < 8 or line[6] in "*/":
            continue
        code = line[7:72]
        up = code.upper()
        cond_line = any(w in up for w in (" IF ", " WHEN ", "UNTIL")) or up.strip().startswith(("IF ", "WHEN "))
        if i > start and cond_line:
            for m in re.finditer(r"(>=|<=|(?<![<>])[<>](?!=)|(?<![<>])=)", code):
                for rep in REL:
                    if rep != m.group(1):
                        new = code[:m.start()] + rep + code[m.end():]
                        out.append((i, new, f"line {i + 1}: '{m.group(1)}' -> '{rep}'"))
            for m in re.finditer(r"\b(AND|OR)\b", code):
                rep = "OR" if m.group(1) == "AND" else "AND"
                out.append((i, code[:m.start()] + rep + code[m.end():], f"line {i + 1}: {m.group(1)} -> {rep}"))
        for m in re.finditer(r"(?<![\w'(-])(\d+)(?![\w')])", code):
            if i <= start and "VALUE" not in up:
                continue
            n = int(m.group(1))
            for rep in {n + 1, max(n - 1, 0)} - {n}:
                new = code[:m.start()] + str(rep).rjust(len(m.group(1)), "0") + code[m.end():]
                out.append((i, new, f"line {i + 1}: {n} -> {rep}"))
        if i > start:
            m = re.search(r"MOVE '([^']+)' TO", code)
            if m:
                lit = m.group(1)
                rep = lit[:-1] + ("X" if lit[-1] != "X" else "Y")
                out.append((i, code.replace(f"'{lit}'", f"'{rep}'", 1), f"line {i + 1}: '{lit}' -> '{rep}'"))
            for a, b in (("+", "-"), ("-", "+"), ("*", "/"), ("/", "*")):
                for m in re.finditer(rf" \{a} ", code):
                    if "COMPUTE" in up or up.strip().startswith(("CUST", "WS", "IN-", "OUT")) or "=" in code:
                        out.append((i, code[:m.start()] + f" {b} " + code[m.end():], f"line {i + 1}: {a} -> {b}"))
            if up.strip().startswith("MOVE ") and not up.rstrip().endswith("."):
                indent = code[: len(code) - len(code.lstrip())]
                out.append((i, indent + "CONTINUE", f"line {i + 1}: statement removed"))
    result = []
    for i, new, desc in out:
        new_line = lines[i][:7] + new
        if len(new_line) > 72:
            continue
        variant = list(lines)
        variant[i] = new_line
        result.append(("\n".join(variant) + "\n", desc))
    return result


def compile_mutant(src: Path, copy_dirs, module: bool) -> bool:
    cmd = ["cobc", "-m" if module else "-x", "-o", str(src.with_suffix(".so" if module else "")), str(src)]
    for d in copy_dirs:
        cmd[1:1] = ["-I", str(d)]
    return subprocess.run(cmd, capture_output=True).returncode == 0


def run_credrev(n_max, rng):
    orig_path = ROOT / "cobol" / "CREDREV.cbl"
    orig = C.parse_program(orig_path)
    _, orig_rules = R.extract(orig)
    from legacylens import smt
    _, orig_kept, _ = smt.analyse(orig, orig_rules, pairwise=False)
    eng = R.Engine(orig, orig_rules)
    df = data.load()
    recs = [data.to_record(r) for _, r in df.sample(2000, random_state=1).iterrows()]
    recs += random_records(orig, orig_rules, 1000, rng)
    expected = []
    for rec in recs:
        rule, _, st, _ = eng.decide(rec)
        expected.append(eng.output(rule, st)[0])
    rows = []
    cands = mutants(orig_path)
    rng.shuffle(cands)
    for k, (text, desc) in enumerate(cands[:n_max]):
        src = OUT / f"CREDREV_m{k}.cbl"
        src.write_text(text)
        if not compile_mutant(src, [], module=False):
            rows.append({"mutant": desc, "status": "does not compile"})
            continue
        try:
            out = run_batch(src.with_suffix(""), recs, timeout=20)
        except subprocess.TimeoutExpired:
            rows.append({"mutant": desc, "status": "detected", "note": "never ends"})
            continue
        except RuntimeError:
            rows.append({"mutant": desc, "status": "detected", "note": "stops with a runtime error"})
            continue
        detected = sum(a != b for a, b in zip(expected, out))
        mut = C.parse_program(src)
        rep = impact.compare(orig, mut, orig_kept)
        rep.pop("_rules")
        equivalent = not rep["regions"]
        _, mrules = R.extract(mut)
        meng = R.Engine(mut, mrules)
        same = 0
        for rec, line in zip(recs, out):
            rule, _, st, _ = meng.decide(rec)
            same += rule is not None and meng.output(rule, st)[0] == line
        rows.append({"mutant": desc, "status": "detected" if detected else ("equivalent" if equivalent else "missed"),
                     "inputs_differing": detected, "z3_regions": len(rep["regions"]),
                     "re_extracted_agree": same, "inputs": len(recs)})
        print(f"CREDREV {k:3d} {rows[-1]['status']:10} {desc} | re-extracted {same}/{len(recs)}", flush=True)
    return rows


def sam2_cases() -> list:
    """The SAM2 inputs every mutant is run on (a grid plus two CRUNCH requests)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("vi", ROOT / "scripts" / "verify_ibm.py")
    vi = importlib.util.module_from_spec(spec)
    argv, sys.argv = sys.argv, sys.argv[:1]
    spec.loader.exec_module(vi)
    sys.argv = argv
    custs = [l.encode("latin-1") for l in (IBM / "RESOURCES" / "SAMPLE.CUSTFILE.txt").read_text(encoding="latin-1").splitlines() if len(l) >= 387]
    cases = []
    for code in ["UPDATE", "ADD   ", "      "]:
        for action in ["REPLACE ", "ADD     ", "XXXXXXXX"]:
            for field in ["NAME      ", "BALANCE   ", "ORDERS    ", "CITY      "]:
                for d in ["000012345", "-00001234", "+00000099", "12345678X", "999999999", "NEW NAME"]:
                    cases.append({"CUST-REC": custs[0], "TRANSACTION-RECORD": vi.tran(code=code, action=action, field=field, data=d),
                                  "TRAN-OK": b"X", "TRAN-MSG": b" " * 50})
    cases += [{"CUST-REC": custs[0], "TRANSACTION-RECORD": vi.crunch(n), "TRAN-OK": b"X", "TRAN-MSG": b" " * 50} for n in (0, 2)]
    return cases


def run_sam2(n_max, rng):
    orig_path = IBM / "COBOL" / "SAM2.cbl"
    dirs = [IBM / "COPYBOOK"]
    orig = C.parse_program(orig_path, dirs)
    _, orig_rules = R.extract(orig)
    from legacylens import smt
    _, orig_rules, _ = smt.analyse(orig, orig_rules, pairwise=False)
    eng = R.Engine(orig, orig_rules)
    sys.argv = sys.argv[:1]
    import importlib.util
    spec = importlib.util.spec_from_file_location("vi", ROOT / "scripts" / "verify_ibm.py")
    vi = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vi)
    cases = sam2_cases()
    expected = []
    for c in cases:
        rule, _, st, _ = eng.decide(c)
        expected.append(eng.output(rule, st) if rule else None)
    rows = []
    cands = mutants(orig_path)
    rng.shuffle(cands)
    for k, (text, desc) in enumerate(cands[:n_max]):
        d = OUT / f"sam2_m{k}"
        d.mkdir(parents=True, exist_ok=True)
        src = d / "SAM2.cbl"
        src.write_text(text)
        try:
            mut = C.parse_program(src, dirs)
            exe, lib = subprog.build(mut, dirs, out_dir=d)
        except Exception:
            rows.append({"mutant": desc, "status": "does not compile"})
            continue
        try:
            out = subprog.run(mut, exe, lib, cases, timeout=20)
        except RuntimeError:
            rows.append({"mutant": desc, "status": "detected", "note": "stops with a runtime error"})
            continue
        except subprocess.TimeoutExpired:
            rows.append({"mutant": desc, "status": "detected", "note": "never ends"})
            print(f"SAM2    {k:3d} detected   {desc} | never ends", flush=True)
            continue
        detected = sum(a != b for a, b in zip(expected, out))
        rep = impact.compare(orig, mut, orig_rules)
        rep.pop("_rules")
        equivalent = not rep["regions"]
        _, mrules = R.extract(mut)
        _, mrules, _ = smt.analyse(mut, mrules, pairwise=False)
        meng = R.Engine(mut, mrules)
        same = 0
        for c, o in zip(cases, out):
            rule, _, st, _ = meng.decide(c)
            same += rule is not None and meng.output(rule, st) == o
        rows.append({"mutant": desc, "status": "detected" if detected else ("equivalent" if equivalent else "missed"),
                     "inputs_differing": detected, "z3_regions": len(rep["regions"]),
                     "re_extracted_agree": same, "inputs": len(cases)})
        print(f"SAM2    {k:3d} {rows[-1]['status']:10} {desc} | re-extracted {same}/{len(cases)}", flush=True)
    return rows


def summary(rows):
    s = {"mutants": len(rows)}
    for key in ("detected", "equivalent", "missed", "does not compile"):
        s[key] = sum(r["status"] == key for r in rows)
    compiled = [r for r in rows if r["status"] != "does not compile"]
    checked = [r for r in compiled if "re_extracted_agree" in r]
    s["re_extraction_exact"] = sum(r["re_extracted_agree"] == r["inputs"] for r in checked)
    s["re_extraction_programs"] = len(checked)
    return s


def main():
    n_max = int(sys.argv[1]) if len(sys.argv) > 1 else 80
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)
    rng = random.Random(9)
    t0 = time.perf_counter()
    res = {}
    for name, fn, n in (("credrev", run_credrev, n_max), ("sam2", run_sam2, max(n_max * 5 // 8, 1))):
        res[name] = fn(n, rng)
        report = {"seconds": round(time.perf_counter() - t0), "summary": {k: summary(v) for k, v in res.items()}, **res}
        (ROOT / "results" / "mutation.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
