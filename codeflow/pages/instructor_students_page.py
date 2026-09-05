"""Instructor Students roster (/instructor/students) — every student's overall mastery,
practice-question performance, and assignment performance at a glance; click through to
/instructor/students/{id} for their full knowledge graph and grade history.
"""
from nicegui import app, ui

from auth.db import get_session
from auth.gateway import require_course
from core.bkt_service import mastery_tier
from core.roster_service import list_student_summaries

# green / orange / red — same scheme used everywhere mastery is shown.
_TIER_STYLE = {
    'mastered': {'text': 'text-green-600'},
    'partial': {'text': 'text-orange-600'},
    'weak': {'text': 'text-red-600'},
}


def create_instructor_students_page():
    course_id = require_course(app.storage.user)
    if course_id is None:
        return

    ui.label('Students').classes('text-2xl font-bold mb-1')
    ui.label("Every student's mastery, practice question performance, and assignment grades — "
             'click a row for the full picture.').classes('text-sm text-gray-500 mb-4')

    with get_session() as session:
        summaries = list_student_summaries(session, course_id)

    if not summaries:
        with ui.card().classes('w-full p-6 items-center gap-2'):
            ui.label('No students yet.').classes('text-gray-500')
        return

    with ui.column().classes('w-full gap-2'):
        for s in summaries:
            style = _TIER_STYLE.get(mastery_tier(s.avg_mastery)) if s.avg_mastery is not None else None
            with ui.row().classes('w-full items-center gap-3 bg-white border border-gray-100 rounded-lg px-4 py-3') \
                    .style('cursor:pointer').on('click', lambda uid=s.user_id: ui.navigate.to(f'/instructor/students/{uid}')):
                with ui.column().classes('flex-1 gap-0'):
                    ui.label(s.name).classes('text-sm font-medium')
                    ui.label(s.email).classes('text-xs text-gray-400')

                if s.avg_mastery is not None:
                    ui.label(f'{round(s.avg_mastery * 100)}% avg mastery').classes(
                        f"text-sm font-semibold {style['text']} w-40")
                else:
                    ui.label('No activity yet').classes('text-sm text-gray-400 w-40')

                q_text = f"{s.questions_correct}/{s.questions_answered} correct ({s.accuracy}%)" \
                    if s.questions_answered else '— questions'
                ui.label(q_text).classes('text-xs text-gray-500 w-48')

                a_text = f"{s.assignments_submitted} submission{'s' if s.assignments_submitted != 1 else ''}"
                if s.avg_assignment_score is not None:
                    a_text += f" · avg {round(s.avg_assignment_score)}%"
                ui.label(a_text).classes('text-xs text-gray-500 w-56')

                ui.button('View →', color='primary').props('flat dense size=sm')
