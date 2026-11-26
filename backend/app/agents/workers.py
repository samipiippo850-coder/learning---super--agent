"""Worker implementations.

Each worker is a pure-ish function ``(db, user_id, subject_id, params) -> report``.
It runs in its own Assignment context and cannot mutate authenticated source
material (only derived tables).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.constants import ErrorType, QuestionType, TestType, WorkerType
from app.models import (
    KnowledgeNode,
    LearnerAttempt,
    MemoryCard,
    Question,
    QuestionKnowledge,
    Source,
    SourceDocument,
)
from app.services import ingest, memory as memory_service, roi as roi_service, tests as test_service
from app.services.frequency import compute_subject_frequency
from app.services.graph import rollback_plan
from app.services.mastery import summary as mastery_summary
from app.services.transform import generate_variants
from app.services.util import real_paper_years, subjects_of


def _needs_subject(db: Session, user_id: int, subject_id: int | None) -> tuple[int | None, str | None]:
    if subject_id is not None:
        from app.models import ExamSubject

        subject = db.get(ExamSubject, subject_id)
        return (subject.id, subject.subject_type) if subject else (None, None)
    subjects = subjects_of(db, user_id)
    if not subjects:
        return None, None
    return subjects[0].id, subjects[0].subject_type


# --------------------------------------------------------------------------- #
# data workers
# --------------------------------------------------------------------------- #
def collector(db: Session, user_id: int, subject_id: int | None, params: dict) -> dict:
    """Register a learning resource (metadata only; download is out of scope offline)."""
    sources = list(db.scalars(select(Source).order_by(Source.id)))
    untrusted = [source.key for source in sources if source.trust_score < 0.5]
    return {
        "status": "READY",
        "summary": f"资料库现有 {len(sources)} 个来源、{db.scalar(select(func.count(SourceDocument.id))) or 0} 份文档",
        "output_refs": [f"source:{source.key}" for source in sources[:10]],
        "evidence": [f"untrusted_sources={untrusted}"],
        "confidence": 0.9,
        "requires_human_review": False,
        "next_actions": ["新增资料请通过 POST /api/documents 导入并标注来源与年份"],
        "payload": {"sources": len(sources), "untrusted": untrusted},
    }


def parser(db: Session, user_id: int, subject_id: int | None, params: dict) -> dict:
    """Parse a pending document into structured questions."""
    content = params.get("content_text") or ""
    if not content:
        return {
            "status": "BLOCKED",
            "summary": "缺少待解析文本：请在 params.content_text 中提供试卷内容",
            "output_refs": [],
            "evidence": [],
            "confidence": 0.2,
            "requires_human_review": False,
            "next_actions": ["调用 POST /api/documents 导入试卷文本"],
            "payload": {},
        }
    parsed = ingest.parse_questions(content, params.get("paper_type") or "real")
    return {
        "status": "READY",
        "summary": f"识别出 {len(parsed)} 道题目（文本解析器，PDF/OCR 需额外依赖）",
        "output_refs": [f"question:{item['number']}" for item in parsed[:10]],
        "evidence": [item["content"][:60] for item in parsed[:3]],
        "confidence": 0.6 if parsed else 0.2,
        "requires_human_review": True,
        "next_actions": ["人工核验题目切分与答案", "调用 POST /api/documents 入库"],
        "payload": {"questions": parsed},
    }


def tagger(db: Session, user_id: int, subject_id: int | None, params: dict) -> dict:
    subject_id, _subject_type = _needs_subject(db, user_id, subject_id)
    if subject_id is None:
        return _blocked("未找到考试科目")
    questions = list(
        db.scalars(
            select(Question)
            .outerjoin(QuestionKnowledge, QuestionKnowledge.question_id == Question.id)
            .where(Question.subject_id == subject_id, QuestionKnowledge.id.is_(None))
            .limit(200)
        )
    )
    tagged = 0
    for question in questions:
        tagged_links = ingest.auto_tag(db, subject_id, question.content)
        for node, weight, confidence in tagged_links:
            db.add(
                QuestionKnowledge(
                    question_id=question.id,
                    knowledge_id=node.id,
                    weight=weight,
                    confidence=min(confidence, 0.5),
                    is_core=False,
                    role="auto",
                )
            )
            tagged += 1
    db.commit()
    return {
        "status": "READY",
        "summary": f"为 {len(questions)} 道未标注题目补充了 {tagged} 条自动知识点标签",
        "output_refs": [],
        "evidence": [],
        "confidence": 0.5,
        "requires_human_review": True,
        "next_actions": ["人工修正低置信度标签（confidence < 0.5）"],
        "payload": {"questions": len(questions), "links": tagged},
    }


def exam_analyst(db: Session, user_id: int, subject_id: int | None, params: dict) -> dict:
    subject_id, _ = _needs_subject(db, user_id, subject_id)
    if subject_id is None:
        return _blocked("未找到考试科目")
    freq = compute_subject_frequency(db, subject_id)
    years = real_paper_years(db, subject_id)
    top = sorted(freq.values(), key=lambda row: row.frequency, reverse=True)[:8]
    unstable = [row.code for row in freq.values() if row.question_count and len(row.years_present) <= 1]
    if not years:
        return _blocked("尚无真题数据，无法统计考频")
    return {
        "status": "READY",
        "summary": f"基于 {len(years)} 年真题（{years[0]}–{years[-1]}）统计 {len(freq)} 个知识点考频",
        "output_refs": [f"frequency:{row.code}" for row in top],
        "evidence": [f"{row.code}: F={row.frequency:.3f} M={row.score_share:.3f}" for row in top[:5]],
        "confidence": round(sum(row.confidence for row in top) / max(1, len(top)), 3),
        "requires_human_review": False,
        "next_actions": ["关注仅出现 1 年的知识点，避免误判为高频考点"],
        "payload": {
            "years": years,
            "top": [row.as_dict() for row in top],
            "unstable": unstable,
        },
    }


# --------------------------------------------------------------------------- #
# planning / generation workers
# --------------------------------------------------------------------------- #
def roi_planner(db: Session, user_id: int, subject_id: int | None, params: dict) -> dict:
    subject_id, _ = _needs_subject(db, user_id, subject_id)
    if subject_id is None:
        return _blocked("未找到考试科目")
    results = roi_service.persist_subject_roi(db, user_id, subject_id)
    top = [row for row in results if not row["blocked"]][:8]
    plan_tasks = 0
    plan_error = ""
    try:
        from app.services.planner import generate_plan

        plan = generate_plan(db, user_id)
        plan_tasks = len(plan["tasks"])
    except Exception as exc:  # pragma: no cover - defensive
        plan_error = str(exc)
    summary = f"重新计算 {len(results)} 个知识点的 ROI，可学习节点 {len([r for r in results if not r['blocked']])} 个"
    if plan_error:
        summary += f"；计划生成失败：{plan_error}"
    else:
        summary += f"；已生成今日计划 {plan_tasks} 项任务"
    return {
        "status": "READY",
        "summary": summary,
        "output_refs": [f"roi:{row['code']}" for row in top],
        "evidence": [
            f"{row['code']}: ROI={row['roi']:.2f} ΔV={row['expected_gain']:.2f} H={row['hours']:.2f}h"
            for row in top[:5]
        ],
        "confidence": 0.85,
        "requires_human_review": False,
        "next_actions": ["按 ROI 顺序执行今日计划", "前置未达标节点需先回滚"],
        "payload": {
            "top": top,
            "blocked": [row["code"] for row in results if row["blocked"]],
            "plan_tasks": plan_tasks,
            "plan_error": plan_error,
        },
    }


def memory_builder(db: Session, user_id: int, subject_id: int | None, params: dict) -> dict:
    cards = memory_service.build_cards(db, user_id, subject_id=subject_id)
    return {
        "status": "READY",
        "summary": f"构建 {len(cards)} 张背诵卡片",
        "output_refs": [f"card:{card.id}" for card in cards[:10]],
        "evidence": [card.front for card in cards[:3]],
        "confidence": 0.8,
        "requires_human_review": False,
        "next_actions": ["进入 /memory 进行主动回忆训练"],
        "payload": {"created": len(cards)},
    }


def question_generator(db: Session, user_id: int, subject_id: int | None, params: dict) -> dict:
    question_id = params.get("question_id")
    level = params.get("level") or "L1"
    count = int(params.get("count") or 1)
    if not question_id:
        return _blocked("缺少 question_id（params.question_id）")
    try:
        created = generate_variants(db, user_id, int(question_id), level=level, count=count)
    except ValueError as exc:
        return _blocked(str(exc))
    return {
        "status": "READY",
        "summary": f"基于题目 {question_id} 生成 {len(created)} 道 {level} 变式题并通过 SymPy 验证",
        "output_refs": [f"question:{item['question'].id}" for item in created],
        "evidence": [item["verification"]["status"] for item in created],
        "confidence": min((item["verification"]["confidence"] for item in created), default=0.5),
        "requires_human_review": False,
        "next_actions": ["将生成题加入练习", "生成题不计入真实考频"],
        "payload": {"created": [item["question"].id for item in created], "level": level},
    }


def solver_verifier(db: Session, user_id: int, subject_id: int | None, params: dict) -> dict:
    question_id = params.get("question_id")
    if not question_id:
        return _blocked("缺少 question_id")
    question = db.get(Question, int(question_id))
    if question is None:
        return _blocked("题目不存在")
    verification = question.verification or {}
    status = verification.get("status") or "unverified"
    return {
        "status": "READY" if status in ("verified", "seed_verified") else "WAITING",
        "summary": f"题目 {question.key} 验证状态：{status}",
        "output_refs": [f"question:{question.id}"],
        "evidence": [check.get("detail", "") for check in verification.get("checks", [])][:5],
        "confidence": float(verification.get("confidence") or 0.5),
        "requires_human_review": status not in ("verified", "seed_verified"),
        "next_actions": [] if status in ("verified", "seed_verified") else ["重新生成或人工核验答案"],
        "payload": verification,
    }


def test_designer(db: Session, user_id: int, subject_id: int | None, params: dict) -> dict:
    subject_id, _ = _needs_subject(db, user_id, subject_id)
    if subject_id is None:
        return _blocked("未找到考试科目")
    test_type = params.get("test_type") or TestType.MICRO
    try:
        test, questions = test_service.generate_test(
            db, user_id, subject_id, test_type=test_type, focus_knowledge_ids=params.get("focus_knowledge_ids") or []
        )
    except ValueError as exc:
        return _blocked(str(exc))
    return {
        "status": "READY",
        "summary": f"生成 {test_type} 测试 #{test.id}，{len(questions)} 题，满分 {test.max_score:.0f}",
        "output_refs": [f"test:{test.id}"],
        "evidence": [item["reason"] for item in test.blueprint.get("selection", [])][:5],
        "confidence": 0.8,
        "requires_human_review": False,
        "next_actions": ["进入 /tests 完成测试"],
        "payload": {"test_id": test.id, "test_type": test_type, "questions": len(questions)},
    }


# --------------------------------------------------------------------------- #
# diagnosis workers
# --------------------------------------------------------------------------- #
def diagnostician(db: Session, user_id: int, subject_id: int | None, params: dict) -> dict:
    subject_id, _ = _needs_subject(db, user_id, subject_id)
    stmt = (
        select(LearnerAttempt)
        .where(LearnerAttempt.user_id == user_id, LearnerAttempt.is_correct.is_(False))
        .order_by(LearnerAttempt.id.desc())
        .limit(30)
    )
    attempts = list(db.scalars(stmt))
    knowledge_ids: set[int] = set()
    for attempt in attempts:
        question = db.get(Question, attempt.question_id)
        if not question or question.subject_id != subject_id:
            continue
        for link in question.knowledge_links:
            knowledge_ids.add(link.knowledge_id)
    rollback = {"needed": False, "nodes": []}
    if knowledge_ids:
        rollback = rollback_plan(
            db, user_id, subject_id, sorted(knowledge_ids), ErrorType.CONCEPT, max_nodes=5
        )
    return {
        "status": "READY" if attempts else "WAITING",
        "summary": f"分析最近 {len(attempts)} 次错误，定位 {len(rollback.get('nodes', []))} 个需回滚知识点",
        "output_refs": [f"knowledge:{node['knowledge_id']}" for node in rollback.get("nodes", [])],
        "evidence": [f"{node['code']} mastery={node['mastery']}" for node in rollback.get("nodes", [])[:5]],
        "confidence": 0.7,
        "requires_human_review": False,
        "next_actions": ["执行最小必要回滚，不重学整章"] if rollback.get("needed") else [],
        "payload": rollback,
    }


def subject_specialist(db: Session, user_id: int, subject_id: int | None, params: dict) -> dict:
    subject_id, subject_type = _needs_subject(db, user_id, subject_id)
    if subject_id is None:
        return _blocked("未找到考试科目")
    from app.models import ExamSubject

    subject = db.get(ExamSubject, subject_id)
    mastery = mastery_summary(db, user_id, subject_id)
    freq = compute_subject_frequency(db, subject_id)
    weakest = sorted(
        ((node, freq.get(node.id)) for node in db.scalars(select(KnowledgeNode).where(KnowledgeNode.subject_id == subject_id))),
        key=lambda item: item[1].frequency if item[1] else 0.0,
    )[:5]
    from app.services.util import mastery_of

    weak_payload = [
        {
            "code": node.code,
            "name": node.name,
            "mastery": round(mastery_of(db, user_id, node.id), 3),
            "frequency": round(stats.frequency, 3) if stats else 0.0,
        }
        for node, stats in weakest
    ]
    return {
        "status": "READY",
        "summary": f"{subject.name if subject else subject_type} 掌握度均值 {mastery['average']:.2f}，稳定掌握 {mastery['stable']} 个",
        "output_refs": [f"knowledge:{row['code']}" for row in weak_payload],
        "evidence": [f"{row['code']} mastery={row['mastery']} F={row['frequency']}" for row in weak_payload[:3]],
        "confidence": 0.8,
        "requires_human_review": False,
        "next_actions": ["先把高频低掌握度知识点排入计划"],
        "payload": {"mastery": mastery, "weakest": weak_payload},
    }


def watchdog_worker(db: Session, user_id: int, subject_id: int | None, params: dict) -> dict:
    from app.agents.watchdog import run_scan

    findings = run_scan(db, user_id, auto_fix=bool(params.get("auto_fix", True)))
    return {
        "status": "READY",
        "summary": f"Watchdog 发现 {len(findings)} 项异常",
        "output_refs": [finding["code"] for finding in findings],
        "evidence": [finding["title"] for finding in findings[:5]],
        "confidence": 0.9,
        "requires_human_review": any(f["severity"] == "high" for f in findings),
        "next_actions": [finding["action"] for finding in findings if finding["action"]][:5],
        "payload": {"findings": findings},
    }


# --------------------------------------------------------------------------- #
# studio workers: book curation / workflow composition / subject management
# --------------------------------------------------------------------------- #
def curator_worker(db: Session, user_id: int, subject_id: int | None, params: dict) -> dict:
    from app.services import curator

    autonomy = curator.get_autonomy(db, user_id)
    if not autonomy.get("allow_agent_search_books", True):
        return _blocked("智能体没有“检索书籍”的权限，请在 自主编排 页开启后再试")
    query = (params.get("message") or params.get("query") or "").strip()
    if not query:
        return _blocked("请描述你要找的书或方向，例如：帮我找计算机考研的数据结构教材")
    books = curator.search_books(db, user_id, query, subject_type=params.get("subject_type"), limit=8)
    if not books:
        return {
            "status": "READY",
            "summary": "没有匹配到站内书籍；可以在自主编排页手动添加，或换个说法（如“计算机 408 数据结构”）",
            "output_refs": [],
            "evidence": [],
            "confidence": 0.3,
            "requires_human_review": False,
            "next_actions": ["在自主编排页添加书籍", "用更具体的关键词重试（专业/科目/书名词）"],
            "payload": {"books": []},
        }
    return {
        "status": "READY",
        "summary": f"按“{query[:40]}”找到 {len(books)} 本书（方向：{curator.infer_subject_type(query) or '未识别'}）",
        "output_refs": [f"book:{book['id']}" for book in books],
        "evidence": [f"{book['title']}（{book['author']}，{book['level']}）" for book in books[:4]],
        "confidence": 0.8,
        "requires_human_review": False,
        "next_actions": ["在自主编排页把书加入清单", "让我按这些书编排工作流"],
        "payload": {"books": books},
    }


def workflow_builder_worker(db: Session, user_id: int, subject_id: int | None, params: dict) -> dict:
    from app.services import curator

    autonomy = curator.get_autonomy(db, user_id)
    if not autonomy.get("allow_agent_edit_workflow", True):
        return _blocked("智能体没有“修改工作流”的权限，请在 自主编排 页开启后再试")
    text = (params.get("message") or params.get("text") or "").strip()
    if not text:
        return _blocked("请描述学习流程，例如：给数学一编排 基础-强化-真题-冲刺 工作流")
    try:
        draft = curator.compose_workflow(db, user_id, text)
    except ValueError as exc:
        return _blocked(str(exc))
    saved = params.get("save", True)
    workflow = curator.save_workflow(db, user_id, draft) if saved else None
    steps_text = " → ".join(step["name"] for step in draft["steps"])
    return {
        "status": "READY",
        "summary": f"已编排工作流：{steps_text}" + ("（已保存）" if workflow else "（草稿）"),
        "output_refs": ([f"workflow:{workflow.id}"] if workflow else []) + [f"book:{b['id']}" for b in draft["suggested_books"]],
        "evidence": [
            f"{step['name']}：{step['focus']}（{step['minutes']} 分钟）" for step in draft["steps"]
        ],
        "confidence": 0.82,
        "requires_human_review": False,
        "next_actions": ["在自主编排页调整步骤或删除", "按该工作流生成今日计划"],
        "payload": {"draft": draft, "workflow": curator.serialize_workflow(workflow) if workflow else None},
    }


def subject_manager_worker(db: Session, user_id: int, subject_id: int | None, params: dict) -> dict:
    from app.services import curator

    text = (params.get("message") or params.get("text") or "").strip()
    autonomy = curator.get_autonomy(db, user_id)
    lowered = text.lower()
    wants_remove = any(keyword in lowered for keyword in ("移除", "删除", "去掉", "不考", "取消"))

    if wants_remove:
        if not autonomy.get("allow_agent_remove_subjects", False):
            return _blocked(
                "删除科目属于不可逆操作，当前权限为「需人工确认」。请到 自主编排 页手动删除科目，"
                "或在权限设置中开启「允许智能体移除科目」。"
            )
        target = None
        for subject in subjects_of(db, user_id):
            if subject.name and subject.name.lower() in lowered:
                target = subject
                break
        if target is None:
            return _blocked("没听出要移除哪个科目，请带上科目名，例如：移除英语一")
        try:
            removed = curator.remove_subject(db, user_id, target.id)
        except ValueError as exc:
            return _blocked(str(exc))
        return {
            "status": "READY",
            "summary": f"已移除科目：{removed['name']}（同时清理 {removed['knowledge_removed']} 个知识点及其工作流）",
            "output_refs": [],
            "evidence": [f"removed subject {removed['removed']}"],
            "confidence": 0.9,
            "requires_human_review": True,
            "next_actions": ["确认计划已重新生成", "如误删请在自主编排页重新添加并导入资料"],
            "payload": removed,
        }

    if not autonomy.get("allow_agent_add_subjects", True):
        return _blocked("智能体没有“新增科目”的权限，请在 自主编排 页开启后再试")
    name = text
    for prefix in ("帮我加一个", "帮我添加", "新增科目", "加一个", "添加科目", "新增", "添加"):
        name = name.replace(prefix, "")
    name = name.strip("：: ，,。.!！?？")
    if len(name) < 2:
        return _blocked("请补充科目名称，例如：新增科目 数据结构与算法")
    subject_type = "custom"
    if any(keyword in text for keyword in ("数学", "高数", "线代", "概率")):
        subject_type = "math_1"
    elif any(keyword in text for keyword in ("英语", "阅读", "写作")):
        subject_type = "english_1"
    try:
        subject = curator.add_subject(db, user_id, name, subject_type=subject_type)
    except ValueError as exc:
        return _blocked(str(exc))
    return {
        "status": "READY",
        "summary": f"已新增科目：{subject.name}（{subject.total_score} 分）",
        "output_refs": [f"subject:{subject.id}"],
        "evidence": [],
        "confidence": 0.85,
        "requires_human_review": False,
        "next_actions": ["在自主编排页导入该科目的资料/真题", "编排它的学习工作流"],
        "payload": {"id": subject.id, "name": subject.name},
    }


def _blocked(summary: str) -> dict:
    return {
        "status": "BLOCKED",
        "summary": summary,
        "output_refs": [],
        "evidence": [],
        "confidence": 0.2,
        "requires_human_review": False,
        "next_actions": [],
        "payload": {},
    }


WORKERS = {
    WorkerType.COLLECTOR: collector,
    WorkerType.PARSER: parser,
    WorkerType.TAGGER: tagger,
    WorkerType.EXAM_ANALYST: exam_analyst,
    WorkerType.ROI_PLANNER: roi_planner,
    WorkerType.QUESTION_GENERATOR: question_generator,
    WorkerType.SOLVER_VERIFIER: solver_verifier,
    WorkerType.MEMORY_BUILDER: memory_builder,
    WorkerType.TEST_DESIGNER: test_designer,
    WorkerType.DIAGNOSTICIAN: diagnostician,
    WorkerType.MATH_SPECIALIST: subject_specialist,
    WorkerType.ENGLISH_SPECIALIST: subject_specialist,
    WorkerType.PROFESSIONAL_SPECIALIST: subject_specialist,
    WorkerType.WATCHDOG: watchdog_worker,
    WorkerType.CURATOR: curator_worker,
    WorkerType.WORKFLOW_BUILDER: workflow_builder_worker,
    WorkerType.SUBJECT_MANAGER: subject_manager_worker,
}

# ticket type -> default worker
TICKET_WORKER = {
    "collect_sources": WorkerType.COLLECTOR,
    "parse_paper": WorkerType.PARSER,
    "tag_questions": WorkerType.TAGGER,
    "analyze_exam_frequency": WorkerType.EXAM_ANALYST,
    "recompute_roi": WorkerType.ROI_PLANNER,
    "plan_daily": WorkerType.ROI_PLANNER,
    "build_memory_cards": WorkerType.MEMORY_BUILDER,
    "generate_questions": WorkerType.QUESTION_GENERATOR,
    "verify_question": WorkerType.SOLVER_VERIFIER,
    "design_test": WorkerType.TEST_DESIGNER,
    "diagnose_errors": WorkerType.DIAGNOSTICIAN,
    "subject_review": WorkerType.MATH_SPECIALIST,
    "watchdog_scan": WorkerType.WATCHDOG,
    "find_books": WorkerType.CURATOR,
    "compose_workflow": WorkerType.WORKFLOW_BUILDER,
    "manage_subjects": WorkerType.SUBJECT_MANAGER,
}
