"""Core Database schema.

Shared substrate for the Content & Animation Service, the AI Question
Authoring Service, the Adaptive Practice Engine, and the Grading &
Dashboard Service. `Concept` is a lightweight pointer into the Knowledge
Graph Engine — the graph itself lives in its own database.
"""
import enum
from datetime import datetime

from sqlalchemy import (
    Boolean, DateTime, Enum, ForeignKey, Integer, JSON, String, Text,
    UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from auth.models import Base


class EnrollmentRole(str, enum.Enum):
    instructor = 'instructor'
    student = 'student'


class AccessScope(str, enum.Enum):
    """Who can see a published Assignment or Question, beyond course membership —
    set per-row by whoever authored it (pages/instructor_assignments_page.py,
    pages/instructor_practice_page.py). `course` is the default and matches every
    prior behavior (anyone enrolled in the course), so nothing already published
    narrows automatically when this column appears.
    """
    course = 'course'      # everyone enrolled in the course — the original, only, behavior
    sections = 'sections'  # only students whose Enrollment.section is in access_sections
    students = 'students'  # only the specific students in the *_access_students join table


class Course(Base):
    __tablename__ = 'courses'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    institution: Mapped[str] = mapped_column(String, nullable=False, default='')
    created_by_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    enrollments: Mapped[list['Enrollment']] = relationship(
        back_populates='course', cascade='all, delete-orphan'
    )
    content_items: Mapped[list['ContentItem']] = relationship(
        back_populates='course', cascade='all, delete-orphan'
    )


class Enrollment(Base):
    __tablename__ = 'enrollments'
    __table_args__ = (UniqueConstraint('course_id', 'user_id', name='uq_enrollment_course_user'),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey('courses.id'), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)
    role: Mapped[EnrollmentRole] = mapped_column(
        Enum(EnrollmentRole), nullable=False, default=EnrollmentRole.student
    )
    # Free-text label (e.g. '01', 'A', 'TR 10am') an instructor assigns per student from
    # the roster page (pages/instructor_students_page.py) — '' means unsectioned. A Course
    # doesn't otherwise model sections at all; this is the only place the concept exists,
    # by design, since one instructor account already owns exactly one Course (see
    # core/course_service.py's module docstring) and "multiple sections" in practice means
    # one roster split into sub-groups, not multiple Course rows.
    section: Mapped[str] = mapped_column(String, nullable=False, default='')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    course: Mapped['Course'] = relationship(back_populates='enrollments')
    user: Mapped['User'] = relationship()


class ContentKind(str, enum.Enum):
    cfg = 'cfg'
    du_chain = 'du_chain'
    dominator = 'dominator'
    call_graph = 'call_graph'
    coverage = 'coverage'


class ContentItem(Base):
    """A code sample plus its precomputed analysis graph."""
    __tablename__ = 'content_items'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_id: Mapped[int | None] = mapped_column(ForeignKey('courses.id'), nullable=True)
    kind: Mapped[ContentKind] = mapped_column(Enum(ContentKind), nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    source_code: Mapped[str] = mapped_column(Text, nullable=False)
    graph_data: Mapped[dict] = mapped_column(JSON, nullable=False)
    published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    course: Mapped['Course | None'] = relationship(back_populates='content_items')
    concepts: Mapped[list['ContentConcept']] = relationship(
        back_populates='content_item', cascade='all, delete-orphan'
    )


class Concept(Base):
    """Pointer into the Knowledge Graph Engine (the graph itself lives in the Knowledge Graph
    Database). Scoped to a Course — every piece of content (material/content item/question/
    assignment) hangs off a Concept via concept_id, so scoping Concept to a course is what
    scopes everything else transitively; nothing else needs its own course_id.
    """
    __tablename__ = 'concepts'
    __table_args__ = (UniqueConstraint('course_id', 'slug', name='uq_concept_course_slug'),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey('courses.id'), nullable=False)
    slug: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default='')

    course: Mapped['Course'] = relationship()


class ContentConcept(Base):
    __tablename__ = 'content_concepts'
    __table_args__ = (UniqueConstraint('content_item_id', 'concept_id', name='uq_content_concept'),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    content_item_id: Mapped[int] = mapped_column(ForeignKey('content_items.id'), nullable=False)
    concept_id: Mapped[int] = mapped_column(ForeignKey('concepts.id'), nullable=False)

    content_item: Mapped['ContentItem'] = relationship(back_populates='concepts')
    concept: Mapped['Concept'] = relationship()


class CourseMaterial(Base):
    """Course material an instructor uploaded (pasted text or a plain-text file) — used
    both as RAG retrieval context and, once published, as student-visible reading
    material on that topic's Topic Hub page (see core/topic_service.py).
    """
    __tablename__ = 'course_materials'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    concept_id: Mapped[int | None] = mapped_column(ForeignKey('concepts.id'), nullable=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    content_text: Mapped[str] = mapped_column(Text, nullable=False)
    published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    uploaded_by_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    concept: Mapped['Concept | None'] = relationship()


class QuestionType(str, enum.Enum):
    node_type = 'node_type'
    du_pair = 'du_pair'
    path_select = 'path_select'
    coverage_count = 'coverage_count'
    multiple_choice = 'multiple_choice'  # free-form — used by AI-authored questions


class QuestionSource(str, enum.Enum):
    manual = 'manual'
    ai_generated = 'ai_generated'          # free-form, from AI Question Authoring chat
    template_generated = 'template_generated'  # node_type/du_pair/path_select/coverage_count, from source code


class Question(Base):
    """Authored by faculty, the AI Question Authoring Service, or generated from source
    code (template_generated); served by the Adaptive Practice Engine.

    content_item_id is nullable because AI-authored questions come from course
    material (CourseMaterial), not necessarily a specific CFG item. graph_data holds
    a template_generated question's own private CFG (not shared with Content Library).
    """
    __tablename__ = 'questions'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    content_item_id: Mapped[int | None] = mapped_column(ForeignKey('content_items.id'), nullable=True)
    material_id: Mapped[int | None] = mapped_column(ForeignKey('course_materials.id'), nullable=True)
    concept_id: Mapped[int | None] = mapped_column(ForeignKey('concepts.id'), nullable=True)
    type: Mapped[QuestionType] = mapped_column(Enum(QuestionType), nullable=False)
    source: Mapped[QuestionSource] = mapped_column(
        Enum(QuestionSource), nullable=False, default=QuestionSource.manual
    )
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    choices: Mapped[list] = mapped_column(JSON, nullable=False)
    answer: Mapped[str] = mapped_column(String, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False, default='')
    hint: Mapped[str] = mapped_column(Text, nullable=False, default='')
    graph_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    difficulty: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # See AccessScope. access_sections is only meaningful when access_scope is 'sections' —
    # a JSON list of section labels (Enrollment.section values); which specific students
    # when access_scope is 'students' lives in QuestionAccessStudent instead, not here.
    access_scope: Mapped[AccessScope] = mapped_column(Enum(AccessScope), nullable=False, default=AccessScope.course)
    access_sections: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    content_item: Mapped['ContentItem | None'] = relationship()
    material: Mapped['CourseMaterial | None'] = relationship()
    concept: Mapped['Concept | None'] = relationship()


class QuestionAccessStudent(Base):
    """One row per (question, student) granted access when Question.access_scope is
    'students' — core/question_service.set_question_access manages these."""
    __tablename__ = 'question_access_students'
    __table_args__ = (UniqueConstraint('question_id', 'student_id', name='uq_question_access_student'),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey('questions.id'), nullable=False)
    student_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)


class QuestionAttempt(Base):
    """A student's answer to a Question — read by the Grading & Dashboard Service."""
    __tablename__ = 'question_attempts'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey('questions.id'), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)
    given_answer: Mapped[str | None] = mapped_column(String, nullable=True)
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    question: Mapped['Question'] = relationship()
    user: Mapped['User'] = relationship()


class AssignmentType(str, enum.Enum):
    write_tests = 'write_tests'        # student writes a test suite from scratch
    evaluate_tests = 'evaluate_tests'  # student assesses the reliability of a given test suite


class FeedbackMode(str, enum.Enum):
    """Which kind(s) of feedback a graded submission gets — set per-assignment by the
    instructor, subject to AppSettings.ai_feedback_enabled as a global admin override.
    """
    test_cases = 'test_cases'  # deterministic diff against the instructor's reference suite
    ai = 'ai'                  # LLM-generated feedback (core/ai_service.generate_assignment_feedback)
    both = 'both'


class AssignmentLanguage(str, enum.Enum):
    python = 'python'  # graded as solution.py, via pytest + coverage.py
    cpp = 'cpp'         # graded as solution.h, via g++ + gcov


class Assignment(Base):
    """A grading assignment: source code plus (for evaluate_tests) a given test suite,
    which may itself have been AI-drafted. `language` picks which sandbox pipeline
    (core/sandbox_service.py) grades submissions with — the student never chooses
    this, it's fixed by whoever authored the assignment.

    course_id scopes the assignment to a course directly (multi-tenancy), independent
    of any topic — an ordinary Assignment is just a program to write a full test suite
    against, covering everything in it, not a single tagged concept. concept_id is
    therefore only set for is_practice rows: Practice Questions still need a Topic so
    they can be picked adaptively by BKT mastery (core/bkt_service.py) the same way
    the old question bank was.

    is_practice distinguishes the two ways a row here reaches students: an ordinary
    graded Assignment (/student/assignments, no topic) vs. a Practice Question
    (adaptively served by topic mastery, hint available) — same authoring form, same
    grading/sandbox pipeline, just a different student-facing surface. hint is only
    meaningful when is_practice is True.
    """
    __tablename__ = 'assignments'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey('courses.id'), nullable=False)
    concept_id: Mapped[int | None] = mapped_column(ForeignKey('concepts.id'), nullable=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default='')
    type: Mapped[AssignmentType] = mapped_column(Enum(AssignmentType), nullable=False)
    language: Mapped[AssignmentLanguage] = mapped_column(
        Enum(AssignmentLanguage), nullable=False, default=AssignmentLanguage.python
    )
    source_code: Mapped[str] = mapped_column(Text, nullable=False)
    given_test_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_practice: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    hint: Mapped[str] = mapped_column(Text, nullable=False, default='')
    feedback_mode: Mapped[FeedbackMode] = mapped_column(
        Enum(FeedbackMode), nullable=False, default=FeedbackMode.test_cases
    )
    # Instructor-controlled, per-assignment: the Detected Issues panel's CFG Playback and
    # Def-Use Chains tabs either step through the graph (particle travel, node-by-node) or
    # jump straight to the same end state instantly. Call Graph tab is unaffected — it was
    # already static. See core/models.FeedbackMode for the analogous test_cases/ai toggle.
    animated_feedback: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # See AccessScope. access_sections is only meaningful when access_scope is 'sections' —
    # a JSON list of section labels (Enrollment.section values); which specific students
    # when access_scope is 'students' lives in AssignmentAccessStudent instead, not here.
    access_scope: Mapped[AccessScope] = mapped_column(Enum(AccessScope), nullable=False, default=AccessScope.course)
    access_sections: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # Deadline — None means no deadline. Blocks new submissions once passed
    # (core/assignment_service.submit_and_grade), it does NOT hide the assignment from
    # view (a student can still read it and see past submissions after the deadline,
    # they just can't submit a new one) — separate from AccessScope, which controls
    # visibility, not submittability. due_at is the default for everyone; a row in
    # AssignmentDueDateOverride for a student's own id, or failing that their section,
    # takes precedence over this (core/assignment_service.resolve_due_at) — the same
    # "most specific wins" resolution AccessScope would use if it had comparable levels.
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    concept: Mapped['Concept | None'] = relationship()


class AssignmentAccessStudent(Base):
    """One row per (assignment, student) granted access when Assignment.access_scope is
    'students' — core/assignment_service.set_assignment_access manages these."""
    __tablename__ = 'assignment_access_students'
    __table_args__ = (UniqueConstraint('assignment_id', 'student_id', name='uq_assignment_access_student'),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assignment_id: Mapped[int] = mapped_column(ForeignKey('assignments.id'), nullable=False)
    student_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)


class AssignmentDueDateOverride(Base):
    """A different deadline than Assignment.due_at for one section or one student —
    core/assignment_service.set_due_date_overrides manages these. Exactly one of
    section/student_id is set per row (enforced in the service layer, not a DB
    constraint): a student-id row beats a section row beats the assignment's own
    due_at, for that student — see resolve_due_at.
    """
    __tablename__ = 'assignment_due_date_overrides'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assignment_id: Mapped[int] = mapped_column(ForeignKey('assignments.id'), nullable=False)
    section: Mapped[str | None] = mapped_column(String, nullable=True)
    student_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AssignmentAttemptStart(Base):
    """Timestamp of a student's most recent visit to an assignment's detail page,
    before they submit — lets a submission report how long that attempt took
    (SubmissionTime-style duration) without asking the student to self-report it.
    Overwritten on every page view, so it always reflects the start of the *current*
    (still-open) attempt.
    """
    __tablename__ = 'assignment_attempt_starts'
    __table_args__ = (UniqueConstraint('assignment_id', 'student_id', name='uq_attempt_start'),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assignment_id: Mapped[int] = mapped_column(ForeignKey('assignments.id'), nullable=False)
    student_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SubmissionStatus(str, enum.Enum):
    graded = 'graded'                # autograded (write_tests)
    pending_review = 'pending_review'  # needs instructor review (evaluate_tests, for now)
    error = 'error'                  # sandbox couldn't run it (timeout, crash, etc.)


class AssignmentSubmission(Base):
    """A student's submission for an Assignment — one row per submit, latest wins.

    Coverage fields mirror standard line/branch coverage-report terminology
    (LinesCovered/Missed, BranchesCovered/Missed, plus the percentages and the
    combined Total* rollups) so a submission can be read the same way a JUnit/
    gcov/coverage.py report would be. condition_coverage_percent has no computation
    behind it yet and is always None: neither coverage.py nor gcov measure per-
    boolean-sub-expression (MC/DC) coverage out of the box, and reporting a number
    under that name without actually measuring it would be misleading — the column
    exists so the schema has a home for it once real instrumentation is built;
    branch coverage is the closest real signal available today.
    method_coverage_percent is real (core/sandbox_service._method_coverage_percent):
    the fraction of the source's own functions invoked at least once.

    started_at/created_at bound the attempt (see AssignmentAttemptStart for how
    started_at is captured); duration_seconds is that span computed once at submit
    time (core/assignment_service.submit_and_grade), rather than recomputed from the
    two timestamps by every reader. concept_feedback is generated internally
    (core/assignment_service.generate_comparison_feedback) by diffing the student's
    coverage against the instructor's own reference test suite — no LLM involved —
    distinct from `feedback` (raw pytest/gcov output).

    student_token/submission_token (core/anonymize.py) are one-way HMAC-SHA256
    pseudonyms — student_token is stable per student (same value across all their
    submissions, for linking without exposing student_id); submission_token is
    unique per row. Neither is reversible back to the underlying id.
    """
    __tablename__ = 'assignment_submissions'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assignment_id: Mapped[int] = mapped_column(ForeignKey('assignments.id'), nullable=False)
    student_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)
    student_token: Mapped[str] = mapped_column(String, nullable=False, index=True)
    # Nullable at the DB level only because it's hashed from this row's own id, which
    # doesn't exist until after the first flush — core/assignment_service.submit_and_grade
    # always sets it immediately after that flush, before the transaction commits, so in
    # practice it's never left null for a real submission.
    submission_token: Mapped[str | None] = mapped_column(String, nullable=True, unique=True, index=True)
    submitted_code: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[SubmissionStatus] = mapped_column(Enum(SubmissionStatus), nullable=False)
    # The two grades a write_tests submission gets — evaluate_tests submissions (pending_review,
    # graded manually) leave all of these None. `score` is the Coverage grade: blended
    # line+branch coverage, 0 if tests_passed is False. test_quality_score is the second
    # grade — same 0-if-failing convention — the unweighted average of the 4 dimension
    # scores right below it (core/test_quality_service.TestQualityBreakdown.overall).
    score: Mapped[float | None] = mapped_column(nullable=True)
    test_quality_score: Mapped[float | None] = mapped_column(nullable=True)
    # Test Quality's breakdown — each dimension is itself an unweighted % of however many
    # of the 7 rule-based heuristics (Assertion Misuse, Happy Path Bias, etc.) fall under
    # it that triggered no issue; see core/test_quality_service._DIMENSIONS for exactly
    # which categories feed which dimension. Computed once at submit time
    # (core/assignment_service.submit_and_grade), not recomputed on each view.
    test_correctness_score: Mapped[float | None] = mapped_column(nullable=True)
    assertion_quality_score: Mapped[float | None] = mapped_column(nullable=True)
    redundancy_score: Mapped[float | None] = mapped_column(nullable=True)
    test_diversity_score: Mapped[float | None] = mapped_column(nullable=True)
    tests_passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    feedback: Mapped[str] = mapped_column(Text, nullable=False, default='')
    concept_feedback: Mapped[str] = mapped_column(Text, nullable=False, default='')
    ai_feedback: Mapped[str] = mapped_column(Text, nullable=False, default='')

    lines_covered: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lines_missed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    branches_covered: Mapped[int | None] = mapped_column(Integer, nullable=True)
    branches_missed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    line_coverage_percent: Mapped[float | None] = mapped_column(nullable=True)
    branch_coverage_percent: Mapped[float | None] = mapped_column(nullable=True)
    total_coverage_percent: Mapped[float | None] = mapped_column(nullable=True)  # blended line+branch

    # Statement coverage — same underlying granularity as line coverage for both
    # coverage.py and gcov, exposed under its own name since it's commonly reported
    # separately. Function/method-level breakdown and assert-statement coverage (did the
    # test suite's own assertions execute) — see core/sandbox_service.py for how each is
    # computed, and GradeResult's docstring for what's real vs. a documented limitation.
    statement_coverage_percent: Mapped[float | None] = mapped_column(nullable=True)
    function_coverage: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # Aggregate of function_coverage above: covered functions / total functions * 100.
    method_coverage_percent: Mapped[float | None] = mapped_column(nullable=True)
    # Not measured — see class docstring. Always None until real MC/DC instrumentation exists.
    condition_coverage_percent: Mapped[float | None] = mapped_column(nullable=True)
    assert_covered: Mapped[int | None] = mapped_column(Integer, nullable=True)
    assert_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    assert_coverage_percent: Mapped[float | None] = mapped_column(nullable=True)

    # Rule-based test-quality findings (core/test_quality_service.py) — categorized
    # weaknesses like "Assertion Misuse" or "Happy Path Bias", computed once at submit
    # time from this same submission's GradeResult, not recomputed on each view.
    detected_issues: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    # Whole-program call graph (Python only — see build_call_graph_view), with
    # covered/uncovered node ids baked in, for the Detected Issues panel's Call
    # Graph tab. Null for C++ and for submissions from before this existed.
    call_graph_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Per-line classification (source line numbers) for the coverage visualization on the
    # student's submission — green/orange/red — and to ground the comparison-based
    # conceptual feedback in the actual lines that were skipped. See
    # core/sandbox_service.py for how each bucket is computed.
    covered_lines: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    partial_lines: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    uncovered_lines: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # (created_at - started_at) in whole seconds, computed once at submit time — None
    # when started_at wasn't captured (e.g. a pre-existing row from before this existed).
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)

    assignment: Mapped['Assignment'] = relationship()
    student: Mapped['User'] = relationship()


class TestSuiteExample(Base):
    """A worked example — source code paired with a correct example test suite, shown to
    students before they're asked to write their own (core/example_service.py). Coverage
    and the CFG are computed once, at authoring time, not per student: this is a static
    reference, not something students submit against. Coverage fields mirror
    AssignmentSubmission's; graph_data mirrors ContentItem's (Python only — CFGBuilder
    doesn't parse C++, so it's left null for cpp examples and the viewer just skips that
    part).
    """
    __tablename__ = 'test_suite_examples'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    concept_id: Mapped[int] = mapped_column(ForeignKey('concepts.id'), nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    language: Mapped[AssignmentLanguage] = mapped_column(Enum(AssignmentLanguage), nullable=False)
    source_code: Mapped[str] = mapped_column(Text, nullable=False)
    test_code: Mapped[str] = mapped_column(Text, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False, default='')
    graph_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    tests_passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    lines_covered: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lines_missed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    branches_covered: Mapped[int | None] = mapped_column(Integer, nullable=True)
    branches_missed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    line_coverage_percent: Mapped[float | None] = mapped_column(nullable=True)
    branch_coverage_percent: Mapped[float | None] = mapped_column(nullable=True)
    total_coverage_percent: Mapped[float | None] = mapped_column(nullable=True)
    covered_lines: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    partial_lines: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    uncovered_lines: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    concept: Mapped['Concept'] = relationship()


class AppSettings(Base):
    """Single-row (id=1) table of admin-controlled, runtime-toggleable global settings —
    distinct from config.py's env-var settings (those need a process restart to change).
    ai_feedback_enabled is the admin's master switch over AI feedback: off means no
    Assignment can produce AI feedback regardless of its own feedback_mode.
    """
    __tablename__ = 'app_settings'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ai_feedback_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ConceptMastery(Base):
    """A student's current Bayesian Knowledge Tracing state for one Concept —
    P(the student has mastered it), updated after every QuestionAttempt tagged
    with that concept. See core/bkt_service.py for the update rule.
    """
    __tablename__ = 'concept_mastery'
    __table_args__ = (UniqueConstraint('user_id', 'concept_id', name='uq_mastery_user_concept'),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)
    concept_id: Mapped[int] = mapped_column(ForeignKey('concepts.id'), nullable=False)
    p_mastery: Mapped[float] = mapped_column(nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped['User'] = relationship()
    concept: Mapped['Concept'] = relationship()
