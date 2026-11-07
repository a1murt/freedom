"""ORM models. See docs/ARCHITECTURE.md §4 for the data model and its invariants."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Dataset(Base):
    __tablename__ = "datasets"
    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(32), default="organizer_api")
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    record_count: Mapped[int] = mapped_column(Integer, default=0)


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset_id: Mapped[int | None] = mapped_column(ForeignKey("datasets.id"), nullable=True)
    channel: Mapped[str] = mapped_column(String(32))  # "agent chat" | "voice call"
    language: Mapped[str] = mapped_column(String(8))  # RU | KZ | EN
    audio_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    operator_name: Mapped[str | None] = mapped_column(String(128), nullable=True)  # detected from the greeting (judge input)
    # identity from the source system (Dynamics 365 agent display name); never fed to the judge
    agent_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    turn_count: Mapped[int] = mapped_column(Integer, default=0)
    first_response_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    terminal_event: Mapped[str | None] = mapped_column(String(32), nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    turns: Mapped[list["Turn"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="Turn.idx"
    )
    evaluations: Mapped[list["Evaluation"]] = relationship(back_populates="conversation")


class Turn(Base):
    __tablename__ = "turns"
    __table_args__ = (UniqueConstraint("conversation_id", "idx"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    idx: Mapped[int] = mapped_column(Integer)
    timestamp_raw: Mapped[str] = mapped_column(String(64))
    offset_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    speaker: Mapped[str] = mapped_column(String(16))  # client | operator | bot | system
    text: Mapped[str] = mapped_column(Text)
    is_duplicate: Mapped[bool] = mapped_column(Boolean, default=False)
    segment: Mapped[str] = mapped_column(String(16), default="pre")  # pre | operator | internal | post
    conversation: Mapped[Conversation] = relationship(back_populates="turns")


class Checklist(Base):
    __tablename__ = "checklists"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    versions: Mapped[list["ChecklistVersion"]] = relationship(
        back_populates="checklist", order_by="ChecklistVersion.version_no"
    )


class ChecklistVersion(Base):
    __tablename__ = "checklist_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    checklist_id: Mapped[int] = mapped_column(ForeignKey("checklists.id"), index=True)
    version_no: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    change_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    scoring_rules: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    sections: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_from_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    checklist: Mapped[Checklist] = relationship(back_populates="versions")
    criteria: Mapped[list["Criterion"]] = relationship(
        back_populates="version", cascade="all, delete-orphan", order_by="Criterion.order"
    )


class Criterion(Base):
    __tablename__ = "criteria"
    id: Mapped[int] = mapped_column(primary_key=True)
    checklist_version_id: Mapped[int] = mapped_column(ForeignKey("checklist_versions.id"), index=True)
    code: Mapped[str] = mapped_column(String(16))
    order: Mapped[int] = mapped_column(Integer, default=0)
    section: Mapped[str | None] = mapped_column(String(16), nullable=True)
    title: Mapped[str] = mapped_column(String(200))
    instruction: Mapped[str] = mapped_column(Text)
    answer_type: Mapped[str] = mapped_column(String(16))  # binary | scale
    options: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    max_points: Mapped[float] = mapped_column(Float, default=1)
    weight: Mapped[float] = mapped_column(Float, default=0)
    na_allowed: Mapped[bool] = mapped_column(Boolean, default=False)
    na_condition: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_critical: Mapped[bool] = mapped_column(Boolean, default=False)
    critical_value: Mapped[str | None] = mapped_column(String(16), nullable=True)
    channel_overrides: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    judge_spec: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    version: Mapped[ChecklistVersion] = relationship(back_populates="criteria")


class Evaluation(Base):
    __tablename__ = "evaluations"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    checklist_version_id: Mapped[int] = mapped_column(ForeignKey("checklist_versions.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued|running|completed|failed
    provider: Mapped[str] = mapped_column(String(32), default="anthropic")
    mode: Mapped[str] = mapped_column(String(16), default="single")  # single | ensemble | partial
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    total_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_total_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    critical_error: Mapped[bool] = mapped_column(Boolean, default=False)
    sections: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    job_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    conversation: Mapped[Conversation] = relationship(back_populates="evaluations")
    criteria: Mapped[list["EvaluationCriterion"]] = relationship(
        back_populates="evaluation", cascade="all, delete-orphan", order_by="EvaluationCriterion.id"
    )
    traces: Mapped[list["LlmTrace"]] = relationship(back_populates="evaluation", cascade="all, delete-orphan")


class EvaluationCriterion(Base):
    __tablename__ = "evaluation_criteria"
    id: Mapped[int] = mapped_column(primary_key=True)
    evaluation_id: Mapped[int] = mapped_column(ForeignKey("evaluations.id", ondelete="CASCADE"), index=True)
    criterion_id: Mapped[int] = mapped_column(ForeignKey("criteria.id"))
    code: Mapped[str] = mapped_column(String(16))
    applicable: Mapped[bool] = mapped_column(Boolean, default=True)
    na_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer: Mapped[Any] = mapped_column(JSON, nullable=True)  # "yes" | "no" | number
    score: Mapped[float | None] = mapped_column(Float, nullable=True)  # points
    max_points: Mapped[float] = mapped_column(Float, default=1)
    weight: Mapped[float] = mapped_column(Float, default=0)
    weight_applied: Mapped[float] = mapped_column(Float, default=0)
    earned: Mapped[float] = mapped_column(Float, default=0)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    checked_span: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    flags: Mapped[list[str]] = mapped_column(JSON, default=list)
    claims: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    votes: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    review_state: Mapped[str] = mapped_column(String(16), default="none")  # none|under_review|confirmed|changed
    final_answer: Mapped[Any] = mapped_column(JSON, nullable=True)
    final_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    final_earned: Mapped[float | None] = mapped_column(Float, nullable=True)
    evaluation: Mapped[Evaluation] = relationship(back_populates="criteria")
    appeals: Mapped[list["Appeal"]] = relationship(back_populates="evaluation_criterion", order_by="Appeal.id")


class Appeal(Base):
    __tablename__ = "appeals"
    id: Mapped[int] = mapped_column(primary_key=True)
    evaluation_criterion_id: Mapped[int] = mapped_column(ForeignKey("evaluation_criteria.id", ondelete="CASCADE"), index=True)
    comment: Mapped[str] = mapped_column(Text)
    author_label: Mapped[str] = mapped_column(String(64), default="QA analyst")
    author_role: Mapped[str | None] = mapped_column(String(32), nullable=True)  # operator | analyst | reviewer | lead | ...
    author_name: Mapped[str | None] = mapped_column(String(128), nullable=True)  # e.g. operator name "Алина"
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | decided
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    evaluation_criterion: Mapped[EvaluationCriterion] = relationship(back_populates="appeals")
    decision: Mapped["Decision | None"] = relationship(back_populates="appeal", uselist=False)


class Decision(Base):
    __tablename__ = "decisions"
    id: Mapped[int] = mapped_column(primary_key=True)
    appeal_id: Mapped[int] = mapped_column(ForeignKey("appeals.id", ondelete="CASCADE"), unique=True)
    action: Mapped[str] = mapped_column(String(16))  # confirm | change
    new_answer: Mapped[Any] = mapped_column(JSON, nullable=True)
    new_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str] = mapped_column(Text)
    reviewer_label: Mapped[str] = mapped_column(String(64), default="Reviewer")
    reviewer_role: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    appeal: Mapped[Appeal] = relationship(back_populates="decision")


class LlmTrace(Base):
    __tablename__ = "llm_traces"
    id: Mapped[int] = mapped_column(primary_key=True)
    evaluation_id: Mapped[int] = mapped_column(ForeignKey("evaluations.id", ondelete="CASCADE"), index=True)
    evaluation_criterion_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    criterion_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    kind: Mapped[str] = mapped_column(String(16), default="judge")  # judge | arbiter | compiler
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    request: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    response: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    evaluation: Mapped[Evaluation] = relationship(back_populates="traces")


class AudioAsset(Base):
    __tablename__ = "audio_assets"
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), primary_key=True)
    local_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    features: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    type: Mapped[str] = mapped_column(String(16), default="evaluate")
    status: Mapped[str] = mapped_column(String(16), default="queued")
    total: Mapped[int] = mapped_column(Integer, default=0)
    done: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    log: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FewshotExample(Base):
    __tablename__ = "fewshot_examples"
    id: Mapped[int] = mapped_column(primary_key=True)
    criterion_code: Mapped[str] = mapped_column(String(16), index=True)
    language: Mapped[str] = mapped_column(String(8))
    channel: Mapped[str] = mapped_column(String(32))
    transcript_excerpt: Mapped[str] = mapped_column(Text)
    verdict: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    source: Mapped[str] = mapped_column(String(16), default="review")  # review | gold
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=True)


class AuditEvent(Base):
    """Who did what, when (process correctness, enterprise audit trail). Written by cqa.audit."""

    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_events_entity", "entity_type", "entity_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    actor_role: Mapped[str] = mapped_column(String(32), default="demo")
    actor_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    action: Mapped[str] = mapped_column(String(64), index=True)  # e.g. appeal.create, decision.create, checklist.version.create
    entity_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # = the X-Request-ID response header
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)


class GoldLabel(Base):
    """Human reference label for one criterion of one conversation (calibration, AI-vs-human agreement)."""

    __tablename__ = "gold_labels"
    __table_args__ = (UniqueConstraint("conversation_id", "criterion_code", "labeler"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    criterion_code: Mapped[str] = mapped_column(String(16))
    value: Mapped[Any] = mapped_column(JSON, nullable=True)  # "yes" | "no" | number | "N/A"
    labeler: Mapped[str] = mapped_column(String(64), default="team")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    split: Mapped[str | None] = mapped_column(String(16), nullable=True)  # train | validation | None
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
