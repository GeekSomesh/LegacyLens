# Submission kit (IBM Z Datathon 2026)

Copy these into the Hack2Skill form. Replace the bracketed parts.

**Project title:** `[TEAM NAME]_IBM Z Datathon 2026 Project` (the format the rules ask for), with LegacyLens as the product name in the description.

**Event code:** SSF2026 · **Theme:** Real-Time AI for Critical Decisions

**One-line summary**
LegacyLens turns legacy COBOL decision code into rules that are proved with z3 and checked against the compiled program, shows what a code change does before it ships, and adds a real-time ONNX risk model and IBM Granite explanations, all on IBM LinuxONE.

**Problem**
Banks make critical decisions (credit limits, fraud holds, account updates) in decades-old COBOL. Nobody can say exactly what those programs decide, so every change is risky and adding AI to them is riskier still.

**Solution**
1. Read the COBOL (copybooks, byte-exact data layout) and extract every decision path as a readable rule.
2. Prove the rules with the z3 SMT solver (impossible paths, simplifications, overlaps, an example per rule) and check them against the compiled program, byte for byte.
3. Find defects with examples: silent overflow, sign loss, division by zero, wrong status codes, writes outside a table.
4. Change impact: z3 finds every region where a new version decides differently; both compiled programs confirm it on real data.
5. Export the rules as DMN 1.3 and review them with a hash-chained audit trail.
6. Real time: the legacy policy plus an ONNX risk model on the z17 AI accelerator; the AI can hold or escalate, never approve; IBM Granite explains each decision, checked against the facts.

**Results (measured on LinuxONE)**
- Rules = compiled COBOL: CREDREV 35,260/35,260; FRAUDHLD 20,212/20,212; IBM SAM2 7,535/7,535; IBM SAM1 300/300 file runs; construct suite 10,000/10,000.
- z3 on IBM's SAM2: 109 paths → 92 proved impossible → 17 rules, all simplifications proved.
- Defects in IBM's samples: a debit stored as a credit, balances silently losing digits, wrong status codes, a file never closed, a write past a table.
- Change impact: 6 regions found by z3, 6/6 confirmed; 1,039 of 30,000 accounts change outcome, including 200 moved to a softer action of which 58% defaulted.
- AI: keeps 57% of defaulters from getting a limit increase (4,501 held-out accounts); 15 µs per risk score; 125,000 decisions/s for the portfolio.

**How it uses IBM Z / LinuxONE**
IBM z17 virtual server in the LinuxONE Community Cloud (Ubuntu 24.04 s390x): ONNX model served by IBM Snap ML on the on-chip AI accelerator (NNPA via zDNN), GnuCOBOL builds as the reference, z3 for proofs, IBM Granite in llama.cpp for s390x (big-endian GGUF), a rootless podman container under systemd, access only through SSH.

**Technology stack**
Python, GnuCOBOL 3.1.2, z3 4.8.12, IBM Snap ML 1.17.2 + zDNN, scikit-learn + skl2onnx + ONNX, llama.cpp + IBM Granite 3.3 2B, FastAPI, podman, systemd, DMN 1.3.

**Links**
- Repository: [GitHub URL]
- Demo video (3 minutes or less): [URL]
- Architecture flowchart: `docs/architecture.png` in the repository
- Open-source resources: see the table at the end of README.md (IBM Z Open Editor samples, UCI credit data, GnuCOBOL, z3, Snap ML, zDNN, scikit-learn, skl2onnx, ONNX, llama.cpp, IBM Granite, FastAPI, podman, OMG DMN schemas)

**Screenshots to attach** (`docs/screenshots/`)
01 LinuxONE virtual server · 02 decision desk · 03 IBM SAM2 rules with z3 · 05 IBM SAM1 findings · 04 evidence. Add a screenshot of the terminal on the server (`uname -m`, `grep -o nnpa /proc/cpuinfo | head -1`, `podman ps`) taken by you.
