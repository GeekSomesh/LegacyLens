"""Construct conformance: small COBOL programs, each run on random input records by
the GnuCOBOL build and by LegacyLens's interpreter; every DISPLAY line must match.

    python scripts/conformance.py [records per program]     # results/conformance.json
"""
import json
import platform
import random
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from legacylens import cobol as C, rules as R  # noqa: E402
from legacylens.runner import compile_program, run_batch  # noqa: E402

PROGRAMS = sorted((ROOT / "tests" / "cobol").glob("C*.cbl"))
COVERS = {
    "C01GOTO": "GO TO, PERFORM THRU, EXIT paragraph",
    "C02LOOPS": "OCCURS, PERFORM VARYING, TEST AFTER, inline PERFORM UNTIL",
    "C03STORE": "REDEFINES, COMP-3, COMP, signed DISPLAY, division",
    "C04EDIT": "signed arithmetic, ROUNDED, edited pictures (Z , . - CR $ * floating)",
    "C05TEXT": "88 lists, SET TRUE, EVALUATE THRU, reference modification, INITIALIZE",
}


def literals(prog: C.Program) -> list:
    out = set()
    for p in prog.paragraphs:
        for s in R.walk_stmts(p.stmts):
            for node in _nodes(s):
                if isinstance(node, C.Lit) and isinstance(node.value, str) and node.value.strip():
                    out.add(node.value)
    for cn in prog.conds.values():
        for lo, hi in cn.values:
            for v in (lo, hi):
                if isinstance(v, str):
                    out.add(v)
    return sorted(out)


def _nodes(x):
    if isinstance(x, list):
        for y in x:
            yield from _nodes(y)
        return
    yield x
    if hasattr(x, "__dataclass_fields__"):
        for k in x.__dataclass_fields__:
            v = getattr(x, k)
            if isinstance(v, (list, tuple)) or hasattr(v, "__dataclass_fields__"):
                yield from _nodes(list(v) if isinstance(v, tuple) else v)


def records(prog: C.Program, n: int, rng: random.Random) -> list:
    rec = next(f for f in prog.fields.values() if f.name == "IN-REC")
    lits = literals(prog)
    leaves = []

    def walk(f):
        if f.pic is not None:
            leaves.append(f)
        for k in f.children:
            walk(prog.fields[k])
    walk(rec)
    out = []
    for _ in range(n):
        buf = bytearray(b" " * rec.length)
        for f in leaves:
            count = f.dims[-1][1] if f.dims else 1
            stride = f.dims[-1][0] if f.dims else f.length
            for k in range(count):
                if f.pic.kind == "num":
                    hi = 10 ** f.length - 1
                    v = rng.choice([0, hi, rng.randint(0, hi), rng.randint(0, min(hi, 999))])
                    text = str(v).rjust(f.length, "0")
                else:
                    pool = lits + ["-", "+", " ", "A", "X", "Z"]
                    text = rng.choice(pool) if rng.random() < 0.6 else "".join(
                        rng.choice("ABCXZ-+ 0123456789") for _ in range(f.length))
                    text = text[:f.length].ljust(f.length)
                off = f.offset - rec.offset + k * stride
                buf[off:off + f.length] = text.encode("latin-1")
        out.append(buf.decode("latin-1"))
    return out


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    rng = random.Random(3)
    report = {"platform": platform.machine(), "programs": {}, "total": 0, "agree": 0}
    for path in PROGRAMS:
        prog = C.parse_program(path)
        recs = records(prog, n, rng)
        exe = compile_program(path)
        cobol = run_batch(exe, recs)
        st = R.State(prog)
        out = []
        st.run_program(inputs=iter([f"{len(recs):06d}"] + recs), out=out)
        agree = sum(a == b for a, b in zip(cobol, out))
        first = next(((r, a, b) for r, a, b in zip(recs, cobol, out) if a != b), None)
        report["programs"][prog.name] = {"covers": COVERS.get(prog.name, ""), "records": len(recs),
                                         "agree": agree, "diagnostics": prog.diagnostics,
                                         "first_difference": first and {"input": first[0], "cobol": first[1],
                                                                        "interpreter": first[2]}}
        report["total"] += len(recs)
        report["agree"] += agree
        print(f"{prog.name:9} {agree}/{len(recs)}  {COVERS.get(prog.name, '')}")
        if first:
            print("   input      ", repr(first[0]))
            print("   cobol      ", repr(first[1]))
            print("   interpreter", repr(first[2]))
    report["agreement"] = round(report["agree"] / report["total"], 4)
    (ROOT / "results" / "conformance.json").write_text(json.dumps(report, indent=2))
    print("total", report["agree"], "/", report["total"])


if __name__ == "__main__":
    main()
