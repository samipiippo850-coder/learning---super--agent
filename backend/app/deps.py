from __future__ import annotations

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import ExamSubject, User
from app.services.util import ensure_user, resolve_subject


def current_user(db: Session = Depends(get_db)) -> User:
    return ensure_user(db, settings.default_user)


def current_subject(
    subject_id: int | None = None,
    subject_type: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> ExamSubject:
    try:
        return resolve_subject(db, user.id, subject_id, subject_type)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
