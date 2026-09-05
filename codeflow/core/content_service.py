"""Content & Animation Service.

Instructors submit source code; this module runs the appropriate analysis
pass, stores the code plus the resulting graph/animation data in the Core
Database, and serves it back out. Students only ever see published rows.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models import Concept, ContentConcept, ContentItem, ContentKind
from graphs.serializer import GraphSerializer
from parser.cfg_builder import CFGBuilder


class ContentGenerationError(Exception):
    """Raised when the submitted source code fails to analyze."""


def _generate_cfg(source_code: str) -> dict:
    graph_data = CFGBuilder().build(source_code)
    if graph_data['metadata'].get('name') == 'Parse Error':
        raise ContentGenerationError(graph_data['metadata'].get('source_code', 'Invalid Python source.'))
    return GraphSerializer.inject_layout_hints(graph_data, 'layered')


_GENERATORS = {
    ContentKind.cfg: _generate_cfg,
}


def supported_kinds() -> list[ContentKind]:
    return list(_GENERATORS)


def generate_graph(kind: ContentKind, source_code: str) -> dict:
    """Run the analysis pass for *kind* over *source_code*.

    Raises ContentGenerationError if the source doesn't analyze cleanly,
    and NotImplementedError for a kind that isn't wired up yet (du_chain,
    dominator, call_graph — coming in later passes).
    """
    generator = _GENERATORS.get(kind)
    if generator is None:
        raise NotImplementedError(f'No content generator registered for kind={kind.value!r} yet.')
    return generator(source_code)


def create_content_item(
    session: Session, *, kind: ContentKind, title: str, source_code: str, created_by_id: int,
    concept_id: int | None = None,
) -> ContentItem:
    graph_data = generate_graph(kind, source_code)
    item = ContentItem(
        kind=kind, title=title, source_code=source_code,
        graph_data=graph_data, created_by_id=created_by_id,
    )
    session.add(item)
    session.flush()
    if concept_id is not None:
        session.add(ContentConcept(content_item_id=item.id, concept_id=concept_id))
        session.flush()
    return item


def list_by_concept(
    session: Session, concept_id: int, *, kind: ContentKind | None = None, published_only: bool = False,
) -> list[ContentItem]:
    stmt = (
        select(ContentItem)
        .join(ContentConcept, ContentConcept.content_item_id == ContentItem.id)
        .where(ContentConcept.concept_id == concept_id)
    )
    if kind is not None:
        stmt = stmt.where(ContentItem.kind == kind)
    if published_only:
        stmt = stmt.where(ContentItem.published.is_(True))
    stmt = stmt.order_by(ContentItem.created_at.desc())
    return list(session.scalars(stmt))


def update_content_item(session: Session, item: ContentItem, *, title: str, source_code: str) -> ContentItem:
    """Update title/code and regenerate the stored graph data from scratch."""
    graph_data = generate_graph(item.kind, source_code)
    item.title = title
    item.source_code = source_code
    item.graph_data = graph_data
    return item


def set_published(session: Session, item: ContentItem, published: bool) -> None:
    item.published = published


def get_content_item(session: Session, item_id: int) -> ContentItem | None:
    return session.get(ContentItem, item_id)


def list_content(
    session: Session, kind: ContentKind, *, published_only: bool = False,
    created_by_id: int | None = None, course_id: int | None = None,
) -> list[ContentItem]:
    stmt = select(ContentItem).where(ContentItem.kind == kind)
    if published_only:
        stmt = stmt.where(ContentItem.published.is_(True))
    if created_by_id is not None:
        stmt = stmt.where(ContentItem.created_by_id == created_by_id)
    if course_id is not None:
        stmt = (
            stmt.join(ContentConcept, ContentConcept.content_item_id == ContentItem.id)
            .join(Concept, Concept.id == ContentConcept.concept_id)
            .where(Concept.course_id == course_id)
        )
    stmt = stmt.order_by(ContentItem.created_at.desc())
    return list(session.scalars(stmt))


def delete_content_item(session: Session, item: ContentItem) -> None:
    session.delete(item)
