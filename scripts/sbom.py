"""CycloneDX 1.5 software bill of materials for the LegacyLens server.

Python distributions visible to the interpreter, the Ubuntu packages the
service depends on, and the model files with their SHA-256.

    python scripts/sbom.py      # sbom.cdx.json
"""
import hashlib
import json
import platform
import subprocess
import uuid
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APT = ["gnucobol3", "libzdnn0", "python3-z3", "python3-numpy", "python3-pandas", "python3-sklearn",
       "python3-onnx", "python3-lxml", "podman", "libpython3.12"]
MODELS = [ROOT / "models" / "risk_gbm.onnx", Path.home() / "models" / "granite-3.3-2b-instruct-be.Q4_0.gguf"]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    comps = []
    for dist in sorted(metadata.distributions(), key=lambda d: (d.metadata["Name"] or "").lower()):
        name = dist.metadata["Name"]
        if not name:
            continue
        lic = dist.metadata.get("License-Expression") or dist.metadata.get("License") or ""
        comp = {"type": "library", "name": name, "version": dist.version,
                "purl": f"pkg:pypi/{name.lower()}@{dist.version}"}
        if lic and len(lic) < 80:
            comp["licenses"] = [{"license": {"name": lic}}]
        comps.append(comp)
    for pkg in APT:
        r = subprocess.run(["dpkg-query", "-W", "-f=${Version}", pkg], capture_output=True, text=True)
        if r.returncode == 0 and r.stdout:
            comps.append({"type": "library", "name": pkg, "version": r.stdout,
                          "purl": f"pkg:deb/ubuntu/{pkg}@{r.stdout}?arch={platform.machine()}"})
    for m in MODELS:
        if m.exists():
            comps.append({"type": "machine-learning-model", "name": m.name, "hashes": [{"alg": "SHA-256", "content": sha256(m)}]})
    bom = {"bomFormat": "CycloneDX", "specVersion": "1.5", "serialNumber": f"urn:uuid:{uuid.uuid4()}", "version": 1,
           "metadata": {"timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "component": {"type": "application", "name": "LegacyLens", "version": "0.2.0"},
                        "properties": [{"name": "platform", "value": f"{platform.system()} {platform.machine()}"}]},
           "components": comps}
    (ROOT / "sbom.cdx.json").write_text(json.dumps(bom, indent=1))
    print(len(comps), "components")


if __name__ == "__main__":
    main()
