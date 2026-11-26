from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db, init_db
from app.deps import current_user
from app.models import (
    ExamSubject,
    ExamTarget,
    KnowledgeNode,
    LearnerAttempt,
    Paper,
    Question,
    Source,
    Ticket,
    User,
)
from app.schemas import ProfileUpdate, SystemResetRequest, SystemSeedRequest
from app.services.dashboard import dashboard as dashboard_service
from app.services.util import active_target, ensure_user, subjects_of

router = APIRouter(tags=["system"])


def _count(db: Session, model) -> int:
    return int(db.scalar(select(func.count()).select_from(model)) or 0)


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "database": "postgresql" if not settings.is_sqlite else "sqlite"}


@router.get("/api/system/status")
def system_status(db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    return {
        "database": "postgresql" if not settings.is_sqlite else "sqlite",
        "llm": {"enabled": settings.llm_enabled, "model": settings.llm_model if settings.llm_enabled else ""},
        "counts": {
            "sources": _count(db, Source),
            "papers": _count(db, Paper),
            "questions": _count(db, Question),
            "knowledge": _count(db, KnowledgeNode),
            "attempts": _count(db, LearnerAttempt),
            "tickets": _count(db, Ticket),
        },
        "seeded": _count(db, Question) > 0,
        "daily_minutes": user.daily_minutes,
    }


@router.post("/api/system/seed")
def seed_demo_data(
    payload: SystemSeedRequest,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    from app.seed.generator import seed_demo

    init_db()
    try:
        return seed_demo(db, force=payload.force)
    except Exception as exc:  # pragma: no cover - surfaced to the client
        raise HTTPException(status_code=500, detail=f"seed failed: {exc}") from exc


@router.post("/api/system/reset")
def reset_database(
    payload: SystemResetRequest,
    db: Session = Depends(get_db),
) -> dict:
    if not payload.confirm:
        raise HTTPException(status_code=400, detail="需要 confirm=true 才会重置全部数据")
    from app.db import engine
    from app.models import Base

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    ensure_user(db)
    return {"reset": True}


@router.get("/api/profile")
def get_profile(db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    target = active_target(db, user.id)
    subjects = subjects_of(db, user.id)
    return {
        "user": {
            "id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "daily_minutes": user.daily_minutes,
            "profile": user.profile,
        },
        "target": {
            "id": target.id,
            "university": target.university,
            "major": target.major,
            "exam_year": target.exam_year,
            "exam_date": target.exam_date.isoformat() if target and target.exam_date else None,
        } if target else None,
        "subjects": [
            {
                "id": subject.id,
                "subject_type": subject.subject_type,
                "name": subject.name,
                "code": subject.code,
                "total_score": subject.total_score,
            }
            for subject in subjects
        ],
    }


@router.put("/api/profile")
def update_profile(
    payload: ProfileUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    target = active_target(db, user.id)
    if target is None:
        target = ExamTarget(user_id=user.id)
        db.add(target)
        db.flush()
    if payload.daily_minutes is not None:
        user.daily_minutes = payload.daily_minutes
    if payload.university is not None:
        target.university = payload.university
    if payload.major is not None:
        target.major = payload.major
    if payload.exam_year is not None:
        target.exam_year = payload.exam_year
    if payload.exam_date is not None:
        target.exam_date = payload.exam_date.astimezone(UTC) if payload.exam_date.tzinfo else payload.exam_date.replace(tzinfo=UTC)
    db.commit()
    return {"updated": True, "daily_minutes": user.daily_minutes}


@router.get("/api/dashboard")
def dashboard(db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    return dashboard_service(db, user.id)
