"""Mastery estimation (PRD 7.2).

Beta-Binomial posterior mean over *independent* attempts:

    p_i = (alpha_0 + s_i) / (alpha_0 + beta_0 + n_i)

Hints, repeated memorisation of the same item and answer-revealed reviews are
tracked separately and never inflate the main mastery estimate.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.constants import MasteryState
from app.models import KnowledgeNode, LearnerAttempt, MasteryStateRow, QuestionKnowledge
from app.services.util import question_knowledge_map


def _state_for(mastery: float, n: float, correct_streak: int, last_tested: datetime | None) -> str:
    if n < 0.5:
        return MasteryState.UNSEEN
    if mastery < 0.40:
        return MasteryState.LEARNING
    if mastery < 0.60:
        return MasteryState.RECOGNITION
    if mastery < 0.75:
        return MasteryState.ACTIVE_RECALL
    if mastery < 0.90 or correct_streak < 3:
        return MasteryState.APPLICATION
    # STABLE also requires a delayed test (>=3 days since first stable signal)
    if last_tested is not None and last_tested > datetime.now(UTC) - timedelta(days=2):
        return MasteryState.APPLICATION
    return MasteryState.STABLE


def get_or_create_state(db: Session, user_id: int, knowledge_id: int) -> MasteryStateRow:
    row = db.scalar(
        select(MasteryStateRow).where(
            MasteryStateRow.user_id == user_id, MasteryStateRow.knowledge_id == knowledge_id
        )
    )
    if row:
        return row
    row = MasteryStateRow(
        user_id=user_id,
        knowledge_id=knowledge_id,
        alpha=settings.mastery_prior_alpha,
        beta=settings.mastery_prior_beta,
        mastery=0.2,
        state=MasteryState.UNSEEN,
    )
    db.add(row)
    db.flush()
    return row


def _refresh(row: MasteryStateRow, correct_streak: int = 0, tested_at: datetime | None = None) -> None:
    alpha0 = settings.mastery_prior_alpha
    beta0 = settings.mastery_prior_beta
    row.alpha = alpha0 + row.independent_correct
    row.beta = beta0 + (row.independent_total - row.independent_correct)
    row.mastery = (alpha0 + row.independent_correct) / (alpha0 + beta0 + row.independent_total)
    row.confidence = min(1.0, row.independent_total / 6.0)
    row.state = _state_for(row.mastery, row.independent_total, correct_streak, tested_at)
    if row.state == MasteryState.STABLE and row.stable_since is None:
        row.stable_since = datetime.now(UTC)
    elif row.state != MasteryState.STABLE:
        row.stable_since = None


def apply_attempt(
    db: Session,
    user_id: int,
    question,
    is_correct: bool,
    independent: bool = True,
    weight_scale: float = 1.0,
) -> list[dict]:
    """Update mastery of every knowledge point the question is tagged with.

    ``weight_scale`` lets callers attenuate evidence (e.g. review of a memorised
    original question counts less than a fresh problem).
    """
    updated: list[dict] = []
    now = datetime.now(UTC)
    for node, weight, _is_core in question_knowledge_map(db, question.id):
        row = get_or_create_state(db, user_id, node.id)
        w = max(0.05, float(weight)) * max(0.05, weight_scale)
        if independent:
            row.independent_total += w
            if is_correct:
                row.independent_correct += w
        else:
            row.hinted_total += w
            if is_correct:
                row.hinted_correct += w
        row.last_tested_at = now
        # streak only counts full independent correct answers
        streak = 3 if (is_correct and independent and w >= 0.4) else 0
        _refresh(row, correct_streak=streak, tested_at=now)
        updated.append(
            {
                "knowledge_id": node.id,
                "code": node.code,
                "name": node.name,
                "mastery": round(row.mastery, 4),
                "state": row.state,
            }
        )
    return updated


def recompute_all(db: Session, user_id: int, subject_id: int | None = None) -> int:
    """Rebuild mastery rows from the full attempt history (idempotent)."""
    stmt = (
        select(LearnerAttempt, QuestionKnowledge, KnowledgeNode)
        .join(QuestionKnowledge, QuestionKnowledge.question_id == LearnerAttempt.question_id)
        .join(KnowledgeNode, KnowledgeNode.id == QuestionKnowledge.knowledge_id)
        .where(LearnerAttempt.user_id == user_id)
    )
    if subject_id:
        from app.models import ExamSubject

        subject_ids = set(
            db.scalars(select(ExamSubject.id).where(ExamSubject.id == subject_id))
        )
        stmt = stmt.where(LearnerAttempt.question_id.in_(
            select(QuestionKnowledge.question_id).where(QuestionKnowledge.knowledge_id.in_(
                select(KnowledgeNode.id).where(KnowledgeNode.subject_id.in_(subject_ids))
            ))
        ))

    rows = db.execute(stmt.order_by(LearnerAttempt.created_at, LearnerAttempt.id)).all()
    touched: dict[int, MasteryStateRow] = {}
    for attempt, link, node in rows:
        row = touched.get(node.id)
        if row is None:
            row = get_or_create_state(db, user_id, node.id)
            row.independent_correct = 0.0
            row.independent_total = 0.0
            row.hinted_correct = 0.0
            row.hinted_total = 0.0
            touched[node.id] = row
        w = max(0.05, float(link.weight))
        if attempt.is_independent:
            row.independent_total += w
            if attempt.is_correct:
                row.independent_correct += w
        else:
            row.hinted_total += w
            if attempt.is_correct:
                row.hinted_correct += w
        row.last_tested_at = attempt.created_at
        _refresh(row, correct_streak=3 if attempt.is_correct and attempt.is_independent else 0, tested_at=attempt.created_at)

    # nodes with no attempts reset to prior
    node_ids = select(KnowledgeNode.id)
    if subject_id:
        node_ids = node_ids.where(KnowledgeNode.subject_id == subject_id)
    for (node_id,) in db.execute(node_ids).all():
        if node_id in touched:
            continue
        row = get_or_create_state(db, user_id, node_id)
        row.independent_correct = 0.0
        row.independent_total = 0.0
        row.hinted_correct = 0.0
        row.hinted_total = 0.0
        _refresh(row, 0, None)
    db.commit()
    return len(touched)


def mastery_rows(db: Session, user_id: int, subject_id: int | None = None) -> list[MasteryStateRow]:
    stmt = select(MasteryStateRow).where(MasteryStateRow.user_id == user_id)
    if subject_id:
        stmt = stmt.join(KnowledgeNode, KnowledgeNode.id == MasteryStateRow.knowledge_id).where(
            KnowledgeNode.subject_id == subject_id
        )
    return list(db.scalars(stmt))


def summary(db: Session, user_id: int, subject_id: int | None = None) -> dict:
    rows = mastery_rows(db, user_id, subject_id)
    by_state: dict[str, int] = {state: 0 for state in MasteryState.ALL}
    for row in rows:
        by_state[row.state] = by_state.get(row.state, 0) + 1
    avg = sum(r.mastery for r in rows) / len(rows) if rows else 0.0
    return {
        "average": round(avg, 4),
        "by_state": by_state,
        "knowledge_total": len(rows),
        "stable": by_state.get(MasteryState.STABLE, 0),
        "studied": sum(1 for r in rows if r.independent_total > 0),
    }
