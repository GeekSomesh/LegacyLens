"""Human review of extracted rules, with a tamper-evident audit trail.

Every action (approve, flag, comment) is appended to an SQLite table. Each entry
stores the SHA-256 of the previous entry plus its own content, so changing or
deleting any past entry breaks the chain, and verify() says where.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

DB = Path(os.environ.get("LEGACYLENS_STATE", Path(__file__).resolve().parent.parent / "build")) / "review.db"
GENESIS = "0" * 64
ACTIONS = {"approve", "flag", "comment"}
_lock = threading.Lock()


def _conn():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS audit (
        seq INTEGER PRIMARY KEY, at TEXT, reviewer TEXT, program TEXT, rule TEXT,
        action TEXT, comment TEXT, rule_hash TEXT, prev TEXT, hash TEXT)""")
    return c


def _digest(prev: str, entry: dict) -> str:
    body = json.dumps({k: entry[k] for k in ("at", "reviewer", "program", "rule", "action", "comment", "rule_hash")},
                      sort_keys=True)
    return hashlib.sha256((prev + body).encode()).hexdigest()


def rule_fingerprint(rule: dict) -> str:
    """Hash of what was reviewed: conditions and actions. A later change to the rule
    makes earlier approvals visibly stale."""
    core = {"conditions": [c["text"] for c in rule.get("conditions", [])], "actions": rule.get("actions", {})}
    return hashlib.sha256(json.dumps(core, sort_keys=True).encode()).hexdigest()[:16]


def record(program: str, rule: str, action: str, reviewer: str, comment: str, rule_hash: str) -> dict:
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {sorted(ACTIONS)}")
    reviewer = (reviewer or "").strip()[:60]
    if not reviewer:
        raise ValueError("a reviewer name is required")
    with _lock, _conn() as c:
        row = c.execute("SELECT hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
        prev = row[0] if row else GENESIS
        entry = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "reviewer": reviewer,
                 "program": program, "rule": rule, "action": action, "comment": (comment or "")[:1000],
                 "rule_hash": rule_hash}
        entry["prev"], entry["hash"] = prev, _digest(prev, entry)
        c.execute("INSERT INTO audit (at, reviewer, program, rule, action, comment, rule_hash, prev, hash) "
                  "VALUES (:at, :reviewer, :program, :rule, :action, :comment, :rule_hash, :prev, :hash)", entry)
        return entry


def history(program: str | None = None) -> list:
    with _conn() as c:
        c.row_factory = sqlite3.Row
        q = "SELECT * FROM audit" + (" WHERE program = ?" if program else "") + " ORDER BY seq"
        return [dict(r) for r in c.execute(q, (program,) if program else ())]


def status(program: str, rules: list) -> dict:
    """Latest decision per rule, and whether it still matches the current rule."""
    out = {}
    current = {r["id"]: rule_fingerprint(r) for r in rules}
    for e in history(program):
        if e["action"] in ("approve", "flag"):
            out[e["rule"]] = {"state": "approved" if e["action"] == "approve" else "flagged",
                              "reviewer": e["reviewer"], "at": e["at"], "comment": e["comment"],
                              "stale": e["rule_hash"] != current.get(e["rule"])}
    return out


def verify() -> dict:
    prev = GENESIS
    rows = history()
    for e in rows:
        if e["prev"] != prev or _digest(prev, e) != e["hash"]:
            return {"ok": False, "entries": len(rows), "broken_at": e["seq"]}
        prev = e["hash"]
    return {"ok": True, "entries": len(rows), "head": prev}
