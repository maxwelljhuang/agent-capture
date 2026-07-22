"""Hold-queue ORM models.

A *hold* is a deferred gated action awaiting a human decision (the
fail-to-human path). Unlike the ledger's append-only span store, this is a
small mutable work queue: rows transition pending → approved/rejected/
timed_out. Status transitions are guarded in the repository and logged.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Hold(Base):
    """One deferred gated action awaiting human review."""

    __tablename__ = "enforcement_hold"

    hold_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    end_customer_id: Mapped[str] = mapped_column(String(255), index=True)
    trajectory_id: Mapped[str] = mapped_column(String(32), index=True)
    span_id: Mapped[str] = mapped_column(String(16))
    parent_content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    policy_name: Mapped[str] = mapped_column(String(255))
    policy_version: Mapped[str] = mapped_column(String(255))
    rule_id: Mapped[str] = mapped_column(String(255))
    proposed_action: Mapped[str] = mapped_column(Text, default="")
    # pending | approved | rejected | timed_out | aborted
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approver_token_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    decision_reason: Mapped[str] = mapped_column(Text, default="")
