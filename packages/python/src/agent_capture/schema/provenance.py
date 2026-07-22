"""Provenance fields (Section 4.4).

These support the downstream tamper-evident ledger and are computed by the
span builder as the last step before export — after redaction, so the hashed
bytes match the bytes the ledger will store.

``schema_version`` is bumped any time the canonical serialization of a span
changes in a way that would invalidate hashes computed under prior versions.
The current value is the single source of truth for both Python and TypeScript
emitters and is regenerated into ``schemas/span.schema.json`` on every build.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.0.0"
"""Span schema version. Bumped only on canonical-form-changing edits."""


class ProvenanceFields(BaseModel):
    """Hash chain entries written at span close.

    Both hashes are hex-encoded SHA-256 of the canonical JSON serialization
    defined in :mod:`agent_capture.schema.canonical`. They are produced
    *after* redaction so the bytes hashed are exactly the bytes shipped.

    Attributes:
        content_hash: SHA-256 of this span's canonical serialized form,
            with ``provenance.content_hash`` itself excluded from the input.
        parent_content_hash: ``content_hash`` of the parent span, or ``None``
            for trajectory roots. Creates the chain that the ledger layer
            verifies.
        schema_version: The version of the span schema used to produce this
            span. Hashes under different schema versions are not comparable.
    """

    content_hash: str = Field(
        ...,
        description="Hex SHA-256 of the canonical serialized span.",
        pattern=r"^[0-9a-f]{64}$",
    )
    parent_content_hash: str | None = Field(
        default=None,
        description="Hex SHA-256 of the parent span's canonical form. Null for trajectory roots.",
        pattern=r"^[0-9a-f]{64}$",
    )
    schema_version: str = Field(
        default=SCHEMA_VERSION,
        description="Span schema version. See SCHEMA_VERSION in this module.",
    )
