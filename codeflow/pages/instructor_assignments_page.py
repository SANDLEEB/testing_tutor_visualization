"""Instructor Assignments (/instructor/assignments).

Source code plus, depending on type, either nothing more (students write a
test suite from scratch) or a given test suite (students assess its
reliability). Both the source code and the test suite can be drafted with an
LLM (see core/ai_service.py for the supported providers) from a
plain-language description, or uploaded directly as a file, then
edited/fixed by the instructor before saving — the compile check on save
(core/sandbox_service.py) catches anything still broken either way.

Graded assignments only — CFG-grounded Practice Questions live on their own
page (pages/instructor_practice_page.py, /instructor/practice).
"""
import asyncio
import html

from nicegui import app, ui

import config
from auth.db import get_session
from core.ai_service import AIServiceError, generate_source_code, generate_test_suite
from core.assignment_service import (
    create_assignment, delete_assignment, get_assignment, list_assignments,
    list_submissions, set_feedback_mode, set_published,
)
from auth.gateway import require_course
from core.models import AssignmentLanguage, AssignmentType, FeedbackMode
from core.sandbox_service import check_source_compiles, check_test_suite_compiles

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


def create_instructor_assignments_page():
    course_id = require_course(app.storage.user)
    if course_id is None:
        return
    user_id = app.storage.user['user_id']

    ui.label('Assignments').classes('text-2xl font-bold mb-1')
    ui.label('Publish a source file for grading — students either write a test suite for it, '
              'or assess the reliability of one you provide.').classes('text-sm text-gray-500 mb-4')

    # ── New assignment form ──────────────────────────────────────────────
    with ui.card().classes('w-full p-4 gap-2 mb-6'):
        ui.label('New').classes('font-semibold text-sm text-gray-500 uppercase tracking-wide')
        title_input = ui.input(label='Title').classes('w-full')
        desc_input = ui.textarea(label='Instructions for students').classes('w-full').props('rows=3 outlined')
        with ui.row().classes('w-full gap-3'):
            type_select = ui.select(_TYPE_LABELS, value=AssignmentType.write_tests, label='Assignment type') \
                .classes('flex-1')
            language_select = ui.select(_LANGUAGE_LABELS, value=AssignmentLanguage.python, label='Language') \
                .classes('flex-1')
            feedback_mode_select = ui.select(
                _FEEDBACK_MODE_LABELS, value=FeedbackMode.test_cases, label='Feedback',
            ).classes('flex-1')
        if not config.AI_ENABLED:
            ui.label(
                "No AI provider key is configured — 'AI feedback' and 'Both' won't produce "
                "AI feedback until one is added to codeflow/.env (see .env.example)."
            ).classes('text-xs text-gray-400 -mt-1')
        source_label = ui.label('').classes('text-xs text-gray-500')
        with ui.row().classes('w-full gap-2 items-end'):
            source_desc_input = ui.textarea(label='Describe what it should do (for AI generation) — plain English, not code') \
                .classes('flex-1').props('rows=2 outlined placeholder="e.g. A function that validates an email address and returns True/False"')
            source_gen_btn = ui.button('Generate Source with AI', icon='auto_awesome').props('outline dense size=sm')
            source_gen_status = ui.label('').classes('text-xs text-gray-400')
            if not config.AI_ENABLED:
                source_gen_status.text = 'Add an AI provider key to codeflow/.env to enable this (see .env.example).'
                source_gen_btn.disable()
        source_input = ui.textarea().classes('w-full font-mono text-xs') \
            .props('rows=10 outlined placeholder="Actual source code goes here — write it yourself, upload a file below, '
                   'or describe it above and click Generate Source with AI. Not a description."')
        ui.label("AI-drafted code is a starting point, not a finished solution — review and fix it "
                  "before creating the assignment; the compile check on save will catch anything broken.") \
            .classes('text-xs text-gray-400 -mt-1')

        async def _read_upload(e, target: 'ui.textarea', kind: str) -> None:
            try:
                text = await e.file.text()
            except UnicodeDecodeError:
                ui.notify(f'{kind} file must be plain text (utf-8).', color='negative')
                return
            target.set_value(text)
            ui.notify(f'{kind} loaded from {e.file.name}.', color='positive')

        ui.upload(
            label='…or upload a source file', auto_upload=True,
            on_upload=lambda e: _read_upload(e, source_input, 'Source'),
        ).props('flat dense accept=".py,.h,.hpp,.cpp,.txt"').classes('w-full')

        async def generate_source():
            description = source_desc_input.value.strip()
            if not description:
                ui.notify('Describe what the source code should do first.', color='warning')
                return
            source_gen_btn.props('loading')
            create_btn.props('disable')
            try:
                code = await generate_source_code(description, language_select.value.value)
                source_input.set_value(code)
                ui.notify('Source code generated — review it below before creating.', color='positive')
            except AIServiceError as e:
                ui.notify(str(e), color='negative')
            finally:
                source_gen_btn.props(remove='loading')
                create_btn.props(remove='disable')

        source_gen_btn.on_click(generate_source)

        given_wrap = ui.column().classes('w-full gap-1')
        with given_wrap:
            given_label = ui.label('').classes('text-xs text-gray-500')
            given_input = ui.textarea().classes('w-full font-mono text-xs') \
                .props('rows=10 outlined placeholder="Actual test code goes here — write it yourself, upload a file below, '
                       'or click Generate with AI once source code exists above."')
            with ui.row().classes('items-center gap-2'):
                gen_btn = ui.button('Generate with AI', icon='auto_awesome').props('outline dense size=sm')
                gen_status = ui.label('').classes('text-xs text-gray-400')
                if not config.AI_ENABLED:
                    gen_status.text = 'Add an AI provider key to codeflow/.env to enable this (see .env.example).'
                    gen_btn.disable()
            ui.upload(
                label='…or upload a test suite file', auto_upload=True,
                on_upload=lambda e: _read_upload(e, given_input, 'Test suite'),
            ).props('flat dense accept=".py,.cpp,.txt"').classes('w-full')
            feedback_requirement_note = ui.label('').classes('text-xs text-amber-600')

        def update_labels():
            lang = language_select.value
            is_eval = type_select.value == AssignmentType.evaluate_tests
            source_label.text = _SOURCE_LABELS[lang]
            hint = _TEST_HINTS[lang]
            given_label.text = (
                f'Given test suite (students will assess its reliability) — {hint}' if is_eval
                else f"Reference test suite (used to generate comparison feedback — what your suite "
                     f"covers that theirs doesn't) — {hint}"
            )
            # Every feedback_mode (rule-based, AI, or both) is a comparison against THIS
            # suite — without one, submit_and_grade has nothing to compare against and no
            # feedback of either kind gets generated, regardless of which mode is picked.
            feedback_requirement_note.text = (
                "No reference test suite yet — with the current Feedback setting, submissions "
                "won't get any comparison feedback (rule-based or AI) until one is added above."
                if not given_input.value.strip() else ''
            )

        type_select.on_value_change(update_labels)
        language_select.on_value_change(update_labels)
        feedback_mode_select.on_value_change(update_labels)
        given_input.on_value_change(update_labels)
        update_labels()

        async def generate():
            if not source_input.value.strip():
                ui.notify('Add source code first.', color='warning')
                return
            gen_btn.props('loading')
            create_btn.props('disable')
            try:
                suite = await generate_test_suite(source_input.value, language_select.value.value)
                given_input.set_value(suite)
                ui.notify('Test suite generated — review it below before creating.', color='positive')
            except AIServiceError as e:
                ui.notify(str(e), color='negative')
            finally:
                gen_btn.props(remove='loading')
                create_btn.props(remove='disable')

        gen_btn.on_click(generate)

        error_label = ui.label('').classes('text-red-500 text-xs')
        error_label.set_visibility(False)
        compile_error_box = ui.html('').classes('w-full')
        compile_error_box.set_visibility(False)

        def show_compile_error(heading: str, output: str, *, append: bool = False):
            error_label.set_visibility(False)
            block = (
                f'<div style="font-size:11px;font-weight:600;color:#cf222e;margin-bottom:4px">{html.escape(heading)}</div>'
                f'<pre style="white-space:pre-wrap;font-size:11px;background:#0f172a;color:#e2e8f0;'
                f'padding:8px;border-radius:6px;max-height:240px;overflow-y:auto;margin:0">'
                f'{html.escape(output)}</pre>'
            )
            prior = compile_error_box.content if append and compile_error_box.visible else ''
            compile_error_box.set_content(prior + block)
            compile_error_box.set_visibility(True)

        create_btn = ui.button('Create', color='primary').classes('w-fit mt-2')

        async def create():
            title = title_input.value.strip()
            source = source_input.value.strip()
            is_eval = type_select.value == AssignmentType.evaluate_tests
            given = given_input.value.strip() or None
            language = language_select.value
            error_label.set_visibility(False)
            compile_error_box.set_visibility(False)
            if not title or not source:
                error_label.text = 'Title and source code are required.'
                error_label.set_visibility(True)
                return
            if is_eval and not given:
                error_label.text = 'Evaluate-tests assignments need a given test suite.'
                error_label.set_visibility(True)
                return

            create_btn.props('loading')
            compiled_cleanly = True
            compile_error_box.set_content('')
            compile_error_box.set_visibility(False)
            try:
                source_check = await asyncio.to_thread(check_source_compiles, language, source)
                if not source_check.ok:
                    compiled_cleanly = False
                    show_compile_error("Your source code doesn't compile (creating anyway):", source_check.output)
                if given:
                    test_check = await asyncio.to_thread(check_test_suite_compiles, language, source, given)
                    if not test_check.ok:
                        compiled_cleanly = False
                        show_compile_error(
                            "Your test suite doesn't compile against the source code (creating anyway):",
                            test_check.output,
                            append=True,
                        )
            finally:
                create_btn.props(remove='loading')

            with get_session() as session:
                create_assignment(
                    session, title=title, description=desc_input.value.strip(),
                    type=type_select.value, language=language, source_code=source,
                    given_test_code=given, created_by_id=user_id, course_id=course_id,
                    feedback_mode=feedback_mode_select.value,
                )
            title_input.set_value('')
            desc_input.set_value('')
            source_input.set_value('')
            given_input.set_value('')
            feedback_mode_select.set_value(FeedbackMode.test_cases)
            if compiled_cleanly:
                ui.notify('Compiled cleanly — created as a draft.', color='positive')
            else:
                ui.notify('Created as a draft despite compile errors — fix and re-check before publishing.', color='warning')
            refresh_list()

        create_btn.on_click(create)

    # ── List ─────────────────────────────────────────────────────────────
    ui.label('Your Assignments').classes('font-semibold text-sm text-gray-500 uppercase tracking-wide')
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
                            score_text = f'{s.score:.0f}%' if s.score is not None else '—'
                            ui.label(score_text).classes('text-sm font-semibold w-16')
                            ui.label(s.created_at.strftime('%Y-%m-%d %H:%M')).classes('text-xs text-gray-400 w-32')
                        if s.line_coverage_percent is not None or s.branch_coverage_percent is not None:
                            duration = ''
                            if s.started_at is not None:
                                secs = round((s.created_at - s.started_at).total_seconds())
                                duration = f' · Duration {secs // 60}m{secs % 60:02d}s'
                            ui.label(
                                f'Lines {s.lines_covered}/{(s.lines_covered or 0) + (s.lines_missed or 0)} '
                                f'({s.line_coverage_percent}%) · '
                                f'Branches {s.branches_covered}/{(s.branches_covered or 0) + (s.branches_missed or 0)} '
                                f'({s.branch_coverage_percent}%){duration}'
                            ).classes('text-xs text-gray-400')
                        if s.concept_feedback:
                            ui.label(s.concept_feedback).classes('text-xs text-[#0969da] italic')
                        if s.ai_feedback:
                            ui.label(f'AI: {s.ai_feedback}').classes('text-xs text-purple-600 italic')
            ui.button('Close', on_click=submissions_dialog.close).props('flat')
        submissions_dialog.open()

    def refresh_list():
        list_container.clear()
        with list_container, get_session() as session:
            assignments = list_assignments(session, created_by_id=user_id, course_id=course_id, is_practice=False)
            if not assignments:
                ui.label('No assignments yet — create one above.').classes('text-sm text-gray-400')
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
                        'Published', value=a.published,
                        on_change=lambda e, aid=a.id: toggle_published(aid, e.value),
                    ).classes('w-36')
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

    def delete_row(assignment_id: int):
        with get_session() as session:
            a = get_assignment(session, assignment_id)
            if a is not None:
                delete_assignment(session, a)
        ui.notify('Deleted.', color='warning')
        refresh_list()

    refresh_list()
