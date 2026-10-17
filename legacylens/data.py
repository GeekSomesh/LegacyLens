"""The UCI "Default of Credit Card Clients" data set and how accounts map to
the COBOL record and to model features.

Source: Yeh, I-C. & Lien, C-H. (2009), UCI Machine Learning Repository,
https://doi.org/10.24432/C55S3H (CC BY 4.0). 30,000 Taiwanese card holders,
April-September 2005, with whether each defaulted in October 2005.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = Path.home() / "data" / "default of credit card clients.xls"

PAY = ["PAY_0", "PAY_2", "PAY_3", "PAY_4", "PAY_5", "PAY_6"]
BILL = [f"BILL_AMT{i}" for i in range(1, 7)]
PAID = [f"PAY_AMT{i}" for i in range(1, 7)]
# Behaviour only: sex, education, marital status and age are left out on purpose.
RAW_FEATURES = ["LIMIT_BAL"] + PAY + BILL + PAID
FEATURES = RAW_FEATURES + ["UTIL_1", "PAID_RATIO_1", "MONTHS_LATE_6"]
LABEL = "default payment next month"


def load(path: Path = DATA) -> pd.DataFrame:
    df = pd.read_excel(path, header=1)
    df = df.rename(columns={LABEL: "DEFAULT"})
    return df


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    limit = out["LIMIT_BAL"].clip(lower=1)
    out["UTIL_1"] = out["BILL_AMT1"] / limit
    out["PAID_RATIO_1"] = out["PAY_AMT1"] / out["BILL_AMT1"].clip(lower=1)
    out["MONTHS_LATE_6"] = (out[PAY] >= 1).sum(axis=1)
    return out


def feature_matrix(df: pd.DataFrame) -> np.ndarray:
    return add_features(df)[FEATURES].to_numpy(dtype=np.float32)


def clamp(v, hi: int) -> int:
    return int(min(max(int(v), 0), hi))


def to_record(row) -> str:
    """One account as the 29-byte CREDREV input record.

    LIMIT, BILL (latest statement, negative balances as 0) and PAID (latest
    payment); DELINQ = months past due in the latest month (PAY_0, 0 if paid
    duly); LATE-6M = number of the last six months with a payment delay.
    """
    late = sum(1 for c in PAY if row[c] >= 1)
    return (f"{clamp(row['ID'], 999999):06d}"
            f"{clamp(row['LIMIT_BAL'], 9999999):07d}"
            f"{clamp(row['BILL_AMT1'], 9999999):07d}"
            f"{clamp(row['PAY_AMT1'], 9999999):07d}"
            f"{clamp(row['PAY_0'], 9):01d}"
            f"{clamp(late, 9):01d}")


def account_from_record(rec: str) -> dict:
    return {"ACCT": int(rec[0:6]), "LIMIT": int(rec[6:13]), "BILL": int(rec[13:20]),
            "PAID": int(rec[20:27]), "DELINQ": int(rec[27]), "LATE_6M": int(rec[28])}


def split(df: pd.DataFrame, seed: int = 7):
    """Stratified 70/15/15 train/validation/test split by account."""
    rng = np.random.default_rng(seed)
    parts = {"train": [], "val": [], "test": []}
    for _, grp in df.groupby("DEFAULT"):
        idx = rng.permutation(grp.index.to_numpy())
        n = len(idx)
        a, b = int(n * 0.70), int(n * 0.85)
        parts["train"].append(idx[:a])
        parts["val"].append(idx[a:b])
        parts["test"].append(idx[b:])
    return {k: df.loc[np.sort(np.concatenate(v))] for k, v in parts.items()}
