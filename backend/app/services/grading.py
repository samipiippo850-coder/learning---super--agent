"""Answer grading: deterministic, model-free, explainable.

Supports the three answer families in the demo domain:
* multiple choice (single or multi letter, e.g. "A", "21.B 22.A")
* numeric / symbolic math (SymPy equivalence, tolerance, set answers)
* free text (normalised token overlap, used for translation/writing/essay
  self-check and short answers)
"""

from __future__ import annotations

import re
import unicodedata

try:  # sympy is a hard dependency but keep grading usable in constrained envs
    import sympy as sp
    from sympy.parsing.sympy_parser import (
        implicit_multiplication_application,
        parse_expr,
        standard_transformations,
    )

    _SYMPY = True
except Exception:  # pragma: no cover
    _SYMPY = False

_TRANSFORMS = None
if _SYMPY:
    _TRANSFORMS = standard_transformations + (implicit_multiplication_application,)


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    value = value.replace("，", ",").replace("。", ".").replace("（", "(").replace("）", ")")
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value


def letters(value: str) -> list[str]:
    """Extract option letters in order: '21.B 22.A' -> ['b','a']."""
    return re.findall(r"[a-h]", normalize_text(value))


def _sympy_equal(a: str, b: str) -> bool | None:
    if not _SYMPY:
        return None
    try:
        ea = parse_expr(_normalize_math(a), transformations=_TRANSFORMS, evaluate=True)
        eb = parse_expr(_normalize_math(b), transformations=_TRANSFORMS, evaluate=True)
        diff = sp.simplify(ea - eb)
        return bool(diff == 0)
    except Exception:
        return None


def _is_close(a: float, b: float, rel: float = 1e-6, abs_: float = 1e-9) -> bool:
    return abs(a - b) <= max(abs_, rel * max(abs(a), abs(b)))


def _numeric_equal(a: str, b: str) -> bool | None:
    try:
        fa, fb = float(a), float(b)
    except (TypeError, ValueError):
        return None
    return _is_close(fa, fb)


def _split_set(value: str) -> list[str] | None:
    text = normalize_text(value).strip("{} ")
    if "," not in text:
        return None
    parts = [p.strip() for p in text.split(",") if p.strip()]
    return parts or None


def answer_equivalent(gold: str, given: str) -> bool:
    """Best-effort equivalence between a reference answer and a learner answer."""
    gold_n, given_n = normalize_text(gold), normalize_text(given)
    if not given_n:
        return False
    if gold_n == given_n:
        return True

    # multiple choice: compare full letter sequences when the reference is letters only
    gold_letters = letters(gold_n)
    if gold_letters and re.fullmatch(r"[a-h\s,.;:、]+", gold_n):
        return letters(given_n) == gold_letters

    # strip common decorations: "答案：", "= ", units
    gold_clean = re.sub(r"^(答案|answer)\s*[:：]?\s*", "", gold_n).strip(" =")
    given_clean = re.sub(r"^(答案|answer)\s*[:：]?\s*", "", given_n).strip(" =")
    if gold_clean == given_clean:
        return True

    numeric = _numeric_equal(gold_clean, given_clean)
    if numeric is not None:
        return numeric

    gold_set, given_set = _split_set(gold_clean), _split_set(given_clean)
    if gold_set and given_set and len(gold_set) == len(given_set):
        return all(
            any(answer_equivalent(g, s) for s in given_set) for g in gold_set
        )

    symbolic = _sympy_equal(gold_clean, given_clean)
    if symbolic is not None:
        return symbolic
    return False


_TOKEN_RE = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]")


def text_overlap(gold: str, given: str) -> float:
    """Token overlap ratio used for prose answers (translation / writing)."""
    gold_tokens = set(_TOKEN_RE.findall(normalize_text(gold)))
    given_tokens = set(_TOKEN_RE.findall(normalize_text(given)))
    if not gold_tokens or not given_tokens:
        return 0.0
    return len(gold_tokens & given_tokens) / len(gold_tokens)


def grade_answer(question_type: str, gold: str, given: str, max_score: float) -> dict:
    """Return {correct, score, method, ratio} for one answer."""
    qtype = (question_type or "").lower()
    if not (given or "").strip():
        return {"correct": False, "score": 0.0, "method": "empty", "ratio": 0.0}

    if qtype in {"reading", "cloze", "calc_single"}:
        ratio = 1.0 if answer_equivalent(gold, given) else 0.0
        return {
            "correct": ratio == 1.0,
            "score": round(max_score * ratio, 2),
            "method": "choice_exact",
            "ratio": ratio,
        }

    if qtype in {"translation", "writing", "essay", "short_answer", "term_explain", "case_analysis"}:
        ratio = text_overlap(gold, given)
        # partial credit: 0 below 30% overlap, then linear to 100%
        ratio = 0.0 if ratio < 0.3 else min(1.0, (ratio - 0.3) / 0.5)
        return {
            "correct": ratio >= 1.0,
            "score": round(max_score * ratio, 2),
            "method": "text_overlap",
            "ratio": round(ratio, 3),
        }

    # numeric / symbolic / free-form math
    if answer_equivalent(gold, given):
        return {"correct": True, "score": max_score, "method": "symbolic_equivalent", "ratio": 1.0}
    ratio = text_overlap(gold, given)
    return {
        "correct": False,
        "score": round(max_score * min(ratio, 0.5), 2),
        "method": "symbolic_mismatch",
        "ratio": round(ratio, 3),
    }

def _normalize_math(value: str) -> str:
    return (value or "").replace("^", "**").replace("×", "*").replace("÷", "/")
