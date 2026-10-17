"""Verify CREDREV's extracted rules against the GnuCOBOL build on real and generated accounts.

    python scripts/verify_rules.py            # writes results/verification.json
"""
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from legacylens import cobol as C, data, rules as R  # noqa: E402
from legacylens.verify import boundary_records, random_records, verify  # noqa: E402

SOURCE = ROOT / "cobol" / "CREDREV.cbl"


def main():
    rng = np.random.default_rng(11)
    df = data.load()
    real = [data.to_record(r) for _, r in df.iterrows()]
    prog = C.parse_program(SOURCE)
    _, rules = R.extract(prog)
    sets = {
        "uci_accounts": real,
        "boundaries": boundary_records(prog, rules, real, rng),
        "random": random_records(prog, rules, 5000, rng),
    }
    report = verify(SOURCE, sets)
    out = ROOT / "results" / "verification.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("program", "rules", "platform", "records", "agreement",
                                             "per_rule", "never_hit")}, indent=2))
    for name, s in report["sets"].items():
        print(name, s)
    for ex in report["examples"][:5]:
        print("MISMATCH", ex)


if __name__ == "__main__":
    main()
