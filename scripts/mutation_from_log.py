"""Rebuild the SAM2 part of results/mutation.json from the run's log (used once,
when the run finished but its final summary step failed).

    python scripts/mutation_from_log.py /tmp/mutation.log
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from mutation import summary  # noqa: E402

LINE = re.compile(r"^SAM2\s+(\d+)\s+(\S+(?: not compile)?)\s+(.*?) \| (?:re-extracted (\d+)/(\d+)|never ends)$")


def main():
    rows = {}
    for line in Path(sys.argv[1]).read_text().splitlines():
        m = LINE.match(line)
        if not m:
            continue
        k, status, desc, agree, n = m.groups()
        row = {"mutant": desc, "status": status}
        if agree:
            row.update(re_extracted_agree=int(agree), inputs=int(n))
        else:
            row["note"] = "never ends"
        if status == "missed":
            row["z3_regions"] = 1          # 'missed' means z3 found a region the tests did not reach
        rows[int(k)] = row
    f = ROOT / "results" / "mutation.json"
    d = json.loads(f.read_text())
    d["sam2"] = [rows[k] for k in sorted(rows)]
    d["summary"] = {k: summary(d[k]) for k in ("credrev", "sam2")}
    f.write_text(json.dumps(d, indent=2))
    print(len(rows), "SAM2 rows;", d["summary"])


if __name__ == "__main__":
    main()
