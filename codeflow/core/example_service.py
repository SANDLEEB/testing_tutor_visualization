"""Worked-example CRUD — a source file paired with a correct example test suite, shown to
students as a "how testing works" walkthrough before they write their own (assignments) or
answer questions about a graph in the abstract (practice questions).

Coverage and the CFG are computed once, at create/update time, by actually running the
example test suite through the same sandbox used for grading (core/sandbox_service.py) —
this is real, measured coverage of a real example, not a canned/fabricated number.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.content_service import ContentGenerationError, generate_graph
from core.models import AssignmentLanguage, Concept, ContentKind, TestSuiteExample
from core.sandbox_service import run_test_suite


def _compute_fields(language: AssignmentLanguage, source_code: str, test_code: str) -> dict:
    result = run_test_suite(language, source_code, test_code)

    graph_data = None
    if language == AssignmentLanguage.python:
        try:
            graph_data = generate_graph(ContentKind.cfg, source_code)
        except ContentGenerationError:
            graph_data = None  # source doesn't parse cleanly enough for a CFG — skip the animation, not fatal

    return {
        'graph_data': graph_data,
        'tests_passed': result.tests_passed if not result.timed_out else None,
        'lines_covered': result.lines_covered, 'lines_missed': result.lines_missed,
        'branches_covered': result.branches_covered, 'branches_missed': result.branches_missed,
        'line_coverage_percent': result.line_coverage_percent,
        'branch_coverage_percent': result.branch_coverage_percent,
        'total_coverage_percent': result.total_coverage_percent,
        'covered_lines': result.covered_lines, 'partial_lines': result.partial_lines,
        'uncovered_lines': result.uncovered_lines,
    }


def create_example(
    session: Session, *, concept_id: int, title: str, language: AssignmentLanguage,
    source_code: str, test_code: str, explanation: str, created_by_id: int,
) -> TestSuiteExample:
    fields = _compute_fields(language, source_code, test_code)
    example = TestSuiteExample(
        concept_id=concept_id, title=title, language=language,
        source_code=source_code, test_code=test_code, explanation=explanation,
        created_by_id=created_by_id, **fields,
    )
    session.add(example)
    session.flush()
    return example


def update_example(
    session: Session, example: TestSuiteExample, *, title: str, source_code: str,
    test_code: str, explanation: str,
) -> TestSuiteExample:
    fields = _compute_fields(example.language, source_code, test_code)
    example.title = title
    example.source_code = source_code
    example.test_code = test_code
    example.explanation = explanation
    for key, value in fields.items():
        setattr(example, key, value)
    return example


def list_examples(
    session: Session, *, concept_id: int | None = None, created_by_id: int | None = None,
    course_id: int | None = None, published_only: bool = False,
) -> list[TestSuiteExample]:
    stmt = select(TestSuiteExample)
    if concept_id is not None:
        stmt = stmt.where(TestSuiteExample.concept_id == concept_id)
    if created_by_id is not None:
        stmt = stmt.where(TestSuiteExample.created_by_id == created_by_id)
    if course_id is not None:
        stmt = stmt.join(Concept, Concept.id == TestSuiteExample.concept_id).where(Concept.course_id == course_id)
    if published_only:
        stmt = stmt.where(TestSuiteExample.published.is_(True))
    stmt = stmt.order_by(TestSuiteExample.created_at.desc())
    return list(session.scalars(stmt))


def get_example(session: Session, example_id: int) -> TestSuiteExample | None:
    return session.get(TestSuiteExample, example_id)


def set_published(session: Session, example: TestSuiteExample, published: bool) -> None:
    example.published = published


def delete_example(session: Session, example: TestSuiteExample) -> None:
    session.delete(example)
