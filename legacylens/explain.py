"""Plain-English explanations of a decision, written by IBM Granite running on
LinuxONE (llama.cpp server), with a guard and a fallback.

The decision, the rule and the risk score are computed first and put into a
factual draft note. Granite only rewrites that note in plain English. If the
rewrite contains a number that is not in the draft, or does not name the
decision and the rule, the draft itself is shown instead.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request

LLM_URL = os.environ.get("LEGACYLENS_LLM_URL", "http://127.0.0.1:8080")

NAMES = {
    "LIMIT_BAL": "credit limit", "PAY_0": "months late in September", "PAY_2": "months late in August",
    "PAY_3": "months late in July", "PAY_4": "months late in June", "PAY_5": "months late in May",
    "PAY_6": "months late in April", "BILL_AMT1": "September statement", "BILL_AMT2": "August statement",
    "BILL_AMT3": "July statement", "BILL_AMT4": "June statement", "BILL_AMT5": "May statement",
    "BILL_AMT6": "April statement", "PAY_AMT1": "September payment", "PAY_AMT2": "August payment",
    "PAY_AMT3": "July payment", "PAY_AMT4": "June payment", "PAY_AMT5": "May payment",
    "PAY_AMT6": "April payment", "UTIL_1": "utilisation of the limit",
    "PAID_RATIO_1": "share of the statement paid", "MONTHS_LATE_6": "late months out of the last six",
}

SYSTEM = (
    "You rewrite credit decision notes for a bank review officer. Start with the decision word exactly "
    "as given, in capitals, followed by a colon. Mention the rule id exactly as given (for example "
    "RULE-05). Keep every number exactly as written. Do not add facts, numbers or advice. "
    "Write at most two sentences."
)
EXAMPLE = [
    {"role": "user", "content": (
        "Draft: Decision: REVIEW. AI early warning: high risk of default next month. The legacy COBOL "
        "program decided KEEP under RULE-06 (lines 73 to 74) because none of the other rules applied. "
        "The AI model puts the risk of default next month at 41 percent (HIGH risk); the biggest factor "
        "is the months late in September of 2 against a typical 0.\n\nRewrite:")},
    {"role": "assistant", "content": (
        "REVIEW: the legacy program would keep this account as it is under RULE-06 (lines 73 to 74), "
        "but the AI puts its risk of default next month at 41 percent (HIGH). The main reason is that "
        "the customer is 2 months late in September, where 0 is typical.")},
]


def fmt(v) -> str:
    if isinstance(v, float) and not v.is_integer():
        return f"{v:.2f}"
    return f"{int(v):,}" if isinstance(v, (int, float)) else str(v)


def draft(result: dict) -> str:
    """A factual note built only from computed values. Granite rewrites it; if
    Granite is unavailable or its rewrite fails the guard, this is shown."""
    r = result
    pol = r["policy"]
    held = [c for c in pol["conditions"] if c["outcome"]]
    because = " and ".join(c["english"] for c in held) if held else "none of the other rules applied"
    lines = sorted({c["line"] for c in held} | set(pol.get("action_lines", []))) or pol["lines"]
    span = f"line {lines[0]}" if len(lines) == 1 else f"lines {lines[0]} to {lines[-1]}"
    text = (f"Decision: {r['hybrid']['final']}. {r['hybrid']['reason']}. "
            f"The legacy COBOL program decided {pol['decision']} under {pol['rule']} ({span}) "
            f"because {because}. "
            f"The AI model puts the risk of default next month at "
            f"{round(r['risk']['probability'] * 100)} percent ({r['risk']['band']} risk)")
    f = r["risk"]["factors"][0] if r["risk"]["factors"] else None
    if f:
        side = "higher" if f["value"] > f["median"] else "lower" if f["value"] < f["median"] else "the same as"
        than = "" if side == "the same as" else " than"
        effect = "raises" if f["effect"] > 0 else "lowers"
        text += (f"; the biggest factor is the {NAMES.get(f['feature'], f['feature'])} of "
                 f"{fmt(f['value'])}, {side}{than} the typical {fmt(f['median'])}, which {effect} the risk")
    return text + "."


NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _numbers(text: str) -> set:
    out = set()
    for n in NUM.findall(text):
        try:
            out.add(float(n.replace(",", "")))
        except ValueError:
            pass
    return out


DECISION_WORDS = re.compile(r"\b(increase|keep|review|decrease|block|hold|approv\w*|declin\w*|reject\w*|"
                            r"deny|denied)\b", re.I)
PERCENT = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(?:%|percent)", re.I)


def _stem(word: str) -> str:
    w = word.lower()
    for root in ("approv", "declin", "reject", "deni", "deny"):
        if w.startswith(root):
            return root
    return w


def guard(reply: str, fact_text: str, final: str, rule: str = ""):
    """Reject a reply that adds numbers, percentages or decision words that are
    not in the facts, or that does not name the decision and the rule."""
    problems = []
    unknown = sorted(n for n in _numbers(reply) if n not in _numbers(fact_text))
    if unknown:
        problems.append("numbers not in the facts: " + ", ".join(f"{n:g}" for n in unknown[:5]))
    pct_facts = {float(n.replace(",", "")) for n in PERCENT.findall(fact_text)}
    bad_pct = sorted({float(n.replace(",", "")) for n in PERCENT.findall(reply)} - pct_facts)
    if bad_pct:
        problems.append("percentages not in the facts: " + ", ".join(f"{n:g}" for n in bad_pct))
    fact_words = {_stem(w) for w in DECISION_WORDS.findall(fact_text)}
    extra = sorted({_stem(w) for w in DECISION_WORDS.findall(reply)} - fact_words)
    if extra:
        problems.append("decision words not in the facts: " + ", ".join(extra))
    if final.lower() not in reply.lower():
        problems.append(f"does not name the decision {final}")
    if rule and rule.lower() not in reply.lower():
        problems.append(f"does not name {rule}")
    return problems


def llm_available(timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(f"{LLM_URL}/health", timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def chat(system: str, user: str, max_tokens: int = 110, timeout: float = 120.0, examples=()):
    """One chat completion from the llama.cpp server; (None, None) if it is unreachable.
    The fixed system prompt and examples come first so the server can reuse their cache."""
    messages = [{"role": "system", "content": system}, *examples, {"role": "user", "content": user}]
    body = json.dumps({"messages": messages, "temperature": 0.1, "max_tokens": max_tokens,
                       "cache_prompt": True}).encode()
    req = urllib.request.Request(f"{LLM_URL}/v1/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            out = json.loads(resp.read())
    except Exception:
        return None, None
    return out["choices"][0]["message"]["content"].strip(), out.get("model", "granite")


def explain(result: dict, timeout: float = 90.0) -> dict:
    note = draft(result)
    t0 = time.perf_counter()
    reply, model = chat(SYSTEM, f"Draft: {note}\n\nRewrite:", max_tokens=90, timeout=timeout,
                        examples=EXAMPLE)
    ms = round((time.perf_counter() - t0) * 1000)
    if reply is None:
        return {"text": note, "source": "template", "note": "LLM unavailable", "ms": ms}
    problems = guard(reply, note, result["hybrid"]["final"], result["policy"]["rule"])
    if problems:
        return {"text": note, "source": "template", "rejected": reply,
                "note": "LLM rewrite rejected: " + "; ".join(problems), "ms": ms, "model": model}
    return {"text": reply, "source": "granite", "model": model, "ms": ms, "draft": note}
