"""Instructor Topics (/instructor/topics).

The only place topics (Concepts) get created, renamed, or deleted — every
other page that tags content with a topic (Assignments, Practice Questions)
only ever picks from what's already here. Also doubles as the class-wide
progress dashboard: average mastery per topic, plus a per-student breakdown,
both driven by the same Bayesian Knowledge Tracing data the student-facing
Adaptive Practice page uses.
"""
from nicegui import app, ui

from auth.db import get_session
from auth.gateway import require_course
from core.bkt_service import get_or_create_concept, list_class_mastery, list_concepts, list_student_mastery_matrix, mastery_tier
from core.topic_service import TopicNotEmptyError, delete_topic, get_topic, update_topic

_TIER_COLOR = {'mastered': '#1a7f37', 'partial': '#9a6700', 'weak': '#cf222e'}


def create_instructor_topics_page():
    course_id = require_course(app.storage.user)
    if course_id is None:
        return

    ui.label('Topics').classes('text-2xl font-bold mb-1')
    ui.label('Add, rename, or remove the topics your assignments and practice questions are '
              'tagged with — and see how the class is doing on each.').classes('text-sm text-gray-500 mb-4')

    # ── New Topic ────────────────────────────────────────────────────────
    with ui.card().classes('w-full p-4 gap-2 mb-6'):
        ui.label('New Topic').classes('font-semibold text-sm text-gray-500 uppercase tracking-wide')
        name_input = ui.input(label='Topic name').classes('w-full')
        desc_input = ui.textarea(label='Description (optional)').classes('w-full').props('rows=2 outlined')
        error_label = ui.label('').classes('text-red-500 text-xs')
        error_label.set_visibility(False)

        def create_topic():
            name = name_input.value.strip()
            error_label.set_visibility(False)
            if not name:
                error_label.text = 'Name the topic first.'
                error_label.set_visibility(True)
                return
            with get_session() as session:
                existing = [c.name for c in list_concepts(session, course_id)]
                if name.lower() in {n.lower() for n in existing}:
                    error_label.text = f'"{name}" already exists — pick a different name.'
                    error_label.set_visibility(True)
                    return
                concept = get_or_create_concept(session, name, course_id)
                if desc_input.value.strip():
                    update_topic(session, concept, name=name, description=desc_input.value.strip())
            name_input.set_value('')
            desc_input.set_value('')
            ui.notify('Topic created.', color='positive')
            refresh_list()

        ui.button('Create', on_click=create_topic, color='primary').classes('w-fit mt-1')

    # ── Edit dialog ──────────────────────────────────────────────────────
    edit_dialog = ui.dialog()

    def open_edit(concept_id: int, name: str, description: str):
        edit_dialog.clear()
        with edit_dialog, ui.card().classes('w-full max-w-md p-4 gap-2'):
            ui.label('Edit Topic').classes('font-semibold text-sm text-gray-500 uppercase tracking-wide')
            edit_name = ui.input(label='Topic name', value=name).classes('w-full')
            edit_desc = ui.textarea(label='Description', value=description).classes('w-full').props('rows=2 outlined')
            edit_error = ui.label('').classes('text-red-500 text-xs')
            edit_error.set_visibility(False)

            def save():
                new_name = edit_name.value.strip()
                edit_error.set_visibility(False)
                if not new_name:
                    edit_error.text = 'Name cannot be empty.'
                    edit_error.set_visibility(True)
                    return
                with get_session() as session:
                    c = get_topic(session, concept_id)
                    if c is not None:
                        update_topic(session, c, name=new_name, description=edit_desc.value.strip())
                edit_dialog.close()
                ui.notify('Saved.', color='positive')
                refresh_list()

            with ui.row().classes('gap-2 mt-2'):
                ui.button('Save', on_click=save, color='primary')
                ui.button('Cancel', on_click=edit_dialog.close).props('flat')
        edit_dialog.open()

    def delete_row(concept_id: int):
        try:
            with get_session() as session:
                c = get_topic(session, concept_id)
                if c is not None:
                    delete_topic(session, c)
        except TopicNotEmptyError as e:
            ui.notify(str(e), color='negative')
            return
        ui.notify('Topic deleted.', color='warning')
        refresh_list()

    # ── List: name, description, class-average mastery, Edit/Delete ────────
    ui.label('Your Topics').classes('font-semibold text-sm text-gray-500 uppercase tracking-wide')
    list_container = ui.column().classes('w-full gap-2 mb-6')

    def refresh_list():
        list_container.clear()
        with list_container, get_session() as session:
            concepts = list_concepts(session, course_id)
            mastery_by_concept = {m['concept'].id: m for m in list_class_mastery(session, course_id)}
            if not concepts:
                ui.label('No topics yet — create one above.').classes('text-sm text-gray-400')
            for c in concepts:
                m = mastery_by_concept.get(c.id)
                with ui.row().classes('w-full items-center gap-3 border-b border-gray-100 py-2'):
                    with ui.column().classes('flex-1 gap-0 min-w-0'):
                        ui.label(c.name).classes('text-sm font-medium truncate')
                        if c.description:
                            ui.label(c.description).classes('text-xs text-gray-400 truncate')
                    if m is not None:
                        pct = round(m['mastery'] * 100)
                        with ui.column().classes('gap-0.5').style('width:180px'):
                            ui.label(f"{pct}% avg · {m['student_count']} student(s)") \
                                .classes('text-xs').style(f"color:{_TIER_COLOR[m['tier']]}")
                            ui.linear_progress(value=m['mastery'], show_value=False) \
                                .props(f"color=\"{ {'mastered': 'positive', 'partial': 'warning', 'weak': 'negative'}[m['tier']] }\"")
                    else:
                        ui.label('No attempts yet').classes('text-xs text-gray-400').style('width:180px')
                    ui.button('Edit', on_click=lambda cid=c.id, n=c.name, d=c.description: open_edit(cid, n, d)) \
                        .props('flat dense size=sm')
                    ui.button('Delete', on_click=lambda cid=c.id: delete_row(cid)) \
                        .props('flat dense size=sm color=negative')

    refresh_list()

    # ── Per-student breakdown ───────────────────────────────────────────
    ui.label('Per-Student Breakdown').classes('font-semibold text-sm text-gray-500 uppercase tracking-wide')
    with get_session() as session:
        concepts = list_concepts(session, course_id)
        matrix = list_student_mastery_matrix(session, course_id)

        if not concepts or not matrix:
            ui.label('No students or topics yet.').classes('text-sm text-gray-400')
            return

        grid_style = f'grid-template-columns: 1.5fr repeat({len(concepts)}, 1fr)'
        with ui.grid().classes('w-full gap-2').style(grid_style):
            ui.label('Student').classes('font-semibold text-xs text-gray-500')
            for c in concepts:
                ui.label(c.name).classes('font-semibold text-xs text-gray-500 truncate')
            for row in matrix:
                ui.label(row['user'].full_name).classes('text-sm truncate')
                for c in concepts:
                    fraction = row['mastery'][c.id]
                    ui.label(f'{round(fraction * 100)}%').classes('text-sm font-semibold') \
                        .style(f'color:{_TIER_COLOR[mastery_tier(fraction)]}')
