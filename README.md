# LegacyLens

**[TEAM NAME_IBM Z Datathon 2026 Project]** · Theme: Real-Time AI for Critical Decisions · Event code SSF2026

LegacyLens reads legacy COBOL, turns every decision path into a readable rule, **proves** the rules (z3) and **checks** them against the compiled program, then adds a real-time AI risk check and a plain-English explanation. It works on IBM's own sample programs as well as on our credit policy, and it found five defects in IBM's sample along the way. Everything runs on an **IBM LinuxONE** virtual server (IBM z17, s390x) in the LinuxONE Community Cloud.

| At a glance (all measured on LinuxONE) | |
|---|---|
| Rules = compiled COBOL, credit policy CREDREV | **35,260 / 35,260** records |
| Rules = compiled COBOL, IBM SAM2 (copybooks, REDEFINES, COMP-3, OCCURS) | **7,535 / 7,535** calls |
| Interpreter = compiled COBOL, IBM SAM1 with real files and a CALL to SAM2 | **300 / 300** file runs |
| Interpreter = GnuCOBOL on five construct programs | **10,000 / 10,000** records |
| z3 on SAM2 | 109 code paths → 92 proved impossible → **17 rules**, 44 redundant conditions removed, every simplification proved equivalent |
| Defects found in IBM's SAM1 sample | **5** (wrong status code reported twice, file never closed, write past a table, arithmetic on text) |
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
| Find defects | Static checks (a test of one file's status that reports another's, files never closed, unused paragraphs) and anything the interpreter sees while running (writes outside a table, arithmetic on text) | `legacylens/lint.py` |
| Score risk | A gradient-boosted model trained with scikit-learn on 30,000 real card accounts, exported to **ONNX**, served by **IBM Snap ML** on the z17's on-chip AI accelerator (NNPA, through zDNN) | `legacylens/model.py`, `models/risk_gbm.onnx` |
| Decide | Policy plus AI: hold a risky limit increase, escalate an account, set review priority, or send a restrictive decision for a second look. The AI never approves on its own | `legacylens/hybrid.py` |
| Explain | A factual draft note is built from the computed values and a glossary of COBOL names; **IBM Granite 3.3 2B** on LinuxONE rewrites it in plain English. A guard rejects any rewrite that adds a number, percentage or decision word not in the draft, or drops the decision or rule; the draft is shown instead. Granite also documents every rule | `legacylens/explain.py`, `glossary.py` |
| Serve | FastAPI service and web UI (decision desk, program explorer, evidence), rootless podman container on s390x, systemd units | `legacylens/api.py`, `Containerfile`, `deploy/` |

Programs analysed:
- `cobol/CREDREV.cbl`: a monthly credit line review written for this project in batch COBOL style (64 lines of code).
- `samples/ibm-zopeneditor/COBOL/SAM2.cbl`: IBM's customer-update subroutine from the [IBM Z Open Editor samples](https://github.com/IBM/zopeneditor-sample), with its copybooks (Apache-2.0, unmodified).
- `samples/ibm-zopeneditor/COBOL/SAM1.cbl`: IBM's batch program that reads a customer file and a transaction file, CALLs SAM2 and writes an updated file and a report.

## Architecture

```mermaid
flowchart LR
  subgraph LinuxONE["IBM LinuxONE Community Cloud · z17 · Ubuntu 24.04 s390x"]
    SRC[COBOL + copybooks] --> P[Parser · byte layout]
    P --> X[Rule extractor]
    X --> Z[z3 symbolic analysis]
    SRC --> GC[GnuCOBOL build]
    X --> RE[Rule engine / interpreter]
    RE <-->|byte-for-byte| GC
    P --> L[Defect checks]
    DATA[(UCI credit data)] --> TR[scikit-learn GBM] --> ONNX[[risk_gbm.onnx]]
    ONNX --> SNAP[Snap ML · zDNN · NNPA]
    subgraph Container["podman container (systemd)"]
      API[FastAPI + web UI]
    end
    API --> RE
    API --> GC
    API --> SNAP
    API --> H[Hybrid policy]
    API --> LLM[IBM Granite · llama.cpp (systemd)]
  end
  USER[Review officer] -->|SSH tunnel / browser| API
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
| ![Program explorer](docs/screenshots/03-cobol-rules.jpg) | ![Evidence](docs/screenshots/04-evidence.jpg) |
| Rules with their lines, z3 results and Granite's documentation | Rules vs compiled COBOL, model metrics, hybrid impact |

## Results (all measured on the LinuxONE server)

Numbers come from `results/*.json`, produced by the scripts in `scripts/`.

### 1. Rules and interpreter vs compiled COBOL

| Program | What was compared | Inputs | Same |
|---|---|---|---|
| CREDREV | extracted rules vs the compiled program, whole output line | 30,000 real accounts, 260 boundary cases, 5,000 random records | **35,260 / 35,260** |
| IBM SAM2 | extracted rules vs the compiled subroutine, all four parameters | IBM's sample data (12), z3 example inputs (17), a grid of codes/actions/fields/amounts (4,506), random (3,000) | **7,535 / 7,535** |
| IBM SAM1 | interpreter (calling its model of SAM2) vs the compiled program: updated customer file, report, console, return code | IBM's sample files + 300 generated file pairs; 1,607 transactions | **300 / 300** defined runs (1 run is undefined behaviour, below) |
| Five construct programs (`tests/cobol`) | interpreter vs compiled program, every DISPLAY line | 2,000 random records each | **10,000 / 10,000** |

The construct programs cover GO TO and PERFORM THRU; OCCURS and VARYING/TEST AFTER loops; REDEFINES, COMP-3, COMP and signed fields; ROUNDED and edited pictures (Z , . - CR $ * floating); 88 lists, SET TRUE, EVALUATE THRU, reference modification and INITIALIZE.

### 2. z3 symbolic analysis

| Program | Code paths | Impossible (proved) | Rules | Redundant conditions removed | Simplifications proved | Overlapping rules |
|---|---|---|---|---|---|---|
| CREDREV | 6 | 0 | 6 | 3 | all | none |
| IBM SAM2 | 109 | 92 | **17** | 44 | all | none |
| IBM SAM1 (per decision) | 28 | 0 | 28 | 8 | all | – |

Each rule also gets an example input found by z3; for SAM2 those 17 inputs are part of the comparison above.

### 3. Defects found in IBM's SAM1 sample

| # | Finding | How |
|---|---|---|
| 1 | When reading the transaction file fails, the error message reports the **customer** file's status code (`MOVE WS-CUSTFILE-STATUS TO ERR-MSG-DATA2` in 710-READ-TRAN-FILE) | static check on the rules |
| 2 | The output-file write error also reports the customer input file's status (740-WRITE-CUSTOUT-FILE) | static check |
| 3 | CUSTOMER-FILE-OUT is opened but never closed | static check |
| 4 | `PERFORM TEST AFTER VARYING I FROM 1 BY 1 UNTIL I > 12` runs once more with I = 13 and writes `WS-CUST-MONTH(13)`, nine bytes into the next field (every ADD transaction; GnuCOBOL does not check subscripts, and the interpreter reproduces the overwrite) | while running |
| 5 | An UPDATE for a contact record ('C') reaches SAM2, which treats it as a customer record and adds to a numeric field that holds text | while running |

### 4. Risk model (4,501 held-out test accounts)

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

### 5. Policy alone vs policy + AI (same test accounts)

| | Accounts | Defaulted next month |
|---|---|---|
| Policy grants a limit increase | 1,500 | 175 (11.7%) |
| Hybrid grants it (AI risk low) | 952 | 75 (7.9%) |
| Hybrid holds it for review | 548 | 100 (18.2%) |
| Early warnings (policy KEEP → review, AI risk high) | 250 | 122 (48.8%) |
| Review queue: urgent / normal / low priority | 219 / 537 / 232 | 54.8% / 15.5% / 15.1% |

The hybrid keeps **57% of the defaulters** among limit increases from getting one, and flags 250 accounts the policy would have left unchanged, half of which defaulted. The combination table was chosen on the validation accounts; these are test numbers.

### 6. Explanations

IBM Granite 3.3 2B Instruct (Q4_0, big-endian) on the server's 3 vCPUs, no GPU. Eleven demo accounts, two per scenario (`results/llm_bench.json`):

| | |
|---|---|
| Rewrites accepted by the guard | 9 of 11 |
| Rejected, draft note shown instead | 2 (both said the program would "keep" an account it sent to REVIEW) |
| Median time per explanation | 21 s (about 5 tokens/s) |
| Rule documentation accepted | 6 of 6 (`results/rule_docs.json`) |

The faster Granite 3.0 1B-A400M (about 4 s) passed the guard on only 1 of 11, so LegacyLens uses the 2B model. The decision appears at once; the explanation follows, and demo accounts are explained in the background at start-up. The UI always shows the facts the explanation was written from.

### 7. Speed on the z17 (`results/benchmark.json`)

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
~/venv/bin/python scripts/analyse_programs.py  # rules, z3, examples, findings    -> results/programs/
~/venv/bin/python scripts/train_model.py       # ONNX model + runtimes            -> models/, results/model.json
~/venv/bin/python scripts/evaluate_hybrid.py   # policy vs hybrid                 -> results/hybrid.json
~/venv/bin/python scripts/benchmark.py         # speed                            -> results/benchmark.json
~/venv/bin/python -m pytest tests              # unit tests
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
- Granite's rewrite can still read awkwardly. The guard catches added numbers and decision words, not every wrong phrasing.

## Open-source resources

| Resource | Use | Licence |
|---|---|---|
| [IBM Z Open Editor samples](https://github.com/IBM/zopeneditor-sample) (SAM1, SAM2, copybooks, sample data) | real COBOL to analyse and verify, bundled unmodified in `samples/ibm-zopeneditor` | Apache-2.0 |
| [UCI Default of Credit Card Clients](https://doi.org/10.24432/C55S3H) (Yeh & Lien, 2009) | training and test data | CC BY 4.0 |
| [GnuCOBOL](https://gnucobol.sourceforge.io/) 3.1.2 | compiles and runs the COBOL on s390x | GPL-3.0 |
| [z3](https://github.com/Z3Prover/z3) 4.8.12 (Ubuntu package) | symbolic analysis and proofs | MIT |
| [IBM Snap ML](https://snapml.readthedocs.io/) 1.17.2 | ONNX import and inference on the AI accelerator | IBM licence (PyPI) |
| [zDNN](https://github.com/IBM/zDNN) (libzdnn0) | NNPA access for Snap ML | Apache-2.0 |
| [scikit-learn](https://scikit-learn.org/), [skl2onnx](https://github.com/onnx/sklearn-onnx), [ONNX](https://onnx.ai/) | training, export, exact reference evaluation | BSD-3 / Apache-2.0 |
| [llama.cpp](https://github.com/ggml-org/llama.cpp) | LLM inference on s390x | MIT |
| [IBM Granite 3.3 2B Instruct](https://huggingface.co/ibm-granite/granite-3.3-2b-instruct), big-endian GGUF from the [s390x verified models](https://huggingface.co/collections/taronaeo/s390x-verified-models-672765393af438d0ccb72a08) listed in llama.cpp's s390x guide | explanations and rule documentation | Apache-2.0 |
| [FastAPI](https://fastapi.tiangolo.com/), [uvicorn](https://www.uvicorn.org/), [pandas](https://pandas.pydata.org/), [NumPy](https://numpy.org/) | service and data handling | MIT / BSD |
| [podman](https://podman.io/), Ubuntu 24.04 | container and OS | Apache-2.0 / various |

## How this was built

DISCLOSURE_PLACEHOLDER

## Licence

Apache-2.0 (see `LICENSE`). The IBM samples in `samples/ibm-zopeneditor` keep their own Apache-2.0 licence and notices.
