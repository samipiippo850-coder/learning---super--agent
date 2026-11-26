"""Ticket / Assignment runtime: lifecycle, idempotency, retries, reports."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.constants import AssignmentStatus, TicketStatus, WorkerType
from app.models import Assignment, HumanReview, Thread, Ticket, WorkerReport
from app.agents.workers import TICKET_WORKER, WORKERS


class TicketTerminalError(RuntimeError):
    pass


def get_or_create_thread(db: Session, user_id: int, subject_id: int | None, topic: str) -> Thread:
    thread = db.scalar(
        select(Thread)
        .where(Thread.user_id == user_id, Thread.topic == topic)
        .order_by(Thread.id.desc())
    )
    if thread:
        return thread
    thread = Thread(user_id=user_id, subject_id=subject_id, topic=topic, status="active")
    db.add(thread)
    db.flush()
    return thread


def create_ticket(
    db: Session,
    user_id: int,
    ticket_type: str,
    title: str,
    *,
    subject_id: int | None = None,
    priority: float = 0.5,
    payload: dict | None = None,
    thread_id: int | None = None,
    idempotency_key: str = "",
    trigger_id: int | None = None,
) -> Ticket:
    payload = payload or {}
    if not idempotency_key:
        digest = hashlib.sha256(
            json.dumps([ticket_type, title, subject_id, payload], sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:24]
        idempotency_key = f"{ticket_type}:{digest}"
    existing = db.scalar(
        select(Ticket).where(Ticket.idempotency_key == idempotency_key, Ticket.status.notin_(TicketStatus.TERMINAL))
    )
    if existing is not None:
        return existing
    ticket = Ticket(
        user_id=user_id,
        thread_id=thread_id,
        subject_id=subject_id,
        ticket_type=ticket_type,
        title=title,
        status=TicketStatus.OPEN,
        priority=priority,
        mandate={
            "can_write": ["derived_tables"],
            "cannot_write": ["sources", "source_documents", "raw_exam_answers"],
            "budget_seconds": 120,
            "requires_human_review_allowed": True,
        },
        payload=payload,
        idempotency_key=idempotency_key,
        trigger_id=trigger_id,
    )
    db.add(ticket)
    db.flush()
    return ticket


def worker_for(ticket: Ticket) -> str:
    if ticket.payload.get("worker_type"):
        return ticket.payload["worker_type"]
    return TICKET_WORKER.get(ticket.ticket_type, WorkerType.EXAM_ANALYST)


def run_ticket(
    db: Session,
    ticket: Ticket,
    worker_type: str | None = None,
    params: dict | None = None,
    *,
    max_attempts: int = 2,
) -> tuple[Assignment, WorkerReport]:
    if ticket.status in TicketStatus.TERMINAL:
        raise TicketTerminalError(f"ticket {ticket.id} already {ticket.status}")
    worker = worker_type or worker_for(ticket)
    if worker not in WORKERS:
        raise ValueError(f"unknown worker type: {worker}")

    merged = {**(ticket.payload or {}), **(params or {})}
    idempotency_key = hashlib.sha256(
        json.dumps([ticket.id, worker, merged], sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:32]

    existing = db.scalar(
        select(Assignment).where(
            Assignment.idempotency_key == idempotency_key,
            Assignment.status.in_([AssignmentStatus.READY, AssignmentStatus.RUNNING]),
        )
    )
    if existing is not None:
        report = db.scalar(select(WorkerReport).where(WorkerReport.assignment_id == existing.id).order_by(WorkerReport.id.desc()))
        if report is not None:
            return existing, report

    attempt = db.scalar(select(Assignment).where(Assignment.ticket_id == ticket.id, Assignment.worker_type == worker))
    attempt_no = len(list(db.scalars(select(Assignment).where(Assignment.ticket_id == ticket.id)).all())) + 1
    assignment = Assignment(
        ticket_id=ticket.id,
        worker_type=worker,
        status=AssignmentStatus.RUNNING,
        attempt=attempt_no,
        input={"worker_type": worker, "params": merged, "ticket_type": ticket.ticket_type},
        idempotency_key=idempotency_key,
        started_at=datetime.now(UTC),
        heartbeat_at=datetime.now(UTC),
    )
    db.add(assignment)
    ticket.status = TicketStatus.RUNNING
    db.commit()

    try:
        result = WORKERS[worker](db, ticket.user_id, ticket.subject_id, merged)
        status = result.get("status") or AssignmentStatus.READY
        assignment.status = status if status in AssignmentStatus.ALL else AssignmentStatus.READY
        assignment.output = result.get("payload") or {}
        report = WorkerReport(
            ticket_id=ticket.id,
            assignment_id=assignment.id,
            status=assignment.status,
            summary=result.get("summary") or "",
            output_refs=result.get("output_refs") or [],
            evidence=result.get("evidence") or [],
            confidence=float(result.get("confidence") or 0.5),
            requires_human_review=bool(result.get("requires_human_review")),
            next_actions=result.get("next_actions") or [],
            payload=result.get("payload") or {},
        )
        db.add(report)
        db.flush()
        assignment.ended_at = datetime.now(UTC)
        assignment.heartbeat_at = datetime.now(UTC)

        if report.requires_human_review:
            ticket.status = TicketStatus.WAITING
            db.add(
                HumanReview(
                    ticket_id=ticket.id,
                    report_id=report.id,
                    question=f"请核验 {worker} 的输出：{report.summary[:120]}",
                    options=["approve", "reject"],
                    status="pending",
                    payload={"report_id": report.id},
                )
            )
        elif assignment.status == AssignmentStatus.BLOCKED:
            ticket.status = TicketStatus.BLOCKED
        elif assignment.status == AssignmentStatus.WAITING:
            ticket.status = TicketStatus.WAITING
        else:
            ticket.status = TicketStatus.READY
        db.commit()
        return assignment, report
    except Exception as exc:  # pragma: no cover - defensive
        db.rollback()
        assignment = db.get(Assignment, assignment.id) or assignment
        assignment.status = AssignmentStatus.FAILED
        assignment.error = str(exc)[:2000]
        assignment.ended_at = datetime.now(UTC)
        ticket = db.get(Ticket, ticket.id)
        if ticket:
            ticket.status = TicketStatus.FAILED
        report = WorkerReport(
            ticket_id=ticket.id if ticket else None,
            assignment_id=assignment.id,
            status=AssignmentStatus.FAILED,
            summary=f"Worker 执行失败: {exc}",
            output_refs=[],
            evidence=[],
            confidence=0.0,
            requires_human_review=True,
            next_actions=["检查输入参数后重试（重新创建 Assignment）"],
            payload={"error": str(exc)},
        )
        db.add(report)
        # retry once automatically when attempts remain
        if assignment.attempt < max_attempts and ticket is not None:
            ticket.status = TicketStatus.PENDING
        db.commit()
        return assignment, report


def cancel_ticket(db: Session, ticket: Ticket) -> Ticket:
    ticket.status = TicketStatus.DROPPED
    db.commit()
    return ticket


def serialize_assignment(assignment: Assignment) -> dict:
    return {
        "id": assignment.id,
        "ticket_id": assignment.ticket_id,
        "worker_type": assignment.worker_type,
        "status": assignment.status,
        "attempt": assignment.attempt,
        "error": assignment.error,
        "started_at": assignment.started_at.isoformat() if assignment.started_at else None,
        "ended_at": assignment.ended_at.isoformat() if assignment.ended_at else None,
        "heartbeat_at": assignment.heartbeat_at.isoformat() if assignment.heartbeat_at else None,
        "output": assignment.output,
    }


def serialize_report(report: WorkerReport) -> dict:
    return {
        "id": report.id,
        "ticket_id": report.ticket_id,
        "assignment_id": report.assignment_id,
        "status": report.status,
        "summary": report.summary,
        "output_refs": report.output_refs,
        "evidence": report.evidence,
        "confidence": report.confidence,
        "requires_human_review": report.requires_human_review,
        "next_actions": report.next_actions,
        "payload": report.payload,
        "created_at": report.created_at.isoformat(),
    }


def serialize_ticket(db: Session, ticket: Ticket) -> dict:
    latest_report = db.scalar(
        select(WorkerReport).where(WorkerReport.ticket_id == ticket.id).order_by(WorkerReport.id.desc())
    )
    thread = db.get(Thread, ticket.thread_id) if ticket.thread_id else None
    return {
        "id": ticket.id,
        "thread_id": ticket.thread_id,
        "thread_topic": thread.topic if thread else None,
        "user_id": ticket.user_id,
        "subject_id": ticket.subject_id,
        "ticket_type": ticket.ticket_type,
        "title": ticket.title,
        "status": ticket.status,
        "priority": ticket.priority,
        "mandate": ticket.mandate,
        "payload": ticket.payload,
        "trigger_id": ticket.trigger_id,
        "created_at": ticket.created_at.isoformat(),
        "updated_at": ticket.updated_at.isoformat(),
        "latest_report": serialize_report(latest_report) if latest_report else None,
        "assignment_count": len(ticket.assignments),
    }
