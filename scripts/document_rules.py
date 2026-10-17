"""Business-language documentation for every extracted rule, written by IBM Granite
on LinuxONE from the rule's facts and checked by the same guard as explanations.

    python scripts/document_rules.py [llm_url]     # results/rule_docs.json
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from legacylens import cobol as C, explain, glossary, rules as R  # noqa: E402

SOURCE = ROOT / "cobol" / "CREDREV.cbl"
SYSTEM = ("You document legacy COBOL business rules for bank analysts. Turn the rule facts into one "
          "plain-English sentence that says when the rule applies and what it decides. Start with the rule id "
          "exactly as given. Keep the decision word in capitals, the reason code and every number exactly as "
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


def main():
    if len(sys.argv) > 1:
        explain.LLM_URL = sys.argv[1]
    prog = C.parse_program(SOURCE)
    _, rules = R.extract(prog)
    names = glossary.load(SOURCE)
    out = {"program": prog.name, "rules": {}}
    for rule in rules:
        decision = str(rule.actions["OUT-DECISION"]).strip()
        reason = str(rule.actions["OUT-REASON"]).strip()
        # A rule is one branch of an IF/EVALUATE chain: the tests that held pick the
        # branch, the tests that failed are the earlier branches, i.e. earlier rules.
        held = [glossary.english(c, names) for c, holds, _ in rule.conditions if holds]
        k = rules.index(rule)
        earlier = "" if k == 0 else (f", if no earlier rule ({rules[0].id}) applied" if k == 1 else
                                     f", if no earlier rule ({rules[0].id} to {rules[k - 1].id}) applied")
        lines = sorted({line for _, holds, line in rule.conditions if holds} | set(rule.action_lines))
        when = (f"{' and '.join(held)}{earlier}" if held else
                f"no earlier rule ({rules[0].id} to {rules[k - 1].id}) applies")
        facts = (f"{rule.id} decides {decision} with reason code {reason} (COBOL lines {lines[0]} to "
                 f"{lines[-1]}). It applies when {when}.")
        t0 = time.perf_counter()
        reply, model = explain.chat(SYSTEM, f"Rule facts: {facts}\n\nDocumentation:", max_tokens=90,
                                    examples=EXAMPLE)
        ms = round((time.perf_counter() - t0) * 1000)
        problems = explain.guard(reply or "", facts, decision, rule.id) if reply else ["LLM unavailable"]
        entry = {"facts": facts, "ms": ms, "model": model}
        if problems:
            entry.update(text=facts, source="template", rejected=reply, note="; ".join(problems))
        else:
            entry.update(text=reply, source="granite")
        out["rules"][rule.id] = entry
        print(rule.id, entry["source"], ms, "ms |", entry.get("note", ""), "|", reply)
    (ROOT / "results" / "rule_docs.json").write_text(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
