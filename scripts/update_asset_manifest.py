"""Regenerate the no-build frontend asset manifest with line-ending independent hashes."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"


def main() -> None:
    paths = sorted([*WEB.joinpath("assets").glob("*.js"), WEB / "assets" / "index.css", *WEB.joinpath("brand").iterdir()])
    entries = {}
    for path in paths:
        if not path.is_file():
            continue
        content = path.read_bytes()
        if path.suffix.lower() in {".css", ".html", ".js", ".svg"}:
            content = content.replace(b"\r\n", b"\n")
        entries[path.relative_to(WEB).as_posix()] = hashlib.sha256(content).hexdigest()
    (WEB / "asset-manifest.json").write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Updated hashes for {len(entries)} local assets.")


if __name__ == "__main__":
    main()
