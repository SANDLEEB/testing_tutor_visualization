"""Student Examples (/student/examples) — published worked examples, browsable by everyone.

The intended on-ramp before Practice Questions: source code, a correct example test
suite, its real measured coverage, and an explanation of how and why it works — see
core/example_service.py and pages/example_view_page.py for the detail view.
"""
from nicegui import app, ui

from auth.db import get_session
from auth.gateway import require_course
from core.example_service import list_examples
from core.models import AssignmentLanguage

_LANGUAGE_LABELS = {
    AssignmentLanguage.python: 'Python',
    AssignmentLanguage.cpp: 'C++',
}


def create_student_examples_page():
    course_id = require_course(app.storage.user)
    if course_id is None:
        return

    ui.label('Examples').classes('text-2xl font-bold mb-1')
    ui.label("Worked examples — see how a correct test suite is built and what it covers, "
              "before you write your own.").classes('text-sm text-gray-500 mb-4')

    with get_session() as session:
        examples = list_examples(session, published_only=True, course_id=course_id)

        if not examples:
            ui.label('No examples published yet — check back soon.').classes('text-sm text-gray-400')
            return

        with ui.column().classes('w-full gap-2'):
            for ex in examples:
                cov = f'{ex.total_coverage_percent}%' if ex.total_coverage_percent is not None else '—'
                with ui.row().classes('w-full items-center gap-3 border-b border-gray-100 py-3') \
                        .style('cursor:pointer').on('click', lambda eid=ex.id: ui.navigate.to(f'/examples/{eid}')):
                    ui.label(ex.title).classes('flex-1 text-sm font-medium truncate')
                    ui.label(ex.concept.name).classes('text-xs text-[#0969da] w-32 truncate')
                    ui.label(_LANGUAGE_LABELS[ex.language]).classes('text-xs text-[#0969da] w-20')
                    ui.label(f'{cov} coverage').classes('text-xs text-gray-500 w-28')
                    ui.button('Open →', color='primary').props('flat dense size=sm')
