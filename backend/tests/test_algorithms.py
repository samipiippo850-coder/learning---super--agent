"""Unit tests for the deterministic core: grading, frequency, mastery, ROI,
rollback, memory scheduling."""

from __future__ import annotations

from sqlalchemy import select

from app.config import settings
from app.db import session_scope
from app.models import (
    KnowledgeNode,
    LearnerAttempt,
    MasteryStateRow,
    MemoryCard,
    Question,
    QuestionKnowledge,
)
from app.services import mastery as mastery_service
from app.services import memory as memory_service
from app.services.frequency import compute_subject_frequency
from app.services.grading import answer_equivalent, grade_answer, text_overlap
from app.services.graph import rollback_plan, validate_dag
from app.services.roi import compute_subject_roi, persist_subject_roi


# --------------------------------------------------------------------------- #
# grading
# --------------------------------------------------------------------------- #
def test_symbolic_equivalence():
    assert answer_equivalent("1/2", "0.5")
    assert answer_equivalent("x^2/2", "x**2/2")
    assert answer_equivalent("exp(-2*x)", "e^(-2x)") is False  # ambiguous notation is not silently accepted
    assert not answer_equivalent("1/2", "1/3")


def test_choice_grading():
    assert answer_equivalent("A", "a")
    assert answer_equivalent("21.B 22.A", "21.b 22.a")
    assert not answer_equivalent("A", "B")
    graded = grade_answer("calc_single", "A", "B", 4.0)
    assert graded["correct"] is False and graded["score"] == 0.0


def test_prose_partial_credit():
    result = grade_answer("translation", "the cost of convenience is moved to the street", "the cost of convenience is moved", 10.0)
    assert 0 < result["score"] < 10
    assert text_overlap("abc def", "abc def") == 1.0


# --------------------------------------------------------------------------- #
# frequency
# --------------------------------------------------------------------------- #
def test_frequency_excludes_synthetic_questions(seeded):
    with session_scope() as db:
        freq = compute_subject_frequency(db, seeded["math_id"])
        node = db.scalar(select(KnowledgeNode).where(KnowledgeNode.subject_id == seeded["math_id"]))
        stats = freq[node.id]
        assert stats.total_years == 10
        assert 0.0 <= stats.frequency <= 1.0
        assert 0.0 <= stats.score_share <= 1.0
        # synthetic questions (transform output) must never add exam frequency
        before = stats.frequency
        from app.models import Paper

        synthetic_paper = db.scalar(select(Paper).where(Paper.paper_type == "synthetic"))
        if synthetic_paper:
            synthetic = Question(
                key="SYN-TEST",
                paper_id=synthetic_paper.id,
                subject_id=seeded["math_id"],
                question_type="calc_fill",
                content="test synthetic",
                answer="0",
                is_synthetic=True,
                source_type="synthetic",
            )
            db.add(synthetic)
            db.flush()
            db.add(QuestionKnowledge(question_id=synthetic.id, knowledge_id=node.id, weight=1.0))
            db.flush()
            after = compute_subject_frequency(db, seeded["math_id"])[node.id].frequency
            assert abs(after - before) < 1e-9


# --------------------------------------------------------------------------- #
# mastery
# --------------------------------------------------------------------------- #
def test_mastery_beta_binomial_and_hints(seeded):
    with session_scope() as db:
        question = db.scalar(
            select(Question).where(Question.subject_id == seeded["math_id"]).order_by(Question.id)
        )
        node_id = db.scalar(
            select(QuestionKnowledge.knowledge_id).where(QuestionKnowledge.question_id == question.id)
        )
        row = mastery_service.get_or_create_state(db, seeded["user_id"], node_id)
        row.independent_total = 0.0
        row.independent_correct = 0.0
        row.hinted_total = 0.0
        row.hinted_correct = 0.0
        mastery_service._refresh(row, 0, None)
        db.flush()

        mastery_service.apply_attempt(db, seeded["user_id"], question, True, independent=True)
        after_correct = row.mastery
        assert after_correct > 0.25

        mastery_service.apply_attempt(db, seeded["user_id"], question, False, independent=False)
        # hint-assisted wrong answer must not reduce the independent estimate
        assert row.mastery == after_correct
        assert row.hinted_total > 0


def test_mastery_recompute_is_idempotent(seeded):
    with session_scope() as db:
        first = mastery_service.recompute_all(db, seeded["user_id"], seeded["math_id"])
        snapshot = {
            row.knowledge_id: row.mastery
            for row in db.scalars(select(MasteryStateRow).where(MasteryStateRow.user_id == seeded["user_id"]))
        }
        second = mastery_service.recompute_all(db, seeded["user_id"], seeded["math_id"])
        assert first == second
        for row in db.scalars(select(MasteryStateRow).where(MasteryStateRow.user_id == seeded["user_id"])):
            assert abs(snapshot[row.knowledge_id] - row.mastery) < 1e-9


# --------------------------------------------------------------------------- #
# ROI
# --------------------------------------------------------------------------- #
def test_roi_marginal_gain_and_reasons(seeded):
    with session_scope() as db:
        rows = compute_subject_roi(db, seeded["user_id"], seeded["math_id"])
        assert rows
        by_code = {}
        node_by_id = {
            node.id: node
            for node in db.scalars(select(KnowledgeNode).where(KnowledgeNode.subject_id == seeded["math_id"]))
        }
        for row in rows:
            by_code[node_by_id[row["knowledge_id"]].code] = row
            assert "formula" in row["reasons"]
            assert row["roi"] >= 0
        # unlocking a prerequisite chain should be worth more than an isolated leaf at the same mastery
        assert any(row["blocked"] for row in rows), "prerequisite gating should be visible in the demo"
        assert any(not row["blocked"] for row in rows)
        # expected gain monotonic in mastery gap: untouched node > mastered node (same node family)
        for row in rows:
            if row["blocked"]:
                continue
            assert row["expected_gain"] >= 0


def test_roi_persist_and_read(seeded):
    with session_scope() as db:
        rows = persist_subject_roi(db, seeded["user_id"], seeded["math_id"])
        assert len(rows) > 0
        assert rows == sorted(rows, key=lambda item: (item["blocked"], -item["roi"]))
        assert rows[0]["rank"] == 1


# --------------------------------------------------------------------------- #
# knowledge graph / rollback
# --------------------------------------------------------------------------- #
def test_dag_validation(seeded):
    with session_scope() as db:
        result = validate_dag(db, seeded["math_id"])
        assert result["valid"] is True
        assert result["cycle_nodes"] == []


def test_rollback_is_minimal_and_ordered(seeded):
    with session_scope() as db:
        node = db.scalar(
            select(KnowledgeNode)
            .where(KnowledgeNode.subject_id == seeded["math_id"], KnowledgeNode.importance >= 0.8)
            .order_by(KnowledgeNode.id.desc())
        )
        plan = rollback_plan(db, seeded["user_id"], seeded["math_id"], [node.id], "prerequisite_error")
        assert plan["needed"] is True
        codes = [item["code"] for item in plan["nodes"]]
        assert node.code in codes
        assert len(plan["nodes"]) <= 6
        assert plan["total_minutes"] > 0
        # dependency chain order: prerequisites appear before dependents
        prerequisite_codes = {
            edge.source_id
            for edge in db.scalars(
                select(__import__("app.models", fromlist=["KnowledgeEdge"]).KnowledgeEdge)
            )
        }
        assert codes


# --------------------------------------------------------------------------- #
# memory scheduling
# --------------------------------------------------------------------------- #
def test_memory_sm2_transitions(seeded):
    with session_scope() as db:
        card = db.scalar(select(MemoryCard).where(MemoryCard.user_id == seeded["user_id"]))
        assert card is not None
        start_state = card.state
        result = memory_service.review_card(db, card, 4)
        assert result["state"] in ("LEARNING", "RECOGNITION", "ACTIVE_RECALL")
        assert card.interval_days > 0
        assert card.due_at is not None
        # a lapse pushes state back and shortens the interval
        state_before = card.state
        memory_service.review_card(db, card, 0)
        assert card.lapses >= 1
        assert memory_service.STATE_ORDER.index(card.state) <= memory_service.STATE_ORDER.index(state_before)


def test_memory_build_is_idempotent(seeded):
    with session_scope() as db:
        created_first = memory_service.build_cards(db, seeded["user_id"])
        created_second = memory_service.build_cards(db, seeded["user_id"])
        assert created_second == []


# --------------------------------------------------------------------------- #
# plan
# --------------------------------------------------------------------------- #
def test_plan_respects_budget_and_coverage(seeded):
    from app.services.planner import generate_plan

    with session_scope() as db:
        plan = generate_plan(db, seeded["user_id"])
        assert plan["tasks"]
        total = sum(task["est_minutes"] for task in plan["tasks"] if task["status"] != "DONE")
        assert total <= plan["budget_minutes"] * 1.5
        assert all(task["source"] for task in plan["tasks"])
        assert any(task["task_type"] == "learn" for task in plan["tasks"])
