"""Grading & Dashboard Service (assignment half): CRUD for Assignment /
AssignmentSubmission, plus grading orchestration via core/sandbox_service.
"""
import asyncio
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

import config
from core import settings_service
from core.ai_service import AIServiceError, generate_assignment_feedback
from core.bkt_service import record_attempt_and_update
from core.models import (
    Assignment, AssignmentAttemptStart, AssignmentLanguage, AssignmentSubmission,
    AssignmentType, FeedbackMode, SubmissionStatus,
)
from core.sandbox_service import GradeResult, run_test_suite
from core.test_quality_service import analyze_test_quality, build_call_graph_view, build_comparison_issue

# Coverage bar a submission has to clear (on top of its tests actually passing) to
# count as "demonstrates understanding" for BKT mastery purposes. Distinct from
# bkt_service.MASTERY_THRESHOLD, which is about the resulting P(mastery) estimate,
# not a single attempt's coverage number.
_MASTERY_COVERAGE_BAR = 70.0

_COMPARISON_PREVIEW_LIMIT = 5  # how many missed lines to spell out before summarizing the rest

_NICE_WORK_MESSAGE = "Your tests cover everything the instructor's reference suite does — nice work."

# Category colors for the two new Detected-Issues entries below — distinct from every
# color test_quality_service._CATEGORIES already uses for its 7 rule-based detectors.
_RULE_COMPARISON_COLOR = '#0550ae'
_AI_COMPARISON_COLOR = '#a21caf'


def _compare_against_reference(
    *, language: AssignmentLanguage, source_code: str, reference_test_code: str,
    student_result: GradeResult,
) -> dict | None:
    """Runs the instructor's reference suite exactly once and returns the raw comparison —
    which lines it covers that the student's own tests don't. Single source of truth for the
    rule-based text, the AI prompt, and both animations below, so the reference suite is never
    run more than once per submission. None if the reference suite itself doesn't run.
    """
    reference_result = run_test_suite(language, source_code, reference_test_code)
    if not reference_result.ran or reference_result.timed_out:
        return None
    reference_covered = set(reference_result.covered_lines) | set(reference_result.partial_lines)
    student_covered = set(student_result.covered_lines) | set(student_result.partial_lines)
    return {'missed_lines': sorted(reference_covered - student_covered)}


def generate_comparison_feedback(*, source_code: str, missed_lines: list[int]) -> str:
    """Formats the deterministic, rule-based text description of missed_lines (the gap
    between the instructor's reference suite and the student's own, from
    _compare_against_reference) — no LLM, no sandbox run of its own.
    """
    if not missed_lines:
        return _NICE_WORK_MESSAGE

    src_lines = source_code.splitlines()
    preview = [
        f'  line {n}: {src_lines[n - 1].strip()}'
        for n in missed_lines[:_COMPARISON_PREVIEW_LIMIT] if 0 < n <= len(src_lines)
    ]
    remaining = len(missed_lines) - len(preview)
    more = f'\n  …and {remaining} more' if remaining > 0 else ''
    plural = 's' if len(missed_lines) != 1 else ''
    return (
        f"The instructor's reference test suite exercises {len(missed_lines)} line{plural} your tests don't reach:\n"
        + '\n'.join(preview) + more
    )


def create_assignment(
    session: Session, *, title: str, description: str, type: AssignmentType,
    language: AssignmentLanguage, source_code: str, given_test_code: str | None, created_by_id: int,
    course_id: int, concept_id: int | None = None, is_practice: bool = False, hint: str = '',
    feedback_mode: FeedbackMode = FeedbackMode.test_cases,
) -> Assignment:
    assignment = Assignment(
        title=title, description=description, type=type, language=language, source_code=source_code,
        given_test_code=given_test_code, created_by_id=created_by_id, course_id=course_id,
        concept_id=concept_id, is_practice=is_practice, hint=hint, feedback_mode=feedback_mode,
    )
    session.add(assignment)
    session.flush()
    return assignment


def update_assignment(
    session: Session, assignment: Assignment, *, title: str, description: str,
    source_code: str, given_test_code: str | None, hint: str = '',
) -> Assignment:
    assignment.title = title
    assignment.description = description
    assignment.source_code = source_code
    assignment.given_test_code = given_test_code
    assignment.hint = hint
    return assignment


def set_feedback_mode(session: Session, assignment: Assignment, mode: FeedbackMode) -> None:
    assignment.feedback_mode = mode


def list_assignments(
    session: Session, *, created_by_id: int | None = None, course_id: int | None = None,
    published_only: bool = False, is_practice: bool | None = None,
) -> list[Assignment]:
    stmt = select(Assignment)
    if created_by_id is not None:
        stmt = stmt.where(Assignment.created_by_id == created_by_id)
    if course_id is not None:
        stmt = stmt.where(Assignment.course_id == course_id)
    if published_only:
        stmt = stmt.where(Assignment.published.is_(True))
    if is_practice is not None:
        stmt = stmt.where(Assignment.is_practice.is_(is_practice))
    stmt = stmt.order_by(Assignment.created_at.desc())
    return list(session.scalars(stmt))


def get_assignment(session: Session, assignment_id: int) -> Assignment | None:
    return session.get(Assignment, assignment_id)


def set_published(session: Session, assignment: Assignment, published: bool) -> None:
    assignment.published = published


def delete_assignment(session: Session, assignment: Assignment) -> None:
    session.delete(assignment)


def mark_assignment_opened(session: Session, assignment_id: int, student_id: int) -> None:
    """Record that a student just opened this assignment's detail page — the start of
    their current attempt. Overwritten on every view, so submit_and_grade always reads
    back the start of the most recent still-open attempt, not a stale earlier one.
    """
    row = session.scalar(
        select(AssignmentAttemptStart).where(
            AssignmentAttemptStart.assignment_id == assignment_id,
            AssignmentAttemptStart.student_id == student_id,
        )
    )
    now = datetime.now(timezone.utc)
    if row is None:
        session.add(AssignmentAttemptStart(assignment_id=assignment_id, student_id=student_id, started_at=now))
    else:
        row.started_at = now


def _generate_ai_feedback(
    session: Session, assignment: Assignment, submitted_code: str, result: GradeResult,
    missed_lines: list[int],
) -> tuple[str, list[int]]:
    """AI feedback for a submission, grounded in missed_lines — the gap between the
    instructor's reference suite and the student's own (_compare_against_reference), not raw
    coverage — gated by two independent switches: a provider key must be configured
    (config.AI_ENABLED) and the admin's global override must be on
    (settings_service.get_settings). Either false means no call is made at all. Failures are
    swallowed the same way generate_comparison_feedback treats a non-running reference suite —
    return ('', []) rather than let a flaky LLM call break grading.
    """
    if not config.AI_ENABLED or not settings_service.get_settings(session).ai_feedback_enabled:
        return '', []
    try:
        return asyncio.run(generate_assignment_feedback(
            source_code=assignment.source_code, submitted_test_code=submitted_code,
            language=assignment.language.value, tests_passed=result.tests_passed, score=result.score,
            line_coverage_percent=result.line_coverage_percent,
            branch_coverage_percent=result.branch_coverage_percent,
            missed_lines=missed_lines,
        ))
    except AIServiceError:
        return '', []


def submit_and_grade(
    session: Session, assignment: Assignment, *, student_id: int, submitted_code: str,
) -> AssignmentSubmission:
    """Grade write_tests submissions immediately in the sandbox (also updating the
    assignment's tagged-concept BKT mastery); queue evaluate_tests submissions for
    instructor review (not yet auto-graded).
    """
    start_row = session.scalar(
        select(AssignmentAttemptStart).where(
            AssignmentAttemptStart.assignment_id == assignment.id,
            AssignmentAttemptStart.student_id == student_id,
        )
    )
    started_at = start_row.started_at if start_row else None

    if assignment.type == AssignmentType.write_tests:
        result = run_test_suite(assignment.language, assignment.source_code, submitted_code)

        comparison = None
        if not result.timed_out and assignment.given_test_code:
            comparison = _compare_against_reference(
                language=assignment.language, source_code=assignment.source_code,
                reference_test_code=assignment.given_test_code, student_result=result,
            )

        concept_feedback = ''
        if comparison and assignment.feedback_mode in (FeedbackMode.test_cases, FeedbackMode.both):
            concept_feedback = generate_comparison_feedback(
                source_code=assignment.source_code, missed_lines=comparison['missed_lines'],
            )

        ai_feedback = ''
        ai_highlight_lines: list[int] = []
        if comparison and assignment.feedback_mode in (FeedbackMode.ai, FeedbackMode.both):
            if comparison['missed_lines']:
                ai_feedback, ai_highlight_lines = _generate_ai_feedback(
                    session, assignment, submitted_code, result, comparison['missed_lines'],
                )
            else:
                ai_feedback = _NICE_WORK_MESSAGE

        detected_issues = []
        call_graph_data = None
        if not result.timed_out and result.line_coverage_percent is not None:
            detected_issues = analyze_test_quality(
                language=assignment.language, source_code=assignment.source_code,
                test_code=submitted_code, result=result,
            )
            call_graph_data = build_call_graph_view(
                language=assignment.language, source_code=assignment.source_code, result=result,
            )

        if comparison and comparison['missed_lines']:
            if assignment.feedback_mode in (FeedbackMode.test_cases, FeedbackMode.both):
                detected_issues.append(build_comparison_issue(
                    language=assignment.language, source_code=assignment.source_code,
                    student_result=result, missed_lines=comparison['missed_lines'], highlight_lines=None,
                    category='Instructor Comparison (Rule-Based)', category_color=_RULE_COMPARISON_COLOR,
                    what_it_is=concept_feedback,
                    why_it_weakens=(
                        "The instructor's reference test suite is the ground truth for what a "
                        "thorough suite should exercise — code it reaches that yours doesn't "
                        "represents behavior you haven't actually verified."
                    ),
                ))
            if assignment.feedback_mode in (FeedbackMode.ai, FeedbackMode.both) and ai_feedback:
                detected_issues.append(build_comparison_issue(
                    language=assignment.language, source_code=assignment.source_code,
                    student_result=result, missed_lines=comparison['missed_lines'],
                    highlight_lines=ai_highlight_lines,
                    category='Instructor Comparison (AI)', category_color=_AI_COMPARISON_COLOR,
                    what_it_is=ai_feedback,
                    why_it_weakens=(
                        "AI-generated feedback, grounded in the same instructor-reference "
                        "comparison as the rule-based version — compare the two."
                        if assignment.feedback_mode == FeedbackMode.both else
                        "AI-generated feedback, grounded in the instructor's reference test suite comparison."
                    ),
                ))
            for i, issue in enumerate(detected_issues, start=1):
                issue['id'] = i

        submission = AssignmentSubmission(
            assignment_id=assignment.id, student_id=student_id, submitted_code=submitted_code,
            status=SubmissionStatus.error if result.timed_out else SubmissionStatus.graded,
            score=result.score, tests_passed=result.tests_passed, feedback=result.output,
            concept_feedback=concept_feedback, ai_feedback=ai_feedback,
            lines_covered=result.lines_covered, lines_missed=result.lines_missed,
            branches_covered=result.branches_covered, branches_missed=result.branches_missed,
            line_coverage_percent=result.line_coverage_percent,
            branch_coverage_percent=result.branch_coverage_percent,
            total_coverage_percent=result.total_coverage_percent,
            covered_lines=result.covered_lines, partial_lines=result.partial_lines,
            uncovered_lines=result.uncovered_lines,
            statement_coverage_percent=result.statement_coverage_percent,
            function_coverage=result.function_coverage,
            assert_covered=result.assert_covered, assert_total=result.assert_total,
            assert_coverage_percent=result.assert_coverage_percent,
            detected_issues=detected_issues,
            call_graph_data=call_graph_data,
            started_at=started_at,
        )
        session.add(submission)
        session.flush()

        if not result.timed_out and assignment.concept_id is not None:
            demonstrated = bool(
                result.tests_passed and result.total_coverage_percent is not None
                and result.total_coverage_percent >= _MASTERY_COVERAGE_BAR
            )
            record_attempt_and_update(
                session, user_id=student_id, concept_id=assignment.concept_id, is_correct=demonstrated,
            )
    else:
        submission = AssignmentSubmission(
            assignment_id=assignment.id, student_id=student_id, submitted_code=submitted_code,
            status=SubmissionStatus.pending_review, score=None, tests_passed=None,
            feedback='Submitted — evaluate_tests assignments are graded manually by your instructor for now.',
            started_at=started_at,
        )
        session.add(submission)
        session.flush()

    return submission


def list_submissions(
    session: Session, assignment_id: int, *, student_id: int | None = None,
) -> list[AssignmentSubmission]:
    stmt = select(AssignmentSubmission).where(AssignmentSubmission.assignment_id == assignment_id)
    if student_id is not None:
        stmt = stmt.where(AssignmentSubmission.student_id == student_id)
    stmt = stmt.order_by(AssignmentSubmission.created_at.desc())
    return list(session.scalars(stmt))


def list_pending_reviews(session: Session, *, course_id: int) -> list[AssignmentSubmission]:
    """evaluate_tests submissions, across every assignment in this course, still waiting
    on a manual grade — the instructor dashboard's "Needs Review" queue."""
    stmt = (
        select(AssignmentSubmission)
        .join(Assignment, AssignmentSubmission.assignment_id == Assignment.id)
        .where(
            Assignment.course_id == course_id,
            AssignmentSubmission.status == SubmissionStatus.pending_review,
        )
        .order_by(AssignmentSubmission.created_at.desc())
    )
    return list(session.scalars(stmt))


def latest_submission(session: Session, assignment_id: int, student_id: int) -> AssignmentSubmission | None:
    subs = list_submissions(session, assignment_id, student_id=student_id)
    return subs[0] if subs else None


def list_submissions_by_student(session: Session, student_id: int) -> list[AssignmentSubmission]:
    """Every submission this student has made, across every assignment, newest first —
    for the instructor's per-student grade history (pages/instructor_student_detail_page.py)."""
    stmt = (
        select(AssignmentSubmission)
        .where(AssignmentSubmission.student_id == student_id)
        .order_by(AssignmentSubmission.created_at.desc())
    )
    return list(session.scalars(stmt))
