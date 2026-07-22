"""Initial ledger schema.

Creates every table from the plan plus the partitioned ``spans`` parent,
the append-only trigger, and one starter monthly partition. Roles
(``ledger_app``/``ledger_reader``/``ledger_retention``/``ledger_attestation``)
are created by the ``ledger db init`` CLI before migrations run — they need
to exist on the DB cluster, not in a tenant database.

Revision ID: 0001
Revises:
Create Date: 2026-05-29
"""

from __future__ import annotations

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- partitioned spans parent -------------------------------------
    op.execute("""
        CREATE TABLE spans (
            span_id              CHAR(16) NOT NULL,
            trajectory_id        CHAR(32) NOT NULL,
            parent_span_id       CHAR(16),
            start_time           TIMESTAMPTZ NOT NULL,
            end_time             TIMESTAMPTZ NOT NULL,
            end_customer_id      TEXT NOT NULL,
            retention_class      TEXT NOT NULL,
            regulatory_regime    TEXT[] NOT NULL,
            data_classification  TEXT NOT NULL,
            type                 TEXT NOT NULL,
            status               TEXT NOT NULL,
            agent_version        TEXT NOT NULL,
            policy_version_active TEXT NOT NULL,
            content_hash         CHAR(64) NOT NULL,
            parent_content_hash  CHAR(64),
            schema_version       TEXT NOT NULL,
            body                 JSONB NOT NULL,
            ingested_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
            ingest_batch_id      UUID NOT NULL,
            PRIMARY KEY (trajectory_id, span_id, start_time)
        ) PARTITION BY RANGE (start_time)
    """)

    # indexes on the parent — Postgres propagates to partitions
    op.execute("CREATE INDEX idx_spans_traj_time       ON spans (trajectory_id, start_time)")
    op.execute("CREATE INDEX idx_spans_customer_time   ON spans (end_customer_id, start_time DESC)")
    op.execute("CREATE INDEX idx_spans_retention_time  ON spans (retention_class, start_time)")
    op.execute("CREATE INDEX idx_spans_type_time       ON spans (type, start_time DESC)")
    op.execute("CREATE INDEX idx_spans_regime_gin      ON spans USING GIN (regulatory_regime)")
    op.execute("CREATE INDEX idx_spans_parent          ON spans (parent_span_id) WHERE parent_span_id IS NOT NULL")
    op.execute("CREATE INDEX idx_spans_content_hash    ON spans (content_hash)")
    op.execute("CREATE INDEX idx_spans_batch           ON spans (ingest_batch_id)")

    # append-only trigger — only the retention role can mutate.
    # Two functions because BEFORE UPDATE must RETURN NEW (else the
    # row keeps its OLD values silently — UPDATE looks accepted but
    # nothing changes) and BEFORE DELETE must RETURN OLD.
    op.execute("""
        CREATE OR REPLACE FUNCTION spans_block_update() RETURNS trigger AS $$
        BEGIN
            IF current_user NOT IN ('ledger_retention') THEN
                RAISE EXCEPTION 'spans is append-only (role=%)', current_user;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE OR REPLACE FUNCTION spans_block_delete() RETURNS trigger AS $$
        BEGIN
            IF current_user NOT IN ('ledger_retention') THEN
                RAISE EXCEPTION 'spans is append-only (role=%)', current_user;
            END IF;
            RETURN OLD;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("""
        CREATE TRIGGER trg_spans_no_update BEFORE UPDATE ON spans
            FOR EACH ROW EXECUTE FUNCTION spans_block_update()
    """)
    op.execute("""
        CREATE TRIGGER trg_spans_no_delete BEFORE DELETE ON spans
            FOR EACH ROW EXECUTE FUNCTION spans_block_delete()
    """)

    # starter partition: this and next month
    now = datetime.now(UTC)
    _create_partition(now.year, now.month)
    nxt_year, nxt_month = (now.year, now.month + 1) if now.month < 12 else (now.year + 1, 1)
    _create_partition(nxt_year, nxt_month)

    # ---- ingest_batches -----------------------------------------------
    op.create_table(
        "ingest_batches",
        sa.Column("batch_id", sa.UUID(), primary_key=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("source_token_id", sa.Text(), nullable=False),
        sa.Column("end_customer_id", sa.Text(), nullable=False),
        sa.Column("span_count", sa.Integer(), nullable=False),
        sa.Column("accepted", sa.Integer(), nullable=False),
        sa.Column("rejected", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
    )
    op.create_index("idx_batches_customer_time", "ingest_batches", ["end_customer_id", sa.text("received_at DESC")])

    # ---- quarantine ---------------------------------------------------
    op.execute("""
        CREATE TABLE quarantine (
            quarantine_id   UUID PRIMARY KEY,
            raw_body        JSONB NOT NULL,
            reason_code     TEXT NOT NULL,
            reason_detail   TEXT,
            end_customer_id TEXT,
            received_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            source_token_id TEXT,
            batch_id        UUID
        )
    """)
    op.execute("CREATE INDEX idx_quarantine_customer_time ON quarantine (end_customer_id, received_at DESC)")
    op.execute("CREATE INDEX idx_quarantine_reason        ON quarantine (reason_code)")

    # ---- attestations -------------------------------------------------
    op.execute("""
        CREATE TABLE attestations (
            attestation_id     UUID PRIMARY KEY,
            end_customer_id    TEXT NOT NULL,
            window_start       TIMESTAMPTZ NOT NULL,
            window_end         TIMESTAMPTZ NOT NULL,
            trajectory_count   INTEGER NOT NULL,
            leaf_hashes_count  INTEGER NOT NULL,
            merkle_root        CHAR(64) NOT NULL,
            signature          BYTEA NOT NULL,
            signing_key_id     TEXT NOT NULL,
            created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            exported_at        TIMESTAMPTZ
        )
    """)
    op.execute("CREATE INDEX idx_attest_customer_window ON attestations (end_customer_id, window_end DESC)")

    op.execute("""
        CREATE TABLE attestation_leaves (
            attestation_id        UUID NOT NULL REFERENCES attestations(attestation_id),
            trajectory_id         CHAR(32) NOT NULL,
            trajectory_root_hash  CHAR(64) NOT NULL,
            leaf_index            INTEGER NOT NULL,
            PRIMARY KEY (attestation_id, trajectory_id)
        )
    """)
    op.execute("CREATE INDEX idx_attest_leaves_traj ON attestation_leaves (trajectory_id)")

    # ---- access_log ---------------------------------------------------
    op.execute("""
        CREATE TABLE access_log (
            access_id        UUID PRIMARY KEY,
            actor_token_id   TEXT NOT NULL,
            actor_role       TEXT NOT NULL,
            end_customer_id  TEXT NOT NULL,
            action           TEXT NOT NULL,
            target_kind      TEXT NOT NULL,
            target_id        TEXT NOT NULL,
            at               TIMESTAMPTZ NOT NULL DEFAULT now(),
            request_id       TEXT,
            ip               INET,
            user_agent       TEXT
        )
    """)
    op.execute("CREATE INDEX idx_access_actor_time    ON access_log (actor_token_id, at DESC)")
    op.execute("CREATE INDEX idx_access_customer_time ON access_log (end_customer_id, at DESC)")
    op.execute("CREATE INDEX idx_access_target        ON access_log (target_kind, target_id)")

    # ---- api_tokens ---------------------------------------------------
    op.create_table(
        "api_tokens",
        sa.Column("token_id", sa.Text(), primary_key=True),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("end_customer_id", sa.Text(), nullable=True),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ---- litigation_holds ---------------------------------------------
    op.create_table(
        "litigation_holds",
        sa.Column("trajectory_id", sa.CHAR(32), primary_key=True),
        sa.Column("placed_by", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("placed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_by", sa.Text(), nullable=True),
    )
    op.execute("CREATE INDEX idx_holds_active ON litigation_holds (placed_at) WHERE released_at IS NULL")

    # ---- retention_operations -----------------------------------------
    op.create_table(
        "retention_operations",
        sa.Column("op_id", sa.UUID(), primary_key=True),
        sa.Column("op_kind", sa.Text(), nullable=False),
        sa.Column("target", sa.Text(), nullable=False),
        sa.Column("spans_affected", sa.Integer(), nullable=False),
        sa.Column("retention_class", sa.Text(), nullable=False),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("executed_by", sa.Text(), nullable=False),
    )


def _create_partition(year: int, month: int) -> None:
    name = f"spans_{year:04d}_{month:02d}"
    nxt_year, nxt_month = (year, month + 1) if month < 12 else (year + 1, 1)
    op.execute(
        f"CREATE TABLE IF NOT EXISTS {name} PARTITION OF spans "
        f"FOR VALUES FROM ('{year:04d}-{month:02d}-01') "
        f"TO ('{nxt_year:04d}-{nxt_month:02d}-01')"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS retention_operations")
    op.execute("DROP TABLE IF EXISTS litigation_holds")
    op.execute("DROP TABLE IF EXISTS api_tokens")
    op.execute("DROP TABLE IF EXISTS access_log")
    op.execute("DROP TABLE IF EXISTS attestation_leaves")
    op.execute("DROP TABLE IF EXISTS attestations")
    op.execute("DROP TABLE IF EXISTS quarantine")
    op.execute("DROP TABLE IF EXISTS ingest_batches")
    op.execute("DROP TRIGGER IF EXISTS trg_spans_no_delete ON spans")
    op.execute("DROP TRIGGER IF EXISTS trg_spans_no_update ON spans")
    op.execute("DROP FUNCTION IF EXISTS spans_block_update()")
    op.execute("DROP FUNCTION IF EXISTS spans_block_delete()")
    op.execute("DROP TABLE IF EXISTS spans CASCADE")
