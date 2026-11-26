from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import current_user
from app.models import (
    KnowledgeEdge,
    KnowledgeNode,
    LearnerAttempt,
    MasteryStateRow,
    Paper,
    Question,
    QuestionKnowledge,
    QuestionVersion,
    User,
)
from app.schemas import DocumentImport, SourceCreate, TransformRequest
from app.services import ingest
from app.services.frequency import compute_subject_frequency
from app.services.graph import related_questions, validate_dag
from app.services.roi import get_roi
from app.services.transform import generate_variants
from app.services.util import resolve_subject, subjects_of

router = APIRouter(tags=["content"])


# --------------------------------------------------------------------------- #
# sources / documents / papers
# --------------------------------------------------------------------------- #
@router.get("/api/sources")
def list_sources(db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    items = ingest.list_sources(db)
    return {"items": items, "total": len(items)}


@router.post("/api/sources")
def create_source(payload: SourceCreate, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    source = ingest.upsert_source(db, payload.model_dump())
    return {"id": source.id, "key": source.key, "title": source.title, "trust_score": source.trust_score}


@router.post("/api/documents")
def import_document(
    payload: DocumentImport,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    try:
        return ingest.import_document(db, user.id, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/papers")
def list_papers(
    subject_id: int | None = None,
    year: int | None = None,
    paper_type: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    items = ingest.list_papers(db, subject_id, year, paper_type)
    return {"items": items, "total": len(items)}


# --------------------------------------------------------------------------- #
# questions
# --------------------------------------------------------------------------- #
def _serialize_question(db: Session, question: Question, with_answer: bool = True) -> dict:
    links = []
    rows = db.execute(
        select(QuestionKnowledge, KnowledgeNode)
        .join(KnowledgeNode, KnowledgeNode.id == QuestionKnowledge.knowledge_id)
        .where(QuestionKnowledge.question_id == question.id)
    ).all()
    for link, node in rows:
        links.append(
            {
                "knowledge_id": node.id,
                "code": node.code,
                "name": node.name,
                "weight": link.weight,
                "confidence": link.confidence,
                "is_core": link.is_core,
                "role": link.role,
            }
        )
    payload = {
        "id": question.id,
        "key": question.key,
        "subject_id": question.subject_id,
        "paper_id": question.paper_id,
        "number": question.number,
        "question_type": question.question_type,
        "content": question.content,
        "score": question.score,
        "difficulty": question.difficulty,
        "source_type": question.source_type,
        "is_synthetic": question.is_synthetic,
        "parent_question_id": question.parent_question_id,
        "transform_level": question.transform_level,
        "verification": question.verification or {},
        "knowledge": links,
        "created_at": question.created_at.isoformat(),
    }
    if with_answer:
        payload["answer"] = question.answer
        payload["explanation"] = question.explanation
    return payload


@router.get("/api/questions")
def list_questions(
    subject_id: int | None = None,
    paper_id: int | None = None,
    knowledge_id: int | None = None,
    synthetic: bool | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    stmt = select(Question)
    if subject_id:
        stmt = stmt.where(Question.subject_id == subject_id)
    if paper_id:
        stmt = stmt.where(Question.paper_id == paper_id)
    if knowledge_id:
        stmt = stmt.where(
            Question.id.in_(select(QuestionKnowledge.question_id).where(QuestionKnowledge.knowledge_id == knowledge_id))
        )
    if synthetic is not None:
        stmt = stmt.where(Question.is_synthetic.is_(synthetic))
    total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    questions = list(db.scalars(stmt.order_by(Question.id.desc()).offset(offset).limit(limit)))
    return {"items": [_serialize_question(db, q) for q in questions], "total": total}


@router.get("/api/questions/{question_id}")
def question_detail(question_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    question = db.get(Question, question_id)
    if question is None:
        raise HTTPException(status_code=404, detail="题目不存在")
    versions = list(
        db.scalars(select(QuestionVersion).where(QuestionVersion.question_id == question_id).order_by(QuestionVersion.id.desc()))
    )
    payload = _serialize_question(db, question)
    payload["versions"] = [
        {
            "id": version.id,
            "transform_type": version.transform_type,
            "knowledge_combo": version.knowledge_combo,
            "rationale": version.rationale,
            "verifier": version.verifier,
            "status": version.status,
        }
        for version in versions
    ]
    return payload


@router.post("/api/questions/transform")
def transform_question(
    payload: TransformRequest,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    try:
        created = generate_variants(db, user.id, payload.question_id, level=payload.level, count=payload.count)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "items": [
            {
                "question": _serialize_question(db, item["question"]),
                "verification": item["verification"],
                "rationale": item["rationale"],
                "level": item["level"],
            }
            for item in created
        ],
        "status": "verified",
        "note": "生成题标记为 synthetic，不计入真实考频统计",
    }


@router.get("/api/questions/{question_id}/versions")
def question_versions(question_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    versions = list(
        db.scalars(select(QuestionVersion).where(QuestionVersion.question_id == question_id).order_by(QuestionVersion.id.desc()))
    )
    return {
        "items": [
            {
                "id": version.id,
                "transform_type": version.transform_type,
                "knowledge_combo": version.knowledge_combo,
                "rationale": version.rationale,
                "verifier": version.verifier,
                "status": version.status,
                "created_at": version.created_at.isoformat(),
            }
            for version in versions
        ]
    }


# --------------------------------------------------------------------------- #
# knowledge graph
# --------------------------------------------------------------------------- #
def _knowledge_stats(db: Session, user_id: int, subject_id: int) -> dict:
    freq = compute_subject_frequency(db, subject_id)
    roi_rows = {row["knowledge_id"]: row for row in get_roi(db, user_id, subject_id=subject_id, limit=500)}
    mastery = {
        row.knowledge_id: row
        for row in db.scalars(
            select(MasteryStateRow).where(MasteryStateRow.user_id == user_id)
        )
    }
    return {"frequency": freq, "roi": roi_rows, "mastery": mastery}


@router.get("/api/knowledge")
def list_knowledge(
    subject_id: int | None = None,
    subject_type: str | None = None,
    chapter: str | None = None,
    category: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    subjects = subjects_of(db, user.id)
    if subject_id or subject_type:
        subject = resolve_subject(db, user.id, subject_id, subject_type)
        subjects = [subject]
    items = []
    for subject in subjects:
        stats = _knowledge_stats(db, user.id, subject.id)
        stmt = select(KnowledgeNode).where(KnowledgeNode.subject_id == subject.id)
        if chapter:
            stmt = stmt.where(KnowledgeNode.chapter == chapter)
        if category:
            stmt = stmt.where(KnowledgeNode.category == category)
        nodes = list(db.scalars(stmt.order_by(KnowledgeNode.importance.desc(), KnowledgeNode.id)))
        prereq_edges = db.execute(
            select(KnowledgeEdge.source_id, KnowledgeEdge.target_id).where(
                KnowledgeEdge.relation_type == "prerequisite",
                KnowledgeEdge.target_id.in_([node.id for node in nodes] or [0]),
            )
        ).all()
        prereqs: dict[int, list[int]] = defaultdict(list)
        for source_id, target_id in prereq_edges:
            prereqs[target_id].append(source_id)
        id_to_code = {node.id: node.code for node in nodes}
        for node in nodes:
            freq = stats["frequency"].get(node.id)
            roi = stats["roi"].get(node.id)
            mastery_row = stats["mastery"].get(node.id)
            items.append(
                {
                    "id": node.id,
                    "code": node.code,
                    "name": node.name,
                    "category": node.category,
                    "chapter": node.chapter,
                    "importance": node.importance,
                    "est_minutes": node.est_minutes,
                    "syllabus_requirement": node.syllabus_requirement,
                    "subject_id": node.subject_id,
                    "subject_name": subject.name,
                    "frequency": round(freq.frequency, 4) if freq else 0.0,
                    "score_share": round(freq.score_share, 4) if freq else 0.0,
                    "years_present": freq.years_present if freq else [],
                    "mastery": round(mastery_row.mastery, 4) if mastery_row else 0.2,
                    "state": mastery_row.state if mastery_row else "UNSEEN",
                    "roi": round(roi["roi"], 4) if roi else 0.0,
                    "blocked": bool(roi["blocked"]) if roi else False,
                    "prerequisites": [id_to_code.get(pid, str(pid)) for pid in prereqs.get(node.id, [])],
                }
            )
    return {"items": items, "total": len(items)}


@router.get("/api/knowledge/graph")
def knowledge_graph(
    subject_id: int | None = None,
    subject_type: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> dict:
    subject = resolve_subject(db, user.id, subject_id, subject_type)
    stats = _knowledge_stats(db, user.id, subject.id)
    nodes = list(
        db.scalars(select(KnowledgeNode).where(KnowledgeNode.subject_id == subject.id).order_by(KnowledgeNode.id))
    )
    edges = list(db.scalars(select(KnowledgeEdge).where(KnowledgeEdge.source_id.in_([n.id for n in nodes] or [0]))))
    node_ids = {node.id for node in nodes}
    return {
        "subject_id": subject.id,
        "subject_name": subject.name,
        "nodes": [
            {
                "id": node.id,
                "code": node.code,
                "name": node.name,
                "chapter": node.chapter,
                "category": node.category,
                "importance": node.importance,
                "mastery": round(stats["mastery"][node.id].mastery, 4) if node.id in stats["mastery"] else 0.2,
                "state": stats["mastery"][node.id].state if node.id in stats["mastery"] else "UNSEEN",
                "frequency": round(stats["frequency"][node.id].frequency, 4) if node.id in stats["frequency"] else 0.0,
                "roi": round(stats["roi"][node.id]["roi"], 4) if node.id in stats["roi"] else 0.0,
                "blocked": bool(stats["roi"][node.id]["blocked"]) if node.id in stats["roi"] else False,
            }
            for node in nodes
        ],
        "edges": [
            {
                "source": edge.source_id,
                "target": edge.target_id,
                "relation_type": edge.relation_type,
                "weight": edge.weight,
            }
            for edge in edges
            if edge.source_id in node_ids and edge.target_id in node_ids
        ],
        "dag": validate_dag(db, subject.id),
    }


@router.get("/api/knowledge/{knowledge_id}")
def knowledge_detail(knowledge_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    node = db.get(KnowledgeNode, knowledge_id)
    if node is None:
        raise HTTPException(status_code=404, detail="知识点不存在")
    subject = db.get(__import__("app.models", fromlist=["ExamSubject"]).ExamSubject, node.subject_id)
    stats = _knowledge_stats(db, user.id, node.subject_id)
    freq = stats["frequency"].get(node.id)
    roi = stats["roi"].get(node.id)
    mastery_row = stats["mastery"].get(node.id)
    incoming = list(db.scalars(select(KnowledgeEdge).where(KnowledgeEdge.target_id == node.id)))
    outgoing = list(db.scalars(select(KnowledgeEdge).where(KnowledgeEdge.source_id == node.id)))
    node_names = {
        row.id: row.name for row in db.scalars(select(KnowledgeNode).where(KnowledgeNode.subject_id == node.subject_id))
    }
    return {
        "id": node.id,
        "code": node.code,
        "name": node.name,
        "category": node.category,
        "chapter": node.chapter,
        "description": node.description,
        "importance": node.importance,
        "est_minutes": node.est_minutes,
        "syllabus_requirement": node.syllabus_requirement,
        "subject": {"id": subject.id, "name": subject.name} if subject else None,
        "frequency": freq.as_dict() if freq else None,
        "roi": roi,
        "mastery": {
            "mastery": mastery_row.mastery if mastery_row else 0.2,
            "state": mastery_row.state if mastery_row else "UNSEEN",
            "confidence": mastery_row.confidence if mastery_row else 0.0,
            "independent_correct": mastery_row.independent_correct if mastery_row else 0.0,
            "independent_total": mastery_row.independent_total if mastery_row else 0.0,
        },
        "edges_in": [
            {"source": edge.source_id, "source_name": node_names.get(edge.source_id), "relation_type": edge.relation_type}
            for edge in incoming
        ],
        "edges_out": [
            {"target": edge.target_id, "target_name": node_names.get(edge.target_id), "relation_type": edge.relation_type}
            for edge in outgoing
        ],
        "related_questions": related_questions(db, node.id),
    }
