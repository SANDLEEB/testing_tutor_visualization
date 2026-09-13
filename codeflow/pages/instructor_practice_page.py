"""Instructor Practice Questions (/instructor/practice).

Paste source code, pick a question style, and the system generates a
graph-grounded practice question (same four styles Quiz Mode uses) —
review the hint/explanation, then publish it for students. Access (which
students can actually see it once published) is set the same way as on
Assignments — entire class, specific section(s), or specific student(s) —
via the shared pages/access_fields.AccessFields, both at creation and later
from the Edit Access dialog on each row.
"""
from nicegui import app, ui

import config
from auth.db import get_session
from auth.gateway import require_course
from core.bkt_service import list_concepts
from core.course_service import list_sections, list_students, student_picker_label
from core.models import AccessScope, QuestionType
from core.practice_service import (
    PracticeGenerationError, create_practice_question, generate_practice_question_ai,
    list_practice_questions,
)
from core.question_service import (
    delete_question, get_question, get_question_access_student_ids, set_published, set_question_access,
)
from pages.access_fields import AccessFields, access_summary

_TYPE_LABELS = {
    QuestionType.node_type: 'Node type identification',
    QuestionType.du_pair: 'Def-Use pair',
    QuestionType.path_select: 'Test path selection',
    QuestionType.coverage_count: 'Coverage count',
}


def _question_access_summary(session, question) -> str:
    n_students = (
        len(get_question_access_student_ids(session, question.id))
        if question.access_scope == AccessScope.students else 0
    )
    return access_summary(
        access_scope=question.access_scope, access_sections=question.access_sections,
        access_student_count=n_students,
    )


def create_instructor_practice_page():
    course_id = require_course(app.storage.user)
    if course_id is None:
        return
    user_id = app.storage.user['user_id']

    ui.label('Practice Questions').classes('text-2xl font-bold mb-1')
    ui.label('Paste source code, name the topic it belongs to, and pick a question style — '
             'the graph and question are generated automatically from it.').classes('text-sm text-gray-500 mb-4')

    with get_session() as session:
        course_sections = list_sections(session, course_id)
        course_students = [(s.id, student_picker_label(s)) for s in list_students(session, course_id)]

    with ui.card().classes('w-full p-4 gap-2 mb-6'):
        ui.label('New Practice Question').classes('font-semibold text-sm text-gray-500 uppercase tracking-wide')
        with get_session() as session:
            topic_options = {c.id: c.name for c in list_concepts(session, course_id)}
        topic_select = ui.select(topic_options, label='Topic').classes('w-full')
        if topic_options:
            ui.label('Which topic this question belongs to — grading it updates the '
                      "student's mastery of that topic.").classes('text-xs text-gray-400 -mt-1')
        else:
            topic_select.disable()
            with ui.row().classes('items-center gap-2 -mt-1'):
                ui.label('No topics yet.').classes('text-xs text-amber-600')
                ui.link('Create one on the Topics page →', '/instructor/topics').classes('text-xs')
        source_input = ui.textarea(label='Python source code').classes('w-full font-mono text-xs').props('rows=10 outlined')
        with ui.row().classes('w-full gap-3'):
            type_select = ui.select(_TYPE_LABELS, value=QuestionType.node_type, label='Question style').classes('flex-1')
            ai_wording_switch = ui.switch('AI-enhanced wording', value=False).classes('flex-1')
        ui.label(
            "Either way the graph, choices, and correct answer are always computed directly from "
            "your source code — AI only rewords the prompt/explanation/hint to read more naturally, "
            "it never changes what's correct." if config.AI_ENABLED else
            "No AI provider key is configured — questions use the algorithmic wording only "
            "(add a key to codeflow/.env to enable AI-enhanced wording)."
        ).classes('text-xs text-gray-400 -mt-1')
        if not config.AI_ENABLED:
            ai_wording_switch.disable()

        access = AccessFields(course_sections=course_sections, course_students=course_students)

        error_label = ui.label('').classes('text-red-500 text-xs')
        error_label.set_visibility(False)

        generate_btn = ui.button('Generate Question', color='primary').classes('w-fit mt-2')

        async def generate():
            source = source_input.value.strip()
            concept_id = topic_select.value
            error_label.set_visibility(False)
            if not concept_id:
                error_label.text = 'Pick a topic for this question first.'
                error_label.set_visibility(True)
                return
            if not source:
                error_label.text = 'Paste some source code first.'
                error_label.set_visibility(True)
                return
            generate_btn.props('loading')
            try:
                if ai_wording_switch.value:
                    fields = await generate_practice_question_ai(source, type_select.value)
                else:
                    fields = None
                access_values = access.read()
                with get_session() as session:
                    question = create_practice_question(
                        session, qtype=type_select.value, source_code=source,
                        concept_id=concept_id, created_by_id=user_id, fields=fields,
                    )
                    set_question_access(
                        session, question, scope=access_values['access_scope'],
                        sections=access_values['access_sections'], student_ids=access_values['access_student_ids'],
                    )
            except PracticeGenerationError as e:
                error_label.text = str(e)
                error_label.set_visibility(True)
                return
            finally:
                generate_btn.props(remove='loading')
            access.reset()
            ui.notify('Question generated as a draft.', color='positive')
            refresh_list()

        generate_btn.on_click(generate)

    ui.label('Your Practice Questions').classes('font-semibold text-sm text-gray-500 uppercase tracking-wide')
    with ui.row().classes('w-full items-center gap-3 text-[11px] text-gray-400 uppercase font-semibold px-2'):
        ui.label('Prompt').classes('flex-1')
        ui.label('Topic').classes('w-36')
        ui.label('Type').classes('w-48')
        ui.label('Access').classes('w-32')
        ui.label('Published').classes('w-36')
        ui.label('').classes('w-56')
    list_container = ui.column().classes('w-full gap-2')

    def toggle_published(question_id: int, published: bool):
        with get_session() as session:
            q = get_question(session, question_id)
            if q is not None:
                set_published(session, q, published)
        ui.notify('Published.' if published else 'Unpublished.', color='positive')

    def delete_row(question_id: int):
        with get_session() as session:
            q = get_question(session, question_id)
            if q is not None:
                delete_question(session, q)
        ui.notify('Deleted.', color='warning')
        refresh_list()

    # ── Edit Access — content (prompt/choices/graph) stays instructor-authored/
    # AI-generated at creation time; only who can see it is editable afterward.
    access_dialog = ui.dialog()

    def open_access_dialog(question_id: int):
        with get_session() as session:
            q = get_question(session, question_id)
            if q is None:
                return
            prompt = q.prompt
            access_scope, access_sections = q.access_scope, list(q.access_sections or [])
            access_student_ids = get_question_access_student_ids(session, question_id)

        access_dialog.clear()
        with access_dialog, ui.card().classes('w-full max-w-xl p-4 gap-2'):
            ui.label('Edit Access').classes('font-semibold text-lg mb-1')
            ui.label(prompt).classes('text-xs text-gray-500 truncate mb-1')
            fields = AccessFields(
                course_sections=course_sections, course_students=course_students,
                access_scope=access_scope, access_sections=access_sections, access_student_ids=access_student_ids,
            )
            with ui.row().classes('gap-2 mt-2'):
                def save():
                    values = fields.read()
                    with get_session() as session:
                        q2 = get_question(session, question_id)
                        if q2 is not None:
                            set_question_access(
                                session, q2, scope=values['access_scope'],
                                sections=values['access_sections'], student_ids=values['access_student_ids'],
                            )
                    access_dialog.close()
                    refresh_list()
                    ui.notify('Access updated.', color='positive')

                ui.button('Save', color='primary', on_click=save)
                ui.button('Cancel', on_click=access_dialog.close).props('flat')
        access_dialog.open()

    def refresh_list():
        list_container.clear()
        with list_container, get_session() as session:
            questions = list_practice_questions(session, created_by_id=user_id, course_id=course_id)
            if not questions:
                ui.label('No practice questions yet — generate your first one above.').classes('text-sm text-gray-400')
            for q in questions:
                with ui.row().classes('w-full items-center gap-3 border-b border-gray-100 py-2'):
                    ui.label(q.prompt).classes('flex-1 text-sm truncate')
                    ui.label(q.concept.name if q.concept_id else '—').classes('text-xs text-[#0969da] w-36 truncate')
                    ui.label(_TYPE_LABELS[q.type]).classes('text-xs text-gray-500 w-48')
                    ui.label(_question_access_summary(session, q)).classes('text-xs text-gray-500 w-32')
                    ui.switch(
                        'Published', value=q.published,
                        on_change=lambda e, qid=q.id: toggle_published(qid, e.value),
                    ).classes('w-36')
                    with ui.row().classes('w-56 gap-1 justify-end'):
                        ui.button('Access', on_click=lambda qid=q.id: open_access_dialog(qid)) \
                            .props('flat dense size=sm')
                        ui.button('Preview', on_click=lambda qid=q.id: ui.navigate.to(f'/practice/{qid}')) \
                            .props('flat dense size=sm')
                        ui.button('Delete', on_click=lambda qid=q.id: delete_row(qid)) \
                            .props('flat dense size=sm color=negative')

    refresh_list()
