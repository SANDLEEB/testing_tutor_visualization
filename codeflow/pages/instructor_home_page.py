"""Instructor Mode landing page — leads with what actually needs the instructor's
attention (submissions to review, topics the class is weak on, drafts not yet
published) rather than a card grid that just repeats the sidebar nav.
"""
from nicegui import app, ui

from auth.db import get_session
from auth.gateway import require_course
from core.assignment_service import list_assignments, list_pending_reviews
from core.bkt_service import MASTERY_THRESHOLD, list_class_mastery
from core.content_service import list_content
from core.graph_viz import can_render_radar, render_mastery_radar
from core.material_service import list_materials
from core.models import ContentKind
from core.question_service import list_questions
from core.roster_service import list_student_summaries
from core.topic_service import list_topics

# green / orange / red — same scheme as the student dashboard and the assignment
# coverage view (mastered / partial / weak), applied here to class-wide averages.
_TIER_STYLE = {
    'mastered': {'text': 'text-green-600', 'bar': 'green'},
    'partial': {'text': 'text-orange-600', 'bar': 'orange'},
    'weak': {'text': 'text-red-600', 'bar': 'red'},
}

# (icon, title, route) — kept as a de-emphasized quick-link row at the bottom,
# same treatment as the student home page's quick links.
_QUICK_LINKS = [
    ('📚', 'Topics', '/instructor/topics'),
    ('✎', 'Manage Content', '/instructor/content'),
    ('💬', 'AI Question Authoring', '/instructor/rag'),
    ('📝', 'Assignments', '/instructor/assignments'),
    ('🧩', 'Practice Questions', '/instructor/practice'),
]


def _draft_count(session, user_id: int, course_id: int) -> int:
    materials = list_materials(session, uploaded_by_id=user_id, course_id=course_id)
    content = list_content(session, ContentKind.cfg, created_by_id=user_id, course_id=course_id)
    questions = list_questions(session, created_by_id=user_id, course_id=course_id)
    assignments = list_assignments(session, created_by_id=user_id, course_id=course_id)
    return sum(1 for x in (*materials, *content, *questions, *assignments) if not x.published)


def create_instructor_home_page():
    user = app.storage.user
    course_id = require_course(user)
    if course_id is None:
        return
    user_id = user['user_id']

    with get_session() as session:
        summaries = list_student_summaries(session, course_id)
        student_count = len(summaries)
        topics = list_topics(session, course_id)
        pending = [
            {'title': s.assignment.title, 'email': s.student.email, 'created_at': s.created_at}
            for s in list_pending_reviews(session, course_id=course_id)
        ]
        drafts = _draft_count(session, user_id, course_id)
        class_mastery = [
            {'name': m['concept'].name, 'mastery': m['mastery'], 'student_count': m['student_count'], 'tier': m['tier']}
            for m in list_class_mastery(session, course_id)
        ]
        class_mastery.sort(key=lambda m: m['mastery'])

        struggling_students = sorted(
            (s for s in summaries if s.avg_mastery is not None and s.avg_mastery < MASTERY_THRESHOLD),
            key=lambda s: s.avg_mastery,
        )[:5]

    name = user.get('full_name', '')
    ui.label(f'Welcome back{", " + name if name else ""}').classes('text-3xl font-bold text-[#0969da] mb-1')
    ui.label('Your class, at a glance.').classes('text-gray-500 mb-6')

    # ── Stat strip ───────────────────────────────────────────────────────
    with ui.row().classes('w-full gap-4 mb-6'):
        for label, value in [
            ('Students', str(student_count)),
            ('Topics', str(len(topics))),
            ('Needs review', str(len(pending))),
            ('Drafts unpublished', str(drafts)),
        ]:
            with ui.column().classes('bg-white border border-gray-200 rounded-lg px-4 py-3 flex-1 gap-0'):
                ui.label(label).classes('text-xs text-gray-400 uppercase font-semibold')
                ui.label(value).classes('text-2xl font-bold text-gray-800')

    # ── Needs review ─────────────────────────────────────────────────────
    if pending:
        with ui.row().classes('w-full items-center justify-between mb-2'):
            ui.label('Needs Review').classes('text-lg font-semibold')
            ui.button('Review in Assignments →', on_click=lambda: ui.navigate.to('/instructor/assignments'),
                       color='primary')
        with ui.column().classes('w-full gap-2 mb-6'):
            for s in pending[:5]:
                with ui.row().classes('w-full items-center gap-3 bg-white border border-gray-100 rounded-lg px-4 py-3'):
                    ui.label(s['title']).classes('flex-1 text-sm font-medium')
                    ui.label(s['email']).classes('text-xs text-gray-500')
                    ui.label(s['created_at'].strftime('%Y-%m-%d %H:%M')).classes('text-xs text-gray-400 w-32 text-right')
            if len(pending) > 5:
                ui.label(f'+ {len(pending) - 5} more waiting on review').classes('text-xs text-gray-400')

    # ── Students needing attention ───────────────────────────────────────
    if struggling_students:
        with ui.row().classes('w-full items-center justify-between mb-2'):
            ui.label('Students Needing Attention').classes('text-lg font-semibold')
            ui.button('View all students →', on_click=lambda: ui.navigate.to('/instructor/students')) \
                .props('flat dense')
        with ui.column().classes('w-full gap-2 mb-6'):
            for s in struggling_students:
                with ui.row().classes('w-full items-center gap-3 bg-white border border-gray-100 rounded-lg px-4 py-3') \
                        .style('cursor:pointer').on('click', lambda uid=s.user_id: ui.navigate.to(f'/instructor/students/{uid}')):
                    with ui.column().classes('flex-1 gap-0'):
                        ui.label(s.name).classes('text-sm font-medium text-red-600')
                        ui.label(s.email).classes('text-xs text-gray-400')
                    ui.label(f'{round(s.avg_mastery * 100)}% avg mastery').classes('text-sm font-bold text-red-600 w-40')
                    ui.linear_progress(value=s.avg_mastery, show_value=False, color='red').classes('w-32')
                    ui.button('View →', color='primary').props('flat dense size=sm')

    # ── Class knowledge ──────────────────────────────────────────────────
    with ui.row().classes('w-full items-center justify-between mb-2'):
        ui.label('Class Knowledge').classes('text-lg font-semibold')
        ui.button('View all students →', on_click=lambda: ui.navigate.to('/instructor/students')).props('flat dense')
    if not class_mastery:
        with ui.card().classes('w-full p-6 items-center gap-2 mb-6'):
            ui.label('No student activity yet.').classes('text-gray-500')
            ui.label('Once students attempt questions or assignments, class-wide mastery per topic shows up here.') \
                .classes('text-sm text-gray-400')
    else:
        with ui.row().classes('items-center gap-3 text-[11px] text-gray-500 mb-2'):
            for tier, label in [('mastered', 'Mastered'), ('partial', 'In progress'), ('weak', 'Needs attention')]:
                with ui.row().classes('items-center gap-1'):
                    ui.element('div').style(f'width:10px;height:10px;background:{_TIER_STYLE[tier]["bar"]};border-radius:2px')
                    ui.label(label)
        if can_render_radar(len(class_mastery)):
            with ui.card().classes('w-full items-center py-4 mb-4'):
                ui.html(render_mastery_radar(class_mastery))
        with ui.column().classes('w-full gap-2 mb-6'):
            for m in class_mastery:
                style = _TIER_STYLE[m['tier']]
                with ui.row().classes('w-full items-center gap-3 bg-white border border-gray-100 rounded-lg px-4 py-3'):
                    ui.label(m['name']).classes(f"flex-1 text-sm font-medium {style['text']}")
                    ui.label(f"{m['student_count']} student{'s' if m['student_count'] != 1 else ''}") \
                        .classes('text-xs text-gray-400')
                    ui.label(f"{round(m['mastery'] * 100)}%").classes(f"text-sm font-bold {style['text']} w-12 text-right")
                    ui.linear_progress(value=m['mastery'], show_value=False, color=style['bar']).classes('w-32')

    # ── Quick links ──────────────────────────────────────────────────────
    ui.separator().classes('my-4')
    with ui.row().classes('gap-3'):
        for icon, label, route in _QUICK_LINKS:
            ui.button(f'{icon}  {label}', on_click=lambda r=route: ui.navigate.to(r)).props('outline')
