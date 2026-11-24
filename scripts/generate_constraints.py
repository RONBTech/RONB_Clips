"""Regenerate constraints.txt from the pinned requirement files.

    python scripts/generate_constraints.py

Pip resolves the same dependencies Docker and CI install, targeting the oldest Python we support (3.11) and
the wheel platforms we ship (Windows x64 and Debian 12 / glibc 2.28 Linux x64). It writes a short report of
the exact versions it chose. Those versions become a lock file, so a transitive update cannot change what
runs without a deliberate edit here.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _report(req: Path, out: Path):
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--dry-run",
            "--ignore-installed",
            "--only-binary=:all:",
            "--python-version",
            "3.11",
            "--implementation",
            "cp",
            "--abi",
            "cp311",
            "--platform",
            "win_amd64",
            "--platform",
            "manylinux_2_28_x86_64",
            "--platform",
            "manylinux2014_x86_64",
            "--quiet",
            "--report",
            str(out),
            "-r",
            str(req),
        ],
        cwd=ROOT,
        check=True,
    )


def main() -> int:
    rows = {}
    with tempfile.TemporaryDirectory(prefix="ronbclips-constraints-") as folder:
        for req in (ROOT / "requirements.txt", ROOT / "requirements-dev.txt"):
            report = Path(folder) / (req.stem + ".json")
            _report(req, report)
            for entry in json.loads(report.read_text(encoding="utf-8"))["install"]:
                meta = entry["metadata"]
                key = meta["name"].lower().replace("_", "-")
                rows[key] = (meta["name"], meta["version"])
    lines = [
        "# Generated from requirements.txt and requirements-dev.txt by pip's dependency resolver.",
        "# Resolved for Python 3.11 on Windows x64 and Debian 12 / glibc 2.28 Linux x64.",
        "# Regenerate with: python scripts/generate_constraints.py",
        "",
    ]
    lines += [f"{name}=={version}" for name, version in (rows[k] for k in sorted(rows))]
    (ROOT / "constraints.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {len(rows)} pinned packages to constraints.txt.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
