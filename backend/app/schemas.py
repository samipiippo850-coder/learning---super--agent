"""Pydantic request/response models for the ExamOS HTTP API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# profile / system
# --------------------------------------------------------------------------- #
class SystemSeedRequest(BaseModel):
    force: bool = False


class SystemResetRequest(BaseModel):
    confirm: bool = False


class ProfileUpdate(BaseModel):
    daily_minutes: int | None = Field(default=None, ge=30, le=1440)
    university: str | None = None
    major: str | None = None
    exam_year: int | None = Field(default=None, ge=2000, le=2100)
    exam_date: datetime | None = None


# --------------------------------------------------------------------------- #
# sources / documents
# --------------------------------------------------------------------------- #
class SourceCreate(BaseModel):
    key: str = Field(min_length=1, max_length=128)
    source_type: str = "user_provided"
    title: str = ""
    url: str = ""
    publisher: str = ""
    license: str = ""
    trust_score: float = Field(default=0.6, ge=0.0, le=1.0)
    retrieved_at: datetime | None = None
    notes: str = ""
    meta: dict[str, Any] = Field(default_factory=dict)


class DocumentImport(BaseModel):
    source_key: str
    filename: str = ""
    doc_type: Literal["paper", "textbook", "syllabus", "notes"] = "paper"
    content_text: str = ""
    subject_id: int | None = None
    subject_type: str | None = None
    paper_year: int | None = None
    paper_type: Literal["real", "mock", "synthetic"] = "real"
    title: str = ""


# --------------------------------------------------------------------------- #
# questions
# --------------------------------------------------------------------------- #
class TransformRequest(BaseModel):
    question_id: int
    level: Literal["L1", "L2", "L3", "L4"] = "L1"
    count: int = Field(default=1, ge=1, le=3)


# --------------------------------------------------------------------------- #
# attempts / tests
# --------------------------------------------------------------------------- #
class AttemptCreate(BaseModel):
    question_id: int
    answer_text: str = ""
    duration_sec: float = Field(default=0.0, ge=0)
    hint_count: int = Field(default=0, ge=0, le=20)
    confidence_self: float = Field(default=0.5, ge=0.0, le=1.0)
    is_independent: bool = True
    source: Literal["practice", "test", "review"] = "practice"


class TestGenerateRequest(BaseModel):
    subject_id: int | None = None
    subject_type: str | None = None
    test_type: Literal["micro", "mixed", "stage", "mock", "diagnostic"] = "micro"
    duration_min: int | None = Field(default=None, ge=5, le=240)
    focus_knowledge_ids: list[int] = Field(default_factory=list)


class TestAnswer(BaseModel):
    question_id: int
    answer_text: str = ""
    duration_sec: float = Field(default=0.0, ge=0)
    hint_count: int = Field(default=0, ge=0, le=20)
    confidence_self: float = Field(default=0.5, ge=0.0, le=1.0)
    is_independent: bool = True


class TestSubmitRequest(BaseModel):
    answers: list[TestAnswer] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# memory
# --------------------------------------------------------------------------- #
class MemoryCardCreate(BaseModel):
    subject_id: int
    knowledge_id: int | None = None
    card_type: str = "definition"
    front: str
    back: str
    source_ref: str = ""


class MemoryReviewRequest(BaseModel):
    quality: int = Field(ge=0, le=5)
    response_text: str = ""


class MemoryBuildRequest(BaseModel):
    subject_id: int | None = None
    subject_type: str | None = None
    knowledge_ids: list[int] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# events / tickets / agent
# --------------------------------------------------------------------------- #
class EventCreate(BaseModel):
    trigger_type: str
    event_key: str | None = None
    subject_id: int | None = None
    subject_type: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class TicketCreate(BaseModel):
    ticket_type: str
    title: str
    subject_id: int | None = None
    priority: float = Field(default=0.5, ge=0.0, le=1.0)
    payload: dict[str, Any] = Field(default_factory=dict)


class TicketRunRequest(BaseModel):
    worker_type: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class AgentRunRequest(BaseModel):
    message: str
    subject_id: int | None = None
    subject_type: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class HumanReviewCreate(BaseModel):
    ticket_id: int | None = None
    question: str
    options: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)


class HumanReviewResolve(BaseModel):
    decision: Literal["approve", "reject"]
    comment: str = ""


class WatchdogScanRequest(BaseModel):
    auto_fix: bool = True


class TaskUpdate(BaseModel):
    status: Literal["TODO", "DOING", "DONE", "SKIPPED", "EXPIRED"]


class PlanRegenerateRequest(BaseModel):
    date: str | None = None
    subject_id: int | None = None
    budget_minutes: int | None = Field(default=None, ge=30, le=1440)

# --------------------------------------------------------------------------- #
# studio: subjects / books / workflows / autonomy
# --------------------------------------------------------------------------- #
class SubjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=96)
    subject_type: str = "custom"
    total_score: float = Field(default=150, ge=10, le=500)
    major: str = ""


class BookCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    author: str = ""
    publisher: str = ""
    subject_type: str = "custom"
    major: str = ""
    level: str = "基础"
    tags: list[str] = []
    description: str = ""
    source_url: str = ""


class BookSearchRequest(BaseModel):
    query: str = ""
    subject_type: str | None = None
    major: str | None = None
    limit: int = Field(default=12, ge=1, le=50)


class WorkflowCreate(BaseModel):
    name: str
    description: str = ""
    subject_id: int | None = None
    steps: list[dict] = []
    source: str = "user"


class WorkflowComposeRequest(BaseModel):
    text: str
    save: bool = False


class WorkflowUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    steps: list[dict] | None = None
    status: str | None = None
    subject_id: int | None = None


class AutonomyUpdate(BaseModel):
    allow_agent_add_subjects: bool | None = None
    allow_agent_search_books: bool | None = None
    allow_agent_edit_workflow: bool | None = None
    allow_agent_remove_subjects: bool | None = None
    require_confirm_for_delete: bool | None = None
