from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

import config
from auth.models import Base

engine = create_engine(config.DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)


def init_db() -> None:
    import core.models  # noqa: F401  (registers Core Database tables on Base before create_all)
    Base.metadata.create_all(engine)
    # create_all only creates missing tables, it never alters existing ones — this project
    # has no Alembic, so new columns on already-existing tables need a guarded one-off ALTER.
    with engine.begin() as conn:
        conn.execute(text(
            "ALTER TABLE assignments ADD COLUMN IF NOT EXISTS feedback_mode "
            "VARCHAR NOT NULL DEFAULT 'test_cases'"
        ))
        conn.execute(text(
            "ALTER TABLE assignment_submissions ADD COLUMN IF NOT EXISTS ai_feedback "
            "TEXT NOT NULL DEFAULT ''"
        ))


@contextmanager
def get_session():
    session: Session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
