"""Question transformation engine (PRD 8).

L1 single-point variation, L2 two linked points, L3 multi-point integration,
L4 cross question-type transfer. Every generated item goes through
``app.services.verify.verify_generated`` which re-solves it with SymPy.

Offline templates are deliberately limited to mathematics, where correctness is
machine-checkable. English / professional generation is marked unsupported and
requires an LLM provider (see ``app/services/llm.py``) - we never emit
unverified questions.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.constants import QuestionType, TransformLevel
from app.models import KnowledgeNode, Paper, Question, QuestionVersion
from app.services.util import question_knowledge_map
from app.services.verify import verify_generated, symbolic_equal

try:
    import sympy as sp

    _SYMPY = True
except Exception:  # pragma: no cover
    _SYMPY = False


def ensure_synthetic_paper(db: Session, subject_id: int) -> Paper:
    key = f"synthetic-{subject_id}"
    paper = db.scalar(select(Paper).where(Paper.key == key))
    if paper:
        return paper
    paper = Paper(
        key=key,
        subject_id=subject_id,
        year=0,
        paper_type="synthetic",
        title="ExamOS 生成题（不计入考频统计）",
        total_score=0.0,
        meta={"synthetic": True},
    )
    db.add(paper)
    db.flush()
    return paper


def _fmt(value) -> str:
    try:
        return str(sp.nsimplify(value)) if _SYMPY else str(value)
    except Exception:
        return str(value)


# --------------------------------------------------------------------------- #
# L1 templates
# --------------------------------------------------------------------------- #
def _tpl_infinitesimal(rng: random.Random) -> dict | None:
    if not _SYMPY:
        return None
    x = sp.symbols("x", real=True)
    # (display, parseable expression, display equivalence, parseable equivalence)
    pairs = [
        ("sin x", "sin(x)", "x", "x"),
        ("tan x", "tan(x)", "x", "x"),
        ("1-cos x", "1-cos(x)", "x^2/2", "x**2/2"),
        ("ln(1+x)", "log(1+x)", "x", "x"),
        ("e^x-1", "exp(x)-1", "x", "x"),
        ("x-sin x", "x-sin(x)", "x^3/6", "x**3/6"),
        ("arcsin x", "asin(x)", "x", "x"),
        ("sqrt(1+x)-1", "sqrt(1+x)-1", "x/2", "x/2"),
    ]
    display, expr_str, equiv_display, equiv_str = rng.choice(pairs)
    expr = sp.sympify(expr_str.replace("^", "**"))
    equiv = sp.sympify(equiv_str.replace("^", "**"))
    try:
        ratio = sp.limit(expr / equiv, x, 0)
    except Exception:
        return None
    if sp.simplify(ratio - 1) != 0:
        return None

    pool = ["x^2/2", "x", "x^3/6", "x^2", "x/2"]
    distractors = [item for item in pool if item != equiv_display][:3]
    options = [(equiv_display, True)] + [(item, False) for item in distractors]
    rng.shuffle(options)
    letters = "ABCD"
    correct = next(letters[i] for i, (_opt, is_correct) in enumerate(options) if is_correct)
    option_text = "\n".join(f"{letters[i]}. {opt}" for i, (opt, _c) in enumerate(options))
    content = f"当 x→0 时，下列无穷小中与 {equiv_display} 等价的是（ ）\n{option_text}"
    answer = correct
    explanation = (
        f"由等价无穷小替换：{display} ~ {equiv_display}（可用泰勒展开或 "
        f"lim ({display})/({equiv_display}) = 1 验证）。其余选项阶数或系数不符，故选 {correct}。"
    )
    return {
        "content": content,
        "answer": answer,
        "explanation": explanation,
        "expected": equiv_display,
        "options": [f"{letters[i]}. {opt}" for i, (opt, _c) in enumerate(options)],
        "answer_letter": correct,
        "correct_option": equiv_display,
        "equivalent_pair": (expr_str, equiv_str),
        "knowledge_codes": ["MA-CALC-LIMIT", "MA-CALC-INFINITESIMAL"],
        "question_type": QuestionType.CALC_SINGLE,
        "score": 4.0,
        "difficulty": 0.35,
        "level": TransformLevel.L1,
        "rationale": "L1：替换目标无穷小并重排选项，考查等价无穷小表",
    }


def _tpl_definite_integral(rng: random.Random) -> dict | None:
    if not _SYMPY:
        return None
    x = sp.symbols("x", real=True)
    a = rng.randint(1, 4)
    b = rng.randint(-3, 3)
    c = rng.randint(-2, 5)
    lo, hi = (0, rng.randint(1, 3)) if rng.random() < 0.7 else (rng.randint(-2, 0), rng.randint(1, 3))
    expr = a * x**2 + b * x + c
    value = sp.integrate(expr, (x, lo, hi))
    if value == 0:
        return None
    content = (
        f"计算定积分 ∫_{lo}^{hi} ({a}x^2 {'+' if b >= 0 else '-'} {abs(b)}x {'+' if c >= 0 else '-'} {abs(c)}) dx。"
    )
    answer = _fmt(sp.simplify(value))
    explanation = (
        f"原函数为 {a}x^3/3 {'+' if b >= 0 else '-'} {abs(b)}x^2/2 {'+' if c >= 0 else '-'} {abs(c)}x，"
        f"代入上下限 {hi} 与 {lo} 得 {answer}。"
    )
    return {
        "content": content,
        "answer": answer,
        "explanation": explanation,
        "expected": sp.simplify(value),
        "options": None,
        "answer_letter": None,
        "knowledge_codes": ["MA-CALC-DEFINT", "MA-CALC-INT-APP"],
        "question_type": QuestionType.CALC_FILL,
        "score": 4.0,
        "difficulty": 0.45,
        "level": TransformLevel.L1,
        "rationale": "L1：更换被积函数系数与积分区间，考查牛顿-莱布尼茨公式",
    }


def _tpl_determinant(rng: random.Random) -> dict | None:
    if not _SYMPY:
        return None
    matrix = sp.Matrix([[rng.randint(-4, 4) for _ in range(3)] for _ in range(3)])
    det = matrix.det()
    if det == 0 or abs(det) > 200:
        return None
    rows = "\n".join(
        " & ".join(str(matrix[i, j]) for j in range(3)) for i in range(3)
    )
    content = f"计算三阶行列式 D = |{rows}| 的值。"
    explanation = "按第一行展开：D = a11*M11 - a12*M12 + a13*M13，逐项计算二阶行列式后求和。"
    return {
        "content": content,
        "answer": _fmt(det),
        "explanation": explanation,
        "expected": sp.simplify(det),
        "options": None,
        "answer_letter": None,
        "knowledge_codes": ["MA-LAL-DET", "MA-LAL-DET-CALC"],
        "question_type": QuestionType.CALC_FILL,
        "score": 4.0,
        "difficulty": 0.5,
        "level": TransformLevel.L1,
        "rationale": "L1：随机生成三阶行列式，考查按行展开与二阶行列式计算",
    }


def _tpl_eigen(rng: random.Random) -> dict | None:
    if not _SYMPY:
        return None
    lam1, lam2 = rng.sample(range(-3, 5), 2)
    tr, det = lam1 + lam2, lam1 * lam2
    # matrix [[a,b],[c,d]] with trace/det fixed
    a = rng.randint(-3, 3)
    d = tr - a
    b = rng.choice([-2, -1, 1, 2])
    c = (det - a * d) / b
    if c != int(c):
        return None
    content = f"设二阶矩阵 A = [[{a}, {b}], [{int(c)}, {d}]]，求 A 的两个特征值。"
    answer = f"{min(lam1, lam2)}, {max(lam1, lam2)}"
    explanation = (
        "特征方程 |λE - A| = λ^2 - tr(A)λ + det(A) = 0，"
        f"其中 tr(A) = {tr}，det(A) = {det}，解得特征值为 {answer}。"
    )
    return {
        "content": content,
        "answer": answer,
        "explanation": explanation,
        "expected": answer,
        "options": None,
        "answer_letter": None,
        "knowledge_codes": ["MA-LAL-EIGEN", "MA-LAL-DET-CALC"],
        "question_type": QuestionType.CALC_FILL,
        "score": 4.0,
        "difficulty": 0.55,
        "level": TransformLevel.L1,
        "rationale": "L1：用迹与行列式构造二阶矩阵，考查特征方程",
    }


def _tpl_probability(rng: random.Random) -> dict | None:
    if not _SYMPY:
        return None
    red, white, blue = rng.randint(2, 5), rng.randint(2, 5), rng.randint(1, 4)
    total = red + white + blue
    k = rng.randint(2, 3)
    favorable = sp.binomial(red, k)
    content = f"袋中有 {red} 个红球、{white} 个白球、{blue} 个蓝球，随机不放回抽取 {k} 个球，求恰好抽到 {k} 个红球的概率。"
    prob = sp.Rational(favorable, sp.binomial(total, k))
    answer = f"{prob}"
    explanation = (
        f"样本点总数 C({total},{k}) = {sp.binomial(total, k)}，"
        f"有利事件数 C({red},{k}) = {favorable}，故 P = {favorable}/{sp.binomial(total, k)} = {answer}。"
    )
    return {
        "content": content,
        "answer": answer,
        "explanation": explanation,
        "expected": prob,
        "options": None,
        "answer_letter": None,
        "knowledge_codes": ["MA-PROB-CLASSIC"],
        "question_type": QuestionType.CALC_FILL,
        "score": 4.0,
        "difficulty": 0.4,
        "level": TransformLevel.L1,
        "rationale": "L1：改变球数与抽取个数，考查古典概型",
    }


# --------------------------------------------------------------------------- #
# L2/L3/L4 templates (link two or more knowledge points)
# --------------------------------------------------------------------------- #
def _tpl_l2_charpoly(rng: random.Random) -> dict | None:
    if not _SYMPY:
        return None
    a, b = rng.randint(1, 4), rng.randint(1, 3)
    matrix = sp.Matrix([[a, b], [b, a]])
    det = matrix.det()
    if det == 0:
        return None
    lam1, lam2 = a + b, a - b
    content = (
        f"设 A = [[{a}, {b}], [{b}, {a}]]。求 det(A)，并说明它与特征值的关系。"
    )
    answer = str(det)
    explanation = (
        f"|λE-A| = (λ-{a})^2 - {b}^2 = 0 得特征值 {a}+{b} = {lam1}, {a}-{b} = {lam2}；"
        f"行列式等于特征值之积：{lam1}×{lam2} = {det}，故 det(A) = {det}。"
    )
    return {
        "content": content,
        "answer": answer,
        "explanation": explanation,
        "expected": det,
        "options": None,
        "answer_letter": None,
        "knowledge_codes": ["MA-LAL-EIGEN", "MA-LAL-DET-CALC"],
        "question_type": QuestionType.CALC_FILL,
        "score": 10.0,
        "difficulty": 0.6,
        "level": TransformLevel.L2,
        "rationale": "L2：特征值（LAL-EIGEN）与行列式性质（LAL-DET-CALC）组合：用特征值之积求行列式",
    }


def _tpl_l3_limit_integral(rng: random.Random) -> dict | None:
    if not _SYMPY:
        return None
    x = sp.symbols("x", real=True)
    a = rng.randint(1, 3)
    b = rng.randint(-2, 2)
    # lim_{x->0} (1/x) * ∫_0^x (a t + b) dt = b
    limit_value = b
    content = (
        f"设 f(x) = ∫_0^x ({a}t {'+' if b >= 0 else '-'} {abs(b)}) dt，求 lim_{{x→0}} f(x)/x。"
    )
    answer = str(limit_value)
    explanation = (
        f"f(x) = {a}x^2/2 {'+' if b >= 0 else '-'} {abs(b)}x，故 f(x)/x → {limit_value}（x→0）。"
        "也可用洛必达法则：f'(0) = b。"
    )
    return {
        "content": content,
        "answer": answer,
        "explanation": explanation,
        "expected": limit_value,
        "options": None,
        "answer_letter": None,
        "knowledge_codes": ["MA-CALC-LIMIT", "MA-CALC-DEFINT", "MA-CALC-DERIV"],
        "question_type": QuestionType.CALC_SOLVE,
        "score": 12.0,
        "difficulty": 0.65,
        "level": TransformLevel.L3,
        "rationale": "L3：变上限积分 + 极限 + 导数定义三点综合",
    }


def _tpl_l4_integral_area(rng: random.Random) -> dict | None:
    if not _SYMPY:
        return None
    x = sp.symbols("x", real=True)
    # parabola and line: intersection points chosen to be integers
    p = rng.randint(1, 2)
    q = rng.randint(1, 4)
    f = x**2
    g = q * x  # intersects at 0, q
    area = sp.integrate(g - f, (x, 0, q))
    content = (
        f"求曲线 y = x^2 与直线 y = {q}x 所围成的平面图形的面积。"
    )
    answer = str(sp.simplify(area))
    explanation = (
        f"交点由 x^2 = {q}x 得 x = 0 与 x = {q}；在 (0, {q}) 上 {q}x ≥ x^2，"
        f"故面积 S = ∫_0^{q} ({q}x - x^2) dx = {answer}。"
    )
    return {
        "content": content,
        "answer": answer,
        "explanation": explanation,
        "expected": sp.simplify(area),
        "options": None,
        "answer_letter": None,
        "knowledge_codes": ["MA-CALC-DEFINT", "MA-CALC-INT-APP"],
        "question_type": QuestionType.CALC_SOLVE,
        "score": 12.0,
        "difficulty": 0.6,
        "level": TransformLevel.L4,
        "rationale": "L4：由“计算定积分”迁移为“几何应用建模”，入口从求值变为建模",
    }


TEMPLATES = {
    TransformLevel.L1: [_tpl_infinitesimal, _tpl_definite_integral, _tpl_determinant, _tpl_eigen, _tpl_probability],
    TransformLevel.L2: [_tpl_l2_charpoly],
    TransformLevel.L3: [_tpl_l3_limit_integral],
    TransformLevel.L4: [_tpl_l4_integral_area],
}


def generate_variants(
    db: Session,
    user_id: int,
    question_id: int,
    level: str = TransformLevel.L1,
    count: int = 1,
) -> list[dict]:
    parent = db.get(Question, question_id)
    if parent is None:
        raise ValueError("question not found")
    if parent.subject_id == 0:
        raise ValueError("invalid subject")

    level = level if level in TransformLevel.ALL else TransformLevel.L1
    paper = ensure_synthetic_paper(db, parent.subject_id)

    # knowledge context of the parent decides which templates are relevant
    parent_nodes = question_knowledge_map(db, parent.id)
    parent_codes = [node.code for node, _w, _c in parent_nodes]
    rng = random.Random(f"{parent.id}-{level}-{datetime.now(UTC).isoformat()}")

    created: list[dict] = []
    candidates = TEMPLATES[level]
    attempts = 0
    while len(created) < count and attempts < 6:
        attempts += 1
        template = candidates[attempts % len(candidates)]
        payload = template(rng)
        if payload is None:
            continue
        verification = verify_generated(
            db,
            parent.subject_id,
            payload["content"],
            payload["answer"],
            payload["expected"],
            payload["knowledge_codes"],
            options=payload.get("options"),
            answer_letter=payload.get("answer_letter"),
            correct_option=payload.get("correct_option"),
            equivalent_pair=payload.get("equivalent_pair"),
        )
        if verification["status"] != "verified":
            continue
        question = Question(
            key=f"SYN-{parent.id}-{level}-{datetime.now(UTC).timestamp():.0f}-{len(created)}",
            paper_id=paper.id,
            subject_id=parent.subject_id,
            number="",
            question_type=payload["question_type"],
            content=payload["content"],
            answer=payload["answer"],
            explanation=payload["explanation"],
            score=payload["score"],
            difficulty=payload["difficulty"],
            source_type="synthetic",
            is_synthetic=True,
            parent_question_id=parent.id,
            transform_level=level,
            verification=verification,
            meta={"rationale": payload["rationale"], "knowledge_codes": payload["knowledge_codes"]},
        )
        db.add(question)
        db.flush()

        # attach knowledge links (respecting the node code space)
        nodes = {
            node.code: node
            for node in db.scalars(select(KnowledgeNode).where(KnowledgeNode.code.in_(payload["knowledge_codes"])))
        }
        from app.models import QuestionKnowledge

        for index, code in enumerate(payload["knowledge_codes"]):
            node = nodes.get(code)
            if node is None:
                continue
            weight = 1.0 / len(payload["knowledge_codes"])
            db.add(
                QuestionKnowledge(
                    question_id=question.id,
                    knowledge_id=node.id,
                    weight=round(weight, 3),
                    confidence=verification["confidence"],
                    is_core=index == 0,
                    role="core" if index == 0 else "supporting",
                )
            )

        db.add(
            QuestionVersion(
                question_id=question.id,
                parent_id=parent.id,
                transform_type=level,
                knowledge_combo=payload["knowledge_codes"],
                rationale=payload["rationale"],
                verifier=verification,
                status="accepted",
            )
        )
        created.append(
            {
                "question": question,
                "verification": verification,
                "rationale": payload["rationale"],
                "level": level,
            }
        )
    db.commit()

    if not created:
        raise ValueError(f"该题目暂不支持 {level} 自动改造（离线模板仅覆盖数学可验证题型）")
    return created
