"""Exam data acquisition: sources, documents, paper parsing and tagging.

The parser accepts either structured JSON (a list of question objects) or plain
text split on Chinese exam numbering. PDF/OCR ingestion is intentionally behind
an interface (``parse_payload``) so a PyMuPDF/OCR worker can be plugged in when
the environment provides those libraries.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import KnowledgeNode, Paper, Question, QuestionKnowledge, Source, SourceDocument
from app.services.util import resolve_subject

_NUMBER_RE = re.compile(r"^\s*(\d{1,2})\s*[.、．]\s*(.*)$")
_ANSWER_RE = re.compile(r"(?:答案|参考答案)\s*[:：]\s*(.+)$")


def sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def upsert_source(db: Session, data: dict) -> Source:
    source = db.scalar(select(Source).where(Source.key == data["key"]))
    if source:
        for field in ("source_type", "title", "url", "publisher", "license", "notes"):
            if data.get(field) is not None:
                setattr(source, field, data[field])
        if data.get("trust_score") is not None:
            source.trust_score = float(data["trust_score"])
        db.commit()
        return source
    source = Source(
        key=data["key"],
        source_type=data.get("source_type") or "user_provided",
        title=data.get("title") or data["key"],
        url=data.get("url") or "",
        publisher=data.get("publisher") or "",
        license=data.get("license") or "",
        trust_score=float(data.get("trust_score") or 0.6),
        retrieved_at=data.get("retrieved_at") or datetime.now(UTC),
        notes=data.get("notes") or "",
        meta=data.get("meta") or {},
    )
    db.add(source)
    db.commit()
    return source


def auto_tag(db: Session, subject_id: int, text: str, max_nodes: int = 3) -> list[tuple[KnowledgeNode, float, float]]:
    """Keyword-based knowledge tagging when no human/LLM tags are available."""
    nodes = list(db.scalars(select(KnowledgeNode).where(KnowledgeNode.subject_id == subject_id)))
    if not nodes or not (text or "").strip():
        return []
    scored: list[tuple[float, KnowledgeNode]] = []
    haystack = text.lower()
    for node in nodes:
        # match on node name tokens and explicit code mentions
        name_tokens = [token for token in re.split(r"[\s·、（）()，,与和]+", node.name) if len(token) >= 2]
        hits = sum(1 for token in name_tokens if token.lower() in haystack)
        if node.code.lower() in haystack:
            hits += 2
        if hits:
            scored.append((hits * node.importance, node))
    scored.sort(key=lambda item: item[0], reverse=True)
    picked = scored[:max_nodes]
    if not picked:
        return []
    total = sum(score for score, _node in picked)
    return [
        (node, round(score / total, 3), 0.45)
        for score, node in picked
    ]


def parse_questions(content_text: str, paper_type: str, default_score: float = 4.0) -> list[dict]:
    """Parse structured JSON or raw paper text into question dicts."""
    text = (content_text or "").strip()
    if not text:
        return []
    if text.startswith("[") or text.startswith("{"):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            payload = payload.get("questions") or []
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict) and item.get("content")]

    # plain text: blocks starting with "1." / "1、" etc.
    blocks: list[dict] = []
    current: dict | None = None
    for line in text.splitlines():
        match = _NUMBER_RE.match(line)
        current_length = (
            len("\n".join(current.get("lines", []))) if current is not None else 0
        )
        if match and (current is None or current_length > 12):
            if current:
                blocks.append(current)
            current = {"number": match.group(1), "lines": [match.group(2)], "answer": "", "explanation": ""}
        elif current is not None:
            answer_match = _ANSWER_RE.search(line)
            if answer_match:
                current["answer"] = answer_match.group(1).strip()
            elif line.strip().startswith(("解析", "解答", "分析")):
                current["explanation"] = line.strip()
            else:
                current["lines"].append(line)
    if current:
        blocks.append(current)

    questions = []
    for index, block in enumerate(blocks, start=1):
        content = "\n".join(part for part in block["lines"] if part.strip()).strip()
        if len(content) < 8:
            continue
        questions.append(
            {
                "number": block.get("number") or str(index),
                "question_type": _guess_type(content),
                "content": content,
                "answer": block.get("answer") or "",
                "explanation": block.get("explanation") or "",
                "score": default_score,
                "difficulty": 0.5,
                "source_type": paper_type,
            }
        )
    return questions


def _guess_type(content: str) -> str:
    if re.search(r"[A-D][.．]", content):
        return "calc_single"
    if len(content) > 180 or "证明" in content or "求" in content:
        return "calc_solve"
    return "calc_fill"


def import_document(db: Session, user_id: int, data: dict) -> dict:
    source = db.scalar(select(Source).where(Source.key == data["source_key"]))
    if source is None:
        raise ValueError(f"source '{data['source_key']}' 不存在，请先创建来源")

    content_text = data.get("content_text") or ""
    digest = sha256_text(content_text)
    existing_doc = db.scalar(select(SourceDocument).where(SourceDocument.sha256 == digest))
    if existing_doc:
        paper = db.scalar(select(Paper).where(Paper.document_id == existing_doc.id))
        return {
            "document": _serialize_document(existing_doc),
            "paper": _serialize_paper(paper) if paper else None,
            "questions_created": 0,
            "duplicate": True,
        }

    subject = resolve_subject(db, user_id, data.get("subject_id"), data.get("subject_type"))
    doc = SourceDocument(
        source_id=source.id,
        filename=data.get("filename") or "imported.txt",
        doc_type=data.get("doc_type") or "paper",
        version="1.0",
        sha256=digest,
        raw_text=content_text[:200_000],
        parse_status="parsed",
        meta={"imported_at": datetime.now(UTC).isoformat(), "chars": len(content_text)},
    )
    db.add(doc)
    db.flush()

    paper = None
    created_questions = 0
    if doc.doc_type == "paper":
        year = int(data.get("paper_year") or 0)
        paper_type = data.get("paper_type") or "real"
        parsed = parse_questions(content_text, paper_type)
        if parsed:
            paper = Paper(
                key=f"{source.key}-{subject.id}-{year or 'x'}-{digest[:8]}",
                document_id=doc.id,
                subject_id=subject.id,
                year=year,
                paper_type=paper_type,
                title=data.get("title") or f"{year or '未标注年份'} {subject.name}试卷",
                total_score=sum(float(item.get("score") or 4.0) for item in parsed),
                meta={"parsed_by": "examos-text-parser"},
            )
            db.add(paper)
            db.flush()
            for item in parsed:
                question = Question(
                    key=f"{paper.key}-{item.get('number') or created_questions}",
                    paper_id=paper.id,
                    subject_id=subject.id,
                    number=str(item.get("number") or ""),
                    question_type=item.get("question_type") or "calc_solve",
                    content=item["content"],
                    answer=item.get("answer") or "",
                    explanation=item.get("explanation") or "",
                    score=float(item.get("score") or 4.0),
                    difficulty=float(item.get("difficulty") or 0.5),
                    source_type=paper_type,
                    is_synthetic=False,
                    verification={"status": "imported", "source": source.key},
                )
                db.add(question)
                db.flush()
                _link_knowledge(db, question, subject.id, item)
                created_questions += 1
            paper.total_score = sum(float(q.score) for q in _paper_questions(db, paper.id))
    db.commit()
    return {
        "document": _serialize_document(doc),
        "paper": _serialize_paper(paper) if paper else None,
        "questions_created": created_questions,
        "duplicate": False,
    }


def _paper_questions(db: Session, paper_id: int) -> list[Question]:
    return list(db.scalars(select(Question).where(Question.paper_id == paper_id)))


def _link_knowledge(db: Session, question: Question, subject_id: int, item: dict) -> None:
    codes = item.get("knowledge") or item.get("knowledge_codes") or []
    if isinstance(codes, str):
        codes = [codes]
    nodes = []
    if codes:
        plain_codes = [code if isinstance(code, str) else code.get("code") for code in codes]
        nodes = list(db.scalars(select(KnowledgeNode).where(KnowledgeNode.code.in_([c for c in plain_codes if c]))))
        if nodes:
            weight = round(1.0 / len(nodes), 3)
            for node in nodes:
                db.add(
                    QuestionKnowledge(
                        question_id=question.id,
                        knowledge_id=node.id,
                        weight=weight,
                        confidence=0.85,
                        is_core=True,
                        role="core",
                    )
                )
            return
    # fallback: automatic tagging with lower confidence
    tagged = auto_tag(db, subject_id, question.content)
    for node, weight, confidence in tagged:
        db.add(
            QuestionKnowledge(
                question_id=question.id,
                knowledge_id=node.id,
                weight=weight,
                confidence=confidence,
                is_core=False,
                role="auto",
            )
        )
    if tagged:
        return

    # last resort: a per-subject "unclassified" node, so imported questions are
    # never orphaned (no knowledge link => attempts could not update mastery).
    from app.models import ExamSubject

    subject = db.get(ExamSubject, subject_id)
    code = f"GEN-{subject.subject_type if subject else subject_id}"
    node = db.scalar(
        select(KnowledgeNode).where(
            KnowledgeNode.subject_id == subject_id, KnowledgeNode.code == code
        )
    )
    if node is None:
        node = KnowledgeNode(
            subject_id=subject_id,
            code=code,
            name="综合与未分类考点",
            category="Concept",
            chapter="综合",
            description="导入题目未能自动匹配知识点时的兜底标签，需人工复核后重新标注。",
            importance=0.35,
            est_minutes=45,
            syllabus_requirement="了解",
            meta={"fallback": True},
        )
        db.add(node)
        db.flush()
    db.add(
        QuestionKnowledge(
            question_id=question.id,
            knowledge_id=node.id,
            weight=1.0,
            confidence=0.2,
            is_core=False,
            role="fallback",
        )
    )


def _serialize_document(doc: SourceDocument) -> dict:
    return {
        "id": doc.id,
        "source_id": doc.source_id,
        "filename": doc.filename,
        "doc_type": doc.doc_type,
        "sha256": doc.sha256,
        "parse_status": doc.parse_status,
        "created_at": doc.created_at.isoformat(),
    }


def _serialize_paper(paper: Paper) -> dict:
    return {
        "id": paper.id,
        "key": paper.key,
        "subject_id": paper.subject_id,
        "year": paper.year,
        "paper_type": paper.paper_type,
        "title": paper.title,
        "total_score": paper.total_score,
    }


def list_sources(db: Session) -> list[dict]:
    from sqlalchemy import func

    rows = db.execute(
        select(Source, func.count(SourceDocument.id))
        .outerjoin(SourceDocument, SourceDocument.source_id == Source.id)
        .group_by(Source.id)
        .order_by(Source.id)
    ).all()
    out = []
    for source, doc_count in rows:
        paper_count = db.scalar(
            select(func.count(Paper.id))
            .join(SourceDocument, SourceDocument.id == Paper.document_id)
            .where(SourceDocument.source_id == source.id)
        ) or 0
        out.append(
            {
                "id": source.id,
                "key": source.key,
                "source_type": source.source_type,
                "title": source.title,
                "url": source.url,
                "publisher": source.publisher,
                "license": source.license,
                "trust_score": source.trust_score,
                "document_count": int(doc_count),
                "paper_count": int(paper_count),
                "created_at": source.created_at.isoformat(),
            }
        )
    return out


def list_papers(db: Session, subject_id: int | None = None, year: int | None = None, paper_type: str | None = None) -> list[dict]:
    from sqlalchemy import func

    stmt = (
        select(Paper, func.count(Question.id))
        .outerjoin(Question, Question.paper_id == Paper.id)
        .group_by(Paper.id)
        .order_by(Paper.year.desc(), Paper.id)
    )
    if subject_id:
        stmt = stmt.where(Paper.subject_id == subject_id)
    if year:
        stmt = stmt.where(Paper.year == year)
    if paper_type:
        stmt = stmt.where(Paper.paper_type == paper_type)
    return [
        {
            "id": paper.id,
            "key": paper.key,
            "subject_id": paper.subject_id,
            "year": paper.year,
            "paper_type": paper.paper_type,
            "title": paper.title,
            "total_score": paper.total_score,
            "question_count": int(count),
            "created_at": paper.created_at.isoformat(),
        }
        for paper, count in db.execute(stmt).all()
    ]
