# LegacyLens

**Aviator_IBM Z Datathon 2026 Project** · Theme: Real-Time AI for Critical Decisions · Event code SSF2026

Team **Aviator**: Piyush Anand · Somesh Srivastava · Praman Patel · Paraj Prasad · Mohit Kumar A

Pitch deck: [docs/LegacyLens_pitch_deck.pdf](docs/LegacyLens_pitch_deck.pdf)

LegacyLens reads legacy COBOL, turns every decision path into a readable rule, **proves** the rules with z3 and **checks** them against the compiled program, shows exactly what a code change does before it goes live, exports the rules as standard DMN, and adds a real-time AI risk check with a plain-English explanation. It works on IBM's own sample programs as well as on two policy programs of ours, and its checks found real defects in all of them. Everything runs on an **IBM LinuxONE** virtual server (IBM z17, s390x) in the LinuxONE Community Cloud.

| At a glance (all measured on LinuxONE) | |
|---|---|
| Rules = compiled COBOL: credit policy CREDREV, fraud screen FRAUDHLD | **35,260 / 35,260** and **20,212 / 20,212** |
| Rules = compiled COBOL, IBM SAM2 (copybooks, REDEFINES, COMP-3, OCCURS) | **7,535 / 7,535** calls |
| Interpreter = compiled COBOL, IBM SAM1 with real files and a CALL to SAM2 | **300 / 300** file runs |
| Interpreter = GnuCOBOL on five construct programs | **10,000 / 10,000** records |
| z3 on SAM2 | 109 code paths → 92 proved impossible → **17 rules**, 44 redundant conditions removed, every simplification proved equivalent |
| Defects found, each with a reproducible example | IBM SAM1: **5** · IBM SAM2: a debit stored as a credit, balances silently losing high-order digits · our own programs: a utilisation and a spend ratio that wrap to small numbers |
| Change impact (CREDREV v1 → v2) | 6 changed-decision regions found by z3, **6/6 confirmed** by both compiled programs; 1,039 of 30,000 accounts change outcome, all inside a predicted region |
| DMN 1.3 export | schema-valid; the exported model matches the compiled COBOL on **35,262 / 35,262** (CREDREV) and **25,192 / 25,192** (FRAUDHLD) |
| Fault injection (bugs planted in the COBOL) | CREDREV: **72 of 72** behaviour-changing mutants caught (70 by the tests, 2 more by z3 with an example); IBM SAM2: **19 of 19** caught, 18 more proved harmless by z3 |
| AI on the credit policy (4,501 held-out accounts) | keeps **57%** of defaulters from getting a limit increase |
| Speed on the z17 | ONNX risk score **15 µs** on the AI accelerator; whole portfolio **125,000 decisions/s**; one API decision **4.8 ms** |

```
COBOL + copybooks ─► parse (byte-exact data layout) ─► rules: one per path or per decision
                                                        ├─► z3: prune, simplify, prove, find an example input
                                                        └─► check against the compiled program (GnuCOBOL on s390x)
account ─► policy decision (rule + COBOL lines)
        ─► AI default risk (ONNX model, Snap ML on the z17)
        ─► hybrid decision (AI can hold or escalate, never approve)
        ─► explanation (IBM Granite on LinuxONE, checked against the facts)
```

## The problem

Banks still make critical, high-volume decisions in COBOL: credit limits, card blocks, claims. The rules are buried in decades-old code. The people who wrote them are retiring, and changing them is risky because nobody can say exactly what they do.

Adding AI to these decisions makes this worse unless three things hold:
1. **We know what the legacy rules do.** Every decision traces to the lines of code that made it.
2. **We can prove it.** The extracted rules give exactly the same answers as the running program.
3. **The AI is fast, safe and explainable.** It scores in real time, it cannot approve anything on its own, and every decision comes with a reason a person can check.

## What LegacyLens does

| Step | How | Where in the repo |
|---|---|---|
| Read the COBOL | Fixed-format parser with copybooks (`COPY ... REPLACING`), FILE/WORKING-STORAGE/LINKAGE sections, PICTURE and USAGE (DISPLAY, COMP-3, COMP), REDEFINES, OCCURS, 88-levels, and the procedure statements listed in `cobol.py`. Every field gets its byte offset, so overlays behave as on the machine. Unsupported code is reported, never guessed | `legacylens/cobol.py`, `numeric.py` |
| Extract rules | Every path through the decision logic becomes a rule: the tests on the way (with lines) and the values written. Large batch programs get one small rule table per decision statement, with PERFORMed paragraphs shown as routing | `legacylens/rules.py` |
| Prove and simplify (z3) | Each rule path is executed symbolically. z3 drops impossible paths, removes conditions implied by the others and proves the shorter rule equivalent, checks that no two rules overlap, and finds a concrete example input for each rule | `legacylens/smt.py` |
| Check against the program | The rules (or, for whole batch programs, the interpreter) and the GnuCOBOL build process the same inputs; outputs are compared byte for byte. CALLed programs get a generated driver; batch programs run on real files | `verify.py`, `subprog.py`, `scripts/verify_*.py` |
| Find defects | z3 proves where a numeric store can silently lose its high-order digits or its sign, and where a division can be reached with a zero divisor, each with an example the compiled program confirms; static checks (a test of one file's status that reports another's, files never closed, unused paragraphs, a decision left unset on some path); and anything the interpreter sees while running (writes outside a table, arithmetic on text) | `legacylens/smt.py`, `lint.py` |
| Change impact | Compare two versions of a program: rules unchanged/changed/added, every region where the decision changes (z3, with an example), both compiled programs on the examples and on a whole portfolio | `legacylens/impact.py` |
| Export | The rules as a DMN 1.3 decision model (derived values as FEEL, a FIRST-hit decision table), validated against the OMG schema and run against the compiled program | `legacylens/dmn.py` |
| Review | Reviewers approve or flag each rule; every action goes into a SHA-256 hash-chained audit trail, and approvals go stale when the rule changes | `legacylens/review.py` |
| Score risk | A gradient-boosted model trained with scikit-learn on 30,000 real card accounts, exported to **ONNX**, served by **IBM Snap ML** on the z17's on-chip AI accelerator (NNPA, through zDNN) | `legacylens/model.py`, `models/risk_gbm.onnx` |
| Decide | Policy plus AI: hold a risky limit increase, escalate an account, set review priority, or send a restrictive decision for a second look. The AI never approves on its own | `legacylens/hybrid.py` |
| Explain | A factual draft note is built from the computed values and a glossary of COBOL names; **IBM Granite 3.3 2B** on LinuxONE rewrites it in plain English. A guard rejects any rewrite that adds a number, percentage or decision word not in the draft, or drops the decision or rule; the draft is shown instead. Granite also documents every rule | `legacylens/explain.py`, `glossary.py` |
| Serve | FastAPI service and web UI (decision desk, program explorer, change impact, evidence), a command line (`python -m legacylens rules / findings / dmn / verify / impact`), rootless podman container on s390x, systemd units, CycloneDX SBOM, GitHub Actions on x86 | `legacylens/api.py`, `__main__.py`, `Containerfile`, `deploy/` |

Programs analysed:
- `cobol/CREDREV.cbl`: a monthly credit line review written for this project in batch COBOL style (64 lines of code), and `CREDREV_v2.cbl`, a realistic change request to it.
- `cobol/FRAUDHLD.cbl`: a real-time card authorisation fraud screen written for this project, with a high-risk country table built through REDEFINES and OCCURS.
- `samples/ibm-zopeneditor/COBOL/SAM2.cbl`: IBM's customer-update subroutine from the [IBM Z Open Editor samples](https://github.com/IBM/zopeneditor-sample), with its copybooks (Apache-2.0, unmodified).
- `samples/ibm-zopeneditor/COBOL/SAM1.cbl`: IBM's batch program that reads a customer file and a transaction file, CALLs SAM2 and writes an updated file and a report.

## Architecture

![LegacyLens architecture](docs/architecture.png)

The same as a diagram source (renders on GitHub):

```mermaid
flowchart LR
  subgraph LinuxONE["IBM LinuxONE Community Cloud · z17 · Ubuntu 24.04 s390x"]
    SRC["COBOL + copybooks"] --> P["Parser · byte layout"]
    P --> X["Rule extractor"]
    X --> Z["z3 symbolic analysis"]
    SRC --> GC["GnuCOBOL build"]
    X --> RE["Rule engine / interpreter"]
    RE <-->|"byte-for-byte"| GC
    P --> L["Defect checks"]
    DATA[("UCI credit data")] --> TR["scikit-learn GBM"] --> ONNX[["risk_gbm.onnx"]]
    ONNX --> SNAP["Snap ML · zDNN · NNPA"]
    subgraph Container["podman container under systemd"]
      API["FastAPI + web UI"]
    end
    API --> RE
    API --> GC
    API --> SNAP
    API --> H["Hybrid policy"]
    API --> LLM["IBM Granite · llama.cpp, systemd service"]
  end
  USER["Review officer"] -->|"SSH tunnel / browser"| API
```

## How it uses IBM LinuxONE

- **On-chip AI accelerator.** The risk model is an ONNX file loaded by IBM Snap ML with `tree_format="zdnn_tensors"`, which runs on the z17 Neural Network Processing Assist through zDNN (`libzdnn0`). The same ONNX file also runs on Snap ML's CPU path and on the exact ONNX reference evaluator; all agree on every risk band.
- **COBOL where COBOL lives.** CREDREV, SAM1 and SAM2 are compiled with GnuCOBOL on s390x, and every rule is checked against those executables. The interpreter reproduces GnuCOBOL's byte formats on this machine: big-endian binary, packed decimal, zoned signs, variable-length record headers, print-file control bytes.
- **Formal verification on the box.** z3 (Ubuntu's s390x package) proves which code paths are impossible and that each simplified rule equals the original.
- **Generative AI on the same box.** IBM Granite runs in llama.cpp built for s390x vector extensions, with big-endian GGUF weights. No data leaves the server; there are no external API calls.
- **Platform.** The service is a rootless podman container started by systemd (Quadlet); Granite is a systemd user service. Only SSH is exposed; the UI is reached through an SSH tunnel.

Evidence: [docs/linuxone_system.txt](docs/linuxone_system.txt) (machine type 9175 = IBM z17, the `nnpa` CPU facility, Snap ML reporting `zdnn_tensors`) and the screenshots below.

| | |
|---|---|
| ![LinuxONE virtual server](docs/screenshots/01-linuxone-virtual-server.jpg) | ![A held limit increase](docs/screenshots/02-decision-hold.jpg) |
| The `coboldecision` server in the LinuxONE Community Cloud (AI_LAB flavor) | A decision: COBOL says INCREASE (RULE-05), AI risk is MEDIUM, so the increase is held; Granite explains |
| ![IBM SAM2 in the program explorer](docs/screenshots/03-cobol-rules.jpg) | ![IBM SAM1 findings](docs/screenshots/05-ibm-sam1-findings.jpg) |
| IBM's SAM2: 109 paths, 92 proved impossible, 17 rules; z3 finds silent overflows and sign loss, each example confirmed on the compiled program | IBM's SAM1: five findings, and the whole program matches the compiled build on 300 file runs |
| ![Rules documented by Granite, with review](docs/screenshots/06-rules-granite-review.jpg) | ![Change impact](docs/screenshots/07-change-impact.jpg) |
| Each rule documented by IBM Granite on the server (numbers and decision words checked); struck conditions are implied (z3 proof); Approve/Flag go to the hash-chained audit trail | Change impact: z3 finds the 6 regions where version 2 decides differently, both compiled programs confirm them, and the 200 accounts moved to a softer action had a 58% default rate |
| ![Evidence](docs/screenshots/04-evidence.jpg) | ![FRAUDHLD with its DMN export](docs/screenshots/08-fraudhld-dmn.jpg) |
| Evidence: IBM SAM2 and SAM1 checks, construct conformance, speed, fault injection, DMN | FRAUDHLD: 8 rules = compiled COBOL on 20,212 requests; the DMN model gives the same decisions on 25,192 records; z3 finds a silent overflow in WS-RATIO |

## Results (all measured on the LinuxONE server)

Numbers come from `results/*.json`, produced by the scripts in `scripts/`.

### 1. Rules and interpreter vs compiled COBOL

| Program | What was compared | Inputs | Same |
|---|---|---|---|
| CREDREV | extracted rules vs the compiled program, whole output line | 30,000 real accounts, 260 boundary cases, 5,000 random records | **35,260 / 35,260** |
| FRAUDHLD | extracted rules vs the compiled program, whole output line | 20,000 generated requests and 212 boundary cases | **20,212 / 20,212** |
| IBM SAM2 | extracted rules vs the compiled subroutine, all four parameters | IBM's sample data (12), z3 example inputs (17), a grid of codes/actions/fields/amounts (4,506), random (3,000) | **7,535 / 7,535** |
| IBM SAM1 | interpreter (calling its model of SAM2) vs the compiled program: updated customer file, report, console, return code | IBM's sample files + 300 generated file pairs; 1,607 transactions | **300 / 300** defined runs (1 run is undefined behaviour, below) |
| Five construct programs (`tests/cobol`) | interpreter vs compiled program, every DISPLAY line | 2,000 random records each | **10,000 / 10,000** |

The construct programs cover GO TO and PERFORM THRU; OCCURS and VARYING/TEST AFTER loops; REDEFINES, COMP-3, COMP and signed fields; ROUNDED and edited pictures (Z , . - CR $ * floating); 88 lists, SET TRUE, EVALUATE THRU, reference modification and INITIALIZE.

### 2. z3 symbolic analysis

| Program | Code paths | Impossible (proved) | Rules | Redundant conditions removed | Simplifications proved | Overlapping rules |
|---|---|---|---|---|---|---|
| CREDREV | 6 | 0 | 6 | 3 | all | none |
| FRAUDHLD | 8 | 0 | 8 | 2 | all | none |
| IBM SAM2 | 109 | 92 | **17** | 44 | all | none |
| IBM SAM1 (per decision) | 28 | 0 | 28 | 8 | all | – |

Each rule also gets an example input found by z3; for SAM2 those 17 inputs are part of the comparison above.

### 3. Defects found

| Program | Finding | How |
|---|---|---|
| IBM SAM1 | When reading the transaction file fails, the error message reports the **customer** file's status code (`MOVE WS-CUSTFILE-STATUS TO ERR-MSG-DATA2` in 710-READ-TRAN-FILE) | static check on the rules |
| IBM SAM1 | The output-file write error also reports the customer input file's status (740-WRITE-CUSTOUT-FILE) | static check |
| IBM SAM1 | CUSTOMER-FILE-OUT is opened but never closed | static check |
| IBM SAM1 | `PERFORM TEST AFTER VARYING I FROM 1 BY 1 UNTIL I > 12` runs once more with I = 13 and writes `WS-CUST-MONTH(13)`, nine bytes into the next field (every ADD transaction; GnuCOBOL does not check subscripts, and the interpreter reproduces the overwrite) | while running |
| IBM SAM1 | An UPDATE for a contact record ('C') reaches SAM2, which treats it as a customer record and adds to a numeric field that holds text | while running |
| IBM SAM2 | **A debit becomes a credit**: REPLACE BALANCE with -123.45 stores the unsigned balance as 0000123.45 (line 121; the same on ADD, line 124, and for the order count, lines 131 and 134) | z3, confirmed on the compiled program |
| IBM SAM2 | **Money disappears silently**: adding 999,999.99 to a balance of 9,999,999.99 gives 10,999,999.98, stored as 0999999.98 (line 124; the order count wraps the same way) | z3, confirmed on the compiled program |
| CREDREV (ours) | Utilisation over 99,999% wraps: a bill 1,000 times the limit reads as 0% (line 46) | z3, confirmed |
| FRAUDHLD (ours) | A spend 100,000 times the average makes the ratio wrap to a small number, so the spike looks normal to the screen (line 68) | z3, confirmed |

### 4. Change impact: CREDREV v1 → v2

`cobol/CREDREV_v2.cbl` is a realistic change request: review accounts at 85% utilisation instead of 90%, lower the high-limit cap from 500,000 to 400,000, and add a rule R07 (no payment on a large bill). LegacyLens compares the two versions (`results/impact_credrev.json`, UI tab "Change impact"):

| | |
|---|---|
| Rules | 1 unchanged, 5 with changed tests, 1 added |
| Regions where the outcome changes (z3) | 6, each with an example account; **6 / 6 confirmed** by running both compiled programs |
| Accounts whose outcome changes (both compiled programs on all 30,000) | **1,039** (960 change decision, 79 only the reason), every one inside a region z3 predicted |

| From → to | Accounts | Defaulted next month |
|---|---|---|
| INCREASE (R05) → KEEP (R06) | 706 | 61 (8.6%) |
| **DECREASE (R03) → REVIEW (R02)** | **200** | **116 (58.0%)** |
| REVIEW (R04) → REVIEW (R02) | 79 | 50 (63.3%) |
| KEEP (R06) → REVIEW (R07) | 44 | 14 (31.8%) |
| KEEP (R06) → REVIEW (R02) | 10 | 7 (70.0%) |

The change looks like a tightening, but it also moves 200 accounts from DECREASE to a softer REVIEW, and 58% of them defaulted the next month: exactly the kind of side effect a change board needs to see before a critical policy goes live.

### 5. DMN export

| Program | Table rows | OMG DMN 1.3 schema | Exported model = compiled COBOL |
|---|---|---|---|
| CREDREV | 19 | valid | **35,262 / 35,262** records |
| FRAUDHLD | 26 | valid | **25,192 / 25,192** records |

The models are in `results/dmn/` and downloadable from the program explorer. Derived values are FEEL literal expressions that keep COBOL's truncation; the country table loop is unrolled into single-assignment decisions. The comparison runs the exported XML through a FEEL-subset evaluator and cuts the decision fields out of the compiled program's output line.

### 6. Fault injection

Small bugs were planted in the PROCEDURE DIVISION, one at a time: a comparison operator, AND/OR, a numeric or text literal, an arithmetic sign, or a removed MOVE (`scripts/mutation.py`, `results/mutation.json`). For each mutant that compiles, LegacyLens checks whether the rules extracted from the *original* program still match the compiled *mutant* (the drift a code change causes), and runs the z3 change-impact analysis between the two.

| Program | Mutants | Compiled | Caught by the tests | Caught by z3 only (with an example) | Proved harmless by z3 | Missed |
|---|---|---|---|---|---|---|
| CREDREV | 80 | 74 | 70 | 2 | 2 | **0** |
| IBM SAM2 | 50 | 37 | 19 | 0 | 18 | **0** |

The two CREDREV bugs the tests missed are boundary changes (`500000` → `499999`, `<` → `<=`) that only one exact value reveals; z3 finds them and gives that value. The 18 harmless SAM2 mutants change values SAM2 computes but never returns (its CPU-burning CRUNCH loop). The mutants that do not compile are mostly edits to paragraph names and level numbers.

Re-extracting the rules from each compiled mutant reproduces it exactly in 62 of 74 (CREDREV) and 35 of 37 (SAM2) cases. Every other case is one where the mutant's output no longer follows from the current record: a removed MOVE leaves a decision unset, so the result depends on the previous record (flagged by the history check); a division can be reached with a zero divisor; or the mutant sends inputs down a path that reads a numeric field holding text (undefined in COBOL). LegacyLens reports these instead of guessing.

### 7. Risk model (4,501 held-out test accounts)

| Metric | Value |
|---|---|
| ROC AUC | 0.777 |
| Average precision | 0.547 |
| High band (risk ≥ 33%): precision / recall | 57.2% / 52.1% |
| Test default rate | 22.1% |

| Runtime (same ONNX file) | One account | Risk-band agreement with exact ONNX |
|---|---|---|
| Snap ML on the AI accelerator (NNPA) | 0.015 ms | 100% |
| Snap ML on the CPU | 0.008 ms | 100% |
| ONNX reference evaluator (exact) | 2.6 ms | reference |
| scikit-learn | 0.136 ms | – |

### 8. Policy alone vs policy + AI (same test accounts)

| | Accounts | Defaulted next month |
|---|---|---|
| Policy grants a limit increase | 1,500 | 175 (11.7%) |
| Hybrid grants it (AI risk low) | 952 | 75 (7.9%) |
| Hybrid holds it for review | 548 | 100 (18.2%) |
| Early warnings (policy KEEP → review, AI risk high) | 250 | 122 (48.8%) |
| Review queue: urgent / normal / low priority | 219 / 537 / 232 | 54.8% / 15.5% / 15.1% |

The hybrid keeps **57% of the defaulters** among limit increases from getting one, and flags 250 accounts the policy would have left unchanged, half of which defaulted. The combination table was chosen on the validation accounts; these are test numbers.

### 9. Explanations

IBM Granite 3.3 2B Instruct (Q4_0, big-endian) on the server's 3 vCPUs, no GPU. Eleven demo accounts, two per scenario (`results/llm_bench.json`):

| | |
|---|---|
| Rewrites accepted by the guard | 9 of 11 |
| Rejected, draft note shown instead | 2 (both said the program would "keep" an account it sent to REVIEW) |
| Median time per explanation | 21 s (about 5 tokens/s) |
| Rule documentation accepted | 6 of 6 (`results/rule_docs.json`) |

The faster Granite 3.0 1B-A400M (about 4 s) passed the guard on only 1 of 11, so LegacyLens uses the 2B model. The decision appears at once; the explanation follows, and demo accounts are explained in the background at start-up. The UI always shows the facts the explanation was written from.

### 10. Speed on the z17 (`results/benchmark.json`)

| Step | Throughput | One decision |
|---|---|---|
| Compiled COBOL policy, batch of 30,000 accounts | 658,779 / s | – |
| ONNX risk model, Snap ML on the AI accelerator | 180,603 / s | 15 µs (p99 20 µs) |
| ONNX risk model, Snap ML on the CPU | 934,523 / s | 8 µs |
| Hybrid decision for the whole portfolio | 124,957 / s | – |
| One account through the web service (rules, live COBOL check, ONNX risk, factors, hybrid) | – | 4.8 ms (p95 6.9 ms) |

On this virtual server Snap ML's CPU path (z17 vector instructions) is faster than its NNPA path for tree models of every size we tried (200 to 1,000 trees; 4 to 5 times); both give the same decisions. The service uses the accelerator, and we report both.

## Run it on LinuxONE (Ubuntu 24.04, s390x)

```bash
sudo apt-get install -y gnucobol3 libzdnn0 python3-venv python3-sklearn python3-onnx python3-pandas \
     python3-numpy python3-z3 python3-pytest podman cmake build-essential
python3 -m venv --system-site-packages ~/venv && ~/venv/bin/pip install -r requirements.txt
mkdir -p ~/data && cd ~/data && curl -LO "https://archive.ics.uci.edu/static/public/350/default+of+credit+card+clients.zip" && unzip default+of+credit+card+clients.zip && cd -

~/venv/bin/python scripts/verify_rules.py      # CREDREV rules vs compiled COBOL  -> results/verification.json
~/venv/bin/python scripts/verify_ibm.py        # IBM SAM2 rules vs compiled COBOL -> results/ibm_sam2.json
~/venv/bin/python scripts/verify_sam1.py       # IBM SAM1 end to end on files     -> results/ibm_sam1.json
~/venv/bin/python scripts/conformance.py       # construct programs               -> results/conformance.json
~/venv/bin/python scripts/verify_fraud.py      # FRAUDHLD rules vs compiled COBOL -> results/fraud_verification.json
~/venv/bin/python scripts/impact_credrev.py    # CREDREV v1 -> v2 change impact   -> results/impact_credrev.json
~/venv/bin/python scripts/export_dmn.py        # DMN models + schema + check      -> results/dmn/
~/venv/bin/python scripts/mutation.py          # fault injection                  -> results/mutation.json
~/venv/bin/python scripts/analyse_programs.py  # rules, z3, examples, findings    -> results/programs/
~/venv/bin/python scripts/train_model.py       # ONNX model + runtimes            -> models/, results/model.json
~/venv/bin/python scripts/evaluate_hybrid.py   # policy vs hybrid                 -> results/hybrid.json
~/venv/bin/python scripts/benchmark.py         # speed                            -> results/benchmark.json
~/venv/bin/python -m pytest tests              # unit tests
~/venv/bin/python scripts/sbom.py              # CycloneDX SBOM                   -> sbom.cdx.json
```

The command line works on any COBOL file in the supported subset:
```bash
python -m legacylens rules    samples/ibm-zopeneditor/COBOL/SAM2.cbl -I samples/ibm-zopeneditor/COPYBOOK
python -m legacylens findings samples/ibm-zopeneditor/COBOL/SAM1.cbl -I samples/ibm-zopeneditor/COPYBOOK
python -m legacylens impact   cobol/CREDREV.cbl cobol/CREDREV_v2.cbl
python -m legacylens dmn      cobol/FRAUDHLD.cbl -o FRAUDHLD.dmn
```

The explanation model (llama.cpp for s390x and a big-endian Granite GGUF):
```bash
git clone --depth 1 https://github.com/ggml-org/llama.cpp && cd llama.cpp
cmake -B build -DCMAKE_BUILD_TYPE=Release -DLLAMA_CURL=OFF -DGGML_NATIVE=OFF -DGGML_VXE=ON \
      -DCMAKE_C_FLAGS="-march=z16 -mtune=z16" -DCMAKE_CXX_FLAGS="-march=z16 -mtune=z16"
cmake --build build -j3 --target llama-server
curl -LO https://huggingface.co/taronaeo/Granite-3.3-2B-Instruct-BE-GGUF/resolve/main/granite-3.3-2b-instruct-be.Q4_0.gguf
./build/bin/llama-server -m granite-3.3-2b-instruct-be.Q4_0.gguf --host 127.0.0.1 --port 8080 -c 4096 -t 3
python scripts/document_rules.py     # Granite documentation for every rule -> results/rule_docs.json
```
(Ubuntu 24.04's GCC 13 does not know the z17 `-march=arch15`, so llama.cpp is built for z16, which runs on z17.)

The service, as a container managed by systemd (rootless podman, Quadlet):
```bash
cp ~/data/"default of credit card clients.xls" ~/data/uci_credit.xls && chmod 644 ~/data/uci_credit.xls
podman build -t legacylens -f Containerfile .
mkdir -p ~/.config/systemd/user ~/.config/containers/systemd
cp deploy/granite.service ~/.config/systemd/user/ && cp deploy/legacylens.container ~/.config/containers/systemd/
systemctl --user daemon-reload && systemctl --user enable --now granite && systemctl --user start legacylens
sudo loginctl enable-linger $USER
ssh -L 8800:127.0.0.1:8000 linux1@<server>     # from your laptop, then open http://localhost:8800
```

## Responsible AI

- **Behaviour only.** The model uses credit limit, repayment status, statements and payments. Sex, education, marital status and age are left out.
- **The AI cannot approve.** It can hold, escalate or ask for a second look. Approvals come only from the legacy policy, and a person reviews every change the AI makes.
- **Traceable.** Every decision names the COBOL rule and its line numbers, the risk band and the features that moved the score most.
- **Checked wording.** The LLM only rewrites a note whose facts were computed by the rules and the model. Its rewrite is rejected if it adds a number, a percentage or a decision word that is not in the note, or leaves out the decision or the rule; then the note itself is shown.
- **Local.** Data, models and the LLM stay on the LinuxONE server.

## Limitations

- The parser covers the COBOL listed in `legacylens/cobol.py` (no CICS/DB2 EXEC blocks, STRING/UNSTRING/INSPECT/SEARCH, SORT, indexed files, EVALUATE ALSO, OCCURS DEPENDING ON beyond its maximum). Anything else is reported as unsupported, never guessed.
- Agreement on tens of thousands of inputs is strong evidence, not a proof. The z3 results are proofs within the encoding, which treats writes through overlays, loops, CALLs and file input as unknowns. Infeasible, implied and equivalent are proved; a kept rule or condition may still be redundant.
- SAM1's rules are per decision statement, so they are documentation and routing. The whole program is checked through the interpreter, not by replaying its rules.
- Rule-level behavioural checks replay each input from the program's initial state; SAM1's run checks state carried across records.
- `CREDREV.cbl` was written for this project. The data set (Taiwan, 2005) is public and old; the model demonstrates the pipeline and is not a credit model to deploy.
- The DMN check uses LegacyLens's own FEEL-subset evaluator: the exported files are schema-valid, but they were not run in a third-party DMN engine. Programs whose derived values need byte overlays (such as SAM2) are not exported.
- Fault injection covers single-point mutations of comparison operators, connectors, literals, arithmetic and statements; it does not prove that every possible bug is caught.
- Granite's rewrite can still read awkwardly. The guard catches added numbers and decision words, not every wrong phrasing.

## Open-source resources

| Resource | Use | Licence |
|---|---|---|
| [IBM Z Open Editor samples](https://github.com/IBM/zopeneditor-sample) (SAM1, SAM2, copybooks, sample data) | real COBOL to analyse and verify, bundled unmodified in `samples/ibm-zopeneditor` | Apache-2.0 |
| [UCI Default of Credit Card Clients](https://doi.org/10.24432/C55S3H) (Yeh & Lien, 2009) | training and test data | CC BY 4.0 |
| [GnuCOBOL](https://gnucobol.sourceforge.io/) 3.1.2 | compiles and runs the COBOL on s390x | GPL-3.0 |
| [z3](https://github.com/Z3Prover/z3) 4.8.12 (Ubuntu package) | symbolic analysis and proofs | MIT |
| [OMG DMN 1.3 schemas](https://www.omg.org/spec/DMN/1.3/) (`legacylens/schemas`) | validating the exported decision models | OMG |
| [IBM Snap ML](https://snapml.readthedocs.io/) 1.17.2 | ONNX import and inference on the AI accelerator | IBM licence (PyPI) |
| [zDNN](https://github.com/IBM/zDNN) (libzdnn0) | NNPA access for Snap ML | Apache-2.0 |
| [scikit-learn](https://scikit-learn.org/), [skl2onnx](https://github.com/onnx/sklearn-onnx), [ONNX](https://onnx.ai/) | training, export, exact reference evaluation | BSD-3 / Apache-2.0 |
| [llama.cpp](https://github.com/ggml-org/llama.cpp) | LLM inference on s390x | MIT |
| [IBM Granite 3.3 2B Instruct](https://huggingface.co/ibm-granite/granite-3.3-2b-instruct), big-endian GGUF from the [s390x verified models](https://huggingface.co/collections/taronaeo/s390x-verified-models-672765393af438d0ccb72a08) listed in llama.cpp's s390x guide | explanations and rule documentation | Apache-2.0 |
| [FastAPI](https://fastapi.tiangolo.com/), [uvicorn](https://www.uvicorn.org/), [pydantic](https://docs.pydantic.dev/), [pandas](https://pandas.pydata.org/), [NumPy](https://numpy.org/), [xlrd](https://github.com/python-excel/xlrd) | service, API models and data handling (xlrd reads the UCI `.xls` file) | MIT / BSD |
| [lxml](https://lxml.de/) | XML Schema validation of the DMN exports | BSD-3 |
| [SQLite](https://sqlite.org/) (Python `sqlite3`) | review audit trail | public domain |
| [pytest](https://pytest.org/) | test suite on LinuxONE and in GitHub Actions | MIT |
| [CycloneDX](https://cyclonedx.org/) | format of the software bill of materials (`sbom.cdx.json`) | Apache-2.0 |
| [Python](https://www.python.org/) 3.12 | language runtime | PSF |
| [podman](https://podman.io/), [systemd](https://systemd.io/), [Ubuntu](https://ubuntu.com/) 24.04 | container, services and OS | Apache-2.0 / LGPL-2.1 / various |

Used only to produce the demo video and pitch deck (not part of the application): [Playwright](https://playwright.dev/) (Apache-2.0) to capture the live UI, [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) voice (Apache-2.0) through [kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx) (MIT), [FFmpeg](https://ffmpeg.org/) (LGPL/GPL), [Pillow](https://python-pillow.org/) (MIT-CMU), [soundfile](https://github.com/bastibe/python-soundfile) (BSD-3), [IBM Plex](https://github.com/IBM/plex) fonts (OFL-1.1).

## How this was built

Before the Datathon, the team built an earlier prototype on a related idea on a different server, using access credentials that were not the ones provided for this competition. Because the rules require the submission to be created during the event on the LinuxONE Community Cloud, that prototype is not part of this submission.

Everything in this repository was built from scratch on 17 and 18 October 2026 on the LinuxONE Community Cloud virtual server `coboldecision` (IBM z17, Ubuntu 24.04 s390x), accessed with the credentials that match our competition registration. No code, data or results from the earlier prototype are used. The git history records each step, and every result in this README was produced by running the code on that server ([docs/linuxone_system.txt](docs/linuxone_system.txt)).

Third-party material is limited to the open-source software, IBM samples and public data listed under [Open-source resources](#open-source-resources), each used under its licence.

## Licence

Apache-2.0 (see `LICENSE`). The IBM samples in `samples/ibm-zopeneditor` keep their own Apache-2.0 licence and notices.
