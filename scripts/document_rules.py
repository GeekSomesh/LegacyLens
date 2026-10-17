"""Business-language documentation for every extracted rule of every program, written
by IBM Granite on LinuxONE from the rule's facts and checked by the explanation guard.

    python scripts/document_rules.py [llm_url]     # results/rule_docs/<PROGRAM>.json
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from legacylens import cobol as C, explain, glossary, rules as R, smt  # noqa: E402

IBM = ROOT / "samples" / "ibm-zopeneditor"
PROGRAMS = [(ROOT / "cobol" / "CREDREV.cbl", []), (ROOT / "cobol" / "FRAUDHLD.cbl", []),
            (IBM / "COBOL" / "SAM2.cbl", [IBM / "COPYBOOK"]), (IBM / "COBOL" / "SAM1.cbl", [IBM / "COPYBOOK"])]
SYSTEM = ("You document legacy COBOL business rules for analysts. Turn the rule facts into one "
          "plain-English sentence that says when the rule applies and what it does. Start with the rule id "
          "exactly as given. Keep decision words in capitals, codes, messages and every number exactly as "
          "written. Add nothing else.")
EXAMPLE = [
    {"role": "user", "content": (
        "Rule facts: RULE-07 decides HOLD with reason code R07 (COBOL lines 80 to 82). It applies when "
        "the balance is more than 5,000 and months past due is at least 2, if no earlier rule "
        "(RULE-01 to RULE-06) applied.\n\nDocumentation:")},
    {"role": "assistant", "content": (
        "RULE-07 puts the account on HOLD (reason R07, lines 80 to 82) when its balance is over 5,000 "
        "and it is at least 2 months past due, unless one of RULE-01 to RULE-06 already applied.")},
]
DECISION_FIELDS = ("OUT-DECISION", "OUT-ACTION")
REASON_FIELDS = ("OUT-REASON",)


def what_it_does(rule, names) -> tuple:
    acts = {k: str(v).strip() for k, v in rule.actions.items()}
    decision = next((acts[k] for k in DECISION_FIELDS if k in acts), "")
    reason = next((acts[k] for k in REASON_FIELDS if k in acts), "")
    if decision:
        text = f"decides {decision}" + (f" with reason code {reason}" if reason else "")
        return text, decision
    parts = []
    for k, v in acts.items():
        if k == R.PERFORM_ACTION:
            parts.append(f"routes to {v}")
        elif k == "CALLS":
            parts.append(f"calls {v}")
        elif v.startswith("<"):
            parts.append(f"updates {glossary.name_of(names.get(k), k)} ({v.strip('<>')})")
        else:
            shown = "blank" if v == "SPACES" else f"'{v}'"
            parts.append(f"sets {glossary.name_of(names.get(k), k)} to {shown}")
    return ", ".join(parts), ""


def main():
    if len(sys.argv) > 1:
        explain.LLM_URL = sys.argv[1]
    out_dir = ROOT / "results" / "rule_docs"
    out_dir.mkdir(parents=True, exist_ok=True)
    for path, dirs in PROGRAMS:
        prog = C.parse_program(path, dirs)
        ex, raw = R.extract(prog)
        report, rules, _ = smt.analyse(prog, raw, pairwise=False)
        names = glossary.load(path)
        out = {"program": prog.name, "rules": {}}
        for k, rule in enumerate(rules):
            keep = report["rules"][rule.id]["kept_index"]
            conds = [glossary.english(c if h else R.negate(c), names) for i, (c, h, _) in enumerate(rule.conditions)
                     if i in keep]
            does, decision = what_it_does(rule, names)
            lines = rule.lines
            when = " and ".join(conds) if conds else "no earlier rule applies"
            facts = (f"{rule.id} {does} (COBOL lines {lines[0]} to {lines[-1]}). It applies when {when}.")
            t0 = time.perf_counter()
            reply, model = explain.chat(SYSTEM, f"Rule facts: {facts}\n\nDocumentation:", max_tokens=110,
                                        examples=EXAMPLE)
            ms = round((time.perf_counter() - t0) * 1000)
            problems = explain.guard(reply or "", facts, decision, rule.id) if reply else ["LLM unavailable"]
            entry = {"facts": facts, "ms": ms, "model": model}
            if problems:
                entry.update(text=facts, source="template", rejected=reply, note="; ".join(problems))
            else:
                entry.update(text=reply, source="granite")
            out["rules"][rule.id] = entry
            print(prog.name, rule.id, entry["source"], ms, "ms |", entry.get("note", ""), "|", (reply or "")[:160],
                  flush=True)
        (out_dir / f"{prog.name}.json").write_text(json.dumps(out, indent=2))
        if prog.name == "CREDREV":
            (ROOT / "results" / "rule_docs.json").write_text(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
