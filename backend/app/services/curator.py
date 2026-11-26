"""Curator: natural-language book discovery + study workflow composition.

Runs fully offline: a curated catalog is indexed by keyword/character overlap,
and workflow composition maps Chinese study-stage keywords onto pipeline steps.
"""

from __future__ import annotations

import re
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BookResource, ExamSubject, ExamTarget, KnowledgeNode, StudyWorkflow, User
from app.seed.studio import DEFAULT_AUTONOMY, WORKFLOW_TEMPLATES
from app.services.util import active_target, subjects_of

_CJK = re.compile(r"[\u4e00-\u9fff]+")
_WORD = re.compile(r"[a-zA-Z0-9]+")

MAJOR_HINTS: dict[str, list[str]] = {
    "cs": ["计算机", "408", "数据结构", "操作系统", "计算机网络", "组成原理", "算法", "人工智能", "软件工程", "数据库"],
    "math": ["数学", "高数", "线代", "概率", "微积分", "数一", "数二", "数三"],
    "english": ["英语", "词汇", "阅读", "作文", "翻译", "长难句", "雅思", "托福"],
    "economics": ["经济", "金融", "微观", "宏观", "投资", "计量"],
    "psychology": ["心理", "实验心理", "统计"],
    "law": ["法", "民法", "刑法", "法理", "宪法", "诉讼法"],
    "journalism": ["新闻", "传播", "媒介", "广告"],
    "management": ["管理", "组织行为", "人力资源", "运营"],
    "education": ["教育", "教育学", "课程与教学"],
    "history": ["历史", "中国史", "世界史", "史学"],
}

STAGE_HINTS: list[tuple[str, list[str]]] = [
    ("基础精讲", ["基础", "教材", "入门", "概念", "打基础"]),
    ("强化刷题", ["强化", "刷题", "练习", "专项", "1000题", "习题"]),
    ("真题精练", ["真题", "历年", "套卷", "试卷"]),
    ("错题回滚", ["错题", "回滚", "诊断", "查漏", "补漏"]),
    ("模考冲刺", ["冲刺", "模拟", "模考", "押题", "限时"]),
    ("背诵记忆", ["背诵", "记忆", "单词", "卡片", "默写"]),
    ("阅读训练", ["阅读", "长难句", "精读", "泛读"]),
    ("写作训练", ["写作", "作文", "模板", "批改"]),
    ("翻译训练", ["翻译", "英译汉", "汉译英"]),
]

DEFAULT_MINUTES = {
    "基础精讲": 90,
    "强化刷题": 90,
    "真题精练": 120,
    "错题回滚": 45,
    "模考冲刺": 150,
    "背诵记忆": 45,
    "阅读训练": 60,
    "写作训练": 60,
    "翻译训练": 60,
}


# --------------------------------------------------------------------------- #
# autonomy settings
# --------------------------------------------------------------------------- #
def get_autonomy(db: Session, user_id: int) -> dict:
    user = db.get(User, user_id)
    profile = dict(user.profile or {}) if user else {}
    settings = dict(DEFAULT_AUTONOMY)
    settings.update(profile.get("autonomy") or {})
    return settings


def set_autonomy(db: Session, user_id: int, patch: dict) -> dict:
    user = db.get(User, user_id)
    if user is None:
        raise ValueError("user not found")
    profile = dict(user.profile or {})
    current = dict(DEFAULT_AUTONOMY)
    current.update(profile.get("autonomy") or {})
    for key, value in patch.items():
        if key in DEFAULT_AUTONOMY and isinstance(value, bool):
            current[key] = value
    profile["autonomy"] = current
    user.profile = profile
    db.commit()
    return current


# --------------------------------------------------------------------------- #
# book search
# --------------------------------------------------------------------------- #
def _tokens(text: str) -> list[str]:
    text = (text or "").lower()
    tokens = _WORD.findall(text)
    for run in _CJK.findall(text):
        if len(run) <= 4:
            tokens.append(run)
        else:
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return [token for token in tokens if len(token) >= 1]


def infer_subject_type(text: str) -> str | None:
    lowered = (text or "").lower()
    scores: Counter[str] = Counter()
    for subject_type, keywords in MAJOR_HINTS.items():
        for keyword in keywords:
            if keyword in lowered:
                scores[subject_type] += 2 if len(keyword) >= 3 else 1
    if not scores:
        return None
    return scores.most_common(1)[0][0]


def search_books(
    db: Session,
    user_id: int,
    query: str,
    subject_type: str | None = None,
    major: str | None = None,
    limit: int = 12,
) -> list[dict]:
    books = list(
        db.scalars(select(BookResource).where(BookResource.user_id == user_id, BookResource.status == "active"))
    )
    inferred = subject_type or infer_subject_type(query)
    query_tokens = _tokens(query)
    scored: list[tuple[float, BookResource, list[str]]] = []
    for book in books:
        haystack = " ".join(
            [
                book.title,
                book.author,
                book.publisher,
                book.major,
                book.subject_type,
                book.level,
                " ".join(book.tags or []),
                book.description,
            ]
        ).lower()
        score = 0.0
        hits: list[str] = []
        for token in query_tokens:
            if token and token in haystack:
                score += 2.0 if token in book.title.lower() else 1.0
                if len(hits) < 4:
                    hits.append(token)
        if inferred and book.subject_type == inferred:
            score += 3.0
        if major and major in (book.major or ""):
            score += 4.0
        if score > 0:
            reason_bits = []
            if inferred and book.subject_type == inferred:
                reason_bits.append(f"匹配方向：{inferred}")
            if hits:
                reason_bits.append("关键词：" + "、".join(dict.fromkeys(hits)))
            scored.append((score, book, reason_bits))
    scored.sort(key=lambda item: (-item[0], item[1].title))
    return [
        {
            "id": book.id,
            "key": book.key,
            "title": book.title,
            "author": book.author,
            "publisher": book.publisher,
            "subject_type": book.subject_type,
            "major": book.major,
            "level": book.level,
            "tags": book.tags,
            "description": book.description,
            "status": book.status,
            "created_by": book.created_by,
            "score": round(score, 2),
            "reason": "；".join(reason_bits) or "方向匹配",
        }
        for score, book, reason_bits in scored[:limit]
    ]


# --------------------------------------------------------------------------- #
# workflow composition
# --------------------------------------------------------------------------- #
def _pick_subject(db: Session, user_id: int, text: str) -> ExamSubject | None:
    lowered = (text or "").lower()
    for subject in subjects_of(db, user_id):
        if subject.name and subject.name.lower() in lowered:
            return subject
        if subject.subject_type == "math_1" and any(k in lowered for k in ("数学一", "数一", "高数", "线代", "概率")):
            return subject
        if subject.subject_type == "english_1" and any(k in lowered for k in ("英语一", "英语", "考研英语")):
            return subject
    return None


def compose_workflow(db: Session, user_id: int, text: str) -> dict:
    text = (text or "").strip()
    if not text:
        raise ValueError("请输入你想怎么学，例如：帮我编排计算机考研的数据结构 基础-强化-真题-冲刺 工作流")

    subject = _pick_subject(db, user_id, text)
    subject_type = subject.subject_type if subject else None
    inferred = subject_type or infer_subject_type(text)
    template = WORKFLOW_TEMPLATES.get(subject_type, WORKFLOW_TEMPLATES["default"])

    lowered = text.lower()
    wanted = [stage for stage, keywords in STAGE_HINTS if any(keyword in lowered for keyword in keywords)]

    steps: list[dict] = []
    if wanted:
        for stage in wanted:
            minutes = DEFAULT_MINUTES.get(stage, 60)
            steps.append(
                {
                    "name": stage,
                    "focus": _focus_for(stage, text),
                    "minutes": minutes,
                    "task_type": _task_type_for(stage),
                    "enabled": True,
                }
            )
        # append exam-prep tail if the user only named early stages
        if "模考冲刺" not in wanted and any(keyword in lowered for keyword in ("考研", "考试", "完整", "全套")):
            steps.append({"name": "模考冲刺", "focus": "限时全真模拟", "minutes": 150, "task_type": "test", "enabled": True})
    else:
        steps = [
            {"name": step["name"], "focus": step["focus"], "minutes": step["minutes"], "task_type": step["task_type"], "enabled": True}
            for step in template
        ]

    major_hint = next(
        (major for subject_type_key, keywords in MAJOR_HINTS.items() for major in keywords if major in text and subject_type_key == inferred),
        "",
    )
    target_name = subject.name if subject else (major_hint or "自定义目标")
    name = f"{target_name} · {'/'.join(step['name'] for step in steps[:3])} 工作流"
    books = search_books(db, user_id, text, subject_type=inferred, major=major_hint or None, limit=3)
    return {
        "name": name,
        "description": f"由自然语言编排：{text[:120]}",
        "subject_id": subject.id if subject else None,
        "steps": steps,
        "source": "nl",
        "suggested_books": books,
    }


def _focus_for(stage: str, text: str) -> str:
    return {
        "基础精讲": "教材概念与例题精讲",
        "强化刷题": "专项题与变式训练",
        "真题精练": "按年份限时真题",
        "错题回滚": "沿知识依赖最小修复",
        "模考冲刺": "全真模拟与时间分配",
        "背诵记忆": "主动回忆与间隔重复",
        "阅读训练": "长难句与题型专项",
        "写作训练": "模板、句型与批改",
        "翻译训练": "英汉互译与表达积累",
    }.get(stage, "按计划推进")


def _task_type_for(stage: str) -> str:
    return {
        "基础精讲": "learn",
        "强化刷题": "practice",
        "真题精练": "test",
        "错题回滚": "diagnose",
        "模考冲刺": "test",
        "背诵记忆": "memory",
        "阅读训练": "practice",
        "写作训练": "practice",
        "翻译训练": "practice",
    }.get(stage, "practice")


def save_workflow(db: Session, user_id: int, draft: dict) -> StudyWorkflow:
    workflow = StudyWorkflow(
        user_id=user_id,
        subject_id=draft.get("subject_id"),
        name=(draft.get("name") or "新工作流")[:160],
        description=draft.get("description") or "",
        steps=draft.get("steps") or [],
        status="active",
        source=draft.get("source") or "nl",
        meta=draft.get("meta") or {},
    )
    db.add(workflow)
    db.commit()
    return workflow


def serialize_workflow(workflow: StudyWorkflow, subject_name: str | None = None) -> dict:
    return {
        "id": workflow.id,
        "name": workflow.name,
        "description": workflow.description,
        "subject_id": workflow.subject_id,
        "subject_name": subject_name,
        "steps": workflow.steps or [],
        "status": workflow.status,
        "source": workflow.source,
        "meta": workflow.meta or {},
        "created_at": workflow.created_at.isoformat(),
    }


def list_workflows(db: Session, user_id: int) -> list[dict]:
    flows = list(
        db.scalars(
            select(StudyWorkflow)
            .where(StudyWorkflow.user_id == user_id, StudyWorkflow.status != "archived")
            .order_by(StudyWorkflow.id.desc())
        )
    )
    names = {subject.id: subject.name for subject in db.scalars(select(ExamSubject))}
    return [serialize_workflow(flow, names.get(flow.subject_id)) for flow in flows]


def update_workflow(db: Session, user_id: int, workflow_id: int, patch: dict) -> StudyWorkflow:
    workflow = db.get(StudyWorkflow, workflow_id)
    if workflow is None or workflow.user_id != user_id:
        raise ValueError("工作流不存在")
    if "name" in patch and patch["name"]:
        workflow.name = str(patch["name"])[:160]
    if "description" in patch:
        workflow.description = str(patch["description"])
    if "steps" in patch and isinstance(patch["steps"], list):
        workflow.steps = patch["steps"]
    if "status" in patch and patch["status"] in ("active", "paused", "archived"):
        workflow.status = patch["status"]
    if "subject_id" in patch:
        workflow.subject_id = patch["subject_id"]
    db.commit()
    return workflow


def delete_workflow(db: Session, user_id: int, workflow_id: int) -> None:
    workflow = db.get(StudyWorkflow, workflow_id)
    if workflow is None or workflow.user_id != user_id:
        raise ValueError("工作流不存在")
    db.delete(workflow)
    db.commit()


# --------------------------------------------------------------------------- #
# subjects
# --------------------------------------------------------------------------- #
def add_subject(
    db: Session,
    user_id: int,
    name: str,
    subject_type: str = "custom",
    total_score: float = 150.0,
    major: str = "",
) -> ExamSubject:
    name = (name or "").strip()
    if not name:
        raise ValueError("科目名称不能为空")
    target = active_target(db, user_id)
    if target is None:
        raise ValueError("请先初始化考试目标（profile）")
    existing = db.scalar(
        select(ExamSubject).where(ExamSubject.target_id == target.id, ExamSubject.name == name)
    )
    if existing is not None:
        raise ValueError(f"科目已存在：{name}")
    subject = ExamSubject(
        target_id=target.id,
        subject_type=subject_type,
        name=name,
        code=(major or "custom")[:32],
        total_score=total_score,
        syllabus={"major": major} if major else {},
    )
    db.add(subject)
    db.commit()
    return subject


def remove_subject(db: Session, user_id: int, subject_id: int) -> dict:
    subjects = subjects_of(db, user_id)
    subject = next((item for item in subjects if item.id == subject_id), None)
    if subject is None:
        raise ValueError("科目不存在")
    if len(subjects) <= 1:
        raise ValueError("至少保留一个科目，不能全部删除")
    db.query(StudyWorkflow).filter(
        StudyWorkflow.user_id == user_id, StudyWorkflow.subject_id == subject_id
    ).delete(synchronize_session=False)
    knowledge_count = len(
        list(db.scalars(select(KnowledgeNode).where(KnowledgeNode.subject_id == subject_id)))
    )
    db.delete(subject)
    db.commit()
    return {"removed": subject_id, "name": subject.name, "knowledge_removed": knowledge_count}
