"""Grading & Dashboard Service (assignment half): CRUD for Assignment /
AssignmentSubmission, plus grading orchestration via core/sandbox_service.
"""
import asyncio
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

import config
from core import settings_service
from core.ai_service import AIServiceError, generate_assignment_feedback
from core.anonymize import hash_student_id, hash_submission_id
from core.bkt_service import record_attempt_and_update
from core.course_service import get_enrollment
from core.models import (
    AccessScope, Assignment, AssignmentAccessStudent, AssignmentAttemptStart, AssignmentDueDateOverride,
    AssignmentLanguage, AssignmentSubmission, AssignmentType, FeedbackMode, SubmissionStatus,
)
from core.sandbox_service import GradeResult, run_test_suite
from core.test_quality_service import (
    TestQualityBreakdown, analyze_test_quality, build_call_graph_view, build_comparison_issue,
    compute_test_quality_breakdown,
)

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
    feedback_mode: FeedbackMode = FeedbackMode.test_cases, animated_feedback: bool = True,
) -> Assignment:
    assignment = Assignment(
        title=title, description=description, type=type, language=language, source_code=source_code,
        given_test_code=given_test_code, created_by_id=created_by_id, course_id=course_id,
        concept_id=concept_id, is_practice=is_practice, hint=hint, feedback_mode=feedback_mode,
        animated_feedback=animated_feedback,
    )
    session.add(assignment)
    session.flush()
    return assignment


def update_assignment(
    session: Session, assignment: Assignment, *, title: str, description: str,
    type: AssignmentType, language: AssignmentLanguage,
    source_code: str, given_test_code: str | None, hint: str = '',
) -> Assignment:
    assignment.title = title
    assignment.description = description
    assignment.type = type
    assignment.language = language
    assignment.source_code = source_code
    assignment.given_test_code = given_test_code
    assignment.hint = hint
    return assignment


def set_feedback_mode(session: Session, assignment: Assignment, mode: FeedbackMode) -> None:
    assignment.feedback_mode = mode


def set_animated_feedback(session: Session, assignment: Assignment, enabled: bool) -> None:
    assignment.animated_feedback = enabled


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


def get_assignment_access_student_ids(session: Session, assignment_id: int) -> list[int]:
    return list(session.scalars(
        select(AssignmentAccessStudent.student_id).where(AssignmentAccessStudent.assignment_id == assignment_id)
    ))


def set_assignment_access(
    session: Session, assignment: Assignment, *, scope: AccessScope,
    sections: list[str] | None = None, student_ids: list[int] | None = None,
) -> None:
    """Who beyond course membership can see this assignment — 'course' (the default,
    everyone enrolled), 'sections' (only Enrollment.section in `sections`), or
    'students' (only the given student_ids, synced into AssignmentAccessStudent).
    sections/student_ids for the scope(s) NOT chosen are cleared, not just ignored,
    so switching scope doesn't leave stale grants behind."""
    assignment.access_scope = scope
    assignment.access_sections = sorted(set(sections)) if scope == AccessScope.sections and sections else []
    session.execute(delete(AssignmentAccessStudent).where(AssignmentAccessStudent.assignment_id == assignment.id))
    if scope == AccessScope.students:
        for student_id in set(student_ids or []):
            session.add(AssignmentAccessStudent(assignment_id=assignment.id, student_id=student_id))


def student_can_access_assignment(
    assignment: Assignment, *, student_id: int, student_section: str, access_student_ids: set[int] = frozenset(),
) -> bool:
    """Pure check, no query — access_student_ids only matters when access_scope is
    'students' (pass get_assignment_access_student_ids's result, or a batch-fetched set)."""
    if assignment.access_scope == AccessScope.course:
        return True
    if assignment.access_scope == AccessScope.sections:
        return student_section in (assignment.access_sections or [])
    return student_id in access_student_ids


def list_visible_assignments_for_student(
    session: Session, *, course_id: int, student_id: int, student_section: str, is_practice: bool | None = None,
) -> list[Assignment]:
    """Published assignments in course_id this specific student can actually see —
    list_assignments(published_only=True) plus access_scope. Every student-facing
    surface (graded-assignments list, adaptive practice) filters through here, not
    list_assignments directly, so a per-student/per-section restriction an
    instructor sets actually holds everywhere a student could otherwise reach it."""
    assignments = list_assignments(session, course_id=course_id, published_only=True, is_practice=is_practice)
    student_scoped_ids = [a.id for a in assignments if a.access_scope == AccessScope.students]
    # Assignment ids (not student ids!) this student has a grant for — batch-fetched
    # once rather than one get_assignment_access_student_ids query per assignment.
    granted_assignment_ids = set()
    if student_scoped_ids:
        granted_assignment_ids = set(session.scalars(
            select(AssignmentAccessStudent.assignment_id).where(
                AssignmentAccessStudent.student_id == student_id,
                AssignmentAccessStudent.assignment_id.in_(student_scoped_ids),
            )
        ))
    return [
        a for a in assignments
        if a.access_scope == AccessScope.course
        or (a.access_scope == AccessScope.sections and student_section in (a.access_sections or []))
        or (a.access_scope == AccessScope.students and a.id in granted_assignment_ids)
    ]


def set_assignment_due_date(session: Session, assignment: Assignment, due_at: datetime | None) -> None:
    assignment.due_at = due_at


def list_due_date_overrides(session: Session, assignment_id: int) -> list[AssignmentDueDateOverride]:
    return list(session.scalars(
        select(AssignmentDueDateOverride).where(AssignmentDueDateOverride.assignment_id == assignment_id)
    ))


def set_due_date_overrides(session: Session, assignment: Assignment, overrides: list[dict]) -> None:
    """Replaces every existing override for this assignment with `overrides` — each a
    {'section': str} or {'student_id': int} dict plus 'due_at' (a timezone-aware
    datetime). Whole-class default deadline is Assignment.due_at
    (set_assignment_due_date), not here."""
    session.execute(
        delete(AssignmentDueDateOverride).where(AssignmentDueDateOverride.assignment_id == assignment.id)
    )
    for o in overrides:
        session.add(AssignmentDueDateOverride(
            assignment_id=assignment.id, section=o.get('section'), student_id=o.get('student_id'),
            due_at=o['due_at'],
        ))


def resolve_due_at(
    assignment: Assignment, *, student_id: int, student_section: str,
    overrides: list[AssignmentDueDateOverride] = (),
) -> datetime | None:
    """The deadline that actually applies to this student — a per-student override
    beats a per-section override beats the assignment's own due_at (None = no
    deadline). Pass list_due_date_overrides's result, or a pre-fetched list for a
    batch of students."""
    student_override = next((o for o in overrides if o.student_id == student_id), None)
    if student_override is not None:
        return student_override.due_at
    if student_section:
        section_override = next((o for o in overrides if o.section == student_section), None)
        if section_override is not None:
            return section_override.due_at
    return assignment.due_at


class AssignmentPastDueError(Exception):
    """Raised by submit_and_grade when this student's resolved deadline has already
    passed — the assignment stays visible/readable, this just blocks a new final
    submission from being graded."""
    def __init__(self, due_at: datetime):
        self.due_at = due_at
        super().__init__(f'Past due — the deadline for this assignment was {due_at.isoformat()}.')


def delete_assignment(session: Session, assignment: Assignment) -> None:
    session.execute(delete(AssignmentAccessStudent).where(AssignmentAccessStudent.assignment_id == assignment.id))
    session.execute(
        delete(AssignmentDueDateOverride).where(AssignmentDueDateOverride.assignment_id == assignment.id)
    )
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

    Raises AssignmentPastDueError before doing any grading work if this student's
    resolved deadline (resolve_due_at) has already passed — checked here, not just in
    the UI, so a late submission can't slip through by calling this directly.
    """
    enrollment = get_enrollment(session, student_id)
    due_at = resolve_due_at(
        assignment, student_id=student_id, student_section=enrollment.section if enrollment else '',
        overrides=list_due_date_overrides(session, assignment.id),
    )
    if due_at is not None and datetime.now(timezone.utc) > due_at:
        raise AssignmentPastDueError(due_at)

    start_row = session.scalar(
        select(AssignmentAttemptStart).where(
            AssignmentAttemptStart.assignment_id == assignment.id,
            AssignmentAttemptStart.student_id == student_id,
        )
    )
    started_at = start_row.started_at if start_row else None
    submitted_at = datetime.now(timezone.utc)
    duration_seconds = round((submitted_at - started_at).total_seconds()) if started_at is not None else None
    student_token = hash_student_id(student_id)

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
        quality: TestQualityBreakdown | None = None
        if not result.timed_out and result.line_coverage_percent is not None:
            detected_issues = analyze_test_quality(
                language=assignment.language, source_code=assignment.source_code,
                test_code=submitted_code, result=result,
            )
            # Same 0-if-failing convention as result.score (the Coverage grade) — computed
            # from detected_issues as they stand right now, before Instructor Comparison
            # issues get appended below (see compute_test_quality_breakdown's docstring).
            quality = (
                compute_test_quality_breakdown(detected_issues) if result.tests_passed
                else TestQualityBreakdown.zero()
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
            assignment_id=assignment.id, student_id=student_id, student_token=student_token,
            submitted_code=submitted_code,
            status=SubmissionStatus.error if result.timed_out else SubmissionStatus.graded,
            score=result.score,
            test_quality_score=quality.overall if quality else None,
            test_correctness_score=quality.test_correctness if quality else None,
            assertion_quality_score=quality.assertion_quality if quality else None,
            redundancy_score=quality.redundancy if quality else None,
            test_diversity_score=quality.test_diversity if quality else None,
            tests_passed=result.tests_passed, feedback=result.output,
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
            method_coverage_percent=result.method_coverage_percent,
            assert_covered=result.assert_covered, assert_total=result.assert_total,
            assert_coverage_percent=result.assert_coverage_percent,
            detected_issues=detected_issues,
            call_graph_data=call_graph_data,
            started_at=started_at, created_at=submitted_at, duration_seconds=duration_seconds,
        )
        session.add(submission)
        session.flush()
        submission.submission_token = hash_submission_id(submission.id)

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
            assignment_id=assignment.id, student_id=student_id, student_token=student_token,
            submitted_code=submitted_code,
            status=SubmissionStatus.pending_review, score=None, tests_passed=None,
            feedback='Submitted — evaluate_tests assignments are graded manually by your instructor for now.',
            started_at=started_at, created_at=submitted_at, duration_seconds=duration_seconds,
        )
        session.add(submission)
        session.flush()
        submission.submission_token = hash_submission_id(submission.id)

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
