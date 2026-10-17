"""LegacyLens web service: one account in, a verified and explained decision out.

    uvicorn legacylens.api:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import json
import platform
import random
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import cobol as C
from . import data, explain, glossary, hybrid, model
from . import rules as R
from .runner import compile_program, run_batch

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "cobol" / "CREDREV.cbl"
STATIC = Path(__file__).resolve().parent / "static"


class Account(BaseModel):
    id: int | None = None                # a UCI account id, or the fields below
    LIMIT_BAL: float | None = None
    PAY_0: int = 0
    PAY_2: int = 0
    PAY_3: int = 0
    PAY_4: int = 0
    PAY_5: int = 0
    PAY_6: int = 0
    BILL_AMT1: float = 0
    BILL_AMT2: float = 0
    BILL_AMT3: float = 0
    BILL_AMT4: float = 0
    BILL_AMT5: float = 0
    BILL_AMT6: float = 0
    PAY_AMT1: float = 0
    PAY_AMT2: float = 0
    PAY_AMT3: float = 0
    PAY_AMT4: float = 0
    PAY_AMT5: float = 0
    PAY_AMT6: float = 0


class Service:
    def __init__(self):
        self.prog = C.parse_program(SOURCE)
        self.extractor, self.rules = R.extract(self.prog)
        self.engine = R.Engine(self.prog, self.rules)
        self.names = glossary.load(SOURCE)
        self.exe = compile_program(SOURCE)
        self.risk = model.RiskModel()
        self.thresholds = self.risk.meta["thresholds"]
        ref = json.loads((ROOT / "models" / "reference.json").read_text())
        self.median = np.asarray(ref["median"], dtype=np.float32)
        df = data.load()
        self.test = data.split(df)["test"].set_index("ID", drop=False)
        self.by_id = df.set_index("ID", drop=False)
        self._scenarios = None
        self.explanations = {}           # account id -> explanation, generated on this server
        self.warm_ids = set()

    # ---------------------------------------------------------------- decisions
    def row_for(self, acct: Account) -> dict:
        if acct.id is not None:
            if acct.id not in self.by_id.index:
                raise HTTPException(404, f"no account {acct.id}")
            row = self.by_id.loc[acct.id].to_dict()
            row["source"] = "uci"
            return row
        if acct.LIMIT_BAL is None:
            raise HTTPException(422, "give an account id or LIMIT_BAL and the payment history")
        row = acct.model_dump()
        row["ID"] = 999999
        row["source"] = "entered"
        return row

    def decide(self, acct: Account) -> dict:
        row = self.row_for(acct)
        t = {}
        record = data.to_record(row)
        t0 = time.perf_counter()
        rule, actions, st, n = self.engine.decide(record)
        t["rules_ms"] = (time.perf_counter() - t0) * 1000
        if rule is None:
            raise HTTPException(500, "no rule matched")
        rule_out = self.engine.output(rule, st)[0]
        t0 = time.perf_counter()
        cobol_out = run_batch(self.exe, [record])[0]
        t["cobol_ms"] = (time.perf_counter() - t0) * 1000
        x = data.feature_matrix(pd.DataFrame([row]))[0]
        t0 = time.perf_counter()
        p = float(self.risk.proba(x[None, :])[0])
        t["risk_ms"] = (time.perf_counter() - t0) * 1000
        factors = model.explain_factors(self.risk, x, self.median)
        b = model.band(p, self.thresholds)
        rd = rule.to_dict(self.prog, self.names)
        decision = actions["OUT-DECISION"]
        result = {
            "account": data.account_from_record(record),
            "record": record,
            "source": row["source"],
            "defaulted": int(row["DEFAULT"]) if "DEFAULT" in row and row.get("source") == "uci" else None,
            "policy": {"decision": decision, "reason_code": actions.get("OUT-REASON"),
                       "rule": rule.id, "lines": rd["lines"], "conditions": rd["conditions"],
                       "action_lines": rule.action_lines,
                       "derived": rd["derived"], "matching_rules": n,
                       "utilisation": st.text("WS-UTIL")},
            "cobol": {"output": cobol_out, "rules_output": rule_out, "agrees": cobol_out == rule_out},
            "risk": {"probability": p, "band": b, "backend": self.risk.backend,
                     "thresholds": self.thresholds, "factors": factors},
            "hybrid": hybrid.combine(decision, b),
            "timing_ms": {k: round(v, 3) for k, v in t.items()},
            "platform": platform.machine(),
        }
        return result

    # ---------------------------------------------------------------- demo scenarios
    def scenarios(self) -> dict:
        if self._scenarios is None:
            test = self.test
            records = [data.to_record(r) for _, r in test.iterrows()]
            policy = [line.split()[1] for line in run_batch(self.exe, records)]
            p = self.risk.proba(data.feature_matrix(test))
            groups = {"held_increase": [], "early_warning": [], "urgent_review": [],
                      "blocked": [], "approved_increase": [], "second_look": []}
            for acct_id, pol, pv in zip(test["ID"], policy, p):
                c = hybrid.combine(pol, model.band(pv, self.thresholds))
                key = {("INCREASE", "HOLD"): "held_increase", ("KEEP", "REVIEW"): "early_warning",
                       ("INCREASE", "INCREASE"): "approved_increase", ("BLOCK", "BLOCK"): "blocked"
                       }.get((pol, c["final"]))
                if pol == "REVIEW" and c.get("priority") == "URGENT":
                    key = "urgent_review"
                if pol in ("BLOCK", "DECREASE") and c["final"] == "REVIEW":
                    key = "second_look"
                if key:
                    groups[key].append(int(acct_id))
            self._scenarios = groups
        return self._scenarios

    def explain(self, acct: Account) -> dict:
        result = self.decide(acct)
        if acct.id is not None and acct.id in self.explanations:
            result["explanation"] = dict(self.explanations[acct.id], cached=True)
            return result
        result["explanation"] = explain.explain(result)
        if acct.id is not None and result["explanation"]["source"] == "granite":
            self.explanations[acct.id] = result["explanation"]
        return result

    def warm_up(self, per_group: int = 2):
        """Explain a few accounts of every scenario in the background, so the demo is instant."""
        if not explain.llm_available(timeout=3):
            return
        for ids in self.scenarios().values():
            for acct_id in ids[:per_group]:
                try:
                    if self.explain(Account(id=acct_id))["explanation"]["source"] == "granite":
                        self.warm_ids.add(acct_id)
                except Exception:
                    pass


app = FastAPI(title="LegacyLens", version="0.1.0")
svc: Service | None = None


@app.on_event("startup")
def _startup():
    global svc
    svc = Service()
    threading.Thread(target=svc.warm_up, daemon=True).start()


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/api/health")
def health():
    return {"ok": True, "platform": platform.machine(), "node": platform.node(),
            "risk_backend": svc.risk.backend, "llm": explain.llm_available()}


@app.get("/api/program")
def program():
    p = svc.prog
    return {"name": p.name, "entry": svc.extractor.entry,
            "decision_fields": sorted(svc.extractor.dec),
            "source": [{"no": no, "text": text} for no, text in p.lines],
            "rules": [r.to_dict(p, svc.names) for r in svc.rules], "glossary": svc.names,
            "rule_docs": _rule_docs(), "diagnostics": p.diagnostics}


def _rule_docs() -> dict:
    f = ROOT / "results" / "rule_docs.json"
    return json.loads(f.read_text())["rules"] if f.exists() else {}


@app.get("/api/results")
def results():
    out = {}
    for name in ("verification", "model", "hybrid"):
        f = ROOT / "results" / f"{name}.json"
        out[name] = json.loads(f.read_text()) if f.exists() else None
    return out


@app.get("/api/scenario/{kind}")
def scenario(kind: str):
    groups = svc.scenarios()
    if kind == "random":
        return {"id": int(random.choice(svc.test["ID"].tolist()))}
    if kind not in groups or not groups[kind]:
        raise HTTPException(404, f"no {kind} accounts")
    warm = [i for i in groups[kind] if i in svc.warm_ids]
    pool = warm if warm and random.random() < 0.7 else groups[kind]
    return {"id": random.choice(pool), "available": len(groups[kind])}


@app.post("/api/decide")
def decide(acct: Account):
    return svc.decide(acct)


@app.post("/api/explain")
def explain_decision(acct: Account):
    return svc.explain(acct)
