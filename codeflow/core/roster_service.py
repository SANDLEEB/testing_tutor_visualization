"""Instructor roster aggregation — one summary per student (average concept mastery,
practice-question performance, assignment performance), for the Students page and the
instructor home dashboard's "needs attention" callout. Pulls from bkt_service /
question_service / assignment_service rather than owning any data itself.

Fields are plain values, not ORM objects — safe to hold onto after the session that
built them closes (SQLAlchemy expires ORM attributes on commit).
"""
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from auth.models import User
from core.assignment_service import list_submissions_by_student
from core.models import ConceptMastery, Enrollment, EnrollmentRole
from core.question_service import get_attempt_stats


@dataclass
class StudentSummary:
    user_id: int
    name: str
    email: str
    cwid: str                     # '' = not set
    section: str                  # '' = unsectioned
    avg_mastery: float | None     # None if the student hasn't attempted anything tracked yet
    tracked_concepts: int
    questions_answered: int
    questions_correct: int
    accuracy: int
    assignments_submitted: int
    avg_assignment_score: float | None


def summarize_student(session: Session, user: User, section: str) -> StudentSummary:
    mastery_rows = list(session.scalars(
        select(ConceptMastery.p_mastery).where(ConceptMastery.user_id == user.id)
    ))
    avg_mastery = sum(mastery_rows) / len(mastery_rows) if mastery_rows else None

    stats = get_attempt_stats(session, user.id)

    submissions = list_submissions_by_student(session, user.id)
    graded_scores = [s.score for s in submissions if s.score is not None]
    avg_score = sum(graded_scores) / len(graded_scores) if graded_scores else None

    return StudentSummary(
        user_id=user.id, name=user.full_name, email=user.email, cwid=user.cwid, section=section,
        avg_mastery=avg_mastery, tracked_concepts=len(mastery_rows),
        questions_answered=stats['total'], questions_correct=stats['correct'], accuracy=stats['accuracy'],
        assignments_submitted=len(submissions), avg_assignment_score=avg_score,
    )


def list_student_summaries(session: Session, course_id: int, *, section: str | None = None) -> list[StudentSummary]:
    """Every student in course_id, optionally narrowed to one section label
    (core/course_service.list_sections lists what's in use) — None means every section."""
    stmt = (
        select(User, Enrollment.section)
        .join(Enrollment, Enrollment.user_id == User.id)
        .where(Enrollment.course_id == course_id, Enrollment.role == EnrollmentRole.student)
    )
    if section is not None:
        stmt = stmt.where(Enrollment.section == section)
    stmt = stmt.order_by(Enrollment.section, User.full_name)
    rows = session.execute(stmt).all()
    return [summarize_student(session, user, sec) for user, sec in rows]
