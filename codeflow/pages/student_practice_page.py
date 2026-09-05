"""Student Practice Questions (/student/practice) — published questions, browsable by everyone."""
from nicegui import app, ui

from auth.db import get_session
from auth.gateway import require_course
from core.models import QuestionType
from core.practice_service import list_practice_questions

_TYPE_LABELS = {
    QuestionType.node_type: 'Node type identification',
    QuestionType.du_pair: 'Def-Use pair',
    QuestionType.path_select: 'Test path selection',
    QuestionType.coverage_count: 'Coverage count',
}


def create_student_practice_page():
    course_id = require_course(app.storage.user)
    if course_id is None:
        return

    ui.label('Practice Questions').classes('text-2xl font-bold mb-1')
    ui.label('Graph-based practice questions published by your instructor.').classes('text-sm text-gray-500 mb-4')

    with ui.card().classes('w-full p-4 mb-4 bg-[#fce4e8]'):
        with ui.row().classes('w-full items-center justify-between'):
            with ui.column().classes('gap-1'):
                ui.label('Adaptive Practice').classes('font-semibold text-[#0969da]')
                ui.label("Let the system pick your next question based on which concepts you're weakest in.") \
                    .classes('text-xs text-[#0969da]')
            ui.button('Start →', on_click=lambda: ui.navigate.to('/student/practice/adaptive'), color='primary')

    with get_session() as session:
        questions = list_practice_questions(session, published_only=True, course_id=course_id)

        if not questions:
            ui.label('No practice questions published yet — check back soon.').classes('text-sm text-gray-400')
            return

        with ui.column().classes('w-full gap-2'):
            for q in questions:
                with ui.row().classes('w-full items-center gap-3 border-b border-gray-100 py-3') \
                        .style('cursor:pointer').on('click', lambda qid=q.id: ui.navigate.to(f'/practice/{qid}')):
                    ui.label(q.prompt).classes('flex-1 text-sm font-medium truncate')
                    ui.label(_TYPE_LABELS[q.type]).classes('text-xs text-gray-500 w-48')
                    ui.button('Open →', color='primary').props('flat dense size=sm')
