"""COBOL PICTURE clauses and how values are stored into them."""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal, getcontext

getcontext().prec = 50


@dataclass(frozen=True)
class Pic:
    """A parsed PICTURE: numeric display (9, S, V) or alphanumeric (X, A)."""
    text: str
    kind: str          # "num" or "alnum"
    digits: int = 0    # integer digits
    scale: int = 0     # decimal digits after V
    signed: bool = False
    length: int = 0    # bytes in display form

    @property
    def is_numeric(self) -> bool:
        return self.kind == "num"


def _expand(text: str) -> str:
    return re.sub(r"(.)\((\d+)\)", lambda m: m.group(1) * int(m.group(2)), text.upper())


def parse_pic(text: str) -> Pic:
    body = _expand(text)
    if set(body) <= set("XA"):
        return Pic(text, "alnum", length=len(body))
    if set(body) <= set("S9V"):
        signed = body.startswith("S")
        body = body.lstrip("S")
        whole, _, frac = body.partition("V")
        return Pic(text, "num", digits=len(whole), scale=len(frac), signed=signed,
                   length=len(whole) + len(frac))
    raise ValueError(f"unsupported PICTURE {text!r}")


def store(pic: Pic, value):
    """Return the value a COBOL receiving field holds after MOVE/COMPUTE.

    Numeric: fraction truncated to the picture's scale, high-order digits
    truncated, sign dropped for unsigned pictures. Alphanumeric: left
    justified, padded with spaces or truncated on the right.
    """
    if pic.is_numeric:
        if isinstance(value, str):
            digits = "".join(ch for ch in value if ch.isdigit()) or "0"
            value = Decimal(digits)
        d = Decimal(value)
        if not pic.signed:
            d = abs(d)
        q = Decimal(1).scaleb(-pic.scale)
        d = d.quantize(q, rounding=ROUND_DOWN)
        limit = Decimal(10) ** pic.digits
        sign = -1 if d < 0 else 1
        d = sign * (abs(d) % limit)
        return d
    if isinstance(value, Decimal):
        value = str(abs(value)).split(".")[0]
    s = str(value)
    return s[: pic.length].ljust(pic.length)


def display(pic: Pic, value) -> str:
    """The DISPLAY form of a stored value (zero padded for numeric)."""
    if pic.is_numeric:
        d = abs(Decimal(value))
        whole = int(d)
        frac = int((d - whole) * (10 ** pic.scale)) if pic.scale else 0
        text = str(whole).rjust(pic.digits, "0")[-pic.digits:] if pic.digits else ""
        if pic.scale:
            text += str(frac).rjust(pic.scale, "0")
        return text
    return str(value)
