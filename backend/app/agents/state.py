"""Router / worker state machines for ExamOS (LangGraph).

Design follows the Super-Agent domain model: a persistent Router thread creates
Tickets, each execution is an Assignment with its own worker context, and every
assignment emits a structured Report. Workers never share a conversation
history; they receive ``input_refs`` + ``scope`` + ``acceptance_criteria``.
"""

from __future__ import annotations

from typing import Any, TypedDict


class RouterState(TypedDict, total=False):
    message: str
    user_id: int
    subject_id: int | None
    subject_type: str | None
    params: dict[str, Any]
    ticket_type: str
    worker_type: str
    ticket_id: int
    report_id: int
    reply: str
    trace: list[dict[str, Any]]
    requires_human_review: bool
    error: str


class WorkerInput(TypedDict, total=False):
    ticket_id: int
    assignment_id: int
    worker_type: str
    subject: str
    task_type: str
    input_refs: list[str]
    scope: dict[str, Any]
    acceptance_criteria: list[str]
    params: dict[str, Any]


class WorkerOutput(TypedDict, total=False):
    ticket_id: int
    assignment_id: int
    status: str
    summary: str
    output_refs: list[str]
    evidence: list[str]
    confidence: float
    requires_human_review: bool
    next_actions: list[str]
    payload: dict[str, Any]
