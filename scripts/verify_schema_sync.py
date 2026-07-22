#!/usr/bin/env python3
"""CI guard: regenerate the JSON Schema and fail if it differs from committed.

This runs in the schema-sync workflow. If it fails, the fix is to run
``uv run python scripts/generate_schema.py`` and commit the result.
"""

from __future__ import annotations

import difflib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from generate_schema import build_schema  # noqa: E402


def main() -> int:
    committed_path = REPO_ROOT / "schemas" / "span.schema.json"
    if not committed_path.exists():
        print(f"missing {committed_path}", file=sys.stderr)
        return 2

    committed = committed_path.read_text(encoding="utf-8")
    fresh = json.dumps(build_schema(), indent=2, sort_keys=True) + "\n"

    if committed == fresh:
        print("schema in sync")
        return 0

    print("schemas/span.schema.json is out of date.", file=sys.stderr)
    print("Run: uv run python scripts/generate_schema.py", file=sys.stderr)
    print(file=sys.stderr)
    diff = difflib.unified_diff(
        committed.splitlines(keepends=True),
        fresh.splitlines(keepends=True),
        fromfile="committed",
        tofile="regenerated",
        n=3,
    )
    sys.stderr.writelines(diff)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
