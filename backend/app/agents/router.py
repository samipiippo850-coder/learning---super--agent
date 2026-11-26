"""Router: persistent LangGraph main graph that turns a user instruction (or an
event) into a Ticket, runs the matching Worker assignment, and reports back.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from sqlalchemy.orm import Session

from app.agents.runtime import (
    create_ticket,
    get_or_create_thread,
    run_ticket,
    serialize_report,
    serialize_ticket,
)
from app.agents.state import RouterState
from app.agents.workers import TICKET_WORKER
from app.config import VAR_DIR
from app.constants import WorkerType
from app.db import session_scope
from app.models import Thread

try:  # LangGraph is a hard dependency, but degrade gracefully if unavailable
    from langgraph.graph import END, StateGraph

    _LANGGRAPH = True
except Exception:  # pragma: no cover
    _LANGGRAPH = False

try:
    from langgraph.checkpoint.sqlite import SqliteSaver

    _SQLITE_SAVER = True
except Exception:  # pragma: no cover
    _SQLITE_SAVER = False


# --------------------------------------------------------------------------- #
# intent classification (rules first; optional LLM only re-ranks candidates)
# --------------------------------------------------------------------------- #
INTENTS: list[tuple[str, str, str, list[str]]] = [
    # structural / studio intents must be matched before the generic ones
    ("manage_subjects", "subject_manager", "新增或移除备考科目",
     ["移除科目", "删除科目", "去掉科目", "移除英语", "移除数学", "不考了", "取消科目",
      "新增科目", "添加科目", "加一个科目", "帮我加一个", "新增一门", "添加一门"]),
    ("find_books", "curator", "按自然语言检索书籍资料",
     ["找书", "书籍", "教材", "书单", "书目", "看什么书", "参考书", "推荐书", "书推荐"]),
    ("compose_workflow", "workflow_builder", "编排学习工作流",
     ["工作流", "学习流程", "编排", "学习路线", "流程编排", "编排学习"]),
    ("plan_daily", "roi_planner", "制定今日学习计划", ["计划", "今日", "安排", "plan", "今天学什么"]),
    ("recompute_roi", "roi_planner", "计算 ROI 并排序", ["roi", "收益", "排序", "优先", "先学什么"]),
    ("analyze_exam_frequency", "exam_analyst", "统计历年考频", ["考频", "频率", "考情", "真题统计", "几年"]),
    ("generate_questions", "question_generator", "生成变式训练题", ["生成题", "变式", "改造", "出题", "训练题"]),
    ("design_test", "test_designer", "生成针对性测试", ["测试", "小测", "模拟", "考试测试", "测一测"]),
    ("build_memory_cards", "memory_builder", "构建背诵材料", ["背诵", "记忆", "卡片", "背什么", "复习卡"]),
    ("diagnose_errors", "diagnostician", "诊断错误并回滚", ["诊断", "错题", "为什么错", "回滚", "补救"]),
    ("tag_questions", "tagger", "知识点标注", ["标注", "知识点", "打标签"]),
    ("collect_sources", "collector", "资料搜集与来源管理", ["搜集", "资料", "来源", "下载"]),
    ("subject_review", "math_specialist", "学科状态回顾", ["状态", "掌握", "进度", "学得怎么样", "汇报"]),
    ("watchdog_scan", "watchdog", "系统自检", ["自检", "watchdog", "监督", "异常"]),
]


def classify_intent(message: str) -> dict[str, Any]:
    text = (message or "").lower()
    for ticket_type, worker, title, keywords in INTENTS:
        for keyword in keywords:
            if keyword.lower() in text:
                return {
                    "ticket_type": ticket_type,
                    "worker_type": worker,
                    "title": title,
                    "matched_keyword": keyword,
                }
    return {
        "ticket_type": "subject_review",
        "worker_type": WorkerType.MATH_SPECIALIST,
        "title": "学科状态回顾",
        "matched_keyword": "",
    }


def _extract_params(message: str) -> dict[str, Any]:
    params: dict[str, Any] = {}
    match = re.search(r"(L[1-4])", message or "", re.IGNORECASE)
    if match:
        params["level"] = match.group(1).upper()
    match = re.search(r"(?:题目|question)[^\d]{0,4}(\d+)", message or "")
    if match:
        params["question_id"] = int(match.group(1))
    match = re.search(r"(micro|mixed|stage|mock|diagnostic)", message or "", re.IGNORECASE)
    if match:
        params["test_type"] = match.group(1).lower()
    return params


# --------------------------------------------------------------------------- #
# graph nodes
# --------------------------------------------------------------------------- #
def classify_node(state: RouterState) -> RouterState:
    intent = classify_intent(state.get("message", ""))
    trace = list(state.get("trace") or [])
    trace.append(
        {
            "step": "classify",
            "ticket_type": intent["ticket_type"],
            "worker_type": intent["worker_type"],
            "keyword": intent["matched_keyword"],
        }
    )
    return {
        **state,
        "ticket_type": intent["ticket_type"],
        "worker_type": intent["worker_type"],
        "params": {**_extract_params(state.get("message", "")), **(state.get("params") or {})},
        "trace": trace,
    }


def plan_ticket_node(state: RouterState) -> RouterState:
    trace = list(state.get("trace") or [])
    with session_scope() as db:
        topic = f"router:{state.get('subject_type') or 'all'}"
        thread = get_or_create_thread(db, state["user_id"], state.get("subject_id"), topic)
        title = state.get("message", "")[:120]
        ticket = create_ticket(
            db,
            state["user_id"],
            state.get("ticket_type") or "subject_review",
            title or "Router 自动任务",
            subject_id=state.get("subject_id"),
            priority=0.7,
            payload={
                "worker_type": state.get("worker_type"),
                "params": state.get("params") or {},
                "message": state.get("message", ""),
                "origin": "router",
                "thread_id": thread.id,
            },
            thread_id=thread.id,
        )
        thread_id = thread.id
        ticket_id = ticket.id
    trace.append({"step": "plan_ticket", "ticket_id": ticket_id, "thread_id": thread_id})
    return {**state, "ticket_id": ticket_id, "trace": trace}


def execute_node(state: RouterState) -> RouterState:
    trace = list(state.get("trace") or [])
    ticket_id = state.get("ticket_id")
    if not ticket_id:
        return {**state, "error": "no ticket", "trace": trace}
    with session_scope() as db:
        from app.models import Ticket

        ticket = db.get(Ticket, ticket_id)
        if ticket is None:
            return {**state, "error": "ticket not found", "trace": trace}
        assignment, report = run_ticket(
            db,
            ticket,
            worker_type=state.get("worker_type"),
            params=state.get("params") or {},
        )
        report_payload = serialize_report(report)
        ticket_payload = serialize_ticket(db, ticket)
    trace.append(
        {
            "step": "execute",
            "assignment_id": assignment.id,
            "worker_type": assignment.worker_type,
            "status": report_payload["status"],
            "summary": report_payload["summary"],
        }
    )
    return {
        **state,
        "report_id": report.id,
        "requires_human_review": report.requires_human_review,
        "trace": trace,
        "params": {
            **(state.get("params") or {}),
            "ticket": ticket_payload,
            "report": report_payload,
        },
    }


def finalize_node(state: RouterState) -> RouterState:
    report = (state.get("params") or {}).get("report") or {}
    ticket = (state.get("params") or {}).get("ticket") or {}
    lines = [
        f"任务：{ticket.get('title') or '自动任务'}",
        f"状态：{ticket.get('status') or report.get('status')}",
        f"结论：{report.get('summary') or state.get('error') or '无输出'}",
    ]
    if report.get("evidence"):
        lines.append("证据：" + "；".join(str(item) for item in report["evidence"][:4]))
    if report.get("next_actions"):
        lines.append("建议：" + "；".join(str(item) for item in report["next_actions"][:4]))
    if report.get("requires_human_review"):
        lines.append("该报告需要人工核验后才能进入正式记录。")
    trace = list(state.get("trace") or [])
    trace.append({"step": "finalize", "requires_human_review": bool(report.get("requires_human_review"))})
    return {**state, "reply": "\n".join(lines), "trace": trace}


def _build_graph():
    if not _LANGGRAPH:
        return None
    builder = StateGraph(RouterState)
    builder.add_node("classify", classify_node)
    builder.add_node("plan_ticket", plan_ticket_node)
    builder.add_node("execute", execute_node)
    builder.add_node("finalize", finalize_node)
    builder.set_entry_point("classify")
    builder.add_edge("classify", "plan_ticket")
    builder.add_edge("plan_ticket", "execute")
    builder.add_edge("execute", "finalize")
    builder.add_edge("finalize", END)
    checkpointer = None
    if _SQLITE_SAVER:
        try:
            conn = sqlite3.connect(str(VAR_DIR / "agent_checkpoints.sqlite"), check_same_thread=False)
            checkpointer = SqliteSaver(conn)
        except Exception:  # pragma: no cover
            checkpointer = None
    return builder.compile(checkpointer=checkpointer)


_GRAPH = _build_graph()


class _FallbackGraph:
    """Sequential fallback used when LangGraph cannot be imported."""

    def invoke(self, state: dict, config: dict | None = None) -> dict:
        for node in (classify_node, plan_ticket_node, execute_node, finalize_node):
            state = node(state)  # type: ignore[arg-type]
        return state


def run_router(
    user_id: int,
    message: str,
    subject_id: int | None = None,
    subject_type: str | None = None,
    params: dict | None = None,
) -> dict:
    graph = _GRAPH or _FallbackGraph()
    state: RouterState = {
        "message": message,
        "user_id": user_id,
        "subject_id": subject_id,
        "subject_type": subject_type,
        "params": params or {},
        "trace": [],
    }
    config = {"configurable": {"thread_id": f"user-{user_id}"}}
    result = graph.invoke(state, config)
    return {
        "reply": result.get("reply") or "",
        "trace": result.get("trace") or [],
        "ticket": (result.get("params") or {}).get("ticket"),
        "report": (result.get("params") or {}).get("report"),
        "requires_human_review": bool(result.get("requires_human_review")),
        "ticket_id": result.get("ticket_id"),
        "thread_id": (result.get("params") or {}).get("ticket", {}).get("thread_id"),
        "intent": {
            "ticket_type": result.get("ticket_type"),
            "worker_type": result.get("worker_type"),
        },
    }
