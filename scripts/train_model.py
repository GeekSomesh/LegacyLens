"""Train the default-risk model, export it to ONNX and check it on LinuxONE.

    python scripts/train_model.py     # models/risk_gbm.onnx, models/reference.json, results/model.json
"""
import json
import platform
import statistics
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score  # noqa: E402

from legacylens import data, model  # noqa: E402


def latency_ms(fn, X, repeat=200):
    fn(X)  # warm up
    times = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn(X)
        times.append((time.perf_counter() - t0) * 1000)
    return round(statistics.median(times), 4)


def main():
    df = data.load()
    parts = data.split(df)
    t0 = time.perf_counter()
    gbm = model.train(parts)
    fit_s = time.perf_counter() - t0
    Xv, yv = data.feature_matrix(parts["val"]), parts["val"]["DEFAULT"].to_numpy()
    Xt, yt = data.feature_matrix(parts["test"]), parts["test"]["DEFAULT"].to_numpy()
    thresholds = model.choose_thresholds(gbm.predict_proba(Xv)[:, 1], yv)
    p_sk = gbm.predict_proba(Xt)[:, 1]
    hi = p_sk >= thresholds["high"]
    metrics = {
        "auc": round(roc_auc_score(yt, p_sk), 4),
        "average_precision": round(average_precision_score(yt, p_sk), 4),
        "brier": round(brier_score_loss(yt, p_sk), 4),
        "high_band_precision": round(float(yt[hi].mean()), 4),
        "high_band_recall": round(float(hi[yt == 1].mean()), 4),
        "test_default_rate": round(float(yt.mean()), 4),
    }
    meta = {"features": data.FEATURES, "thresholds": thresholds, "metrics_test": metrics,
            "dataset": "UCI Default of Credit Card Clients (CC BY 4.0), doi:10.24432/C55S3H",
            "split": {k: len(v) for k, v in parts.items()},
            "trained_on": f"{platform.machine()} {platform.node()}",
            "estimator": "sklearn GradientBoostingClassifier(n_estimators=200, max_depth=3, lr=0.05)"}
    path = model.export_onnx(gbm, model.MODEL, meta)
    Xtr = data.feature_matrix(parts["train"])
    (ROOT / "models" / "reference.json").write_text(
        json.dumps({"features": data.FEATURES, "median": np.median(Xtr, axis=0).tolist()}, indent=1))

    # ONNX parity and speed on every backend
    backends = {}
    exact = model.RiskModel(path, backend="onnx")
    p_ref = exact.proba(Xt)
    backends["onnx_reference"] = {"max_abs_diff_vs_sklearn": float(np.abs(p_ref - p_sk).max())}
    for name in ("nnpa", "cpu"):
        try:
            rm = model.RiskModel(path, backend=name)
        except Exception as exc:
            backends[name] = {"error": repr(exc)}
            continue
        p = rm.proba(Xt)
        bands_ref = [model.band(v, thresholds) for v in p_ref]
        bands = [model.band(v, thresholds) for v in p]
        backends[name] = {
            "auc": round(roc_auc_score(yt, p), 4),
            "max_abs_diff_vs_onnx": round(float(np.abs(p - p_ref).max()), 5),
            "mean_abs_diff_vs_onnx": round(float(np.abs(p - p_ref).mean()), 6),
            "band_agreement_vs_onnx": round(sum(a == b for a, b in zip(bands, bands_ref)) / len(p), 5),
            "latency_ms_one_account": latency_ms(rm.proba, Xt[:1]),
            "latency_ms_test_batch": latency_ms(rm.proba, Xt, repeat=20),
        }
    backends["onnx_reference"]["latency_ms_one_account"] = latency_ms(exact.proba, Xt[:1], repeat=20)
    backends["sklearn"] = {"latency_ms_one_account": latency_ms(lambda X: gbm.predict_proba(X), Xt[:1]),
                           "latency_ms_test_batch": latency_ms(lambda X: gbm.predict_proba(X), Xt, repeat=20)}
    result = {"model": str(path.relative_to(ROOT)), "onnx_bytes": path.stat().st_size,
              "fit_seconds": round(fit_s, 1), "thresholds": thresholds, "metrics_test": metrics,
              "test_accounts": int(len(yt)), "backends": backends, "platform": platform.machine()}
    (ROOT / "results" / "model.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
