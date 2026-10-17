"""Change impact of CREDREV version 2 against version 1, proved with z3 and confirmed
with both compiled programs on z3's example accounts and on all 30,000 real accounts.

    python scripts/impact_credrev.py      # results/impact_credrev.json
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from legacylens import cobol as C, data, impact, rules as R  # noqa: E402
from legacylens.runner import compile_program, run_batch  # noqa: E402

OLD = ROOT / "cobol" / "CREDREV.cbl"
NEW = ROOT / "cobol" / "CREDREV_v2.cbl"


def main():
    old, new = C.parse_program(OLD), C.parse_program(NEW)
    rep = impact.compare(old, new)
    old_rules, new_rules = rep.pop("_rules")
    eng_old, eng_new = R.Engine(old, old_rules), R.Engine(new, new_rules)
    exe_old, exe_new = compile_program(OLD), compile_program(NEW)

    # confirm every z3 example with both compiled programs
    group = next(rest[0].target.name for kind, *rest in old_rules[0].steps if kind == "accept")
    examples = []
    for reg in rep["regions"]:
        if not reg["example"]:
            continue
        rec = impact.record_from_values(old, group, {k: v for k, v in reg["example"].items() if k != "IN-ACCT"})
        rec = "000001" + rec[6:]
        reg["record"] = rec
        examples.append(rec)
    if examples:
        out_old, out_new = run_batch(exe_old, examples), run_batch(exe_new, examples)
        i = 0
        for reg in rep["regions"]:
            if "record" not in reg:
                continue
            a, b = out_old[i], out_new[i]
            i += 1
            r_old, _, st_o, _ = eng_old.decide(reg["record"])
            r_new, _, st_n, _ = eng_new.decide(reg["record"])
            reg["compiled_old"], reg["compiled_new"] = a, b
            reg["confirmed"] = (a != b and r_old is not None and r_new is not None
                                and r_old.id == reg["old_rule"] and r_new.id == reg["new_rule"]
                                and eng_old.output(r_old, st_o)[0] == a and eng_new.output(r_new, st_n)[0] == b)

    # the whole portfolio through both compiled programs
    df = data.load()
    recs = [data.to_record(r) for _, r in df.iterrows()]
    out_old, out_new = run_batch(exe_old, recs), run_batch(exe_new, recs)
    def decision(line):
        parts = line.split()
        return f"{parts[1]} ({parts[2]})"
    moves = impact.transitions(out_old, out_new, pick=decision)
    by_region = {}
    for rec, a, b in zip(recs, out_old, out_new):
        if decision(a) != decision(b):
            r_old, *_ = eng_old.decide(rec)
            r_new, *_ = eng_new.decide(rec)
            key = f"{r_old.id} -> {r_new.id}"
            by_region[key] = by_region.get(key, 0) + 1
    defaulted = dict(zip(recs, df["DEFAULT"].tolist()))
    move_rows = []
    for (a, b), n in sorted(moves.items(), key=lambda kv: -kv[1]):
        d = sum(defaulted[r] for r, x, y in zip(recs, out_old, out_new) if decision(x) == a and decision(y) == b)
        move_rows.append({"from": a, "to": b, "accounts": n, "defaulted_next_month": d})
    covered = all(k in {f"{r['old_rule']} -> {r['new_rule']}" for r in rep["regions"]} for k in by_region)
    rep["portfolio"] = {"accounts": len(recs), "changed": sum(moves.values()), "moves": move_rows,
                        "by_rule_pair": by_region, "every_change_inside_a_z3_region": covered}
    rep["portfolio"]["decision_changed"] = sum(m["accounts"] for m in move_rows if m["from"].split()[0] != m["to"].split()[0])
    (ROOT / "results" / "impact_credrev.json").write_text(json.dumps(rep, indent=2, default=str))
    print("\n".join(rep["diff"]))
    print(json.dumps({k: rep[k] for k in ("rules_old", "rules_new", "changed", "removed", "added")}, indent=1)[:2500])
    for reg in rep["regions"]:
        print(reg["old_rule"], "->", reg["new_rule"], reg["status"], reg["old"], "->", reg["new"],
              "| confirmed:", reg.get("confirmed"), "|", reg.get("compiled_old"), "=>", reg.get("compiled_new"))
    print(json.dumps(rep["portfolio"], indent=1))


if __name__ == "__main__":
    main()
