"""Agent-layer and HTTP API tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agents import router as agent_router
from app.agents.runtime import (
    TicketTerminalError,
    create_ticket,
    get_or_create_thread,
    run_ticket,
)
from app.agents.watchdog import last_findings, run_scan
from app.db import session_scope
from app.main import app
from app.models import Ticket


def test_intent_classification():
    cases = {
        "帮我统计数学一的历年考频": "exam_analyst",
        "重新计算 ROI 并排序": "roi_planner",
        "生成一道 L2 变式题": "question_generator",
        "今天学什么": "roi_planner",
        "诊断一下最近的错题": "diagnostician",
        "构建背诵卡片": "memory_builder",
        "来一份模拟测试": "test_designer",
    }
    for message, expected_worker in cases.items():
        intent = agent_router.classify_intent(message)
        assert intent["worker_type"] == expected_worker, (message, intent)


def test_langgraph_router_end_to_end(seeded):
    result = agent_router.run_router(seeded["user_id"], "帮我重新计算 ROI 并安排今天的学习")
    assert result["reply"]
    steps = [step["step"] for step in result["trace"]]
    assert steps[:4] == ["classify", "plan_ticket", "execute", "finalize"]
    assert result["ticket"] is not None
    assert result["ticket"]["status"] in ("READY", "WAITING")


def test_ticket_idempotency(seeded):
    with session_scope() as db:
        thread = get_or_create_thread(db, seeded["user_id"], seeded["math_id"], "test-thread")
        first = create_ticket(db, seeded["user_id"], "analyze_exam_frequency", "统计考频", subject_id=seeded["math_id"], thread_id=thread.id)
        second = create_ticket(db, seeded["user_id"], "analyze_exam_frequency", "统计考频", subject_id=seeded["math_id"], thread_id=thread.id)
        db.commit()
        assert first.id == second.id


def test_ticket_lifecycle_and_terminal_guard(seeded):
    with session_scope() as db:
        thread = get_or_create_thread(db, seeded["user_id"], seeded["math_id"], "lifecycle")
        ticket = create_ticket(
            db,
            seeded["user_id"],
            "recompute_roi",
            "重算 ROI",
            subject_id=seeded["math_id"],
            thread_id=thread.id,
        )
        assignment, report = run_ticket(db, ticket)
        assert assignment.status == "READY"
        assert report.summary
        assert ticket.status in ("READY", "WAITING")
        with pytest.raises(TicketTerminalError):
            run_ticket(db, ticket)


def test_event_dedupe_and_routing(seeded):
    client = TestClient(app)
    payload = {"trigger_type": "review_due", "payload": {"source": "test"}}
    first = client.post("/api/events", json=payload)
    assert first.status_code == 200
    body = first.json()
    assert body["decision"]["action"] in ("create_ticket", "dedupe")
    second = client.post("/api/events", json=payload)
    assert second.status_code == 200
    assert second.json()["decision"]["action"] == "dedupe"


def test_watchdog_scan(seeded):
    with session_scope() as db:
        findings = run_scan(db, seeded["user_id"], auto_fix=True)
        assert isinstance(findings, list)
        persisted = last_findings()
        assert "findings" in persisted


def test_http_api_flow(seeded):
    client = TestClient(app)

    assert client.get("/health").json()["status"] == "ok"
    status = client.get("/api/system/status").json()
    assert status["counts"]["questions"] > 0

    dashboard = client.get("/api/dashboard").json()
    assert dashboard["coverage"]["knowledge_total"] > 0

    roi = client.get("/api/roi?limit=10").json()
    assert roi["items"]
    assert "formula" in roi["items"][0]["reasons"]

    graph = client.get("/api/knowledge/graph?subject_type=math_1").json()
    assert graph["dag"]["valid"] is True

    plan = client.get("/api/plan/today").json()
    assert plan["tasks"]
    task_id = plan["tasks"][0]["id"]
    patched = client.patch(f"/api/tasks/{task_id}", json={"status": "DONE"})
    assert patched.status_code == 200

    generated = client.post("/api/tests/generate", json={"subject_type": "math_1", "test_type": "micro"})
    assert generated.status_code == 200
    test = generated.json()
    assert len(test["questions"]) == 5
    assert all("answer" not in question for question in test["questions"])
    answers = []
    for index, question in enumerate(test["questions"]):
        answers.append(
            {
                "question_id": question["id"],
                "answer_text": "0",
                "duration_sec": 30,
                "hint_count": 0,
                "confidence_self": 0.5,
                "is_independent": True,
            }
        )
    submitted = client.post(f"/api/tests/{test['test']['id']}/submit", json={"answers": answers})
    assert submitted.status_code == 200
    assert submitted.json()["max_score"] > 0

    agent = client.post("/api/agent/run", json={"message": "分析一下数学一考频"}).json()
    assert agent["reply"]
    assert agent["ticket"]["status"] in ("READY", "WAITING")

    memory = client.get("/api/memory/cards?due_only=true&limit=3").json()
    assert memory["items"]
    review = client.post(
        f"/api/memory/cards/{memory['items'][0]['id']}/review", json={"quality": 5}
    )
    assert review.status_code == 200

    watchdog = client.post("/api/watchdog/scan", json={"auto_fix": False})
    assert watchdog.status_code == 200
    assert "findings" in watchdog.json()


def test_api_validation_errors(seeded):
    client = TestClient(app)
    assert client.post("/api/events", json={"trigger_type": "not-a-trigger"}).status_code == 422
    assert client.get("/api/questions/999999").status_code == 404
    assert client.post("/api/tests/generate", json={"subject_type": "math_1", "test_type": "micro", "duration_min": 2}).status_code == 422
    assert client.post("/api/questions/transform", json={"question_id": 999999, "level": "L1"}).status_code == 422


def test_question_crud_and_knowledge_detail(seeded):
    client = TestClient(app)
    questions = client.get("/api/questions?limit=3").json()
    assert questions["total"] > 0
    first = questions["items"][0]
    detail = client.get(f"/api/questions/{first['id']}").json()
    assert detail["answer"]
    knowledge = client.get(f"/api/knowledge/{detail['knowledge'][0]['knowledge_id']}").json()
    assert knowledge["name"]
    assert "related_questions" in knowledge


def test_ui_routing_spa_fallback_and_api_404(seeded):
    """A wrong URL must show the dashboard (not Next's exported 404.html),
    while unknown API paths keep JSON 404 semantics."""
    from app.main import WEB_OUT

    if not (WEB_OUT / "index.html").exists():
        pytest.skip("web/out not built")
    client = TestClient(app)

    assert client.get("/").status_code == 200
    fallback = client.get("/some/unknown/page")
    assert fallback.status_code == 200
    assert "ExamOS" in fallback.text

    api_404 = client.get("/api/does-not-exist")
    assert api_404.status_code == 404
    assert api_404.headers["content-type"].startswith("application/json")

    legacy = client.get("/ui", follow_redirects=False)
    assert legacy.status_code == 307
    assert legacy.headers["location"] == "/"

    plan = client.get("/plan", follow_redirects=False)
    assert plan.status_code in (200, 307)
    assert client.get("/plan/").status_code == 200

# --------------------------------------------------------------------------- #
# studio: books / workflows / subjects / autonomy
# --------------------------------------------------------------------------- #
def test_studio_overview_books_and_compose(seeded):
    client = TestClient(app)
    overview = client.get("/api/studio/overview").json()
    assert overview["books"]["total"] >= 20
    assert len(overview["subjects"]) >= 2
    assert "allow_agent_search_books" in overview["autonomy"]

    search = client.post("/api/books/search", json={"query": "计算机 408 数据结构 教材"}).json()
    assert search["items"], "book search should return curated matches"
    assert search["inferred_subject_type"] == "cs"
    assert any("数据结构" in book["title"] for book in search["items"])

    composed = client.post(
        "/api/workflows/compose",
        json={"text": "给数学一编排 基础-强化-真题-冲刺 工作流", "save": True},
    ).json()
    assert composed["saved"] is True
    names = [step["name"] for step in composed["draft"]["steps"]]
    assert len(names) >= 3 and composed["workflow"]["id"]

    workflows = client.get("/api/workflows").json()["items"]
    assert any(item["id"] == composed["workflow"]["id"] for item in workflows)


def test_studio_subject_lifecycle_and_autonomy(seeded):
    client = TestClient(app)
    created = client.post(
        "/api/subjects",
        json={"name": "验收科目", "subject_type": "custom", "total_score": 100, "major": "验收"},
    )
    assert created.status_code == 200
    subject_id = created.json()["id"]

    duplicate = client.post("/api/subjects", json={"name": "验收科目", "subject_type": "custom"})
    assert duplicate.status_code == 422

    blocked = client.delete(f"/api/subjects/{subject_id}")
    assert blocked.status_code == 409

    removed = client.delete(f"/api/subjects/{subject_id}?confirm=true")
    assert removed.status_code == 200
    assert removed.json()["removed"] == subject_id

    patched = client.put("/api/autonomy", json={"allow_agent_remove_subjects": True})
    assert patched.status_code == 200
    assert patched.json()["allow_agent_remove_subjects"] is True
    client.put("/api/autonomy", json={"allow_agent_remove_subjects": False})


def test_agent_studio_intents(seeded):
    client = TestClient(app)
    cases = [
        ("帮我找心理学考研的实验心理学教材", "curator"),
        ("给英语一编排 词汇-阅读-写作-真题 工作流", "workflow_builder"),
        ("移除英语一", "subject_manager"),
    ]
    for message, worker in cases:
        result = client.post("/api/agent/run", json={"message": message}).json()
        assert result["intent"]["worker_type"] == worker, (message, result["intent"])
        assert result["reply"]
