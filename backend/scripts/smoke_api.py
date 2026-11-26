"""End-to-end API smoke test for ExamOS.

Usage (backend must be running on 127.0.0.1:8000):
    python scripts/smoke_api.py
"""

from __future__ import annotations

import json
import sys

import httpx

BASE = "http://127.0.0.1:8000"
PASS, FAIL = "PASS", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    results.append((name, PASS if condition else FAIL, detail))


def main() -> int:
    client = httpx.Client(base_url=BASE, timeout=90)

    health = client.get("/health")
    check("health", health.status_code == 200 and health.json()["status"] == "ok", health.text[:120])

    status = client.get("/api/system/status").json()
    check("seeded", status["counts"]["questions"] > 0, json.dumps(status["counts"], ensure_ascii=False))

    dash = client.get("/api/dashboard").json()
    check("dashboard", "mastery" in dash and "coverage" in dash, f"knowledge={dash['coverage']['knowledge_total']}")
    check("countdown", dash["countdown_days"] is not None, f"days={dash['countdown_days']}")

    roi = client.get("/api/roi?limit=200").json()
    eligible = [row for row in roi["items"] if not row["blocked"]]
    check("roi rows", len(roi["items"]) > 0, f"items={len(roi['items'])}")
    check("roi ranking not all blocked", len(eligible) >= 5, f"eligible={len(eligible)}")
    if roi["items"]:
        top = eligible[0] if eligible else roi["items"][0]
        check("roi explainable", "formula" in top["reasons"] and "frequency" in top["reasons"], top["code"])

    graph = client.get("/api/knowledge/graph?subject_type=math_1").json()
    check("knowledge graph dag", graph["dag"]["valid"], f"nodes={len(graph['nodes'])} edges={len(graph['edges'])}")

    plan = client.get("/api/plan/today").json()
    check("plan tasks", len(plan["tasks"]) >= 3, f"tasks={len(plan['tasks'])} budget={plan['budget_minutes']}")
    check("plan within budget", plan["used_minutes"] <= plan["budget_minutes"] * 1.35,
          f"used={plan['used_minutes']}")

    # ---------------- attempt -> mastery -> diagnosis -> rollback -------------
    questions = client.get("/api/questions?subject_id=1&limit=20").json()["items"]
    math_question = next(
        q
        for q in questions
        if q["question_type"] in {"calc_single", "calc_fill", "calc_solve"} and q.get("knowledge")
    )
    attempt = client.post(
        "/api/attempts",
        json={"question_id": math_question["id"], "answer_text": "0", "duration_sec": 40},
    ).json()
    check("attempt graded", attempt["attempt"]["correct"] is False, attempt["attempt"]["question_key"])
    check("attempt diagnosis", attempt["diagnosis"]["error_type"] is not None, attempt["diagnosis"]["label"])
    check("attempt rollback", attempt["rollback"]["needed"] is True,
          f"nodes={len(attempt['rollback']['nodes'])}")

    # correct attempt raises mastery
    detail = client.get(f"/api/questions/{math_question['id']}").json()
    good = client.post(
        "/api/attempts",
        json={"question_id": math_question["id"], "answer_text": detail["answer"], "duration_sec": 60},
    ).json()
    check("correct answer grading", good["attempt"]["correct"] is True, detail["answer"][:40])
    check("mastery updated", len(good["mastery"]) > 0, f"knowledge={len(good['mastery'])}")

    # ---------------- question transformation + verification -----------------
    transform = client.post(
        "/api/questions/transform",
        json={"question_id": math_question["id"], "level": "L1", "count": 1},
    )
    transform_body = transform.json()
    ok = transform.status_code == 200 and transform_body.get("items")
    check("transform L1", bool(ok), f"status={transform.status_code}")
    if ok:
        item = transform_body["items"][0]
        verification = item["verification"]
        check("transform verified", verification["status"] == "verified",
              json.dumps(verification["checks"], ensure_ascii=False)[:200])
        check("transform synthetic flag", item["question"]["is_synthetic"] is True,
              f"parent={item['question']['parent_question_id']}")

    # ---------------- test generation + submission ---------------------------
    generated = client.post("/api/tests/generate", json={"subject_type": "math_1", "test_type": "micro"})
    check("test generate", generated.status_code == 200, str(generated.status_code))
    if generated.status_code == 200:
        body = generated.json()
        test_id = body["test"]["id"]
        questions = body["questions"]
        answers = []
        for index, question in enumerate(questions):
            correct_text = client.get(f"/api/questions/{question['id']}").json()["answer"]
            answers.append(
                {
                    "question_id": question["id"],
                    "answer_text": correct_text if index % 2 == 0 else "0",
                    "duration_sec": 60,
                    "hint_count": 0,
                    "confidence_self": 0.6,
                    "is_independent": True,
                }
            )
        blueprint = body["test"]["blueprint"]
        check("blueprint buckets", len(blueprint.get("bucket_counts", {})) >= 2,
              json.dumps(blueprint.get("bucket_counts"), ensure_ascii=False))
        submitted = client.post(f"/api/tests/{test_id}/submit", json={"answers": answers})
        report = submitted.json()
        check("test submit", submitted.status_code == 200, str(submitted.status_code))
        check("test score", 0 <= report["score"] <= report["max_score"],
              f"{report['score']}/{report['max_score']}")
        check("test diagnosis", isinstance(report["diagnosis"], list), f"n={len(report['diagnosis'])}")
        check("test report stored", report.get("next_actions") is not None and len(report["next_actions"]) > 0,
              "; ".join(report["next_actions"])[:120])
        stored = client.get(f"/api/tests/{test_id}/report")
        check("report endpoint", stored.status_code == 200, str(stored.status_code))

    # ---------------- memory engine -----------------------------------------
    client.post("/api/memory/build", json={})
    cards = client.get("/api/memory/cards?limit=5").json()["items"]
    check("memory due cards", len(cards) > 0, f"n={len(cards)}")
    if cards:
        reviewed = client.post(f"/api/memory/cards/{cards[0]['id']}/review", json={"quality": 4, "response_text": "回忆正确"})
        check("memory review", reviewed.status_code == 200 and reviewed.json()["state"] != "UNSEEN",
              reviewed.json().get("state", ""))

    # ---------------- agent / events / watchdog -----------------------------
    agent = client.post("/api/agent/run", json={"message": "帮我重新计算 ROI 并安排今天的学习"}).json()
    check("agent reply", bool(agent.get("reply")), agent.get("reply", "")[:120])
    check("agent trace", len(agent.get("trace", [])) >= 4, f"steps={len(agent.get('trace', []))}")
    check("agent ticket", agent.get("ticket") is not None, str(agent.get("ticket", {}).get("status")))

    event = client.post("/api/events", json={"trigger_type": "week_end", "subject_type": "math_1"}).json()
    check("event routed", event["decision"]["action"] in ("create_ticket", "dedupe"), event["decision"]["reason"])
    dedupe = client.post("/api/events", json={"trigger_type": "week_end", "subject_type": "math_1"}).json()
    check("event dedupe", dedupe["decision"]["action"] == "dedupe", dedupe["decision"]["reason"])

    watchdog = client.post("/api/watchdog/scan", json={"auto_fix": True})
    check("watchdog scan", watchdog.status_code == 200, str(watchdog.status_code))

    tickets = client.get("/api/tickets?limit=50").json()
    check("tickets listed", tickets["total"] > 0, f"n={tickets['total']}")
    if tickets["items"]:
        detail_ticket = client.get(f"/api/tickets/{tickets['items'][0]['id']}").json()
        check("ticket detail reports", "reports" in detail_ticket and "assignments" in detail_ticket,
              f"reports={len(detail_ticket['reports'])}")

    # ---------------- import a document -------------------------------------
    source = client.post("/api/sources", json={"key": "smoke-source", "source_type": "user_provided", "title": "冒烟测试资料"}).json()
    document = client.post(
        "/api/documents",
        json={
            "source_key": "smoke-source",
            "filename": "mock.txt",
            "doc_type": "paper",
            "subject_type": "math_1",
            "paper_year": 2025,
            "paper_type": "mock",
            "content_text": "1. 求 lim_{x→0} sin(3x)/x 的值。\n答案：3\n2. 计算 ∫_0^1 2x dx。\n答案：1",
        },
    ).json()
    check(
        "document import",
        document.get("duplicate") is True or document.get("questions_created") == 2,
        json.dumps(document, ensure_ascii=False)[:160],
    )

    # ---------------- studio: books / workflows / autonomy -------------------
    client.post("/api/studio/bootstrap", json={})
    overview = client.get("/api/studio/overview").json()
    check("studio books seeded", overview["books"]["total"] >= 20, f"books={overview['books']['total']}")
    check("studio subjects", len(overview["subjects"]) >= 2, f"subjects={len(overview['subjects'])}")

    found = client.post("/api/books/search", json={"query": "计算机 408 数据结构 教材"}).json()
    check("book NL search", len(found["items"]) > 0 and found["inferred_subject_type"] == "cs",
          f"n={len(found['items'])} type={found['inferred_subject_type']}")

    draft = client.post("/api/workflows/compose", json={"text": "给数学一编排 基础-强化-真题 工作流"}).json()
    check("workflow compose", len(draft["draft"]["steps"]) >= 3,
          " → ".join(step["name"] for step in draft["draft"]["steps"]))

    agent_books = client.post("/api/agent/run", json={"message": "帮我找心理学考研的实验心理学教材"}).json()
    check("agent finds books", agent_books["intent"]["worker_type"] == "curator" and bool(agent_books["reply"]),
          agent_books["intent"]["ticket_type"])

    autonomy = client.get("/api/autonomy").json()
    check("autonomy settings", "allow_agent_search_books" in autonomy, json.dumps(autonomy, ensure_ascii=False))

    # ---------------- summary ------------------------------------------------
    failures = [(name, detail) for name, status, detail in results if status == FAIL]
    for name, status, detail in results:
        print(f"[{status}] {name}: {detail}")
    print(f"\n{len(results) - len(failures)}/{len(results)} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
