"""Behavioural checks for programs that are CALLed with parameters.

A small COBOL driver is generated: it ACCEPTs each USING parameter as one line,
CALLs the program (loaded dynamically and CANCELled after every call, so each
call starts from the program's initial state), and DISPLAYs every parameter
afterwards. The rule engine runs the extracted rules on the same bytes, and
the parameters are compared byte for byte.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from decimal import Decimal
from pathlib import Path

from . import cobol as C
from .rules import State

BUILD = Path(__file__).resolve().parent.parent / "build"


def driver_source(prog: C.Program) -> str:
    sizes = [prog.fields[u].length for u in prog.using]
    lines = [
        "       IDENTIFICATION DIVISION.",
        "       PROGRAM-ID. LLDRIVER.",
        "       DATA DIVISION.",
        "       WORKING-STORAGE SECTION.",
        "       01  LL-COUNT              PIC 9(6).",
    ]
    lines += [f"       01  LL-P{i + 1}  PIC X({n})." for i, n in enumerate(sizes)]
    args = " ".join(f"LL-P{i + 1}" for i in range(len(sizes)))
    lines += [
        "       PROCEDURE DIVISION.",
        "       LL-MAIN.",
        "           ACCEPT LL-COUNT",
        "           PERFORM LL-ONE LL-COUNT TIMES",
        "           STOP RUN.",
        "       LL-ONE.",
    ]
    lines += [f"           ACCEPT LL-P{i + 1}" for i in range(len(sizes))]
    lines += [f"           CALL '{prog.name}' USING {args}",
              f"           CANCEL '{prog.name}'"]
    lines += [f"           DISPLAY LL-P{i + 1}" for i in range(len(sizes))]
    lines[-1] += "."
    return "\n".join(lines) + "\n"


def build(prog: C.Program, copy_dirs=(), out_dir: Path = BUILD) -> tuple:
    """Compile the program as a loadable module and the driver as an executable."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cobc = shutil.which("cobc")
    if not cobc:
        raise RuntimeError("GnuCOBOL (cobc) is not installed")
    incl = [x for d in copy_dirs for x in ("-I", str(d))]
    mod = out_dir / f"{prog.name}.so"
    r = subprocess.run([cobc, "-m", "-o", str(mod), *incl, prog.path], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"cobc {prog.name}: {r.stderr}")
    src = out_dir / f"{prog.name}_driver.cbl"
    src.write_text(driver_source(prog))
    exe = out_dir / f"{prog.name}_driver"
    r = subprocess.run([cobc, "-x", "-o", str(exe), str(src)], capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"cobc driver: {r.stderr}")
    return exe, out_dir


def run(prog: C.Program, exe: Path, lib: Path, cases: list, timeout: int = 600) -> list:
    """cases: list of dicts param -> bytes. Returns list of lists of parameter texts."""
    n = len(prog.using)
    lines = [f"{len(cases):06d}"]
    for case in cases:
        for u in prog.using:
            text = case[u].decode("latin-1")
            if "\n" in text or "\r" in text:
                raise ValueError("parameters must not contain line breaks")
            lines.append(text)
    env = dict(os.environ, COB_LIBRARY_PATH=str(lib))
    r = subprocess.run([str(exe)], input="\n".join(lines) + "\n", capture_output=True, text=True,
                       encoding="latin-1", env=env, timeout=timeout)
    if r.returncode:
        raise RuntimeError(f"driver exited {r.returncode}: {r.stderr[:400]}")
    out = r.stdout.split("\n")
    if out and out[-1] == "":
        out.pop()
    if len(out) != n * len(cases):
        raise RuntimeError(f"expected {n * len(cases)} lines, got {len(out)}")
    sizes = [prog.fields[u].length for u in prog.using]
    return [[out[k * n + i].ljust(sizes[i]) for i in range(n)] for k in range(len(cases))]


def blank_params(prog: C.Program) -> dict:
    """Parameters with every field at its natural empty value (spaces, zeros)."""
    st = State(prog)
    out = {}
    for u in prog.using:
        f = prog.fields[u]
        buf = bytearray(b" " * f.length)
        st.bind(f.record, buf)
        _fill_defaults(st, prog, f)
        out[u] = bytes(buf)
    return out


def _fill_defaults(st: State, prog: C.Program, f: C.Field):
    if f.pic is not None:
        for ref in st._occurrences(f):
            st.set(ref, Decimal(0) if f.pic.kind == "num" else C.Fig("SPACE"))
        return
    for ck in f.children:
        c = prog.fields[ck]
        if not c.redefines:
            _fill_defaults(st, prog, c)


def apply_values(prog: C.Program, params: dict, values: dict) -> dict:
    """Write field values (from a z3 witness) into parameter bytes. Larger fields
    first so that overlapping smaller fields win; NUMERIC=False writes letters."""
    st = State(prog)
    bufs = {u: bytearray(b) for u, b in params.items()}
    for u, b in bufs.items():
        st.bind(prog.fields[u].record, b)
    items = []
    for key, v in values.items():
        name = key.split("#")[0]
        f = prog.fields.get(name)
        if f is None or f.record not in {prog.fields[u].record for u in bufs}:
            continue
        items.append((f.length, key, f, v))
    for _, key, f, v in sorted(items, key=lambda t: -t[0]):
        ref = C.Ref(f.key)
        if key.endswith("#numeric"):
            if v is False:
                buf, off, ln, _ = st.locate(ref)
                buf[off:off + ln] = b"X" * ln
            continue
        if v is None:
            st.set(ref, "Z" * f.length) if f.pic.kind != "num" else None
        elif f.pic.kind == "num":
            st.set(ref, Decimal(v))
        else:
            st.set(ref, str(v))
    return {u: bytes(b) for u, b in bufs.items()}
