"""Verify FRAUDHLD's extracted rules against its GnuCOBOL build on generated
authorisation requests and on each condition's boundaries.

    python scripts/verify_fraud.py      # results/fraud_verification.json
"""
import json
import random
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from legacylens import cobol as C, rules as R  # noqa: E402
from legacylens.verify import boundary_records, verify  # noqa: E402

SOURCE = ROOT / "cobol" / "FRAUDHLD.cbl"
COUNTRIES = ["X1", "X2", "X3", "X4", "X5", "GB", "US", "IN", "DE", "SG"]
MCCS = [5411, 5812, 4829, 7995, 6010, 6011, 6051, 5999, 5311]


def request(rng, i):
    home = rng.choice(["GB", "US", "IN", "DE", "SG"])
    country = home if rng.random() < 0.6 else rng.choice(COUNTRIES)
    avg = rng.choice([0, round(10 ** rng.uniform(0, 4), 2)])
    amount = round(min(avg * 10 ** rng.uniform(-1, 1.5), 9999999.99), 2) if avg and rng.random() < 0.8 \
        else round(10 ** rng.uniform(0, 5), 2)
    return (f"{i % 1000000:06d}{int(round(amount * 100)):09d}{int(round(avg * 100)):09d}"
            f"{rng.choice([0, 1, 2, 5, 20, 21, 35, rng.randint(0, 99)]):02d}{rng.choice(MCCS):04d}"
            f"{country}{home}{rng.randint(0, 23):02d}{rng.choice('PE')}{rng.choice('YN ')}")


def main():
    rng = random.Random(21)
    reqs = [request(rng, i) for i in range(20000)]
    prog = C.parse_program(SOURCE)
    _, rules = R.extract(prog)
    sets = {"generated_requests": reqs,
            "boundaries": boundary_records(prog, rules, reqs, np.random.default_rng(5))}
    report = verify(SOURCE, sets)
    (ROOT / "results" / "fraud_verification.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({k: report[k] for k in ("program", "rules", "records", "agreement", "per_rule", "never_hit")}, indent=1))
    for ex in report["examples"][:3]:
        print("MISMATCH", ex)


if __name__ == "__main__":
    main()
