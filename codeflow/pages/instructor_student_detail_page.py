"""Instructor's view of one student (/instructor/students/{id}) — their full knowledge
graph (per-topic mastery), practice-question attempt history, and assignment submission
history. The per-student counterpart to the class-wide view on the instructor home page.
"""
from nicegui import app, ui

from auth.db import get_session
from auth.gateway import require_course
from auth.service import get_user_by_id
from core.assignment_service import list_submissions_by_student
from core.bkt_service import get_mastery, mastery_tier
from core.course_service import get_enrollment
from core.graph_viz import can_render_radar, render_mastery_radar
from core.question_service import list_attempts_for_user
from core.topic_service import list_topics_with_published_content

_TIER_STYLE = {
    'mastered': {'text': 'text-green-600', 'bar': 'green', 'badge': '✓ Mastered'},
    'partial': {'text': 'text-orange-600', 'bar': 'orange', 'badge': None},
    'weak': {'text': 'text-red-600', 'bar': 'red', 'badge': 'Needs attention'},
}

_HISTORY_LIMIT = 20  # most recent N shown inline — plenty for a classroom-scale roster


def create_instructor_student_detail_page(student_id: int):
    course_id = require_course(app.storage.user)
    if course_id is None:
        return

    with get_session() as session:
        student = get_user_by_id(session, student_id)
        enrollment = get_enrollment(session, student_id)
        if student is None or enrollment is None or enrollment.course_id != course_id:
            ui.label('Student not found in this course.').classes('text-red-500 text-lg m-4')
            return
        name, email = student.full_name, student.email

        knowledge = []
        for t in list_topics_with_published_content(session, course_id):
            if t.question_count == 0 and t.assignment_count == 0:
                continue
            mastery = get_mastery(session, student_id, t.concept.id)
            knowledge.append({'name': t.concept.name, 'mastery': mastery, 'tier': mastery_tier(mastery)})
        knowledge.sort(key=lambda k: k['mastery'])

        attempts = [
            {
                'prompt': a.question.prompt,
                'topic': a.question.concept.name if a.question.concept_id else '—',
                'is_correct': a.is_correct, 'created_at': a.created_at,
            }
            for a in list_attempts_for_user(session, student_id)
        ]

        submissions = [
            {
                'title': sub.assignment.title, 'status': sub.status.value, 'score': sub.score,
                'tests_passed': sub.tests_passed, 'created_at': sub.created_at,
            }
            for sub in list_submissions_by_student(session, student_id)
        ]

    ui.button('← Back to Students', on_click=lambda: ui.navigate.to('/instructor/students')) \
        .props('flat dense size=sm').classes('mb-2')
    ui.label(name).classes('text-2xl font-bold mb-1')
    ui.label(email).classes('text-sm text-gray-500 mb-6')

    # ── Knowledge graph ──────────────────────────────────────────────────
    ui.label('Knowledge Graph').classes('text-lg font-semibold mb-2')
    if not knowledge:
        ui.label('No tracked topics yet.').classes('text-sm text-gray-400 mb-6')
    else:
        if can_render_radar(len(knowledge)):
            with ui.card().classes('w-full items-center py-4 mb-4'):
                ui.html(render_mastery_radar(knowledge))
        with ui.column().classes('w-full gap-2 mb-6'):
            for k in knowledge:
                style = _TIER_STYLE[k['tier']]
                with ui.row().classes('w-full items-center gap-3 bg-white border border-gray-100 rounded-lg px-4 py-3'):
                    ui.label(k['name']).classes(f"flex-1 text-sm font-medium {style['text']}")
                    if style['badge']:
                        ui.label(style['badge']).classes(f"text-xs {style['text']} font-semibold")
                    ui.label(f"{round(k['mastery'] * 100)}%").classes(f"text-sm font-bold {style['text']} w-12 text-right")
                    ui.linear_progress(value=k['mastery'], show_value=False, color=style['bar']).classes('w-32')

    # ── Practice questions ───────────────────────────────────────────────
    ui.label('Practice Questions').classes('text-lg font-semibold mb-2')
    if not attempts:
        ui.label('No attempts yet.').classes('text-sm text-gray-400 mb-6')
    else:
        correct = sum(1 for a in attempts if a['is_correct'])
        ui.label(f'{correct}/{len(attempts)} correct ({round(correct / len(attempts) * 100)}%)') \
            .classes('text-sm text-gray-500 mb-2')
        with ui.column().classes('w-full gap-1 mb-6'):
            for a in attempts[:_HISTORY_LIMIT]:
                color = 'text-green-600' if a['is_correct'] else 'text-red-500'
                with ui.row().classes('w-full items-center gap-3 border-b border-gray-100 py-2'):
                    ui.label(a['prompt']).classes('flex-1 text-sm truncate')
                    ui.label(a['topic']).classes('text-xs text-[#0969da] w-32 truncate')
                    ui.label('✓ Correct' if a['is_correct'] else '✗ Incorrect').classes(f'text-xs font-semibold {color} w-24')
                    ui.label(a['created_at'].strftime('%Y-%m-%d %H:%M')).classes('text-xs text-gray-400 w-32')
            if len(attempts) > _HISTORY_LIMIT:
                ui.label(f'+ {len(attempts) - _HISTORY_LIMIT} earlier attempts').classes('text-xs text-gray-400')

    # ── Assignments ───────────────────────────────────────────────────────
    ui.label('Assignments').classes('text-lg font-semibold mb-2')
    if not submissions:
        ui.label('No submissions yet.').classes('text-sm text-gray-400')
    else:
        with ui.column().classes('w-full gap-1'):
            for sub in submissions[:_HISTORY_LIMIT]:
                score_text = f"{sub['score']:.0f}%" if sub['score'] is not None else '—'
                with ui.row().classes('w-full items-center gap-3 border-b border-gray-100 py-2'):
                    ui.label(sub['title']).classes('flex-1 text-sm truncate')
                    ui.label(sub['status']).classes('text-xs text-gray-500 w-32')
                    ui.label(score_text).classes('text-sm font-semibold w-16')
                    ui.label(sub['created_at'].strftime('%Y-%m-%d %H:%M')).classes('text-xs text-gray-400 w-32')
            if len(submissions) > _HISTORY_LIMIT:
                ui.label(f'+ {len(submissions) - _HISTORY_LIMIT} earlier submissions').classes('text-xs text-gray-400')
