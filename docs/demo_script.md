# Demo video script (under 3 minutes)

Record the screen at 1080p. Keep the browser at 100% zoom. Have two windows ready: a terminal with an SSH session to the LinuxONE server, and the browser with the LegacyLens UI (through `ssh -L 8800:127.0.0.1:8000 linux1@<server>`, then http://localhost:8800).

| Time | Show | Say (roughly) |
|---|---|---|
| 0:00–0:20 | Title slide or README top | "Banks still make critical credit decisions in COBOL. Nobody can say exactly what those programs do, so adding AI to them is risky. LegacyLens fixes that, and it runs entirely on IBM LinuxONE." |
| 0:20–0:40 | LinuxONE Community Cloud portal: the `coboldecision` server, AI_LAB flavor. Terminal: `uname -m`, `grep -o nnpa /proc/cpuinfo \| head -1`, `podman ps` | "This is our LinuxONE virtual server: an IBM z17, s390x, with the on-chip AI accelerator, NNPA. The service runs here in a podman container." |
| 0:40–1:05 | UI, **COBOL rules** tab. Click a rule to highlight its lines. Terminal: `python scripts/verify_rules.py` (7 s) | "We parse the COBOL and turn every decision path into a rule, with its line numbers. Then we prove it: the rules and the compiled COBOL program give the same output line for all 35,260 records, real accounts plus boundary and random cases. 100 percent." |
| 1:05–1:50 | UI, **Decide** tab. Click **Risky limit increase**. Point at policy box, AI risk box, final HOLD, highlighted COBOL lines, timing, Granite explanation | "The legacy policy says INCREASE, rule 5, these lines. Our risk model, an ONNX file served by IBM Snap ML on the z17 AI accelerator, scores this account in a fraction of a millisecond: medium risk. So the increase is held for review. IBM Granite, running on the same server, explains it, and we check every number it writes against the facts." |
| 1:50–2:10 | Click **Early warning**, then **Safe increase** | "Here the policy would keep the account unchanged, but the AI sees a high risk of default and escalates it. And here both agree: the increase goes through. The AI can hold or escalate, never approve on its own." |
| 2:10–2:40 | UI, **Evidence** tab: hybrid table | "On 4,500 held-out accounts, the hybrid keeps 57 percent of the defaulters among limit increases from getting one, and its early warnings flag accounts where half defaulted the next month. Same ONNX model on three runtimes, all agreeing." |
| 2:40–2:58 | README architecture diagram | "COBOL rules you can trust, real-time AI on the accelerator, and explanations a reviewer can check. All on LinuxONE. Thank you." |

Before recording: start the LLM (`sudo systemctl start granite` or the llama-server command in the README) and the container, open the UI once and wait about a minute so the demo accounts' explanations are cached.
