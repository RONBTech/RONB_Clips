"""Check that every statically named application setting appears in .env.example."""

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREFIXES = ("RONBCLIPS_", "GOOGLE_")
EXTRA_SETTINGS = {"DATABASE_URL"}
CALL_NAMES = {"env", "_limit", "_secret", "_read_secret"}


def called_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def implemented_settings():
    found = set(EXTRA_SETTINGS)
    for path in (ROOT / "studio").glob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as error:
            print(f"Cannot inspect {path}: {error}", file=sys.stderr)
            return None
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or called_name(node.func) not in CALL_NAMES:
                continue
            for arg in node.args[:2]:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    value = arg.value
                    if value.startswith(PREFIXES):
                        found.add(value)
    return found


def example_settings():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    return set(re.findall(r"(?m)^\s*#?\s*((?:RONBCLIPS|GOOGLE)_[A-Z0-9_]+|DATABASE_URL)\s*=", text))


def main():
    expected = implemented_settings()
    if expected is None:
        return 1
    absent = sorted(expected - example_settings())
    if absent:
        print("Missing implemented settings in .env.example:")
        print("\n".join(f"  {key}" for key in absent))
        return 1
    print(f"PASS: .env.example covers all {len(expected)} statically named application settings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
