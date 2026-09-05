"""Content Library (/content) — published CFG content, browsable by everyone."""
from nicegui import app, ui

from auth.db import get_session
from auth.gateway import require_course
from core.content_service import list_content
from core.models import ContentKind


def create_content_library_page():
    course_id = require_course(app.storage.user)
    if course_id is None:
        return

    ui.label('Content Library').classes('text-2xl font-bold mb-1')
    ui.label('Control Flow Graph examples published by instructors.').classes('text-sm text-gray-500 mb-4')

    with get_session() as session:
        items = list_content(session, ContentKind.cfg, published_only=True, course_id=course_id)

        if not items:
            ui.label('No published content yet — check back soon.').classes('text-sm text-gray-400')
            return

        with ui.grid(columns=3).classes('w-full gap-4'):
            for item in items:
                with ui.card().classes('p-4').style('cursor:pointer') \
                        .on('click', lambda iid=item.id: ui.navigate.to(f'/content/{iid}')):
                    ui.label(item.title).classes('text-lg font-semibold mb-1')
                    ui.label(item.updated_at.strftime('Updated %Y-%m-%d')).classes('text-xs text-gray-400 mb-2')
                    ui.button('View →', color='primary').classes('text-sm') \
                        .on('click', lambda iid=item.id: ui.navigate.to(f'/content/{iid}'))
