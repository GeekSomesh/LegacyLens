"""COBOL PICTURE clauses, USAGE, and how values are stored as bytes.

Byte formats follow GnuCOBOL on an ASCII platform: zoned decimal (DISPLAY)
with the sign of a signed field embedded in the last byte (negative digits
0-9 stored as 'p'-'y'), packed decimal (COMP-3) with sign nibble C/D/F, and
big-endian binary (COMP, COMP-4, BINARY, COMP-5) sized 2/4/8 bytes by digits.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from decimal import ROUND_DOWN, Decimal, getcontext

getcontext().prec = 60


class NotNumeric(ValueError):
    """A numeric field holds bytes that are not a valid number (undefined in COBOL)."""


@dataclass(frozen=True)
class Pic:
    """A parsed PICTURE plus USAGE: numeric (9 S V P), alphanumeric (X A) or edited."""
    text: str
    kind: str              # "num", "alnum" or "edited"
    digits: int = 0        # integer digits
    scale: int = 0         # digits after V (or after the point in an edited picture)
    signed: bool = False
    length: int = 0        # characters in display form
    usage: str = "display"  # display, comp-3, binary
    sign_separate: bool = False
    sign_leading: bool = False

    @property
    def is_numeric(self) -> bool:
        return self.kind == "num"

    @property
    def size(self) -> int:
        """Bytes of storage."""
        if self.kind != "num":
            return self.length
        n = self.digits + self.scale
        if self.usage == "comp-3":
            return n // 2 + 1
        if self.usage == "binary":
            return 2 if n <= 4 else 4 if n <= 9 else 8
        return n + (1 if self.sign_separate else 0)


def _expand(text: str) -> str:
    return re.sub(r"(.)\((\d+)\)", lambda m: m.group(1) * int(m.group(2)), text.upper())


def parse_pic(text: str, usage: str = "display") -> Pic:
    body = _expand(text)
    if set(body) <= set("XA"):
        return Pic(text, "alnum", length=len(body))
    if set(body) <= set("S9VP"):
        signed = body.startswith("S")
        body = body.lstrip("S")
        whole, _, frac = body.partition("V")
        whole = whole.replace("P", "9")
        return Pic(text, "num", digits=len(whole), scale=len(frac), signed=signed,
                   length=len(whole) + len(frac), usage=usage)
    if set(body) - set("9ZXB0/,.+-*$CRDBSV"):
        raise ValueError(f"unsupported PICTURE {text!r}")
    width = len(body.replace("V", "").replace("S", ""))
    left, dot, right = body.partition(".")
    digit_chars = set("9Z*")
    scale = sum(ch in digit_chars for ch in right) if dot else 0
    digits = sum(ch in digit_chars or ch in "+-$" for ch in left)
    return Pic(text, "edited", digits=digits, scale=scale, length=width)


def with_usage(pic: Pic, usage: str) -> Pic:
    return replace(pic, usage=usage) if pic.kind == "num" else pic


def with_sign(pic: Pic, leading: bool, separate: bool) -> Pic:
    return replace(pic, sign_leading=leading, sign_separate=separate)


# ---------------------------------------------------------------- values


def store(pic: Pic, value):
    """The value a receiving field holds after MOVE/COMPUTE: numeric values lose
    fraction digits beyond the scale and high-order digits beyond the picture,
    and unsigned fields drop the sign; alphanumeric values are left justified."""
    if pic.is_numeric or pic.kind == "edited":
        if isinstance(value, str):
            digits = "".join(ch for ch in value if ch.isdigit()) or "0"
            value = Decimal(digits)
        d = Decimal(value)
        if not pic.signed and pic.kind == "num":
            d = abs(d)
        d = d.quantize(Decimal(1).scaleb(-pic.scale), rounding=ROUND_DOWN)
        limit = Decimal(10) ** pic.digits
        sign = -1 if d < 0 else 1
        return sign * (abs(d) % limit)
    if isinstance(value, Decimal):
        value = str(abs(value)).split(".")[0]
    s = str(value)
    return s[: pic.length].ljust(pic.length)


def _scaled(pic: Pic, value: Decimal) -> int:
    return int((abs(Decimal(value)) * (Decimal(10) ** pic.scale)).to_integral_value(rounding=ROUND_DOWN))


def encode(pic: Pic, value) -> bytes:
    """Bytes for a value already stored with store()."""
    if pic.kind == "alnum":
        return str(value).encode("latin-1", "replace")[: pic.length].ljust(pic.length, b" ")
    if pic.kind == "edited":
        return format_edited(pic, Decimal(value)).encode("latin-1")
    d = Decimal(value)
    n = pic.digits + pic.scale
    mag = _scaled(pic, d) % (10 ** n if n else 1)
    neg = d < 0 and pic.signed
    if pic.usage == "comp-3":
        digits = str(mag).rjust(n, "0")
        if len(digits) % 2 == 0:
            digits = "0" + digits
        sign = 0xD if neg else (0xC if pic.signed else 0xF)
        nibbles = [int(c) for c in digits] + [sign]
        return bytes((nibbles[i] << 4) | nibbles[i + 1] for i in range(0, len(nibbles), 2))
    if pic.usage == "binary":
        v = -mag if neg else mag
        return v.to_bytes(pic.size, "big", signed=pic.signed)
    text = str(mag).rjust(n, "0")
    if pic.signed and pic.sign_separate:
        mark = "-" if neg else "+"
        return (mark + text if pic.sign_leading else text + mark).encode()
    if neg:
        if pic.sign_leading:
            text = chr(ord(text[0]) + 0x40) + text[1:]
        else:
            text = text[:-1] + chr(ord(text[-1]) + 0x40)
    return text.encode("latin-1")


_NEG_OVERPUNCH = {**{chr(0x70 + i): i for i in range(10)}, "}": 0,
                  **{chr(ord("J") + i): i + 1 for i in range(9)}}
_POS_OVERPUNCH = {"{": 0, **{chr(ord("A") + i): i + 1 for i in range(9)}}


def decode(pic: Pic, raw: bytes):
    """The value held in raw bytes; NotNumeric for invalid numeric data."""
    if pic.kind != "num":
        return raw.decode("latin-1")
    n = pic.digits + pic.scale
    if pic.usage == "comp-3":
        nib = []
        for b in raw:
            nib += [b >> 4, b & 0xF]
        sign = nib.pop()
        if any(x > 9 for x in nib) or sign < 0xA:
            raise NotNumeric(raw.hex())
        mag = int("".join(map(str, nib)) or "0")
        neg = sign == 0xD
    elif pic.usage == "binary":
        mag = int.from_bytes(raw, "big", signed=pic.signed)
        neg = mag < 0
        mag = abs(mag)
    else:
        text = raw.decode("latin-1")
        neg = False
        if pic.signed and pic.sign_separate:
            mark, text = (text[0], text[1:]) if pic.sign_leading else (text[-1], text[:-1])
            if mark not in "+-":
                raise NotNumeric(raw)
            neg = mark == "-"
        elif pic.signed:
            pos = 0 if pic.sign_leading else len(text) - 1
            ch = text[pos]
            if ch in _NEG_OVERPUNCH:
                neg = True
                text = text[:pos] + str(_NEG_OVERPUNCH[ch]) + text[pos + 1:]
            elif ch in _POS_OVERPUNCH:
                text = text[:pos] + str(_POS_OVERPUNCH[ch]) + text[pos + 1:]
        if len(text) != n or not text.isdigit():
            raise NotNumeric(raw)
        mag = int(text)
    v = Decimal(mag).scaleb(-pic.scale)
    return -v if neg else v


def is_numeric_data(pic: Pic | None, raw: bytes) -> bool:
    """The NUMERIC class test."""
    if pic is None or pic.kind != "num":
        return len(raw) > 0 and all(0x30 <= b <= 0x39 for b in raw)
    try:
        decode(pic, raw)
        return True
    except NotNumeric:
        return False


def display(pic: Pic, value) -> str:
    """What DISPLAY prints for a stored value."""
    if pic.kind == "alnum":
        return str(value)
    if pic.kind == "edited":
        return format_edited(pic, Decimal(value))
    d = Decimal(value)
    text = str(_scaled(pic, d)).rjust(pic.digits + pic.scale, "0")
    if pic.signed:
        return ("-" if d < 0 else "+") + text
    return text


def format_edited(pic: Pic, value: Decimal) -> str:
    """Numeric-edited output for the common symbols: 9 Z * , . B 0 / + - CR DB $."""
    body = _expand(pic.text).replace("V", "")
    neg = value < 0
    left, dot, right = body.partition(".")
    nleft = sum(ch in "9Z*" for ch in left) + max(0, sum(ch in "+-$" for ch in left) - 1)
    nright = sum(ch in "9Z*" for ch in right)
    mag = abs(value).quantize(Decimal(1).scaleb(-nright), rounding=ROUND_DOWN)
    whole = str(int(mag) % (10 ** nleft) if nleft else 0).rjust(nleft, "0")
    frac = str(int((mag - int(mag)) * (10 ** nright))).rjust(nright, "0") if nright else ""
    out, digits, k, seen = [], whole + frac, 0, False
    fill = "*" if "*" in body else " "
    float_char = next((c for c in "+-$" if body.count(c) > 1), None)
    float_done = False
    i = 0
    while i < len(body):
        ch = body[i]
        if body[i:i + 2] in ("CR", "DB"):
            out.append(body[i:i + 2] if neg else "  ")
            i += 2
            continue
        if ch == "." :
            out.append(".")
            seen = True
        elif ch == float_char:
            if not float_done and body.index(ch) == i:
                out.append(" ")
                float_done = True
            else:
                d = digits[k]
                k += 1
                if d != "0" or seen:
                    if not seen:
                        mark = "$" if ch == "$" else ("-" if neg else ("+" if ch == "+" else " "))
                        for j in range(len(out) - 1, -1, -1):
                            if out[j] == " ":
                                out[j] = mark
                                break
                    seen = True
                    out.append(d)
                else:
                    out.append(" ")
        elif ch in "9Z*":
            d = digits[k]
            k += 1
            if ch == "9" or d != "0" or seen or i > len(left):
                seen = True
                out.append(d)
            else:
                out.append(fill if ch == "*" else " ")
        elif ch == ",":
            out.append("," if seen else fill)
        elif ch in "B":
            out.append(" ")
        elif ch in "0/":
            out.append(ch)
        elif ch == "+":
            out.append("-" if neg else "+")
        elif ch == "-":
            out.append("-" if neg else " ")
        elif ch == "$":
            out.append("$")
        i += 1
    return "".join(out)
