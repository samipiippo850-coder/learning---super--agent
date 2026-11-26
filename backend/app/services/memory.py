"""Memory & recitation engine (PRD 9 + 13.2 memory rollback).

Cards move UNSEEN -> LEARNING -> RECOGNITION -> ACTIVE_RECALL -> APPLICATION ->
STABLE. Scheduling is SM-2 style; a review only counts when the learner produced
an answer *before* revealing the back (active recall).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.constants import MemoryCardType, MemoryState
from app.models import KnowledgeNode, MemoryCard
from app.services.util import subjects_of


STATE_ORDER = list(MemoryState.ALL)


def _next_state(current: str, quality: int) -> str:
    index = STATE_ORDER.index(current) if current in STATE_ORDER else 0
    if quality < 3:
        return STATE_ORDER[max(0, index - 1)]
    if quality == 5:
        return STATE_ORDER[min(len(STATE_ORDER) - 1, index + 2)]
    if quality == 4:
        return STATE_ORDER[min(len(STATE_ORDER) - 1, index + 1)]
    return current if index > 0 else MemoryState.LEARNING


def build_cards(
    db: Session,
    user_id: int,
    subject_id: int | None = None,
    knowledge_ids: list[int] | None = None,
    min_importance: float = 0.55,
) -> list[MemoryCard]:
    """Extract recitation material from knowledge nodes and their exam history."""
    subjects = subjects_of(db, user_id)
    if subject_id:
        subjects = [s for s in subjects if s.id == subject_id]
    created: list[MemoryCard] = []
    for subject in subjects:
        stmt = select(KnowledgeNode).where(KnowledgeNode.subject_id == subject.id)
        if knowledge_ids:
            stmt = stmt.where(KnowledgeNode.id.in_(knowledge_ids))
        else:
            stmt = stmt.where(KnowledgeNode.importance >= min_importance)
        for node in db.scalars(stmt.order_by(KnowledgeNode.importance.desc())):
            existing = db.scalar(
                select(MemoryCard).where(
                    MemoryCard.user_id == user_id,
                    MemoryCard.knowledge_id == node.id,
                    MemoryCard.card_type == _card_type_for(node.category),
                )
            )
            if existing:
                continue
            card = MemoryCard(
                user_id=user_id,
                subject_id=subject.id,
                knowledge_id=node.id,
                card_type=_card_type_for(node.category),
                front=_front_for(node),
                back=node.description or node.name,
                source_ref=f"{node.chapter}｜{node.code}",
                state=MemoryState.UNSEEN,
                due_at=datetime.now(UTC),
            )
            db.add(card)
            created.append(card)
    db.commit()
    return created


def _card_type_for(category: str) -> str:
    return {
        "THOREM": MemoryCardType.THEOREM,
        "Theorem": MemoryCardType.THEOREM,
        "Formula": MemoryCardType.FORMULA,
        "Method": MemoryCardType.SHORT_ANSWER,
        "Skill": MemoryCardType.SHORT_ANSWER,
        "ErrorPattern": MemoryCardType.COMPARISON,
        "QuestionType": MemoryCardType.SHORT_ANSWER,
        "MemoryItem": MemoryCardType.DEFINITION,
    }.get(category, MemoryCardType.DEFINITION)


def _front_for(node: KnowledgeNode) -> str:
    if node.category == "Formula":
        return f"写出/陈述公式：{node.name}"
    if node.category == "Theorem":
        return f"陈述定理及其成立条件：{node.name}"
    if node.category == "Method":
        return f"说明方法适用场景与步骤：{node.name}"
    return f"解释概念：{node.name}"


def review_card(db: Session, card: MemoryCard, quality: int, response_text: str = "") -> dict:
    quality = max(0, min(5, int(quality)))
    now = datetime.now(UTC)
    ease = card.ease or 2.5
    interval = card.interval_days or 0.0
    reps = card.reps or 0

    if quality < 3:
        card.lapses = (card.lapses or 0) + 1
        reps = 0
        interval = 0.5
        ease = max(1.3, ease - 0.2)
    else:
        reps += 1
        if reps == 1:
            interval = 1.0
        elif reps == 2:
            interval = 3.0
        else:
            interval = max(1.0, interval * ease)
        ease = min(3.0, ease + (0.1 if quality == 5 else 0.0))
        if quality >= 4 and reps >= 4:
            interval = max(interval, 21.0)

    card.ease = round(ease, 3)
    card.interval_days = round(interval, 2)
    card.reps = reps
    card.state = _next_state(card.state, quality)
    card.due_at = now + timedelta(days=interval)
    if response_text:
        meta = dict(card.meta or {})
        meta["last_response"] = response_text[:500]
        card.meta = meta
    db.commit()
    return {
        "card": serialize_card(card),
        "next_due": card.due_at.isoformat(),
        "state": card.state,
        "interval_days": card.interval_days,
        "ease": card.ease,
    }


def serialize_card(card: MemoryCard) -> dict:
    return {
        "id": card.id,
        "subject_id": card.subject_id,
        "knowledge_id": card.knowledge_id,
        "card_type": card.card_type,
        "front": card.front,
        "back": card.back,
        "source_ref": card.source_ref,
        "state": card.state,
        "ease": card.ease,
        "interval_days": card.interval_days,
        "reps": card.reps,
        "lapses": card.lapses,
        "due_at": card.due_at.isoformat() if card.due_at else None,
    }


def list_cards(
    db: Session,
    user_id: int,
    due_only: bool = False,
    subject_id: int | None = None,
    limit: int = 200,
) -> list[MemoryCard]:
    stmt = select(MemoryCard).where(MemoryCard.user_id == user_id)
    if subject_id:
        stmt = stmt.where(MemoryCard.subject_id == subject_id)
    if due_only:
        stmt = stmt.where(MemoryCard.due_at.is_not(None), MemoryCard.due_at <= datetime.now(UTC))
        stmt = stmt.order_by(MemoryCard.due_at)
    else:
        stmt = stmt.order_by(MemoryCard.due_at)
    return list(db.scalars(stmt.limit(limit)))
