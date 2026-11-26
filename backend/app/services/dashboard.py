"""Dashboard aggregation used by the web front-end home page."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.constants import MasteryState
from app.models import (
    KnowledgeNode,
    LearnerAttempt,
    MasteryStateRow,
    MemoryCard,
    Question,
    RoiSnapshot,
    StudyTask,
    TestSession,
    Ticket,
    WorkerReport,
)
from app.services.util import active_target, open_ticket_count, subjects_of


def dashboard(db: Session, user_id: int) -> dict:
    target = active_target(db, user_id)
    subjects = subjects_of(db, user_id)
    subject_map = {subject.id: subject for subject in subjects}

    exam_date = target.exam_date if target else None
    countdown_days = None
    if exam_date:
        exam_dt = exam_date if exam_date.tzinfo else exam_date.replace(tzinfo=UTC)
        countdown_days = max(0, (exam_dt.date() - date.today()).days)

    mastery_rows = list(db.scalars(select(MasteryStateRow).where(MasteryStateRow.user_id == user_id)))
    by_state: dict[str, int] = {state: 0 for state in MasteryState.ALL}
    for row in mastery_rows:
        by_state[row.state] = by_state.get(row.state, 0) + 1
    average = sum(r.mastery for r in mastery_rows) / len(mastery_rows) if mastery_rows else 0.0
    knowledge_total = db.scalar(select(func.count(KnowledgeNode.id))) or 0

    top_roi = db.execute(
        select(RoiSnapshot, KnowledgeNode)
        .join(KnowledgeNode, KnowledgeNode.id == RoiSnapshot.knowledge_id)
        .where(RoiSnapshot.user_id == user_id, RoiSnapshot.blocked.is_(False))
        .order_by(RoiSnapshot.roi.desc())
        .limit(5)
    ).all()
    top_roi_payload = [
        {
            "knowledge_id": node.id,
            "code": node.code,
            "name": node.name,
            "subject": subject_map.get(snapshot.subject_id).name if subject_map.get(snapshot.subject_id) else "",
            "roi": snapshot.roi,
            "expected_gain": snapshot.expected_gain,
            "frequency": snapshot.frequency,
        }
        for snapshot, node in top_roi
    ]

    today = date.today().isoformat()
    tasks = list(
        db.scalars(
            select(StudyTask).where(StudyTask.user_id == user_id, StudyTask.plan_date == today)
        )
    )
    used_minutes = sum(t.est_minutes for t in tasks if t.status not in ("DONE", "SKIPPED"))

    due_cards = db.scalar(
        select(func.count(MemoryCard.id)).where(
            MemoryCard.user_id == user_id,
            MemoryCard.due_at.is_not(None),
            MemoryCard.due_at <= datetime.now(UTC),
        )
    ) or 0

    week_ago = datetime.now(UTC) - timedelta(days=7)
    attempts = list(
        db.scalars(
            select(LearnerAttempt)
            .where(LearnerAttempt.user_id == user_id, LearnerAttempt.created_at >= week_ago)
            .order_by(LearnerAttempt.created_at.desc())
        )
    )
    accuracy = (
        sum(1 for a in attempts if a.is_correct) / len(attempts) if attempts else 0.0
    )
    recent_attempts = []
    for attempt in attempts[:8]:
        question = db.get(Question, attempt.question_id)
        recent_attempts.append(
            {
                "id": attempt.id,
                "question_id": attempt.question_id,
                "question_key": question.key if question else "",
                "question_type": question.question_type if question else "",
                "correct": attempt.is_correct,
                "error_type": attempt.error_type,
                "duration_sec": attempt.duration_sec,
                "created_at": attempt.created_at.isoformat(),
                "subject": subject_map.get(question.subject_id).name if question and subject_map.get(question.subject_id) else "",
            }
        )

    open_count, running = open_ticket_count(db, user_id)
    recent_reports = list(
        db.scalars(
            select(WorkerReport)
            .where(WorkerReport.requires_human_review.is_(False))
            .order_by(WorkerReport.id.desc())
            .limit(5)
        )
    )
    last_test = db.scalar(
        select(TestSession).where(TestSession.user_id == user_id).order_by(TestSession.id.desc())
    )

    return {
        "countdown_days": countdown_days,
        "exam_date": exam_date.isoformat() if exam_date else None,
        "university": target.university if target else "",
        "major": target.major if target else "",
        "exam_year": target.exam_year if target else None,
        "subjects": [
            {
                "id": subject.id,
                "subject_type": subject.subject_type,
                "name": subject.name,
                "total_score": subject.total_score,
            }
            for subject in subjects
        ],
        "mastery": {"average": round(average, 4), "by_state": by_state},
        "coverage": {
            "knowledge_total": int(knowledge_total),
            "studied": sum(1 for r in mastery_rows if r.independent_total > 0),
            "stable": by_state.get(MasteryState.STABLE, 0),
        },
        "top_roi": top_roi_payload,
        "due": {"cards": int(due_cards), "tasks": len([t for t in tasks if t.status == "TODO"])},
        "today": {
            "budget_minutes": settings.daily_minutes,
            "used_minutes": round(used_minutes, 1),
            "task_count": len(tasks),
            "done": len([t for t in tasks if t.status == "DONE"]),
        },
        "accuracy": {"last7d": round(accuracy, 4), "attempts": len(attempts)},
        "agent": {
            "open_tickets": open_count,
            "running": running,
            "recent_reports": [
                {
                    "id": report.id,
                    "summary": report.summary,
                    "status": report.status,
                    "confidence": report.confidence,
                    "created_at": report.created_at.isoformat(),
                }
                for report in recent_reports
            ],
        },
        "last_test": {
            "id": last_test.id,
            "test_type": last_test.test_type,
            "score": last_test.score,
            "max_score": last_test.max_score,
            "status": last_test.status,
            "created_at": last_test.created_at.isoformat(),
        } if last_test else None,
        "recent_attempts": recent_attempts,
    }
