"""Compare Granite models for the explanation step: latency and guard pass rate.

Each model runs in its own llama-server; give their URLs as name=url.

    python scripts/bench_llm.py granite-3.3-2b=http://127.0.0.1:8080 granite-3.0-1b-a400m=http://127.0.0.1:8081
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from legacylens import explain  # noqa: E402
from legacylens.api import Account, Service  # noqa: E402


def main():
    models = dict(arg.split("=", 1) for arg in sys.argv[1:])
    svc = Service()
    groups = svc.scenarios()
    ids = [g[i] for g in groups.values() for i in range(2) if len(g) > i]
    out = {"accounts": ids, "models": {}}
    for name, url in models.items():
        explain.LLM_URL = url
        rows = []
        for acct_id in ids:
            result = svc.decide(Account(id=acct_id))
            e = explain.explain(result, timeout=180)
            rows.append({"id": acct_id, "final": result["hybrid"]["final"], **e})
            print(name, acct_id, e["source"], e["ms"], "ms |", e.get("note", ""), "|", e.get("rejected", e["text"])[:300])
        ok = [r for r in rows if r["source"] == "granite"]
        out["models"][name] = {"accepted": len(ok), "total": len(rows),
                               "median_ms": sorted(r["ms"] for r in rows)[len(rows) // 2], "rows": rows}
    (ROOT / "results" / "llm_bench.json").write_text(json.dumps(out, indent=2))
    for name, m in out["models"].items():
        print(name, f"accepted {m['accepted']}/{m['total']}", f"median {m['median_ms']} ms")


if __name__ == "__main__":
    main()
