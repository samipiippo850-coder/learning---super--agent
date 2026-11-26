"""Exam ROI engine (PRD 7.3 / 7.4).

Expected exam score of a knowledge set K is modelled over *question units*
(real historical questions):

    V(K) = sum_q  pi_q * m_q * p_hat_q(K)
    p_hat_q(K) = sum_{i in knowledge(q)} w_qi * m_i

where pi_q is the estimated probability the question unit is tested, m_q its
historical score and m_i the learner's estimated mastery. The marginal value of
learning knowledge point i is computed as a finite difference
V(K ∪ {i}) - V(K), so knowledge points that unlock many high-value questions
dominate the ranking instead of simply summing isolated scores.

ROI_i = C_i * DeltaV_i / H_i
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import settings
from app.constants import RelationType
from app.models import (
    KnowledgeEdge,
    KnowledgeNode,
    MasteryStateRow,
    Question,
    QuestionKnowledge,
    RoiSnapshot,
)
from app.services.frequency import compute_subject_frequency
from app.services.util import real_paper_years


def _load_subject_graph(db: Session, subject_id: int):
    nodes = list(db.scalars(select(KnowledgeNode).where(KnowledgeNode.subject_id == subject_id).order_by(KnowledgeNode.id)))
    node_ids = [n.id for n in nodes]
    edges = list(
        db.scalars(
            select(KnowledgeEdge).where(
                KnowledgeEdge.source_id.in_(node_ids + [0]), KnowledgeEdge.target_id.in_(node_ids + [0])
            )
        )
    )
    prerequisites: dict[int, list[tuple[int, float]]] = defaultdict(list)
    for edge in edges:
        if edge.relation_type == RelationType.PREREQUISITE:
            prerequisites[edge.target_id].append((edge.source_id, float(edge.weight)))
    return nodes, prerequisites


def _load_units(db: Session, subject_id: int):
    """Real-question units with pi_q and m_q estimates."""
    freq = compute_subject_frequency(db, subject_id)
    years = real_paper_years(db, subject_id)
    year_max = max(years) if years else 0
    decay = settings.year_decay

    rows = db.execute(
        select(Question.id, Question.score, Question.difficulty, Question.paper_id)
        .join(Question.paper)
        .where(
            Question.subject_id == subject_id,
            Question.is_synthetic.is_(False),
        )
    ).all() if years else []
    # attach year via paper map
    from app.models import Paper

    paper_years = dict(db.execute(select(Paper.id, Paper.year).where(Paper.subject_id == subject_id)).all())
    unit_links = db.execute(
        select(QuestionKnowledge.question_id, QuestionKnowledge.knowledge_id, QuestionKnowledge.weight).where(
            QuestionKnowledge.question_id.in_([r[0] for r in rows] + [0])
        )
    ).all()

    links: dict[int, list[tuple[int, float]]] = defaultdict(list)
    for qid, kid, weight in unit_links:
        links[qid].append((kid, float(weight)))

    units = []
    for qid, score, difficulty, _paper in rows:
        knowledge = links.get(qid, [])
        if not knowledge:
            continue
        year = paper_years.get(_paper) or 0
        w_year = decay ** (year_max - year) if year_max and year else 1.0
        pi = sum(w * freq[kid].frequency for kid, w in knowledge if kid in freq)
        units.append(
            {
                "question_id": qid,
                "score": float(score or 0.0),
                "difficulty": float(difficulty or 0.5),
                "year": year,
                "year_weight": w_year,
                "pi": pi,
                "knowledge": knowledge,
            }
        )
    return units, freq


def _value(K: dict[int, float], units: list[dict], target: dict[int, float]) -> float:
    total = 0.0
    for unit in units:
        p = 0.0
        for kid, w in unit["knowledge"]:
            m = target.get(kid, K.get(kid, 0.0))
            p += w * m
        total += unit["pi"] * unit["score"] * min(1.0, p)
    return total


def compute_subject_roi(db: Session, user_id: int, subject_id: int) -> list[dict]:
    nodes, prerequisites = _load_subject_graph(db, subject_id)
    units, freq = _load_units(db, subject_id)
    mastery_rows = {
        row.knowledge_id: row
        for row in db.scalars(
            select(MasteryStateRow).where(MasteryStateRow.user_id == user_id)
        )
    }
    current: dict[int, float] = {n.id: 0.0 for n in nodes}
    for nid in current:
        row = mastery_rows.get(nid)
        current[nid] = float(row.mastery) if row else 0.2

    target = settings.target_mastery
    gate = settings.prerequisite_gate
    base_value = _value(current, units, current)

    results: list[dict] = []
    for node in nodes:
        m0 = current[node.id]
        gap = max(0.0, target - m0)
        # hypothetical mastery vector after studying this node
        after = dict(current)
        after[node.id] = target
        delta_value = _value(current, units, after) - base_value

        # prerequisite gating
        blocked_edges = [
            (pid, weight) for pid, weight in prerequisites.get(node.id, [])
            if current.get(pid, 0.0) < gate
        ]
        blocked = bool(blocked_edges)
        if blocked:
            penalty = 0.55 ** len(blocked_edges)
            delta_value *= penalty

        stats = freq.get(node.id)
        frequency = stats.frequency if stats else 0.0
        score_share = stats.score_share if stats else 0.0
        data_conf = stats.confidence if stats else 0.2
        mastery_conf = mastery_rows[node.id].confidence if node.id in mastery_rows else 0.0
        confidence = round(0.65 * data_conf + 0.35 * max(mastery_conf, 0.15), 4)

        # hours needed to reach target mastery from current state
        remaining = max(0.15, (target - m0) / max(0.01, target - 0.2))
        hours = round(max(0.25, (node.est_minutes / 60.0) * remaining), 3)

        roi = 0.0 if hours <= 0 else (confidence * delta_value) / hours
        reasons = {
            "frequency": round(frequency, 4),
            "score_share": round(score_share, 4),
            "gap": round(gap, 4),
            "expected_gain": round(delta_value, 3),
            "hours": hours,
            "confidence": confidence,
            "prerequisite": (
                "前置知识未达标: " + ", ".join(
                    next((n.code for n in nodes if n.id == pid), str(pid)) for pid, _ in blocked_edges
                )
            ) if blocked else "前置知识已满足",
            "data_basis": (stats._stability_note() if stats else "无历史考频数据"),
            "formula": [
                f"V(K) 基线 = {base_value:.2f} 分",
                f"ΔV({node.code}) = {delta_value:.2f} 分（边际收益）",
                f"ROI = C({confidence:.2f}) × ΔV({delta_value:.2f}) / H({hours:.2f}h) = {roi:.2f}",
            ],
        }
        results.append(
            {
                "knowledge_id": node.id,
                "code": node.code,
                "name": node.name,
                "subject_id": subject_id,
                "chapter": node.chapter,
                "mastery": round(m0, 4),
                "frequency": round(frequency, 4),
                "score_share": round(score_share, 4),
                "expected_gain": round(delta_value, 3),
                "hours": hours,
                "roi": round(roi, 4),
                "confidence": confidence,
                "blocked": blocked,
                "reasons": reasons,
            }
        )

    results.sort(key=lambda item: (item["blocked"], -item["roi"]))
    for rank, item in enumerate(results, start=1):
        item["rank"] = rank
    return results


def persist_subject_roi(db: Session, user_id: int, subject_id: int) -> list[dict]:
    results = compute_subject_roi(db, user_id, subject_id)
    now = datetime.now(UTC)
    db.execute(
        delete(RoiSnapshot).where(RoiSnapshot.user_id == user_id, RoiSnapshot.subject_id == subject_id)
    )
    for item in results:
        db.add(
            RoiSnapshot(
                user_id=user_id,
                subject_id=subject_id,
                knowledge_id=item["knowledge_id"],
                frequency=item["frequency"],
                score_share=item["score_share"],
                expected_gain=item["expected_gain"],
                hours=item["hours"],
                roi=item["roi"],
                confidence=item["confidence"],
                rank=item["rank"],
                blocked=item["blocked"],
                reasons=item["reasons"],
                computed_at=now,
            )
        )
    db.commit()
    return results


def get_roi(
    db: Session,
    user_id: int,
    subject_id: int | None = None,
    limit: int = 50,
    include_blocked: bool = True,
) -> list[dict]:
    stmt = (
        select(RoiSnapshot, KnowledgeNode)
        .join(KnowledgeNode, KnowledgeNode.id == RoiSnapshot.knowledge_id)
        .where(RoiSnapshot.user_id == user_id)
    )
    if subject_id:
        stmt = stmt.where(RoiSnapshot.subject_id == subject_id)
    if not include_blocked:
        stmt = stmt.where(RoiSnapshot.blocked.is_(False))
    rows = db.execute(stmt.order_by(RoiSnapshot.roi.desc())).all()
    if not rows:
        return []
    latest = max(row.RoiSnapshot.computed_at for row in rows)
    out = []
    for snapshot, node in rows:
        if snapshot.computed_at != latest:
            continue
        out.append(
            {
                "knowledge_id": snapshot.knowledge_id,
                "code": node.code,
                "name": node.name,
                "subject_id": snapshot.subject_id,
                "chapter": node.chapter,
                "frequency": snapshot.frequency,
                "score_share": snapshot.score_share,
                "expected_gain": snapshot.expected_gain,
                "hours": snapshot.hours,
                "roi": snapshot.roi,
                "confidence": snapshot.confidence,
                "rank": snapshot.rank,
                "blocked": snapshot.blocked,
                "reasons": snapshot.reasons,
                "computed_at": snapshot.computed_at.isoformat(),
            }
        )
    out.sort(key=lambda item: (item["blocked"], -item["roi"]))
    for position, item in enumerate(out, start=1):
        item["rank"] = position
    return out[:limit]
