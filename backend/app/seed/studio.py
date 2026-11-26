"""Studio seed: book resource catalog + default study workflows.

These are curated, publicly available textbooks / exam-prep books (title,
author, publisher) used by the "find books & compose a workflow" feature. The
catalog is deliberately small and editable from the 自主编排 page: users can
add their own books or remove entries.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BookResource, ExamSubject, StudyWorkflow, User

# subject_type is a broad bucket used for grouping/filtering:
# math / english / cs / economics / psychology / law / journalism / management / education / custom
BOOK_CATALOG: list[dict] = [
    # ---- 计算机 / 408 ------------------------------------------------
    {"key": "book-cs-ds-yan", "title": "数据结构（C语言版）", "author": "严蔚敏、吴伟民", "publisher": "清华大学出版社", "subject_type": "cs", "major": "计算机科学与技术", "level": "基础", "tags": ["408", "数据结构", "考研"]},
    {"key": "book-cs-ds-wangdao", "title": "2026 数据结构考研复习指导", "author": "王道论坛", "publisher": "电子工业出版社", "subject_type": "cs", "major": "计算机科学与技术", "level": "强化", "tags": ["408", "数据结构", "刷题"]},
    {"key": "book-cs-co-tang", "title": "计算机组成原理（第2版）", "author": "唐朔飞", "publisher": "高等教育出版社", "subject_type": "cs", "major": "计算机科学与技术", "level": "基础", "tags": ["408", "计算机组成原理"]},
    {"key": "book-cs-os-tang", "title": "计算机操作系统（第4版）", "author": "汤小丹、梁红兵", "publisher": "西安电子科技大学出版社", "subject_type": "cs", "major": "计算机科学与技术", "level": "基础", "tags": ["408", "操作系统"]},
    {"key": "book-cs-net-xie", "title": "计算机网络（第8版）", "author": "谢希仁", "publisher": "电子工业出版社", "subject_type": "cs", "major": "计算机科学与技术", "level": "基础", "tags": ["408", "计算机网络"]},
    {"key": "book-cs-algo-clrs", "title": "算法导论（原书第3版）", "author": "Thomas H. Cormen 等", "publisher": "机械工业出版社", "subject_type": "cs", "major": "计算机科学与技术", "level": "进阶", "tags": ["算法", "数据结构", "理论"]},
    {"key": "book-cs-db-wang", "title": "数据库系统概论（第6版）", "author": "王珊、杜小勇", "publisher": "高等教育出版社", "subject_type": "cs", "major": "计算机科学与技术", "level": "基础", "tags": ["数据库", "专业课"]},
    {"key": "book-cs-ml-zhou", "title": "机器学习", "author": "周志华", "publisher": "清华大学出版社", "subject_type": "cs", "major": "人工智能", "level": "进阶", "tags": ["机器学习", "人工智能"]},
    # ---- 数学 --------------------------------------------------------
    {"key": "book-math-calc-tongji", "title": "高等数学（第七版）上册/下册", "author": "同济大学数学系", "publisher": "高等教育出版社", "subject_type": "math", "major": "数学一", "level": "基础", "tags": ["高数", "教材", "考研数学"]},
    {"key": "book-math-linalg-tongji", "title": "线性代数（第六版）", "author": "同济大学数学系", "publisher": "高等教育出版社", "subject_type": "math", "major": "数学一", "level": "基础", "tags": ["线代", "教材", "考研数学"]},
    {"key": "book-math-prob-zju", "title": "概率论与数理统计（第五版）", "author": "盛骤、谢式千", "publisher": "高等教育出版社", "subject_type": "math", "major": "数学一", "level": "基础", "tags": ["概率论", "教材", "考研数学"]},
    {"key": "book-math-review-liyongle", "title": "数学复习全书（提高篇）", "author": "李永乐团队", "publisher": "国家开放大学出版社", "subject_type": "math", "major": "数学一", "level": "强化", "tags": ["复习全书", "考研数学", "综合"]},
    {"key": "book-math-zhangyu1000", "title": "张宇考研数学1000题", "author": "张宇", "publisher": "北京理工大学出版社", "subject_type": "math", "major": "数学一", "level": "强化", "tags": ["习题", "刷题", "考研数学"]},
    {"key": "book-math-fenbi", "title": "考研数学历年真题全解", "author": "李艳芳", "publisher": "中国政法大学出版社", "subject_type": "math", "major": "数学一", "level": "冲刺", "tags": ["真题", "考研数学"]},
    # ---- 英语 --------------------------------------------------------
    {"key": "book-en-vocab-lianlian", "title": "恋练有词：考研英语词汇识记与应用大全", "author": "朱伟、唐迟", "publisher": "群言出版社", "subject_type": "english", "major": "英语一", "level": "基础", "tags": ["词汇", "考研英语"]},
    {"key": "book-en-grammar-tangchi", "title": "考研英语长难句的逻辑", "author": "唐迟", "publisher": "机械工业出版社", "subject_type": "english", "major": "英语一", "level": "强化", "tags": ["长难句", "阅读", "语法"]},
    {"key": "book-en-reading-huangpishu", "title": "考研英语黄皮书·历年真题解析", "author": "张剑", "publisher": "中国政法大学出版社", "subject_type": "english", "major": "英语一", "level": "冲刺", "tags": ["真题", "阅读", "考研英语"]},
    {"key": "book-en-writing-160", "title": "考研英语高分写作（160篇）", "author": "王江涛", "publisher": "群言出版社", "subject_type": "english", "major": "英语一", "level": "强化", "tags": ["写作", "作文", "模板"]},
    # ---- 经济 / 金融 -------------------------------------------------
    {"key": "book-econ-western-gao", "title": "西方经济学（宏观+微观）", "author": "高鸿业", "publisher": "中国人民大学出版社", "subject_type": "economics", "major": "经济学", "level": "基础", "tags": ["经济学", "考研", "教材"]},
    {"key": "book-econ-micro-pindyck", "title": "微观经济学（第8版）", "author": "平狄克、鲁宾费尔德", "publisher": "中国人民大学出版社", "subject_type": "economics", "major": "经济学", "level": "强化", "tags": ["微观经济学", "进阶"]},
    {"key": "book-econ-macro-mankiw", "title": "宏观经济学（第10版）", "author": "N. 格里高利·曼昆", "publisher": "中国人民大学出版社", "subject_type": "economics", "major": "经济学", "level": "强化", "tags": ["宏观经济学", "进阶"]},
    {"key": "book-fin-investments-bodie", "title": "投资学（第10版）", "author": "兹维·博迪 等", "publisher": "机械工业出版社", "subject_type": "economics", "major": "金融学", "level": "进阶", "tags": ["金融", "投资学"]},
    # ---- 心理学 ------------------------------------------------------
    {"key": "book-psy-general-peng", "title": "普通心理学（第5版）", "author": "彭聃龄", "publisher": "北京师范大学出版社", "subject_type": "psychology", "major": "心理学", "level": "基础", "tags": ["心理学", "教材"]},
    {"key": "book-psy-experiment-guo", "title": "实验心理学", "author": "郭秀艳", "publisher": "人民教育出版社", "subject_type": "psychology", "major": "心理学", "level": "强化", "tags": ["实验心理学", "方法"]},
    {"key": "book-psy-stats-zhang", "title": "现代心理与教育统计学", "author": "张厚粲", "publisher": "北京师范大学出版社", "subject_type": "psychology", "major": "心理学", "level": "强化", "tags": ["统计", "心理学"]},
    # ---- 法学 --------------------------------------------------------
    {"key": "book-law-jurisprudence-zhang", "title": "法理学（第五版）", "author": "张文显", "publisher": "高等教育出版社", "subject_type": "law", "major": "法学", "level": "基础", "tags": ["法理学", "考研"]},
    {"key": "book-law-civil-wang", "title": "民法（第九版）", "author": "王利明", "publisher": "中国人民大学出版社", "subject_type": "law", "major": "法学", "level": "基础", "tags": ["民法", "考研"]},
    {"key": "book-law-criminal-gao", "title": "刑法学（第十版）", "author": "高铭暄、马克昌", "publisher": "北京大学出版社", "subject_type": "law", "major": "法学", "level": "基础", "tags": ["刑法", "考研"]},
    # ---- 新闻传播 ----------------------------------------------------
    {"key": "book-jc-communication-guo", "title": "传播学教程（第二版）", "author": "郭庆光", "publisher": "中国人民大学出版社", "subject_type": "journalism", "major": "新闻传播学", "level": "基础", "tags": ["传播学", "考研"]},
    {"key": "book-jc-news-theory-li", "title": "新闻学概论（第七版）", "author": "李良荣", "publisher": "复旦大学出版社", "subject_type": "journalism", "major": "新闻传播学", "level": "基础", "tags": ["新闻学", "考研"]},
    # ---- 管理学 ------------------------------------------------------
    {"key": "book-mgmt-robbins", "title": "管理学（第15版）", "author": "斯蒂芬·罗宾斯", "publisher": "中国人民大学出版社", "subject_type": "management", "major": "管理学", "level": "基础", "tags": ["管理学", "考研"]},
    {"key": "book-mgmt-org-zhang", "title": "组织行为学（第5版）", "author": "张德", "publisher": "高等教育出版社", "subject_type": "management", "major": "管理学", "level": "强化", "tags": ["组织行为学", "管理"]},
    # ---- 教育学 / 历史 -----------------------------------------------
    {"key": "book-edu-foundation-12", "title": "教育学基础（第三版）", "author": "全国十二所重点师范大学", "publisher": "教育科学出版社", "subject_type": "education", "major": "教育学", "level": "基础", "tags": ["教育学", "考研"]},
    {"key": "book-history-china-zhang", "title": "中国史纲要（修订本）", "author": "翦伯赞", "publisher": "北京大学出版社", "subject_type": "history", "major": "历史学", "level": "基础", "tags": ["中国史", "考研"]},
    {"key": "book-history-world-wu", "title": "世界史·古代史编", "author": "吴于廑、齐世荣", "publisher": "高等教育出版社", "subject_type": "history", "major": "历史学", "level": "基础", "tags": ["世界史", "考研"]},
]

WORKFLOW_TEMPLATES: dict[str, list[dict]] = {
    "math_1": [
        {"name": "基础精讲", "focus": "教材概念 + 例题", "minutes": 90, "task_type": "learn"},
        {"name": "强化刷题", "focus": "分章节专项练习", "minutes": 90, "task_type": "practice"},
        {"name": "真题精练", "focus": "按年份整套限时", "minutes": 120, "task_type": "test"},
        {"name": "错题回滚", "focus": "沿前置知识最小修复", "minutes": 45, "task_type": "diagnose"},
        {"name": "模考冲刺", "focus": "全真模拟 + 时间分配", "minutes": 150, "task_type": "test"},
    ],
    "english_1": [
        {"name": "词汇积累", "focus": "词义 + 搭配 + 例句", "minutes": 60, "task_type": "memory"},
        {"name": "长难句与阅读", "focus": "句法拆解 + 题型训练", "minutes": 60, "task_type": "practice"},
        {"name": "翻译与写作", "focus": "模板 + 句型 + 批改", "minutes": 60, "task_type": "practice"},
        {"name": "真题套卷", "focus": "限时整套 + 精读复盘", "minutes": 120, "task_type": "test"},
        {"name": "作文背诵", "focus": "个人素材 + 默写", "minutes": 30, "task_type": "memory"},
    ],
    "default": [
        {"name": "教材通读", "focus": "建立章节框架", "minutes": 60, "task_type": "learn"},
        {"name": "章节练习", "focus": "基础题 + 变式题", "minutes": 60, "task_type": "practice"},
        {"name": "真题/案例", "focus": "按题型专项突破", "minutes": 90, "task_type": "practice"},
        {"name": "总结记忆", "focus": "知识图谱 + 背诵卡", "minutes": 30, "task_type": "memory"},
        {"name": "模拟检验", "focus": "按考试蓝图小测", "minutes": 90, "task_type": "test"},
    ],
}

DEFAULT_AUTONOMY = {
    "allow_agent_add_subjects": True,
    "allow_agent_search_books": True,
    "allow_agent_edit_workflow": True,
    "allow_agent_remove_subjects": False,
    "require_confirm_for_delete": True,
}


def seed_studio(db: Session, user_id: int, force: bool = False) -> dict:
    """Idempotently seed books, workflows and autonomy defaults for a user."""
    created_books = 0
    existing_keys = {
        key for (key,) in db.execute(select(BookResource.key).where(BookResource.user_id == user_id)).all()
    }
    for book in BOOK_CATALOG:
        if book["key"] in existing_keys and not force:
            continue
        if book["key"] in existing_keys:
            continue
        db.add(
            BookResource(
                user_id=user_id,
                key=book["key"],
                title=book["title"],
                author=book.get("author", ""),
                publisher=book.get("publisher", ""),
                subject_type=book.get("subject_type", "custom"),
                major=book.get("major", ""),
                level=book.get("level", "基础"),
                tags=book.get("tags", []),
                description=book.get("description", ""),
                source_url=book.get("source_url", ""),
                created_by="catalog",
            )
        )
        created_books += 1

    created_workflows = 0
    subjects = list(db.scalars(select(ExamSubject)))
    for subject in subjects:
        exists = db.scalar(
            select(StudyWorkflow).where(
                StudyWorkflow.user_id == user_id,
                StudyWorkflow.subject_id == subject.id,
                StudyWorkflow.source == "template",
            )
        )
        if exists:
            continue
        steps = WORKFLOW_TEMPLATES.get(subject.subject_type, WORKFLOW_TEMPLATES["default"])
        db.add(
            StudyWorkflow(
                user_id=user_id,
                subject_id=subject.id,
                name=f"{subject.name} · 标准工作流",
                description="按 基础 → 强化 → 真题 → 回滚 → 模考 编排，可在自主编排页修改",
                steps=[dict(step) for step in steps],
                status="active",
                source="template",
            )
        )
        created_workflows += 1

    user = db.get(User, user_id)
    if user is not None:
        profile = dict(user.profile or {})
        if "autonomy" not in profile:
            profile["autonomy"] = dict(DEFAULT_AUTONOMY)
            user.profile = profile

    db.commit()
    return {"books": created_books, "workflows": created_workflows, "subjects": len(subjects)}
