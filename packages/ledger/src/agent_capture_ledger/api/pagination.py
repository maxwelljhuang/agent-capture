"""Opaque cursor-based pagination.

The wire form is a base64-encoded JSON blob. Callers treat it as opaque:
we change the encoding without breaking compatibility because the cursor
only roundtrips through us. Stable ordering is mandatory — the underlying
indexes are on ``(end_customer_id, start_time DESC)`` so we paginate by
``(start_time, trajectory_id)``.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Cursor:
    after_time: datetime
    after_id: str

    def encode(self) -> str:
        payload = json.dumps(
            {
                "t": self.after_time.isoformat(),
                "i": self.after_id,
            },
            separators=(",", ":"),
        )
        return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")

    @classmethod
    def decode(cls, raw: str | None) -> Cursor | None:
        if not raw:
            return None
        pad = "=" * (-len(raw) % 4)
        try:
            obj = json.loads(base64.urlsafe_b64decode(raw + pad).decode())
            return cls(
                after_time=datetime.fromisoformat(obj["t"]),
                after_id=obj["i"],
            )
        except (ValueError, KeyError, json.JSONDecodeError):
            return None
