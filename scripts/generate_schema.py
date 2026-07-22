#!/usr/bin/env python3
"""Generate ``schemas/span.schema.json`` from the Pydantic models.

This is the only correct way to update the JSON Schema. Hand-edits to the
schemas/ directory are rejected by CI (see scripts/verify_schema_sync.py).

Run:
    uv run python scripts/generate_schema.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure the Python package is importable when run from the repo root.
REPO_ROOT = Path(__file__).resolve().parent.parent
PY_PKG = REPO_ROOT / "packages" / "python" / "src"
sys.path.insert(0, str(PY_PKG))

from agent_capture.schema import SCHEMA_VERSION, Span  # noqa: E402


def build_schema() -> dict[str, object]:
    """Return the canonical JSON Schema for the Span model."""
    schema = Span.model_json_schema(mode="serialization")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = (
        f"https://schemas.agent-capture.dev/span/v{SCHEMA_VERSION}/span.schema.json"
    )
    schema["title"] = "AgentCaptureSpan"
    schema["description"] = (
        f"agent-capture span schema v{SCHEMA_VERSION}. Generated from the Pydantic "
        "models in packages/python/src/agent_capture/schema/. Do not edit by hand."
    )
    return schema


def main() -> int:
    schema = build_schema()
    out_path = REPO_ROOT / "schemas" / "span.schema.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(schema, indent=2, sort_keys=True) + "\n"
    out_path.write_text(rendered, encoding="utf-8")
    print(f"wrote {out_path.relative_to(REPO_ROOT)} ({len(rendered)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
