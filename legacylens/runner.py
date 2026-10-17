"""Compile a COBOL program with GnuCOBOL and run it on a batch of records."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

BUILD = Path(__file__).resolve().parent.parent / "build"


def compile_program(source, out_dir: Path = BUILD) -> Path:
    source = Path(source)
    out_dir.mkdir(parents=True, exist_ok=True)
    exe = out_dir / source.stem
    if exe.exists() and exe.stat().st_mtime >= source.stat().st_mtime:
        return exe
    cobc = shutil.which("cobc")
    if not cobc:
        raise RuntimeError("GnuCOBOL (cobc) is not installed")
    proc = subprocess.run([cobc, "-x", "-o", str(exe), str(source)],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"cobc failed:\n{proc.stderr}")
    return exe


def run_batch(exe: Path, records: list, timeout: int = 600) -> list:
    """Feed a record count and the records on stdin; return one output line per record."""
    stdin = f"{len(records):06d}\n" + "".join(r + "\n" for r in records)
    proc = subprocess.run([str(exe)], input=stdin, capture_output=True, text=True,
                          timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"{exe.name} exited {proc.returncode}: {proc.stderr[:500]}")
    lines = proc.stdout.splitlines()
    if len(lines) != len(records):
        raise RuntimeError(f"expected {len(records)} output lines, got {len(lines)}")
    return lines
