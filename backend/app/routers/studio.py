from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import current_user
from app.models import BookResource, ExamSubject, StudyWorkflow, User
from app.schemas import (
    AutonomyUpdate,
    BookCreate,
    BookSearchRequest,
    SubjectCreate,
    WorkflowComposeRequest,
    WorkflowCreate,
    WorkflowUpdate,
)
from app.seed.studio import seed_studio
from app.services import curator
from app.services.util import active_target, subjects_of

router = APIRouter(tags=["studio"])


@router.get("/api/studio/overview")
def studio_overview(db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    subjects = subjects_of(db, user.id)
    workflows = curator.list_workflows(db, user.id)
    books_total = db.scalar(select(func.count(BookResource.id)).where(BookResource.user_id == user.id)) or 0
    books_active = (
        db.scalar(
            select(func.count(BookResource.id)).where(
                BookResource.user_id == user.id, BookResource.status == "active"
            )
        )
        or 0
    )
    return {
        "subjects": [
            {
                "id": subject.id,
                "name": subject.name,
                "subject_type": subject.subject_type,
                "total_score": subject.total_score,
                "code": subject.code,
            }
            for subject in subjects
        ],
        "workflows": workflows,
        "books": {"total": int(books_total), "active": int(books_active)},
        "autonomy": curator.get_autonomy(db, user.id),
    }


@router.post("/api/studio/bootstrap")
def studio_bootstrap(db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    result = seed_studio(db, user.id)
    return {"seeded": result}


# --------------------------------------------------------------------------- #
# subjects
# --------------------------------------------------------------------------- #
@router.get("/api/subjects")
def list_subjects(db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    items = [
        {
            "id": subject.id,
            "name": subject.name,
            "subject_type": subject.subject_type,
            "total_score": subject.total_score,
            "code": subject.code,
            "syllabus": subject.syllabus,
        }
        for subject in subjects_of(db, user.id)
    ]
    return {"items": items, "total": len(items)}


@router.post("/api/subjects")
def create_subject(
    payload: SubjectCreate, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> dict:
    autonomy = curator.get_autonomy(db, user.id)
    # adding a subject is a structural change; human is doing it here explicitly
    try:
        subject = curator.add_subject(
            db,
            user.id,
            payload.name,
            subject_type=payload.subject_type,
            total_score=payload.total_score,
            major=payload.major,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # every subject gets a starter workflow so it is immediately usable
    if autonomy.get("allow_agent_edit_workflow", True):
        template = "math_1" if payload.subject_type == "math_1" else "english_1" if payload.subject_type == "english_1" else "default"
        from app.seed.studio import WORKFLOW_TEMPLATES

        steps = [dict(step) for step in WORKFLOW_TEMPLATES[template]]
        db.add(
            StudyWorkflow(
                user_id=user.id,
                subject_id=subject.id,
                name=f"{subject.name} · 标准工作流",
                description="新增科目时自动生成，可在自主编排页修改",
                steps=steps,
                status="active",
                source="template",
            )
        )
        db.commit()
    return {"id": subject.id, "name": subject.name, "subject_type": subject.subject_type}


@router.delete("/api/subjects/{subject_id}")
def delete_subject(
    subject_id: int,
    confirm: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    autonomy = curator.get_autonomy(db, user.id)
    if autonomy.get("require_confirm_for_delete", True) and not confirm:
        raise HTTPException(status_code=409, detail="删除科目需要 confirm=true（会同时删除该科目的题库、知识图谱和工作流）")
    try:
        return curator.remove_subject(db, user.id, subject_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# --------------------------------------------------------------------------- #
# books
# --------------------------------------------------------------------------- #
@router.get("/api/books")
def list_books(
    q: str = "",
    subject_type: str | None = None,
    major: str | None = None,
    status: str = "active",
    limit: int = Query(default=100, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    if q.strip():
        items = curator.search_books(
            db, user.id, q, subject_type=subject_type, major=major, limit=limit
        )
        return {"items": items, "total": len(items), "mode": "search"}
    stmt = select(BookResource).where(BookResource.user_id == user.id)
    if status:
        stmt = stmt.where(BookResource.status == status)
    if subject_type:
        stmt = stmt.where(BookResource.subject_type == subject_type)
    if major:
        stmt = stmt.where(BookResource.major.contains(major))
    books = list(db.scalars(stmt.order_by(BookResource.id.desc()).limit(limit)))
    return {
        "items": [
            {
                "id": book.id,
                "key": book.key,
                "title": book.title,
                "author": book.author,
                "publisher": book.publisher,
                "subject_type": book.subject_type,
                "major": book.major,
                "level": book.level,
                "tags": book.tags,
                "description": book.description,
                "status": book.status,
                "created_by": book.created_by,
                "score": None,
                "reason": "",
            }
            for book in books
        ],
        "total": len(books),
        "mode": "list",
    }


@router.post("/api/books")
def create_book(
    payload: BookCreate, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> dict:
    book = BookResource(
        user_id=user.id,
        key=f"user-{uuid.uuid4().hex[:10]}",
        title=payload.title,
        author=payload.author,
        publisher=payload.publisher,
        subject_type=payload.subject_type,
        major=payload.major,
        level=payload.level,
        tags=payload.tags,
        description=payload.description,
        source_url=payload.source_url,
        created_by="user",
    )
    db.add(book)
    db.commit()
    return {"id": book.id, "title": book.title, "key": book.key}


@router.post("/api/books/search")
def search_books_endpoint(
    payload: BookSearchRequest, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> dict:
    items = curator.search_books(
        db,
        user.id,
        payload.query,
        subject_type=payload.subject_type,
        major=payload.major,
        limit=payload.limit,
    )
    return {"items": items, "total": len(items), "inferred_subject_type": curator.infer_subject_type(payload.query)}


@router.delete("/api/books/{book_id}")
def archive_book(book_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    book = db.get(BookResource, book_id)
    if book is None or book.user_id != user.id:
        raise HTTPException(status_code=404, detail="书籍不存在")
    book.status = "archived"
    db.commit()
    return {"id": book.id, "status": book.status}


# --------------------------------------------------------------------------- #
# workflows
# --------------------------------------------------------------------------- #
@router.get("/api/workflows")
def list_workflows(db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    items = curator.list_workflows(db, user.id)
    return {"items": items, "total": len(items)}


@router.post("/api/workflows")
def create_workflow(
    payload: WorkflowCreate, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> dict:
    workflow = curator.save_workflow(
        db,
        user.id,
        {
            "name": payload.name,
            "description": payload.description,
            "subject_id": payload.subject_id,
            "steps": payload.steps,
            "source": payload.source,
        },
    )
    return curator.serialize_workflow(workflow)


@router.post("/api/workflows/compose")
def compose_workflow(
    payload: WorkflowComposeRequest, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> dict:
    try:
        draft = curator.compose_workflow(db, user.id, payload.text)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if payload.save:
        workflow = curator.save_workflow(db, user.id, draft)
        return {"draft": draft, "workflow": curator.serialize_workflow(workflow), "saved": True}
    return {"draft": draft, "workflow": None, "saved": False}


@router.patch("/api/workflows/{workflow_id}")
def patch_workflow(
    workflow_id: int,
    payload: WorkflowUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    try:
        workflow = curator.update_workflow(db, user.id, workflow_id, payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return curator.serialize_workflow(workflow)


@router.delete("/api/workflows/{workflow_id}")
def remove_workflow(
    workflow_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> dict:
    try:
        curator.delete_workflow(db, user.id, workflow_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": workflow_id}


# --------------------------------------------------------------------------- #
# autonomy
# --------------------------------------------------------------------------- #
@router.get("/api/autonomy")
def get_autonomy(db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    return curator.get_autonomy(db, user.id)


@router.put("/api/autonomy")
def put_autonomy(
    payload: AutonomyUpdate, db: Session = Depends(get_db), user: User = Depends(current_user)
) -> dict:
    patch = {key: value for key, value in payload.model_dump(exclude_unset=True).items() if value is not None}
    return curator.set_autonomy(db, user.id, patch)
