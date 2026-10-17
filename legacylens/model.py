"""Default-risk model: trained with scikit-learn, stored as ONNX, served on
LinuxONE by IBM Snap ML on the on-chip AI accelerator (NNPA, through zDNN).

The ONNX file is the single source of truth for inference. Backends:
  nnpa  - Snap ML, tree_format="zdnn_tensors" (IBM z16/z17 AI accelerator)
  cpu   - Snap ML, tree_format="compress_trees"
  onnx  - the ONNX reference evaluator (pure Python, exact, slow)
"""
from __future__ import annotations

import json
import time
import warnings
from pathlib import Path

import numpy as np

from . import data

ROOT = Path(__file__).resolve().parent.parent
MODEL = ROOT / "models" / "risk_gbm.onnx"


def train(parts: dict, seed: int = 0):
    from sklearn.ensemble import GradientBoostingClassifier
    X = data.feature_matrix(parts["train"])
    y = parts["train"]["DEFAULT"].to_numpy()
    m = GradientBoostingClassifier(n_estimators=200, max_depth=3, learning_rate=0.05,
                                   subsample=1.0, random_state=seed)
    m.fit(X, y)
    return m


def choose_thresholds(p: np.ndarray, y: np.ndarray) -> dict:
    """On validation data: 'high' maximises F1 for default; 'low' is the largest
    score below which at most 8% of accounts defaulted."""
    best_f1, high = 0.0, 0.5
    for t in np.linspace(0.05, 0.95, 181):
        pred = p >= t
        tp = int((pred & (y == 1)).sum())
        if tp == 0:
            continue
        prec, rec = tp / pred.sum(), tp / (y == 1).sum()
        f1 = 2 * prec * rec / (prec + rec)
        if f1 > best_f1:
            best_f1, high = f1, float(t)
    low = 0.05
    for t in np.linspace(0.02, high, 200):
        sel = p < t
        if sel.sum() >= 50 and y[sel].mean() <= 0.08:
            low = float(t)
    return {"low": round(low, 4), "high": round(high, 4)}


def export_onnx(model, path: Path, meta: dict) -> Path:
    from skl2onnx import to_onnx
    X = np.zeros((1, len(data.FEATURES)), dtype=np.float32)
    onx = to_onnx(model, X, options={id(model): {"zipmap": False}},
                  target_opset={"": 17, "ai.onnx.ml": 3})
    for k, v in meta.items():
        p = onx.metadata_props.add()
        p.key, p.value = k, json.dumps(v)
    onx.producer_name = "legacylens"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(onx.SerializeToString())
    return path


def onnx_meta(path: Path = MODEL) -> dict:
    import onnx
    m = onnx.load(str(path))
    return {p.key: json.loads(p.value) for p in m.metadata_props}


class RiskModel:
    """Loads the ONNX model once and scores accounts with the chosen backend."""

    def __init__(self, path: Path = MODEL, backend: str = "auto"):
        self.path = Path(path)
        self.meta = onnx_meta(self.path)
        self.backend = backend
        self._snap = None
        self._ref = None
        if backend in ("auto", "nnpa", "cpu"):
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    from snapml import BoostingMachineClassifier
                bm = BoostingMachineClassifier()
                fmt = {"auto": "auto", "nnpa": "zdnn_tensors", "cpu": "compress_trees"}[backend]
                bm.import_model(str(self.path), "onnx", tree_format=fmt)
                self._snap = bm
                self.backend = "nnpa" if bm.booster_.optimized_tree_format_ == "zdnn_tensors" else "cpu"
            except Exception as exc:  # Snap ML missing or not on Z: fall back to exact ONNX
                if backend != "auto":
                    raise
                self.error = repr(exc)
                self.backend = "onnx"
        if self.backend == "onnx":
            import onnx
            from onnx.reference import ReferenceEvaluator
            self._ref = ReferenceEvaluator(onnx.load(str(self.path)))

    def proba(self, X: np.ndarray) -> np.ndarray:
        X = np.ascontiguousarray(X, dtype=np.float32)
        if self._snap is not None:
            return self._snap.predict_proba(X)[:, 1]
        out = self._ref.run(None, {"X": X})
        return np.asarray(out[1])[:, 1]

    def timed(self, X: np.ndarray):
        t0 = time.perf_counter()
        p = self.proba(X)
        return p, (time.perf_counter() - t0) * 1000


def band(p: float, thresholds: dict) -> str:
    if p >= thresholds["high"]:
        return "HIGH"
    if p < thresholds["low"]:
        return "LOW"
    return "MEDIUM"


def explain_factors(rm: RiskModel, x: np.ndarray, reference: np.ndarray, top: int = 3) -> list:
    """Which features move this account's risk most: replace one feature at a
    time with the portfolio median and measure the change in score."""
    base = rm.proba(x[None, :])[0]
    rows = np.repeat(x[None, :], len(data.FEATURES), axis=0)
    for j in range(len(data.FEATURES)):
        rows[j, j] = reference[j]
    p = rm.proba(rows)
    deltas = base - p
    order = np.argsort(-np.abs(deltas))[:top]
    return [{"feature": data.FEATURES[j], "value": float(x[j]), "median": float(reference[j]),
             "effect": float(deltas[j])} for j in order]
