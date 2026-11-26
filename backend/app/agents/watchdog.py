"""Watchdog: independent supervision loop (PRD 5.5).

It may only modify *derived* state (ticket/assignment status, plans) and file
correction suggestions - never raw exam questions, source material or the
learner's recorded answers.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import VAR_DIR
from app.constants import AssignmentStatus, TicketStatus, WorkerType
from app.models import (
    Assignment,
    KnowledgeNode,
    MasteryStateRow,
    MemoryCard,
    Question,
    QuestionKnowledge,
    StudyTask,
    TestSession,
    Ticket,
    WorkerReport,
)
from app.services.roi import get_roi

FINDINGS_FILE = VAR_DIR / "watchdog_findings.json"


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def run_scan(db: Session, user_id: int, auto_fix: bool = True) -> list[dict]:
    findings: list[dict] = []
    actions_taken: list[str] = []
    now = datetime.now(UTC)

    # 1. stalled assignments ------------------------------------------------
    running = list(db.scalars(select(Assignment).where(Assignment.status == AssignmentStatus.RUNNING)))
    for assignment in running:
        heartbeat = _aware(assignment.heartbeat_at or assignment.started_at)
        if heartbeat and (now - heartbeat) > timedelta(minutes=10):
            finding = {
                "code": "STALLED_ASSIGNMENT",
                "severity": "high",
                "title": f"Worker 停滞：assignment {assignment.id}",
                "detail": f"{assignment.worker_type} 已 {(now - heartbeat).seconds // 60} 分钟无心跳",
                "action": "标记 FAILED 并允许重新创建 Assignment",
                "auto_fixed": False,
            }
            if auto_fix:
                assignment.status = AssignmentStatus.FAILED
                assignment.error = "watchdog: stalled execution"
                ticket = db.get(Ticket, assignment.ticket_id)
                if ticket and ticket.status not in TicketStatus.TERMINAL:
                    ticket.status = TicketStatus.BLOCKED
                finding["auto_fixed"] = True
                actions_taken.append(f"assignment {assignment.id} -> FAILED")
            findings.append(finding)

    # 2. duplicate active tickets ------------------------------------------
    rows = db.execute(
        select(Ticket.ticket_type, func.count(Ticket.id))
        .where(Ticket.user_id == user_id, Ticket.status.in_([TicketStatus.OPEN, TicketStatus.PENDING]))
        .group_by(Ticket.ticket_type)
        .having(func.count(Ticket.id) > 2)
    ).all()
    for ticket_type, count in rows:
        findings.append(
            {
                "code": "DUPLICATE_TICKETS",
                "severity": "medium",
                "title": f"同类任务积压：{ticket_type} × {count}",
                "detail": "同一类工单重复创建，可能是事件未去重或 Router 循环触发",
                "action": "保留最新一张，其余标记 DROPPED",
                "auto_fixed": False,
            }
        )
        if auto_fix:
            tickets = list(
                db.scalars(
                    select(Ticket)
                    .where(Ticket.user_id == user_id, Ticket.ticket_type == ticket_type, Ticket.status.in_([TicketStatus.OPEN, TicketStatus.PENDING]))
                    .order_by(Ticket.id.desc())
                )
            )
            for duplicate in tickets[1:]:
                duplicate.status = TicketStatus.DROPPED
            actions_taken.append(f"deduplicated {ticket_type}")

    # 3. rejected synthetic questions --------------------------------------
    rejected = list(
        db.scalars(
            select(Question).where(
                Question.is_synthetic.is_(True),
            )
        )
    )
    rejected = [q for q in rejected if (q.verification or {}).get("status") == "rejected"]
    if rejected:
        findings.append(
            {
                "code": "REJECTED_SYNTHETIC",
                "severity": "medium",
                "title": f"{len(rejected)} 道生成题未通过验证",
                "detail": "这些题不应进入训练题库",
                "action": "隔离生成题（置 meta.quarantined=true）",
                "auto_fixed": False,
            }
        )
        if auto_fix:
            for question in rejected:
                meta = dict(question.meta or {})
                meta["quarantined"] = True
                question.meta = meta
            actions_taken.append(f"quarantined {len(rejected)} synthetic questions")

    # 4. study plan backlog -------------------------------------------------
    today = datetime.now().date().isoformat()
    tasks = list(
        db.scalars(select(StudyTask).where(StudyTask.user_id == user_id, StudyTask.plan_date == today))
    )
    planned = sum(task.est_minutes for task in tasks if task.status in ("TODO", "DOING"))
    from app.config import settings

    if planned > settings.daily_minutes * 1.4:
        findings.append(
            {
                "code": "PLAN_OVERLOAD",
                "severity": "medium",
                "title": f"今日计划超载：{planned:.0f} 分钟 / 预算 {settings.daily_minutes} 分钟",
                "detail": "计划积压会降低完成率并污染掌握度估计",
                "action": "降低当日任务估算时长或顺延低 ROI 任务",
                "auto_fixed": False,
            }
        )
    if not tasks:
        findings.append(
            {
                "code": "NO_PLAN",
                "severity": "low",
                "title": "今日尚无学习计划",
                "detail": "Router 未生成计划或计划已过期",
                "action": "触发生成今日计划",
                "auto_fixed": False,
            }
        )
        if auto_fix:
            from app.services.planner import generate_plan

            generate_plan(db, user_id)
            actions_taken.append("generated today plan")

    # 5. repeated forgetting ------------------------------------------------
    lapsed = list(
        db.scalars(
            select(MemoryCard).where(MemoryCard.user_id == user_id, MemoryCard.lapses >= 3)
        )
    )
    if lapsed:
        findings.append(
            {
                "code": "REPEATED_FORGETTING",
                "severity": "low",
                "title": f"{len(lapsed)} 张卡片反复遗忘（lapses ≥ 3）",
                "detail": "同一记忆材料多次遗忘，说明复习间隔或编码方式不合适",
                "action": "拆分卡片、增加主动回忆与延迟测试",
                "auto_fixed": False,
            }
        )

    # 6. ROI drift / untouched high-frequency knowledge ---------------------
    roi_rows = get_roi(db, user_id, limit=400)
    scheduled_ids = {task.knowledge_id for task in tasks if task.knowledge_id}
    neglected = [
        row for row in roi_rows
        if row["frequency"] >= 0.5 and row["reasons"].get("gap", 0) >= 0.5
        and row["knowledge_id"] not in scheduled_ids and not row["blocked"]
    ]
    if len(neglected) >= 3:
        findings.append(
            {
                "code": "ROI_DRIFT",
                "severity": "medium",
                "title": f"{len(neglected)} 个高频知识点未进入今日计划",
                "detail": "高频低掌握度知识点被忽略，ROI 预测与实际计划持续偏离",
                "action": "重新计算 ROI 并重排计划",
                "auto_fixed": False,
            }
        )

    # 7. unanswered tests ---------------------------------------------------
    open_tests = list(
        db.scalars(
            select(TestSession).where(TestSession.user_id == user_id, TestSession.status == "open")
        )
    )
    if len(open_tests) > 2:
        findings.append(
            {
                "code": "PENDING_TESTS",
                "severity": "low",
                "title": f"{len(open_tests)} 份测试未提交",
                "detail": "未完成的测试会阻塞掌握度更新与回滚判断",
                "action": "提醒提交或作废测试",
                "auto_fixed": False,
            }
        )

    db.commit()
    persisted = {
        "scanned_at": now.isoformat(),
        "findings": findings,
        "actions_taken": actions_taken,
    }
    FINDINGS_FILE.write_text(json.dumps(persisted, ensure_ascii=False, indent=2), encoding="utf-8")
    return findings


def last_findings() -> dict:
    if not FINDINGS_FILE.exists():
        return {"scanned_at": None, "findings": [], "actions_taken": []}
    try:
        return json.loads(FINDINGS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"scanned_at": None, "findings": [], "actions_taken": []}
