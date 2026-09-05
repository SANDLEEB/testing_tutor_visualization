"""Student Topics (/student/topics) — personal mastery view.

Every topic the instructor has set up, with this student's own Bayesian
Knowledge Tracing mastery estimate — same data and same tiering
(core/bkt_service.py) the Adaptive Practice page's mastery cards use, just
laid out as a full list instead of a strip across the top.
"""
from nicegui import app, ui

from auth.db import get_session
from auth.gateway import require_course
from core.bkt_service import list_mastery, mastery_tier

_TIER_COLOR = {'mastered': '#1a7f37', 'partial': '#9a6700', 'weak': '#cf222e'}
_TIER_BAR = {'mastered': 'positive', 'partial': 'warning', 'weak': 'negative'}


def create_student_topics_page():
    course_id = require_course(app.storage.user)
    if course_id is None:
        return
    user_id = app.storage.user['user_id']

    ui.label('My Topic Mastery').classes('text-2xl font-bold mb-1')
    ui.label('How you\'re tracking across every topic your instructor has set up.') \
        .classes('text-sm text-gray-500 mb-4')

    with get_session() as session:
        rows = list_mastery(session, user_id, course_id)

        if not rows:
            ui.label('No topics yet — check back once your instructor adds some.').classes('text-sm text-gray-400')
            return

        with ui.column().classes('w-full gap-3'):
            for r in rows:
                tier = mastery_tier(r['mastery'])
                pct = round(r['mastery'] * 100)
                with ui.column().classes('w-full gap-1'):
                    with ui.row().classes('w-full items-center justify-between'):
                        ui.label(r['concept'].name).classes('text-sm font-medium')
                        ui.label(f'{pct}%').classes('text-sm font-semibold').style(f'color:{_TIER_COLOR[tier]}')
                    ui.linear_progress(value=r['mastery'], show_value=False).props(f'color={_TIER_BAR[tier]}')
