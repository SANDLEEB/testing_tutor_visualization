"""Instructor Assignments (/instructor/assignments).

Source code plus, depending on type, either nothing more (students write a
test suite from scratch) or a given test suite (students assess its
reliability). Both the source code and the test suite can be drafted with an
LLM (see core/ai_service.py for the supported providers) from a
plain-language description, or uploaded directly as a file, then
edited/fixed by the instructor before saving — the compile check on save
(core/sandbox_service.py) catches anything still broken either way.

Two tabs: "Your Assignments" (draft/published assignments already created —
the default view) and "New Assignment" (the creation form). Creating an
assignment switches back to "Your Assignments" so the new draft is visible
immediately. Existing assignments are fully editable at any time — title,
instructions, type, language, source, and reference tests — via the Edit
dialog on each row; _AssignmentFields below is the single implementation of
that editor, shared by both the New Assignment form and the Edit dialog so
the two can't drift out of sync.

Graded assignments only — CFG-grounded Practice Questions live on their own
page (pages/instructor_practice_page.py, /instructor/practice).
"""
import asyncio
import html
from collections.abc import Callable

from nicegui import app, ui

import config
from auth.db import get_session
from core.ai_service import AIServiceError, generate_source_code, generate_test_suite
from core.assignment_service import (
    create_assignment, delete_assignment, get_assignment, get_assignment_access_student_ids,
    list_assignments, list_due_date_overrides, list_submissions, set_animated_feedback, set_assignment_access,
    set_assignment_due_date, set_due_date_overrides, set_feedback_mode, set_published, update_assignment,
)
from auth.gateway import require_course
from core.course_service import list_sections, list_students, student_picker_label
from core.models import AccessScope, AssignmentLanguage, AssignmentType, FeedbackMode
from core.sandbox_service import check_source_compiles, check_test_suite_compiles
from pages.access_fields import AccessFields, access_summary
from pages.due_date_fields import DueDateFields

_TYPE_LABELS = {
    AssignmentType.write_tests: 'Write tests from scratch',
    AssignmentType.evaluate_tests: 'Evaluate a given test suite',
}

_FEEDBACK_MODE_LABELS = {
    FeedbackMode.test_cases: 'Instructor test cases',
    FeedbackMode.ai: 'AI feedback',
    FeedbackMode.both: 'Both',
}

_LANGUAGE_LABELS = {
    AssignmentLanguage.python: 'Python (pytest)',
    AssignmentLanguage.cpp: 'C++ (g++ / testkit.h)',
}

_SOURCE_LABELS = {
    AssignmentLanguage.python: 'Source code (solution.py)',
    AssignmentLanguage.cpp: 'Solution header (solution.h — full function definitions, header-only)',
}

_TEST_HINTS = {
    AssignmentLanguage.python: 'must `from solution import ...`',
    AssignmentLanguage.cpp: (
        'must `#include "solution.h"`, then either `#include "testkit.h"` and use '
        'TEST()/ASSERT_*(), or `#include <gtest/gtest.h>` and write standard Google Test'
    ),
}

def _assignment_access_summary(session, assignment) -> str:
    n_students = (
        len(get_assignment_access_student_ids(session, assignment.id))
        if assignment.access_scope == AccessScope.students else 0
    )
    return access_summary(
        access_scope=assignment.access_scope, access_sections=assignment.access_sections,
        access_student_count=n_students,
    )


def _due_summary(session, assignment) -> str:
    base = assignment.due_at.strftime('%b %d, %I:%M %p') if assignment.due_at else 'No deadline'
    n_overrides = len(list_due_date_overrides(session, assignment.id))
    return f'{base} (+{n_overrides} override{"s" if n_overrides != 1 else ""})' if n_overrides else base


class _AssignmentFields:
    """Title / instructions / type / language / source / reference-tests / access editor —
    everything an Assignment needs except feedback source and playback (those have their
    own always-visible controls: grouped separately on the New Assignment form, and
    editable straight from the list row for existing ones, so they aren't duplicated
    here). Used identically by the New Assignment form and the Edit dialog: build it, let
    the instructor fill it in, call `read()` to validate, `compile_check()` before saving,
    and `reset()` after a successful create.
    """

    def __init__(
        self, *, title: str = '', description: str = '', type: AssignmentType = AssignmentType.write_tests,
        language: AssignmentLanguage = AssignmentLanguage.python, source_code: str = '', given_test_code: str = '',
        after_type_language: Callable[[], None] | None = None,
        course_sections: list[str] = (), course_students: list[tuple[int, str]] = (),
        access_scope: AccessScope = AccessScope.course, access_sections: list[str] = (),
        access_student_ids: list[int] = (),
        due_at=None, due_date_overrides: list[dict] = (),
    ):
        self.title_input = ui.input(label='Title', value=title).classes('w-full')
        self.desc_input = ui.textarea(label='Instructions for students', value=description) \
            .classes('w-full').props('rows=3 outlined')
        with ui.row().classes('w-full gap-3'):
            self.type_select = ui.select(_TYPE_LABELS, value=type, label='Assignment type').classes('flex-1')
            self.language_select = ui.select(_LANGUAGE_LABELS, value=language, label='Language').classes('flex-1')

        if after_type_language is not None:
            after_type_language()

        self.access = AccessFields(
            course_sections=course_sections, course_students=course_students,
            access_scope=access_scope, access_sections=access_sections, access_student_ids=access_student_ids,
        )
        self.due_date = DueDateFields(
            course_sections=course_sections, course_students=course_students,
            due_at=due_at, overrides=due_date_overrides,
        )

        self.source_label = ui.label('').classes('text-xs text-gray-500 mt-1')
        with ui.row().classes('w-full gap-2 items-end'):
            self.source_desc_input = ui.textarea(
                label='Describe what it should do (for AI generation) — plain English, not code',
            ).classes('flex-1').props(
                'rows=2 outlined placeholder="e.g. A function that validates an email address and returns True/False"'
            )
            self.source_gen_btn = ui.button('Generate Source with AI', icon='auto_awesome').props('outline dense size=sm')
            self.source_gen_status = ui.label('').classes('text-xs text-gray-400')
            if not config.AI_ENABLED:
                self.source_gen_status.text = 'Add an AI provider key to codeflow/.env to enable this (see .env.example).'
                self.source_gen_btn.disable()
        self.source_input = ui.textarea(value=source_code).classes('w-full font-mono text-xs') \
            .props('rows=10 outlined placeholder="Actual source code goes here — write it yourself, upload a file below, '
                   'or describe it above and click Generate Source with AI. Not a description."')
        ui.label("AI-drafted code is a starting point, not a finished solution — review and fix it "
                  "before saving; the compile check will catch anything broken.") \
            .classes('text-xs text-gray-400 -mt-1')

        ui.upload(
            label='…or upload a source file', auto_upload=True,
            on_upload=lambda e: self._read_upload(e, self.source_input, 'Source'),
        ).props('flat dense accept=".py,.h,.hpp,.cpp,.txt"').classes('w-full')

        self.given_label = ui.label('').classes('text-xs text-gray-500 mt-2')
        self.given_input = ui.textarea(value=given_test_code).classes('w-full font-mono text-xs') \
            .props('rows=10 outlined placeholder="Actual test code goes here — write it yourself, upload a file below, '
                   'or click Generate with AI once source code exists above."')
        with ui.row().classes('items-center gap-2'):
            self.gen_btn = ui.button('Generate with AI', icon='auto_awesome').props('outline dense size=sm')
            self.gen_status = ui.label('').classes('text-xs text-gray-400')
            if not config.AI_ENABLED:
                self.gen_status.text = 'Add an AI provider key to codeflow/.env to enable this (see .env.example).'
                self.gen_btn.disable()
        ui.upload(
            label='…or upload a test suite file', auto_upload=True,
            on_upload=lambda e: self._read_upload(e, self.given_input, 'Test suite'),
        ).props('flat dense accept=".py,.cpp,.txt"').classes('w-full')
        self.feedback_requirement_note = ui.label('').classes('text-xs text-amber-600')

        self.error_label = ui.label('').classes('text-red-500 text-xs')
        self.error_label.set_visibility(False)
        self.compile_error_box = ui.html('').classes('w-full')
        self.compile_error_box.set_visibility(False)

        self.type_select.on_value_change(self._update_labels)
        self.language_select.on_value_change(self._update_labels)
        self.given_input.on_value_change(self._update_labels)
        self._update_labels()

        self.source_gen_btn.on_click(self._generate_source)
        self.gen_btn.on_click(self._generate_tests)

    async def _read_upload(self, e, target: 'ui.textarea', kind: str) -> None:
        try:
            text = await e.file.text()
        except UnicodeDecodeError:
            ui.notify(f'{kind} file must be plain text (utf-8).', color='negative')
            return
        target.set_value(text)
        ui.notify(f'{kind} loaded from {e.file.name}.', color='positive')

    def _update_labels(self) -> None:
        lang = self.language_select.value
        is_eval = self.type_select.value == AssignmentType.evaluate_tests
        self.source_label.text = _SOURCE_LABELS[lang]
        hint = _TEST_HINTS[lang]
        self.given_label.text = (
            f'Given test suite (students will assess its reliability) — {hint}' if is_eval
            else f"Reference test suite (used to generate comparison feedback — what your suite "
                 f"covers that theirs doesn't) — {hint}"
        )
        # Every feedback_mode (rule-based, AI, or both) is a comparison against THIS
        # suite — without one, submit_and_grade has nothing to compare against and no
        # feedback of either kind gets generated, regardless of which mode is picked.
        self.feedback_requirement_note.text = (
            "No reference test suite yet — submissions won't get any comparison feedback "
            "(rule-based or AI) until one is added above."
            if not self.given_input.value.strip() else ''
        )

    async def _generate_source(self) -> None:
        description = self.source_desc_input.value.strip()
        if not description:
            ui.notify('Describe what the source code should do first.', color='warning')
            return
        self.source_gen_btn.props('loading')
        try:
            code = await generate_source_code(description, self.language_select.value.value)
            self.source_input.set_value(code)
            ui.notify('Source code generated — review it below before saving.', color='positive')
        except AIServiceError as e:
            ui.notify(str(e), color='negative')
        finally:
            self.source_gen_btn.props(remove='loading')

    async def _generate_tests(self) -> None:
        if not self.source_input.value.strip():
            ui.notify('Add source code first.', color='warning')
            return
        self.gen_btn.props('loading')
        try:
            suite = await generate_test_suite(self.source_input.value, self.language_select.value.value)
            self.given_input.set_value(suite)
            ui.notify('Test suite generated — review it below before saving.', color='positive')
        except AIServiceError as e:
            ui.notify(str(e), color='negative')
        finally:
            self.gen_btn.props(remove='loading')

    def show_compile_error(self, heading: str, output: str, *, append: bool = False) -> None:
        self.error_label.set_visibility(False)
        block = (
            f'<div style="font-size:11px;font-weight:600;color:#cf222e;margin-bottom:4px">{html.escape(heading)}</div>'
            f'<pre style="white-space:pre-wrap;font-size:11px;background:#0f172a;color:#e2e8f0;'
            f'padding:8px;border-radius:6px;max-height:240px;overflow-y:auto;margin:0">'
            f'{html.escape(output)}</pre>'
        )
        prior = self.compile_error_box.content if append and self.compile_error_box.visible else ''
        self.compile_error_box.set_content(prior + block)
        self.compile_error_box.set_visibility(True)

    def read(self) -> dict | None:
        """Validates required fields; returns the cleaned values, or None (with
        error_label shown) if something's missing."""
        title = self.title_input.value.strip()
        source = self.source_input.value.strip()
        is_eval = self.type_select.value == AssignmentType.evaluate_tests
        given = self.given_input.value.strip() or None
        self.error_label.set_visibility(False)
        self.compile_error_box.set_visibility(False)
        if not title or not source:
            self.error_label.text = 'Title and source code are required.'
            self.error_label.set_visibility(True)
            return None
        if is_eval and not given:
            self.error_label.text = 'Evaluate-tests assignments need a given test suite.'
            self.error_label.set_visibility(True)
            return None
        return {
            'title': title, 'description': self.desc_input.value.strip(),
            'type': self.type_select.value, 'language': self.language_select.value,
            'source_code': source, 'given_test_code': given,
            **self.access.read(), **self.due_date.read(),
        }

    async def compile_check(self) -> bool:
        """Runs the compile checks against the current field values, showing any
        errors inline. Returns True if everything compiled cleanly — callers save
        either way (the check surfaces problems, it doesn't block saving)."""
        language = self.language_select.value
        source = self.source_input.value.strip()
        given = self.given_input.value.strip() or None
        self.compile_error_box.set_content('')
        self.compile_error_box.set_visibility(False)
        compiled_cleanly = True
        source_check = await asyncio.to_thread(check_source_compiles, language, source)
        if not source_check.ok:
            compiled_cleanly = False
            self.show_compile_error("Your source code doesn't compile (saving anyway):", source_check.output)
        if given:
            test_check = await asyncio.to_thread(check_test_suite_compiles, language, source, given)
            if not test_check.ok:
                compiled_cleanly = False
                self.show_compile_error(
                    "Your test suite doesn't compile against the source code (saving anyway):",
                    test_check.output, append=True,
                )
        return compiled_cleanly

    def reset(self) -> None:
        self.title_input.set_value('')
        self.desc_input.set_value('')
        self.source_input.set_value('')
        self.given_input.set_value('')
        self.access.reset()
        self.due_date.reset()


def create_instructor_assignments_page():
    course_id = require_course(app.storage.user)
    if course_id is None:
        return
    user_id = app.storage.user['user_id']

    ui.label('Assignments').classes('text-2xl font-bold mb-1')
    ui.label('Publish a source file for grading — students either write a test suite for it, '
              'or assess the reliability of one you provide.').classes('text-sm text-gray-500 mb-4')

    with get_session() as session:
        course_sections = list_sections(session, course_id)
        course_students = [(s.id, student_picker_label(s)) for s in list_students(session, course_id)]

    with ui.tabs().classes('w-full') as tabs:
        list_tab = ui.tab('list', label='Your Assignments')
        new_tab = ui.tab('new', label='New Assignment')

    with ui.tab_panels(tabs, value=list_tab).classes('w-full p-0'):

        # ── New assignment form ──────────────────────────────────────────
        with ui.tab_panel(new_tab).classes('p-0'):
            with ui.card().classes('w-full p-4 gap-2'):
                feedback_mode_select = None
                animated_switch = None

                # Two independent controls: which feedback SOURCE(S) a submission gets
                # (instructor test cases, AI, or both), and separately, whether the CFG/
                # Def-Use graph feedback plays back animated or jumps straight to the
                # result. Neither implies the other — kept as visually distinct groups.
                # Only shown here: an existing assignment controls both from its list row.
                def _feedback_controls():
                    nonlocal feedback_mode_select, animated_switch
                    with ui.row().classes('w-full gap-8 items-start border border-gray-200 rounded p-3 mt-1'):
                        with ui.column().classes('gap-1'):
                            ui.label('Feedback source').classes('text-xs font-semibold text-gray-500 uppercase tracking-wide')
                            feedback_mode_select = ui.select(
                                _FEEDBACK_MODE_LABELS, value=FeedbackMode.test_cases,
                            ).props('dense outlined').classes('w-56')
                            ui.label('Instructor test cases, AI-generated feedback, or both.') \
                                .classes('text-[11px] text-gray-400')
                            if not config.AI_ENABLED:
                                ui.label(
                                    "No AI provider key is configured — 'AI feedback' and 'Both' won't "
                                    "produce AI feedback until one is added to codeflow/.env (see .env.example)."
                                ).classes('text-[11px] text-gray-400')
                        with ui.column().classes('gap-1'):
                            ui.label('Playback').classes('text-xs font-semibold text-gray-500 uppercase tracking-wide')
                            animated_switch = ui.switch('Animated', value=True)
                            ui.label(
                                "On: the Detected Issues panel's CFG Playback and Def-Use Chains tabs step "
                                "through the graph node-by-node. Off: they jump straight to the same result."
                            ).classes('text-[11px] text-gray-400').style('max-width:260px')

                fields = _AssignmentFields(
                    after_type_language=_feedback_controls,
                    course_sections=course_sections, course_students=course_students,
                )

                create_btn = ui.button('Create', color='primary').classes('w-fit mt-2')

                async def create():
                    values = fields.read()
                    if values is None:
                        return
                    create_btn.props('loading')
                    try:
                        compiled_cleanly = await fields.compile_check()
                    finally:
                        create_btn.props(remove='loading')

                    with get_session() as session:
                        new_assignment = create_assignment(
                            session, title=values['title'], description=values['description'],
                            type=values['type'], language=values['language'], source_code=values['source_code'],
                            given_test_code=values['given_test_code'], created_by_id=user_id, course_id=course_id,
                            feedback_mode=feedback_mode_select.value, animated_feedback=animated_switch.value,
                        )
                        set_assignment_access(
                            session, new_assignment, scope=values['access_scope'],
                            sections=values['access_sections'], student_ids=values['access_student_ids'],
                        )
                        set_assignment_due_date(session, new_assignment, values['due_at'])
                        set_due_date_overrides(session, new_assignment, values['due_date_overrides'])
                    fields.reset()
                    feedback_mode_select.set_value(FeedbackMode.test_cases)
                    animated_switch.set_value(True)
                    if compiled_cleanly:
                        ui.notify('Compiled cleanly — created as a draft.', color='positive')
                    else:
                        ui.notify('Created as a draft despite compile errors — fix and re-check before publishing.', color='warning')
                    refresh_list()
                    tabs.set_value(list_tab)

                create_btn.on_click(create)

        # ── Your Assignments ───────────────────────────────────────────────
        with ui.tab_panel(list_tab).classes('p-0'):
            with ui.row().classes('w-full items-center justify-between mb-2'):
                ui.label('Draft and published assignments — students only see the published ones.') \
                    .classes('text-xs text-gray-400')
                ui.button('New Assignment', icon='add', on_click=lambda: tabs.set_value(new_tab)) \
                    .props('outline dense size=sm')
            with ui.row().classes('w-full items-center gap-3 text-[11px] text-gray-400 uppercase font-semibold px-2'):
                ui.label('Title').classes('flex-1')
                ui.label('Language').classes('w-36')
                ui.label('Type').classes('w-48')
                ui.label('Feedback source').classes('w-40')
                ui.label('Playback').classes('w-28')
                ui.label('Access').classes('w-32')
                ui.label('Due').classes('w-36')
                ui.label('Published').classes('w-36')
                ui.label('').classes('w-56')
            list_container = ui.column().classes('w-full gap-2')

    submissions_dialog = ui.dialog()

    def open_submissions(assignment_id: int, title: str):
        submissions_dialog.clear()
        with submissions_dialog, ui.card().classes('w-full max-w-4xl p-4 gap-2'):
            ui.label(f'Submissions — {title}').classes('font-semibold text-lg mb-2')
            with get_session() as session:
                subs = list_submissions(session, assignment_id)
                if not subs:
                    ui.label('No submissions yet.').classes('text-sm text-gray-400')
                for s in subs:
                    with ui.column().classes('w-full gap-1 border-b border-gray-100 py-2'):
                        with ui.row().classes('w-full items-center gap-3'):
                            ui.label(s.student.email).classes('flex-1 text-sm truncate')
                            ui.label(s.status.value).classes('text-xs text-gray-500 w-32')
                            cov_text = f'Cov {s.score:.0f}%' if s.score is not None else 'Cov —'
                            qual_text = f'Qual {s.test_quality_score:.0f}%' if s.test_quality_score is not None else 'Qual —'
                            ui.label(cov_text).classes('text-sm font-semibold w-24')
                            ui.label(qual_text).classes('text-sm font-semibold w-24')
                            ui.label(s.created_at.strftime('%Y-%m-%d %H:%M')).classes('text-xs text-gray-400 w-32')
                        if s.line_coverage_percent is not None or s.branch_coverage_percent is not None:
                            duration = ''
                            if s.duration_seconds is not None:
                                duration = f' · Duration {s.duration_seconds // 60}m{s.duration_seconds % 60:02d}s'
                            method = f' · Methods {s.method_coverage_percent}%' if s.method_coverage_percent is not None else ''
                            ui.label(
                                f'Lines {s.lines_covered}/{(s.lines_covered or 0) + (s.lines_missed or 0)} '
                                f'({s.line_coverage_percent}%) · '
                                f'Branches {s.branches_covered}/{(s.branches_covered or 0) + (s.branches_missed or 0)} '
                                f'({s.branch_coverage_percent}%){method}{duration}'
                            ).classes('text-xs text-gray-400')
                        if s.test_quality_score is not None:
                            ui.label(
                                f'Quality breakdown — Correctness {s.test_correctness_score:.0f}% · '
                                f'Assertions {s.assertion_quality_score:.0f}% · '
                                f'Redundancy {s.redundancy_score:.0f}% · '
                                f'Diversity {s.test_diversity_score:.0f}%'
                            ).classes('text-xs text-gray-400')
                        if s.concept_feedback:
                            ui.label(s.concept_feedback).classes('text-xs text-[#0969da] italic')
                        if s.ai_feedback:
                            ui.label(f'AI: {s.ai_feedback}').classes('text-xs text-purple-600 italic')
            ui.button('Close', on_click=submissions_dialog.close).props('flat')
        submissions_dialog.open()

    # ── Edit — every field is editable at any time, draft or published. Type and
    # language changes only affect grading going forward (existing submissions
    # already graded against the old pipeline aren't retroactively regraded).
    edit_dialog = ui.dialog()

    def open_edit_dialog(assignment_id: int):
        with get_session() as session:
            a = get_assignment(session, assignment_id)
            if a is None:
                return
            title, description, atype, alang = a.title, a.description, a.type, a.language
            source_code, given_test_code, hint = a.source_code, a.given_test_code, a.hint
            access_scope, access_sections = a.access_scope, list(a.access_sections or [])
            access_student_ids = get_assignment_access_student_ids(session, assignment_id)
            due_at = a.due_at
            due_date_overrides = [
                {'section': o.section, 'student_id': o.student_id, 'due_at': o.due_at}
                for o in list_due_date_overrides(session, assignment_id)
            ]
            submission_count = len(list_submissions(session, assignment_id))

        edit_dialog.clear()
        with edit_dialog, ui.card().classes('w-full max-w-3xl p-4 gap-2'):
            ui.label(f'Edit — {title}').classes('font-semibold text-lg mb-1')
            if submission_count:
                ui.label(
                    f"{submission_count} existing submission{'s' if submission_count != 1 else ''} won't be "
                    "regraded — changes here only affect what students see and submit against going forward."
                ).classes('text-xs text-amber-600')

            fields = _AssignmentFields(
                title=title, description=description, type=atype, language=alang,
                source_code=source_code, given_test_code=given_test_code or '',
                course_sections=course_sections, course_students=course_students,
                access_scope=access_scope, access_sections=access_sections, access_student_ids=access_student_ids,
                due_at=due_at, due_date_overrides=due_date_overrides,
            )

            with ui.row().classes('gap-2 mt-2'):
                save_btn = ui.button('Save', color='primary')
                ui.button('Cancel', on_click=edit_dialog.close).props('flat')

            async def save():
                values = fields.read()
                if values is None:
                    return
                save_btn.props('loading')
                try:
                    compiled_cleanly = await fields.compile_check()
                finally:
                    save_btn.props(remove='loading')

                with get_session() as session:
                    a2 = get_assignment(session, assignment_id)
                    if a2 is not None:
                        update_assignment(
                            session, a2, title=values['title'], description=values['description'],
                            type=values['type'], language=values['language'],
                            source_code=values['source_code'], given_test_code=values['given_test_code'],
                            hint=hint,
                        )
                        set_assignment_access(
                            session, a2, scope=values['access_scope'],
                            sections=values['access_sections'], student_ids=values['access_student_ids'],
                        )
                        set_assignment_due_date(session, a2, values['due_at'])
                        set_due_date_overrides(session, a2, values['due_date_overrides'])
                edit_dialog.close()
                refresh_list()
                ui.notify(
                    'Saved.' if compiled_cleanly else 'Saved despite compile errors — fix and re-check.',
                    color='positive' if compiled_cleanly else 'warning',
                )

            save_btn.on_click(save)
        edit_dialog.open()

    def refresh_list():
        list_container.clear()
        with list_container, get_session() as session:
            assignments = list_assignments(session, created_by_id=user_id, course_id=course_id, is_practice=False)
            if not assignments:
                ui.label("No assignments yet — create one in the New Assignment tab.").classes('text-sm text-gray-400')
            for a in assignments:
                with ui.row().classes('w-full items-center gap-3 border-b border-gray-100 py-2'):
                    ui.label(a.title).classes('flex-1 text-sm font-medium truncate')
                    ui.label(_LANGUAGE_LABELS[a.language]).classes('text-xs text-[#0969da] w-36')
                    ui.label(_TYPE_LABELS[a.type]).classes('text-xs text-gray-500 w-48')
                    ui.select(
                        _FEEDBACK_MODE_LABELS, value=a.feedback_mode,
                        on_change=lambda e, aid=a.id: change_feedback_mode(aid, e.value),
                    ).props('dense').classes('w-40')
                    ui.switch(
                        'Animated', value=a.animated_feedback,
                        on_change=lambda e, aid=a.id: change_animated_feedback(aid, e.value),
                    ).classes('w-28')
                    ui.label(_assignment_access_summary(session, a)).classes('text-xs text-gray-500 w-32')
                    ui.label(_due_summary(session, a)).classes('text-xs text-gray-500 w-36')
                    ui.switch(
                        'Published', value=a.published,
                        on_change=lambda e, aid=a.id: toggle_published(aid, e.value),
                    ).classes('w-36')
                    with ui.row().classes('w-56 gap-1 justify-end'):
                        ui.button('Edit', on_click=lambda aid=a.id: open_edit_dialog(aid)) \
                            .props('flat dense size=sm')
                        ui.button('Submissions', on_click=lambda aid=a.id, t=a.title: open_submissions(aid, t)) \
                            .props('flat dense size=sm')
                        ui.button('Delete', on_click=lambda aid=a.id: delete_row(aid)) \
                            .props('flat dense size=sm color=negative')

    def toggle_published(assignment_id: int, published: bool):
        with get_session() as session:
            a = get_assignment(session, assignment_id)
            if a is not None:
                set_published(session, a, published)
        ui.notify('Published.' if published else 'Unpublished.', color='positive')

    def change_feedback_mode(assignment_id: int, mode: FeedbackMode):
        with get_session() as session:
            a = get_assignment(session, assignment_id)
            if a is not None:
                set_feedback_mode(session, a, mode)
        ui.notify(f'Feedback mode set to {_FEEDBACK_MODE_LABELS[mode]}.', color='positive')

    def change_animated_feedback(assignment_id: int, enabled: bool):
        with get_session() as session:
            a = get_assignment(session, assignment_id)
            if a is not None:
                set_animated_feedback(session, a, enabled)
        ui.notify('Feedback set to animated.' if enabled else 'Feedback set to static.', color='positive')

    def delete_row(assignment_id: int):
        with get_session() as session:
            a = get_assignment(session, assignment_id)
            if a is not None:
                delete_assignment(session, a)
        ui.notify('Deleted.', color='warning')
        refresh_list()

    refresh_list()
