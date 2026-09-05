"""Topic Hub — the Table of Contents view over a Concept.

A topic (Concept) aggregates everything an instructor has tied to it: course
material (pasted/uploaded, or drafted via the RAG chat), CFG content items,
and practice questions. This module is read/aggregate/admin logic; the
individual content types are still created through their own services
(core/material_service.py, core/content_service.py, core/practice_service.py)
— this just ties them together by concept_id.
"""
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from core.content_service import list_by_concept as list_content_by_concept
from core.content_service import set_published as set_content_published
from core.material_service import list_materials
from core.material_service import set_published as set_material_published
from core.models import Assignment, Concept, ConceptMastery, ContentConcept, Question, TestSuiteExample
from core.question_service import list_questions
from core.question_service import set_published as set_question_published


class TopicNotEmptyError(Exception):
    """Raised when trying to delete a topic that still has content attached."""


@dataclass
class TopicCounts:
    concept: Concept
    material_count: int
    content_count: int
    question_count: int
    assignment_count: int = 0
    example_count: int = 0

    @property
    def total(self) -> int:
        return (
            self.material_count + self.content_count + self.question_count
            + self.assignment_count + self.example_count
        )


def _list_assignments_by_concept(session: Session, concept_id: int, *, published_only: bool = False):
    stmt = select(Assignment).where(Assignment.concept_id == concept_id)
    if published_only:
        stmt = stmt.where(Assignment.published.is_(True))
    return list(session.scalars(stmt))


def list_topics(session: Session, course_id: int) -> list[TopicCounts]:
    """Every topic (Concept) in this course, each with counts of attached material/
    content/questions/assignments/examples — used both as a Table of Contents (by pages
    still being rebuilt) and as delete_topic's non-empty check."""
    concepts = list(session.scalars(
        select(Concept).where(Concept.course_id == course_id).order_by(Concept.name)
    ))
    counts = []
    for c in concepts:
        material_count = len(list_materials(session, concept_id=c.id))
        content_count = len(session.scalars(
            select(ContentConcept).where(ContentConcept.concept_id == c.id)
        ).all())
        question_count = len(session.scalars(
            select(Question).where(Question.concept_id == c.id)
        ).all())
        assignment_count = len(_list_assignments_by_concept(session, c.id))
        example_count = len(session.scalars(
            select(TestSuiteExample).where(TestSuiteExample.concept_id == c.id)
        ).all())
        counts.append(TopicCounts(
            concept=c, material_count=material_count,
            content_count=content_count, question_count=question_count,
            assignment_count=assignment_count, example_count=example_count,
        ))
    return counts


def list_topics_with_published_content(session: Session, course_id: int) -> list[TopicCounts]:
    """Topics in this course that have at least one *published* item of any kind — what
    students browse."""
    all_topics = list_topics(session, course_id)
    result = []
    for t in all_topics:
        materials = list_materials(session, concept_id=t.concept.id, published_only=True)
        content = list_content_by_concept(session, t.concept.id, published_only=True)
        questions = list_questions(session, concept_id=t.concept.id, published_only=True)
        assignments = _list_assignments_by_concept(session, t.concept.id, published_only=True)
        if materials or content or questions or assignments:
            result.append(TopicCounts(
                concept=t.concept, material_count=len(materials),
                content_count=len(content), question_count=len(questions),
                assignment_count=len(assignments),
            ))
    return result


def get_topic(session: Session, concept_id: int) -> Concept | None:
    return session.get(Concept, concept_id)


def update_topic(session: Session, concept: Concept, *, name: str, description: str) -> None:
    concept.name = name.strip()
    concept.description = description.strip()


def publish_all(session: Session, concept_id: int) -> int:
    """Publish every draft material/visualization/question tied to this topic in one
    action — the "Publish" button on the Topic Hub. Returns how many were newly published."""
    count = 0
    for m in list_materials(session, concept_id=concept_id):
        if not m.published:
            set_material_published(session, m, True)
            count += 1
    for item in list_content_by_concept(session, concept_id):
        if not item.published:
            set_content_published(session, item, True)
            count += 1
    for q in list_questions(session, concept_id=concept_id):
        if not q.published:
            set_question_published(session, q, True)
            count += 1
    return count


def delete_topic(session: Session, concept: Concept) -> None:
    """Refuses to delete a topic with any authored content still attached (material,
    visualizations, questions, assignments, worked examples). ConceptMastery rows are pure
    derived progress data, not authored content — once a topic has none of the above left,
    any leftover mastery percentages for it are meaningless, so they're cleaned up here
    rather than also blocking the delete.
    """
    counts = next((t for t in list_topics(session, concept.course_id) if t.concept.id == concept.id), None)
    if counts and counts.total > 0:
        raise TopicNotEmptyError(
            f'"{concept.name}" still has {counts.total} item(s) attached — remove them first.'
        )
    session.execute(delete(ConceptMastery).where(ConceptMastery.concept_id == concept.id))
    session.delete(concept)
