"""Pytest bootstrap: isolated SQLite database + seeded demo dataset."""

from __future__ import annotations

import os
import pathlib
import tempfile

TEST_DIR = pathlib.Path(__file__).resolve().parents[1] / "var" / "_test"
TEST_DIR.mkdir(parents=True, exist_ok=True)
for stale in TEST_DIR.glob("test.db*"):
    stale.unlink(missing_ok=True)
os.environ["EXAMOS_VAR_DIR"] = str(TEST_DIR)
os.environ["EXAMOS_DATABASE_URL"] = f"sqlite:///{(TEST_DIR / 'test.db').as_posix()}"
os.environ["EXAMOS_LLM_ENABLED"] = "false"

import pytest  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import engine, init_db, session_scope  # noqa: E402
from app.models import Base  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    init_db()
    yield
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture(scope="session")
def seeded():
    """Seed once per session; returns ids of the demo user and subjects."""
    from sqlalchemy import select

    from app.models import ExamSubject, User
    from app.seed.generator import seed_demo

    with session_scope() as db:
        result = seed_demo(db, force=True)
        user = db.scalar(select(User).order_by(User.id))
        math_subject = db.scalar(select(ExamSubject).where(ExamSubject.subject_type == "math_1"))
        english_subject = db.scalar(select(ExamSubject).where(ExamSubject.subject_type == "english_1"))
        return {
            "result": result,
            "user_id": user.id,
            "math_id": math_subject.id,
            "english_id": english_subject.id,
            "settings": settings,
        }


@pytest.fixture()
def db():
    with session_scope() as session:
        yield session


@pytest.fixture()
def math_question(seeded):
    from sqlalchemy import select

    from app.models import Question

    with session_scope() as session:
        question = session.scalar(
            select(Question)
            .where(Question.subject_id == seeded["math_id"], Question.question_type == "calc_single")
            .order_by(Question.id)
        )
        return {"id": question.id, "answer": question.answer, "content": question.content}
