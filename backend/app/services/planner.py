"""Daily study plan generation (PRD 7.4 step 8, 18 Phase 2).

Order of construction:
1. filter knowledge points whose prerequisites are satisfied (ROI ordering)
2. allocate the daily budget: learn/practice > review > test > syllabus coverage
3. add spaced-repetition reviews that are due today
4. schedule periodic tests when the cadence requires one
5. enforce a syllabus coverage floor so high-frequency drilling never hides
   untouched chapters
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.constants import MasteryState, TaskStatus, TaskType, TestType
from app.models import (
    ExamSubject,
    KnowledgeNode,
    LearnerAttempt,
    MasteryStateRow,
    MemoryCard,
    Question,
    RoiSnapshot,
    StudyTask,
    TestSession,
)
from app.services.roi import get_roi, persist_subject_roi
from app.services.util import due_cards, subjects_of


def _plan_date(value: str | None) -> str:
    return value or date.today().isoformat()


def ensure_roi(db: Session, user_id: int, subject_id: int) -> list[dict]:
    existing = get_roi(db, user_id, subject_id=subject_id, limit=500)
    if existing:
        return existing
    return persist_subject_roi(db, user_id, subject_id)


def due_tests(db: Session, user_id: int) -> list[dict]:
    """Periodic test cadence: daily micro, 3-5 day mixed, weekly stage, 2-4 week mock."""
    now = datetime.now(UTC)
    last = {
        row[0]: row[1]
        for row in db.execute(
            select(TestSession.test_type, func.max(TestSession.created_at)).where(
                TestSession.user_id == user_id
            ).group_by(TestSession.test_type)
        ).all()
    }
    due: list[dict] = []
    micro = last.get(TestType.MICRO)
    if micro is None or (now - _as_aware(micro)) >= timedelta(hours=20):
        due.append({"test_type": TestType.MICRO, "reason": "每日微测未完成"})
    mixed = last.get(TestType.MIXED)
    if mixed is None or (now - _as_aware(mixed)) >= timedelta(days=3):
        due.append({"test_type": TestType.MIXED, "reason": "混合小测到期（每 3-5 天）"})
    stage = last.get(TestType.STAGE)
    if stage is not None and (now - _as_aware(stage)) >= timedelta(days=7):
        due.append({"test_type": TestType.STAGE, "reason": "阶段测试到期（每周）"})
    mock = last.get(TestType.MOCK)
    if mock is None or (now - _as_aware(mock)) >= timedelta(days=21):
        due.append({"test_type": TestType.MOCK, "reason": "综合模拟到期（每 2-4 周）"})
    return due


def _as_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def generate_plan(
    db: Session,
    user_id: int,
    plan_date: str | None = None,
    subject_id: int | None = None,
    budget_minutes: int | None = None,
) -> dict:
    plan_date = _plan_date(plan_date)
    budget = int(budget_minutes or settings.daily_minutes)

    # idempotent: drop stale unfinished tasks for this day
    stmt = delete(StudyTask).where(
        StudyTask.user_id == user_id,
        StudyTask.plan_date == plan_date,
        StudyTask.status.in_([TaskStatus.TODO, TaskStatus.DOING]),
    )
    if subject_id:
        stmt = stmt.where(StudyTask.subject_id == subject_id)
    db.execute(stmt)
    db.flush()

    subjects = subjects_of(db, user_id)
    if subject_id:
        subjects = [s for s in subjects if s.id == subject_id]
    if not subjects:
        raise ValueError("没有可用的考试科目，请先初始化考试目标")

    due_cards_list = due_cards(db, user_id)
    due_tests_list = due_tests(db, user_id) if not subject_id else [
        item for item in due_tests(db, user_id) if item["test_type"] != TestType.MOCK
    ]

    review_budget = int(budget * (0.15 if due_cards_list else 0.0))
    test_budget = int(budget * (0.10 if due_tests_list else 0.0))
    coverage_budget = int(budget * 0.10)
    learn_budget = max(30, budget - review_budget - test_budget - coverage_budget)

    tasks: list[StudyTask] = []
    used = 0.0

    def add_task(**kwargs) -> None:
        nonlocal used
        est = float(kwargs.get("est_minutes", 30))
        if used + est > budget * 1.05 and kwargs.get("task_type") not in (TaskType.REVIEW, TaskType.TEST):
            return
        task = StudyTask(user_id=user_id, plan_date=plan_date, **kwargs)
        db.add(task)
        tasks.append(task)
        used += est

    # --- 1/2. ROI-ordered learning & practice ---------------------------------
    coverage_chapters: dict[int, list[dict]] = {}
    for subject in subjects:
        roi_rows = ensure_roi(db, user_id, subject.id)
        mastery = {
            row.knowledge_id: row
            for row in db.scalars(
                select(MasteryStateRow).where(
                    MasteryStateRow.user_id == user_id,
                    MasteryStateRow.knowledge_id.in_([r["knowledge_id"] for r in roi_rows] or [0]),
                )
            )
        }
        enriched: list[dict] = []
        for row in roi_rows:
            mastery_row = mastery.get(row["knowledge_id"])
            enriched.append({**row, "mastery": mastery_row.mastery if mastery_row else 0.2})
        coverage_chapters.setdefault(subject.id, []).extend(enriched)

        open_rows = [row for row in enriched if not row["blocked"] and row["mastery"] < settings.target_mastery]
        for row in open_rows:
            if used >= learn_budget:
                break
            node = db.get(KnowledgeNode, row["knowledge_id"])
            if node is None:
                continue
            is_learn = row["mastery"] < 0.40
            hours = max(0.25, row["hours"])
            est = max(20.0, min(90.0, hours * 60.0 * 0.5))
            add_task(
                subject_id=subject.id,
                knowledge_id=node.id,
                task_type=TaskType.LEARN if is_learn else TaskType.PRACTICE,
                title=f"{'学习' if is_learn else '练习'} · {node.name}",
                detail=(
                    f"ROI {row['roi']:.2f}｜考频 {row['frequency']:.2f}｜分值占比 {row['score_share']:.2f}｜"
                    f"当前掌握度 {row['mastery']:.2f}｜预计收益 {row['expected_gain']:.1f} 分"
                ),
                est_minutes=est,
                priority=float(row["roi"]),
                status=TaskStatus.TODO,
                source="roi_planner",
                payload={"knowledge_id": node.id, "roi": row["roi"], "chapter": node.chapter},
            )

    # --- 3. spaced repetition reviews ----------------------------------------
    if due_cards_list:
        import math

        per_subject: dict[int, int] = {}
        for card in due_cards_list:
            per_subject[card.subject_id] = per_subject.get(card.subject_id, 0) + 1
        share = review_budget / max(1, len(per_subject))
        for sid, count in per_subject.items():
            subject = next((s for s in subjects if s.id == sid), None)
            add_task(
                subject_id=sid,
                knowledge_id=None,
                task_type=TaskType.REVIEW,
                title=f"间隔复习 · {subject.name if subject else sid}（{count} 张卡片）",
                detail="主动回忆优先，看答案后的熟悉感不计入掌握",
                est_minutes=max(10.0, min(share, count * 1.5)),
                priority=0.9,
                status=TaskStatus.TODO,
                source="memory_engine",
                payload={"due_cards": count},
            )

    # --- 4. periodic tests ----------------------------------------------------
    for item in due_tests_list:
        add_task(
            subject_id=subjects[0].id if subjects else None,
            knowledge_id=None,
            task_type=TaskType.TEST,
            title=f"周期测试 · {_test_label(item['test_type'])}",
            detail=item["reason"] + "（由测试蓝图生成，非随机抽题）",
            est_minutes=15.0 if item["test_type"] == TestType.MICRO else 30.0,
            priority=0.85,
            status=TaskStatus.TODO,
            source="test_designer",
            payload={"test_type": item["test_type"]},
        )

    # --- 5. syllabus coverage floor -----------------------------------------
    per_subject_coverage = max(1, int(coverage_budget / max(1, len(subjects))))
    for sid, rows in coverage_chapters.items():
        chapters = {row["chapter"] for row in rows}
        mastered_chapters = {row["chapter"] for row in rows if row["mastery"] >= settings.prerequisite_gate}
        missing = chapters - mastered_chapters
        if not missing:
            continue
        candidate = next((row for row in rows if row["chapter"] in missing and not row["blocked"]), None)
        if candidate is None:
            continue
        node = db.get(KnowledgeNode, candidate["knowledge_id"])
        add_task(
            subject_id=sid,
            knowledge_id=candidate["knowledge_id"],
            task_type=TaskType.COVERAGE,
            title=f"考纲覆盖 · {candidate['chapter']}",
            detail=f"该章节尚无达到 {settings.prerequisite_gate:.2f} 掌握度的知识点，按考纲底线补齐",
            est_minutes=float(per_subject_coverage),
            priority=0.7,
            status=TaskStatus.TODO,
            source="coverage_guard",
            payload={"chapter": candidate["chapter"], "knowledge_id": node.id if node else None},
        )

    tasks.sort(key=lambda task: (task.task_type == TaskType.TEST, -task.priority))
    # mark the first task as the suggested next action
    if tasks:
        tasks[0].status = TaskStatus.DOING
    db.commit()

    return plan_payload(db, user_id, plan_date, tasks, due_cards_list, due_tests_list, budget)


def plan_payload(
    db: Session,
    user_id: int,
    plan_date: str,
    tasks: list[StudyTask],
    cards: list[MemoryCard],
    tests: list[dict],
    budget: int,
) -> dict:
    task_rows = [
        {
            "id": task.id,
            "task_type": task.task_type,
            "title": task.title,
            "detail": task.detail,
            "est_minutes": task.est_minutes,
            "priority": task.priority,
            "status": task.status,
            "plan_date": task.plan_date,
            "subject_id": task.subject_id,
            "knowledge_id": task.knowledge_id,
            "source": task.source,
            "payload": task.payload,
        }
        for task in sorted(tasks, key=lambda t: (t.status != TaskStatus.DOING, -t.priority, t.id))
    ]
    used = sum(t["est_minutes"] for t in task_rows if t["status"] != TaskStatus.DONE)
    subjects = {s.id: s for s in subjects_of(db, user_id)}
    for row in task_rows:
        subject = subjects.get(row["subject_id"])
        row["subject_name"] = subject.name if subject else ""
    return {
        "date": plan_date,
        "budget_minutes": budget,
        "used_minutes": round(used, 1),
        "tasks": task_rows,
        "due_reviews": [
            {
                "id": card.id,
                "front": card.front,
                "card_type": card.card_type,
                "state": card.state,
                "due_at": card.due_at.isoformat() if card.due_at else None,
            }
            for card in cards[:20]
        ],
        "due_tests": tests,
        "generated_at": datetime.now(UTC).isoformat(),
    }


def _test_label(test_type: str) -> str:
    return {
        TestType.MICRO: "每日微测",
        TestType.MIXED: "混合小测",
        TestType.STAGE: "阶段测试",
        TestType.MOCK: "综合模拟",
        TestType.DIAGNOSTIC: "初始诊断",
    }.get(test_type, test_type)


def today_plan(db: Session, user_id: int, plan_date: str | None = None, subject_id: int | None = None) -> dict:
    plan_date = _plan_date(plan_date)
    stmt = select(StudyTask).where(StudyTask.user_id == user_id, StudyTask.plan_date == plan_date)
    if subject_id:
        stmt = stmt.where(StudyTask.subject_id == subject_id)
    tasks = list(db.scalars(stmt))
    if not tasks:
        return generate_plan(db, user_id, plan_date=plan_date, subject_id=subject_id)

    cards = due_cards(db, user_id)
    tests = due_tests(db, user_id)
    user = db.get(StudyTask, tasks[0].id)
    budget = settings.daily_minutes
    if user is not None:
        from app.models import User

        user_row = db.get(User, user_id)
        if user_row:
            budget = user_row.daily_minutes or budget
    return plan_payload(db, user_id, plan_date, tasks, cards, tests, budget)
