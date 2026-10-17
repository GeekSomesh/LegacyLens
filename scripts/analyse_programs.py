"""Analyse every program LegacyLens ships: rules, z3 report, example inputs and findings.

    python scripts/analyse_programs.py      # results/programs/<NAME>.json and index.json
"""
import json
import re
import sys
import time
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


from legacylens import cobol as C, glossary, impact, lint, rules as R, smt, subprog  # noqa: E402
from legacylens.runner import compile_program, run_batch  # noqa: E402

IBM = ROOT / "samples" / "ibm-zopeneditor"
PROGRAMS = [
    {"name": "CREDREV", "source": ROOT / "cobol" / "CREDREV.cbl", "copy": [],
     "origin": "Credit line review policy written for LegacyLens", "verification": "verification.json"},
    {"name": "SAM2", "source": IBM / "COBOL" / "SAM2.cbl", "copy": [IBM / "COPYBOOK"],
     "origin": "IBM zopeneditor-sample (Apache-2.0): customer update subroutine", "verification": "ibm_sam2.json"},
    {"name": "SAM1", "source": IBM / "COBOL" / "SAM1.cbl", "copy": [IBM / "COPYBOOK"],
     "origin": "IBM zopeneditor-sample (Apache-2.0): batch customer file update", "verification": "ibm_sam1.json",
     "kind": "program model on whole file runs"},
]


def plain(v):
    if isinstance(v, Decimal):
        return str(v.normalize()) if v == v.to_integral() else str(v)
    return v


def _dmn_info(name):
    f = ROOT / "results" / "dmn.json"
    if not f.exists():
        return None
    info = json.loads(f.read_text()).get(name)
    return info if info and info.get("exported") else None


def value_risks(prog, kept, analysis, spec) -> list:
    """z3: numeric stores that can drop high-order digits or the sign; each example
    is run through the compiled program to show what it really stores."""
    seen = {}
    for r in kept:
        for risk in analysis.store_risks(r.id):
            seen.setdefault((risk["kind"], risk["field"], risk["line"]), risk)
    out = []
    for (kind, field, line), risk in sorted(seen.items(), key=lambda kv: kv[0][2]):
        f = prog.fields[field]
        ex = {k: v for k, v in (risk["example"] or {}).items()}
        shown = ", ".join(f"{k.replace('#numeric', ' IS NUMERIC')}={v}" for k, v in ex.items()
                          if v is not None and k not in ("IN-ACCT",))
        what = (f"{field} (PIC {f.pic.text}) can receive a value with more integer digits than it holds; "
                f"COBOL drops the high-order digits without an error") if kind == "silent overflow" else (
                f"a negative value can be stored in unsigned {field} (PIC {f.pic.text}); it is kept without its sign")
        item = {"kind": kind, "lines": [line], "message": f"line {line}: {what}. Example found by z3: {shown}"}
        try:
            item["confirmed"] = confirm(prog, spec, ex, field, kind, line)
            item["message"] += f". Compiled program: {item['confirmed']}"
        except Exception as exc:  # confirmation is best effort
            item["confirmed"] = None
        out.append(item)
    return out


AMOUNTS = ["-00012345", "+99999999", "999999999", "-99999999", "000012345", "+00000001", "-00000001"]


def confirm(prog, spec, values, field, kind, line) -> str:
    """Find a concrete input near z3's example that makes the interpreter record this
    loss at this line, then show what the compiled program stores for it."""
    if prog.using:
        base = subprog.apply_values(prog, subprog.blank_params(prog), values)
        hit = None
        for amount in AMOUNTS:
            for start in (None, Decimal(0), Decimal("9999999.99"), Decimal(99999)):
                params = dict(base)
                for name, f in prog.fields.items():
                    if f.pic is not None and f.pic.kind == "num" and f.section == "linkage" and not f.dims                             and name.startswith("TRAN-UPDATE-NUM") and len(amount) == f.length:
                        buf = bytearray(params["TRANSACTION-RECORD"])
                        off = f.offset
                        buf[off:off + f.length] = amount.encode()
                        params["TRANSACTION-RECORD"] = bytes(buf)
                if start is not None:
                    params = subprog.apply_values(prog, params, {field: start})
                st = R.State(prog)
                bufs = {u: bytearray(b) for u, b in params.items()}
                for u, b in bufs.items():
                    st.bind(prog.fields[u].record, b)
                try:
                    st.run_program()
                except Exception:
                    continue
                if any(e[0] == kind and e[1] == line for e in st.events):
                    ev = next(e for e in st.events if e[0] == kind and e[1] == line)
                    hit = (params, ev)
                    break
            if hit:
                break
        if not hit:
            return "no concrete example found by the bounded search"
        params, ev = hit
        exe, lib = subprog.build(prog, spec["copy"])
        out = subprog.run(prog, exe, lib, [params])[0]
        after = R.State(prog)
        for u, text in zip(prog.using, out):
            after.bind(prog.fields[u].record, bytearray(text.encode("latin-1")))
        data = params["TRANSACTION-RECORD"].decode("latin-1")
        return (f"for update amount '{data[44:53]}' the computed value is {ev[3]}, "
                f"and the compiled program stores {field} = {after.text(field)}")
    group = next(rest[0].target.name for k, *rest in R.extract(prog)[1][0].steps if k == "accept")
    rec = impact.record_from_values(prog, group, values)
    out = run_batch(compile_program(spec["source"]), [rec])[0]
    return f"prints \"{out.strip()}\""


def main():
    out_dir = ROOT / "results" / "programs"
    out_dir.mkdir(parents=True, exist_ok=True)
    index = []
    for spec in PROGRAMS:
        t0 = time.perf_counter()
        prog = C.parse_program(spec["source"], spec["copy"])
        ex, raw = R.extract(prog)
        report, kept, analysis = smt.analyse(prog, raw, pairwise=not ex.modular)
        names = glossary.load(spec["source"])
        rules = []
        for r in kept:
            d = r.to_dict(prog, names)
            z = report["rules"][r.id]
            for i, c in enumerate(d["conditions"]):
                c["implied"] = i not in z["kept_index"]
            d["z3"] = {"removed": len(z["removed"]), "equivalent": z["equivalent"]}
            w = analysis.witness(r.id)
            if w:
                d["example"] = {k: ("(any other text)" if v is None else plain(v)) for k, v in w.items()}
            rules.append(d)
        found = lint.findings(prog, kept)
        found += value_risks(prog, kept, analysis, spec)
        verification = None
        vf = ROOT / "results" / spec["verification"]
        if vf.exists():
            v = json.loads(vf.read_text())
            sets = v.get("sets", {})
            total = sum(s.get("records", s.get("cases", 0)) for s in sets.values())
            agree = sum(s.get("agree", 0) for s in sets.values())
            verification = {"cases": total, "agree": agree, "sets": sets, "platform": v.get("platform"),
                            "kind": spec.get("kind", "rules")}
            for msg, n in v.get("runtime_findings", {}).items():
                line = re.match(r"line (\d+):", msg)
                found.append({"kind": "found while running", "lines": [int(line.group(1))] if line else [],
                              "message": f"{msg} ({n} of {v.get('runs', n)} runs)"})
        data = {
            "name": prog.name, "origin": spec["origin"], "path": str(Path(spec["source"]).relative_to(ROOT)),
            "lines": [{"no": n, "text": t} for n, t in prog.lines],
            "copybooks": sorted({f for f, _ in prog.locs.values()}),
            "paragraphs": len(prog.paragraphs), "fields": len(prog.fields),
            "diagnostics": prog.diagnostics, "using": prog.using,
            "mode": "per decision statement" if ex.modular else "whole program",
            "entry": ex.entry, "decision_fields": sorted(ex.dec),
            "raw_paths": len(raw), "rules": rules,
            "z3": {"infeasible": len(report["infeasible"]), "unknown": len(report["unknown"]),
                   "conditions_removed": sum(len(v["removed"]) for v in report["rules"].values()),
                   "all_proved": all(v["equivalent"] for v in report["rules"].values()),
                   "overlaps": report["overlaps"]},
            "findings": found, "verification": verification,
            "dmn": _dmn_info(prog.name),
            "seconds": round(time.perf_counter() - t0, 2),
        }
        (out_dir / f"{prog.name}.json").write_text(json.dumps(data, indent=1, default=str))
        index.append({k: data[k] for k in ("name", "origin", "path", "raw_paths", "mode", "z3", "seconds")}
                     | {"rules": len(rules), "findings": len(found),
                        "lines_of_code": sum(1 for _ in C.read_fixed(Path(spec["source"]).read_text(errors="replace"))),
                        "verified": verification and {"cases": verification["cases"], "agree": verification["agree"]}})
        print(prog.name, len(raw), "->", len(rules), "rules;", len(found), "findings;", data["z3"], f"{data['seconds']} s")
        for f in found:
            print("   ", f["kind"], "-", f["message"])
    (out_dir / "index.json").write_text(json.dumps(index, indent=1, default=str))


if __name__ == "__main__":
    main()
