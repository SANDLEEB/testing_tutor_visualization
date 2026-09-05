"""Student Mode landing page — leads with the student's own knowledge state
(per-topic mastery from the Adaptive Practice Engine's BKT model), not a nav grid.
"""
from nicegui import app, ui

from auth.db import get_session
from auth.gateway import require_course
from core.bkt_service import MASTERY_THRESHOLD, get_mastery, mastery_tier
from core.graph_viz import can_render_radar, render_mastery_radar
from core.question_service import get_attempt_stats
from core.topic_service import list_topics_with_published_content

# green / orange / red — mirrors the coverage color scheme on the Assignments page:
# mastered = fully "covered", partial = some grasp but not there yet, weak = not covered.
_TIER_STYLE = {
    'mastered': {'text': 'text-green-600', 'bar': 'green', 'badge': '✓ Mastered'},
    'partial': {'text': 'text-orange-600', 'bar': 'orange', 'badge': None},
    'weak': {'text': 'text-red-600', 'bar': 'red', 'badge': 'Needs attention'},
}


def create_home_page():
    user = app.storage.user
    course_id = require_course(user)
    if course_id is None:
        return
    user_id = user['user_id']

    with get_session() as session:
        topics = list_topics_with_published_content(session, course_id)
        knowledge = []
        for t in topics:
            if t.question_count == 0 and t.assignment_count == 0:
                continue  # nothing to have a mastery opinion about yet
            mastery = get_mastery(session, user_id, t.concept.id)
            knowledge.append({
                'name': t.concept.name, 'mastery': mastery,
                'is_mastered': mastery >= MASTERY_THRESHOLD,
                'tier': mastery_tier(mastery),
            })
        knowledge.sort(key=lambda k: k['mastery'])
        stats = get_attempt_stats(session, user_id)

    name = user.get('full_name', '')
    ui.label(f'Welcome back{", " + name if name else ""}').classes('text-3xl font-bold text-[#0969da] mb-1')
    ui.label('Your knowledge, by topic.').classes('text-gray-500 mb-6')

    # ── Stat strip ───────────────────────────────────────────────────────
    mastered_count = sum(1 for k in knowledge if k['is_mastered'])
    with ui.row().classes('w-full gap-4 mb-6'):
        for label, value in [
            ('Topics tracked', str(len(knowledge))),
            ('Topics mastered', f'{mastered_count}/{len(knowledge)}' if knowledge else '0'),
            ('Questions answered', str(stats['total'])),
            ('Accuracy', f"{stats['accuracy']}%" if stats['total'] else '—'),
        ]:
            with ui.column().classes('bg-white border border-gray-200 rounded-lg px-4 py-3 flex-1 gap-0'):
                ui.label(label).classes('text-xs text-gray-400 uppercase font-semibold')
                ui.label(value).classes('text-2xl font-bold text-gray-800')

    # ── Knowledge overview ───────────────────────────────────────────────
    with ui.row().classes('w-full items-center justify-between mb-2'):
        ui.label('Your Knowledge').classes('text-lg font-semibold')
        if knowledge:
            ui.button('Practice Weakest →', on_click=lambda: ui.navigate.to('/student/practice/adaptive'),
                       color='primary')

    if not knowledge:
        with ui.card().classes('w-full p-6 items-center gap-2'):
            ui.label('No practice questions published yet.').classes('text-gray-500')
            ui.label("Once your instructor publishes some, your progress on each topic shows up here.") \
                .classes('text-sm text-gray-400')
            ui.button('Browse Topics', on_click=lambda: ui.navigate.to('/student/topics'), color='primary') \
                .classes('mt-2')
    else:
        with ui.row().classes('items-center gap-3 text-[11px] text-gray-500 mb-2'):
            for tier, label in [('mastered', 'Mastered'), ('partial', 'In progress'), ('weak', 'Needs attention')]:
                with ui.row().classes('items-center gap-1'):
                    ui.element('div').style(f'width:10px;height:10px;background:{_TIER_STYLE[tier]["bar"]};border-radius:2px')
                    ui.label(label)

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

    # ── Quick links ──────────────────────────────────────────────────────
    ui.separator().classes('my-4')
    with ui.row().classes('gap-3'):
        for label, route in [
            ('Topics', '/student/topics'),
            ('Content Library', '/student/content'),
            ('Assignments', '/student/assignments'),
            ('Practice Questions', '/student/practice'),
        ]:
            ui.button(label, on_click=lambda r=route: ui.navigate.to(r)).props('outline')
