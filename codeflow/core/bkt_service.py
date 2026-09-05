"""Adaptive Practice Engine — Bayesian Knowledge Tracing (BKT).

Tracks each student's estimated probability of having mastered each Concept
(ConceptMastery.p_mastery), updates it after every QuestionAttempt via the
standard two-step BKT rule (Bayesian update on the observed correctness,
then a learning-transition step), and uses the resulting per-concept
mastery estimates to pick which concept — and which question within it —
to serve next.

Parameters are global for now (not per-concept or per-student): with a
handful of instructor-authored questions and a small number of attempts,
there isn't enough data yet to fit per-concept parameters reliably. These
are the commonly-cited defaults from the original Corbett & Anderson model,
tuned for ~4-choice multiple choice (hence P_GUESS ≈ 1/4).
"""
import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.course_service import list_students
from core.models import Assignment, AssignmentSubmission, Concept, ConceptMastery, Question, QuestionAttempt

P_L0 = 0.30    # prior probability of already knowing a concept, before any practice
P_T = 0.15     # probability of learning it after one practice opportunity
P_GUESS = 0.25  # probability of answering correctly without knowing (~4-choice MC)
P_SLIP = 0.10   # probability of a careless mistake despite knowing it

MASTERY_THRESHOLD = 0.90
WEAK_THRESHOLD = 0.50  # below this, a student's grasp is weak enough to flag for attention


def _slugify(name: str) -> str:
    slug = re.sub(r'[^a-z0-9]+', '-', name.strip().lower()).strip('-')
    return slug or 'topic'


def get_or_create_concept(session: Session, name: str, course_id: int) -> Concept:
    """Look up a Concept by (case-insensitive) name *within this course*, creating it if
    it doesn't exist yet.

    Concepts (topics) are entirely instructor-driven and created in exactly one place —
    the instructor Topics page (pages/instructor_topics_page.py). Every other page that
    tags content with a topic (Assignments, Practice Questions) only ever picks from
    topics that already exist; this get-or-create helper backs topic creation itself, not
    those pickers. Scoped per-course so two different courses can each have their own
    "Loops" topic without colliding.
    """
    name = name.strip()
    existing = session.scalar(
        select(Concept).where(Concept.course_id == course_id, func.lower(Concept.name) == name.lower())
    )
    if existing:
        return existing

    slug = _slugify(name)
    base_slug = slug
    n = 2
    while session.scalar(select(Concept).where(Concept.course_id == course_id, Concept.slug == slug)):
        slug = f'{base_slug}-{n}'
        n += 1

    concept = Concept(course_id=course_id, slug=slug, name=name, description='')
    session.add(concept)
    session.flush()
    return concept


def list_concept_names(session: Session, course_id: int) -> list[str]:
    """Topic names instructors have created so far in this course — for the topic picker."""
    return list(session.scalars(
        select(Concept.name).where(Concept.course_id == course_id).order_by(Concept.name)
    ))


def list_concepts(session: Session, course_id: int) -> list[Concept]:
    """Full Concept rows for this course, ordered by name — for pickers/pages that need
    more than just the name (e.g. the concept id to select by)."""
    return list(session.scalars(
        select(Concept).where(Concept.course_id == course_id).order_by(Concept.name)
    ))


def _bayes_update(p_know: float, is_correct: bool) -> float:
    """Posterior P(knows) given the observed correctness of this one attempt."""
    if is_correct:
        numerator = p_know * (1 - P_SLIP)
        denominator = numerator + (1 - p_know) * P_GUESS
    else:
        numerator = p_know * P_SLIP
        denominator = numerator + (1 - p_know) * (1 - P_GUESS)
    return numerator / denominator if denominator > 0 else p_know


def _apply_learning(p_know_posterior: float) -> float:
    """P(knows) on the *next* opportunity, after the learning-transition step."""
    return p_know_posterior + (1 - p_know_posterior) * P_T


def mastery_tier(p_mastery: float) -> str:
    """'mastered' / 'partial' / 'weak' — presentation tier for color-coding mastery in the UI
    (green / orange / red), separate from the raw p_mastery estimate."""
    if p_mastery >= MASTERY_THRESHOLD:
        return 'mastered'
    if p_mastery >= WEAK_THRESHOLD:
        return 'partial'
    return 'weak'


def get_mastery(session: Session, user_id: int, concept_id: int) -> float:
    row = session.scalar(
        select(ConceptMastery).where(
            ConceptMastery.user_id == user_id, ConceptMastery.concept_id == concept_id,
        )
    )
    return row.p_mastery if row else P_L0


def record_attempt_and_update(
    session: Session, *, user_id: int, concept_id: int, is_correct: bool,
) -> tuple[float, float]:
    """Bayesian-update this student's mastery of *concept_id* after one attempt.

    Returns (prior_mastery, new_mastery) so callers can show the change.
    """
    row = session.scalar(
        select(ConceptMastery).where(
            ConceptMastery.user_id == user_id, ConceptMastery.concept_id == concept_id,
        )
    )
    prior = row.p_mastery if row else P_L0
    posterior = _bayes_update(prior, is_correct)
    new_mastery = _apply_learning(posterior)

    if row is None:
        row = ConceptMastery(user_id=user_id, concept_id=concept_id, p_mastery=new_mastery)
        session.add(row)
    else:
        row.p_mastery = new_mastery
    session.flush()
    return prior, new_mastery


def list_mastery(session: Session, user_id: int, course_id: int) -> list[dict]:
    """Every concept in *course_id* with this student's current mastery estimate (defaults
    to P_L0 if never attempted)."""
    concepts = list(session.scalars(
        select(Concept).where(Concept.course_id == course_id).order_by(Concept.name)
    ))
    rows = {
        m.concept_id: m.p_mastery
        for m in session.scalars(select(ConceptMastery).where(ConceptMastery.user_id == user_id))
    }
    return [
        {
            'concept': c,
            'mastery': rows.get(c.id, P_L0),
            'is_mastered': rows.get(c.id, P_L0) >= MASTERY_THRESHOLD,
        }
        for c in concepts
    ]


def list_class_mastery(session: Session, course_id: int) -> list[dict]:
    """Per-concept average mastery across every student who has attempted something tied
    to it, within *course_id* — the class-wide counterpart to list_mastery, for the
    instructor dashboard. Concepts nobody has attempted yet are omitted (there's no class
    signal for them)."""
    concepts = list(session.scalars(
        select(Concept).where(Concept.course_id == course_id).order_by(Concept.name)
    ))
    result = []
    for c in concepts:
        rows = list(session.scalars(
            select(ConceptMastery.p_mastery).where(ConceptMastery.concept_id == c.id)
        ))
        if not rows:
            continue
        avg = sum(rows) / len(rows)
        result.append({'concept': c, 'mastery': avg, 'student_count': len(rows), 'tier': mastery_tier(avg)})
    return result


def list_student_mastery_matrix(session: Session, course_id: int) -> list[dict]:
    """Every student enrolled in course_id, each paired with their mastery of every concept
    in the course (defaulting to P_L0 for a concept they haven't attempted yet, same as
    list_mastery) — the instructor's per-student x per-topic breakdown table.
    """
    concepts = list_concepts(session, course_id)
    students = list_students(session, course_id)
    result = []
    for student in students:
        rows = {
            m.concept_id: m.p_mastery
            for m in session.scalars(select(ConceptMastery).where(ConceptMastery.user_id == student.id))
        }
        result.append({
            'user': student,
            'mastery': {c.id: rows.get(c.id, P_L0) for c in concepts},
        })
    return result


def pick_next_question(session: Session, user_id: int, course_id: int) -> Question | None:
    """Adaptive selection: weakest unmastered concept first, then an
    unattempted question in it, else the one this student saw longest ago.
    """
    mastery_rows = list_mastery(session, user_id, course_id)
    unmastered = sorted((m for m in mastery_rows if not m['is_mastered']), key=lambda m: m['mastery'])
    mastered = [m for m in mastery_rows if m['is_mastered']]
    concept_order = unmastered + mastered

    attempted_ids = set(session.scalars(
        select(QuestionAttempt.question_id).where(QuestionAttempt.user_id == user_id)
    ))

    for m in concept_order:
        questions = list(session.scalars(
            select(Question).where(
                Question.concept_id == m['concept'].id, Question.published.is_(True),
            )
        ))
        if not questions:
            continue

        unattempted = [q for q in questions if q.id not in attempted_ids]
        if unattempted:
            return unattempted[0]

        def _last_attempt_at(q):
            return session.scalar(
                select(QuestionAttempt.created_at)
                .where(QuestionAttempt.user_id == user_id, QuestionAttempt.question_id == q.id)
                .order_by(QuestionAttempt.created_at.desc()).limit(1)
            )

        return min(questions, key=_last_attempt_at)

    return None


def pick_next_practice_assignment(session: Session, user_id: int, course_id: int) -> Assignment | None:
    """Same adaptive selection as pick_next_question, over is_practice Assignment rows
    instead of the Question bank: weakest unmastered concept first, then an unsubmitted
    practice assignment in it, else the one this student last submitted longest ago.
    """
    mastery_rows = list_mastery(session, user_id, course_id)
    unmastered = sorted((m for m in mastery_rows if not m['is_mastered']), key=lambda m: m['mastery'])
    mastered = [m for m in mastery_rows if m['is_mastered']]
    concept_order = unmastered + mastered

    submitted_ids = set(session.scalars(
        select(AssignmentSubmission.assignment_id).where(AssignmentSubmission.student_id == user_id)
    ))

    for m in concept_order:
        assignments = list(session.scalars(
            select(Assignment).where(
                Assignment.concept_id == m['concept'].id,
                Assignment.is_practice.is_(True),
                Assignment.published.is_(True),
            )
        ))
        if not assignments:
            continue

        unsubmitted = [a for a in assignments if a.id not in submitted_ids]
        if unsubmitted:
            return unsubmitted[0]

        def _last_submitted_at(a):
            return session.scalar(
                select(AssignmentSubmission.created_at)
                .where(AssignmentSubmission.student_id == user_id, AssignmentSubmission.assignment_id == a.id)
                .order_by(AssignmentSubmission.created_at.desc()).limit(1)
            )

        return min(assignments, key=_last_submitted_at)

    return None
