"""Instructor-only Content Library management (/content/manage).

Create, edit, publish/unpublish, and delete CFG content items. Code is
analyzed server-side on save; students never see a draft until it's
explicitly published.
"""
from nicegui import app, ui

from auth.db import get_session
from auth.gateway import require_course
from core.bkt_service import get_or_create_concept, list_concept_names
from core.content_service import (
    ContentGenerationError, create_content_item, delete_content_item,
    get_content_item, list_content, set_published, update_content_item,
)
from core.models import ContentKind

_KIND = ContentKind.cfg  # only CFG is wired up so far


def create_content_manage_page():
    course_id = require_course(app.storage.user)
    if course_id is None:
        return
    user_id = app.storage.user['user_id']

    ui.label('Manage Content — Control Flow Graphs').classes('text-2xl font-bold mb-1')
    ui.label('Submitted code is analyzed and stored immediately as a draft. '
             'Students only see items you publish.').classes('text-sm text-gray-500 mb-4')

    # ── New content form ────────────────────────────────────────────────
    with ui.card().classes('w-full p-4 gap-2 mb-6'):
        ui.label('New Content').classes('font-semibold text-sm text-gray-500 uppercase tracking-wide')
        with get_session() as session:
            existing_topics = list_concept_names(session, course_id)
        topic_select = ui.select(
            existing_topics, label='Topic', with_input=True, new_value_mode='add-unique',
        ).classes('w-full')
        ui.label('Which topic this visualization belongs to on the Topic Hub — pick one or type a new one.') \
            .classes('text-xs text-gray-400 -mt-1')
        title_input = ui.input(label='Title').classes('w-full')
        code_input = ui.textarea(label='Python source').classes('w-full font-mono text-xs').props('rows=10 outlined')
        error_label = ui.label('').classes('text-red-500 text-sm')
        error_label.set_visibility(False)

        def create_item():
            title = title_input.value.strip()
            code = code_input.value
            topic = (topic_select.value or '').strip()
            error_label.set_visibility(False)
            if not topic:
                error_label.text = 'Name a topic for this content first.'
                error_label.set_visibility(True)
                return
            if not title or not code.strip():
                error_label.text = 'Title and source code are required.'
                error_label.set_visibility(True)
                return
            try:
                with get_session() as session:
                    concept = get_or_create_concept(session, topic, course_id)
                    create_content_item(
                        session, kind=_KIND, title=title, source_code=code,
                        created_by_id=user_id, concept_id=concept.id,
                    )
                    # Deliberately not refreshing topic_select.options here — see the
                    # matching comment in instructor_practice_page.py's create() for why.
            except ContentGenerationError as e:
                error_label.text = f'Could not generate CFG: {e}'
                error_label.set_visibility(True)
                return
            title_input.set_value('')
            code_input.set_value('')
            ui.notify('Content created as draft.', color='positive')
            refresh_list()

        ui.button('Generate & Save as Draft', on_click=create_item, color='primary').classes('w-fit')

    # ── Existing content list ───────────────────────────────────────────
    ui.label('Your Content').classes('font-semibold text-sm text-gray-500 uppercase tracking-wide')
    list_container = ui.column().classes('w-full gap-2')

    edit_dialog = ui.dialog()

    def open_edit(item_id: int):
        with get_session() as session:
            item = get_content_item(session, item_id)
            if item is None:
                return
            item_title, item_code = item.title, item.source_code

        edit_dialog.clear()
        with edit_dialog, ui.card().classes('w-full max-w-2xl p-4 gap-2'):
            ui.label(f'Edit — {item_title}').classes('font-semibold text-lg')
            e_title = ui.input(label='Title', value=item_title).classes('w-full')
            e_code = ui.textarea(label='Python source', value=item_code) \
                .classes('w-full font-mono text-xs').props('rows=12 outlined')
            e_error = ui.label('').classes('text-red-500 text-sm')
            e_error.set_visibility(False)

            def save_edit():
                title = e_title.value.strip()
                code = e_code.value
                if not title or not code.strip():
                    e_error.text = 'Title and source code are required.'
                    e_error.set_visibility(True)
                    return
                try:
                    with get_session() as session:
                        item = get_content_item(session, item_id)
                        update_content_item(session, item, title=title, source_code=code)
                except ContentGenerationError as ex:
                    e_error.text = f'Could not generate CFG: {ex}'
                    e_error.set_visibility(True)
                    return
                edit_dialog.close()
                ui.notify('Content updated.', color='positive')
                refresh_list()

            with ui.row().classes('gap-2 mt-2'):
                ui.button('Save', on_click=save_edit, color='primary')
                ui.button('Cancel', on_click=edit_dialog.close).props('flat')

        edit_dialog.open()

    def delete_item(item_id: int):
        with get_session() as session:
            item = get_content_item(session, item_id)
            if item is not None:
                delete_content_item(session, item)
        ui.notify('Content deleted.', color='warning')
        refresh_list()

    def toggle_published(item_id: int, published: bool):
        with get_session() as session:
            item = get_content_item(session, item_id)
            if item is not None:
                set_published(session, item, published)
        ui.notify('Published.' if published else 'Unpublished.', color='positive')

    def refresh_list():
        list_container.clear()
        with list_container, get_session() as session:
            items = list_content(session, _KIND, created_by_id=user_id, course_id=course_id)

            if not items:
                ui.label('No content yet — create your first item above.').classes('text-sm text-gray-400')

            for item in items:
                topic_name = item.concepts[0].concept.name if item.concepts else '—'
                with ui.row().classes('w-full items-center gap-3 border-b border-gray-100 py-2'):
                    ui.label(item.title).classes('flex-1 text-sm font-medium truncate')
                    ui.label(topic_name).classes('text-xs text-[#0969da] w-36 truncate')
                    ui.label(item.created_at.strftime('%Y-%m-%d')).classes('text-xs text-gray-400 w-24')
                    ui.switch(
                        'Published', value=item.published,
                        on_change=lambda e, iid=item.id: toggle_published(iid, e.value),
                    ).classes('w-36')
                    ui.button('Preview', on_click=lambda iid=item.id: ui.navigate.to(f'/content/{iid}')) \
                        .props('flat dense size=sm')
                    ui.button('Edit', on_click=lambda iid=item.id: open_edit(iid)).props('flat dense size=sm')
                    ui.button('Delete', on_click=lambda iid=item.id: delete_item(iid)) \
                        .props('flat dense size=sm color=negative')

    refresh_list()
