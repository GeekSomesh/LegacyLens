"""Combine the legacy policy (the COBOL rules) with the AI risk score.

The AI never approves anything on its own. It can hold a limit increase,
escalate an account to a human, set the priority of the human review queue,
or send a restrictive decision for a second look. Every final decision names
the COBOL rule and the risk band that produced it.

The combination table was chosen on the validation accounts (default rate
by policy decision and risk band) and is reported on the test accounts.
"""
from __future__ import annotations

ACTIONS = {
    ("INCREASE", "MEDIUM"): ("HOLD", "Limit increase held: AI risk is not low"),
    ("INCREASE", "HIGH"): ("HOLD", "Limit increase held: AI risk is high"),
    ("KEEP", "HIGH"): ("REVIEW", "AI early warning: high risk of default next month"),
    ("BLOCK", "LOW"): ("REVIEW", "Policy blocks, but AI risk is low: second look"),
    ("DECREASE", "LOW"): ("REVIEW", "Policy decreases, but AI risk is low: second look"),
}
PRIORITY = {"HIGH": "URGENT", "MEDIUM": "NORMAL", "LOW": "LOW"}


def combine(policy: str, risk_band: str) -> dict:
    final, why = ACTIONS.get((policy, risk_band), (policy, "AI agrees with the policy"))
    out = {"policy": policy, "risk_band": risk_band, "final": final, "reason": why,
           "changed": final != policy}
    if final in ("REVIEW", "HOLD"):
        out["priority"] = PRIORITY[risk_band]
    return out


def _cell(rows):
    n = len(rows)
    d = sum(rows)
    return {"accounts": n, "defaulted": d, "default_rate": round(d / n, 4) if n else None}


def impact(policies: list, bands: list, defaulted: list) -> dict:
    """What the policy decided, what the hybrid changed, and how many of the
    affected accounts defaulted the next month."""
    rows = [(p, b, int(d)) for p, b, d in zip(policies, bands, defaulted)]
    out = {"accounts": len(rows), "default_rate": round(sum(d for *_, d in rows) / len(rows), 4)}
    out["policy"] = {p: _cell([d for q, _, d in rows if q == p]) for p in sorted({r[0] for r in rows})}
    out["policy_by_band"] = {f"{p}/{b}": _cell([d for q, c, d in rows if q == p and c == b])
                             for p, b in sorted({(r[0], r[1]) for r in rows})}
    changes = {}
    for p, b, d in rows:
        c = combine(p, b)
        if c["changed"]:
            changes.setdefault(f"{p} -> {c['final']}", []).append(d)
    out["changes"] = {k: _cell(v) for k, v in changes.items()}
    inc = [d for p, _, d in rows if p == "INCREASE"]
    held = [d for p, b, d in rows if p == "INCREASE" and b != "LOW"]
    granted = [d for p, b, d in rows if p == "INCREASE" and b == "LOW"]
    out["limit_increases"] = {
        "policy_granted": _cell(inc),
        "hybrid_granted": _cell(granted),
        "hybrid_held": _cell(held),
        "defaulters_avoided_share": round(sum(held) / sum(inc), 4) if sum(inc) else None,
    }
    review = [(b, d) for p, b, d in rows if p == "REVIEW"]
    out["review_queue"] = {pr: _cell([d for b, d in review if PRIORITY[b] == pr])
                           for pr in ("URGENT", "NORMAL", "LOW")}
    out["early_warnings"] = _cell([d for p, b, d in rows if p == "KEEP" and b == "HIGH"])
    return out
