# Demo video script (under 3 minutes)

Record the screen at 1080p, browser at 100% zoom. Have two windows ready: a terminal with an SSH session to the LinuxONE server, and the browser with the LegacyLens UI (`ssh -L 8800:127.0.0.1:8000 linux1@<server>`, then http://localhost:8800). Start the services a few minutes before recording so the demo accounts' explanations are ready.

| Time | Show | Say (roughly) |
|---|---|---|
| 0:00–0:15 | README top ("At a glance") | "Banks still make critical decisions in COBOL that nobody can fully explain. LegacyLens turns that code into rules, proves them, and adds real-time AI, all on IBM LinuxONE." |
| 0:15–0:30 | LinuxONE portal: `coboldecision`, AI_LAB. Terminal: `uname -m`, `grep -o nnpa /proc/cpuinfo \| head -1`, `podman ps`, `systemctl --user is-active legacylens granite` | "Our LinuxONE server: an IBM z17 with the on-chip AI accelerator. The service runs in a podman container under systemd, next to IBM Granite." |
| 0:30–1:05 | UI **Programs & rules** → **SAM2**. Point at the stats, click a rule (lines highlight), open "Example input found by z3" | "This is IBM's own sample code: copybooks, overlays, packed decimals. We found 109 paths in it; the z3 solver proved 92 impossible and simplified the rest to 17 rules, proving each one equivalent. Then we ran the rules against the compiled program, 7,535 calls, byte for byte: 100 percent." |
| 1:05–1:25 | **SAM1**: the findings box | "On IBM's batch program the whole file run matches too, and LegacyLens found five defects in IBM's sample: error messages that report the wrong file's status, a file never closed, and a loop that writes past the end of a table." |
| 1:25–2:05 | **Decide** tab → **Risky limit increase**. Point at the policy box (COBOL rule + lines, "compiled COBOL gives the same output"), AI risk (ONNX on NNPA), final HOLD, timing, Granite explanation | "Now the credit policy. COBOL says increase the limit, rule 5. Our ONNX risk model, served on the z17's AI accelerator, scores the account in microseconds: medium risk. So the increase is held for review. IBM Granite on the same server explains it, and every number it writes is checked against the facts." |
| 2:05–2:20 | **Early warning**, then **Safe increase** | "The AI can escalate or hold, never approve on its own. Here it agrees, and the increase goes through." |
| 2:20–2:45 | **Evidence** tab: hybrid table, speed table | "On 4,500 held-out accounts the hybrid keeps 57 percent of defaulters from getting a limit increase. The whole portfolio is decided at 125,000 accounts a second; one decision through the API takes about 5 milliseconds." |
| 2:45–2:58 | README architecture | "COBOL rules you can prove, real-time AI on the accelerator, explanations you can check. All on LinuxONE." |
