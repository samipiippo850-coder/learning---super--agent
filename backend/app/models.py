"""SQLAlchemy domain model for ExamOS.

The schema is intentionally portable (no PG-specific column types) so the same
code runs on SQLite for local development and PostgreSQL in production.
pgvector-specific indexing lives in ``app/services/vectors.py`` behind an
adapter interface.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_uid() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


# --------------------------------------------------------------------------- #
# Identity / exam target
# --------------------------------------------------------------------------- #
class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, default="default")
    display_name: Mapped[str] = mapped_column(String(128), default="考生")
    profile: Mapped[dict] = mapped_column(JSON, default=dict)
    daily_minutes: Mapped[int] = mapped_column(Integer, default=240)

    targets: Mapped[list[ExamTarget]] = relationship(back_populates="user", cascade="all, delete-orphan")


class ExamTarget(TimestampMixin, Base):
    __tablename__ = "exam_targets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    university: Mapped[str] = mapped_column(String(128), default="")
    major: Mapped[str] = mapped_column(String(128), default="")
    exam_year: Mapped[int] = mapped_column(Integer, default=0)
    exam_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")

    user: Mapped[User] = relationship(back_populates="targets")
    subjects: Mapped[list[ExamSubject]] = relationship(back_populates="target", cascade="all, delete-orphan")


class ExamSubject(TimestampMixin, Base):
    __tablename__ = "exam_subjects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("exam_targets.id", ondelete="CASCADE"), index=True)
    subject_type: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(128))
    code: Mapped[str] = mapped_column(String(32), default="")
    total_score: Mapped[float] = mapped_column(Float, default=150.0)
    syllabus: Mapped[dict] = mapped_column(JSON, default=dict)

    target: Mapped[ExamTarget] = relationship(back_populates="subjects")


# --------------------------------------------------------------------------- #
# Sources / documents / papers
# --------------------------------------------------------------------------- #
class Source(TimestampMixin, Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    source_type: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(256))
    url: Mapped[str] = mapped_column(String(512), default="")
    publisher: Mapped[str] = mapped_column(String(128), default="")
    license: Mapped[str] = mapped_column(String(128), default="")
    trust_score: Mapped[float] = mapped_column(Float, default=0.5)
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    meta: Mapped[dict] = mapped_column(JSON, default=dict)

    documents: Mapped[list[SourceDocument]] = relationship(back_populates="source", cascade="all, delete-orphan")


class SourceDocument(TimestampMixin, Base):
    __tablename__ = "source_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(256), default="")
    doc_type: Mapped[str] = mapped_column(String(32), default="paper")  # paper / textbook / syllabus
    version: Mapped[str] = mapped_column(String(32), default="1.0")
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    pages: Mapped[int] = mapped_column(Integer, default=0)
    storage_path: Mapped[str] = mapped_column(String(512), default="")
    raw_text: Mapped[str] = mapped_column(Text, default="")
    parse_status: Mapped[str] = mapped_column(String(32), default="parsed")
    meta: Mapped[dict] = mapped_column(JSON, default=dict)

    source: Mapped[Source] = relationship(back_populates="documents")
    papers: Mapped[list[Paper]] = relationship(back_populates="document", cascade="all, delete-orphan")


class Paper(TimestampMixin, Base):
    __tablename__ = "papers"
    __table_args__ = (UniqueConstraint("key", name="uq_paper_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(128), index=True)
    document_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_documents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    subject_id: Mapped[int] = mapped_column(ForeignKey("exam_subjects.id", ondelete="CASCADE"), index=True)
    year: Mapped[int] = mapped_column(Integer, default=0, index=True)
    paper_type: Mapped[str] = mapped_column(String(32), default="real", index=True)
    title: Mapped[str] = mapped_column(String(256), default="")
    total_score: Mapped[float] = mapped_column(Float, default=150.0)
    exam_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)

    document: Mapped[SourceDocument | None] = relationship(back_populates="papers")
    questions: Mapped[list[Question]] = relationship(back_populates="paper", cascade="all, delete-orphan")


# --------------------------------------------------------------------------- #
# Knowledge graph
# --------------------------------------------------------------------------- #
class KnowledgeNode(TimestampMixin, Base):
    __tablename__ = "knowledge_nodes"
    __table_args__ = (UniqueConstraint("subject_id", "code", name="uq_knowledge_subject_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subject_id: Mapped[int] = mapped_column(ForeignKey("exam_subjects.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(256))
    category: Mapped[str] = mapped_column(String(32), default="Concept", index=True)
    chapter: Mapped[str] = mapped_column(String(128), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    importance: Mapped[float] = mapped_column(Float, default=0.5)  # syllabus importance 0..1
    est_minutes: Mapped[float] = mapped_column(Float, default=45.0)  # hours to reach target mastery
    syllabus_requirement: Mapped[str] = mapped_column(String(32), default="掌握")
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class KnowledgeEdge(TimestampMixin, Base):
    __tablename__ = "knowledge_edges"
    __table_args__ = (
        UniqueConstraint("source_id", "target_id", "relation_type", name="uq_knowledge_edge"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("knowledge_nodes.id", ondelete="CASCADE"), index=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("knowledge_nodes.id", ondelete="CASCADE"), index=True)
    relation_type: Mapped[str] = mapped_column(String(32), default="prerequisite", index=True)
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


# --------------------------------------------------------------------------- #
# Questions
# --------------------------------------------------------------------------- #
class Question(TimestampMixin, Base):
    __tablename__ = "questions"
    __table_args__ = (UniqueConstraint("key", name="uq_question_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(128), index=True)
    paper_id: Mapped[int] = mapped_column(ForeignKey("papers.id", ondelete="CASCADE"), index=True)
    subject_id: Mapped[int] = mapped_column(ForeignKey("exam_subjects.id", ondelete="CASCADE"), index=True)
    number: Mapped[str] = mapped_column(String(32), default="")
    question_type: Mapped[str] = mapped_column(String(32), default="calc_solve", index=True)
    content: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text, default="")
    explanation: Mapped[str] = mapped_column(Text, default="")
    score: Mapped[float] = mapped_column(Float, default=10.0)
    difficulty: Mapped[float] = mapped_column(Float, default=0.5)
    source_type: Mapped[str] = mapped_column(String(32), default="real", index=True)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    parent_question_id: Mapped[int | None] = mapped_column(
        ForeignKey("questions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    transform_level: Mapped[str | None] = mapped_column(String(8), nullable=True)
    verification: Mapped[dict] = mapped_column(JSON, default=dict)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)

    paper: Mapped[Paper] = relationship(back_populates="questions")
    knowledge_links: Mapped[list[QuestionKnowledge]] = relationship(
        back_populates="question", cascade="all, delete-orphan"
    )
    attempts: Mapped[list[LearnerAttempt]] = relationship(back_populates="question")


class QuestionVersion(TimestampMixin, Base):
    __tablename__ = "question_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("questions.id", ondelete="SET NULL"), nullable=True)
    transform_type: Mapped[str] = mapped_column(String(8), default="L1")
    knowledge_combo: Mapped[list] = mapped_column(JSON, default=list)
    rationale: Mapped[str] = mapped_column(Text, default="")
    verifier: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="accepted")


class QuestionKnowledge(TimestampMixin, Base):
    __tablename__ = "question_knowledge"
    __table_args__ = (UniqueConstraint("question_id", "knowledge_id", name="uq_question_knowledge"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"), index=True)
    knowledge_id: Mapped[int] = mapped_column(ForeignKey("knowledge_nodes.id", ondelete="CASCADE"), index=True)
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    confidence: Mapped[float] = mapped_column(Float, default=0.8)
    is_core: Mapped[bool] = mapped_column(Boolean, default=True)
    role: Mapped[str] = mapped_column(String(64), default="core")

    question: Mapped[Question] = relationship(back_populates="knowledge_links")
    knowledge: Mapped[KnowledgeNode] = relationship()


# --------------------------------------------------------------------------- #
# Learner state
# --------------------------------------------------------------------------- #
class LearnerAttempt(TimestampMixin, Base):
    __tablename__ = "learner_attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"), index=True)
    test_id: Mapped[int | None] = mapped_column(
        ForeignKey("test_sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    answer_text: Mapped[str] = mapped_column(Text, default="")
    is_correct: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    score_awarded: Mapped[float] = mapped_column(Float, default=0.0)
    duration_sec: Mapped[float] = mapped_column(Float, default=0.0)
    hint_count: Mapped[int] = mapped_column(Integer, default=0)
    confidence_self: Mapped[float] = mapped_column(Float, default=0.5)
    error_type: Mapped[str] = mapped_column(String(32), default="unknown")
    is_independent: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(32), default="practice")  # practice / test / review
    meta: Mapped[dict] = mapped_column(JSON, default=dict)

    question: Mapped[Question] = relationship(back_populates="attempts")


class MasteryStateRow(TimestampMixin, Base):
    __tablename__ = "mastery_states"
    __table_args__ = (UniqueConstraint("user_id", "knowledge_id", name="uq_mastery_user_knowledge"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    knowledge_id: Mapped[int] = mapped_column(ForeignKey("knowledge_nodes.id", ondelete="CASCADE"), index=True)
    alpha: Mapped[float] = mapped_column(Float, default=2.0)
    beta: Mapped[float] = mapped_column(Float, default=8.0)
    mastery: Mapped[float] = mapped_column(Float, default=0.2)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    state: Mapped[str] = mapped_column(String(32), default="UNSEEN", index=True)
    independent_correct: Mapped[int] = mapped_column(Integer, default=0)
    independent_total: Mapped[int] = mapped_column(Integer, default=0)
    hinted_correct: Mapped[int] = mapped_column(Integer, default=0)
    hinted_total: Mapped[int] = mapped_column(Integer, default=0)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    stable_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RoiSnapshot(TimestampMixin, Base):
    __tablename__ = "roi_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    subject_id: Mapped[int] = mapped_column(ForeignKey("exam_subjects.id", ondelete="CASCADE"), index=True)
    knowledge_id: Mapped[int] = mapped_column(ForeignKey("knowledge_nodes.id", ondelete="CASCADE"), index=True)
    frequency: Mapped[float] = mapped_column(Float, default=0.0)
    score_share: Mapped[float] = mapped_column(Float, default=0.0)
    expected_gain: Mapped[float] = mapped_column(Float, default=0.0)  # ΔV in exam points
    hours: Mapped[float] = mapped_column(Float, default=1.0)
    roi: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    rank: Mapped[int] = mapped_column(Integer, default=0)
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    reasons: Mapped[dict] = mapped_column(JSON, default=dict)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class StudyTask(TimestampMixin, Base):
    __tablename__ = "study_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    subject_id: Mapped[int] = mapped_column(ForeignKey("exam_subjects.id", ondelete="CASCADE"), index=True)
    knowledge_id: Mapped[int | None] = mapped_column(
        ForeignKey("knowledge_nodes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    task_type: Mapped[str] = mapped_column(String(32), default="learn", index=True)
    title: Mapped[str] = mapped_column(String(256), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    est_minutes: Mapped[float] = mapped_column(Float, default=30.0)
    priority: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(32), default="TODO", index=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    plan_date: Mapped[str] = mapped_column(String(16), default="", index=True)  # YYYY-MM-DD
    source: Mapped[str] = mapped_column(String(64), default="roi_planner")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


class MemoryCard(TimestampMixin, Base):
    __tablename__ = "memory_cards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    knowledge_id: Mapped[int | None] = mapped_column(
        ForeignKey("knowledge_nodes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    subject_id: Mapped[int] = mapped_column(ForeignKey("exam_subjects.id", ondelete="CASCADE"), index=True)
    card_type: Mapped[str] = mapped_column(String(32), default="definition", index=True)
    front: Mapped[str] = mapped_column(Text)
    back: Mapped[str] = mapped_column(Text)
    source_ref: Mapped[str] = mapped_column(String(256), default="")
    state: Mapped[str] = mapped_column(String(32), default="UNSEEN", index=True)
    ease: Mapped[float] = mapped_column(Float, default=2.5)
    interval_days: Mapped[float] = mapped_column(Float, default=0.0)
    reps: Mapped[int] = mapped_column(Integer, default=0)
    lapses: Mapped[int] = mapped_column(Integer, default=0)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
class TestSession(TimestampMixin, Base):
    __tablename__ = "test_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    subject_id: Mapped[int] = mapped_column(ForeignKey("exam_subjects.id", ondelete="CASCADE"), index=True)
    test_type: Mapped[str] = mapped_column(String(32), default="micro", index=True)
    blueprint: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="open", index=True)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    max_score: Mapped[float] = mapped_column(Float, default=0.0)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TestResult(TimestampMixin, Base):
    __tablename__ = "test_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    test_id: Mapped[int] = mapped_column(ForeignKey("test_sessions.id", ondelete="CASCADE"), index=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"), index=True)
    result: Mapped[str] = mapped_column(String(32), default="incorrect")
    score: Mapped[float] = mapped_column(Float, default=0.0)
    error_type: Mapped[str] = mapped_column(String(32), default="unknown")
    duration_sec: Mapped[float] = mapped_column(Float, default=0.0)


class HumanReview(TimestampMixin, Base):
    __tablename__ = "human_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int | None] = mapped_column(
        ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    report_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    question: Mapped[str] = mapped_column(Text)
    options: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    decision: Mapped[str] = mapped_column(String(64), default="")
    comment: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)


# --------------------------------------------------------------------------- #
# Event engine / tickets / assignments
# --------------------------------------------------------------------------- #
class Trigger(TimestampMixin, Base):
    __tablename__ = "triggers"
    __table_args__ = (UniqueConstraint("event_key", name="uq_trigger_event_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    trigger_type: Mapped[str] = mapped_column(String(32), index=True)
    event_key: Mapped[str] = mapped_column(String(256), index=True)
    subject_id: Mapped[int | None] = mapped_column(
        ForeignKey("exam_subjects.id", ondelete="SET NULL"), nullable=True
    )
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="received", index=True)
    ticket_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Thread(TimestampMixin, Base):
    __tablename__ = "threads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    subject_id: Mapped[int | None] = mapped_column(
        ForeignKey("exam_subjects.id", ondelete="SET NULL"), nullable=True
    )
    topic: Mapped[str] = mapped_column(String(256), default="")
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    summary: Mapped[str] = mapped_column(Text, default="")


class Ticket(TimestampMixin, Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    thread_id: Mapped[int | None] = mapped_column(
        ForeignKey("threads.id", ondelete="SET NULL"), nullable=True, index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    subject_id: Mapped[int | None] = mapped_column(
        ForeignKey("exam_subjects.id", ondelete="SET NULL"), nullable=True
    )
    ticket_type: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(32), default="OPEN", index=True)
    priority: Mapped[float] = mapped_column(Float, default=0.5)
    mandate: Mapped[dict] = mapped_column(JSON, default=dict)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(256), default="", index=True)
    trigger_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    assignments: Mapped[list[Assignment]] = relationship(
        back_populates="ticket", cascade="all, delete-orphan", order_by="Assignment.id"
    )
    reports: Mapped[list[WorkerReport]] = relationship(
        back_populates="ticket", cascade="all, delete-orphan", order_by="WorkerReport.id"
    )


class Assignment(TimestampMixin, Base):
    __tablename__ = "assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    worker_type: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="PENDING", index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    input: Mapped[dict] = mapped_column(JSON, default=dict)
    output: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
    idempotency_key: Mapped[str] = mapped_column(String(256), default="", index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    ticket: Mapped[Ticket] = relationship(back_populates="assignments")


class WorkerReport(TimestampMixin, Base):
    __tablename__ = "worker_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    assignment_id: Mapped[int | None] = mapped_column(
        ForeignKey("assignments.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), default="READY")
    summary: Mapped[str] = mapped_column(Text, default="")
    output_refs: Mapped[list] = mapped_column(JSON, default=list)
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    requires_human_review: Mapped[bool] = mapped_column(Boolean, default=False)
    next_actions: Mapped[list] = mapped_column(JSON, default=list)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)

    ticket: Mapped[Ticket] = relationship(back_populates="reports")


# --------------------------------------------------------------------------- #
# Studio: book resources / study workflows / autonomy
# --------------------------------------------------------------------------- #
class BookResource(TimestampMixin, Base):
    """A textbook or study resource the learner (or the agent) pulled into scope."""

    __tablename__ = "book_resources"
    __table_args__ = (UniqueConstraint("key", name="uq_book_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(160), index=True)
    title: Mapped[str] = mapped_column(String(256))
    author: Mapped[str] = mapped_column(String(160), default="")
    publisher: Mapped[str] = mapped_column(String(160), default="")
    subject_type: Mapped[str] = mapped_column(String(64), default="custom", index=True)
    major: Mapped[str] = mapped_column(String(96), default="", index=True)
    level: Mapped[str] = mapped_column(String(32), default="基础")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    description: Mapped[str] = mapped_column(Text, default="")
    source_url: Mapped[str] = mapped_column(String(512), default="")
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    created_by: Mapped[str] = mapped_column(String(32), default="user")
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


class StudyWorkflow(TimestampMixin, Base):
    """A study pipeline (stages) that can be composed from natural language."""

    __tablename__ = "study_workflows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    subject_id: Mapped[int | None] = mapped_column(
        ForeignKey("exam_subjects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    steps: Mapped[list] = mapped_column(JSON, default=list)  # [{name, focus, minutes, task_type}]
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    source: Mapped[str] = mapped_column(String(32), default="template")  # template / nl / user
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


# --------------------------------------------------------------------------- #
# Indexes for common query paths
# --------------------------------------------------------------------------- #
Index("ix_attempts_user_created", LearnerAttempt.user_id, LearnerAttempt.created_at)
Index("ix_attempts_knowledge_via_question", LearnerAttempt.question_id, LearnerAttempt.is_correct)
Index("ix_roi_user_computed", RoiSnapshot.user_id, RoiSnapshot.computed_at)
Index("ix_task_user_date", StudyTask.user_id, StudyTask.plan_date, StudyTask.status)
Index("ix_card_user_due", MemoryCard.user_id, MemoryCard.due_at)
Index("ix_ticket_user_status", Ticket.user_id, Ticket.status)
Index("ix_assignment_ticket_status", Assignment.ticket_id, Assignment.status)
