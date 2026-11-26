"""Error diagnosis heuristics (PRD 13.1).

Deliberately rule-based and explainable: every diagnosis carries evidence.
When an LLM provider is configured it can only *rank* candidate error types,
never invent a new one, and its output still has to pass the same schema.
"""

from __future__ import annotations

from app.constants import ErrorType, QuestionType
from app.services.grading import letters, text_overlap

QUESTION_TYPES_PROSE = {
    QuestionType.TRANSLATION,
    QuestionType.WRITING,
    QuestionType.ESSAY,
    QuestionType.SHORT_ANSWER,
    QuestionType.TERM_EXPLAIN,
    QuestionType.CASE_ANALYSIS,
}
QUESTION_TYPES_CHOICE = {QuestionType.READING, QuestionType.CLOZE, QuestionType.CALC_SINGLE}


def classify_error(
    question,
    given: str,
    correct: bool,
    duration_sec: float = 0.0,
    hint_count: int = 0,
    confidence_self: float = 0.5,
    expected_minutes: float = 5.0,
) -> dict:
    if correct:
        return {
            "error_type": None,
            "label": "正确",
            "severity": "none",
            "evidence": [],
            "explanation": "答案正确，记录为有效掌握证据",
        }

    qtype = (question.question_type or "").lower()
    evidence: list[str] = []
    candidates: list[tuple[str, float, str]] = []

    if not (given or "").strip():
        candidates.append((ErrorType.CONCEPT, 0.9, "未作答，通常意味着概念或方法层面无从下手"))
        evidence.append("answer_is_empty")

    gold, given_text = question.answer or "", given or ""

    if qtype in QUESTION_TYPES_CHOICE:
        if letters(given_text) and letters(gold) and letters(given_text) != letters(gold):
            candidates.append((ErrorType.CONCEPT, 0.55, "选项判断错误，优先检查概念辨析与干扰项识别"))
            evidence.append("wrong_choice")

    if qtype in QUESTION_TYPES_PROSE:
        ratio = text_overlap(gold, given_text)
        if ratio < 0.35:
            candidates.append((ErrorType.EXPRESSION, 0.5, "表达覆盖不足：关键术语/要点缺失"))
            evidence.append(f"text_overlap={ratio:.2f}")
        elif ratio < 0.6:
            candidates.append((ErrorType.COMPREHENSION, 0.4, "理解基本到位但要点不全"))
            evidence.append(f"text_overlap={ratio:.2f}")

    if hint_count > 0:
        candidates.append((ErrorType.PREREQUISITE, 0.45, f"使用 {hint_count} 次提示仍出错，提示前置知识缺口"))
        evidence.append("needed_hints")

    if duration_sec > 0:
        expected_sec = max(60.0, expected_minutes * 60.0)
        if duration_sec > expected_sec * 2.0:
            candidates.append((ErrorType.TIME, 0.5, "耗时超过预期两倍，解题路径不熟练"))
            evidence.append(f"duration={duration_sec:.0f}s expected≈{expected_sec:.0f}s")
        elif duration_sec < expected_sec * 0.25 and qtype in {QuestionType.CALC_SINGLE, QuestionType.CALC_FILL}:
            candidates.append((ErrorType.CALCULATION, 0.4, "作答过快，疑似计算或审题失误"))
            evidence.append(f"duration={duration_sec:.0f}s")

    if confidence_self >= 0.75:
        candidates.append((ErrorType.CONCEPT, 0.4, "自评高信心但答错，存在概念性误解（虚假掌握）"))
        evidence.append("high_confidence_wrong")

    if question.question_type in {QuestionType.CALC_SOLVE, QuestionType.CALCULATION} and hint_count == 0:
        candidates.append((ErrorType.CALCULATION, 0.35, "解答题错误常见于中间步骤计算"))
        evidence.append("solve_question")

    if not candidates:
        candidates.append((ErrorType.UNKNOWN, 0.3, "证据不足，先标记 At Risk，结合后续重复错误确认"))

    candidates.sort(key=lambda item: item[1], reverse=True)
    best_type, score, explanation = candidates[0]
    return {
        "error_type": best_type,
        "label": _LABELS.get(best_type, best_type),
        "severity": "high" if score >= 0.6 else "medium" if score >= 0.4 else "low",
        "evidence": evidence,
        "candidates": [{"error_type": t, "confidence": round(s, 2), "explanation": e} for t, s, e in candidates],
        "explanation": explanation,
    }


_LABELS = {
    ErrorType.CONCEPT: "概念错误",
    ErrorType.PREREQUISITE: "前置知识缺失",
    ErrorType.METHOD: "方法选择错误",
    ErrorType.CALCULATION: "计算错误",
    ErrorType.MEMORY: "记忆错误",
    ErrorType.COMPREHENSION: "理解偏差",
    ErrorType.EXPRESSION: "表达不完整",
    ErrorType.LOGICAL: "逻辑错误",
    ErrorType.INTEGRATION: "综合应用错误",
    ErrorType.TIME: "时间控制错误",
    ErrorType.UNKNOWN: "待确认",
}
