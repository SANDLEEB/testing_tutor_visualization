"""Instructor Students roster (/instructor/students) — every student's overall mastery,
practice-question performance, and assignment performance at a glance; click "View" for
their full knowledge graph and grade history at /instructor/students/{id}.

If the course has multiple sections (core/course_service.list_sections — a free-text label
on Enrollment, not a separate model; see its docstring for why), a filter dropdown appears
above the roster to narrow it to one. The section badge next to each student's name is
itself editable, so this page is also where an instructor assigns/changes a student's
section — the filter only ever offers labels someone has actually been given.
"""
from nicegui import app, ui

from auth.db import get_session
from auth.gateway import require_course
from auth.service import set_cwid
from core.bkt_service import mastery_tier
from core.course_service import list_sections, set_student_section
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
             'click View for the full picture.').classes('text-sm text-gray-500 mb-4')

    state = {'section': None}  # None = every section
    filter_row = ui.row().classes('items-center gap-2 mb-3')
    roster_container = ui.column().classes('w-full gap-2')

    def refresh_filter_options():
        with get_session() as session:
            sections = list_sections(session, course_id)
        filter_row.clear()
        if not sections:
            return
        with filter_row:
            ui.label('Section').classes('text-xs text-gray-500')
            options = {None: 'All sections', **{s: s for s in sections}}

            def on_change(e):
                state['section'] = e.value
                refresh_roster()

            ui.select(options, value=state['section'], on_change=on_change) \
                .props('dense outlined').classes('w-48')

    refresh_filter_options()

    def save_section(user_id: int, value: str):
        value = value.strip()
        with get_session() as session:
            set_student_section(session, student_id=user_id, course_id=course_id, section=value)
        ui.notify(f'Section set to "{value}".' if value else 'Section cleared.', color='positive')
        refresh_filter_options()  # a brand-new label needs to show up in the filter too
        refresh_roster()

    def save_cwid(user_id: int, value: str):
        with get_session() as session:
            set_cwid(session, user_id, value)
        ui.notify(f'CWID set to "{value.strip()}".' if value.strip() else 'CWID cleared.', color='positive')

    def refresh_roster():
        roster_container.clear()
        with roster_container, get_session() as session:
            summaries = list_student_summaries(session, course_id, section=state['section'])
            if not summaries:
                with ui.card().classes('w-full p-6 items-center gap-2'):
                    msg = 'No students yet.' if state['section'] is None else 'No students in this section.'
                    ui.label(msg).classes('text-gray-500')
                return
            for s in summaries:
                style = _TIER_STYLE.get(mastery_tier(s.avg_mastery)) if s.avg_mastery is not None else None
                with ui.row().classes('w-full items-center gap-3 bg-white border border-gray-100 rounded-lg px-4 py-3'):
                    with ui.column().classes('flex-1 gap-0 min-w-0'):
                        ui.label(s.name).classes('text-sm font-medium')
                        ui.label(s.email).classes('text-xs text-gray-400')

                    cwid_input = ui.input(value=s.cwid, placeholder='CWID') \
                        .props('dense borderless input-class="text-xs text-center"') \
                        .classes('w-24').tooltip('Campus ID / CWID (click to edit)')
                    cwid_input.on('blur', lambda uid=s.user_id, inp=cwid_input: save_cwid(uid, inp.value))

                    section_input = ui.input(value=s.section, placeholder='Section') \
                        .props('dense borderless input-class="text-xs text-center"') \
                        .classes('w-20').tooltip('Section (click to edit)')
                    section_input.on(
                        'blur', lambda uid=s.user_id, inp=section_input: save_section(uid, inp.value),
                    )

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

                    ui.button(
                        'View →', on_click=lambda uid=s.user_id: ui.navigate.to(f'/instructor/students/{uid}'),
                        color='primary',
                    ).props('flat dense size=sm')

    refresh_roster()
