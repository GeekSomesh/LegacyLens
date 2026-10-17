"""How fast LegacyLens decides on LinuxONE.

    python scripts/benchmark.py [api_url]     # results/benchmark.json

Measures the compiled COBOL policy on all 30,000 accounts in one batch, the
ONNX risk model on the same accounts (NNPA and CPU), the hybrid decision for
the whole portfolio, and, if an API URL is given, the latency of one decision
through the web service.
"""
import json
import platform
import statistics
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from legacylens import data, hybrid, model  # noqa: E402
from legacylens.runner import compile_program, run_batch  # noqa: E402


def best_of(fn, n=5):
    times = []
    out = None
    for _ in range(n):
        t0 = time.perf_counter()
        out = fn()
        times.append(time.perf_counter() - t0)
    return min(times), out


def main():
    api = sys.argv[1] if len(sys.argv) > 1 else None
    df = data.load()
    records = [data.to_record(r) for _, r in df.iterrows()]
    X = data.feature_matrix(df)
    exe = compile_program(ROOT / "cobol" / "CREDREV.cbl")
    res = {"platform": platform.machine(), "node": platform.node(), "accounts": len(records)}

    t, lines = best_of(lambda: run_batch(exe, records))
    res["cobol_batch"] = {"seconds": round(t, 4), "per_second": round(len(records) / t)}

    for backend in ("nnpa", "cpu"):
        rm = model.RiskModel(backend=backend)
        t, _ = best_of(lambda: rm.proba(X))
        one = []
        for i in range(500):
            t0 = time.perf_counter()
            rm.proba(X[i:i + 1])
            one.append(time.perf_counter() - t0)
        res[f"risk_{backend}"] = {"batch_seconds": round(t, 4), "per_second": round(len(X) / t),
                                  "one_account_us_p50": round(statistics.median(one) * 1e6, 1),
                                  "one_account_us_p99": round(sorted(one)[int(len(one) * 0.99)] * 1e6, 1)}

    rm = model.RiskModel()
    th = rm.meta["thresholds"]

    def portfolio():
        policy = [line.split()[1] for line in run_batch(exe, records)]
        p = rm.proba(X)
        return [hybrid.combine(a, model.band(b, th))["final"] for a, b in zip(policy, p)]
    t, finals = best_of(portfolio, 3)
    res["hybrid_portfolio"] = {"seconds": round(t, 3), "per_second": round(len(records) / t),
                               "note": "compiled COBOL policy + ONNX risk on NNPA + hybrid rule, all 30,000 accounts"}

    if api:
        lat = []
        for i in range(1, 201):
            body = json.dumps({"id": i}).encode()
            req = urllib.request.Request(f"{api}/api/decide", data=body, headers={"Content-Type": "application/json"})
            t0 = time.perf_counter()
            urllib.request.urlopen(req).read()
            lat.append(time.perf_counter() - t0)
        res["api_decide"] = {"calls": len(lat), "ms_p50": round(statistics.median(lat) * 1000, 2),
                             "ms_p95": round(sorted(lat)[int(len(lat) * 0.95)] * 1000, 2),
                             "note": "one account through the web service: rules, live COBOL check, ONNX risk, factors, hybrid"}
    (ROOT / "results" / "benchmark.json").write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
