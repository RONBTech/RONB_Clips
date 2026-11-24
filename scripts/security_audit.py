"""Run the automated security checks: source scan, dependency scan and a few project-specific rules.

    python scripts/security_audit.py            # local run: missing scanners are reported, not fatal
    python scripts/security_audit.py --strict   # CI: every scanner must be installed and clean

The scanners are pinned in requirements-dev.txt. Install with `pip install -r requirements-dev.txt`.
"""

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(name: str, command: list[str]) -> tuple[bool, bool, str]:
    """Return (passed, skipped, report)."""
    if not shutil.which(command[0]):
        return False, True, f"{name}: SKIPPED ({command[0]} is not installed; pip install -r requirements-dev.txt)"
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    out = (result.stdout + result.stderr).strip()
    if result.returncode == 0:
        return True, False, f"{name}: PASS\n{out}" if out else f"{name}: PASS"
    return False, False, f"{name}: FAIL (exit {result.returncode})\n{out}"


def project_rules() -> tuple[bool, bool, str]:
    """Checks that do not need a scanner, so they always run."""
    problems: list[str] = []
    server = (ROOT / "studio" / "server.py").read_text(encoding="utf-8")
    for header in ("nosniff", "DENY", "strict-origin-when-cross-origin", "content-security-policy"):
        if header not in server:
            problems.append(f"missing security header in studio/server.py: {header}")
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for path in ("data/", "*.key", ".env", "secrets/"):
        if path not in gitignore:
            problems.append(f".gitignore is missing {path}")
    # A key accidentally pasted into a tracked text file would be committed forever; catch the common shapes.
    pattern = re.compile(r"(?:AIza[0-9A-Za-z_\-]{30,}|sk-[0-9A-Za-z_\-]{30,}|ghp_[0-9A-Za-z]{30,})")
    for folder in ("studio", "web", "scripts", "tests"):
        for path in (ROOT / folder).rglob("*"):
            if path.is_file() and path.suffix in {".py", ".js", ".html", ".json", ".md"}:
                try:
                    text = path.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue
                if pattern.search(text):
                    problems.append(f"possible API key committed in {path.relative_to(ROOT)}")
    if problems:
        return False, False, "project rules: FAIL\n" + "\n".join("  - " + p for p in problems)
    return True, False, "project rules: PASS"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--strict", action="store_true", help="fail if any scanner is missing as well as on findings")
    ap.add_argument("--skip-deps", action="store_true", help="skip pip-audit (offline machine)")
    args = ap.parse_args()

    results = [project_rules(), run("bandit source scan", [sys.executable, "-m", "bandit", "-q", "-ll", "-r", "studio"])]
    if not args.skip_deps:
        results.append(run("pip-audit dependencies", [sys.executable, "-m", "pip_audit", "-r", "requirements.txt"]))

    for _passed, _skipped, text in results:
        print(text)
    failed = [i for i, (passed, skipped, _) in enumerate(results) if not passed and (args.strict or not skipped)]
    names = ["project rules", "bandit", "pip-audit"]
    print("\nSECURITY AUDIT:", "PASS" if not failed else "FAIL: " + ", ".join(names[i] for i in failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
