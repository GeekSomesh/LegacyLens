"""Findings a reviewer should look at, found from the parsed program and its rules.

- status mismatch: a rule tests one file's FILE STATUS but its message reports
  another file's status field;
- file never closed: a file is OPENed but no CLOSE names it;
- paragraph never used: no PERFORM or GO TO reaches it and it cannot be
  reached by falling through from the start.
"""
from __future__ import annotations

from . import cobol as C
from .rules import refs_in, walk_stmts


def _status_fields(prog: C.Program) -> dict:
    return {d["status"]: name for name, d in prog.files.items() if d.get("status")}


def findings(prog: C.Program, rules: list) -> list:
    out = []
    status = _status_fields(prog)
    for r in rules:
        tested = set()
        for c, _, _ in r.conditions:
            tested |= refs_in(c) & set(status)
        if not tested:
            continue
        reported = {}
        for kind, *rest in r.steps:
            if kind == "exec" and isinstance(rest[0], C.Move) and isinstance(rest[0].source, C.Ref):
                if rest[0].source.name in status:
                    reported[rest[0].source.name] = rest[0].line
        wrong = {f: line for f, line in reported.items() if f not in tested}
        for f, line in wrong.items():
            out.append({"kind": "status mismatch", "rule": r.id, "lines": [line],
                        "message": f"{r.id} tests {', '.join(sorted(tested))} ({', '.join(status[t] for t in sorted(tested))}) "
                                   f"but reports {f}, the status of {status[f]}"})
    opened, closed = {}, set()
    for p in prog.paragraphs:
        for s in walk_stmts(p.stmts):
            if isinstance(s, C.Open):
                for _, f in s.files:
                    opened.setdefault(f, s.line)
            elif isinstance(s, C.Close):
                closed |= set(s.files)
    for f, line in opened.items():
        if f not in closed:
            out.append({"kind": "file never closed", "lines": [line],
                        "message": f"{f} is opened (line {line}) but never closed"})
    used = set()
    for p in prog.paragraphs:
        for s in walk_stmts(p.stmts):
            if isinstance(s, C.Perform) and s.target:
                i = prog.para_index(s.target)
                j = prog.para_index(s.thru) if s.thru else i
                used |= {prog.paragraphs[k].name for k in range(i, j + 1)}
            elif isinstance(s, C.GoTo):
                used.add(s.target)
    # fall-through from the first paragraph until a STOP RUN / GOBACK
    for p in prog.paragraphs:
        used.add(p.name)
        if any(isinstance(s, C.Stop) for s in p.stmts):
            break
    for p in prog.paragraphs:
        if p.name not in used:
            out.append({"kind": "paragraph never used", "lines": [p.line],
                        "message": f"paragraph {p.name} is never performed or reached"})
    return out
