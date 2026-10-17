"""Business names for COBOL data names, and conditions rendered in plain English.

The glossary lives next to the program (cobol/<PROGRAM>.glossary.json). Names
without an entry are shown as they are.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import cobol as C
from .rules import expr_text, negate

OPS = {"=": "is", "<>": "is not", "<": "is less than", ">": "is more than",
       "<=": "is at most", ">=": "is at least"}


def load(source) -> dict:
    p = Path(source).with_suffix(".glossary.json")
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def name_of(entry, default: str) -> str:
    if isinstance(entry, dict):
        return entry.get("name", default)
    return entry or default


def unit_of(e, names: dict) -> str:
    entry = names.get(e.name) if isinstance(e, C.Ref) else None
    return entry.get("unit", "") if isinstance(entry, dict) else ""


def term(e, names: dict) -> str:
    if isinstance(e, C.Ref):
        return name_of(names.get(e.name), e.name)
    if isinstance(e, C.Lit):
        v = e.value
        if isinstance(v, C.Fig):
            return {"SPACE": "blank", "ZERO": "zero", "LOW": "low-values", "HIGH": "high-values"}.get(v.kind, e.text)
        if isinstance(v, str):
            return f"'{v.strip()}'"
        return f"{int(v):,}" if v == int(v) else e.text
    return expr_text(e)


def english(c, names: dict, top: bool = True) -> str:
    if isinstance(c, C.Rel):
        unit = unit_of(c.left, names) if isinstance(c.right, C.Lit) else ""
        return f"{term(c.left, names)} {OPS[c.op]} {term(c.right, names)}" + (f" {unit}" if unit else "")
    if isinstance(c, C.CondRef):
        return name_of(names.get(c.name), c.name)
    if isinstance(c, C.ClassCond):
        return f"{term(c.operand, names)} is {c.kind.lower().replace('-', ' ')}"
    if isinstance(c, C.AtEnd):
        return f"the end of {c.file.lower().replace('-', ' ')} is reached"
    if isinstance(c, C.Not):
        inner = c.cond
        if isinstance(inner, C.CondRef):
            return f"not {name_of(names.get(inner.name), inner.name)}"
        if isinstance(inner, C.ClassCond):
            return f"{term(inner.operand, names)} is not {inner.kind.lower().replace('-', ' ')}"
        if isinstance(inner, C.AtEnd):
            return f"the end of {inner.file.lower().replace('-', ' ')} is not reached"
        return english(negate(inner), names, top)
    if isinstance(c, (C.And, C.Or)):
        word = "and" if isinstance(c, C.And) else "or"
        t = f"{english(c.left, names, False)} {word} {english(c.right, names, False)}"
        return t if top or isinstance(c, C.And) else f"({t})"
    return "?"


def rule_english(rule, names: dict) -> list:
    """The conditions of a rule as they must hold, in plain English."""
    return [english(c if holds else negate(c), names) for c, holds, _ in rule.conditions]
