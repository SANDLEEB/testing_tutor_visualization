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
        conn.execute(text(
            "ALTER TABLE assignments ADD COLUMN IF NOT EXISTS animated_feedback "
            "BOOLEAN NOT NULL DEFAULT true"
        ))
        conn.execute(text(
            "ALTER TABLE assignment_submissions ADD COLUMN IF NOT EXISTS method_coverage_percent "
            "DOUBLE PRECISION"
        ))
        conn.execute(text(
            "ALTER TABLE assignment_submissions ADD COLUMN IF NOT EXISTS condition_coverage_percent "
            "DOUBLE PRECISION"
        ))
        conn.execute(text(
            "ALTER TABLE assignment_submissions ADD COLUMN IF NOT EXISTS duration_seconds INTEGER"
        ))
        # student_token/submission_token (core/anonymize.py) are hashed, not literal-defaultable —
        # add nullable, backfill every existing row in Python (so the backfill uses the exact
        # same HMAC as new writes), then enforce NOT NULL / uniqueness once nothing is null.
        conn.execute(text(
            "ALTER TABLE assignment_submissions ADD COLUMN IF NOT EXISTS student_token VARCHAR"
        ))
        conn.execute(text(
            "ALTER TABLE assignment_submissions ADD COLUMN IF NOT EXISTS submission_token VARCHAR"
        ))
        rows_needing_tokens = conn.execute(text(
            "SELECT id, student_id FROM assignment_submissions "
            "WHERE student_token IS NULL OR submission_token IS NULL"
        )).fetchall()
        if rows_needing_tokens:
            from core.anonymize import hash_student_id, hash_submission_id  # local: avoids import-order issues
            for row in rows_needing_tokens:
                conn.execute(
                    text(
                        "UPDATE assignment_submissions SET student_token = :st, submission_token = :subt "
                        "WHERE id = :id"
                    ),
                    {'st': hash_student_id(row.student_id), 'subt': hash_submission_id(row.id), 'id': row.id},
                )
        conn.execute(text(
            "ALTER TABLE assignment_submissions ALTER COLUMN student_token SET NOT NULL"
        ))
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_assignment_submissions_submission_token "
            "ON assignment_submissions (submission_token)"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_assignment_submissions_student_token "
            "ON assignment_submissions (student_token)"
        ))
        conn.execute(text(
            "ALTER TABLE enrollments ADD COLUMN IF NOT EXISTS section VARCHAR NOT NULL DEFAULT ''"
        ))
        conn.execute(text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS cwid VARCHAR NOT NULL DEFAULT ''"
        ))
        conn.execute(text(
            "ALTER TABLE assignments ADD COLUMN IF NOT EXISTS due_at TIMESTAMPTZ"
        ))
        # AccessScope — who beyond course membership can see a published Assignment/Question
        # (pages/instructor_assignments_page.py, pages/instructor_practice_page.py). 'course'
        # matches every prior behavior, so nothing already published narrows on upgrade.
        for _table in ('assignments', 'questions'):
            conn.execute(text(
                f"ALTER TABLE {_table} ADD COLUMN IF NOT EXISTS access_scope "
                f"VARCHAR NOT NULL DEFAULT 'course'"
            ))
            conn.execute(text(
                f"ALTER TABLE {_table} ADD COLUMN IF NOT EXISTS access_sections JSON NOT NULL DEFAULT '[]'"
            ))
        conn.execute(text(
            "ALTER TABLE assignment_submissions ADD COLUMN IF NOT EXISTS test_quality_score "
            "DOUBLE PRECISION"
        ))
        for _col in ('test_correctness_score', 'assertion_quality_score', 'redundancy_score', 'test_diversity_score'):
            conn.execute(text(
                f"ALTER TABLE assignment_submissions ADD COLUMN IF NOT EXISTS {_col} DOUBLE PRECISION"
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
