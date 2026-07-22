"""RFC 7807 Problem Details for every ledger error.

Every reject path uses these — automation downstream pattern-matches on the
stable ``type`` URI (``https://schemas.agent-capture.dev/errors/LE003``).
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException
from fastapi.responses import JSONResponse

ERROR_TYPE_BASE = "https://schemas.agent-capture.dev/errors"


@dataclass(frozen=True)
class LedgerError:
    code: str
    status: int
    title: str

    @property
    def type_uri(self) -> str:
        return f"{ERROR_TYPE_BASE}/{self.code}"

    def problem(self, detail: str | None = None, **extra: object) -> dict[str, object]:
        body: dict[str, object] = {
            "type": self.type_uri,
            "title": self.title,
            "status": self.status,
            "code": self.code,
        }
        if detail is not None:
            body["detail"] = detail
        body.update(extra)
        return body

    def http(self, detail: str | None = None, **extra: object) -> HTTPException:
        return HTTPException(status_code=self.status, detail=self.problem(detail, **extra))


# --- catalog (stable, machine-readable) ---------------------------------

# ingest-side
LE001 = LedgerError("LE001", 422, "Span shape invalid")
LE002 = LedgerError("LE002", 403, "Tenant mismatch")
LE003 = LedgerError("LE003", 422, "Content hash mismatch")
LE004 = LedgerError("LE004", 409, "Immutability violation")
LE005 = LedgerError("LE005", 422, "Parent hash mismatch")
LE006 = LedgerError("LE006", 422, "Schema version unsupported")

# query-side
LE007 = LedgerError("LE007", 400, "Invalid query parameter")

# auth
LE101 = LedgerError("LE101", 401, "Auth missing")
LE102 = LedgerError("LE102", 401, "Auth invalid")
LE103 = LedgerError("LE103", 403, "Auth insufficient role")
LE104 = LedgerError("LE104", 403, "Tenant scope violation")

# infrastructure
LE201 = LedgerError("LE201", 503, "Backpressure")
LE202 = LedgerError("LE202", 503, "Database unavailable")

# attestation
LE301 = LedgerError("LE301", 500, "Signing key unavailable")
LE302 = LedgerError("LE302", 500, "Attestation sink unavailable")


def problem_response(err: LedgerError, detail: str | None = None, **extra: object) -> JSONResponse:
    return JSONResponse(
        status_code=err.status,
        content=err.problem(detail, **extra),
        media_type="application/problem+json",
    )
