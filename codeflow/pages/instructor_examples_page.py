"""Instructor Examples (/instructor/examples).

Author worked examples — source code paired with a correct example test suite — that
students see before writing their own tests or answering practice questions in the
abstract. Source, test suite, and the explanation can each be drafted with an LLM, then
edited/fixed by the instructor before saving; the compile check on save
(core/sandbox_service.py) catches anything still broken, and coverage + the CFG are
computed for real by actually running the example suite once, at save time.
"""
import asyncio
import html

from nicegui import app, ui

import config
from auth.db import get_session
from auth.gateway import require_course
from core.ai_service import (
    AIServiceError, generate_example_explanation, generate_source_code, generate_test_suite,
)
from core.bkt_service import get_or_create_concept, list_concept_names
from core.example_service import (
    create_example, delete_example, get_example, list_examples, set_published,
)
from core.models import AssignmentLanguage
from core.sandbox_service import check_source_compiles, check_test_suite_compiles

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
    AssignmentLanguage.cpp: 'must `#include "solution.h"` and `#include "testkit.h"`, use TEST()/ASSERT_*()',
}


def create_instructor_examples_page():
    course_id = require_course(app.storage.user)
    if course_id is None:
        return
    user_id = app.storage.user['user_id']

    ui.label('Examples').classes('text-2xl font-bold mb-1')
    ui.label('Worked examples — source code plus a correct example test suite — that students see '
              'before writing tests themselves. Coverage and the graph are measured for real, once, '
              'when you save.').classes('text-sm text-gray-500 mb-4')

    with ui.card().classes('w-full p-4 gap-2 mb-6'):
        ui.label('New Example').classes('font-semibold text-sm text-gray-500 uppercase tracking-wide')
        with get_session() as session:
            existing_topics = list_concept_names(session, course_id)
        topic_select = ui.select(
            existing_topics, label='Topic', with_input=True, new_value_mode='add-unique',
        ).classes('w-full')
        title_input = ui.input(label='Title').classes('w-full')
        language_select = ui.select(_LANGUAGE_LABELS, value=AssignmentLanguage.python, label='Language').classes('w-full')

        source_label = ui.label('').classes('text-xs text-gray-500')
        with ui.row().classes('w-full gap-2 items-end'):
            source_desc_input = ui.input(label='Describe what it should do (for AI generation)').classes('flex-1')
            source_gen_btn = ui.button('Generate Source with AI', icon='auto_awesome').props('outline dense size=sm')
            source_gen_status = ui.label('').classes('text-xs text-gray-400')
            if not config.AI_ENABLED:
                source_gen_status.text = 'Add an AI provider key to codeflow/.env to enable this (see .env.example).'
                source_gen_btn.disable()
        source_input = ui.textarea().classes('w-full font-mono text-xs').props('rows=8 outlined')

        test_label = ui.label('Example test suite').classes('text-xs text-gray-500 mt-2')
        with ui.row().classes('items-center gap-2'):
            test_gen_btn = ui.button('Generate Test Suite with AI', icon='auto_awesome').props('outline dense size=sm')
            test_gen_status = ui.label('').classes('text-xs text-gray-400')
            if not config.AI_ENABLED:
                test_gen_status.text = 'Add an AI provider key to codeflow/.env to enable this (see .env.example).'
                test_gen_btn.disable()
        test_input = ui.textarea().classes('w-full font-mono text-xs').props('rows=8 outlined')

        explanation_label = ui.label('Explanation (shown to students)').classes('text-xs text-gray-500 mt-2')
        with ui.row().classes('items-center gap-2'):
            explain_gen_btn = ui.button('Generate Explanation with AI', icon='auto_awesome').props('outline dense size=sm')
            explain_gen_status = ui.label('').classes('text-xs text-gray-400')
            if not config.AI_ENABLED:
                explain_gen_status.text = 'Add an AI provider key to codeflow/.env to enable this (see .env.example).'
                explain_gen_btn.disable()
        explanation_input = ui.textarea().classes('w-full text-sm').props('rows=6 outlined')
        ui.label("AI-drafted content is a starting point — review and fix it before saving; the "
                  "compile check will catch anything broken, but only you can judge if it's a good example.") \
            .classes('text-xs text-gray-400 -mt-1')

        def update_labels():
            lang = language_select.value
            source_label.text = _SOURCE_LABELS[lang]
            hint = _TEST_HINTS[lang]
            test_label.text = f'Example test suite — {hint}'

        language_select.on_value_change(update_labels)
        update_labels()

        async def generate_source():
            description = source_desc_input.value.strip()
            if not description:
                ui.notify('Describe what the source code should do first.', color='warning')
                return
            source_gen_btn.props('loading')
            try:
                code = await generate_source_code(description, language_select.value.value)
                source_input.set_value(code)
            except AIServiceError as e:
                ui.notify(str(e), color='negative')
            finally:
                source_gen_btn.props(remove='loading')

        source_gen_btn.on_click(generate_source)

        async def generate_test():
            if not source_input.value.strip():
                ui.notify('Add source code first.', color='warning')
                return
            test_gen_btn.props('loading')
            try:
                suite = await generate_test_suite(source_input.value, language_select.value.value)
                test_input.set_value(suite)
            except AIServiceError as e:
                ui.notify(str(e), color='negative')
            finally:
                test_gen_btn.props(remove='loading')

        test_gen_btn.on_click(generate_test)

        async def generate_explanation():
            source = source_input.value.strip()
            test = test_input.value.strip()
            if not source or not test:
                ui.notify('Add source code and a test suite first.', color='warning')
                return
            explain_gen_btn.props('loading')
            try:
                # Ground the explanation in real, measured coverage rather than having the
                # LLM guess — run the same grading pass save-time will run.
                from core.sandbox_service import run_test_suite
                result = await asyncio.to_thread(run_test_suite, language_select.value, source, test)
                text = await generate_example_explanation(
                    source_code=source, test_code=test, language=language_select.value.value,
                    line_coverage_percent=result.line_coverage_percent,
                    branch_coverage_percent=result.branch_coverage_percent,
                    missing_lines=result.uncovered_lines,
                )
                explanation_input.set_value(text.strip())
            except AIServiceError as e:
                ui.notify(str(e), color='negative')
            finally:
                explain_gen_btn.props(remove='loading')

        explain_gen_btn.on_click(generate_explanation)

        error_label = ui.label('').classes('text-red-500 text-xs')
        error_label.set_visibility(False)
        compile_error_box = ui.html('').classes('w-full')
        compile_error_box.set_visibility(False)

        def show_compile_error(heading: str, output: str):
            error_label.set_visibility(False)
            compile_error_box.set_content(
                f'<div style="font-size:11px;font-weight:600;color:#cf222e;margin-bottom:4px">{html.escape(heading)}</div>'
                f'<pre style="white-space:pre-wrap;font-size:11px;background:#0f172a;color:#e2e8f0;'
                f'padding:8px;border-radius:6px;max-height:240px;overflow-y:auto;margin:0">'
                f'{html.escape(output)}</pre>'
            )
            compile_error_box.set_visibility(True)

        create_btn = ui.button('Create Example', color='primary').classes('w-fit mt-2')

        async def create():
            title = title_input.value.strip()
            source = source_input.value.strip()
            test = test_input.value.strip()
            topic = (topic_select.value or '').strip()
            language = language_select.value
            error_label.set_visibility(False)
            compile_error_box.set_visibility(False)
            if not topic:
                error_label.text = 'Name a topic for this example first.'
                error_label.set_visibility(True)
                return
            if not title or not source or not test:
                error_label.text = 'Title, source code, and a test suite are required.'
                error_label.set_visibility(True)
                return

            create_btn.props('loading')
            try:
                source_check = await asyncio.to_thread(check_source_compiles, language, source)
                if not source_check.ok:
                    show_compile_error("Your source code doesn't compile:", source_check.output)
                    return
                test_check = await asyncio.to_thread(check_test_suite_compiles, language, source, test)
                if not test_check.ok:
                    show_compile_error("Your test suite doesn't compile against the source code:", test_check.output)
                    return
            finally:
                create_btn.props(remove='loading')

            with get_session() as session:
                concept = get_or_create_concept(session, topic, course_id)
                create_example(
                    session, concept_id=concept.id, title=title, language=language,
                    source_code=source, test_code=test, explanation=explanation_input.value.strip(),
                    created_by_id=user_id,
                )
                # Deliberately not refreshing topic_select.options here — see the
                # matching comment in instructor_practice_page.py's create() for why.
            title_input.set_value('')
            source_desc_input.set_value('')
            source_input.set_value('')
            test_input.set_value('')
            explanation_input.set_value('')
            ui.notify('Compiled cleanly — created as a draft.', color='positive')
            refresh_list()

        create_btn.on_click(create)

    ui.label('Your Examples').classes('font-semibold text-sm text-gray-500 uppercase tracking-wide')
    list_container = ui.column().classes('w-full gap-2')

    def toggle_published(example_id: int, published: bool):
        with get_session() as session:
            ex = get_example(session, example_id)
            if ex is not None:
                set_published(session, ex, published)
        ui.notify('Published.' if published else 'Unpublished.', color='positive')

    def delete_row(example_id: int):
        with get_session() as session:
            ex = get_example(session, example_id)
            if ex is not None:
                delete_example(session, ex)
        ui.notify('Deleted.', color='warning')
        refresh_list()

    def refresh_list():
        list_container.clear()
        with list_container, get_session() as session:
            examples = list_examples(session, created_by_id=user_id, course_id=course_id)
            if not examples:
                ui.label('No examples yet — create one above.').classes('text-sm text-gray-400')
            for ex in examples:
                cov = f'{ex.total_coverage_percent}%' if ex.total_coverage_percent is not None else '—'
                with ui.row().classes('w-full items-center gap-3 border-b border-gray-100 py-2'):
                    ui.label(ex.title).classes('flex-1 text-sm font-medium truncate')
                    ui.label(ex.concept.name).classes('text-xs text-[#0969da] w-32 truncate')
                    ui.label(_LANGUAGE_LABELS[ex.language]).classes('text-xs text-[#0969da] w-32')
                    ui.label(f'{cov} coverage').classes('text-xs text-gray-500 w-28')
                    ui.switch(
                        'Published', value=ex.published,
                        on_change=lambda e, eid=ex.id: toggle_published(eid, e.value),
                    ).classes('w-36')
                    ui.button('Preview', on_click=lambda eid=ex.id: ui.navigate.to(f'/examples/{eid}')) \
                        .props('flat dense size=sm')
                    ui.button('Delete', on_click=lambda eid=ex.id: delete_row(eid)) \
                        .props('flat dense size=sm color=negative')

    refresh_list()
