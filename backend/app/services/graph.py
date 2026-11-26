"""Knowledge graph utilities: DAG validation, prerequisite closure, rollback (PRD 13)."""

from __future__ import annotations

from collections import defaultdict, deque

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.constants import ErrorType, KnowledgeCategory, RelationType
from app.models import KnowledgeEdge, KnowledgeNode, MasteryStateRow, QuestionKnowledge


def prerequisite_map(db: Session, subject_id: int) -> dict[int, list[int]]:
    node_ids = [row[0] for row in db.execute(select(KnowledgeNode.id).where(KnowledgeNode.subject_id == subject_id))]
    edges = db.execute(
        select(KnowledgeEdge.source_id, KnowledgeEdge.target_id).where(
            KnowledgeEdge.relation_type == RelationType.PREREQUISITE,
            KnowledgeEdge.target_id.in_(node_ids + [0]),
        )
    ).all()
    out: dict[int, list[int]] = defaultdict(list)
    for source, target in edges:
        out[target].append(source)
    return out


def validate_dag(db: Session, subject_id: int) -> dict:
    """Kahn topological sort; returns cycle details when the graph is invalid."""
    nodes = list(db.scalars(select(KnowledgeNode).where(KnowledgeNode.subject_id == subject_id)))
    node_ids = {node.id for node in nodes}
    edges = db.execute(
        select(KnowledgeEdge.source_id, KnowledgeEdge.target_id).where(
            KnowledgeEdge.relation_type == RelationType.PREREQUISITE,
            KnowledgeEdge.source_id.in_(list(node_ids) + [0]),
            KnowledgeEdge.target_id.in_(list(node_ids) + [0]),
        )
    ).all()
    indegree = {nid: 0 for nid in node_ids}
    adjacency: dict[int, list[int]] = defaultdict(list)
    for source, target in edges:
        adjacency[source].append(target)
        indegree[target] += 1
    queue = deque([nid for nid, deg in indegree.items() if deg == 0])
    visited = 0
    while queue:
        current = queue.popleft()
        visited += 1
        for nxt in adjacency.get(current, []):
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)
    cycle_nodes = [nid for nid, deg in indegree.items() if deg > 0]
    return {
        "valid": visited == len(node_ids),
        "node_count": len(node_ids),
        "edge_count": len(edges),
        "cycle_nodes": [next((n.code for n in nodes if n.id == nid), nid) for nid in cycle_nodes],
    }


def ancestors(db: Session, subject_id: int, knowledge_id: int, max_depth: int = 6) -> list[tuple[int, int]]:
    """Return (knowledge_id, depth) for prerequisite ancestors, breadth-first."""
    prereqs = prerequisite_map(db, subject_id)
    seen: dict[int, int] = {}
    queue = deque([(knowledge_id, 0)])
    while queue:
        current, depth = queue.popleft()
        if depth >= max_depth:
            continue
        for parent in prereqs.get(current, []):
            if parent in seen:
                continue
            seen[parent] = depth + 1
            queue.append((parent, depth + 1))
    return list(seen.items())


def dependency_chain(db: Session, subject_id: int, knowledge_id: int) -> list[int]:
    """Prerequisites first, then the node itself (study order)."""
    anc = ancestors(db, subject_id, knowledge_id)
    ordered = [nid for nid, _depth in sorted(anc, key=lambda item: (-item[1], item[0]))]
    return ordered + [knowledge_id]


def rollback_plan(
    db: Session,
    user_id: int,
    subject_id: int,
    knowledge_ids: list[int],
    error_type: str,
    max_nodes: int = 6,
    question_id: int | None = None,
) -> dict:
    """Locate the minimal necessary remediation set for a failed question."""
    strategy, depth = ErrorType.ROLLBACK.get(error_type, ErrorType.ROLLBACK[ErrorType.UNKNOWN])
    gate = settings.prerequisite_gate

    mastery = {
        row.knowledge_id: row
        for row in db.scalars(select(MasteryStateRow).where(MasteryStateRow.user_id == user_id))
    }
    nodes = {
        node.id: node
        for node in db.scalars(select(KnowledgeNode).where(KnowledgeNode.subject_id == subject_id))
    }
    prereqs = prerequisite_map(db, subject_id)

    seeds: list[int]
    if error_type == ErrorType.PREREQUISITE or strategy == "prerequisite_chain":
        seeds = []
        for kid in knowledge_ids:
            for parent in prereqs.get(kid, []):
                row = mastery.get(parent)
                if row is None or row.mastery < gate:
                    seeds.append(parent)
        # the failed knowledge point itself is the final step of the repair chain
        seeds.extend(knowledge_ids)
        if not seeds:
            seeds = list(knowledge_ids)
    elif error_type == ErrorType.CONCEPT:
        weakest = sorted(knowledge_ids, key=lambda kid: mastery.get(kid).mastery if mastery.get(kid) else 0.0)
        seeds = weakest[:1]
    elif error_type == ErrorType.METHOD:
        method_ids = [
            node.id for node in nodes.values()
            if node.category in (KnowledgeCategory.METHOD, KnowledgeCategory.SKILL)
            and node.chapter in {nodes[kid].chapter for kid in knowledge_ids if kid in nodes}
        ]
        seeds = method_ids[:2] or knowledge_ids[:1]
    elif error_type == ErrorType.MEMORY:
        seeds = list(knowledge_ids)
    else:
        weakest = sorted(knowledge_ids, key=lambda kid: mastery.get(kid).mastery if mastery.get(kid) else 0.0)
        seeds = weakest[:2]

    # expand one level of prerequisites for integration / prerequisite failures
    expanded: list[int] = []
    for kid in seeds:
        if depth >= 2:
            expanded.extend(parent for parent in prereqs.get(kid, []))
        expanded.append(kid)

    ordered: list[int] = []
    seen: set[int] = set()
    for kid in expanded:
        for node_id in dependency_chain(db, subject_id, kid):
            if node_id in seen or node_id not in nodes:
                continue
            seen.add(node_id)
            ordered.append(node_id)

    result_nodes = []
    total_minutes = 0.0
    for node_id in ordered:
        node = nodes[node_id]
        row = mastery.get(node_id)
        current = row.mastery if row else 0.2
        if current >= settings.target_mastery and node_id not in knowledge_ids:
            continue
        minutes = float(node.est_minutes) * max(0.3, 1.0 - current)
        total_minutes += minutes
        result_nodes.append(
            {
                "knowledge_id": node.id,
                "code": node.code,
                "name": node.name,
                "chapter": node.chapter,
                "category": node.category,
                "mastery": round(current, 4),
                "state": row.state if row else "UNSEEN",
                "est_minutes": round(minutes, 1),
                "reason": _reason_for(node, error_type, current, gate),
            }
        )
        if len(result_nodes) >= max_nodes:
            break

    return {
        "needed": bool(result_nodes),
        "error_type": error_type,
        "strategy": strategy,
        "question_id": question_id,
        "nodes": result_nodes,
        "total_minutes": round(total_minutes, 1),
        "principle": "沿知识图谱定位最小必要修复范围，不重学整个章节",
        "note": "单次失误先标记 At Risk；重复错误或延迟测试失败才正式回滚"
        if error_type in (ErrorType.CALCULATION, ErrorType.TIME)
        else "",
    }


def _reason_for(node: KnowledgeNode, error_type: str, mastery: float, gate: float) -> str:
    if error_type == ErrorType.PREREQUISITE:
        return f"前置知识 {node.code} 掌握度 {mastery:.2f} 低于门槛 {gate:.2f}"
    if error_type == ErrorType.CONCEPT:
        return f"概念 {node.name} 掌握度仅 {mastery:.2f}，需回到定义与条件"
    if error_type == ErrorType.METHOD:
        return f"方法类知识 {node.name} 未稳定，需方法识别专项训练"
    if error_type == ErrorType.CALCULATION:
        return f"{node.name} 涉及运算，需计算专项训练"
    if error_type == ErrorType.MEMORY:
        return f"{node.name} 属于记忆型知识，需主动回忆与间隔复习"
    if error_type == ErrorType.INTEGRATION:
        return f"综合题考查 {node.name} 的组合应用"
    return f"{node.name} 当前掌握度 {mastery:.2f}"


def related_questions(db: Session, knowledge_id: int, limit: int = 5) -> list[dict]:
    from app.models import Question

    rows = db.execute(
        select(Question)
        .join(QuestionKnowledge, QuestionKnowledge.question_id == Question.id)
        .where(QuestionKnowledge.knowledge_id == knowledge_id)
        .order_by(QuestionKnowledge.weight.desc(), Question.id)
        .limit(limit)
    ).scalars().all()
    return [
        {"id": q.id, "key": q.key, "question_type": q.question_type, "content": q.content[:160], "score": q.score}
        for q in rows
    ]
