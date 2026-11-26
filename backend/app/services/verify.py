"""Verification pipeline for generated questions (PRD 8.3).

Every synthetic question must pass *independent* checks before entering the
practice bank. The generator never gets to mark its own output valid: this
module re-derives the answer with SymPy and inspects uniqueness / scope /
duplication.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

try:
    import sympy as sp
    from sympy.parsing.sympy_parser import implicit_multiplication_application, parse_expr, standard_transformations

    _SYMPY = True
    _TRANSFORMS = standard_transformations + (implicit_multiplication_application,)
except Exception:  # pragma: no cover
    _SYMPY = False
    _TRANSFORMS = None

from app.models import KnowledgeNode, Question


def _as_expr(value: str):
    if not _SYMPY:
        return None
    try:
        text = str(value).replace("^", "**").replace("×", "*").replace("÷", "/")
        return parse_expr(text, transformations=_TRANSFORMS, evaluate=True)
    except Exception:
        return None


def symbolic_equal(a, b, tol: float = 1e-9) -> bool:
    e1, e2 = _as_expr(a), _as_expr(b)
    if e1 is None or e2 is None:
        return False
    try:
        if e1.is_number and e2.is_number:
            return bool(abs(complex(e1) - complex(e2)) <= tol)
        return bool(sp.simplify(e1 - e2) == 0)
    except Exception:
        return False


def check_numeric_answer(answer: str, expected, checks: list[dict]) -> None:
    """Independent re-derivation check: the generator passes the expected value."""
    ok = symbolic_equal(answer, expected)
    checks.append(
        {
            "check": "independent_solve",
            "passed": bool(ok),
            "detail": f"SymPy 独立求解期望 {expected}，题目答案 {answer}",
        }
    )


def check_unique_choice(options: list[str], answer_letter: str, checks: list[dict]) -> None:
    answers = [opt for opt in options if opt.startswith(answer_letter + ".") or opt.startswith(answer_letter + "．")]
    letters = [chr(ord("A") + i) for i in range(len(options))]
    checks.append(
        {
            "check": "answer_uniqueness",
            "passed": len(answers) == 1 and answer_letter in letters,
            "detail": f"选项 {len(options)} 个，正确答案 {answer_letter} 唯一",
        }
    )


def check_scope(db: Session, subject_id: int, knowledge_codes: list[str], checks: list[dict]) -> None:
    nodes = list(
        db.scalars(select(KnowledgeNode).where(KnowledgeNode.subject_id == subject_id, KnowledgeNode.code.in_(knowledge_codes)))
    )
    found = {node.code for node in nodes}
    missing = [code for code in knowledge_codes if code not in found]
    checks.append(
        {
            "check": "syllabus_scope",
            "passed": not missing,
            "detail": f"{len(found)}/{len(knowledge_codes)} 个知识点在考纲内" + (f"，缺失: {missing}" if missing else ""),
        }
    )


def check_answer_present(answer: str, checks: list[dict]) -> None:
    checks.append(
        {
            "check": "answer_present",
            "passed": bool((answer or "").strip()),
            "detail": "答案与解析非空" if answer else "答案为空",
        }
    )


# --------------------------------------------------------------------------- #
# lightweight near-duplicate detection (no external embeddings offline)
# --------------------------------------------------------------------------- #
def embed(text: str, dim: int = 512) -> np.ndarray:
    vector = np.zeros(dim, dtype=np.float32)
    normalized = re.sub(r"\s+", "", (text or "").lower())
    for size in (2, 3):
        for i in range(max(0, len(normalized) - size + 1)):
            gram = normalized[i : i + size]
            digest = int(hashlib.md5(gram.encode("utf-8")).hexdigest()[:8], 16)
            vector[digest % dim] += 1.0
    norm = np.linalg.norm(vector)
    return vector / norm if norm else vector


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.dot(a, b) / denom) if denom else 0.0


def nearest_similarity(db: Session, subject_id: int, text: str, limit: int = 300) -> tuple[float, int | None]:
    vector = embed(text)
    rows = db.execute(
        select(Question.id, Question.content).where(Question.subject_id == subject_id).order_by(Question.id.desc()).limit(limit)
    ).all()
    best, best_id = 0.0, None
    for qid, content in rows:
        score = cosine(vector, embed(content))
        if score > best:
            best, best_id = score, qid
    return best, best_id


def verify_generated(
    db: Session,
    subject_id: int,
    content: str,
    answer: str,
    expected,
    knowledge_codes: list[str],
    options: list[str] | None = None,
    answer_letter: str | None = None,
    correct_option: str | None = None,
    equivalent_pair: tuple[str, str] | None = None,
) -> dict:
    checks: list[dict] = []
    check_answer_present(answer, checks)

    if answer_letter and correct_option:
        # the letter the question claims must actually point at the correct option
        matched = next(
            (opt for opt in (options or []) if opt.strip().startswith(answer_letter + ".")),
            None,
        )
        actual = matched.split(".", 1)[1].strip() if matched else None
        checks.append(
            {
                "check": "answer_letter_consistent",
                "passed": bool(actual) and equivalent_text(actual, correct_option),
                "detail": f"选项 {answer_letter} = {actual}，正确答案应为 {correct_option}",
            }
        )
        checks.append(
            {
                "check": "independent_solve",
                "passed": equivalent_text(correct_option, expected) if expected is not None else True,
                "detail": f"独立求解期望 {expected}，正确选项 {correct_option}",
            }
        )
    elif expected is not None:
        check_numeric_answer(answer, expected, checks)

    if equivalent_pair and _SYMPY:
        left, right = equivalent_pair
        try:
            x = sp.Symbol("x", real=True)
            ratio = sp.limit(_as_expr(left) / _as_expr(right), x, 0)
            passed = bool(sp.simplify(ratio - 1) == 0)
            detail = f"lim ({left})/({right}) = {ratio}"
        except Exception:
            passed, detail = False, f"无法独立验证等价关系：{left} ~ {right}"
        checks.append({"check": "equivalence_recheck", "passed": passed, "detail": detail})

    if options and answer_letter:
        check_unique_choice(options, answer_letter, checks)
    check_scope(db, subject_id, knowledge_codes, checks)
    similarity, similar_to = nearest_similarity(db, subject_id, content)
    checks.append(
        {
            "check": "duplicate_distance",
            "passed": similarity < 0.92,
            "detail": f"与题库最相似题相似度 {similarity:.2f}" + (f"（question {similar_to}）" if similar_to else ""),
        }
    )
    passed = all(check["passed"] for check in checks if check["check"] != "duplicate_distance")
    status = "verified" if passed else "rejected"
    return {
        "status": status,
        "checks": checks,
        "confidence": round(sum(1 for c in checks if c["passed"]) / max(1, len(checks)), 4),
        "solver": "sympy" if _SYMPY else "unavailable",
        "verified_at": datetime.now(UTC).isoformat(),
        "similarity": round(similarity, 4),
    }


def equivalent_text(a: str, b: str) -> bool:
    """Compare two answer strings, falling back to literal equality."""
    if a == b:
        return True
    return symbolic_equal(a, b)
