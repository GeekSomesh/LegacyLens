"""IBM's public SAM2 sample (zopeneditor-sample, Apache-2.0): extract the rules,
prune and simplify them with z3, and check them against the compiled program.

    python scripts/verify_ibm.py [path to zopeneditor-sample]   # results/ibm_sam2.json
"""
import itertools
import json
import platform
import random
import sys
import time
from collections import Counter
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from legacylens import cobol as C, rules as R, smt, subprog  # noqa: E402

SAMPLES = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "samples" / "ibm-zopeneditor"


def tran(code="UPDATE", key="00001A", action="REPLACE ", field="BALANCE   ", ss="01", data=""):
    rec = f"{code:<6.6} {key:<6.6}        {action:<8.8} {field:<10.10} {ss:<2.2} {data:<36.36}"
    return rec.ljust(80)[:80].encode("latin-1")


def crunch(loops=2, code="CRUNCH"):
    rec = bytearray(tran(code=code, action="", field="", ss="", data=""))
    rec[14:23] = f"{loops:09d}".encode()
    return bytes(rec)


def set_field(prog, rec: bytes, owner: str, name: str, value) -> bytes:
    st = R.State(prog)
    buf = bytearray(rec)
    st.bind(prog.fields[owner].record, buf)
    st.set(C.Ref(name), value)
    return bytes(buf)


def main():
    src = SAMPLES / "COBOL" / "SAM2.cbl"
    dirs = [SAMPLES / "COPYBOOK"]
    prog = C.parse_program(src, dirs)
    t0 = time.perf_counter()
    ex, raw_rules = R.extract(prog)
    t1 = time.perf_counter()
    z3_report, rules, analysis = smt.analyse(prog, raw_rules)
    t2 = time.perf_counter()
    engine = R.Engine(prog, rules)
    exe, lib = subprog.build(prog, dirs)

    custs = [line.rstrip("\n").encode("latin-1") for line in
             (SAMPLES / "RESOURCES" / "SAMPLE.CUSTFILE.txt").read_text(encoding="latin-1").splitlines()
             if len(line) >= 387]
    trans = [line.rstrip("\n").ljust(80)[:80].encode("latin-1") for line in
             (SAMPLES / "RESOURCES" / "SAMPLE.TRANFILE.txt").read_text(encoding="latin-1").splitlines()]
    blank = subprog.blank_params(prog)

    def case(cust, tr, ok=b"X", msg=b" " * 50):
        return {"CUST-REC": cust, "TRANSACTION-RECORD": tr, "TRAN-OK": ok, "TRAN-MSG": msg}

    sets = {}
    sets["ibm_sample_data"] = [case(c, t) for c in custs for t in trans]

    # one z3-generated example per rule
    witnesses = {}
    wcases = []
    for r in rules:
        w = analysis.witness(r.id)
        if w is None:
            continue
        params = subprog.apply_values(prog, dict(blank, **{"CUST-REC": custs[0]}), w)
        if params["TRANSACTION-RECORD"][:6] == b"CRUNCH":
            params["TRANSACTION-RECORD"] = set_field(prog, params["TRANSACTION-RECORD"], "TRANSACTION-RECORD",
                                                     "CRUNCH-CPU-LOOPS", Decimal(2))
        witnesses[r.id] = params
        wcases.append(params)
    sets["z3_examples"] = wcases

    # systematic grid
    codes = ["UPDATE", "ADD   ", "DELETE", "*COMMT", "      ", "update"]
    actions = ["REPLACE ", "ADD     ", "DELETE  ", "        ", "REPLACEX"]
    fields = ["NAME      ", "BALANCE   ", "ORDERS    ", "ADDR      ", "          "]
    datas = ["000012345", "-00001234", "+00000099", "12345678X", "ABC", "", "999999999", "-99999999",
             "00000001A", "NEW NAME CORP"]
    custs_grid = [custs[0], set_field(prog, custs[1], "CUST-REC", "CUST-ACCT-BALANCE", Decimal("9999999.99")),
                  set_field(prog, custs[2], "CUST-REC", "CUST-ORDERS-YTD", Decimal(0))]
    grid = [case(c, tran(code=k, action=a, field=f, data=d))
            for k, a, f, d, c in itertools.product(codes, actions, fields, datas, custs_grid)]
    blank_months = bytearray(custs[0])
    blank_months[84:84 + 108] = b" " * 108
    grid += [case(c, crunch(n)) for n in (0, 1, 3) for c in (custs[0], bytes(blank_months))]
    sets["grid"] = grid

    # random variations
    rng = random.Random(5)
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 +-*"
    rnd = []
    for _ in range(3000):
        k = rng.choice(codes + ["CRUNCH"] * 2 + ["".join(rng.choice(alphabet) for _ in range(6))])
        if k == "CRUNCH":
            rnd.append(case(rng.choice(custs_grid), crunch(rng.randint(0, 3))))
            continue
        data = rng.choice(datas + ["".join(rng.choice(alphabet) for _ in range(rng.randint(0, 12)))]
                          + [f"{rng.choice('+- 0')}{rng.randint(0, 99999999):08d}"])
        rnd.append(case(rng.choice(custs_grid), tran(code=k, action=rng.choice(actions), field=rng.choice(fields),
                                                     ss=f"{rng.randint(0, 99):02d}", data=data)))
    sets["random"] = rnd

    report = {"program": prog.name, "source": "IBM zopeneditor-sample COBOL/SAM2.cbl (Apache-2.0)",
              "platform": platform.machine(), "parameters": prog.using,
              "decision_fields": sorted(ex.dec), "raw_paths": len(raw_rules), "rules": len(rules),
              "extract_seconds": round(t1 - t0, 2), "z3_seconds": round(t2 - t1, 2),
              "z3": {"infeasible_paths": len(z3_report["infeasible"]), "unknown": z3_report["unknown"],
                     "overlapping_pairs": z3_report["overlaps"],
                     "conditions_removed": sum(len(v["removed"]) for v in z3_report["rules"].values()),
                     "all_simplifications_proved": all(v["equivalent"] for v in z3_report["rules"].values())},
              "sets": {}, "per_rule": Counter(), "examples": []}
    for name, cases in sets.items():
        t0 = time.perf_counter()
        cobol = subprog.run(prog, exe, lib, cases)
        t1 = time.perf_counter()
        agree = undefined = none = multi = 0
        for c, out in zip(cases, cobol):
            rule, info, st, n = engine.decide(c)
            if rule is None:
                if info.get("undefined"):
                    undefined += 1
                else:
                    none += 1
                mine = None
            else:
                multi += n > 1
                report["per_rule"][rule.id] += 1
                mine = engine.output(rule, st)
            if mine == out:
                agree += 1
            elif rule is not None and len(report["examples"]) < 10:
                diff = [u for u, a, b in zip(prog.using, mine, out) if a != b]
                report["examples"].append({"set": name, "rule": rule.id, "params_differ": diff,
                                           "tran": c["TRANSACTION-RECORD"].decode("latin-1"),
                                           "cobol": {u: o for u, o in zip(prog.using, out) if u in diff},
                                           "rules": {u: m for u, m, o in zip(prog.using, mine, out) if m != o}})
        t2 = time.perf_counter()
        defined = len(cases) - undefined
        report["sets"][name] = {"cases": len(cases), "agree": agree, "undefined": undefined,
                                "no_rule": none, "several_rules": multi, "defined": defined,
                                "agreement_on_defined": round(agree / defined, 4) if defined else None,
                                "cobol_seconds": round(t1 - t0, 2), "engine_seconds": round(t2 - t1, 2)}
    # does each z3 example reach its own rule?
    confirmed = 0
    for rid, params in witnesses.items():
        rule, *_ = engine.decide(params)
        confirmed += rule is not None and rule.id == rid
    report["z3"]["examples_reaching_their_rule"] = f"{confirmed} of {len(rules)}"
    report["per_rule"] = {r.id: report["per_rule"].get(r.id, 0) for r in rules}
    report["never_hit"] = [k for k, v in report["per_rule"].items() if v == 0]
    report["rule_list"] = [{"id": r.id, "actions": {k: str(v).strip() for k, v in r.actions.items()},
                            "conditions": z3_report["rules"][r.id]["kept"],
                            "removed": z3_report["rules"][r.id]["removed"]} for r in rules]
    out = ROOT / "results" / "ibm_sam2.json"
    out.write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps({k: report[k] for k in ("raw_paths", "rules", "z3", "never_hit", "z3_seconds")}, indent=2, default=str))
    for k, v in report["sets"].items():
        print(k, v)
    for e in report["examples"][:4]:
        print("MISMATCH", json.dumps(e)[:900])


if __name__ == "__main__":
    main()
