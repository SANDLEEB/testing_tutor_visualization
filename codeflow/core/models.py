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
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    content_item: Mapped['ContentItem | None'] = relationship()
    material: Mapped['CourseMaterial | None'] = relationship()
    concept: Mapped['Concept | None'] = relationship()


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
    published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    concept: Mapped['Concept | None'] = relationship()


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
    gcov/coverage.py report would be. There's no separate condition-level
    (MC/DC) field: neither coverage.py nor gcov measure per-boolean-sub-expression
    coverage out of the box, and reporting a number under that name without
    actually measuring it would be misleading — branch coverage is the closest
    real signal available for now.

    started_at/created_at give SubmissionTime as a duration (see
    AssignmentAttemptStart for how started_at is captured). concept_feedback is
    generated internally (core/assignment_service.generate_comparison_feedback) by
    diffing the student's coverage against the instructor's own reference test suite
    — no LLM involved — distinct from `feedback` (raw pytest/gcov output).
    """
    __tablename__ = 'assignment_submissions'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assignment_id: Mapped[int] = mapped_column(ForeignKey('assignments.id'), nullable=False)
    student_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)
    submitted_code: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[SubmissionStatus] = mapped_column(Enum(SubmissionStatus), nullable=False)
    score: Mapped[float | None] = mapped_column(nullable=True)
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
