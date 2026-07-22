"""Initial enforcement hold-queue schema.

Creates the ``enforcement_hold`` table (the fail-to-human review queue). Matches
``storage/models.Hold``; the prod schema path is ``enforcement db migrate``
(``init_db``/``create_all`` remains only for dev + tests).

Revision ID: 0001
Revises:
Create Date: 2026-06-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "enforcement_hold",
        sa.Column("hold_id", sa.String(36), primary_key=True),
        sa.Column("end_customer_id", sa.String(255), nullable=False),
        sa.Column("trajectory_id", sa.String(32), nullable=False),
        sa.Column("span_id", sa.String(16), nullable=False),
        sa.Column("parent_content_hash", sa.String(64), nullable=True),
        sa.Column("policy_name", sa.String(255), nullable=False),
        sa.Column("policy_version", sa.String(255), nullable=False),
        sa.Column("rule_id", sa.String(255), nullable=False),
        sa.Column("proposed_action", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approver_token_id", sa.String(255), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=False, server_default=""),
    )
    op.create_index("ix_enforcement_hold_end_customer_id", "enforcement_hold", ["end_customer_id"])
    op.create_index("ix_enforcement_hold_trajectory_id", "enforcement_hold", ["trajectory_id"])
    op.create_index("ix_enforcement_hold_status", "enforcement_hold", ["status"])


def downgrade() -> None:
    op.drop_index("ix_enforcement_hold_status", table_name="enforcement_hold")
    op.drop_index("ix_enforcement_hold_trajectory_id", table_name="enforcement_hold")
    op.drop_index("ix_enforcement_hold_end_customer_id", table_name="enforcement_hold")
    op.drop_table("enforcement_hold")
