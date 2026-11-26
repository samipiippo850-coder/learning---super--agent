"""Test blueprint generation, grading and post-test diagnosis (PRD 13)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.constants import ErrorType, QuestionType, TaskType, TestType, TriggerType
from app.models import (
    KnowledgeNode,
    LearnerAttempt,
    MasteryStateRow,
    MemoryCard,
    Question,
    QuestionKnowledge,
    TestResult,
    TestSession,
    Trigger,
)
from app.services import mastery as mastery_service
from app.services.diagnose import classify_error
from app.services.grading import grade_answer
from app.services.graph import rollback_plan
from app.services.roi import get_roi, persist_subject_roi
from app.services.util import question_knowledge_map

BLUEPRINTS: dict[str, dict] = {
    TestType.MICRO: {
        "question_count": 5,
        "duration_min": 15,
        "mix": {"high_roi": 0.35, "recent_error": 0.25, "recent_learn": 0.2, "unseen": 0.2},
        "description": "每日微测：最近学习 + 高 ROI + 历史错题 + 未见题",
    },
    TestType.MIXED: {
        "question_count": 10,
        "duration_min": 30,
        "mix": {"high_roi": 0.4, "recent_error": 0.2, "due_review": 0.15, "combination": 0.15, "unseen": 0.1},
        "description": "混合小测：跨知识点组合 + 到期复习",
    },
    TestType.STAGE: {
        "question_count": 15,
        "duration_min": 60,
        "mix": {"high_roi": 0.35, "recent_error": 0.25, "due_review": 0.15, "combination": 0.15, "unseen": 0.1},
        "description": "阶段测试：整章覆盖与综合能力",
    },
    TestType.MOCK: {
        "question_count": 22,
        "duration_min": 120,
        "mix": {"high_roi": 0.4, "recent_error": 0.2, "due_review": 0.15, "combination": 0.15, "unseen": 0.1},
        "description": "综合模拟：按真题结构与分值权重抽题",
    },
    TestType.DIAGNOSTIC: {
        "question_count": 12,
        "duration_min": 45,
        "mix": {"syllabus_coverage": 0.7, "unseen": 0.3},
        "description": "初始诊断：覆盖全部章节，估计个人得分率起点",
    },
}


def generate_test(
    db: Session,
    user_id: int,
    subject_id: int,
    test_type: str = TestType.MICRO,
    duration_min: int | None = None,
    focus_knowledge_ids: list[int] | None = None,
) -> tuple[TestSession, list[Question]]:
    spec = BLUEPRINTS.get(test_type, BLUEPRINTS[TestType.MICRO])
    target_count = int(spec["question_count"])
    duration = duration_min or int(spec["duration_min"])

    roi_rows = get_roi(db, user_id, subject_id=subject_id, limit=500)
    if not roi_rows:
        roi_rows = persist_subject_roi(db, user_id, subject_id)
    roi_by_kid = {row["knowledge_id"]: row for row in roi_rows}

    mastery_rows = {
        row.knowledge_id: row
        for row in db.scalars(select(MasteryStateRow).where(MasteryStateRow.user_id == user_id))
    }
    recent_error_kids = _recent_error_knowledge(db, user_id, subject_id)
    recent_learn_kids = _recent_learning_knowledge(db, user_id, subject_id)
    due_review_kids = _due_review_knowledge(db, user_id, subject_id)
    focus = set(focus_knowledge_ids or [])

    # candidate pool: real questions of the subject
    pool = list(
        db.scalars(
            select(Question)
            .where(Question.subject_id == subject_id, Question.is_synthetic.is_(False))
            .order_by(Question.id)
        )
    )
    if not pool:
        raise ValueError("题库为空，请先导入或生成演示数据")

    q_links = {
        qid: [(kid, weight) for kid, weight, _core in question_knowledge_map(db, qid)]
        for qid in (question.id for question in pool)
    }

    # score each candidate by blueprint bucket
    scored: list[tuple[float, str, Question]] = []
    for question in pool:
        links = q_links.get(question.id, [])
        if not links:
            continue
        kids = [kid for kid, _w in links]
        high_roi = max(
            (roi_by_kid.get(kid, {}).get("roi", 0.0) for kid in kids),
            default=0.0,
        )
        mastery_avg = sum(
            (mastery_rows[kid].mastery if kid in mastery_rows else 0.2) for kid in kids
        ) / max(1, len(kids))
        touched_recently = any(kid in recent_learn_kids for kid in kids)
        is_due = any(kid in due_review_kids for kid in kids)
        is_error = any(kid in recent_error_kids for kid in kids)
        is_unseen = mastery_avg < 0.30
        is_combo = len(kids) >= 2

        bucket = "high_roi"
        if focus and any(kid in focus for kid in kids):
            bucket = "focus"
        elif is_error:
            bucket = "recent_error"
        elif is_due:
            bucket = "due_review"
        elif is_combo:
            bucket = "combination"
        elif is_unseen:
            bucket = "unseen"
        elif touched_recently:
            bucket = "recent_learn"

        # expected gain heuristic + difficulty matching
        score = high_roi * 2.0 + (1.0 - mastery_avg) * 0.8
        if bucket == "focus":
            score += 3.0
        elif bucket == "recent_error":
            score += 1.6
        elif bucket == "due_review":
            score += 1.2
        elif bucket == "unseen":
            score += 0.9
        if test_type in (TestType.STAGE, TestType.MOCK):
            score += float(question.score or 0) / 20.0
        scored.append((score, bucket, question))

    scored.sort(key=lambda item: item[0], reverse=True)
    selected: list[Question] = []
    reasons: list[dict] = []
    used: set[int] = set()
    bucket_counts: dict[str, int] = {}

    def add(question: Question, bucket: str, reason: str) -> None:
        if question.id in used or len(selected) >= target_count:
            return
        used.add(question.id)
        selected.append(question)
        reasons.append(
            {
                "question_id": question.id,
                "key": question.key,
                "bucket": bucket,
                "reason": reason,
                "score": round(float(question.score or 0), 1),
            }
        )
        bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1

    # first pass: fill the blueprint quota for each bucket from the best remaining candidates
    mix: dict[str, float] = spec["mix"]
    for bucket_name, share in mix.items():
        quota = max(1, round(target_count * share))
        for _score, bucket, question in scored:
            if bucket_counts.get(bucket_name, 0) >= quota or len(selected) >= target_count:
                break
            if question.id in used:
                continue
            add(
                question,
                bucket_name,
                f"{_reason_for_bucket(bucket_name, question, q_links)}（信号：{bucket}）",
            )
    # second pass: fill remaining slots by global score
    for _score, bucket, question in scored:
        if len(selected) >= target_count:
            break
        add(question, bucket, _reason_for_bucket(bucket, question, q_links))

    if not selected:
        raise ValueError("无法为该蓝图抽到题目，请先扩展题库")

    max_score = sum(float(q.score or 0) for q in selected)
    blueprint = {
        "test_type": test_type,
        "subject_id": subject_id,
        "description": spec["description"],
        "duration_min": duration,
        "mix_target": mix,
        "bucket_counts": bucket_counts,
        "selection": reasons,
        "question_count": len(selected),
        "max_score": round(max_score, 1),
        "generated_at": datetime.now(UTC).isoformat(),
    }
    test = TestSession(
        user_id=user_id,
        subject_id=subject_id,
        test_type=test_type,
        blueprint=blueprint,
        status="open",
        max_score=max_score,
    )
    db.add(test)
    db.commit()
    return test, selected


def _reason_for_bucket(bucket: str, question: Question, q_links: dict) -> str:
    kids = ", ".join(str(kid) for kid, _w in q_links.get(question.id, []))
    return {
        "high_roi": f"高 ROI 知识点组合（{len(kids.split(',')) if kids else 0} 个知识点）",
        "recent_error": "命中近期错题知识点",
        "recent_learn": "覆盖最近学习内容",
        "due_review": "间隔复习到期",
        "unseen": "未见题/低掌握度探测",
        "combination": "多知识点综合题",
        "focus": "用户指定重点",
        "syllabus_coverage": "考纲章节覆盖",
    }.get(bucket, "综合抽题")


def _recent_error_knowledge(db: Session, user_id: int, subject_id: int) -> set[int]:
    since = datetime.now(UTC) - timedelta(days=21)
    rows = db.execute(
        select(QuestionKnowledge.knowledge_id, func.count(QuestionKnowledge.knowledge_id))
        .join(LearnerAttempt, LearnerAttempt.question_id == QuestionKnowledge.question_id)
        .where(
            LearnerAttempt.user_id == user_id,
            LearnerAttempt.is_correct.is_(False),
            LearnerAttempt.created_at >= since,
        )
        .group_by(QuestionKnowledge.knowledge_id)
    ).all()
    return {kid for kid, count in rows if count >= 1}


def _recent_learning_knowledge(db: Session, user_id: int, subject_id: int) -> set[int]:
    since = datetime.now(UTC) - timedelta(days=3)
    rows = db.execute(
        select(QuestionKnowledge.knowledge_id)
        .join(LearnerAttempt, LearnerAttempt.question_id == QuestionKnowledge.question_id)
        .where(LearnerAttempt.user_id == user_id, LearnerAttempt.created_at >= since)
        .distinct()
    ).all()
    return {row[0] for row in rows}


def _due_review_knowledge(db: Session, user_id: int, subject_id: int) -> set[int]:
    now = datetime.now(UTC)
    rows = db.scalars(
        select(MemoryCard).where(
            MemoryCard.user_id == user_id,
            MemoryCard.subject_id == subject_id,
            MemoryCard.due_at.is_not(None),
            MemoryCard.due_at <= now,
        )
    ).all()
    return {card.knowledge_id for card in rows if card.knowledge_id}


def serialize_question(question: Question, with_answer: bool = False) -> dict:
    links = []
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
        "transform_level": question.transform_level,
        "parent_question_id": question.parent_question_id,
        "verification": question.verification or {},
        "knowledge": links,
    }
    if with_answer:
        payload["answer"] = question.answer
        payload["explanation"] = question.explanation
    return payload


def get_test(db: Session, test_id: int) -> tuple[TestSession, list[Question]]:
    test = db.get(TestSession, test_id)
    if test is None:
        raise ValueError("test not found")
    question_ids = [item["question_id"] for item in (test.blueprint or {}).get("selection", [])]
    questions = []
    for qid in question_ids:
        question = db.get(Question, qid)
        if question:
            questions.append(question)
    return test, questions


def submit_test(db: Session, test_id: int, answers: list[dict]) -> dict:
    test, questions = get_test(db, test_id)
    if test.status == "submitted":
        raise ValueError("test already submitted")
    by_id = {question.id: question for question in questions}
    subject_id = test.subject_id

    before_mastery: dict[int, float] = {}
    results: list[dict] = []
    diagnosis_items: list[dict] = []
    failing_knowledge: set[int] = set()
    error_types: dict[int, str] = {}
    total_score = 0.0

    for answer in answers:
        question = by_id.get(int(answer.get("question_id", 0)))
        if question is None:
            continue
        given = str(answer.get("answer_text") or "")
        graded = grade_answer(question.question_type, question.answer, given, float(question.score or 0))
        total_score += graded["score"]

        expected_minutes = max(1.0, float(question.score or 4) / 10.0 * 4.0)
        diagnosis = classify_error(
            question,
            given,
            graded["correct"],
            duration_sec=float(answer.get("duration_sec") or 0.0),
            hint_count=int(answer.get("hint_count") or 0),
            confidence_self=float(answer.get("confidence_self") or 0.5),
            expected_minutes=expected_minutes,
        )
        error_type = diagnosis["error_type"] or ErrorType.UNKNOWN

        for node, weight, _core in question_knowledge_map(db, question.id):
            before_mastery.setdefault(node.id, _mastery_now(db, test.user_id, node.id))
        mastery_updates = mastery_service.apply_attempt(
            db,
            test.user_id,
            question,
            is_correct=graded["correct"],
            independent=bool(answer.get("is_independent", True)) and int(answer.get("hint_count") or 0) == 0,
        )

        attempt = LearnerAttempt(
            user_id=test.user_id,
            question_id=question.id,
            test_id=test.id,
            answer_text=given,
            is_correct=graded["correct"],
            score_awarded=graded["score"],
            duration_sec=float(answer.get("duration_sec") or 0.0),
            hint_count=int(answer.get("hint_count") or 0),
            confidence_self=float(answer.get("confidence_self") or 0.5),
            error_type=error_type,
            is_independent=bool(answer.get("is_independent", True)) and int(answer.get("hint_count") or 0) == 0,
            source="test",
            meta={"method": graded["method"], "ratio": graded["ratio"]},
        )
        db.add(attempt)
        db.flush()

        db.add(
            TestResult(
                test_id=test.id,
                question_id=question.id,
                result="correct" if graded["correct"] else "incorrect",
                score=graded["score"],
                error_type=error_type,
                duration_sec=float(answer.get("duration_sec") or 0.0),
            )
        )

        if not graded["correct"]:
            for node, _w, _core in question_knowledge_map(db, question.id):
                failing_knowledge.add(node.id)
                error_types.setdefault(node.id, error_type)

        results.append(
            {
                "question_id": question.id,
                "key": question.key,
                "question_type": question.question_type,
                "correct": graded["correct"],
                "score": graded["score"],
                "max_score": float(question.score or 0),
                "method": graded["method"],
                "ratio": graded["ratio"],
                "answer": question.answer,
                "explanation": question.explanation,
                "knowledge": [node.code for node, _w, _core in question_knowledge_map(db, question.id)],
                "diagnosis": diagnosis,
                "mastery": mastery_updates,
            }
        )
        if diagnosis["error_type"]:
            diagnosis_items.append(
                {
                    "question_id": question.id,
                    "question_key": question.key,
                    "knowledge": [node.code for node, _w, _core in question_knowledge_map(db, question.id)],
                    **diagnosis,
                }
            )

    mastery_delta = []
    for kid, before in before_mastery.items():
        after = _mastery_now(db, test.user_id, kid)
        mastery_delta.append(
            {
                "knowledge_id": kid,
                "before": round(before, 4),
                "after": round(after, 4),
                "delta": round(after - before, 4),
            }
        )

    rollback: dict = {"needed": False, "nodes": [], "strategy": ""}
    if failing_knowledge:
        # aggregate one rollback plan from the most severe failed knowledge
        primary_error = max(
            error_types.values(),
            key=lambda err: list(ErrorType.ALL).index(err) if err in ErrorType.ALL else 99,
        )
        rollback = rollback_plan(
            db,
            test.user_id,
            subject_id,
            sorted(failing_knowledge),
            primary_error,
            max_nodes=6,
        )

    test.status = "submitted"
    test.score = round(total_score, 2)
    test.max_score = round(sum(float(q.score or 0) for q in questions), 2)
    test.submitted_at = datetime.now(UTC)

    next_actions = _next_actions(test, rollback, failing_knowledge, len(questions))
    report = {
        "score": test.score,
        "max_score": test.max_score,
        "accuracy": round(test.score / test.max_score, 4) if test.max_score else 0.0,
        "correct": sum(1 for r in results if r["correct"]),
        "question_count": len(questions),
        "results": results,
        "diagnosis": diagnosis_items,
        "rollback": rollback,
        "mastery_delta": mastery_delta,
        "next_actions": next_actions,
        "generated_at": datetime.now(UTC).isoformat(),
    }
    blueprint = dict(test.blueprint or {})
    blueprint["report"] = report
    test.blueprint = blueprint

    db.add(
        Trigger(
            user_id=test.user_id,
            trigger_type=TriggerType.TEST_FINISHED,
            event_key=f"test-finished-{test.id}",
            subject_id=subject_id,
            payload={"test_id": test.id, "score": test.score, "max_score": test.max_score},
            status="processed",
        )
    )
    db.commit()
    return report


def _mastery_now(db: Session, user_id: int, knowledge_id: int) -> float:
    row = db.scalar(
        select(MasteryStateRow).where(
            MasteryStateRow.user_id == user_id, MasteryStateRow.knowledge_id == knowledge_id
        )
    )
    return float(row.mastery) if row else 0.2


def _next_actions(test: TestSession, rollback: dict, failing: set[int], question_count: int) -> list[str]:
    actions: list[str] = []
    if rollback.get("needed"):
        codes = ", ".join(node["code"] for node in rollback.get("nodes", [])[:3])
        actions.append(f"按回滚计划修复最小必要知识：{codes}")
    if failing:
        actions.append("明日微测优先覆盖本次错误知识点")
    accuracy = (test.score / test.max_score) if test.max_score else 0.0
    if accuracy >= 0.85:
        actions.append("正确率良好：可提高题目难度或进入综合训练")
    elif accuracy < 0.5:
        actions.append("正确率偏低：暂停新知识，先做前置知识回滚")
    actions.append("重新计算 ROI 并生成明日计划")
    return actions


def test_summary(test: TestSession) -> dict:
    report = (test.blueprint or {}).get("report", {})
    return {
        "id": test.id,
        "subject_id": test.subject_id,
        "test_type": test.test_type,
        "status": test.status,
        "score": test.score,
        "max_score": test.max_score,
        "accuracy": report.get("accuracy"),
        "blueprint": {k: v for k, v in (test.blueprint or {}).items() if k != "report"},
        "created_at": test.created_at.isoformat(),
        "submitted_at": test.submitted_at.isoformat() if test.submitted_at else None,
    }
