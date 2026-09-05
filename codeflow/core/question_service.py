"""Question bank CRUD — quiz questions authored manually or by the AI Question
Authoring Service. Students only ever see published rows (mirrors
core/content_service.py's draft → publish flow for ContentItem).
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models import Concept, Question, QuestionAttempt, QuestionSource, QuestionType


def create_question(
    session: Session, *, prompt: str, choices: list[str], answer: str, explanation: str,
    created_by_id: int, material_id: int | None = None, concept_id: int | None = None,
    source: QuestionSource = QuestionSource.ai_generated,
    type: QuestionType = QuestionType.multiple_choice,
) -> Question:
    question = Question(
        prompt=prompt, choices=choices, answer=answer, explanation=explanation,
        created_by_id=created_by_id, material_id=material_id, concept_id=concept_id,
        source=source, type=type,
    )
    session.add(question)
    session.flush()
    return question


def list_questions(
    session: Session, *, created_by_id: int | None = None, concept_id: int | None = None,
    course_id: int | None = None, published_only: bool = False,
) -> list[Question]:
    stmt = select(Question)
    if created_by_id is not None:
        stmt = stmt.where(Question.created_by_id == created_by_id)
    if concept_id is not None:
        stmt = stmt.where(Question.concept_id == concept_id)
    if course_id is not None:
        stmt = stmt.join(Concept, Concept.id == Question.concept_id).where(Concept.course_id == course_id)
    if published_only:
        stmt = stmt.where(Question.published.is_(True))
    stmt = stmt.order_by(Question.created_at.desc())
    return list(session.scalars(stmt))


def get_question(session: Session, question_id: int) -> Question | None:
    return session.get(Question, question_id)


def set_published(session: Session, question: Question, published: bool) -> None:
    question.published = published


def delete_question(session: Session, question: Question) -> None:
    session.delete(question)


def record_attempt(
    session: Session, *, question_id: int, user_id: int, given_answer: str, is_correct: bool,
) -> QuestionAttempt:
    attempt = QuestionAttempt(
        question_id=question_id, user_id=user_id, given_answer=given_answer, is_correct=is_correct,
    )
    session.add(attempt)
    session.flush()
    return attempt


def get_attempt_stats(session: Session, user_id: int) -> dict:
    attempts = list(session.scalars(select(QuestionAttempt).where(QuestionAttempt.user_id == user_id)))
    total = len(attempts)
    correct = sum(1 for a in attempts if a.is_correct)
    return {'total': total, 'correct': correct, 'accuracy': round(correct / total * 100) if total else 0}


def list_attempts_for_user(session: Session, user_id: int) -> list[QuestionAttempt]:
    """Every attempt this student has made, newest first — for the instructor's
    per-student grade history (pages/instructor_student_detail_page.py)."""
    stmt = (
        select(QuestionAttempt)
        .where(QuestionAttempt.user_id == user_id)
        .order_by(QuestionAttempt.created_at.desc())
    )
    return list(session.scalars(stmt))
