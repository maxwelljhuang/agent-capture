"""SQLAlchemy declarative models for the ledger schema.

Mirrors the SQL in the plan (packages/ledger §"Database schema"). The actual
DDL — including the partitioned ``spans`` table, the append-only trigger,
and the role definitions — lives in the Alembic migration, because raw DDL
is the only honest way to express those constructs.

These models exist for two reasons:
1. Queries — repository code uses them for typed access.
2. Alembic — autogenerate diffs against them for non-DDL columns.

Because ``spans`` is PARTITION BY RANGE (start_time), Alembic cannot
autogenerate it correctly. The initial migration writes the DDL by hand and
``include_object`` in env.py excludes ``spans`` from autogen forever.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import (
    ARRAY,
    CHAR,
    INTEGER,
    UUID,
    Column,
    DateTime,
    LargeBinary,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# ---- spans -------------------------------------------------------------
# Defined here for typed queries. DDL is in the initial migration.


class Span(Base):
    __tablename__ = "spans"

    span_id: Any = Column(CHAR(16), primary_key=True)
    trajectory_id: Any = Column(CHAR(32), primary_key=True)
    start_time: Any = Column(DateTime(timezone=True), primary_key=True)

    parent_span_id: Any = Column(CHAR(16), nullable=True)
    end_time: Any = Column(DateTime(timezone=True), nullable=False)

    end_customer_id: Any = Column(Text, nullable=False)
    retention_class: Any = Column(Text, nullable=False)
    regulatory_regime: Any = Column(ARRAY(Text), nullable=False)
    data_classification: Any = Column(Text, nullable=False)

    type: Any = Column(Text, nullable=False)
    status: Any = Column(Text, nullable=False)
    agent_version: Any = Column(Text, nullable=False)
    policy_version_active: Any = Column(Text, nullable=False)

    content_hash: Any = Column(CHAR(64), nullable=False)
    parent_content_hash: Any = Column(CHAR(64), nullable=True)
    schema_version: Any = Column(Text, nullable=False)

    body: Any = Column(JSONB, nullable=False)

    ingested_at: Any = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    ingest_batch_id: Any = Column(UUID(as_uuid=True), nullable=False)

    __table_args__ = (
        # the actual indexes are created in the migration so they live on the
        # partitioned parent table; declared here for documentation only
        {"info": {"managed_by_alembic_ddl": True}},
    )


# ---- ingest tracking ----------------------------------------------------


class IngestBatch(Base):
    __tablename__ = "ingest_batches"

    batch_id: Any = Column(UUID(as_uuid=True), primary_key=True)
    received_at: Any = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    source_token_id: Any = Column(Text, nullable=False)
    end_customer_id: Any = Column(Text, nullable=False)
    span_count: Any = Column(INTEGER, nullable=False)
    accepted: Any = Column(INTEGER, nullable=False)
    rejected: Any = Column(INTEGER, nullable=False)
    duration_ms: Any = Column(INTEGER, nullable=False)


# ---- quarantine --------------------------------------------------------


class Quarantine(Base):
    __tablename__ = "quarantine"

    quarantine_id: Any = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    raw_body: Any = Column(JSONB, nullable=False)
    reason_code: Any = Column(Text, nullable=False)
    reason_detail: Any = Column(Text, nullable=True)
    end_customer_id: Any = Column(Text, nullable=True)
    received_at: Any = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    source_token_id: Any = Column(Text, nullable=True)
    batch_id: Any = Column(UUID(as_uuid=True), nullable=True)


# ---- attestations ------------------------------------------------------


class Attestation(Base):
    __tablename__ = "attestations"

    attestation_id: Any = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    end_customer_id: Any = Column(Text, nullable=False)
    window_start: Any = Column(DateTime(timezone=True), nullable=False)
    window_end: Any = Column(DateTime(timezone=True), nullable=False)
    trajectory_count: Any = Column(INTEGER, nullable=False)
    leaf_hashes_count: Any = Column(INTEGER, nullable=False)
    merkle_root: Any = Column(CHAR(64), nullable=False)
    signature: Any = Column(LargeBinary, nullable=False)
    signing_key_id: Any = Column(Text, nullable=False)
    created_at: Any = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    exported_at: Any = Column(DateTime(timezone=True), nullable=True)


class AttestationLeaf(Base):
    __tablename__ = "attestation_leaves"

    attestation_id: Any = Column(UUID(as_uuid=True), primary_key=True)
    trajectory_id: Any = Column(CHAR(32), primary_key=True)
    trajectory_root_hash: Any = Column(CHAR(64), nullable=False)
    leaf_index: Any = Column(INTEGER, nullable=False)


# ---- access log --------------------------------------------------------


class AccessLog(Base):
    __tablename__ = "access_log"

    access_id: Any = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    actor_token_id: Any = Column(Text, nullable=False)
    actor_role: Any = Column(Text, nullable=False)
    end_customer_id: Any = Column(Text, nullable=False)
    action: Any = Column(Text, nullable=False)
    target_kind: Any = Column(Text, nullable=False)
    target_id: Any = Column(Text, nullable=False)
    at: Any = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    request_id: Any = Column(Text, nullable=True)
    ip: Any = Column(INET, nullable=True)
    user_agent: Any = Column(Text, nullable=True)


# ---- tokens ------------------------------------------------------------


class ApiToken(Base):
    __tablename__ = "api_tokens"

    token_id: Any = Column(Text, primary_key=True)
    token_hash: Any = Column(Text, nullable=False)
    role: Any = Column(Text, nullable=False)
    end_customer_id: Any = Column(Text, nullable=True)
    label: Any = Column(Text, nullable=True)
    created_at: Any = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    created_by: Any = Column(Text, nullable=True)
    revoked_at: Any = Column(DateTime(timezone=True), nullable=True)
    expires_at: Any = Column(DateTime(timezone=True), nullable=True)


# ---- litigation holds --------------------------------------------------


class LitigationHold(Base):
    __tablename__ = "litigation_holds"

    trajectory_id: Any = Column(CHAR(32), primary_key=True)
    placed_by: Any = Column(Text, nullable=False)
    reason: Any = Column(Text, nullable=True)
    placed_at: Any = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    released_at: Any = Column(DateTime(timezone=True), nullable=True)
    released_by: Any = Column(Text, nullable=True)


# ---- retention audit ---------------------------------------------------


class RetentionOperation(Base):
    __tablename__ = "retention_operations"

    op_id: Any = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    op_kind: Any = Column(Text, nullable=False)
    target: Any = Column(Text, nullable=False)
    spans_affected: Any = Column(INTEGER, nullable=False)
    retention_class: Any = Column(Text, nullable=False)
    executed_at: Any = Column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    executed_by: Any = Column(Text, nullable=False)


__all__ = [
    "AccessLog",
    "ApiToken",
    "Attestation",
    "AttestationLeaf",
    "Base",
    "IngestBatch",
    "LitigationHold",
    "Quarantine",
    "RetentionOperation",
    "Span",
]
