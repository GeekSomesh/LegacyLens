"""Reader and parser for a documented subset of fixed-format COBOL.

Source: fixed format, COPY ... REPLACING ==a== BY ==b== (copybooks from
search directories, every line traced to its file and line).
Data: FILE, WORKING-STORAGE and LINKAGE sections; levels 01-49, 77, 88;
PIC 9 S V P X A and numeric-edited; USAGE DISPLAY, COMP-3/PACKED-DECIMAL,
COMP/COMP-4/COMP-5/BINARY; VALUE (literals and figurative constants);
OCCURS n; REDEFINES; SIGN LEADING/TRAILING [SEPARATE]. Every field gets its
byte offset and length, so overlays behave as on the machine.
Procedure: paragraphs and sections; ACCEPT [FROM DATE/TIME], DISPLAY, MOVE,
COMPUTE, ADD/SUBTRACT, IF/ELSE, EVALUATE (TRUE or one subject, stacked WHENs,
THRU, OTHER), PERFORM (paragraph, THRU, n TIMES, UNTIL, VARYING, TEST
BEFORE/AFTER, inline), GO TO, CALL ... USING, OPEN, CLOSE, READ ... AT END,
WRITE ... FROM, INITIALIZE, SET condition TO TRUE, STOP RUN, GOBACK, EXIT,
CONTINUE; conditions with relations, 88 names, NUMERIC/ALPHABETIC tests,
POSITIVE/NEGATIVE/ZERO, abbreviated combined relations; subscripts and
reference modification.
Anything else is kept as an Unsupported node with a diagnostic, never guessed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from .numeric import Pic, parse_pic, with_sign, with_usage

# ---------------------------------------------------------------- AST


@dataclass
class CondName:
    name: str
    field: str
    values: list  # list of (lo, hi) pairs; lo == hi for a single value
    line: int


@dataclass
class Field:
    name: str
    key: str               # unique key (FILLERs and duplicates get #n)
    level: int
    line: int
    pic: Pic | None = None
    value: object = None
    parent: str | None = None
    children: list = field(default_factory=list)
    offset: int = 0        # byte offset within its 01-level record
    length: int = 0        # bytes of one occurrence
    occurs: int | None = None
    redefines: str | None = None
    section: str = "working"
    record: str = ""       # key of the 01-level record
    dims: list = field(default_factory=list)   # [(stride, count)] for subscripts
    file: str | None = None

    @property
    def is_group(self) -> bool:
        return self.pic is None


class Fig:
    """A figurative constant: SPACE, ZERO, LOW-VALUE, HIGH-VALUE, QUOTE, or ALL 'x'."""

    def __init__(self, kind: str, text: str = ""):
        self.kind = kind
        self.text = text

    def fill(self, n: int) -> str:
        unit = {"SPACE": " ", "ZERO": "0", "LOW": "\x00", "HIGH": "\xff", "QUOTE": '"'}.get(self.kind, self.text)
        return (unit * (n // max(len(unit), 1) + 1))[:n]

    def __eq__(self, other):
        return isinstance(other, Fig) and (self.kind, self.text) == (other.kind, other.text)

    def __hash__(self):
        return hash((self.kind, self.text))

    def __repr__(self):
        return f"Fig({self.kind})"


@dataclass
class Lit:
    value: object          # Decimal, str or Fig
    text: str


@dataclass
class Ref:
    name: str
    subs: tuple = ()       # subscript expressions
    refmod: tuple | None = None   # (start expr, length expr or None)


@dataclass
class BinOp:
    op: str
    left: object
    right: object


@dataclass
class Neg:
    expr: object


@dataclass
class Rel:
    op: str                # = <> < > <= >=
    left: object
    right: object


@dataclass
class CondRef:
    name: str


@dataclass
class ClassCond:
    operand: object
    kind: str              # NUMERIC, ALPHABETIC, ALPHABETIC-LOWER, ALPHABETIC-UPPER


@dataclass
class AtEnd:
    file: str              # true when the last READ of this file hit end of file


@dataclass
class Not:
    cond: object


@dataclass
class And:
    left: object
    right: object


@dataclass
class Or:
    left: object
    right: object


@dataclass
class Accept:
    target: Ref
    line: int
    source: str | None = None


@dataclass
class Display:
    items: list
    line: int


@dataclass
class Move:
    source: object
    targets: list
    line: int


@dataclass
class Compute:
    targets: list
    expr: object
    line: int
    rounded: tuple = ()    # names of targets with ROUNDED


@dataclass
class If:
    cond: object
    then: list
    else_: list
    line: int
    end_line: int = 0


@dataclass
class When:
    cond: object
    stmts: list
    line: int


@dataclass
class Evaluate:
    whens: list
    other: list | None
    line: int
    end_line: int = 0


@dataclass
class Perform:
    target: str | None     # None for an inline PERFORM
    thru: str | None
    times: object | None
    until: object | None
    body: list
    line: int
    varying: tuple | None = None   # (Ref, from expr, by expr)
    test_after: bool = False


@dataclass
class GoTo:
    target: str
    line: int


@dataclass
class Call:
    target: object         # Lit or Ref
    args: list
    line: int


@dataclass
class Open:
    files: list            # [(mode, file)]
    line: int


@dataclass
class Close:
    files: list
    line: int


@dataclass
class Read:
    file: str
    into: Ref | None
    at_end: list
    not_at_end: list
    line: int


@dataclass
class Write:
    record: Ref
    source: object | None
    line: int


@dataclass
class Initialize:
    targets: list
    line: int


@dataclass
class SetTrue:
    names: list
    line: int


@dataclass
class Stop:
    line: int


@dataclass
class Continue:
    line: int


@dataclass
class Unsupported:
    text: str
    line: int


@dataclass
class Paragraph:
    name: str
    stmts: list
    line: int
    end_line: int = 0


@dataclass
class Program:
    name: str
    path: str
    lines: list            # [(lineno, text)] of the main source file
    fields: dict           # key -> Field
    conds: dict            # condition-name -> CondName
    paragraphs: list
    diagnostics: list
    using: list = field(default_factory=list)       # PROCEDURE DIVISION USING names
    files: dict = field(default_factory=dict)       # file name -> {"assign", "status", "records"}
    locs: dict = field(default_factory=dict)        # line id -> (file, line) for copybook lines

    def paragraph(self, name: str) -> Paragraph:
        for p in self.paragraphs:
            if p.name == name:
                return p
        raise KeyError(name)

    def para_index(self, name: str) -> int:
        for i, p in enumerate(self.paragraphs):
            if p.name == name:
                return i
        raise KeyError(name)

    def where(self, line_id: int) -> str:
        if line_id in self.locs:
            f, n = self.locs[line_id]
            return f"{f}:{n}"
        return f"{Path(self.path).name}:{line_id}"

    def records(self, section: str | None = None) -> list:
        return [f for f in self.fields.values() if f.parent is None and
                (section is None or f.section == section)]


# ---------------------------------------------------------------- source

COPY_LINE_BASE = 100000


def read_fixed(text: str) -> list:
    """Fixed format: columns 8-72 are code, '*' or '/' in column 7 is a comment.
    Returns [(lineno, code)] for code lines."""
    out = []
    for no, raw in enumerate(text.splitlines(), 1):
        if len(raw) < 7:
            continue
        ind = raw[6]
        if ind in "*/":
            continue
        code = raw[7:72].rstrip()
        if ind == "-":
            if out:
                prev_no, prev = out[-1]
                piece = code.lstrip()
                if piece[:1] in "'\"" and prev.rstrip()[-1:] not in "'\"":
                    piece = piece[1:]
                out[-1] = (prev_no, prev.rstrip() + piece if piece[:1] not in "'\"" else prev + piece)
            continue
        if code.strip():
            out.append((no, code))
    return out


def find_copybook(name: str, dirs) -> Path | None:
    for d in dirs:
        d = Path(d)
        for cand in (name, name.upper(), name.lower()):
            for ext in ("", ".cpy", ".CPY", ".cbl", ".CBL", ".cob", ".copy"):
                p = d / f"{cand}{ext}"
                if p.is_file():
                    return p
    return None


COPY_RE = re.compile(r"^\s*COPY\s+(['\"]?)([A-Za-z0-9_-]+)\1(?:\s+(?:OF|IN)\s+[A-Za-z0-9-]+)?(.*)$",
                     re.I | re.S)


def expand_copies(code: list, dirs, locs: dict, diagnostics: list, counter: list, depth: int = 0) -> list:
    """Replace COPY statements by the copybook's code lines (with REPLACING)."""
    out, i = [], 0
    while i < len(code):
        no, text = code[i]
        if not re.match(r"^\s*COPY\s", text, re.I):
            out.append((no, text))
            i += 1
            continue
        stmt, j = text, i
        while not re.search(r"\.\s*$", stmt) and j + 1 < len(code):
            j += 1
            stmt += " " + code[j][1]
        i = j + 1
        m = COPY_RE.match(stmt.strip().rstrip("."))
        if not m:
            diagnostics.append(f"line {no}: cannot read COPY statement")
            continue
        name, rest = m.group(2), m.group(3)
        pairs = re.findall(r"==(.*?)==\s+BY\s+==(.*?)==", rest, re.I | re.S)
        pairs += [(a, b) for a, b in re.findall(r"(?<!=)\b([A-Za-z0-9:-]+)\s+BY\s+([A-Za-z0-9:-]+)\b(?!=)", rest, re.I)
                  if "==" not in rest]
        path = find_copybook(name, dirs)
        if path is None:
            diagnostics.append(f"line {no}: copybook {name} not found")
            continue
        sub = read_fixed(path.read_text(encoding="utf-8", errors="replace"))
        mapped = []
        for cno, ctext in sub:
            for a, b in pairs:
                ctext = ctext.replace(a.strip(), b.strip())
            counter[0] += 1
            lid = COPY_LINE_BASE + counter[0]
            locs[lid] = (path.name, cno)
            mapped.append((lid, ctext))
        if depth < 8:
            mapped = expand_copies(mapped, dirs, locs, diagnostics, counter, depth + 1)
        out.extend(mapped)
    return out


# ---------------------------------------------------------------- tokens

TOKEN = re.compile(r"""
 (?P<str>'[^']*'|"[^"]*")
|(?P<num>[+-]?\d*\.\d+(?![\w-])|[+-]?\d+(?![\w-]))
|(?P<word>[A-Za-z0-9][A-Za-z0-9_-]*[A-Za-z0-9]|[A-Za-z0-9])
|(?P<op>>=|<=|<>|\*\*|[=<>()*/+:-])
|(?P<period>\.)
|(?P<skip>[,;\s]+)
""", re.X)


@dataclass
class Tok:
    kind: str
    text: str
    line: int

    @property
    def up(self) -> str:
        return self.text.upper()


def tokenize(code_lines: list) -> list:
    toks = []
    for no, code in code_lines:
        pos = 0
        while pos < len(code):
            m = TOKEN.match(code, pos)
            if not m:
                raise ValueError(f"line {no}: cannot read {code[pos:pos + 20]!r}")
            pos = m.end()
            kind = m.lastgroup
            if kind != "skip":
                toks.append(Tok(kind, m.group(), no))
    return toks


VERBS = {
    "ACCEPT", "DISPLAY", "MOVE", "COMPUTE", "ADD", "SUBTRACT", "MULTIPLY", "DIVIDE",
    "IF", "EVALUATE", "PERFORM", "STOP", "GOBACK", "EXIT", "CONTINUE", "INITIALIZE",
    "SET", "GO", "CALL", "READ", "WRITE", "OPEN", "CLOSE", "STRING", "UNSTRING",
    "INSPECT", "SEARCH", "REWRITE", "DELETE", "START", "RETURN", "SORT", "MERGE",
    "RELEASE", "CANCEL", "ALTER", "NEXT", "EXEC",
}
SCOPE_WORDS = {"ELSE", "END-IF", "WHEN", "END-EVALUATE", "END-PERFORM", "END-COMPUTE",
               "END-ADD", "END-SUBTRACT", "END-READ", "END-WRITE", "END-CALL", "END-STRING",
               "END-UNSTRING", "END-SEARCH", "END-MULTIPLY", "END-DIVIDE", "END-EXEC"}
REL_WORDS = {"=", "<", ">", "<=", ">=", "<>", "EQUAL", "GREATER", "LESS", "EQUALS"}
CLASS_WORDS = {"NUMERIC", "ALPHABETIC", "ALPHABETIC-LOWER", "ALPHABETIC-UPPER"}
FIGURATIVE = {
    "ZERO": Fig("ZERO"), "ZEROS": Fig("ZERO"), "ZEROES": Fig("ZERO"),
    "SPACE": Fig("SPACE"), "SPACES": Fig("SPACE"),
    "LOW-VALUE": Fig("LOW"), "LOW-VALUES": Fig("LOW"),
    "HIGH-VALUE": Fig("HIGH"), "HIGH-VALUES": Fig("HIGH"),
    "QUOTE": Fig("QUOTE"), "QUOTES": Fig("QUOTE"),
}
SPECIAL_REGISTERS = {"RETURN-CODE": "S9(4)"}


# ---------------------------------------------------------------- data division


def _split_entries(code_lines: list) -> list:
    """Split DATA DIVISION text into (line, entry) at period terminators."""
    entries, buf, start = [], [], None
    for no, code in code_lines:
        quote = None
        for i, ch in enumerate(code):
            if start is None and not ch.isspace():
                start = no
            if quote:
                if ch == quote:
                    quote = None
                buf.append(ch)
                continue
            if ch in "'\"":
                quote = ch
                buf.append(ch)
                continue
            nxt = code[i + 1] if i + 1 < len(code) else " "
            if ch == "." and nxt.isspace():
                entries.append((start, "".join(buf).strip()))
                buf, start = [], None
                continue
            buf.append(ch)
        buf.append(" ")
    if "".join(buf).strip():
        entries.append((start, "".join(buf).strip()))
    return entries


ENTRY_TOKS = re.compile(r"'[^']*'|\"[^\"]*\"|[^\s]+")
CLAUSE_WORDS = {"PIC", "PICTURE", "VALUE", "VALUES", "OCCURS", "REDEFINES", "USAGE", "COMP", "COMP-1",
                "COMP-2", "COMP-3", "COMP-4", "COMP-5", "COMPUTATIONAL", "COMPUTATIONAL-3",
                "COMPUTATIONAL-4", "COMPUTATIONAL-5", "BINARY", "PACKED-DECIMAL", "DISPLAY", "INDEX",
                "SIGN", "JUST", "JUSTIFIED", "BLANK", "SYNC", "SYNCHRONIZED", "EXTERNAL", "GLOBAL",
                "LEADING", "TRAILING", "IS"}
USAGES = {"COMP": "binary", "COMP-4": "binary", "COMP-5": "binary", "BINARY": "binary",
          "COMPUTATIONAL": "binary", "COMPUTATIONAL-4": "binary", "COMPUTATIONAL-5": "binary",
          "COMP-3": "comp-3", "COMPUTATIONAL-3": "comp-3", "PACKED-DECIMAL": "comp-3",
          "DISPLAY": "display"}


def _literal(tok: str, nxt: str | None = None):
    up = tok.upper()
    if up in FIGURATIVE:
        return FIGURATIVE[up]
    if up == "ALL" and nxt:
        return Fig("ALL", nxt[1:-1] if nxt[:1] in "'\"" else nxt)
    if tok[:1] in "'\"":
        return tok[1:-1]
    return Decimal(tok)


def _values(toks: list) -> list:
    values, i = [], 0
    while i < len(toks):
        t = toks[i]
        if t.upper() in {"IS", "ARE"}:
            i += 1
            continue
        if t.upper() == "ALL" and i + 1 < len(toks):
            lo = _literal(t, toks[i + 1])
            i += 2
        else:
            lo = _literal(t)
            i += 1
        if i + 1 < len(toks) and toks[i].upper() in {"THRU", "THROUGH"}:
            values.append((lo, _literal(toks[i + 1])))
            i += 2
        else:
            values.append((lo, lo))
    return values


def parse_environment(code_lines: list) -> dict:
    """SELECT file ASSIGN TO name [FILE STATUS IS field]."""
    text = " ".join(c for _, c in code_lines)
    files = {}
    for sel in re.finditer(r"\bSELECT\s+(?:OPTIONAL\s+)?([A-Za-z0-9-]+)(.*?)(?=\bSELECT\b|$)", text, re.I | re.S):
        name, rest = sel.group(1).upper(), sel.group(2)
        a = re.search(r"\bASSIGN\s+(?:TO\s+)?(['\"]?)([A-Za-z0-9._/-]+)\1", rest, re.I)
        s = re.search(r"\bSTATUS\s+(?:IS\s+)?([A-Za-z0-9-]+)", rest, re.I)
        o = re.search(r"\bORGANIZATION\s+(?:IS\s+)?(LINE\s+SEQUENTIAL|SEQUENTIAL|INDEXED|RELATIVE)", rest, re.I)
        files[name] = {"assign": a.group(2) if a else name, "status": s.group(1).upper() if s else None,
                       "organization": (o.group(1).upper() if o else "SEQUENTIAL"), "records": []}
    return files


def parse_data(code_lines: list, diagnostics: list, files: dict):
    fields, conds, stack, last = {}, {}, [], None
    section, current_file, counter = "working", None, 0
    for line, entry in _split_entries(code_lines):
        up = entry.upper()
        if re.match(r"^(WORKING-STORAGE|LOCAL-STORAGE)\s+SECTION", up):
            section, current_file, stack = "working", None, []
            continue
        if re.match(r"^LINKAGE\s+SECTION", up):
            section, current_file, stack = "linkage", None, []
            continue
        if re.match(r"^FILE\s+SECTION", up):
            section, current_file, stack = "file", None, []
            continue
        m = re.match(r"^(FD|SD)\s+([A-Za-z0-9-]+)", up)
        if m:
            current_file, stack = m.group(2), []
            files.setdefault(current_file, {"assign": current_file, "status": None,
                                            "organization": "SEQUENTIAL", "records": []})
            continue
        toks = [t if t[:1] in "'\"" else t.rstrip(",;") for t in ENTRY_TOKS.findall(entry)]
        toks = [t for t in toks if t]
        if not toks or not toks[0].isdigit():
            continue
        level = int(toks[0])
        rest = toks[1:]
        name = "FILLER"
        if rest and rest[0].upper() not in CLAUSE_WORDS:
            name = rest[0].upper()
            rest = rest[1:]
        if level == 66:
            diagnostics.append(f"line {line}: RENAMES (66) is not supported")
            continue
        if level == 88:
            k = next((i for i, t in enumerate(rest) if t.upper() in {"VALUE", "VALUES"}), None)
            conds[name] = CondName(name, last, _values(rest[k + 1:]) if k is not None else [], line)
            continue
        if level == 77:
            level = 1
        f = Field(name, name, level, line, section=section, file=current_file)
        usage, sign_leading, sign_separate, occurs = "display", False, False, None
        i = 0
        pic_text = None
        while i < len(rest):
            t = rest[i].upper()
            if t in {"PIC", "PICTURE"}:
                j = i + 1
                if j < len(rest) and rest[j].upper() == "IS":
                    j += 1
                pic_text = rest[j]
                i = j + 1
            elif t in {"VALUE", "VALUES"}:
                j = i + 1
                while j < len(rest) and rest[j].upper() in {"IS", "ARE"}:
                    j += 1
                vals = _values(rest[j:j + 2] if rest[j:j + 1] and rest[j].upper() == "ALL" else rest[j:j + 1])
                f.value = vals[0][0] if vals else None
                i = j + (2 if rest[j:j + 1] and rest[j].upper() == "ALL" else 1)
            elif t == "OCCURS":
                occurs = int(rest[i + 1])
                if i + 3 < len(rest) and rest[i + 2].upper() == "TO":
                    occurs = int(rest[i + 3])
                    diagnostics.append(f"line {line}: {name}: OCCURS DEPENDING ON uses the maximum")
                i += 2
                while i < len(rest) and rest[i].upper() in {"TIMES", "TO"}:
                    i += 1
                if i < len(rest) and rest[i].upper() == "INDEXED":
                    i += 2
                    while i < len(rest) and rest[i].upper() not in CLAUSE_WORDS:
                        i += 1
            elif t == "REDEFINES":
                f.redefines = rest[i + 1].upper()
                i += 2
            elif t in USAGES:
                usage = USAGES[t]
                i += 1
            elif t == "USAGE":
                i += 1
            elif t in {"COMP-1", "COMP-2", "INDEX"}:
                diagnostics.append(f"line {line}: {name}: USAGE {t} is not supported")
                usage = "binary"
                i += 1
            elif t == "SIGN":
                i += 1
            elif t in {"LEADING", "TRAILING"}:
                sign_leading = t == "LEADING"
                i += 1
                if i < len(rest) and rest[i].upper() == "SEPARATE":
                    sign_separate = True
                    i += 1
                    if i < len(rest) and rest[i].upper() == "CHARACTER":
                        i += 1
            else:
                i += 1
        if pic_text:
            pic = with_usage(parse_pic(pic_text), usage)
            if sign_leading or sign_separate:
                pic = with_sign(pic, sign_leading, sign_separate)
            f.pic = pic
        elif usage == "binary" and level != 1:
            f.pic = with_usage(parse_pic("S9(9)"), "binary")
        f.occurs = occurs
        while stack and stack[-1].level >= level:
            stack.pop()
        if stack:
            f.parent = stack[-1].key
        key = name
        if name == "FILLER" or name in fields:
            counter += 1
            key = f"{name}#{counter}"
            if name != "FILLER":
                diagnostics.append(f"line {line}: duplicate name {name} (references use the first)")
        f.key = key
        if stack:
            stack[-1].children.append(key)
        elif current_file:
            files[current_file]["records"].append(key)
        stack.append(f)
        fields[key] = f
        last = key
    for reg, pic in SPECIAL_REGISTERS.items():
        if reg not in fields:
            fields[reg] = Field(reg, reg, 1, 0, pic=with_usage(parse_pic(pic), "binary"), section="special")

    def layout(f: Field, offset: int, record: str, dims: list) -> int:
        """Byte offsets and lengths; REDEFINES overlays its sibling, OCCURS repeats."""
        f.offset, f.record, f.dims = offset, record, list(dims)
        if f.pic is not None:
            f.length = f.pic.size
            return f.length
        cur = offset
        for ck in f.children:
            c = fields[ck]
            start = cur
            if c.redefines:
                base = next((fields[k] for k in f.children if fields[k].name == c.redefines), None)
                start = base.offset if base else cur
            child_dims = dims + ([[0, c.occurs]] if c.occurs else [])
            one = layout(c, start, record, child_dims)
            if c.occurs:
                set_stride(c, len(dims), one)
            if not c.redefines:
                cur = start + one * (c.occurs or 1)
        f.length = cur - offset
        return f.length

    def set_stride(f: Field, index: int, stride: int):
        f.dims[index] = [stride, f.dims[index][1]]
        for ck in f.children:
            set_stride(fields[ck], index, stride)

    first_of_file = {}
    for f in list(fields.values()):
        if f.parent is not None:
            continue
        record = f.key
        if f.redefines and f.redefines in fields:
            record = fields[f.redefines].record
        elif f.file:
            record = first_of_file.setdefault(f.file, f.key)
        layout(f, 0, record, [[0, f.occurs]] if f.occurs else [])
        if f.occurs:
            set_stride(f, 0, f.length)
    return fields, conds


# ---------------------------------------------------------------- procedure


class ParseError(Exception):
    pass


class ProcParser:
    def __init__(self, toks: list, fields: dict, conds: dict, diagnostics: list):
        self.t = toks
        self.i = 0
        self.fields = fields
        self.conds = conds
        self.diag = diagnostics
        self.last_subject = None

    def peek(self, k: int = 0):
        j = self.i + k
        return self.t[j] if j < len(self.t) else None

    def at(self, *words) -> bool:
        tok = self.peek()
        return tok is not None and tok.kind in ("word", "op") and tok.up in words

    def take(self):
        tok = self.peek()
        if tok is None:
            raise ParseError("unexpected end of program")
        self.i += 1
        return tok

    def expect(self, word: str):
        tok = self.take()
        if tok.up != word:
            raise ParseError(f"line {tok.line}: expected {word}, found {tok.text}")
        return tok

    def at_period(self) -> bool:
        tok = self.peek()
        return tok is not None and tok.kind == "period"

    def at_stmt_end(self, stops) -> bool:
        tok = self.peek()
        return (tok is None or tok.kind == "period"
                or (tok.kind == "word" and (tok.up in VERBS or tok.up in stops
                                            or tok.up in SCOPE_WORDS)))

    def program(self) -> list:
        paras, current = [], None
        while self.peek() is not None:
            tok, nxt = self.peek(), self.peek(1)
            if (tok.kind == "word" and tok.up not in VERBS and nxt is not None
                    and (nxt.kind == "period" or nxt.up == "SECTION")):
                self.take()
                if nxt.up == "SECTION":
                    self.take()
                self.take()
                if current:
                    current.end_line = max(current.line, self.t[self.i - 2].line - 1)
                current = Paragraph(tok.up, [], tok.line)
                paras.append(current)
                continue
            if current is None:
                current = Paragraph("(MAIN)", [], tok.line)
                paras.append(current)
            try:
                current.stmts.extend(self.stmts(set()))
            except ParseError as exc:
                self.diag.append(str(exc))
                while self.peek() is not None and not self.at_period():
                    self.take()
            if self.at_period():
                self.take()
            elif self.peek() is not None and self.peek().up in SCOPE_WORDS | {"NOT", "AT"}:
                bad = self.take()
                self.diag.append(f"line {bad.line}: stray {bad.text}")
            elif self.peek() is not None and self.peek().kind != "word":
                bad = self.take()
                self.diag.append(f"line {bad.line}: unexpected {bad.text}")
        if current:
            current.end_line = self.t[-1].line
        return paras

    def stmts(self, stops: set) -> list:
        out = []
        while True:
            tok = self.peek()
            if tok is None or tok.kind == "period" or tok.up in stops or tok.up in SCOPE_WORDS:
                return out
            out.append(self.stmt(stops))

    def stmt(self, stops: set):
        tok = self.peek()
        verb = tok.up
        line = tok.line
        if verb == "ACCEPT":
            self.take()
            target = self.ref()
            source = None
            if self.at("FROM"):
                self.take()
                source = self.take().up
            return Accept(target, line, source)
        if verb == "DISPLAY":
            self.take()
            items = []
            while not self.at_stmt_end(stops):
                if self.at("UPON"):
                    self.take(); self.take(); continue
                if self.at("WITH", "NO", "ADVANCING"):
                    self.take(); continue
                items.append(self.atom())
            return Display(items, line)
        if verb == "MOVE":
            self.take()
            if self.at("CORRESPONDING", "CORR"):
                return self.unsupported(line, "MOVE CORRESPONDING", stops)
            src = self.atom()
            self.expect("TO")
            targets = [self.ref()]
            while self.peek() is not None and self.peek().kind == "word" and not self.at_stmt_end(stops):
                targets.append(self.ref())
            return Move(src, targets, line)
        if verb == "COMPUTE":
            self.take()
            targets, rounded = [], []
            while not self.at("=", "EQUAL"):
                targets.append(self.ref())
                if self.at("ROUNDED"):
                    self.take()
                    rounded.append(targets[-1].name)
            self.take()
            expr = self.expr()
            if self.at("ON", "SIZE", "NOT"):
                return self.unsupported(line, "ON SIZE ERROR", stops)
            if self.at("END-COMPUTE"):
                self.take()
            return Compute(targets, expr, line, tuple(rounded))
        if verb in ("ADD", "SUBTRACT"):
            self.take()
            values = [self.atom()]
            while not self.at("TO", "FROM", "GIVING"):
                values.append(self.atom())
            if self.at("GIVING"):
                return self.unsupported(line, f"{verb} GIVING", stops)
            self.take()
            targets = [self.ref()]
            while self.peek() is not None and self.peek().kind == "word" and not self.at_stmt_end(stops) \
                    and not self.at("GIVING", "ON", "ROUNDED", "NOT"):
                targets.append(self.ref())
            if self.at("GIVING", "ON", "SIZE", "NOT"):
                return self.unsupported(line, f"{verb} with GIVING/SIZE ERROR", stops)
            if self.at("END-ADD", "END-SUBTRACT"):
                self.take()
            out = []
            for target in targets:
                expr = target
                for v in values:
                    expr = BinOp("+" if verb == "ADD" else "-", expr, v)
                out.append(Compute([target], expr, line))
            return out[0] if len(out) == 1 else If(Rel("=", Lit(Decimal(0), "0"), Lit(Decimal(0), "0")),
                                                   out, [], line, line)
        if verb == "IF":
            self.take()
            cond = self.cond()
            if self.at("THEN"):
                self.take()
            if self.at("NEXT"):
                return self.unsupported(line, "NEXT SENTENCE", stops)
            then = self.stmts(stops | {"ELSE", "END-IF"})
            else_ = []
            if self.at("ELSE"):
                self.take()
                else_ = self.stmts(stops | {"END-IF"})
            if self.at("END-IF"):
                end = self.take().line
            else:
                end = self.t[self.i - 1].line
            return If(cond, then, else_, line, end)
        if verb == "EVALUATE":
            return self.evaluate(stops)
        if verb == "PERFORM":
            return self.perform(stops)
        if verb == "GO":
            self.take()
            if self.at("TO"):
                self.take()
            target = self.take().up
            if self.at("DEPENDING"):
                return self.unsupported(line, "GO TO DEPENDING ON", stops)
            return GoTo(target, line)
        if verb == "CALL":
            self.take()
            target = self.atom()
            args = []
            if self.at("USING"):
                self.take()
                while not self.at_stmt_end(stops) and not self.at("RETURNING", "ON", "EXCEPTION", "OVERFLOW", "NOT"):
                    if self.at("BY"):
                        self.take()
                        if self.at("CONTENT", "VALUE"):
                            self.diag.append(f"line {line}: CALL BY {self.peek().text} treated as BY REFERENCE")
                        self.take()
                        continue
                    if self.at("REFERENCE", "CONTENT", "VALUE"):
                        self.take()
                        continue
                    args.append(self.atom())
            if self.at("RETURNING", "ON", "EXCEPTION", "OVERFLOW", "NOT"):
                return self.unsupported(line, "CALL RETURNING/EXCEPTION", stops)
            if self.at("END-CALL"):
                self.take()
            return Call(target, args, line)
        if verb == "OPEN":
            self.take()
            files, mode = [], None
            while not self.at_stmt_end(stops):
                if self.at("INPUT", "OUTPUT", "I-O", "EXTEND"):
                    mode = self.take().up
                    continue
                files.append((mode, self.take().up))
            return Open(files, line)
        if verb == "CLOSE":
            self.take()
            files = []
            while not self.at_stmt_end(stops):
                if self.at("WITH", "LOCK", "NO", "REWIND"):
                    self.take()
                    continue
                files.append(self.take().up)
            return Close(files, line)
        if verb == "READ":
            self.take()
            file = self.take().up
            into = None
            while self.at("NEXT", "RECORD", "INTO"):
                if self.take().up == "INTO":
                    into = self.ref()
            at_end, not_at_end = [], []
            if self.at("AT", "END"):
                if self.at("AT"):
                    self.take()
                self.expect("END")
                at_end = self.stmts(stops | {"NOT", "END-READ"})
            if self.at("NOT"):
                self.take()
                if self.at("AT"):
                    self.take()
                self.expect("END")
                not_at_end = self.stmts(stops | {"END-READ"})
            if self.at("INVALID"):
                return self.unsupported(line, "READ INVALID KEY", stops)
            if self.at("END-READ"):
                self.take()
            return Read(file, into, at_end, not_at_end, line)
        if verb == "WRITE":
            self.take()
            record = self.ref()
            source = None
            if self.at("FROM"):
                self.take()
                source = self.atom()
            if self.at("BEFORE", "AFTER"):
                self.take()
                if self.at("ADVANCING"):
                    self.take()
                if self.at("PAGE"):
                    self.take()
                else:
                    self.atom()
                    if self.at("LINE", "LINES"):
                        self.take()
            if self.at("INVALID", "AT", "END-OF-PAGE", "EOP"):
                return self.unsupported(line, "WRITE INVALID KEY/END-OF-PAGE", stops)
            if self.at("END-WRITE"):
                self.take()
            return Write(record, source, line)
        if verb == "INITIALIZE":
            self.take()
            targets = [self.ref()]
            while self.peek() is not None and self.peek().kind == "word" and not self.at_stmt_end(stops):
                if self.at("REPLACING"):
                    return self.unsupported(line, "INITIALIZE REPLACING", stops)
                targets.append(self.ref())
            return Initialize(targets, line)
        if verb == "SET":
            start = self.i
            self.take()
            names = []
            while self.peek() is not None and self.peek().kind == "word" and not self.at("TO"):
                names.append(self.take().up)
            if self.at("TO") and self.peek(1) is not None and self.peek(1).up == "TRUE" and \
                    all(n in self.conds for n in names):
                self.take(); self.take()
                return SetTrue(names, line)
            self.i = start
            return self.unsupported(line, "SET", stops)
        if verb == "STOP":
            self.take(); self.expect("RUN")
            return Stop(line)
        if verb == "GOBACK":
            self.take()
            return Stop(line)
        if verb == "EXIT":
            self.take()
            if self.at("PROGRAM"):
                self.take()
                return Stop(line)
            if self.at("PARAGRAPH", "SECTION", "PERFORM"):
                return self.unsupported(line, f"EXIT {self.peek().text}", stops)
            return Continue(line)
        if verb == "CONTINUE":
            self.take()
            return Continue(line)
        return self.unsupported(line, verb, stops)

    def unsupported(self, line: int, what: str, stops: set):
        words = [self.take().text]
        while not self.at_stmt_end(stops):
            words.append(self.take().text)
        if self.peek() is not None and self.peek().up.startswith("END-") and \
                self.peek().up not in {"END-IF", "END-EVALUATE", "END-PERFORM"}:
            words.append(self.take().text)
        self.diag.append(f"line {line}: unsupported statement {what}")
        return Unsupported(" ".join(words), line)

    def evaluate(self, stops: set):
        line = self.take().line
        if self.at("TRUE"):
            self.take()
            subject = None
        else:
            subject = self.expr()
        if self.at("ALSO"):
            return self.unsupported(line, "EVALUATE ... ALSO", stops)
        whens, other = [], None
        inner = stops | {"WHEN", "END-EVALUATE"}
        while self.at("WHEN"):
            wline = self.take().line
            if self.at("OTHER"):
                self.take()
                other = self.stmts(inner)
                continue
            cond = self.when_object(subject)
            while self.at("WHEN") and not (self.peek(1) and self.peek(1).up == "OTHER"):
                self.take()
                cond = Or(cond, self.when_object(subject))
            whens.append(When(cond, self.stmts(inner), wline))
        end = self.t[self.i - 1].line
        if self.at("END-EVALUATE"):
            end = self.take().line
        return Evaluate(whens, other, line, end)

    def when_object(self, subject):
        if subject is None:
            return self.cond()
        if self.at("ANY"):
            self.take()
            return Rel("=", Lit(Decimal(0), "0"), Lit(Decimal(0), "0"))
        negate = False
        if self.at("NOT"):
            self.take()
            negate = True
        lo = self.expr()
        if self.at("THRU", "THROUGH"):
            self.take()
            hi = self.expr()
            c = And(Rel(">=", subject, lo), Rel("<=", subject, hi))
        else:
            c = Rel("=", subject, lo)
        return Not(c) if negate else c

    def perform(self, stops: set):
        line = self.take().line
        target = thru = times = until = varying = None
        test_after = False
        body = []
        tok = self.peek()
        inline = tok is None or tok.kind != "word" or tok.up in {"UNTIL", "VARYING", "WITH", "TEST"} \
            or (self.peek(1) is not None and self.peek(1).up == "TIMES")
        if not inline:
            target = self.take().up
            if self.at("THRU", "THROUGH"):
                self.take()
                thru = self.take().up
        if self.at("WITH"):
            self.take()
        if self.at("TEST"):
            self.take()
            test_after = self.take().up == "AFTER"
        if self.at("VARYING"):
            self.take()
            var = self.ref()
            self.expect("FROM")
            start = self.expr()
            step = Lit(Decimal(1), "1")
            if self.at("BY"):
                self.take()
                step = self.expr()
            self.expect("UNTIL")
            until = self.cond()
            varying = (var, start, step)
            if self.at("AFTER"):
                return self.unsupported(line, "PERFORM VARYING ... AFTER", stops)
        elif self.at("UNTIL"):
            self.take()
            until = self.cond()
        elif self.peek() is not None and self.peek(1) is not None and self.peek(1).up == "TIMES":
            times = self.atom()
            self.take()
        if inline:
            body = self.stmts(stops | {"END-PERFORM"})
            if self.at("END-PERFORM"):
                self.take()
        return Perform(target, thru, times, until, body, line, varying, test_after)

    # operands and expressions
    def ref(self) -> Ref:
        tok = self.take()
        if tok.kind != "word":
            raise ParseError(f"line {tok.line}: expected a data name, found {tok.text}")
        name = tok.up
        while self.at("OF", "IN"):
            self.take(); self.take()
        subs, refmod = (), None
        if self.at("("):
            self.take()
            first = self.expr()
            if self.at(":"):
                self.take()
                length = None if self.at(")") else self.expr()
                self.expect(")")
                return Ref(name, (), (first, length))
            items = [first]
            while not self.at(")"):
                items.append(self.expr())
            self.expect(")")
            subs = tuple(items)
            if self.at("("):
                self.take()
                start = self.expr()
                self.expect(":")
                length = None if self.at(")") else self.expr()
                self.expect(")")
                refmod = (start, length)
        return Ref(name, subs, refmod)

    def atom(self):
        tok = self.peek()
        if tok.kind == "num":
            self.take()
            return Lit(Decimal(tok.text), tok.text)
        if tok.kind == "str":
            self.take()
            return Lit(tok.text[1:-1], tok.text)
        if tok.kind == "word" and tok.up in FIGURATIVE:
            self.take()
            return Lit(FIGURATIVE[tok.up], tok.up)
        if tok.kind == "word" and tok.up == "ALL":
            self.take()
            lit = self.take()
            return Lit(Fig("ALL", lit.text[1:-1] if lit.kind == "str" else lit.text), f"ALL {lit.text}")
        return self.ref()

    def expr(self):
        left = self.term()
        while self.at("+", "-"):
            op = self.take().text
            left = BinOp(op, left, self.term())
        return left

    def term(self):
        left = self.factor()
        while self.at("*", "/"):
            op = self.take().text
            left = BinOp(op, left, self.factor())
        return left

    def factor(self):
        if self.at("-"):
            self.take()
            return Neg(self.factor())
        if self.at("+"):
            self.take()
            return self.factor()
        base = self.primary()
        if self.at("**"):
            self.take()
            return BinOp("**", base, self.factor())
        return base

    def primary(self):
        if self.at("("):
            self.take()
            e = self.expr()
            self.expect(")")
            return e
        return self.atom()

    # conditions
    def cond(self):
        c = self.and_cond()
        while self.at("OR"):
            self.take()
            c = Or(c, self.and_cond())
        return c

    def and_cond(self):
        c = self.not_cond()
        while self.at("AND"):
            self.take()
            c = And(c, self.not_cond())
        return c

    def not_cond(self):
        if self.at("NOT") and not self._rel_ahead(1):
            self.take()
            return Not(self.not_cond())
        return self.simple_cond()

    def _rel_ahead(self, k: int) -> bool:
        tok = self.peek(k)
        return tok is not None and tok.up in REL_WORDS

    def rel_op(self):
        start = self.i
        if self.at("IS"):
            self.take()
        neg = False
        if self.at("NOT"):
            self.take()
            neg = True
        tok = self.peek()
        op = None
        if tok is not None and tok.kind == "op" and tok.text in {"=", "<", ">", "<=", ">=", "<>"}:
            self.take()
            op = tok.text
            if op in "<>" and self.at("="):
                op += self.take().text
        elif self.at("EQUAL", "EQUALS"):
            self.take()
            if self.at("TO"):
                self.take()
            op = "="
        elif self.at("GREATER", "LESS"):
            base = ">" if self.take().up == "GREATER" else "<"
            if self.at("THAN"):
                self.take()
            if self.at("OR"):
                self.take(); self.expect("EQUAL")
                if self.at("TO"):
                    self.take()
                base += "="
            op = base
        if op is None:
            self.i = start
            return None
        if neg:
            op = {"=": "<>", "<>": "=", "<": ">=", ">": "<=", "<=": ">", ">=": "<"}[op]
        return op

    def class_or_sign(self, left):
        start = self.i
        if self.at("IS"):
            self.take()
        neg = False
        if self.at("NOT"):
            self.take()
            neg = True
        c = None
        if self.at(*CLASS_WORDS):
            c = ClassCond(left, self.take().up)
        elif self.at("POSITIVE", "NEGATIVE", "ZERO"):
            kind = self.take().up
            c = Rel({"POSITIVE": ">", "NEGATIVE": "<", "ZERO": "="}[kind], left, Lit(Decimal(0), "0"))
        if c is None:
            self.i = start
            return None
        return Not(c) if neg else c

    def simple_cond(self):
        if self.peek() is not None and (self.peek().up in REL_WORDS or
                                         (self.at("NOT") and self._rel_ahead(1))):
            op = self.rel_op()
            if op and self.last_subject is not None:
                return Rel(op, self.last_subject, self.expr())
        if self.at("("):
            start = self.i
            try:
                left = self.expr()
                op = self.rel_op()
                if op:
                    self.last_subject = left
                    return Rel(op, left, self.expr())
                c = self.class_or_sign(left)
                if c is not None:
                    return c
            except ParseError:
                pass
            self.i = start
            self.take()
            c = self.cond()
            self.expect(")")
            return c
        left = self.expr()
        op = self.rel_op()
        if op:
            self.last_subject = left
            return Rel(op, left, self.expr())
        c = self.class_or_sign(left)
        if c is not None:
            return c
        if isinstance(left, Ref) and left.name in self.conds:
            return CondRef(left.name)
        tok = self.peek()
        raise ParseError(f"line {tok.line if tok else '?'}: cannot read condition")


def parse_program(path, copy_dirs=None) -> Program:
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    diagnostics: list = []
    locs: dict = {}
    dirs = [path.parent] + [Path(d) for d in (copy_dirs or [])]
    code = expand_copies(read_fixed(text), dirs, locs, diagnostics, [0])
    upper = [(no, c.upper()) for no, c in code]

    def find(marker):
        return next((k for k, (_, c) in enumerate(upper) if marker in c), None)

    data_start = find("DATA DIVISION")
    proc_start = find("PROCEDURE DIVISION")
    env_start = find("ENVIRONMENT DIVISION")
    name_m = next((re.search(r"PROGRAM-ID\.\s*['\"]?([A-Z0-9-]+)", c) for _, c in upper if "PROGRAM-ID" in c), None)
    files = parse_environment(code[env_start:data_start]) if env_start is not None and data_start else {}
    fields, conds = ({}, {}) if data_start is None else parse_data(code[data_start + 1:proc_start], diagnostics, files)
    toks = tokenize(code[proc_start:])
    k = 0
    using = []
    while toks[k].kind != "period":
        k += 1
    head = [t for t in toks[:k] if t.kind == "word"]
    if any(t.up == "USING" for t in head):
        u = [t.up for t in head]
        using = [w for w in u[u.index("USING") + 1:] if w not in {"BY", "REFERENCE", "VALUE", "CONTENT"}]
    parser = ProcParser(toks[k + 1:], fields, conds, diagnostics)
    paragraphs = parser.program()
    lines = list(enumerate(text.splitlines(), 1))
    return Program(name_m.group(1) if name_m else path.stem, str(path), lines, fields, conds,
                   paragraphs, diagnostics, using, files, locs)
