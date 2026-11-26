"""Shared service helpers: user/subject resolution, counts, serialisation helpers."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.constants import WorkerType
from app.models import (
    Assignment,
    ExamSubject,
    ExamTarget,
    KnowledgeNode,
    MasteryStateRow,
    MemoryCard,
    Paper,
    Question,
    StudyTask,
    Ticket,
    User,
)


def ensure_user(db: Session, username: str = "default") -> User:
    user = db.scalar(select(User).where(User.username == username))
    if user:
        return user
    user = User(username=username, display_name="考生", profile={"stage": "initial"}, daily_minutes=240)
    db.add(user)
    db.flush()
    target = ExamTarget(user_id=user.id, university="示例大学", major="计算机科学与技术", exam_year=2027)
    db.add(target)
    db.flush()
    db.add(ExamSubject(target_id=target.id, subject_type="math_1", name="数学一", code="301", total_score=150))
    db.add(ExamSubject(target_id=target.id, subject_type="english_1", name="英语一", code="201", total_score=100))
    db.commit()
    active = db.scalar(select(ExamTarget).where(ExamTarget.user_id == user.id).order_by(ExamTarget.id.desc()))
    return user


def active_target(db: Session, user_id: int) -> ExamTarget | None:
    return db.scalar(
        select(ExamTarget).where(ExamTarget.user_id == user_id).order_by(ExamTarget.id.desc())
    )


def resolve_subject(
    db: Session,
    user_id: int,
    subject_id: int | None = None,
    subject_type: str | None = None,
) -> ExamSubject:
    target = active_target(db, user_id)
    if target is None:
        raise ValueError("no exam target configured")
    if subject_id is not None:
        subject = db.get(ExamSubject, subject_id)
        if subject is None or subject.target_id != target.id:
            raise ValueError(f"subject {subject_id} not found")
        return subject
    stmt = select(ExamSubject).where(ExamSubject.target_id == target.id)
    if subject_type:
        stmt = stmt.where(ExamSubject.subject_type == subject_type)
    subject = db.scalar(stmt.order_by(ExamSubject.id))
    if subject is None:
        raise ValueError("no matching subject")
    return subject


def subjects_of(db: Session, user_id: int) -> list[ExamSubject]:
    target = active_target(db, user_id)
    if target is None:
        return []
    return list(db.scalars(select(ExamSubject).where(ExamSubject.target_id == target.id).order_by(ExamSubject.id)))


def real_paper_years(db: Session, subject_id: int) -> list[int]:
    rows = db.execute(
        select(Paper.year, func.count(Question.id))
        .join(Question, Question.paper_id == Paper.id)
        .where(Paper.subject_id == subject_id, Paper.paper_type == "real", Question.is_synthetic.is_(False))
        .group_by(Paper.year)
    ).all()
    return sorted({year for year, count in rows if year and count})


def paper_trust(db: Session, subject_id: int) -> float:
    from app.models import Source, SourceDocument

    avg = db.scalar(
        select(func.avg(Source.trust_score))
        .join(SourceDocument, SourceDocument.source_id == Source.id)
        .join(Paper, Paper.document_id == SourceDocument.id)
        .where(Paper.subject_id == subject_id)
    )
    return float(avg) if avg is not None else 0.6


def knowledge_links_by_question(db: Session, question_ids: list[int]) -> dict[int, list[tuple[int, float]]]:
    from app.models import QuestionKnowledge

    if not question_ids:
        return {}
    rows = db.execute(
        select(QuestionKnowledge.question_id, QuestionKnowledge.knowledge_id, QuestionKnowledge.weight).where(
            QuestionKnowledge.question_id.in_(question_ids)
        )
    ).all()
    out: dict[int, list[tuple[int, float]]] = {}
    for qid, kid, weight in rows:
        out.setdefault(qid, []).append((kid, float(weight)))
    return out


def question_knowledge_map(db: Session, question_id: int) -> list[tuple[KnowledgeNode, float, bool]]:
    from app.models import QuestionKnowledge

    rows = db.execute(
        select(KnowledgeNode, QuestionKnowledge.weight, QuestionKnowledge.is_core)
        .join(QuestionKnowledge, QuestionKnowledge.knowledge_id == KnowledgeNode.id)
        .where(QuestionKnowledge.question_id == question_id)
    ).all()
    return list(rows)


def mastery_of(db: Session, user_id: int, knowledge_id: int) -> float:
    row = db.scalar(
        select(MasteryStateRow).where(
            MasteryStateRow.user_id == user_id, MasteryStateRow.knowledge_id == knowledge_id
        )
    )
    return float(row.mastery) if row else 0.0


def mastery_row(db: Session, user_id: int, knowledge_id: int) -> MasteryStateRow | None:
    return db.scalar(
        select(MasteryStateRow).where(
            MasteryStateRow.user_id == user_id, MasteryStateRow.knowledge_id == knowledge_id
        )
    )


def open_ticket_count(db: Session, user_id: int) -> tuple[int, int]:
    open_count = db.scalar(
        select(func.count(Ticket.id)).where(Ticket.user_id == user_id, Ticket.status.in_(["OPEN", "PENDING", "BLOCKED", "WAITING"]))
    ) or 0
    running = db.scalar(
        select(func.count(Ticket.id)).where(Ticket.user_id == user_id, Ticket.status.in_(["RUNNING", "PROGRESS"]))
    ) or 0
    return int(open_count), int(running)


def due_cards(db: Session, user_id: int, limit: int | None = None) -> list[MemoryCard]:
    now = datetime.now().astimezone()
    stmt = (
        select(MemoryCard)
        .where(MemoryCard.user_id == user_id, MemoryCard.due_at.is_not(None), MemoryCard.due_at <= now)
        .order_by(MemoryCard.due_at)
    )
    if limit:
        stmt = stmt.limit(limit)
    return list(db.scalars(stmt))


def today_plan_date() -> str:
    return date.today().isoformat()


def pending_tasks(db: Session, user_id: int, plan_date: str, subject_id: int | None = None) -> list[StudyTask]:
    stmt = select(StudyTask).where(
        StudyTask.user_id == user_id, StudyTask.plan_date == plan_date, StudyTask.status.in_(["TODO", "DOING"])
    )
    if subject_id:
        stmt = stmt.where(StudyTask.subject_id == subject_id)
    return list(db.scalars(stmt.order_by(StudyTask.priority.desc(), StudyTask.id)))


def latest_assignment(db: Session, ticket_id: int) -> Assignment | None:
    return db.scalar(select(Assignment).where(Assignment.ticket_id == ticket_id).order_by(Assignment.id.desc()))


def worker_label(worker: str) -> str:
    return {
        WorkerType.COLLECTOR: "资料搜集",
        WorkerType.PARSER: "试卷解析",
        WorkerType.TAGGER: "知识点标注",
        WorkerType.EXAM_ANALYST: "考情分析",
        WorkerType.ROI_PLANNER: "ROI 规划",
        WorkerType.QUESTION_GENERATOR: "题目改造",
        WorkerType.SOLVER_VERIFIER: "解题验证",
        WorkerType.MEMORY_BUILDER: "背诵构建",
        WorkerType.TEST_DESIGNER: "测试设计",
        WorkerType.DIAGNOSTICIAN: "错误诊断",
        WorkerType.MATH_SPECIALIST: "数学一",
        WorkerType.ENGLISH_SPECIALIST: "英语一",
        WorkerType.PROFESSIONAL_SPECIALIST: "专业课",
        WorkerType.WATCHDOG: "Watchdog",
        WorkerType.CURATOR: "书籍检索",
        WorkerType.WORKFLOW_BUILDER: "工作流编排",
        WorkerType.SUBJECT_MANAGER: "科目管理",
    }.get(worker, worker)
