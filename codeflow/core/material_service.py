"""Course material CRUD — the instructor-uploaded corpus that core/ai_service.py retrieves
from for RAG, and, once published, student-visible reading material on a Topic Hub page.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models import Concept, CourseMaterial


def create_material(
    session: Session, *, title: str, content_text: str, uploaded_by_id: int,
    concept_id: int | None = None,
) -> CourseMaterial:
    material = CourseMaterial(
        title=title, content_text=content_text, uploaded_by_id=uploaded_by_id, concept_id=concept_id,
    )
    session.add(material)
    session.flush()
    return material


def list_materials(
    session: Session, *, uploaded_by_id: int | None = None, concept_id: int | None = None,
    course_id: int | None = None, published_only: bool = False,
) -> list[CourseMaterial]:
    stmt = select(CourseMaterial)
    if uploaded_by_id is not None:
        stmt = stmt.where(CourseMaterial.uploaded_by_id == uploaded_by_id)
    if concept_id is not None:
        stmt = stmt.where(CourseMaterial.concept_id == concept_id)
    if course_id is not None:
        stmt = stmt.join(Concept, Concept.id == CourseMaterial.concept_id).where(Concept.course_id == course_id)
    if published_only:
        stmt = stmt.where(CourseMaterial.published.is_(True))
    stmt = stmt.order_by(CourseMaterial.created_at.desc())
    return list(session.scalars(stmt))


def get_material(session: Session, material_id: int) -> CourseMaterial | None:
    return session.get(CourseMaterial, material_id)


def set_published(session: Session, material: CourseMaterial, published: bool) -> None:
    material.published = published


def delete_material(session: Session, material: CourseMaterial) -> None:
    session.delete(material)
