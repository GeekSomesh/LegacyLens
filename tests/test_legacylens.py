"""Unit tests that need neither GnuCOBOL nor the data set (run anywhere: python -m pytest)."""
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from legacylens import cobol as C  # noqa: E402
from legacylens import explain, hybrid  # noqa: E402
from legacylens import rules as R  # noqa: E402
from legacylens.numeric import parse_pic, store  # noqa: E402

SOURCE = ROOT / "cobol" / "CREDREV.cbl"


def rec(acct, limit, bill, paid, delinq, late):
    return f"{acct:06d}{limit:07d}{bill:07d}{paid:07d}{delinq:01d}{late:01d}"


def test_pic_and_store():
    p = parse_pic("9(5)")
    assert (p.kind, p.digits, p.length) == ("num", 5, 5)
    assert store(p, Decimal("999999900")) == Decimal(99900)      # high-order truncation
    assert store(p, Decimal("12.9")) == Decimal(12)              # fraction truncated
    assert store(parse_pic("S9(3)V99"), Decimal("-1.239")) == Decimal("-1.23")
    assert store(parse_pic("X(4)"), "AB") == "AB  "


def test_parse_credrev():
    prog = C.parse_program(SOURCE)
    assert prog.diagnostics == []
    assert [p.name for p in prog.paragraphs] == ["MAIN-PARA", "REVIEW-ACCOUNT", "COMPUTE-RATIOS", "APPLY-POLICY"]
    assert prog.fields["IN-REC"].length == 29
    assert prog.conds["SEVERELY-LATE"].values == [(Decimal(3), Decimal(9))]


def test_rules_extracted():
    prog = C.parse_program(SOURCE)
    ex, rules = R.extract(prog)
    assert ex.entry == "REVIEW-ACCOUNT"
    assert sorted(ex.dec) == ["OUT-DECISION", "OUT-REASON"]
    assert [r.actions["OUT-REASON"] for r in rules] == ["R01", "R02", "R03", "R04", "R05", "R06"]
    assert rules[4].condition_texts()[-1] == "IN-LATE-6M = 0 AND WS-UTIL < 30 AND IN-LIMIT < WS-HIGH-LIMIT"


def test_engine_matches_expected_outputs():
    prog = C.parse_program(SOURCE)
    _, rules = R.extract(prog)
    eng = R.Engine(prog, rules)
    cases = {
        rec(1, 100000, 3000, 1, 0, 0): "000001 INCREASE R05 00003",
        rec(2, 50000, 48000, 0, 2, 0): "000002 REVIEW   R02 00096",
        rec(3, 100, 99999, 9000000, 0, 0): "000003 KEEP     R06 99999",
        rec(4, 0, 1000, 0, 0, 0): "000004 REVIEW   R04 00999",
        rec(5, 1, 9999999, 0, 0, 0): "000005 REVIEW   R04 99900",
        rec(6, 20000, 100, 0, 3, 5): "000006 BLOCK    R01 00000",
    }
    for record, expected in cases.items():
        rule, _, st, n = eng.decide(record)
        assert n == 1
        assert eng.output(rule, st) == [expected]


def test_parser_handles_common_forms(tmp_path):
    src = tmp_path / "T.cbl"
    body = """       IDENTIFICATION DIVISION.
       PROGRAM-ID. T.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01  A   PIC 9(3).
       01  B   PIC 9(3).
       01  OUT PIC X(4).
       PROCEDURE DIVISION.
       P1.
           ACCEPT A
           IF A IS GREATER THAN 10 AND NOT LESS THAN 20 OR A = 5
               MOVE 'HIGH' TO OUT
           ELSE
               EVALUATE A
                   WHEN 1 THRU 3
                       MOVE 'LOW' TO OUT
                   WHEN OTHER
                       MOVE 'MID' TO OUT
               END-EVALUATE
           END-IF
           ADD 1 TO B
           DISPLAY OUT
           STOP RUN.
"""
    src.write_text(body)
    prog = C.parse_program(src)
    assert prog.diagnostics == []
    _, rules = R.extract(prog)
    assert [r.actions["OUT"].strip() for r in rules] == ["HIGH", "LOW", "MID"]
    eng = R.Engine(prog, rules)
    assert eng.decide("025")[1]["OUT"] == "HIGH"
    assert eng.decide("005")[1]["OUT"] == "HIGH"
    assert eng.decide("002")[1]["OUT"] == "LOW"
    assert eng.decide("015")[1]["OUT"] == "MID"


def test_hybrid_never_approves_on_its_own():
    for policy in ("INCREASE", "KEEP", "REVIEW", "DECREASE", "BLOCK"):
        for band in ("LOW", "MEDIUM", "HIGH"):
            c = hybrid.combine(policy, band)
            if c["changed"]:
                assert c["final"] in ("HOLD", "REVIEW")


def test_guard_rejects_invented_numbers():
    facts = "Decision: HOLD. Legacy COBOL policy: INCREASE by RULE-05, lines 68 to 71. risk 21 percent."
    assert explain.guard("HOLD: rule RULE-05 at lines 68 to 71, risk 21%.", facts, "HOLD", "RULE-05") == []
    assert explain.guard("HOLD under RULE-05 because risk is 45%.", facts, "HOLD", "RULE-05")
    assert explain.guard("The increase was approved under RULE-05.", facts, "HOLD", "RULE-05")
    assert explain.guard("HOLD: risk 21%.", facts, "HOLD", "RULE-05")


def test_plain_english_conditions():
    from legacylens import glossary
    prog = C.parse_program(SOURCE)
    _, rules = R.extract(prog)
    names = glossary.load(SOURCE)
    assert glossary.rule_english(rules[0], names) == ["3 or more months past due"]
    assert glossary.rule_english(rules[4], names)[-1] == (
        "late months in the last six is 0 and utilisation in percent is less than 30 "
        "and credit limit is less than the 500,000 limit cap")
