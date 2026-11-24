"""No-build frontend integrity gate: parse scripts, resolve local assets and enforce a size budget."""

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
MAX_BUNDLE_BYTES = 300_000  # shipped local JS + CSS, excluding images and remote fonts
fails = []


def check(ok, message):
    print(("PASS " if ok else "FAIL ") + message)
    if not ok:
        fails.append(message)


html = (WEB / "index.html").read_text(encoding="utf-8")
assets = sorted((WEB / "assets").glob("*.js"))
node = subprocess.run(["node", "--version"], capture_output=True, text=True)
check(node.returncode == 0, "Node.js is available for JavaScript syntax parsing")
if node.returncode == 0:
    for path in assets:
        result = subprocess.run(
            ["node", "--input-type=module", "--check"], input=path.read_text(encoding="utf-8-sig").encode("utf-8"), capture_output=True
        )
        check(
            result.returncode == 0,
            f"JavaScript parses: {path.relative_to(ROOT)}"
            + ("" if result.returncode == 0 else f" ({result.stderr.decode('utf-8', errors='replace').strip()})"),
        )

refs = re.findall(r"""(?:src|href)=["'](/(?:assets|brand)/[^"'#?]+)""", html)
for path in assets:
    source = path.read_text(encoding="utf-8")
    refs.extend(re.findall(r"""from\s+["'](\.[^"']+)["']|import\s+["'](\.[^"']+)["']""", source))
resolved = []
for ref in refs:
    if isinstance(ref, tuple):
        ref = next(x for x in ref if x)
        target = (WEB / "assets" / ref).resolve()
    else:
        target = (WEB / ref.lstrip("/")).resolve()
    resolved.append((ref, target))
for ref, target in resolved:
    check(target.is_file() and WEB.resolve() in target.parents, f"Local asset resolves: {ref}")
manifest = json.loads((WEB / "asset-manifest.json").read_text(encoding="utf-8"))
for name, digest in manifest.items():
    path = WEB / name
    check(path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest, f"Asset manifest hash matches: {name}")
size = sum(p.stat().st_size for p in assets) + (WEB / "assets/index.css").stat().st_size
check(size <= MAX_BUNDLE_BYTES, f"Local JS/CSS size {size:,} bytes <= {MAX_BUNDLE_BYTES:,} byte budget")
print("Summary: " + ("FAIL" if fails else "PASS"))
sys.exit(bool(fails))
