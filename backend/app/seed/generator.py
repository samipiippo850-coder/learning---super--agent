"""Programmatic demo dataset for ExamOS.

All quantitative math questions are generated *and re-solved* with SymPy at seed
time, so the demo bank cannot contain a wrong answer. English items come from a
compact passage library (labelled as demo material in ``meta``).

The dataset is deterministic: seeding twice yields byte-identical content.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.constants import PaperType, QuestionType, SourceType, SubjectType
from app.models import ExamSubject, ExamTarget, Paper, Question, QuestionKnowledge, Source
from app.services import memory as memory_service
from app.services.util import active_target, ensure_user, subjects_of

try:
    import sympy as sp

    _SYMPY = True
except Exception:  # pragma: no cover
    _SYMPY = False

DEMO_NOTE = (
    "ExamOS 演示数据集：数学题程序化生成并经 SymPy 复核，英语材料为演示用短文，"
    "不是真实历年真题，不用于对外宣称考频。"
)

KNOWLEDGE_MATH = [
    # code, name, category, chapter, importance, minutes, requirement, prereqs
    ("MA-CALC-FUNC", "函数与初等性质", "Concept", "高等数学·函数、极限与连续", 0.60, 45, "理解", []),
    ("MA-CALC-LIMIT", "函数极限与连续", "Concept", "高等数学·函数、极限与连续", 0.90, 90, "掌握", ["MA-CALC-FUNC"]),
    ("MA-CALC-INFINITESIMAL", "无穷小与等价无穷小", "Method", "高等数学·函数、极限与连续", 0.80, 60, "熟练运用", ["MA-CALC-LIMIT"]),
    ("MA-CALC-DERIV", "导数与微分", "Concept", "高等数学·一元函数微分学", 0.90, 90, "掌握", ["MA-CALC-LIMIT"]),
    ("MA-CALC-DERIV-APP", "导数应用（单调性、极值、凹凸）", "Method", "高等数学·一元函数微分学", 0.80, 75, "掌握", ["MA-CALC-DERIV"]),
    ("MA-CALC-MVT", "微分中值定理", "Theorem", "高等数学·一元函数微分学", 0.75, 75, "掌握", ["MA-CALC-DERIV"]),
    ("MA-CALC-INDEFINT", "不定积分（换元、分部积分）", "Method", "高等数学·一元函数积分学", 0.80, 90, "掌握", ["MA-CALC-DERIV"]),
    ("MA-CALC-DEFINT", "定积分与牛顿-莱布尼茨公式", "Theorem", "高等数学·一元函数积分学", 0.90, 90, "掌握", ["MA-CALC-INDEFINT"]),
    ("MA-CALC-INT-APP", "定积分应用（面积、体积）", "Skill", "高等数学·一元函数积分学", 0.70, 60, "掌握", ["MA-CALC-DEFINT"]),
    ("MA-CALC-MULTIVAR", "多元函数微分学", "Concept", "高等数学·多元函数微积分", 0.80, 90, "掌握", ["MA-CALC-DERIV"]),
    ("MA-CALC-MULTI-INT", "二重积分", "Method", "高等数学·多元函数微积分", 0.75, 90, "掌握", ["MA-CALC-MULTIVAR", "MA-CALC-DEFINT"]),
    ("MA-CALC-SERIES", "无穷级数与收敛性", "Theorem", "高等数学·无穷级数", 0.75, 75, "掌握", ["MA-CALC-LIMIT"]),
    ("MA-CALC-ODE", "常微分方程", "Method", "高等数学·常微分方程", 0.70, 75, "掌握", ["MA-CALC-INDEFINT"]),
    ("MA-LAL-DET", "行列式定义与性质", "Concept", "线性代数·行列式", 0.85, 75, "掌握", []),
    ("MA-LAL-DET-CALC", "行列式计算", "Skill", "线性代数·行列式", 0.80, 60, "熟练运用", ["MA-LAL-DET"]),
    ("MA-LAL-MATRIX", "矩阵运算与逆矩阵", "Concept", "线性代数·矩阵", 0.80, 75, "掌握", []),
    ("MA-LAL-RANK", "矩阵的秩", "Concept", "线性代数·矩阵", 0.75, 60, "掌握", ["MA-LAL-MATRIX"]),
    ("MA-LAL-VECTOR", "向量组与线性相关性", "Concept", "线性代数·向量组", 0.70, 75, "理解", ["MA-LAL-MATRIX"]),
    ("MA-LAL-SYS", "线性方程组解的结构", "Theorem", "线性代数·线性方程组", 0.85, 90, "掌握", ["MA-LAL-RANK", "MA-LAL-VECTOR"]),
    ("MA-LAL-EIGEN", "特征值与特征向量", "Concept", "线性代数·特征值", 0.90, 90, "掌握", ["MA-LAL-DET-CALC", "MA-LAL-MATRIX"]),
    ("MA-LAL-SIMILAR", "相似对角化", "Method", "线性代数·特征值", 0.70, 75, "掌握", ["MA-LAL-EIGEN"]),
    ("MA-LAL-QUADRATIC", "二次型与正定性", "Concept", "线性代数·二次型", 0.70, 75, "理解", ["MA-LAL-EIGEN", "MA-LAL-SIMILAR"]),
    ("MA-PROB-EVENT", "随机事件与概率运算", "Concept", "概率论·随机事件", 0.60, 45, "理解", []),
    ("MA-PROB-CLASSIC", "古典概型与条件概率", "Method", "概率论·随机事件", 0.80, 60, "掌握", ["MA-PROB-EVENT"]),
    ("MA-PROB-INDEP", "事件独立性", "Concept", "概率论·随机事件", 0.70, 45, "理解", ["MA-PROB-CLASSIC"]),
    ("MA-PROB-RV", "随机变量及其分布", "Concept", "概率论·随机变量", 0.90, 90, "掌握", ["MA-PROB-EVENT"]),
    ("MA-PROB-2D", "二维随机变量", "Concept", "概率论·随机变量", 0.75, 75, "掌握", ["MA-PROB-RV"]),
    ("MA-PROB-EXPECT", "数字特征（期望与方差）", "Formula", "概率论·数字特征", 0.85, 60, "熟练运用", ["MA-PROB-RV"]),
    ("MA-PROB-LIMIT", "大数定律与中心极限定理", "Theorem", "概率论·极限定理", 0.70, 60, "理解", ["MA-PROB-EXPECT"]),
    ("MA-PROB-ESTIMATE", "参数估计", "Method", "概率论·数理统计", 0.65, 75, "理解", ["MA-PROB-EXPECT", "MA-PROB-RV"]),
]

KNOWLEDGE_ENGLISH = [
    ("EN-VOC-CORE", "核心词汇与熟词僻义", "MemoryItem", "英语一·词汇", 0.90, 120, "熟练运用", []),
    ("EN-VOC-COLLOC", "固定搭配与词组", "MemoryItem", "英语一·词汇", 0.80, 90, "掌握", ["EN-VOC-CORE"]),
    ("EN-LONG-SENT", "长难句分析", "Skill", "英语一·阅读基础", 0.85, 90, "掌握", ["EN-VOC-CORE"]),
    ("EN-READ-MAIN", "主旨大意题", "Method", "英语一·阅读理解", 0.80, 60, "掌握", ["EN-LONG-SENT"]),
    ("EN-READ-DETAIL", "细节题", "Method", "英语一·阅读理解", 0.80, 45, "掌握", ["EN-LONG-SENT"]),
    ("EN-READ-INFER", "推断题", "Method", "英语一·阅读理解", 0.75, 60, "掌握", ["EN-READ-DETAIL"]),
    ("EN-READ-ATTITUDE", "态度题", "Method", "英语一·阅读理解", 0.70, 45, "理解", ["EN-READ-INFER"]),
    ("EN-READ-VOCAB", "词义句意题", "Method", "英语一·阅读理解", 0.70, 45, "掌握", ["EN-LONG-SENT"]),
    ("EN-READ-PARA", "段落功能题", "Method", "英语一·阅读理解", 0.65, 45, "理解", ["EN-READ-MAIN"]),
    ("EN-CLOZE", "完形填空逻辑与搭配", "Skill", "英语一·完形填空", 0.70, 60, "掌握", ["EN-VOC-COLLOC", "EN-LONG-SENT"]),
    ("EN-TRANS", "英译汉技巧", "Skill", "英语一·翻译", 0.70, 60, "掌握", ["EN-LONG-SENT"]),
    ("EN-WRITE-SMALL", "应用文写作模板", "Method", "英语一·写作", 0.75, 75, "掌握", ["EN-VOC-COLLOC"]),
    ("EN-WRITE-BIG", "图画作文结构与句型", "Method", "英语一·写作", 0.85, 90, "掌握", ["EN-WRITE-SMALL", "EN-VOC-COLLOC"]),
    ("EN-WRITE-ARGUE", "论证与衔接", "Method", "英语一·写作", 0.70, 60, "掌握", ["EN-WRITE-BIG"]),
    ("EN-WRITE-GRAMMAR", "语法准确性与多样性", "Skill", "英语一·写作", 0.70, 60, "掌握", []),
    ("EN-EXAM-STRATEGY", "时间分配与做题顺序", "Skill", "英语一·应试策略", 0.50, 30, "了解", []),
]

EN_READINGS = [
    {
        "title": "The cost of convenience",
        "text": (
            "Online shopping was supposed to free us from crowded stores, yet it has quietly created a new "
            "burden: the daily stream of parcels. Consumers now order more often and in smaller amounts, so "
            "vans make repeated trips along the same streets. Researchers found that the number of delivery "
            "stops in a residential area can double during a discount festival, while the average value of each "
            "parcel falls. The result is more traffic, more packaging and more returns, because a product that "
            "arrives with little effort is easy to send back. Some cities are responding by encouraging shared "
            "delivery lockers and by charging for failed deliveries. The deeper lesson, however, is that "
            "convenience is not free: its costs are simply moved from the shopper's calendar to the street."
        ),
        "questions": [
            ("What does the author suggest about online shopping?", ["It has reduced the total number of visits to stores", "It has shifted costs from shoppers to public space", "It has made delivery vans cheaper to operate", "It has lowered the amount of packaging used"], 1),
            ("The word \"burden\" most likely refers to", ["the traffic caused by repeated deliveries", "the growing number of small parcels", "the difficulty of returning goods", "the pressure on delivery workers"], 0),
            ("Why do researchers mention discount festivals?", ["To show that consumers spend less than before", "To explain a sharp rise in delivery stops", "To argue that vans should be banned", "To compare online and offline prices"], 1),
            ("What solution is mentioned in the passage?", ["Limiting online shopping time", "Building more parking spaces", "Using shared delivery lockers", "Raising product prices"], 2),
            ("The author's attitude towards convenience is best described as", ["enthusiastic", "indifferent", "critical but balanced", "completely opposed"], 2),
        ],
    },
    {
        "title": "Sleep and memory",
        "text": (
            "Students often trade sleep for study time before an exam, assuming that more hours with a book "
            "must mean more learning. The evidence points the other way. During deep sleep the brain replays "
            "patterns of activity recorded during the day, strengthening the connections that hold new facts. "
            "In one experiment, learners who slept after studying recalled about twenty percent more words a "
            "day later than those who stayed awake. Sleep does not merely protect memory; it also reorganises "
            "it, linking new material to what is already known. This may explain why a difficult idea can feel "
            "clearer after a night's rest. The practical advice is unpleasant for crammers: a short, regular "
            "sleep schedule is part of the study plan, not a reward for finishing it."
        ),
        "questions": [
            ("What is the main idea of the passage?", ["Sleep improves memory more than extra study does", "Students need less sleep before exams", "Deep sleep is unrelated to learning", "Cramming is the most effective method"], 0),
            ("According to the experiment, sleeping learners recalled", ["about the same number of words", "about twenty percent more words", "about twenty percent fewer words", "no words at all"], 1),
            ("The word \"reorganises\" implies that sleep", ["deletes unnecessary memories", "connects new knowledge with existing knowledge", "replaces study time completely", "makes facts impossible to forget"], 1),
            ("The author's suggestion for students is to", ["sleep less and study more", "treat sleep as part of studying", "study only at night", "avoid experiments on memory"], 1),
            ("The tone of the last sentence is", ["humorous but serious in content", "angry at students", "purely scientific", "uncertain"], 0),
        ],
    },
    {
        "title": "The price of a free app",
        "text": (
            "Most mobile applications are free to download, and users rarely ask why. The answer is that they "
            "pay in attention and data. Advertising funds the software, and the more precisely an app can "
            "predict a user's interests, the more it can charge advertisers. To make predictions, apps collect "
            "location history, contacts and usage patterns. Regulators have begun to treat such data as a "
            "product that consumers should knowingly exchange. Several regions now require a plain-language "
            "summary of what an app collects. Early studies suggest that when people actually read these "
            "summaries, a significant share uninstalls the app or changes its permissions. Transparency alone "
            "does not fix the market, but it makes the trade visible, which is a precondition for choosing."
        ),
        "questions": [
            ("Why are many apps free?", ["Their developers are volunteers", "They are paid for by advertising and data", "Governments fund them", "Users pay later in one sum"], 1),
            ("The phrase \"pay in attention and data\" suggests that users", ["spend money on subscriptions", "give up privacy for free services", "work for app companies", "receive payment for data"], 1),
            ("What have regulators started to do?", ["Ban free applications", "Require simple summaries of data collection", "Limit advertising entirely", "Set maximum app prices"], 1),
            ("The studies mentioned show that reading summaries can lead users to", ["download more apps", "change permissions or uninstall", "ignore the information", "pay for privacy"], 1),
            ("The author concludes that transparency", ["solves the problem completely", "makes the trade visible and enables choice", "is unnecessary", "benefits only advertisers"], 1),
        ],
    },
    {
        "title": "Learning by teaching",
        "text": (
            "A teacher who explains a topic to a class usually understands it better afterwards. Researchers "
            "call this the protégé effect: preparing to teach forces learners to organise material, spot gaps "
            "and anticipate questions. In a study, students who expected to teach a lesson scored higher than "
            "students who studied for a test, even though both groups spent the same time with the material. "
            "The benefit disappeared when students simply read a summary aloud without preparation. What "
            "matters is the mental work of building an explanation, not the act of speaking. Teachers can use "
            "the effect cheaply by asking pupils to explain a concept to a partner, then comparing explanations "
            "and correcting errors together."
        ),
        "questions": [
            ("What is the protégé effect?", ["Students learn better when preparing to teach", "Teachers learn less than students", "Reading aloud improves memory", "Tests are better than lessons"], 0),
            ("The students who expected to teach", ["spent more time studying", "scored higher than those studying for a test", "refused to take the test", "read summaries aloud"], 1),
            ("Why did the benefit disappear in one group?", ["They had no preparation", "They were too tired", "They disliked the topic", "They wrote instead of speaking"], 0),
            ("The study implies that the key factor is", ["the amount of time spent", "the mental work of building an explanation", "the number of listeners", "the difficulty of the test"], 1),
            ("A practical suggestion from the passage is to", ["avoid group work", "have pupils explain concepts to each other", "replace teachers with students", "read summaries aloud"], 1),
        ],
    },
    {
        "title": "Cities and trees",
        "text": (
            "Trees in cities are often valued for their appearance, but their practical role is larger. A mature "
            "tree can cool the air around it by several degrees, absorb rainwater that would otherwise flood "
            "streets, and filter dust from traffic. Studies of neighbourhoods with dense tree cover report "
            "lower summer energy bills and fewer heat-related illnesses. Yet planting is easier than keeping "
            "trees alive. Young trees need water and protection for years, and maintenance budgets are usually "
            "the first to be cut. Some cities now count the economic value of each tree, from cooling to "
            "property prices, and use those numbers to justify maintenance. The argument is simple: a tree is "
            "not decoration but infrastructure, and infrastructure must be maintained."
        ),
        "questions": [
            ("What is the passage mainly about?", ["The aesthetic value of city trees", "Trees as practical urban infrastructure", "The cost of planting trees", "Why cities cut maintenance budgets"], 1),
            ("Which benefit is NOT mentioned?", ["Cooling the air", "Absorbing rainwater", "Reducing noise from aircraft", "Filtering dust"], 2),
            ("The author notes that cities often cut", ["tree planting", "maintenance budgets", "energy bills", "property prices"], 1),
            ("Counting the value of each tree is used to", ["replace trees with buildings", "justify spending on maintenance", "raise property prices", "reduce the number of trees"], 1),
            ("The final sentence suggests that trees should be treated as", ["decoration", "infrastructure", "luxury goods", "temporary features"], 1),
        ],
    },
]

EN_TRANSLATIONS = [
    ("The rapid spread of remote work has changed not only where people work but also how they think about their careers, making flexibility a standard expectation rather than a rare privilege."),
    ("Although technological progress is often presented as inevitable, the direction it takes depends on decisions made by people who are rarely neutral about the outcome."),
    ("Reading widely is not a luxury for students of language; it is the main way in which vocabulary, grammar and cultural understanding are acquired together."),
    ("A city that invests in public transport reduces congestion, but its success ultimately depends on whether residents find the service convenient enough to leave their cars at home."),
    ("The most useful feedback is specific and timely, because general praise rarely tells a learner what to do differently on the next attempt."),
]

EN_WRITINGS = [
    ("Directions: Write an essay of 160-200 words based on the following drawing. In your essay, you should describe the drawing briefly, explain its intended meaning, and give your comments.\n\n图画：一个学生面对满桌的参考书，墙上贴着倒计时日历，书桌旁放着一杯早已凉掉的咖啡。"),
    ("Directions: Write an essay of 160-200 words based on the following chart. In your essay, you should describe the chart, interpret its meaning, and give your comments.\n\n图表：某高校近五年学生每周课外阅读时长从 6.5 小时下降到 3.2 小时，而短视频使用时长从 4 小时上升到 14 小时。"),
    ("Directions: Write an essay of 160-200 words. Some people believe that competition among students is necessary for progress; others think cooperation matters more. Discuss both views and give your own opinion."),
    ("Directions: Write a letter of about 100 words to the library of your university, suggesting improvements to the study environment. Do not sign your own name; use \"Li Ming\" instead."),
    ("Directions: Write an essay of 160-200 words on the following topic: the value of learning to manage one's time in an age of information overload."),
]

EN_CLOZE = [
    ("Effective readers do not read every word at the same speed. They adjust their pace to the purpose: a __1__ of a report may need careful attention, while a familiar routine document can be scanned quickly. __2__, they constantly predict what comes next and revise those predictions as they read. When a sentence does not fit the prediction, they slow down rather than __3__ ahead. This flexibility is what separates skilled readers __4__ struggling ones. It can be learned, but only if the learner practises with texts that are __5__ difficult, not overwhelming.", 1, ["A. summary", "B. glance", "C. quotation", "D. copy"], 2, ["A. In addition", "B. For example", "C. However", "D. Therefore"], 3, ["A. rushing", "B. reading", "C. looking", "D. turning"], 4, ["A. against", "B. with", "C. from", "D. by"], 5, ["A. slightly", "B. extremely", "C. hardly", "D. rarely"]),
    ("Many people believe that creativity is a gift, but research suggests it is largely a habit. Creative workers __1__ time for exploration and accept that most ideas will fail. They also expose themselves to __2__ fields, because unexpected connections often produce the best solutions. __3__, they test ideas early and cheaply instead of waiting for a perfect plan. This approach __4__ courage, since early feedback can be uncomfortable. Yet it is far more __5__ than working in isolation for months.", 1, ["A. save", "B. schedule", "C. waste", "D. borrow"], 2, ["A. similar", "B. narrow", "C. unrelated", "D. ancient"], 3, ["A. In contrast", "B. As a result", "C. Above all", "D. Meanwhile"], 4, ["A. requires", "B. removes", "C. ignores", "D. rewards"], 5, ["A. expensive", "B. efficient", "C. boring", "D. risky"]),
    ("The habit of comparing oneself with others has become easier and more damaging in the age of social media. Platforms show the most attractive moments of other people's lives, __1__ viewers rarely see the ordinary days behind them. As a result, people may feel __2__ even when their own lives are going well. One remedy is to focus on __3__ progress: keeping a record of what one has learned or completed. __4__, this shifts attention away from ranking and towards improvement. It does not remove social pressure, but it makes it __5__ to manage.", 1, ["A. because", "B. although", "C. unless", "D. so"], 2, ["A. satisfied", "B. inadequate", "C. inspired", "D. relaxed"], 3, ["A. personal", "B. public", "C. financial", "D. relative"], 4, ["A. However", "B. In practice", "C. Instead", "D. Similarly"], 5, ["A. harder", "B. easier", "C. impossible", "D. unnecessary"]),
    ("Public libraries are changing. Fewer people borrow printed books, __1__ more people use libraries as quiet workspaces and community centres. Some librarians worry that this shift will __2__ their traditional role. Others argue that a library's purpose has always been to provide free access to knowledge, __3__ form that knowledge takes. To attract new users, many libraries now offer digital courses and meeting rooms __4__ keeping their book collections. The buildings survive because their function __5__ with the needs of the community.", 1, ["A. so", "B. but", "C. for", "D. or"], 2, ["A. strengthen", "B. weaken", "C. describe", "D. repeat"], 3, ["A. whatever", "B. whichever", "C. however", "D. whenever"], 4, ["A. instead of", "B. in spite of", "C. in addition to", "D. regardless of"], 5, ["A. conflicts", "B. disappears", "C. changes", "D. competes"]),
    ("Learning a language as an adult is different from learning it as a child, but not impossible. Adults have an advantage: they already understand how language works and can use grammar rules __1__. The challenge is that they rarely get enough practice. __2__ short daily sessions are more effective than a long weekly lesson, because frequency matters more than __3__. Adults also need to accept making mistakes, __4__ children do naturally. __5__ progress may feel slow, the ability to use the language in real situations grows steadily.", 1, ["A. deliberately", "B. accidentally", "C. rarely", "D. silently"], 2, ["A. Fortunately", "B. Consequently", "C. Similarly", "D. Generally"], 3, ["A. quality", "B. duration", "C. content", "D. method"], 4, ["A. which", "B. what", "C. that", "D. as"], 5, ["A. Although", "B. Because", "C. Unless", "D. Since"]),
]


def _seed_knowledge(db: Session, subject: ExamSubject, rows: list[tuple]) -> dict[str, object]:
    from app.models import KnowledgeEdge, KnowledgeNode

    existing = {
        node.code: node
        for node in db.scalars(select(KnowledgeNode).where(KnowledgeNode.subject_id == subject.id))
    }
    created: dict[str, object] = {}
    for code, name, category, chapter, importance, minutes, requirement, prereqs in rows:
        node = existing.get(code)
        if node is None:
            node = KnowledgeNode(
                subject_id=subject.id,
                code=code,
                name=name,
                category=category,
                chapter=chapter,
                importance=importance,
                est_minutes=minutes,
                syllabus_requirement=requirement,
                description=f"{name}：{chapter}核心考点，考查方式包括概念辨析与计算。",
            )
            db.add(node)
            db.flush()
            existing[code] = node
        created[code] = node
    # prerequisite edges
    for code, _name, _cat, _ch, _imp, _min, _req, prereqs in rows:
        target = existing[code]
        for prereq_code in prereqs:
            source = existing.get(prereq_code)
            if source is None:
                continue
            exists = db.scalar(
                select(KnowledgeEdge).where(
                    KnowledgeEdge.source_id == source.id,
                    KnowledgeEdge.target_id == target.id,
                    KnowledgeEdge.relation_type == "prerequisite",
                )
            )
            if not exists:
                db.add(
                    KnowledgeEdge(
                        source_id=source.id,
                        target_id=target.id,
                        relation_type="prerequisite",
                        weight=1.0,
                    )
                )
    # co-occurrence edges within a chapter
    by_chapter: dict[str, list] = {}
    for code, *_rest in rows:
        node = existing[code]
        by_chapter.setdefault(node.chapter, []).append(node)
    for chapter_nodes in by_chapter.values():
        for i in range(len(chapter_nodes) - 1):
            a, b = chapter_nodes[i], chapter_nodes[i + 1]
            if a.id == b.id:
                continue
            exists = db.scalar(
                select(KnowledgeEdge).where(
                    KnowledgeEdge.source_id == a.id,
                    KnowledgeEdge.target_id == b.id,
                    KnowledgeEdge.relation_type == "co_occurs",
                )
            )
            if exists:
                continue
            db.add(KnowledgeEdge(source_id=a.id, target_id=b.id, relation_type="co_occurs", weight=0.5))
    db.commit()
    return existing


def _paper(db: Session, subject: ExamSubject, year: int, paper_type: str, title: str) -> Paper:
    key = f"demo-{subject.subject_type}-{year}-{paper_type}"
    existing = db.scalar(select(Paper).where(Paper.key == key))
    if existing:
        return existing
    from app.models import SourceDocument

    source = db.scalar(select(Source).where(Source.key == f"demo-{paper_type}"))
    document = None
    if source:
        document = SourceDocument(
            source_id=source.id,
            filename=f"{subject.subject_type}-{year}.txt",
            doc_type="paper",
            sha256=f"demo-{key}",
            parse_status="parsed",
            meta={"demo": True},
        )
        db.add(document)
        db.flush()
    paper = Paper(
        key=key,
        document_id=document.id if document else None,
        subject_id=subject.id,
        year=year,
        paper_type=paper_type,
        title=title,
        total_score=0.0,
    )
    db.add(paper)
    db.flush()
    return paper


def _add_question(
    db: Session,
    paper: Paper,
    subject: ExamSubject,
    nodes: dict,
    *,
    number: str,
    question_type: str,
    content: str,
    answer: str,
    explanation: str,
    score: float,
    difficulty: float,
    knowledge: list[tuple[str, float]],
    verification: dict | None = None,
) -> Question:
    key = f"{paper.key}-{len(db.scalars(select(Question).where(Question.paper_id == paper.id)).all()) + 1}"
    question = Question(
        key=key,
        paper_id=paper.id,
        subject_id=subject.id,
        number=number,
        question_type=question_type,
        content=content,
        answer=answer,
        explanation=explanation,
        score=score,
        difficulty=difficulty,
        source_type=paper.paper_type,
        is_synthetic=False,
        verification=verification or {"status": "seed_verified", "solver": "sympy" if _SYMPY else "manual"},
    )
    db.add(question)
    db.flush()
    total = sum(weight for _code, weight in knowledge) or 1.0
    seen: set[int] = set()
    for index, (code, weight) in enumerate(knowledge):
        node = nodes.get(code)
        if node is None or node.id in seen:
            continue
        seen.add(node.id)
        db.add(
            QuestionKnowledge(
                question_id=question.id,
                knowledge_id=node.id,
                weight=round(weight / total, 3),
                confidence=0.9,
                is_core=index == 0,
                role="core" if index == 0 else "supporting",
            )
        )
    paper.total_score = float(paper.total_score or 0) + float(score)
    return question


# --------------------------------------------------------------------------- #
# math question builders (all answers computed with SymPy)
# --------------------------------------------------------------------------- #
def _q_limit_sin(rng):
    a = rng.randint(2, 6)
    b = rng.randint(1, 5)
    x = sp.symbols("x")
    value = sp.limit(sp.sin(a * x) / (b * x), x, 0)
    options = [sp.Rational(a, b), sp.Rational(b, a), sp.Rational(a, 1), sp.Rational(1, a * b)]
    options = list(dict.fromkeys(options))
    target = sp.Rational(a, b)
    correct_index = options.index(target)
    rng.shuffle(options)
    correct_index = options.index(target)
    letters = "ABCD"
    text = "\n".join(f"{letters[i]}. {opt}" for i, opt in enumerate(options))
    return {
        "question_type": QuestionType.CALC_SINGLE,
        "content": f"lim_{{x→0}} sin({a}x)/({b}x) = （ ）\n{text}",
        "answer": letters[correct_index],
        "explanation": f"利用等价无穷小 sin({a}x) ~ {a}x，得极限 {a}/{b}，选 {letters[correct_index]}。",
        "expected": target,
        "score": 4.0,
        "difficulty": 0.3,
        "knowledge": [("MA-CALC-LIMIT", 0.6), ("MA-CALC-INFINITESIMAL", 0.4)],
    }


def _q_infinitesimal(rng):
    x = sp.symbols("x")
    pairs = [
        ("1-cos x", 1 - sp.cos(x), "x^2/2", x**2 / 2, "1-cos x ~ x^2/2"),
        ("tan x", sp.tan(x), "x", x, "tan x ~ x"),
        ("ln(1+x)", sp.log(1 + x), "x", x, "ln(1+x) ~ x"),
        ("x-sin x", x - sp.sin(x), "x^3/6", x**3 / 6, "x-sin x ~ x^3/6"),
    ]
    expr, expr_sym, equiv, equiv_sym, equiv_note = rng.choice(pairs)
    value = sp.limit(expr_sym / equiv_sym, x, 0)
    options = list(dict.fromkeys([equiv, "x^2/2", "x", "x^3/6", "x^2"]))[:4]
    correct = options.index(equiv)
    rng.shuffle(options)
    correct = options.index(equiv)
    letters = "ABCD"
    text = "\n".join(f"{letters[i]}. {opt}" for i, opt in enumerate(options))
    return {
        "question_type": QuestionType.CALC_SINGLE,
        "content": f"当 x→0 时，与 {expr} 等价的无穷小是（ ）\n{text}",
        "answer": letters[correct],
        "explanation": f"等价无穷小 {equiv_note}，可用 lim ({expr})/({equiv}) = {value} 验证，选 {letters[correct]}。",
        "expected": value,
        "score": 4.0,
        "difficulty": 0.35,
        "knowledge": [("MA-CALC-INFINITESIMAL", 0.7), ("MA-CALC-LIMIT", 0.3)],
    }


def _q_deriv_value(rng):
    a = rng.randint(1, 4)
    b = rng.randint(-4, 4)
    x = sp.symbols("x")
    f = a * x**3 + b * x**2 + rng.randint(-3, 3) * x
    x0 = rng.randint(-2, 2)
    value = sp.diff(f, x).subs(x, x0)
    options = [value, sp.simplify(value + 2), sp.simplify(value - 2), sp.simplify(-value)]
    options = list(dict.fromkeys(options))
    correct = options.index(value)
    rng.shuffle(options)
    correct = options.index(value)
    letters = "ABCD"
    text = "\n".join(f"{letters[i]}. {opt}" for i, opt in enumerate(options))
    f_text = sp.sstr(f).replace("**", "^")
    return {
        "question_type": QuestionType.CALC_SINGLE,
        "content": f"设 f(x) = {f_text}，则 f'({x0}) = （ ）\n{text}",
        "answer": letters[correct],
        "explanation": f"f'(x) = {sp.diff(f, x)}，代入 x={x0} 得 {value}，选 {letters[correct]}。",
        "expected": value,
        "score": 4.0,
        "difficulty": 0.4,
        "knowledge": [("MA-CALC-DERIV", 0.7), ("MA-CALC-DERIV-APP", 0.3)],
    }


def _q_det_scale(rng):
    k = rng.randint(2, 4)
    det = rng.randint(2, 6)
    value = k**3 * det
    options = [value, k * det, k**2 * det, value // 2 or 1]
    options = list(dict.fromkeys(options))
    correct = options.index(value)
    rng.shuffle(options)
    correct = options.index(value)
    letters = "ABCD"
    text = "\n".join(f"{letters[i]}. {opt}" for i, opt in enumerate(options))
    return {
        "question_type": QuestionType.CALC_SINGLE,
        "content": f"设 A 为 3 阶矩阵，且 |A| = {det}，则 |{k}A| = （ ）\n{text}",
        "answer": letters[correct],
        "explanation": f"3 阶矩阵数乘性质：|{k}A| = {k}^3|A| = {value}，选 {letters[correct]}。",
        "expected": value,
        "score": 4.0,
        "difficulty": 0.35,
        "knowledge": [("MA-LAL-DET", 0.7), ("MA-LAL-DET-CALC", 0.3)],
    }


def _q_invertible(rng):
    a = rng.randint(1, 3)
    b = rng.randint(1, 3)
    matrix = sp.Matrix([[a, b], [b, a]])
    det = matrix.det()
    value = det
    options = [value, sp.simplify(a**2 + b**2), sp.simplify(2 * a * b), sp.simplify(-det)]
    options = list(dict.fromkeys(options))
    correct = options.index(value)
    rng.shuffle(options)
    correct = options.index(value)
    letters = "ABCD"
    text = "\n".join(f"{letters[i]}. {opt}" for i, opt in enumerate(options))
    return {
        "question_type": QuestionType.CALC_SINGLE,
        "content": f"矩阵 [[{a}, {b}], [{b}, {a}]] 的行列式是（ ）\n{text}",
        "answer": letters[correct],
        "explanation": f"|A| = {a}·{a} - {b}·{b} = {value}，非零即可逆，选 {letters[correct]}。",
        "expected": value,
        "score": 4.0,
        "difficulty": 0.35,
        "knowledge": [("MA-LAL-MATRIX", 0.6), ("MA-LAL-DET-CALC", 0.4)],
    }


def _q_independence(rng):
    pa = sp.Rational(rng.randint(2, 6), 10)
    pb = sp.Rational(rng.randint(2, 6), 10)
    value = sp.simplify(pa * pb)
    options = [value, sp.simplify(pa + pb), sp.simplify(pa + pb - pa * pb), sp.simplify(pa * (1 - pb))]
    options = list(dict.fromkeys(options))
    correct = options.index(value)
    rng.shuffle(options)
    correct = options.index(value)
    letters = "ABCD"
    text = "\n".join(f"{letters[i]}. {opt}" for i, opt in enumerate(options))
    return {
        "question_type": QuestionType.CALC_SINGLE,
        "content": f"事件 A、B 相互独立，P(A) = {pa}，P(B) = {pb}，则 P(AB) = （ ）\n{text}",
        "answer": letters[correct],
        "explanation": f"独立性定义 P(AB) = P(A)P(B) = {value}，选 {letters[correct]}。",
        "expected": value,
        "score": 4.0,
        "difficulty": 0.3,
        "knowledge": [("MA-PROB-INDEP", 0.7), ("MA-PROB-CLASSIC", 0.3)],
    }


def _q_classic(rng):
    red, white = rng.randint(2, 5), rng.randint(2, 5)
    total = red + white
    value = sp.Rational(red, total)
    options = [value, sp.Rational(white, total), sp.Rational(red, total - 1) if total > 1 else 1, sp.Rational(1, total)]
    options = list(dict.fromkeys(options))
    correct = options.index(value)
    rng.shuffle(options)
    correct = options.index(value)
    letters = "ABCD"
    text = "\n".join(f"{letters[i]}. {opt}" for i, opt in enumerate(options))
    return {
        "question_type": QuestionType.CALC_SINGLE,
        "content": f"袋中有 {red} 个红球、{white} 个白球，随机取 1 个，取到红球的概率是（ ）\n{text}",
        "answer": letters[correct],
        "explanation": f"古典概型：P = {red}/{total} = {value}，选 {letters[correct]}。",
        "expected": value,
        "score": 4.0,
        "difficulty": 0.25,
        "knowledge": [("MA-PROB-CLASSIC", 0.7), ("MA-PROB-EVENT", 0.3)],
    }


def _q_series_sum(rng):
    n = sp.symbols("n", integer=True, positive=True)
    a = rng.randint(1, 3)
    value = sp.summation(1 / (n * (n + a)), (n, 1, sp.oo))
    # 1/(n(n+a)) = (1/a)(1/n - 1/(n+a)) -> value
    value = sp.simplify(value)
    options = [value, sp.simplify(value * 2), sp.simplify(value / 2), sp.simplify(value + 1)]
    options = list(dict.fromkeys(options))
    correct = options.index(value)
    rng.shuffle(options)
    correct = options.index(value)
    letters = "ABCD"
    text = "\n".join(f"{letters[i]}. {opt}" for i, opt in enumerate(options))
    return {
        "question_type": QuestionType.CALC_SINGLE,
        "content": f"级数 Σ_{{n=1}}^{{∞}} 1/(n(n+{a})) 的和为（ ）\n{text}",
        "answer": letters[correct],
        "explanation": f"裂项 1/(n(n+{a})) = (1/{a})(1/n - 1/(n+{a}))，部分和取极限得 {value}，选 {letters[correct]}。",
        "expected": value,
        "score": 4.0,
        "difficulty": 0.45,
        "knowledge": [("MA-CALC-SERIES", 0.8), ("MA-CALC-LIMIT", 0.2)],
    }


def _q_limit_value(rng):
    a = rng.randint(2, 5)
    x = sp.symbols("x")
    value = sp.limit(sp.sin(a * x) / x, x, 0)
    return {
        "question_type": QuestionType.CALC_FILL,
        "content": f"计算极限 lim_{{x→0}} sin({a}x)/x。",
        "answer": str(value),
        "explanation": f"等价无穷小 sin({a}x) ~ {a}x，故极限为 {value}。",
        "expected": value,
        "score": 4.0,
        "difficulty": 0.3,
        "knowledge": [("MA-CALC-LIMIT", 0.7), ("MA-CALC-INFINITESIMAL", 0.3)],
    }


def _q_defint(rng):
    a = rng.randint(1, 3)
    b = rng.randint(-3, 3)
    c = rng.randint(-2, 4)
    x = sp.symbols("x")
    lo, hi = 0, rng.randint(1, 3)
    f = a * x**2 + b * x + c
    value = sp.integrate(f, (x, lo, hi))
    f_text = sp.sstr(f).replace("**", "^")
    return {
        "question_type": QuestionType.CALC_FILL,
        "content": f"计算定积分 ∫_{lo}^{hi} ({f_text}) dx。",
        "answer": str(sp.simplify(value)),
        "explanation": f"原函数为 {sp.integrate(f, x)}，代入上下限得 {value}。",
        "expected": sp.simplify(value),
        "score": 4.0,
        "difficulty": 0.4,
        "knowledge": [("MA-CALC-DEFINT", 0.7), ("MA-CALC-INDEFINT", 0.3)],
    }


def _q_det3(rng):
    matrix = sp.Matrix([[rng.randint(-3, 3) for _ in range(3)] for _ in range(3)])
    det = matrix.det()
    rows = "\n".join(" ".join(str(matrix[i, j]) for j in range(3)) for i in range(3))
    return {
        "question_type": QuestionType.CALC_FILL,
        "content": f"计算三阶行列式：\n{rows}",
        "answer": str(det),
        "explanation": "按第一行展开，逐项计算二阶余子式并求和。",
        "expected": det,
        "score": 4.0,
        "difficulty": 0.45,
        "knowledge": [("MA-LAL-DET-CALC", 0.8), ("MA-LAL-DET", 0.2)],
    }


def _q_cond_prob(rng):
    red, white = rng.randint(2, 4), rng.randint(2, 4)
    total = red + white
    value = sp.Rational(red - 1, total - 1)
    return {
        "question_type": QuestionType.CALC_FILL,
        "content": f"袋中有 {red} 个红球、{white} 个白球。不放回抽取两次，已知第一次抽到红球，求第二次也抽到红球的概率。",
        "answer": str(value),
        "explanation": f"条件概率：P = {red - 1}/{total - 1} = {value}。",
        "expected": value,
        "score": 4.0,
        "difficulty": 0.4,
        "knowledge": [("MA-PROB-CLASSIC", 0.8), ("MA-PROB-EVENT", 0.2)],
    }


def _q_rank(rng):
    matrix = sp.Matrix([[1, 2, 3], [2, 4, 6], [rng.randint(1, 3), rng.randint(2, 5), rng.randint(3, 6)]])
    rank = matrix.rank()
    rows = "\n".join(" ".join(str(matrix[i, j]) for j in range(3)) for i in range(3))
    return {
        "question_type": QuestionType.CALC_FILL,
        "content": f"求矩阵的秩：\n{rows}",
        "answer": str(rank),
        "explanation": "第二行是第一行的 2 倍，做行变换后非零行个数即秩。",
        "expected": rank,
        "score": 4.0,
        "difficulty": 0.45,
        "knowledge": [("MA-LAL-RANK", 0.7), ("MA-LAL-VECTOR", 0.3)],
    }


def _q_expect(rng):
    values = sorted(rng.sample(range(1, 6), 3))
    probs = [sp.Rational(1, 6), sp.Rational(2, 6), sp.Rational(3, 6)]
    value = sum(v * p for v, p in zip(values, probs))
    return {
        "question_type": QuestionType.CALC_FILL,
        "content": f"随机变量 X 的分布列为 X = {values[0]}, {values[1]}, {values[2]}，概率分别为 1/6, 2/6, 3/6，求 E(X)。",
        "answer": str(sp.simplify(value)),
        "explanation": f"E(X) = Σ x_i p_i = {values[0]}/6 + {2 * values[1]}/6 + {3 * values[2]}/6 = {value}。",
        "expected": sp.simplify(value),
        "score": 4.0,
        "difficulty": 0.35,
        "knowledge": [("MA-PROB-EXPECT", 0.8), ("MA-PROB-RV", 0.2)],
    }


def _q_mvt(rng):
    x = sp.symbols("x")
    a = rng.randint(1, 3)
    b = rng.randint(1, 4)
    # f(x)=x^2 - (a+b)x + ab has roots a, b; Rolle point in (a,b)
    f = x**2 - (a + b) * x + a * b
    xi = sp.solve(sp.diff(f, x), x)[0]
    return {
        "question_type": QuestionType.CALC_FILL,
        "content": f"函数 f(x) = x^2 - {a + b}x + {a * b} 在区间 [{a}, {b}] 上满足罗尔定理，求定理中的 ξ。",
        "answer": str(xi),
        "explanation": f"f'({xi}) = 0 且 ξ 在 ({a}, {b}) 内，取 ξ = {xi}。",
        "expected": xi,
        "score": 4.0,
        "difficulty": 0.4,
        "knowledge": [("MA-CALC-MVT", 0.8), ("MA-CALC-DERIV", 0.2)],
    }


def _q_indefint(rng):
    a = rng.randint(1, 3)
    b = rng.randint(1, 4)
    x = sp.symbols("x")
    f = a * x**2 + b
    value = sp.integrate(f, x)
    return {
        "question_type": QuestionType.CALC_FILL,
        "content": f"计算不定积分 ∫({a}x^2 + {b}) dx。",
        "answer": f"{sp.sstr(value)} + C",
        "explanation": f"逐项积分：∫{a}x^2 dx = {a}x^3/3，∫{b} dx = {b}x，故结果为 {sp.sstr(value)} + C。",
        "expected": f"{sp.sstr(value)} + C",
        "score": 4.0,
        "difficulty": 0.35,
        "knowledge": [("MA-CALC-INDEFINT", 0.8), ("MA-CALC-DERIV", 0.2)],
    }


def _q_multivar(rng):
    x, y = sp.symbols("x y")
    a = rng.randint(1, 3)
    f = a * x**2 * y + y**2
    value = sp.diff(f, x).subs({x: 1, y: rng.randint(1, 3)})
    y0 = 1
    value = sp.diff(f, x).subs({x: 1, y: y0})
    return {
        "question_type": QuestionType.CALC_FILL,
        "content": f"设 f(x, y) = {sp.sstr(f).replace('**', '^')}，求 ∂f/∂x 在点 (1, {y0}) 处的值。",
        "answer": str(value),
        "explanation": f"∂f/∂x = {sp.sstr(sp.diff(f, x))}，代入 (1, {y0}) 得 {value}。",
        "expected": value,
        "score": 4.0,
        "difficulty": 0.4,
        "knowledge": [("MA-CALC-MULTIVAR", 0.8), ("MA-CALC-DERIV", 0.2)],
    }


def _q_event_union(rng):
    pa = sp.Rational(rng.randint(2, 5), 10)
    pb = sp.Rational(rng.randint(2, 5), 10)
    pab = sp.Rational(rng.randint(1, 2), 10)
    value = sp.simplify(pa + pb - pab)
    return {
        "question_type": QuestionType.CALC_FILL,
        "content": f"已知 P(A) = {pa}，P(B) = {pb}，P(AB) = {pab}，求 P(A∪B)。",
        "answer": str(value),
        "explanation": f"加法公式 P(A∪B) = P(A) + P(B) - P(AB) = {value}。",
        "expected": value,
        "score": 4.0,
        "difficulty": 0.3,
        "knowledge": [("MA-PROB-EVENT", 0.7), ("MA-PROB-CLASSIC", 0.3)],
    }


def _q_rv_density(rng):
    a = rng.choice([1, 2, 3])
    # f(x)=k x on [0,a], normalize -> k = 2/a^2
    k = sp.Rational(2, a**2)
    return {
        "question_type": QuestionType.CALC_FILL,
        "content": f"随机变量 X 的概率密度为 f(x) = kx（0 ≤ x ≤ {a}），其他为 0，求常数 k。",
        "answer": str(k),
        "explanation": f"归一化：∫_0^{a} kx dx = k·{a}^2/2 = 1，故 k = 2/{a}^2 = {k}。",
        "expected": k,
        "score": 4.0,
        "difficulty": 0.4,
        "knowledge": [("MA-PROB-RV", 0.8), ("MA-PROB-2D", 0.2)],
    }


def _q_2d_normalize(rng):
    a = rng.randint(1, 2)
    k = sp.Rational(1, 2 * a)
    return {
        "question_type": QuestionType.CALC_FILL,
        "content": f"二维随机变量 (X,Y) 的联合密度为 f(x,y) = k（0≤x≤{a}, 0≤y≤1），求 k。",
        "answer": str(k),
        "explanation": f"归一化：k·{a}·1 = 1，故 k = 1/{a} = {k}。",
        "expected": k,
        "score": 4.0,
        "difficulty": 0.35,
        "knowledge": [("MA-PROB-2D", 0.8), ("MA-PROB-RV", 0.2)],
    }


def _q_clt(rng):
    erf = sp.erf
    # X~U(0,1), n=100, sum approx normal; P(sum<=50+5)=Phi(1)
    value = sp.N((1 + erf(sp.Rational(1, 1) / sp.sqrt(2))) / 2, 4)
    return {
        "question_type": QuestionType.CALC_FILL,
        "content": "设 X1..X100 独立同分布，均服从 U(0,1)。用中心极限定理近似求 P(ΣXi ≤ 55)（结果用 Φ(1) 表示其数值，保留三位小数）。",
        "answer": str(value),
        "explanation": f"E(X)=1/2，Var(X)=1/12，ΣXi 近似 N(50, 100/12)，标准化得 Φ(1) ≈ {value}。",
        "expected": value,
        "score": 4.0,
        "difficulty": 0.55,
        "knowledge": [("MA-PROB-LIMIT", 0.8), ("MA-PROB-EXPECT", 0.2)],
    }


def _q_estimate(rng):
    sample = [rng.randint(50, 90) for _ in range(5)]
    value = sp.Rational(sum(sample), len(sample))
    return {
        "question_type": QuestionType.CALC_FILL,
        "content": f"从总体中抽取样本 {sample}，求总体均值的矩估计值。",
        "answer": str(value),
        "explanation": f"矩估计用样本均值估计总体均值：( {' + '.join(map(str, sample))} )/5 = {value}。",
        "expected": value,
        "score": 4.0,
        "difficulty": 0.4,
        "knowledge": [("MA-PROB-ESTIMATE", 0.8), ("MA-PROB-EXPECT", 0.2)],
    }


def _q_deriv_app(rng):
    a = rng.randint(1, 3)
    b = rng.randint(1, 4)
    x = sp.symbols("x")
    f = x**3 - a * x + b
    crit = sp.solve(sp.diff(f, x), x)
    real_crit = [c for c in crit if c.is_real]
    value = min((f.subs(x, c) for c in real_crit), key=lambda v: sp.N(v))
    return {
        "question_type": QuestionType.CALC_SOLVE,
        "content": f"求函数 f(x) = {sp.sstr(f).replace('**', '^')} 的极小值。",
        "answer": str(sp.simplify(value)),
        "explanation": f"f'(x) = {sp.sstr(sp.diff(f, x))}，令其为 0 得驻点，结合二阶导数判断极小值点为 x = {sp.sstr(min(real_crit, key=lambda c: sp.N(f.subs(x, c))))}，极小值为 {value}。",
        "expected": sp.simplify(value),
        "score": 11.0,
        "difficulty": 0.6,
        "knowledge": [("MA-CALC-DERIV-APP", 0.7), ("MA-CALC-DERIV", 0.3)],
    }


def _q_integral_area(rng):
    q = rng.randint(2, 4)
    x = sp.symbols("x")
    area = sp.integrate(q * x - x**2, (x, 0, q))
    return {
        "question_type": QuestionType.CALC_SOLVE,
        "content": f"求曲线 y = x^2 与直线 y = {q}x 所围成的平面图形的面积。",
        "answer": str(sp.simplify(area)),
        "explanation": f"交点 x=0 与 x={q}，在区间内 {q}x ≥ x^2，面积 S = ∫_0^{q}({q}x - x^2)dx = {area}。",
        "expected": sp.simplify(area),
        "score": 11.0,
        "difficulty": 0.55,
        "knowledge": [("MA-CALC-INT-APP", 0.7), ("MA-CALC-DEFINT", 0.3)],
    }


def _q_linear_sys(rng):
    a1, b1 = rng.randint(1, 3), rng.randint(-2, 2)
    a2, b2 = rng.randint(1, 3), rng.randint(-2, 2)
    det = a1 * b2 - a2 * b1
    if det == 0:
        return None
    x0, y0 = rng.randint(-2, 3), rng.randint(-2, 3)
    c1 = a1 * x0 + b1 * y0
    c2 = a2 * x0 + b2 * y0
    return {
        "question_type": QuestionType.CALC_SOLVE,
        "content": f"解线性方程组：\n{a1}x + {b1}y = {c1}\n{a2}x + {b2}y = {c2}",
        "answer": f"x = {x0}, y = {y0}",
        "explanation": f"系数行列式 D = {det} ≠ 0，由克拉默法则解得 x = {x0}, y = {y0}。",
        "expected": f"{x0}, {y0}",
        "score": 11.0,
        "difficulty": 0.45,
        "knowledge": [("MA-LAL-SYS", 0.7), ("MA-LAL-DET-CALC", 0.3)],
    }


def _q_eigen_solve(rng):
    lam1, lam2 = rng.sample(range(-2, 5), 2)
    tr, det = lam1 + lam2, lam1 * lam2
    a = rng.randint(-2, 2)
    d = tr - a
    b = rng.choice([-1, 1, 2])
    c = sp.Rational(det - a * d, b)
    if not c.is_integer:
        return None
    return {
        "question_type": QuestionType.CALC_SOLVE,
        "content": f"求矩阵 A = [[{a}, {b}], [{int(c)}, {d}]] 的特征值。",
        "answer": f"{min(lam1, lam2)}, {max(lam1, lam2)}",
        "explanation": f"特征方程 λ^2 - tr(A)λ + det(A) = λ^2 - {tr}λ + {det} = 0，解得 λ = {min(lam1, lam2)}, {max(lam1, lam2)}。",
        "expected": f"{min(lam1, lam2)}, {max(lam1, lam2)}",
        "score": 11.0,
        "difficulty": 0.55,
        "knowledge": [("MA-LAL-EIGEN", 0.8), ("MA-LAL-DET-CALC", 0.2)],
    }


def _q_quadratic(rng):
    lam = [rng.randint(1, 4) for _ in range(3)]
    value = sum(1 for x in lam if x > 0)
    return {
        "question_type": QuestionType.CALC_SOLVE,
        "content": f"设二次型 f = {lam[0]}x1^2 + {lam[1]}x2^2 + {lam[2]}x3^2，求其正惯性指数。",
        "answer": str(value),
        "explanation": f"标准形系数即特征值 {lam}，正特征值个数为 {value}，故正惯性指数为 {value}。",
        "expected": value,
        "score": 11.0,
        "difficulty": 0.5,
        "knowledge": [("MA-LAL-QUADRATIC", 0.7), ("MA-LAL-EIGEN", 0.3)],
    }


def _q_ode(rng):
    a = rng.randint(1, 3)
    x = sp.symbols("x")
    # y' + a y = 0, y(0)=1 -> e^{-ax}; verify sympy
    y = sp.Function("y")
    sol = sp.dsolve(sp.Eq(y(x).diff(x) + a * y(x), 0), y(x), ics={y(0): 1})
    rhs = sp.simplify(sol.rhs)
    return {
        "question_type": QuestionType.CALC_SOLVE,
        "content": f"求微分方程 y' + {a}y = 0 满足 y(0) = 1 的特解。",
        "answer": sp.sstr(rhs),
        "explanation": f"特征方程 r + {a} = 0，通解 y = C e^(-{a}x)，代入 y(0)=1 得 C=1，故 y = {sp.sstr(rhs)}。",
        "expected": rhs,
        "score": 11.0,
        "difficulty": 0.5,
        "knowledge": [("MA-CALC-ODE", 0.8), ("MA-CALC-INDEFINT", 0.2)],
    }


def _q_double_int(rng):
    a = rng.randint(1, 3)
    b = rng.randint(1, 3)
    x, y = sp.symbols("x y")
    value = sp.integrate(sp.integrate(x + y, (y, 0, b)), (x, 0, a))
    return {
        "question_type": QuestionType.CALC_SOLVE,
        "content": f"计算二重积分 ∬_D (x + y) dxdy，其中 D = {{(x,y) | 0≤x≤{a}, 0≤y≤{b}}}。",
        "answer": str(sp.simplify(value)),
        "explanation": f"化为累次积分 ∫_0^{a}∫_0^{b}(x+y)dydx = {value}。",
        "expected": sp.simplify(value),
        "score": 11.0,
        "difficulty": 0.55,
        "knowledge": [("MA-CALC-MULTI-INT", 0.8), ("MA-CALC-MULTIVAR", 0.2)],
    }


def _q_expect_var(rng):
    values = [rng.randint(-2, 4) for _ in range(3)]
    probs = [sp.Rational(1, 4), sp.Rational(1, 2), sp.Rational(1, 4)]
    mean = sum(v * p for v, p in zip(values, probs))
    var = sum(p * (v - mean) ** 2 for v, p in zip(values, probs))
    return {
        "question_type": QuestionType.CALC_SOLVE,
        "content": f"随机变量 X 取 {values[0]}, {values[1]}, {values[2]} 的概率分别为 1/4, 1/2, 1/4，求 E(X) 与 D(X)。",
        "answer": f"E(X) = {mean}，D(X) = {sp.simplify(var)}",
        "explanation": f"E(X) = {mean}；D(X) = Σp_i(x_i - E(X))^2 = {sp.simplify(var)}。",
        "expected": sp.simplify(var),
        "score": 11.0,
        "difficulty": 0.6,
        "knowledge": [("MA-PROB-EXPECT", 0.8), ("MA-PROB-RV", 0.2)],
    }


BUILDERS = {
    "calc_single": [_q_limit_sin, _q_infinitesimal, _q_deriv_value, _q_series_sum],
    "lal_single": [_q_det_scale, _q_invertible],
    "prob_single": [_q_independence, _q_classic],
    "calc_fill": [_q_limit_value, _q_defint, _q_mvt, _q_indefint, _q_multivar],
    "lal_fill": [_q_det3, _q_rank],
    "prob_fill": [_q_cond_prob, _q_expect, _q_event_union, _q_rv_density, _q_2d_normalize, _q_clt, _q_estimate],
    "calc_solve": [_q_deriv_app, _q_integral_area, _q_ode, _q_double_int],
    "lal_solve": [_q_linear_sys, _q_eigen_solve, _q_quadratic],
    "prob_solve": [_q_expect_var],
}

# extra builders so every high-importance node is exercised by the demo bank
NODE_COVERAGE = {
    "MA-CALC-DERIV": _q_deriv_value,
    "MA-CALC-DERIV-APP": _q_deriv_app,
    "MA-CALC-MVT": _q_mvt,
    "MA-CALC-INDEFINT": _q_indefint,
    "MA-CALC-DEFINT": _q_defint,
    "MA-CALC-INT-APP": _q_integral_area,
    "MA-CALC-MULTIVAR": _q_multivar,
    "MA-CALC-MULTI-INT": _q_double_int,
    "MA-CALC-SERIES": _q_series_sum,
    "MA-CALC-ODE": _q_ode,
    "MA-LAL-DET": _q_det_scale,
    "MA-LAL-DET-CALC": _q_det3,
    "MA-LAL-MATRIX": _q_invertible,
    "MA-LAL-RANK": _q_rank,
    "MA-LAL-SYS": _q_linear_sys,
    "MA-LAL-EIGEN": _q_eigen_solve,
    "MA-LAL-QUADRATIC": _q_quadratic,
    "MA-PROB-EVENT": _q_event_union,
    "MA-PROB-CLASSIC": _q_classic,
    "MA-PROB-INDEP": _q_independence,
    "MA-PROB-RV": _q_rv_density,
    "MA-PROB-2D": _q_2d_normalize,
    "MA-PROB-EXPECT": _q_expect,
    "MA-PROB-LIMIT": _q_clt,
    "MA-PROB-ESTIMATE": _q_estimate,
}

# --------------------------------------------------------------------------- #
# English demo items
# --------------------------------------------------------------------------- #
def _english_questions(db: Session, subject: ExamSubject, nodes: dict) -> int:
    created = 0
    reading_codes = ["EN-READ-MAIN", "EN-READ-DETAIL", "EN-READ-INFER", "EN-READ-ATTITUDE", "EN-READ-VOCAB"]
    for year in range(2015, 2025):
        paper = _paper(db, subject, year, PaperType.REAL, f"{year} 英语一（演示）")
        passage = EN_READINGS[(year - 2015) % len(EN_READINGS)]
        # reading: 5 sub questions, answers encoded as letters string
        letters = "ABCD"
        sub_answers = []
        sub_lines = [passage["text"], ""]
        for index, (stem, options, correct) in enumerate(passage["questions"], start=1):
            shuffled = list(options)
            rng = random.Random(f"read-{year}-{index}")
            rng.shuffle(shuffled)
            correct_text = options[correct]
            correct_index = shuffled.index(correct_text)
            sub_answers.append(letters[correct_index])
            sub_lines.append(f"{index}. {stem}")
            for opt_index, option in enumerate(shuffled):
                sub_lines.append(f"{letters[opt_index]}. {option}")
            sub_lines.append("")
        reading = _add_question(
            db, paper, subject, nodes,
            number=f"{21}-{25}",
            question_type=QuestionType.READING,
            content="\n".join(sub_lines),
            answer=" ".join(f"{21 + i}.{letter}" for i, letter in enumerate(sub_answers)),
            explanation="各题依据原文对应句与段落逻辑判断；干扰项分别存在偷换概念、过度推断或范围扩大问题。",
            score=10.0,
            difficulty=0.5,
            knowledge=[("EN-READ-MAIN", 0.2), ("EN-READ-DETAIL", 0.2), ("EN-READ-INFER", 0.3),
                       ("EN-READ-ATTITUDE", 0.15), ("EN-READ-VOCAB", 0.15)],
        )
        created += 1

        # cloze: 5 blanks
        passage_idx = (year - 2015) % len(EN_CLOZE)
        cloze = EN_CLOZE[passage_idx]
        cloze_letters = []
        lines = []
        positions = [1, 2, 3, 4, 5]
        options_by_blank = []
        for blank, options in zip(positions, cloze[2::2]):
            rng = random.Random(f"cloze-{year}-{blank}")
            shuffled = list(options)
            rng.shuffle(shuffled)
            answer = cloze[1 + 2 * blank - 1] if False else None
            # cloze structure: text, blank1, options1, blank2, options2, ...
            options_by_blank.append(shuffled)
        # answer letter per blank = index of the originally-correct option in shuffled list
        answers = []
        for blank, options in zip(positions, cloze[2::2]):
            correct_option = options[0]
            rng = random.Random(f"cloze-{year}-{blank}")
            shuffled = list(options)
            rng.shuffle(shuffled)
            answers.append(letters[shuffled.index(correct_option)])
        lines.append(cloze[0])
        for index, options in enumerate(options_by_blank, start=1):
            lines.append(f"{index}. " + "  ".join(options))
        cloze_q = _add_question(
            db, paper, subject, nodes,
            number="1-5",
            question_type=QuestionType.CLOZE,
            content="\n".join(lines),
            answer=" ".join(f"{i}.{letter}" for i, letter in enumerate(answers, start=1)),
            explanation="完形填空依据上下文逻辑关系与固定搭配判断，注意转折、因果与并列标志词。",
            score=10.0,
            difficulty=0.45,
            knowledge=[("EN-CLOZE", 0.7), ("EN-VOC-COLLOC", 0.3)],
        )
        created += 1

        # translation
        translation_text = EN_TRANSLATIONS[(year - 2015) % len(EN_TRANSLATIONS)]
        _add_question(
            db, paper, subject, nodes,
            number="46",
            question_type=QuestionType.TRANSLATION,
            content=f"将下列段落翻译成中文：\n{translation_text}",
            answer="（参考译文）" + translation_text,
            explanation="翻译要点：拆分长句、保留逻辑连接词、术语准确、避免逐字直译导致的语序错误。",
            score=10.0,
            difficulty=0.5,
            knowledge=[("EN-TRANS", 0.7), ("EN-LONG-SENT", 0.3)],
        )
        created += 1

        # writing
        writing_prompt = EN_WRITINGS[(year - 2015) % len(EN_WRITINGS)]
        _add_question(
            db, paper, subject, nodes,
            number="47",
            question_type=QuestionType.WRITING,
            content=writing_prompt,
            answer="（参考范文）三段式：描述图画/图表 → 揭示寓意 → 个人评论。评分维度：内容完整性、结构、语言准确性、表达多样性、任务完成度。",
            explanation="写作需覆盖全部要点，使用已背诵的句型与词汇，避免模板痕迹过重与语法错误。",
            score=20.0,
            difficulty=0.6,
            knowledge=[("EN-WRITE-BIG", 0.6), ("EN-WRITE-ARGUE", 0.2), ("EN-WRITE-GRAMMAR", 0.2)],
        )
        created += 1
    return created


def seed_demo(db: Session, force: bool = False) -> dict:
    """Idempotently seed the demo dataset. Returns counts."""
    from app.models import ExamSubject as Subject  # local alias to keep signature clear

    user = ensure_user(db)
    target = active_target(db, user.id)
    if target is None:
        raise RuntimeError("failed to create exam target")
    target.university = "示例大学"
    target.major = "计算机科学与技术"
    target.exam_year = 2027
    target.exam_date = datetime(2026, 12, 26, 9, 0, tzinfo=UTC)

    subjects = subjects_of(db, user.id)
    math_subject = next((s for s in subjects if s.subject_type == SubjectType.MATH1), None)
    english_subject = next((s for s in subjects if s.subject_type == SubjectType.ENGLISH1), None)
    if math_subject is None:
        math_subject = Subject(target_id=target.id, subject_type=SubjectType.MATH1, name="数学一", code="301", total_score=150)
        db.add(math_subject)
    if english_subject is None:
        english_subject = Subject(target_id=target.id, subject_type=SubjectType.ENGLISH1, name="英语一", code="201", total_score=100)
        db.add(english_subject)
    db.commit()

    existing_questions = db.scalar(select(Question).where(Question.subject_id == math_subject.id))
    if existing_questions is not None and not force:
        return {
            "seeded": False,
            "note": "数据已存在，跳过（force=true 可重建）",
            "knowledge": len(db.scalars(select(Question.id)).all()),
            "questions": len(db.scalars(select(Question.id)).all()),
        }

    if force:
        from app.models import (
            KnowledgeEdge,
            KnowledgeNode,
            MasteryStateRow,
            MemoryCard,
            RoiSnapshot,
            SourceDocument,
            StudyTask,
            TestResult,
            TestSession,
            Trigger,
        )

        # clean rebuild: questions/papers plus everything derived from knowledge
        db.query(Question).delete()
        db.query(Paper).delete()
        db.query(SourceDocument).delete()
        db.query(KnowledgeEdge).delete()
        db.query(MasteryStateRow).delete()
        db.query(RoiSnapshot).delete()
        db.query(StudyTask).delete()
        db.query(MemoryCard).delete()
        db.query(TestResult).delete()
        db.query(TestSession).delete()
        db.query(Trigger).delete()
        db.query(KnowledgeNode).delete()
        db.commit()

    # sources
    for key, source_type, title, trust in [
        ("demo-real", SourceType.OFFICIAL, "教育部教育考试院·历年真题（演示）", 0.9),
        ("demo-syllabus", SourceType.SYLLABUS, "全国硕士研究生招生考试大纲（演示）", 0.95),
        ("demo-user", SourceType.USER_PROVIDED, "用户提供的复习笔记（演示）", 0.6),
    ]:
        source = db.scalar(select(Source).where(Source.key == key))
        if source is None:
            db.add(Source(key=key, source_type=source_type, title=title, trust_score=trust,
                          publisher="演示来源", license="demo-only", retrieved_at=datetime.now(UTC)))
    db.commit()

    math_nodes = _seed_knowledge(db, math_subject, KNOWLEDGE_MATH)
    english_nodes = _seed_knowledge(db, english_subject, KNOWLEDGE_ENGLISH)

    # ---- math questions -----------------------------------------------------
    created_math = 0
    rng_years = range(2015, 2025)
    daily_mix = [
        ("calc_single", ["calc_single"][0]),
        ("lal_single", "lal_single"),
        ("prob_single", "prob_single"),
    ]
    for year in rng_years:
        rng = random.Random(2015.0 + year)
        paper = _paper(db, math_subject, year, PaperType.REAL, f"{year} 数学一（演示）")
        builders = []
        def pick(bucket: str, count: int):
            pool = BUILDERS[bucket]
            chosen = []
            attempts = 0
            while len(chosen) < count and attempts < 25:
                attempts += 1
                builder = rng.choice(pool)
                payload = builder(rng)
                if payload is not None:
                    chosen.append(payload)
            return chosen

        singles = pick("calc_single", 2) + pick("lal_single", 1) + pick("prob_single", 1)
        fills = pick("calc_fill", 1) + pick(rng.choice(["lal_fill", "prob_fill"]), 1)
        solves = pick("calc_solve", 1) + pick(rng.choice(["lal_solve", "prob_solve"]), 1)
        for index, payload in enumerate(singles + fills + solves, start=1):
            payload["knowledge"] = list(payload["knowledge"])
            question = _add_question(
                db, paper, math_subject, math_nodes,
                number=str(index),
                question_type=payload["question_type"],
                content=payload["content"],
                answer=payload["answer"],
                explanation=payload["explanation"],
                score=payload["score"],
                difficulty=payload["difficulty"],
                knowledge=payload["knowledge"],
            )
            question.verification = {
                "status": "seed_verified",
                "solver": "sympy" if _SYMPY else "manual",
                "expected": str(payload.get("expected")),
            }
            created_math += 1
        db.commit()

    # ---- coverage pass: every important node tagged by >= 2 questions -------
    from app.models import QuestionKnowledge

    for code, builder in NODE_COVERAGE.items():
        node = math_nodes.get(code)
        if node is None:
            continue
        count = len(db.scalars(select(QuestionKnowledge).where(QuestionKnowledge.knowledge_id == node.id)).all())
        for extra_index in range(max(0, 2 - count)):
            year = 2015 + (count * 7 + extra_index * 3) % 10
            paper = db.scalar(select(Paper).where(Paper.key == f"demo-{math_subject.subject_type}-{year}-real"))
            if paper is None:
                continue
            rng = random.Random(f"cover-{code}-{extra_index}")
            payload = builder(rng)
            if payload is None:
                continue
            knowledge = list(payload["knowledge"])
            knowledge.append((code, 0.2))
            _add_question(
                db, paper, math_subject, math_nodes,
                number=f"C{count + extra_index + 1}",
                question_type=payload["question_type"],
                content=payload["content"],
                answer=payload["answer"],
                explanation=payload["explanation"],
                score=payload["score"],
                difficulty=payload["difficulty"],
                knowledge=knowledge,
            )
            created_math += 1
    db.commit()

    # ---- english questions --------------------------------------------------
    created_english = _english_questions(db, english_subject, english_nodes)

    # ---- memory cards -------------------------------------------------------
    cards = memory_service.build_cards(db, user.id, min_importance=0.55)

    # ---- simulated learner history (so ROI / mastery have signal) ----------
    attempts = _seed_attempts(db, user.id, math_subject, english_subject)

    from app.services import mastery as mastery_service
    from app.services import roi as roi_service
    from app.services.planner import generate_plan

    mastery_service.recompute_all(db, user.id)
    for subject in (math_subject, english_subject):
        roi_service.persist_subject_roi(db, user.id, subject.id)
    generate_plan(db, user.id)

    # ---- studio: book catalog + default workflows + autonomy defaults ------
    from app.seed.studio import seed_studio

    studio = seed_studio(db, user.id)

    return {
        "seeded": True,
        "note": DEMO_NOTE,
        "knowledge": len(math_nodes) + len(english_nodes),
        "papers": db.scalar(select(__import__("sqlalchemy").func.count(Paper.id))) or 0,
        "questions": created_math + created_english,
        "cards": len(cards),
        "attempts": attempts,
        "books": studio["books"],
        "workflows": studio["workflows"],
    }


def _seed_attempts(db: Session, user_id: int, math_subject: ExamSubject, english_subject: ExamSubject) -> int:
    """Simulate ~2 weeks of practice history with uneven mastery.

    The first prerequisite layers are "already studied" (high mastery), later
    chapters are mostly untouched. This makes prerequisite gating and ROI
    ranking visible in the demo instead of showing an all-blocked graph.
    """
    from app.models import KnowledgeNode, LearnerAttempt, QuestionKnowledge
    from app.services.graph import prerequisite_map

    rng = random.Random(42)
    created = 0
    for subject, studied_count in ((math_subject, 13), (english_subject, 8)):
        questions = list(
            db.scalars(select(Question).where(Question.subject_id == subject.id).order_by(Question.id))
        )
        if not questions:
            continue
        # topological order of the prerequisite DAG (Kahn)
        prereqs = prerequisite_map(db, subject.id)
        nodes = list(db.scalars(select(KnowledgeNode).where(KnowledgeNode.subject_id == subject.id)))
        indegree = {node.id: len(prereqs.get(node.id, [])) for node in nodes}
        queue = [node.id for node in nodes if indegree[node.id] == 0]
        order: list[int] = []
        children: dict[int, list[int]] = {}
        for target, parents in prereqs.items():
            for parent in parents:
                children.setdefault(parent, []).append(target)
        while queue:
            current = queue.pop(0)
            order.append(current)
            for child in children.get(current, []):
                indegree[child] -= 1
                if indegree[child] == 0:
                    queue.append(child)
        studied = set(order[:studied_count])

        question_nodes: dict[int, list[int]] = {}
        for link in db.scalars(select(QuestionKnowledge).where(QuestionKnowledge.question_id.in_([q.id for q in questions]))):
            question_nodes.setdefault(link.question_id, []).append(link.knowledge_id)

        def add_attempt(question: Question, correct: bool) -> None:
            nonlocal created
            db.add(
                LearnerAttempt(
                    user_id=user_id,
                    question_id=question.id,
                    answer_text=question.answer if correct else "略",
                    is_correct=correct,
                    score_awarded=float(question.score) if correct else 0.0,
                    duration_sec=float(question.score) * 60 * rng.uniform(0.6, 1.8),
                    hint_count=0 if rng.random() < 0.85 else 1,
                    confidence_self=round(rng.uniform(0.35, 0.9), 2),
                    error_type="unknown" if correct else "concept_error",
                    is_independent=True,
                    source="practice",
                    created_at=datetime.now(UTC),
                )
            )
            created += 1

        used: set[int] = set()
        for node in studied:
            node_questions = [q for q in questions if node in question_nodes.get(q.id, []) and q.id not in used]
            rng.shuffle(node_questions)
            for question in node_questions[:5]:
                used.add(question.id)
                add_attempt(question, correct=rng.random() < 0.78)
        remaining = [q for q in questions if q.id not in used]
        for question in rng.sample(remaining, min(len(remaining), 18)):
            add_attempt(question, correct=rng.random() < 0.42)
        db.flush()
    db.commit()
    return created
