"""Reader and parser for a small, documented subset of fixed-format COBOL.

Supported: WORKING-STORAGE fields (levels, PIC 9/S/V/X, VALUE, 88-level
condition names with values or THRU ranges); paragraphs; ACCEPT, DISPLAY,
MOVE, COMPUTE, ADD ... TO, SUBTRACT ... FROM, IF/ELSE/END-IF, EVALUATE
(TRUE or one subject, WHEN OTHER), PERFORM (paragraph, THRU, n TIMES,
UNTIL, inline UNTIL/TIMES), STOP RUN, GOBACK, EXIT, CONTINUE.
Anything else is kept as an Unsupported node with a diagnostic, never guessed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from .numeric import Pic, parse_pic

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
    level: int
    line: int
    pic: Pic | None = None
    value: object = None
    parent: str | None = None
    children: list = field(default_factory=list)
    offset: int = 0
    length: int = 0

    @property
    def is_group(self) -> bool:
        return self.pic is None


@dataclass
class Lit:
    value: object          # Decimal for numbers, str for strings
    text: str


@dataclass
class Ref:
    name: str


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
    target: str
    line: int


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


@dataclass
class If:
    cond: object
    then: list
    else_: list
    line: int
    end_line: int = 0


@dataclass
class When:
    cond: object           # condition this branch tests (already built from the subject)
    stmts: list
    line: int


@dataclass
class Evaluate:
    whens: list            # list[When]
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
    lines: list            # list[(lineno, text)] of the original source
    fields: dict           # name -> Field
    conds: dict            # condition-name -> CondName
    paragraphs: list       # list[Paragraph]
    diagnostics: list

    def paragraph(self, name: str) -> Paragraph:
        for p in self.paragraphs:
            if p.name == name:
                return p
        raise KeyError(name)


# ---------------------------------------------------------------- source


def read_fixed(text: str) -> list:
    """Fixed format: columns 8-72 are code, '*' or '/' in column 7 is a comment.
    Returns [(lineno, code)] for code lines."""
    out = []
    for no, raw in enumerate(text.splitlines(), 1):
        raw = raw.rstrip("\n")
        if len(raw) < 7:
            continue
        ind = raw[6]
        if ind in "*/":
            continue
        if ind == "-":
            raise ValueError(f"line {no}: continuation lines are not supported")
        code = raw[7:72].rstrip()
        if code.strip():
            out.append((no, code))
    return out


# ---------------------------------------------------------------- tokens

TOKEN = re.compile(r"""
 (?P<str>'[^']*'|"[^"]*")
|(?P<num>[+-]?\d+(?:\.\d+)?(?![\w-]))
|(?P<word>[A-Za-z0-9][A-Za-z0-9-]*[A-Za-z0-9]|[A-Za-z0-9])
|(?P<op>>=|<=|<>|\*\*|[=<>()*/+-])
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
}
SCOPE_WORDS = {"ELSE", "END-IF", "WHEN", "END-EVALUATE", "END-PERFORM", "END-COMPUTE",
               "END-ADD", "END-SUBTRACT"}
REL_WORDS = {"=", "<", ">", "<=", ">=", "<>", "EQUAL", "GREATER", "LESS", "EQUALS"}
FIGURATIVE = {"ZERO": Decimal(0), "ZEROS": Decimal(0), "ZEROES": Decimal(0),
              "SPACE": " ", "SPACES": " "}


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


def _literal(tok: str):
    up = tok.upper()
    if up in FIGURATIVE:
        return FIGURATIVE[up]
    if tok[:1] in "'\"":
        return tok[1:-1]
    return Decimal(tok)


VALUE_TOKS = re.compile(r"'[^']*'|\"[^\"]*\"|[+-]?\d+(?:\.\d+)?|[A-Za-z-]+")


def parse_data(code_lines: list, diagnostics: list):
    fields, conds, stack, last = {}, {}, [], None
    for line, entry in _split_entries(code_lines):
        m = re.match(r"(\d{1,2})\s+([A-Za-z0-9][A-Za-z0-9-]*)?(.*)$", entry, re.S)
        if not m:
            continue                      # section headers, FD, etc.
        level = int(m.group(1))
        name = (m.group(2) or "FILLER").upper()
        rest = m.group(3) or ""
        if name in {"PIC", "PICTURE", "VALUE"}:
            rest, name = f"{name} {rest}", "FILLER"
        if level == 88:
            vm = re.search(r"\bVALUES?\s+(?:ARE\s+|IS\s+)?(.*)$", rest, re.I | re.S)
            toks = VALUE_TOKS.findall(vm.group(1)) if vm else []
            values, i = [], 0
            while i < len(toks):
                lo = _literal(toks[i])
                if i + 2 < len(toks) and toks[i + 1].upper() in {"THRU", "THROUGH"}:
                    values.append((lo, _literal(toks[i + 2])))
                    i += 3
                else:
                    values.append((lo, lo))
                    i += 1
            conds[name] = CondName(name, last, values, line)
            continue
        for word in ("OCCURS", "REDEFINES", "COMP", "COMP-3", "BINARY", "PACKED-DECIMAL"):
            if re.search(rf"\b{word}\b", rest, re.I):
                diagnostics.append(f"line {line}: {name}: {word} is not supported")
        pm = re.search(r"\bPIC(?:TURE)?\s+(?:IS\s+)?(\S+)", rest, re.I)
        f = Field(name, level, line)
        if pm:
            f.pic = parse_pic(pm.group(1))
        vm = re.search(r"\bVALUE\s+(?:IS\s+)?('[^']*'|\"[^\"]*\"|[+-]?\d+(?:\.\d+)?|[A-Za-z-]+)",
                       rest, re.I)
        if vm:
            f.value = _literal(vm.group(1))
        while stack and stack[-1].level >= level:
            stack.pop()
        if stack:
            f.parent = stack[-1].name
            stack[-1].children.append(name)
        stack.append(f)
        if name != "FILLER":
            fields[name] = f
        last = name
    # lengths and offsets
    def size(f: Field, offset: int) -> int:
        f.offset = offset
        if f.pic is not None:
            f.length = f.pic.length
        else:
            total = 0
            for child in f.children:
                total += size(fields[child], offset + total)
            f.length = total
        return f.length
    for f in fields.values():
        if f.parent is None:
            size(f, 0)
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

    # helpers
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

    # paragraphs
    def program(self) -> list:
        paras = []
        current = None
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
            current.stmts.extend(self.stmts(set()))
            if self.at_period():
                self.take()
            elif self.peek() is not None and self.peek().kind != "word":
                raise ParseError(f"line {self.peek().line}: unexpected {self.peek().text}")
            elif self.peek() is not None and self.peek().up in SCOPE_WORDS:
                bad = self.take()
                self.diag.append(f"line {bad.line}: stray {bad.text}")
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
            target = self.ident()
            if self.at("FROM"):
                self.take(); self.take()
            return Accept(target, line)
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
            src = self.atom()
            self.expect("TO")
            targets = [self.ident()]
            while self.peek() is not None and self.peek().kind == "word" and not self.at_stmt_end(stops):
                targets.append(self.ident())
            return Move(src, targets, line)
        if verb == "COMPUTE":
            self.take()
            targets = []
            while not self.at("=", "EQUAL"):
                targets.append(self.ident())
                if self.at("ROUNDED"):
                    self.take()
                    self.diag.append(f"line {line}: ROUNDED is treated as truncation")
            self.take()
            expr = self.expr()
            if self.at("ON", "SIZE"):
                return self.unsupported(line, "ON SIZE ERROR", stops)
            if self.at("END-COMPUTE"):
                self.take()
            return Compute(targets, expr, line)
        if verb in ("ADD", "SUBTRACT"):
            self.take()
            values = [self.atom()]
            while not self.at("TO", "FROM"):
                values.append(self.atom())
            self.take()
            target = self.ident()
            if self.at("GIVING", "ON", "SIZE"):
                return self.unsupported(line, f"{verb} with GIVING/SIZE ERROR", stops)
            if self.at("END-ADD", "END-SUBTRACT"):
                self.take()
            expr = Ref(target)
            for v in values:
                expr = BinOp("+" if verb == "ADD" else "-", expr, v)
            return Compute([target], expr, line)
        if verb == "IF":
            self.take()
            cond = self.cond()
            if self.at("THEN"):
                self.take()
            then = self.stmts(stops | {"ELSE", "END-IF"})
            else_ = []
            if self.at("ELSE"):
                self.take()
                else_ = self.stmts(stops | {"END-IF"})
            end = line
            if self.at("END-IF"):
                end = self.take().line
            else:
                end = self.t[self.i - 1].line
            return If(cond, then, else_, line, end)
        if verb == "EVALUATE":
            return self.evaluate(stops)
        if verb == "PERFORM":
            return self.perform(stops)
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
            if self.at("PARAGRAPH", "SECTION"):
                self.take()
            return Continue(line)
        if verb == "CONTINUE":
            self.take()
            return Continue(line)
        return self.unsupported(line, verb, stops)

    def unsupported(self, line: int, what: str, stops: set):
        words = [self.take().text]
        while not self.at_stmt_end(stops):
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
        target = thru = times = until = None
        body = []
        tok = self.peek()
        inline = tok is None or tok.kind != "word" or tok.up in {"UNTIL", "VARYING", "WITH", "TEST"} \
            or (self.peek(1) is not None and self.peek(1).up == "TIMES")
        if not inline:
            target = self.take().up
            if self.at("THRU", "THROUGH"):
                self.take()
                thru = self.take().up
        if self.at("WITH", "TEST"):
            while not self.at("UNTIL"):
                self.take()
        if self.at("VARYING"):
            return self.unsupported(line, "PERFORM VARYING", stops)
        if self.at("UNTIL"):
            self.take()
            until = self.cond()
        elif self.peek() is not None and self.peek(1) is not None and self.peek(1).up == "TIMES":
            times = self.atom()
            self.take()
        if inline:
            body = self.stmts(stops | {"END-PERFORM"})
            if self.at("END-PERFORM"):
                self.take()
        return Perform(target, thru, times, until, body, line)

    # operands and expressions
    def ident(self) -> str:
        tok = self.take()
        if tok.kind != "word":
            raise ParseError(f"line {tok.line}: expected a data name, found {tok.text}")
        if self.at("OF", "IN"):
            self.take(); self.take()
        if self.at("("):
            raise ParseError(f"line {tok.line}: subscripts and reference modification are not supported")
        return tok.up

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
            raise ParseError(f"line {tok.line}: ALL literals are not supported")
        return Ref(self.ident())

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

    def simple_cond(self):
        # abbreviated combined relation: "A > 1 AND < 5"
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
        if isinstance(left, Ref) and left.name in self.conds:
            return CondRef(left.name)
        tok = self.peek()
        raise ParseError(f"line {tok.line if tok else '?'}: cannot read condition")


def parse_program(path) -> Program:
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    code = read_fixed(text)
    diagnostics: list = []
    upper = [(no, c.upper()) for no, c in code]
    data_start = next((k for k, (_, c) in enumerate(upper) if "DATA DIVISION" in c), None)
    proc_start = next(k for k, (_, c) in enumerate(upper) if "PROCEDURE DIVISION" in c)
    name_m = next((re.search(r"PROGRAM-ID\.\s*([A-Z0-9-]+)", c) for _, c in upper
                   if "PROGRAM-ID" in c), None)
    fields, conds = ({}, {}) if data_start is None else parse_data(
        code[data_start + 1:proc_start], diagnostics)
    toks = tokenize(code[proc_start:])
    k = 0
    while toks[k].kind != "period":     # skip "PROCEDURE DIVISION [USING ...]."
        k += 1
    parser = ProcParser(toks[k + 1:], fields, conds, diagnostics)
    paragraphs = parser.program()
    lines = list(enumerate(text.splitlines(), 1))
    return Program(name_m.group(1) if name_m else path.stem, str(path), lines,
                   fields, conds, paragraphs, diagnostics)
