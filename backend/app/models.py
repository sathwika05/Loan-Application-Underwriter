from datetime import datetime

from sqlalchemy import (ARRAY, BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Index,
                        Integer, Numeric, String, Text, func, text)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base

LOAN_TYPES = ("mortgage", "auto", "personal", "business")
REGIONS = ("East", "West", "Midwest", "South")
PRIORITIES = ("normal", "high", "urgent")


class Underwriter(Base):
    __tablename__ = "underwriter"
    __table_args__ = (
        CheckConstraint("max_amount > 0", name="ck_uw_max_amount"),
        CheckConstraint("max_caseload > 0", name="ck_uw_max_caseload"),
        CheckConstraint("quality_score BETWEEN 0 AND 1", name="ck_uw_quality"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    loan_types: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    regions: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    max_amount: Mapped[float] = mapped_column(Numeric(14, 2, asdecimal=False), nullable=False)
    max_caseload: Mapped[int] = mapped_column(Integer, nullable=False)
    avg_turnaround_hours: Mapped[float] = mapped_column(Numeric(6, 2, asdecimal=False), nullable=False, default=24)
    quality_score: Mapped[float] = mapped_column(Numeric(3, 2, asdecimal=False), nullable=False, default=0.8)
    profile: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))


class LoanApplication(Base):
    __tablename__ = "loan_application"
    __table_args__ = (
        CheckConstraint(f"loan_type IN {LOAN_TYPES}", name="ck_app_loan_type"),
        CheckConstraint("amount > 0", name="ck_app_amount"),
        CheckConstraint("credit_score BETWEEN 300 AND 850", name="ck_app_credit"),
        CheckConstraint(f"priority IN {PRIORITIES}", name="ck_app_priority"),
        CheckConstraint("status IN ('NEW','ROUTED','QUEUED','COMPLETED')", name="ck_app_status"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    applicant_name: Mapped[str] = mapped_column(String(100), nullable=False)
    submitter: Mapped[str] = mapped_column(String(100), nullable=False)
    loan_type: Mapped[str] = mapped_column(String(20), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(14, 2, asdecimal=False), nullable=False)
    region: Mapped[str] = mapped_column(String(20), nullable=False)
    credit_score: Mapped[int | None] = mapped_column(Integer)
    priority: Mapped[str] = mapped_column(String(10), nullable=False, default="normal")
    notes: Mapped[str | None] = mapped_column(Text)
    sla_due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="NEW")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Assignment(Base):
    __tablename__ = "assignment"
    __table_args__ = (
        CheckConstraint("method IN ('AUTO','OVERRIDE')", name="ck_asg_method"),
        CheckConstraint("method <> 'OVERRIDE' OR length(coalesce(override_reason,'')) >= 10",
                        name="ck_asg_override_reason"),
        # NFR1: at most one active assignment per application, enforced by the database
        Index("uq_active_assignment", "application_id", unique=True, postgresql_where=text("is_active")),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("loan_application.id"), nullable=False)
    underwriter_id: Mapped[int] = mapped_column(ForeignKey("underwriter.id"), nullable=False)
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    decision: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    override_reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False, default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditEvent(Base):
    __tablename__ = "audit_event"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    application_id: Mapped[int | None] = mapped_column(ForeignKey("loan_application.id"))
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    actor: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Notification(Base):
    __tablename__ = "notification"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recipient: Mapped[str] = mapped_column(String(200), nullable=False)
    application_id: Mapped[int | None] = mapped_column(ForeignKey("loan_application.id"))
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Outcome(Base):
    """What happened after an underwriter worked a case. Feeds adaptive learning."""
    __tablename__ = "outcome"
    __table_args__ = (
        CheckConstraint("decision IN ('approved','declined')", name="ck_outcome_decision"),
        CheckConstraint("turnaround_hours > 0", name="ck_outcome_turnaround"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assignment_id: Mapped[int] = mapped_column(ForeignKey("assignment.id"), nullable=False, unique=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("loan_application.id"), nullable=False)
    underwriter_id: Mapped[int] = mapped_column(ForeignKey("underwriter.id"), nullable=False)
    decision: Mapped[str] = mapped_column(String(10), nullable=False)
    turnaround_hours: Mapped[float] = mapped_column(Numeric(8, 2, asdecimal=False), nullable=False)
    sla_met: Mapped[bool] = mapped_column(Boolean, nullable=False)
    rework: Mapped[bool] = mapped_column(Boolean, nullable=False)
    recorded_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RoutingWeights(Base):
    """Versioned scoring weights; the highest version is used for routing."""
    __tablename__ = "routing_weights"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    weights: Mapped[dict] = mapped_column(JSONB, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    stats: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
