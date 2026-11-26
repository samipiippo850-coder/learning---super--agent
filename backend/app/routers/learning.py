from __future__ import annotations

import uuid
from datetime import UTC, datetime, date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.constants import ErrorType, TriggerType
from app.db import get_db
from app.deps import current_user
from app.models import (
    ExamSubject,
    LearnerAttempt,
    MasteryStateRow,
    MemoryCard,
    Question,
    StudyTask,
    TestResult,
    TestSession,
    Trigger,
    User,
)
from app.schemas import (
    AttemptCreate,
    MemoryBuildRequest,
    MemoryCardCreate,
    MemoryReviewRequest,
    PlanRegenerateRequest,
    TaskUpdate,
    TestGenerateRequest,
    TestSubmitRequest,
)
from app.services import mastery as mastery_service
from app.services import memory as memory_service
from app.services import tests as test_service
from app.services.diagnose import classify_error
from app.services.grading import grade_answer
from app.services.graph import rollback_plan
from app.services.planner import generate_plan, today_plan
from app.services.roi import get_roi, persist_subject_roi
from app.services.util import question_knowledge_map, resolve_subject, subjects_of

router = APIRouter(tags=["learning"])


# --------------------------------------------------------------------------- #
# mastery
# --------------------------------------------------------------------------- #
@router.get("/api/mastery")
def mastery_list(
    subject_id: int | None = None,
    subject_type: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    subjects = subjects_of(db, user.id)
    if subject_id or subject_type:
        subjects = [resolve_subject(db, user.id, subject_id, subject_type)]
    items = []
    for subject in subjects:
        summary = mastery_service.summary(db, user.id, subject.id)
        rows = {
            row.knowledge_id: row
            for row in db.scalars(select(MasteryStateRow).where(MasteryStateRow.user_id == user.id))
        }
        from app.models import KnowledgeNode

        for node in db.scalars(select(KnowledgeNode).where(KnowledgeNode.subject_id == subject.id)):
            row = rows.get(node.id)
            items.append(
                {
                    "knowledge_id": node.id,
                    "code": node.code,
                    "name": node.name,
                    "chapter": node.chapter,
                    "subject_id": subject.id,
                    "subject_name": subject.name,
                    "mastery": round(row.mastery, 4) if row else 0.2,
                    "state": row.state if row else "UNSEEN",
                    "confidence": round(row.confidence, 4) if row else 0.0,
                    "independent_correct": round(row.independent_correct, 2) if row else 0.0,
                    "independent_total": round(row.independent_total, 2) if row else 0.0,
                    "last_tested_at": row.last_tested_at.isoformat() if row and row.last_tested_at else None,
                    "summary": summary,
                }
            )
    return {"items": items, "total": len(items)}


@router.post("/api/mastery/recompute")
def mastery_recompute(
    payload: dict | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    subject_id = (payload or {}).get("subject_id")
    updated = mastery_service.recompute_all(db, user.id, subject_id)
    return {"updated": updated}


# --------------------------------------------------------------------------- #
# roi
# --------------------------------------------------------------------------- #
@router.get("/api/roi")
def roi_list(
    subject_id: int | None = None,
    subject_type: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    subjects = subjects_of(db, user.id)
    if subject_id or subject_type:
        subjects = [resolve_subject(db, user.id, subject_id, subject_type)]
    items = []
    for subject in subjects:
        rows = get_roi(db, user.id, subject_id=subject.id, limit=limit)
        if not rows:
            persist_subject_roi(db, user.id, subject.id)
            rows = get_roi(db, user.id, subject_id=subject.id, limit=limit)
        for row in rows:
            row["subject_name"] = subject.name
            items.append(row)
    items.sort(key=lambda item: (item["blocked"], -item["roi"]))
    for position, item in enumerate(items, start=1):
        item["rank"] = position
    total_gain = sum(item["expected_gain"] for item in items if not item["blocked"])
    return {
        "items": items[:limit],
        "summary": {
            "total_expected_gain": round(total_gain, 2),
            "computed_at": items[0]["computed_at"] if items else None,
            "eligible": len([item for item in items if not item["blocked"]]),
        },
    }


@router.post("/api/roi/recompute")
def roi_recompute(
    payload: dict | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    subject_id = (payload or {}).get("subject_id")
    subjects = subjects_of(db, user.id)
    if subject_id:
        subjects = [resolve_subject(db, user.id, subject_id, None)]
    snapshots = 0
    top: list[dict] = []
    for subject in subjects:
        rows = persist_subject_roi(db, user.id, subject.id)
        snapshots += len(rows)
        top.extend(
            {
                "code": row["code"],
                "name": row["name"],
                "roi": row["roi"],
                "subject": subject.name,
                "blocked": row["blocked"],
            }
            for row in rows[:5]
        )
    top.sort(key=lambda item: item["roi"], reverse=True)
    return {"snapshots": snapshots, "top": top[:10]}


# --------------------------------------------------------------------------- #
# plan / tasks
# --------------------------------------------------------------------------- #
@router.get("/api/plan/today")
def get_today_plan(
    date: str | None = None,
    subject_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    return today_plan(db, user.id, plan_date=date, subject_id=subject_id)


@router.post("/api/plan/regenerate")
def regenerate_plan(
    payload: PlanRegenerateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    return generate_plan(
        db,
        user.id,
        plan_date=payload.date,
        subject_id=payload.subject_id,
        budget_minutes=payload.budget_minutes,
    )


@router.patch("/api/tasks/{task_id}")
def update_task(task_id: int, payload: TaskUpdate, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    task = db.get(StudyTask, task_id)
    if task is None or task.user_id != user.id:
        raise HTTPException(status_code=404, detail="任务不存在")
    task.status = payload.status
    db.commit()
    return {
        "id": task.id,
        "status": task.status,
        "title": task.title,
        "task_type": task.task_type,
        "est_minutes": task.est_minutes,
    }


# --------------------------------------------------------------------------- #
# attempts
# --------------------------------------------------------------------------- #
@router.post("/api/attempts")
def create_attempt(
    payload: AttemptCreate,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    question = db.get(Question, payload.question_id)
    if question is None:
        raise HTTPException(status_code=404, detail="题目不存在")
    graded = grade_answer(question.question_type, question.answer, payload.answer_text, float(question.score or 0))
    expected_minutes = max(1.0, float(question.score or 4) / 10.0 * 4.0)
    diagnosis = classify_error(
        question,
        payload.answer_text,
        graded["correct"],
        duration_sec=payload.duration_sec,
        hint_count=payload.hint_count,
        confidence_self=payload.confidence_self,
        expected_minutes=expected_minutes,
    )
    error_type = diagnosis["error_type"] or ErrorType.UNKNOWN

    mastery_updates = mastery_service.apply_attempt(
        db,
        user.id,
        question,
        is_correct=graded["correct"],
        independent=payload.is_independent and payload.hint_count == 0,
    )
    attempt = LearnerAttempt(
        user_id=user.id,
        question_id=question.id,
        answer_text=payload.answer_text,
        is_correct=graded["correct"],
        score_awarded=graded["score"],
        duration_sec=payload.duration_sec,
        hint_count=payload.hint_count,
        confidence_self=payload.confidence_self,
        error_type=error_type,
        is_independent=payload.is_independent and payload.hint_count == 0,
        source=payload.source,
        meta={"method": graded["method"], "ratio": graded["ratio"]},
    )
    db.add(attempt)
    db.flush()

    rollback = {"needed": False, "nodes": [], "strategy": ""}
    if not graded["correct"]:
        knowledge_ids = [node.id for node, _w, _c in question_knowledge_map(db, question.id)]
        rollback = rollback_plan(
            db, user.id, question.subject_id, knowledge_ids, error_type, question_id=question.id
        )
        db.add(
            Trigger(
                user_id=user.id,
                trigger_type=TriggerType.NEW_ATTEMPT,
                event_key=f"attempt-{attempt.id}-{uuid.uuid4().hex[:10]}",
                subject_id=question.subject_id,
                payload={"question_id": question.id, "correct": False, "error_type": error_type},
                status="processed",
            )
        )
    db.commit()
    return {
        "attempt": {
            "id": attempt.id,
            "question_id": question.id,
            "question_key": question.key,
            "correct": graded["correct"],
            "score_awarded": graded["score"],
            "max_score": question.score,
            "method": graded["method"],
            "answer": question.answer,
            "explanation": question.explanation,
        },
        "mastery": mastery_updates,
        "diagnosis": diagnosis,
        "rollback": rollback,
    }


@router.get("/api/attempts")
def list_attempts(
    subject_id: int | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    stmt = select(LearnerAttempt).where(LearnerAttempt.user_id == user.id).order_by(LearnerAttempt.id.desc())
    if subject_id:
        stmt = stmt.where(
            LearnerAttempt.question_id.in_(select(Question.id).where(Question.subject_id == subject_id))
        )
    attempts = list(db.scalars(stmt.limit(limit)))
    items = []
    for attempt in attempts:
        question = db.get(Question, attempt.question_id)
        items.append(
            {
                "id": attempt.id,
                "question_id": attempt.question_id,
                "question_key": question.key if question else "",
                "question_type": question.question_type if question else "",
                "correct": attempt.is_correct,
                "score_awarded": attempt.score_awarded,
                "error_type": attempt.error_type,
                "duration_sec": attempt.duration_sec,
                "hint_count": attempt.hint_count,
                "source": attempt.source,
                "created_at": attempt.created_at.isoformat(),
            }
        )
    return {"items": items, "total": len(items)}


# --------------------------------------------------------------------------- #
# memory
# --------------------------------------------------------------------------- #
@router.get("/api/memory/cards")
def list_cards(
    due_only: bool = False,
    subject_id: int | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    cards = memory_service.list_cards(db, user.id, due_only=due_only, subject_id=subject_id, limit=limit)
    return {"items": [memory_service.serialize_card(card) for card in cards], "total": len(cards)}


@router.post("/api/memory/cards")
def create_card(payload: MemoryCardCreate, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    card = MemoryCard(
        user_id=user.id,
        subject_id=payload.subject_id,
        knowledge_id=payload.knowledge_id,
        card_type=payload.card_type,
        front=payload.front,
        back=payload.back,
        source_ref=payload.source_ref,
        due_at=datetime.now(UTC),
    )
    db.add(card)
    db.commit()
    return memory_service.serialize_card(card)


@router.post("/api/memory/cards/{card_id}/review")
def review_card(
    card_id: int,
    payload: MemoryReviewRequest,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    card = db.get(MemoryCard, card_id)
    if card is None or card.user_id != user.id:
        raise HTTPException(status_code=404, detail="卡片不存在")
    return memory_service.review_card(db, card, payload.quality, payload.response_text)


@router.post("/api/memory/build")
def build_cards(payload: MemoryBuildRequest, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    cards = memory_service.build_cards(
        db, user.id, subject_id=payload.subject_id, knowledge_ids=payload.knowledge_ids or None
    )
    return {
        "created": len(cards),
        "cards": [memory_service.serialize_card(card) for card in cards[:50]],
    }


# --------------------------------------------------------------------------- #
# tests
# --------------------------------------------------------------------------- #
@router.post("/api/tests/generate")
def generate_test(
    payload: TestGenerateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    subject = resolve_subject(db, user.id, payload.subject_id, payload.subject_type)
    try:
        test, questions = test_service.generate_test(
            db,
            user.id,
            subject.id,
            test_type=payload.test_type,
            duration_min=payload.duration_min,
            focus_knowledge_ids=payload.focus_knowledge_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "test": {
            "id": test.id,
            "subject_id": test.subject_id,
            "test_type": test.test_type,
            "max_score": test.max_score,
            "blueprint": test.blueprint,
            "status": test.status,
            "created_at": test.created_at.isoformat(),
        },
        "questions": [test_service.serialize_question(question, with_answer=False) for question in questions],
    }


@router.get("/api/tests")
def list_tests(
    subject_id: int | None = None,
    limit: int = Query(default=20, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    stmt = select(TestSession).where(TestSession.user_id == user.id).order_by(TestSession.id.desc())
    if subject_id:
        stmt = stmt.where(TestSession.subject_id == subject_id)
    tests = list(db.scalars(stmt.limit(limit)))
    return {"items": [test_service.test_summary(test) for test in tests], "total": len(tests)}


@router.get("/api/tests/{test_id}")
def get_test(test_id: int, with_answers: bool = False, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    try:
        test, questions = test_service.get_test(db, test_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "test": test_service.test_summary(test),
        "questions": [test_service.serialize_question(question, with_answer=with_answers) for question in questions],
    }


@router.post("/api/tests/{test_id}/submit")
def submit_test(
    test_id: int,
    payload: TestSubmitRequest,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    test = db.get(TestSession, test_id)
    if test is None or test.user_id != user.id:
        raise HTTPException(status_code=404, detail="测试不存在")
    try:
        report = test_service.submit_test(db, test_id, [answer.model_dump() for answer in payload.answers])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return report


@router.get("/api/tests/{test_id}/report")
def test_report(test_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    test = db.get(TestSession, test_id)
    if test is None or test.user_id != user.id:
        raise HTTPException(status_code=404, detail="测试不存在")
    report = (test.blueprint or {}).get("report")
    if report is None:
        raise HTTPException(status_code=404, detail="该测试尚未提交，暂无报告")
    return {"test": test_service.test_summary(test), "report": report}
