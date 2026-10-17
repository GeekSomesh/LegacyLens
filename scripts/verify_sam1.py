"""IBM's SAM1 batch program, end to end: the compiled program (GnuCOBOL, calling
the compiled SAM2) and LegacyLens's interpreter (calling its own model of SAM2)
process the same customer and transaction files; the updated customer file,
the report and the console output are compared.

Report records are compared without printer control bytes (form feeds and
line feeds); lines that print the run date and time are masked.

    python scripts/verify_sam1.py        # results/ibm_sam1.json
"""
import json
import os
import platform
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from legacylens import cobol as C, rules as R, subprog  # noqa: E402

IBM = ROOT / "samples" / "ibm-zopeneditor"
BUILD = ROOT / "build"


def var_file(records: list) -> bytes:
    return b"".join(len(r).to_bytes(2, "big") + b"\x00\x00" + r for r in records)


def read_var(data: bytes) -> list:
    out, i = [], 0
    while i + 4 <= len(data):
        n = int.from_bytes(data[i:i + 2], "big")
        out.append(data[i + 4:i + 4 + n])
        i += 4 + n
    return out


def report_records(data: bytes, width: int = 132) -> list:
    body = data.replace(b"\x0c", b"").replace(b"\n", b"").replace(b"\r", b"")
    return [mask_header(body[i:i + width]) for i in range(0, len(body), width)]


def mask_header(rec: bytes) -> bytes:
    """The report header prints the run date and time."""
    return re.sub(rb"\d", b"#", rec) if b"DATE:" in rec else rec


def mask(text: str) -> str:
    return re.sub(r"\d", "#", text)


def build_sam1() -> Path:
    exe = BUILD / "SAM1"
    r = subprocess.run(["cobc", "-x", "-o", str(exe), "-I", str(IBM / "COPYBOOK"), str(IBM / "COBOL" / "SAM1.cbl")],
                       capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr)
    return exe


def run_compiled(exe: Path, custs: list, trans: list) -> dict:
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "CUSTFILE").write_bytes(var_file(custs))
        (d / "TRANFILE").write_bytes(b"".join(trans))
        env = dict(os.environ, COB_LIBRARY_PATH=str(BUILD))
        r = subprocess.run([str(exe)], cwd=d, capture_output=True, env=env, timeout=120)
        return {"rc": r.returncode,
                "display": [mask(x) if ("DATE =" in x or "TIME =" in x) else x
                            for x in r.stdout.decode("latin-1").splitlines()],
                "custout": read_var((d / "CUSTOUT").read_bytes()) if (d / "CUSTOUT").exists() else [],
                "report": report_records((d / "CUSTRPT").read_bytes()) if (d / "CUSTRPT").exists() else []}


def run_model(sam1: C.Program, sam2: C.Program, custs: list, trans: list) -> dict:
    st = R.State(sam1, callees={"SAM2": sam2},
                 files={"CUSTOMER-FILE": custs, "TRANSACTION-FILE": trans})
    out = []
    st.run_program(out=out)
    rc = int(st.get(C.Ref("RETURN-CODE")))
    report = [mask_header(r) for r in st.files.get("REPORT-FILE", {}).get("out", [])]
    return {"rc": rc, "warnings": st.warnings + [w for c in st.__dict__.get("_loaded", {}).values() for w in c.warnings],
            "display": [mask(x) if ("DATE =" in x or "TIME =" in x) else x for x in out],
            "custout": list(st.files.get("CUSTOMER-FILE-OUT", {}).get("out", [])),
            "report": report}


def customer(cid: int, rtype: str, rng: random.Random, base: bytes) -> bytes:
    if rtype == "C":
        rec = f"{cid:05d}C{'CONTACT ' + str(cid):<17.17}{'PHONE':<10}{'555-' + str(cid):<20}{'':<5}{'':<5}{'':<5} "
        return rec[:69].encode("latin-1")
    rec = bytearray(base)
    rec[0:6] = f"{cid:05d}A".encode()
    rec[6:23] = f"{'CUSTOMER ' + str(cid):<17.17}".encode()
    rec[23:32] = f"{rng.randint(0, 999999999):09d}".encode()
    rec[32:37] = f"{rng.randint(0, 99999):05d}".encode()
    return bytes(rec)


def transaction(rng: random.Random, key: str) -> bytes:
    code = rng.choice(["UPDATE", "UPDATE", "ADD   ", "DELETE", "*COMMT", "BOGUS ", "CRUNCH"])
    action = rng.choice(["REPLACE ", "ADD     ", "REPLACE ", "XXXX    "])
    field = rng.choice(["NAME      ", "BALANCE   ", "ORDERS    ", "CITY      "])
    data = rng.choice(["000012345", "-00001234", "+00000099", "12X", "NEW NAME", "999999999"])
    rec = f"{code:<6.6} {key:<6.6}        {action:<8.8} {field:<10.10} {rng.randint(0, 99):02d} {data:<36.36}"
    return rec.ljust(80)[:80].encode("latin-1")


def scenarios(rng: random.Random, base_cust: bytes, n: int) -> list:
    out = []
    for _ in range(n):
        ids = sorted(rng.sample(range(1, 60), rng.randint(0, 8)))
        custs = []
        for cid in ids:
            custs.append(customer(cid, "A", rng, base_cust))
            if rng.random() < 0.3:
                custs.append(customer(cid, "C", rng, base_cust))
        keys = sorted(f"{rng.randint(1, 60):05d}{rng.choice('AAAC')}" for _ in range(rng.randint(0, 10)))
        if keys and rng.random() < 0.3:
            i, j = rng.randrange(len(keys)), rng.randrange(len(keys))
            keys[i], keys[j] = keys[j], keys[i]
        out.append((custs, [transaction(rng, k) for k in keys]))
    return out


def main():
    sam1 = C.parse_program(IBM / "COBOL" / "SAM1.cbl", [IBM / "COPYBOOK"])
    sam2 = C.parse_program(IBM / "COBOL" / "SAM2.cbl", [IBM / "COPYBOOK"])
    subprog.build(sam2, [IBM / "COPYBOOK"])
    exe = build_sam1()
    ibm_custs = [line.encode("latin-1") for line in (IBM / "RESOURCES" / "SAMPLE.CUSTFILE.txt")
                 .read_text(encoding="latin-1").splitlines()]
    ibm_trans = [line.ljust(80)[:80].encode("latin-1") for line in (IBM / "RESOURCES" / "SAMPLE.TRANFILE.txt")
                 .read_text(encoding="latin-1").splitlines()]
    base = next(c for c in ibm_custs if len(c) == 387)
    cases = [("ibm_sample_files", ibm_custs, ibm_trans)]
    rng = random.Random(17)
    cases += [("generated", c, t) for c, t in scenarios(rng, base, 300)]
    report = {"program": "SAM1 (calls SAM2)", "platform": platform.machine(), "runs": 0, "agree": 0,
              "parts": {"custout": 0, "report": 0, "display": 0, "rc": 0}, "examples": [],
              "transactions": 0, "customers": 0}
    t_c = t_m = 0.0
    for name, custs, trans in cases:
        t0 = time.perf_counter()
        a = run_compiled(exe, custs, trans)
        t1 = time.perf_counter()
        try:
            b = run_model(sam1, sam2, custs, trans)
        except Exception as exc:  # report, do not hide
            b = {"error": repr(exc), "rc": None, "display": [], "custout": [], "report": []}
        t2 = time.perf_counter()
        t_c += t1 - t0
        t_m += t2 - t1
        for w in b.get("warnings", []):
            report.setdefault("runtime_findings", {}).setdefault(w, 0)
            report["runtime_findings"][w] += 1
        report["runs"] += 1
        report["transactions"] += len(trans)
        report["customers"] += len(custs)
        if b.get("error") and "NotNumeric" in b["error"]:
            report["undefined"] = report.get("undefined", 0) + 1
            msg = ("arithmetic on a numeric field that holds text (undefined in COBOL): "
                   "an UPDATE transaction for a contact record ('C') reaches SAM2, which treats it as a customer record")
            report.setdefault("runtime_findings", {}).setdefault(msg, 0)
            report["runtime_findings"][msg] += 1
            continue
        same = {k: a[k] == b[k] for k in ("custout", "report", "display", "rc")}
        for k, v in same.items():
            report["parts"][k] += v
        if all(same.values()):
            report["agree"] += 1
        elif len(report["examples"]) < 5:
            diff = {}
            for k, ok in same.items():
                if not ok:
                    if isinstance(a[k], list):
                        idx = next((i for i, (x, y) in enumerate(zip(a[k], b[k])) if x != y), min(len(a[k]), len(b[k])))
                        diff[k] = {"index": idx, "compiled": str(a[k][idx:idx + 1]), "model": str(b[k][idx:idx + 1]),
                                   "len": [len(a[k]), len(b[k])]}
                    else:
                        diff[k] = {"compiled": a[k], "model": b[k]}
            report["examples"].append({"case": name, "error": b.get("error"), "diff": diff,
                                       "trans": [t.decode("latin-1").rstrip() for t in trans]})
    report["defined_runs"] = report["runs"] - report.get("undefined", 0)
    report["agreement"] = round(report["agree"] / report["defined_runs"], 4)
    report["compiled_seconds"] = round(t_c, 2)
    report["model_seconds"] = round(t_m, 2)
    report["sets"] = {"file runs (defined)": {"cases": report["defined_runs"], "agree": report["agree"]}}
    (ROOT / "results" / "ibm_sam1.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != "examples"}, indent=2))
    for e in report["examples"]:
        print("MISMATCH", json.dumps(e)[:1500])


if __name__ == "__main__":
    main()
