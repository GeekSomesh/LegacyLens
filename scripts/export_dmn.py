"""Export rule sets as DMN 1.3, validate them against the OMG schema, and run the
exported model against the compiled COBOL program.

    python scripts/export_dmn.py      # results/dmn/<PROGRAM>.dmn, results/dmn.json
"""
import json
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from legacylens import cobol as C, data, dmn, rules as R, smt  # noqa: E402
from legacylens.runner import compile_program, run_batch  # noqa: E402
from legacylens.verify import boundary_records, random_records  # noqa: E402

SCHEMAS = ROOT / "legacylens" / "schemas"


def validate(xml_text: str):
    try:
        from lxml import etree
    except ImportError:
        return None, "lxml not installed"
    xsd = SCHEMAS / "DMN13.xsd"
    if not xsd.exists():
        return None, "schema not available"
    schema = etree.XMLSchema(etree.parse(str(xsd)))
    doc = etree.fromstring(xml_text.encode())
    ok = schema.validate(doc)
    return ok, "" if ok else str(schema.error_log.last_error)


def inputs_for(prog, group, record: str) -> dict:
    st = R.State(prog)
    st.set(C.Ref(group), record)
    env = {}
    for k in R.descendants(prog, group):
        f = prog.fields[k]
        if f.pic is not None:
            v = st.get(C.Ref(k))
            env[dmn.feel_name(k)] = v if isinstance(v, Decimal) else str(v).rstrip()
    return env


def display_fields(prog, rules, line: str) -> dict:
    """Cut a compiled program's DISPLAY line into the displayed fields by their widths."""
    disp = next(rest[0] for kind, *rest in rules[0].steps if kind == "display")
    out, pos = {}, 0
    for item in disp.items:
        if isinstance(item, C.Ref):
            f = prog.fields[item.name]
            w = f.pic.length + (1 if f.pic.kind == "num" and f.pic.signed else 0) +                 (1 if f.pic.kind == "num" and f.pic.scale else 0)
            out[item.name] = line[pos:pos + w]
            pos += w
        else:
            pos += len(item.value) if isinstance(item.value, str) else len(item.text)
    return out


def main():
    import numpy as np
    out_dir = ROOT / "results" / "dmn"
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {}
    for path in [ROOT / "cobol" / "CREDREV.cbl"] + sorted((ROOT / "cobol").glob("*.cbl")):
        if path.stem in report or path.stem.endswith("_v2"):
            continue
        prog = C.parse_program(path)
        ex, raw = R.extract(prog)
        z, rules, _ = smt.analyse(prog, raw, pairwise=False)
        kept = {rid: v["kept_index"] for rid, v in z["rules"].items()}
        try:
            xml = dmn.export(prog, rules, kept)
        except dmn.Unexportable as exc:
            report[path.stem] = {"exported": False, "reason": str(exc)}
            continue
        (out_dir / f"{path.stem}.dmn").write_text(xml)
        valid, why = validate(xml)
        group = next(rest[0].target.name for kind, *rest in rules[0].steps if kind == "accept")
        rng = np.random.default_rng(4)
        if path.stem == "CREDREV":
            recs = [data.to_record(r) for _, r in data.load().iterrows()]
        elif path.stem == "FRAUDHLD":
            import random as _r
            sys.path.insert(0, str(ROOT / "scripts"))
            from verify_fraud import request
            g = _r.Random(21)
            recs = [request(g, i) for i in range(20000)]
        else:
            recs = []
        recs += boundary_records(prog, rules, recs or random_records(prog, rules, 200, rng), rng)
        recs += random_records(prog, rules, 5000, rng)
        cobol = run_batch(compile_program(path), recs)
        dec = sorted({k for r in rules for k in r.actions})
        agree = 0
        first = None
        for rec, line in zip(recs, cobol):
            got = dmn.evaluate(xml, inputs_for(prog, group, rec))
            shown = display_fields(prog, rules, line)
            want = {dmn.feel_name(k): shown[k].strip() for k in dec}
            got_s = {k: str(v).strip() for k, v in got.items()}
            if got_s == want:
                agree += 1
            elif first is None:
                first = {"record": rec, "dmn": got_s, "cobol": line}
        table_rows = xml.count("<rule ")
        report[path.stem] = {"exported": True, "schema_valid": valid, "schema_note": why, "rows": table_rows,
                             "rules": len(rules), "records": len(recs), "agree_with_compiled_cobol": agree,
                             "first_difference": first, "file": f"results/dmn/{path.stem}.dmn"}
        print(path.stem, report[path.stem])
    (ROOT / "results" / "dmn.json").write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
