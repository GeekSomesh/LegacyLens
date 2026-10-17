"""Policy (compiled COBOL) vs policy + AI risk on validation and test accounts.

The combination table in legacylens/hybrid.py was chosen from the validation
numbers; the test numbers are the result to report.

    python scripts/evaluate_hybrid.py     # results/hybrid.json
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from legacylens import data, hybrid, model  # noqa: E402
from legacylens.runner import compile_program, run_batch  # noqa: E402


def main():
    parts = data.split(data.load())
    exe = compile_program(ROOT / "cobol" / "CREDREV.cbl")
    rm = model.RiskModel()
    th = rm.meta["thresholds"]
    result = {"backend": rm.backend, "thresholds": th}
    for name in ("val", "test"):
        d = parts[name]
        policy = [line.split()[1] for line in run_batch(exe, [data.to_record(r) for _, r in d.iterrows()])]
        bands = [model.band(v, th) for v in rm.proba(data.feature_matrix(d))]
        result[name] = hybrid.impact(policy, bands, d["DEFAULT"].tolist())
    (ROOT / "results" / "hybrid.json").write_text(json.dumps(result, indent=2))
    t = result["test"]
    print(json.dumps({k: t[k] for k in ("accounts", "default_rate", "limit_increases",
                                        "review_queue", "early_warnings", "changes")}, indent=2))


if __name__ == "__main__":
    main()
