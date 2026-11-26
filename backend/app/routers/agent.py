from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents import router as agent_router
from app.agents.runtime import (
    cancel_ticket,
    create_ticket,
    get_or_create_thread,
    run_ticket,
    serialize_assignment,
    serialize_report,
    serialize_ticket,
)
from app.agents.watchdog import last_findings, run_scan
from app.constants import TicketStatus, TriggerType, WorkerType
from app.db import get_db
from app.deps import current_user
from app.models import (
    Assignment,
    HumanReview,
    Thread,
    Ticket,
    Trigger,
    User,
    WorkerReport,
)
from app.schemas import (
    AgentRunRequest,
    EventCreate,
    HumanReviewCreate,
    HumanReviewResolve,
    TicketCreate,
    TicketRunRequest,
    WatchdogScanRequest,
)
from app.services.util import resolve_subject

router = APIRouter(tags=["agent"])

TRIGGER_ROUTES: dict[str, tuple[str, str]] = {
    TriggerType.NEW_PAPER: ("analyze_exam_frequency", WorkerType.EXAM_ANALYST),
    TriggerType.NEW_SYLLABUS: ("analyze_exam_frequency", WorkerType.EXAM_ANALYST),
    TriggerType.NEW_ATTEMPT: ("diagnose_errors", WorkerType.DIAGNOSTICIAN),
    TriggerType.REPEATED_ERROR: ("diagnose_errors", WorkerType.DIAGNOSTICIAN),
    TriggerType.TEST_FINISHED: ("subject_review", WorkerType.MATH_SPECIALIST),
    TriggerType.REVIEW_DUE: ("build_memory_cards", WorkerType.MEMORY_BUILDER),
    TriggerType.WEEK_END: ("recompute_roi", WorkerType.ROI_PLANNER),
    TriggerType.EXAM_APPROACHING: ("subject_review", WorkerType.MATH_SPECIALIST),
    TriggerType.MANUAL: ("subject_review", WorkerType.MATH_SPECIALIST),
}

TICKET_TITLES = {
    "analyze_exam_frequency": "统计历年考频与分值占比",
    "diagnose_errors": "诊断近期错误并定位最小回滚范围",
    "subject_review": "学科掌握度与薄弱点回顾",
    "build_memory_cards": "构建背诵卡片",
    "recompute_roi": "重新计算 ROI 排序",
    "design_test": "生成针对性测试",
    "generate_questions": "生成变式训练题",
    "tag_questions": "为未标注题目补充知识点",
    "parse_paper": "解析试卷文本并结构化",
    "collect_sources": "资料搜集与来源盘点",
    "watchdog_scan": "系统异常自检",
}


# --------------------------------------------------------------------------- #
# events
# --------------------------------------------------------------------------- #
@router.post("/api/events")
def create_event(
    payload: EventCreate,
    auto_run: bool = True,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    if payload.trigger_type not in TriggerType.ALL:
        raise HTTPException(status_code=422, detail=f"未知触发类型: {payload.trigger_type}")

    subject_id = payload.subject_id
    if subject_id is None and payload.subject_type:
        try:
            subject_id = resolve_subject(db, user.id, None, payload.subject_type).id
        except ValueError:
            subject_id = None

    # rule filter 1: idempotency / dedupe on event key
    event_key = payload.event_key or hashlib.sha256(
        f"{payload.trigger_type}:{subject_id}:{sorted(payload.payload.items(), key=lambda kv: kv[0])}".encode("utf-8")
    ).hexdigest()[:32]
    existing = db.scalar(select(Trigger).where(Trigger.event_key == event_key))
    if existing is not None:
        ticket = db.get(Ticket, existing.ticket_id) if existing.ticket_id else None
        return {
            "trigger": _serialize_trigger(existing),
            "decision": {"action": "dedupe", "reason": "相同 event_key 已处理，跳过重复触发"},
            "ticket": serialize_ticket(db, ticket) if ticket else None,
        }

    # rule filter 2: subject / payload sanity
    if payload.trigger_type in (TriggerType.NEW_PAPER, TriggerType.NEW_SYLLABUS) and subject_id is None:
        trigger = Trigger(
            user_id=user.id,
            trigger_type=payload.trigger_type,
            event_key=event_key,
            payload=payload.payload,
            status="ignored",
        )
        db.add(trigger)
        db.commit()
        return {
            "trigger": _serialize_trigger(trigger),
            "decision": {"action": "ignore", "reason": "缺少 subject_id，无法确定考频统计范围"},
            "ticket": None,
        }

    trigger = Trigger(
        user_id=user.id,
        trigger_type=payload.trigger_type,
        event_key=event_key,
        subject_id=subject_id,
        payload=payload.payload,
        status="accepted",
    )
    db.add(trigger)
    db.flush()

    ticket_type, worker = TRIGGER_ROUTES[payload.trigger_type]
    thread = get_or_create_thread(db, user.id, subject_id, f"event:{payload.trigger_type}")
    ticket = create_ticket(
        db,
        user.id,
        ticket_type,
        TICKET_TITLES.get(ticket_type, ticket_type),
        subject_id=subject_id,
        priority=0.8 if payload.trigger_type in (TriggerType.REPEATED_ERROR, TriggerType.EXAM_APPROACHING) else 0.6,
        payload={"worker_type": worker, "params": payload.payload, "trigger": payload.trigger_type},
        thread_id=thread.id,
        trigger_id=trigger.id,
    )
    trigger.ticket_id = ticket.id
    trigger.status = "routed"
    db.commit()

    report = None
    if auto_run:
        assignment, report = run_ticket(db, ticket, worker_type=worker, params=payload.payload)
        trigger.status = "processed"
        db.commit()
    return {
        "trigger": _serialize_trigger(trigger),
        "decision": {"action": "create_ticket", "reason": f"语义评估通过，分配 {worker}"},
        "ticket": serialize_ticket(db, ticket),
        "report": serialize_report(report) if report else None,
    }


def _serialize_trigger(trigger: Trigger) -> dict:
    return {
        "id": trigger.id,
        "trigger_type": trigger.trigger_type,
        "event_key": trigger.event_key,
        "subject_id": trigger.subject_id,
        "payload": trigger.payload,
        "status": trigger.status,
        "ticket_id": trigger.ticket_id,
        "created_at": trigger.created_at.isoformat(),
    }


# --------------------------------------------------------------------------- #
# tickets
# --------------------------------------------------------------------------- #
@router.get("/api/tickets")
def list_tickets(
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    stmt = select(Ticket).where(Ticket.user_id == user.id).order_by(Ticket.id.desc())
    if status:
        stmt = stmt.where(Ticket.status == status)
    tickets = list(db.scalars(stmt.limit(limit)))
    return {"items": [serialize_ticket(db, ticket) for ticket in tickets], "total": len(tickets)}


@router.post("/api/tickets")
def create_ticket_endpoint(
    payload: TicketCreate,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    thread = get_or_create_thread(db, user.id, payload.subject_id, f"manual:{payload.ticket_type}")
    ticket = create_ticket(
        db,
        user.id,
        payload.ticket_type,
        payload.title,
        subject_id=payload.subject_id,
        priority=payload.priority,
        payload=payload.payload,
        thread_id=thread.id,
    )
    db.commit()
    return serialize_ticket(db, ticket)


@router.get("/api/tickets/{ticket_id}")
def ticket_detail(ticket_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    ticket = db.get(Ticket, ticket_id)
    if ticket is None or ticket.user_id != user.id:
        raise HTTPException(status_code=404, detail="任务不存在")
    assignments = list(db.scalars(select(Assignment).where(Assignment.ticket_id == ticket.id).order_by(Assignment.id)))
    reports = list(db.scalars(select(WorkerReport).where(WorkerReport.ticket_id == ticket.id).order_by(WorkerReport.id)))
    reviews = list(db.scalars(select(HumanReview).where(HumanReview.ticket_id == ticket.id).order_by(HumanReview.id)))
    return {
        "ticket": serialize_ticket(db, ticket),
        "assignments": [serialize_assignment(assignment) for assignment in assignments],
        "reports": [serialize_report(report) for report in reports],
        "human_reviews": [_serialize_review(review) for review in reviews],
    }


@router.post("/api/tickets/{ticket_id}/run")
def run_ticket_endpoint(
    ticket_id: int,
    payload: TicketRunRequest,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    ticket = db.get(Ticket, ticket_id)
    if ticket is None or ticket.user_id != user.id:
        raise HTTPException(status_code=404, detail="任务不存在")
    if ticket.status in TicketStatus.TERMINAL:
        raise HTTPException(status_code=409, detail=f"任务已结束（{ticket.status}）")
    assignment, report = run_ticket(db, ticket, worker_type=payload.worker_type, params=payload.params)
    return {
        "ticket": serialize_ticket(db, ticket),
        "assignment": serialize_assignment(assignment),
        "report": serialize_report(report),
    }


@router.post("/api/tickets/{ticket_id}/cancel")
def cancel_ticket_endpoint(ticket_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    ticket = db.get(Ticket, ticket_id)
    if ticket is None or ticket.user_id != user.id:
        raise HTTPException(status_code=404, detail="任务不存在")
    cancel_ticket(db, ticket)
    return serialize_ticket(db, ticket)


# --------------------------------------------------------------------------- #
# agent console
# --------------------------------------------------------------------------- #
@router.post("/api/agent/run")
def agent_run(payload: AgentRunRequest, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    subject_id = payload.subject_id
    if subject_id is None and payload.subject_type:
        try:
            subject_id = resolve_subject(db, user.id, None, payload.subject_type).id
        except ValueError:
            subject_id = None
    result = agent_router.run_router(
        user.id,
        payload.message,
        subject_id=subject_id,
        subject_type=payload.subject_type,
        params=payload.params,
    )
    return result


@router.get("/api/agent/threads")
def list_threads(limit: int = Query(default=30, ge=1, le=200), db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    threads = list(
        db.scalars(select(Thread).where(Thread.user_id == user.id).order_by(Thread.id.desc()).limit(limit))
    )
    items = []
    for thread in threads:
        count = int(db.scalar(select(func.count(Ticket.id)).where(Ticket.thread_id == thread.id)) or 0)
        items.append(
            {
                "id": thread.id,
                "topic": thread.topic,
                "status": thread.status,
                "subject_id": thread.subject_id,
                "ticket_count": count,
                "created_at": thread.created_at.isoformat(),
                "updated_at": thread.updated_at.isoformat(),
            }
        )
    return {"items": items, "total": len(items)}


@router.get("/api/agent/threads/{thread_id}")
def thread_detail(thread_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    thread = db.get(Thread, thread_id)
    if thread is None or thread.user_id != user.id:
        raise HTTPException(status_code=404, detail="线程不存在")
    tickets = list(db.scalars(select(Ticket).where(Ticket.thread_id == thread.id).order_by(Ticket.id.desc())))
    return {
        "thread": {
            "id": thread.id,
            "topic": thread.topic,
            "status": thread.status,
            "subject_id": thread.subject_id,
            "created_at": thread.created_at.isoformat(),
        },
        "tickets": [serialize_ticket(db, ticket) for ticket in tickets],
    }


# --------------------------------------------------------------------------- #
# human review
# --------------------------------------------------------------------------- #
def _serialize_review(review: HumanReview) -> dict:
    return {
        "id": review.id,
        "ticket_id": review.ticket_id,
        "report_id": review.report_id,
        "question": review.question,
        "options": review.options,
        "status": review.status,
        "decision": review.decision,
        "comment": review.comment,
        "created_at": review.created_at.isoformat(),
    }


@router.post("/api/human-review")
def create_human_review(payload: HumanReviewCreate, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    review = HumanReview(
        ticket_id=payload.ticket_id,
        question=payload.question,
        options=payload.options,
        status="pending",
        payload=payload.payload,
    )
    db.add(review)
    db.commit()
    return _serialize_review(review)


@router.get("/api/human-review")
def list_human_reviews(
    status: str | None = "pending",
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    stmt = select(HumanReview).order_by(HumanReview.id.desc())
    if status:
        stmt = stmt.where(HumanReview.status == status)
    reviews = list(db.scalars(stmt.limit(limit)))
    return {"items": [_serialize_review(review) for review in reviews], "total": len(reviews)}


@router.post("/api/human-review/{review_id}/resolve")
def resolve_human_review(
    review_id: int,
    payload: HumanReviewResolve,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    review = db.get(HumanReview, review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="核验记录不存在")
    review.status = "resolved"
    review.decision = payload.decision
    review.comment = payload.comment
    if review.ticket_id:
        ticket = db.get(Ticket, review.ticket_id)
        if ticket:
            ticket.status = TicketStatus.READY if payload.decision == "approve" else TicketStatus.FAILED
    if review.report_id:
        report = db.get(WorkerReport, review.report_id)
        if report:
            report.requires_human_review = False
            report.status = "READY" if payload.decision == "approve" else "FAILED"
    db.commit()
    return _serialize_review(review)


# --------------------------------------------------------------------------- #
# watchdog
# --------------------------------------------------------------------------- #
@router.post("/api/watchdog/scan")
def watchdog_scan(
    payload: WatchdogScanRequest,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    findings = run_scan(db, user.id, auto_fix=payload.auto_fix)
    persisted = last_findings()
    return {
        "findings": findings,
        "actions_taken": persisted.get("actions_taken", []),
        "scanned_at": persisted.get("scanned_at"),
    }


@router.get("/api/watchdog/findings")
def watchdog_findings(db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    data = last_findings()
    return {"items": data.get("findings", []), "scanned_at": data.get("scanned_at"), "actions_taken": data.get("actions_taken", [])}
