"""Tests for the test engine, question transformation and verification."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db import session_scope
from app.models import Question, TestSession
from app.services import tests as test_service
from app.services.transform import generate_variants
from app.services.verify import verify_generated


def test_blueprint_has_mix_and_reasons(seeded):
    with session_scope() as db:
        test, questions = test_service.generate_test(
            db, seeded["user_id"], seeded["math_id"], test_type="micro"
        )
        blueprint = test.blueprint
        assert len(questions) == blueprint["question_count"] == 5
        assert len(blueprint["selection"]) == 5
        assert all(item["reason"] for item in blueprint["selection"])
        assert len(blueprint["bucket_counts"]) >= 2, blueprint["bucket_counts"]
        assert test.max_score == sum(float(question.score) for question in questions)


def test_submit_grades_and_updates_mastery(seeded):
    with session_scope() as db:
        test, questions = test_service.generate_test(
            db, seeded["user_id"], seeded["math_id"], test_type="micro"
        )
        answers = []
        for index, question in enumerate(questions):
            answers.append(
                {
                    "question_id": question.id,
                    "answer_text": question.answer if index % 2 == 0 else "0",
                    "duration_sec": 60,
                    "hint_count": 0,
                    "confidence_self": 0.6,
                    "is_independent": True,
                }
            )
        report = test_service.submit_test(db, test.id, answers)
        assert report["score"] <= report["max_score"]
        assert report["correct"] + (len(questions) - report["correct"]) == len(questions)
        assert len(report["results"]) == len(questions)
        assert report["mastery_delta"]
        assert report["next_actions"]
        stored = db.get(TestSession, test.id)
        assert stored.status == "submitted"
        assert stored.blueprint["report"]["score"] == report["score"]


def test_submit_twice_is_rejected(seeded):
    with session_scope() as db:
        test, questions = test_service.generate_test(
            db, seeded["user_id"], seeded["math_id"], test_type="micro"
        )
        payload = [
            {
                "question_id": question.id,
                "answer_text": "0",
                "duration_sec": 30,
                "hint_count": 0,
                "confidence_self": 0.5,
                "is_independent": True,
            }
            for question in questions
        ]
        test_service.submit_test(db, test.id, payload)
        with pytest.raises(ValueError):
            test_service.submit_test(db, test.id, payload)


def test_incorrect_test_triggers_rollback(seeded):
    with session_scope() as db:
        test, questions = test_service.generate_test(
            db, seeded["user_id"], seeded["math_id"], test_type="mixed"
        )
        payload = [
            {
                "question_id": question.id,
                "answer_text": "",
                "duration_sec": 30,
                "hint_count": 0,
                "confidence_self": 0.9,
                "is_independent": True,
            }
            for question in questions
        ]
        report = test_service.submit_test(db, test.id, payload)
        assert report["rollback"]["needed"] is True
        assert report["rollback"]["nodes"]
        assert report["diagnosis"]


def test_transform_all_levels_verified(seeded, math_question):
    with session_scope() as db:
        for level in ("L1", "L2", "L3", "L4"):
            created = generate_variants(db, seeded["user_id"], math_question["id"], level=level, count=1)
            assert created, level
            item = created[0]
            question = item["question"]
            assert question.is_synthetic is True
            assert question.parent_question_id == math_question["id"]
            assert question.transform_level == level
            assert item["verification"]["status"] == "verified"
            checks = {check["check"]: check["passed"] for check in item["verification"]["checks"]}
            assert checks.get("answer_present") is True
            assert checks.get("independent_solve") is True
            assert checks.get("syllabus_scope") is True
            # verification never claims confidence 0 for accepted questions
            assert item["verification"]["confidence"] > 0


def test_transform_unsupported_subject_is_rejected(seeded):
    with session_scope() as db:
        english_question = db.scalar(
            select(Question).where(Question.subject_id == seeded["english_id"]).order_by(Question.id)
        )
        with pytest.raises(ValueError):
            generate_variants(db, seeded["user_id"], english_question.id, level="L2", count=1)


def test_verify_rejects_wrong_answer(seeded):
    with session_scope() as db:
        result = verify_generated(
            db,
            seeded["math_id"],
            "计算 1+1 的值。",
            "3",
            2,
            ["MA-CALC-LIMIT"],
        )
        assert result["status"] == "rejected"
        checks = {check["check"]: check["passed"] for check in result["checks"]}
        assert checks["independent_solve"] is False


def test_verify_rejects_unknown_knowledge_scope(seeded):
    with session_scope() as db:
        result = verify_generated(db, seeded["math_id"], "计算 1+1。", "2", 2, ["NOT-A-REAL-CODE"])
        assert result["status"] == "rejected"
        checks = {check["check"]: check["passed"] for check in result["checks"]}
        assert checks["syllabus_scope"] is False
