"""Exam frequency / score-share statistics (PRD 7.1).

Only authentic or source-verified papers count as exam history. AI-generated
questions (``is_synthetic``) are excluded from every statistic here.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import KnowledgeNode, Paper, Question, QuestionKnowledge
from app.services.util import paper_trust, real_paper_years


@dataclass
class KnowledgeFrequency:
    knowledge_id: int
    code: str
    name: str
    subject_id: int
    frequency: float = 0.0
    score_share: float = 0.0
    years_present: list[int] = field(default_factory=list)
    total_years: int = 0
    question_count: int = 0
    score_sum: float = 0.0
    confidence: float = 0.0
    year_range: tuple[int, int] | None = None
    weight_year_max: int = 0

    def as_dict(self) -> dict:
        return {
            "knowledge_id": self.knowledge_id,
            "code": self.code,
            "name": self.name,
            "subject_id": self.subject_id,
            "frequency": round(self.frequency, 4),
            "score_share": round(self.score_share, 4),
            "years_present": self.years_present,
            "years_present_count": len(self.years_present),
            "total_years": self.total_years,
            "question_count": self.question_count,
            "score_sum": round(self.score_sum, 2),
            "confidence": round(self.confidence, 4),
            "year_range": list(self.year_range) if self.year_range else None,
            "stability_note": self._stability_note(),
        }

    def _stability_note(self) -> str:
        if len(self.years_present) <= 1:
            return "仅 1 年出现，不足以判定为稳定考点"
        if len(self.years_present) >= 6:
            return f"近 {len(self.years_present)}/{self.total_years} 年出现，考频稳定"
        return f"{len(self.years_present)}/{self.total_years} 年出现，需结合考纲判断"


def compute_subject_frequency(db: Session, subject_id: int, decay: float | None = None) -> dict[int, KnowledgeFrequency]:
    decay = settings.year_decay if decay is None else decay
    nodes = list(db.scalars(select(KnowledgeNode).where(KnowledgeNode.subject_id == subject_id).order_by(KnowledgeNode.id)))
    out: dict[int, KnowledgeFrequency] = {
        node.id: KnowledgeFrequency(
            knowledge_id=node.id, code=node.code, name=node.name, subject_id=subject_id
        )
        for node in nodes
    }
    years = real_paper_years(db, subject_id)
    if not years:
        return out

    year_max = max(years)
    year_weights = {y: decay ** (year_max - y) for y in years}
    total_weight = sum(year_weights.values())
    trust = paper_trust(db, subject_id)

    # weighted paper totals per year
    paper_totals: dict[int, float] = defaultdict(float)
    rows = db.execute(
        select(Paper.year, Paper.total_score).where(
            Paper.subject_id == subject_id, Paper.paper_type == "real"
        )
    ).all()
    for year, total in rows:
        if year:
            paper_totals[year] += float(total or 0.0)
    weighted_total = sum(year_weights.get(y, 0.0) * total for y, total in paper_totals.items())

    q_rows = db.execute(
        select(Question.id, Question.score, Question.is_synthetic, Paper.year)
        .join(Paper, Paper.id == Question.paper_id)
        .where(Paper.subject_id == subject_id, Paper.paper_type == "real", Question.is_synthetic.is_(False))
    ).all()
    question_year = {qid: year for qid, _score, _syn, year in q_rows}
    question_score = {qid: float(score or 0.0) for qid, score, _syn, _year in q_rows}

    links = db.execute(
        select(QuestionKnowledge.question_id, QuestionKnowledge.knowledge_id, QuestionKnowledge.weight).where(
            QuestionKnowledge.question_id.in_(list(question_year.keys()))
        )
    ).all() if question_year else []

    seen_years: dict[int, set[int]] = defaultdict(set)
    score_sum: dict[int, float] = defaultdict(float)
    qcount: dict[int, int] = defaultdict(int)
    weighted_score: dict[int, float] = defaultdict(float)
    for qid, kid, weight in links:
        stats = out.get(kid)
        if stats is None:
            continue
        year = question_year[qid]
        w = float(weight)
        qcount[kid] += 1
        score_sum[kid] += question_score[qid] * w
        weighted_score[kid] += year_weights.get(year, 0.0) * question_score[qid] * w
        seen_years[kid].add(year)

    for kid, stats in out.items():
        present = sorted(seen_years.get(kid, set()))
        stats.years_present = present
        stats.total_years = len(years)
        stats.question_count = qcount.get(kid, 0)
        stats.score_sum = score_sum.get(kid, 0.0)
        stats.weight_year_max = year_max
        stats.year_range = (min(years), max(years)) if years else None
        stats.frequency = (
            sum(year_weights[y] for y in present) / total_weight if total_weight else 0.0
        )
        stats.score_share = (
            weighted_score.get(kid, 0.0) / weighted_total if weighted_total else 0.0
        )
        coverage = len(present) / max(1, len(years))
        # confidence grows with year coverage, question count and source trust
        stats.confidence = round(
            min(1.0, 0.25 * trust + 0.45 * coverage + 0.30 * min(1.0, qcount.get(kid, 0) / 8.0)), 4
        )
    return out


def frequency_for_questions(
    db: Session, question_ids: list[int], subject_id: int
) -> dict[int, float]:
    """Probability that a question unit is tested, used by the ROI engine.

    Estimated as the weighted frequency of its knowledge points, i.e. how often
    the underlying content appears in the historical paper series.
    """
    freq = compute_subject_frequency(db, subject_id)
    links = db.execute(
        select(QuestionKnowledge.question_id, QuestionKnowledge.knowledge_id, QuestionKnowledge.weight).where(
            QuestionKnowledge.question_id.in_(question_ids)
        )
    ).all() if question_ids else []
    weights: dict[int, float] = defaultdict(float)
    for qid, kid, weight in links:
        stats = freq.get(kid)
        if stats:
            weights[qid] += float(weight) * stats.frequency
    return {qid: min(1.0, weights.get(qid, 0.0)) for qid in question_ids}
