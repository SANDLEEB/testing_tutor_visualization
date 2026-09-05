"""Topic Hub (/topics/{concept_id}) — everything tied to one topic, and where
instructors do the ongoing work of building it.

Aggregates course material (pasted/uploaded or drafted via the embedded RAG
chat below), CFG content items, and practice questions under a single topic
page. Instructors (any faculty/admin — topics are shared, not owned) chat
with the LLM, upload material, generate questions, and publish — all from
this one tab — then keep coming back to add, edit, or delete. Students see
only the published slice, read-only.
"""
from nicegui import app, ui

import config
from auth.db import get_session
from auth.gateway import require_course
from core.ai_service import AIServiceError, chat_completion, parse_questions, retrieve_context
from core.content_service import delete_content_item, list_by_concept, set_published as set_content_published
from core.material_service import (
    create_material, delete_material, list_materials, set_published as set_material_published,
)
from core.question_service import create_question, delete_question, list_questions
from core.question_service import set_published as set_question_published
from core.topic_service import TopicNotEmptyError, delete_topic, get_topic, publish_all, update_topic

_MAX_PASTE_CHARS = 200_000


def create_topic_hub_page(concept_id: int):
    user = app.storage.user
    user_id = user['user_id']
    is_instructor = user.get('role') in ('faculty', 'admin')
    chat_state = {'chat': []}  # {role, content, questions?}

    course_id = require_course(user)
    if course_id is None:
        return

    with get_session() as session:
        concept = get_topic(session, concept_id)
        if concept is None or concept.course_id != course_id:
            ui.label('Topic not found.').classes('text-red-500 text-lg m-4')
            return
        name, description = concept.name, concept.description

    # ── Header ───────────────────────────────────────────────────────────
    header_box = ui.column().classes('w-full gap-2 mb-4')

    def render_header():
        header_box.clear()
        with header_box:
            if is_instructor:
                name_input = ui.input(label='Topic name', value=name).classes('text-2xl font-bold w-full max-w-xl')
                desc_input = ui.textarea(label='Description', value=description) \
                    .classes('w-full max-w-xl').props('rows=2 outlined')

                def save_header():
                    with get_session() as session:
                        c = get_topic(session, concept_id)
                        update_topic(session, c, name=name_input.value, description=desc_input.value)
                    ui.notify('Saved.', color='positive')

                def do_publish_all():
                    with get_session() as session:
                        n = publish_all(session, concept_id)
                    ui.notify(f'Published {n} item(s).' if n else 'Everything is already published.',
                              color='positive')
                    refresh_material()
                    refresh_content()
                    refresh_questions()

                def do_delete_topic():
                    try:
                        with get_session() as session:
                            c = get_topic(session, concept_id)
                            delete_topic(session, c)
                    except TopicNotEmptyError as e:
                        ui.notify(str(e), color='negative')
                        return
                    ui.notify('Topic deleted.', color='warning')
                    ui.navigate.to('/instructor/topics')

                with ui.row().classes('gap-2'):
                    ui.button('Save', on_click=save_header, color='primary').props('dense size=sm')
                    ui.button('Publish All', on_click=do_publish_all, color='positive').props('dense size=sm')
                    ui.button('Delete Topic', on_click=do_delete_topic, color='negative').props('dense size=sm outline')
            else:
                ui.label(name).classes('text-2xl font-bold')
                if description:
                    ui.label(description).classes('text-sm text-gray-600')

    render_header()

    # ── Course Material ──────────────────────────────────────────────────
    ui.label('Course Material').classes('font-semibold text-sm text-gray-500 uppercase tracking-wide mt-2')
    material_box = ui.column().classes('w-full gap-2 mb-2')

    def refresh_material():
        material_box.clear()
        with material_box, get_session() as session:
            materials = list_materials(session, concept_id=concept_id, published_only=not is_instructor)
            if not materials:
                ui.label('No material yet.' if is_instructor else 'No material published yet.') \
                    .classes('text-sm text-gray-400')
            for m in materials:
                with ui.expansion(m.title).classes('w-full'):
                    ui.label(m.content_text[:2000]).classes('text-xs text-gray-700 whitespace-pre-wrap')
                    if is_instructor:
                        with ui.row().classes('gap-2 mt-2'):
                            ui.switch(
                                'Published', value=m.published,
                                on_change=lambda e, mid=m.id: toggle_material(mid, e.value),
                            ).props('dense size=sm')
                            ui.button('Delete', on_click=lambda mid=m.id: remove_material(mid)) \
                                .props('flat dense size=sm color=negative')

    def toggle_material(material_id: int, published: bool):
        with get_session() as session:
            m = next((x for x in list_materials(session, concept_id=concept_id) if x.id == material_id), None)
            if m is not None:
                set_material_published(session, m, published)
        ui.notify('Published.' if published else 'Unpublished.', color='positive')
        refresh_material()

    def remove_material(material_id: int):
        with get_session() as session:
            m = next((x for x in list_materials(session, concept_id=concept_id) if x.id == material_id), None)
            if m is not None:
                delete_material(session, m)
        ui.notify('Deleted.', color='warning')
        refresh_material()

    refresh_material()

    if is_instructor:
        with ui.expansion('+ Add Material').classes('w-full mb-4'):
            mat_title = ui.input(label='Title').classes('w-full')
            mat_text = ui.textarea(label='Paste text').classes('w-full font-mono text-xs').props('rows=5 outlined')
            mat_error = ui.label('').classes('text-red-500 text-xs')
            mat_error.set_visibility(False)

            def upload_handler(e):
                try:
                    text = e.content.read().decode('utf-8', errors='ignore')
                except Exception:
                    mat_error.text = 'Could not read that file as UTF-8 text.'
                    mat_error.set_visibility(True)
                    return
                mat_text.set_value(text[:_MAX_PASTE_CHARS])
                if not mat_title.value.strip():
                    mat_title.set_value(e.name)

            ui.upload(label='or upload a .txt / .md file', auto_upload=True, on_upload=upload_handler) \
                .props('accept=.txt,.md flat').classes('w-full')

            def add_material():
                title = mat_title.value.strip()
                text = mat_text.value.strip()
                mat_error.set_visibility(False)
                if not title or not text:
                    mat_error.text = 'Title and text are required.'
                    mat_error.set_visibility(True)
                    return
                with get_session() as session:
                    create_material(
                        session, title=title, content_text=text[:_MAX_PASTE_CHARS],
                        uploaded_by_id=user_id, concept_id=concept_id,
                    )
                mat_title.set_value('')
                mat_text.set_value('')
                ui.notify('Material added as a draft.', color='positive')
                refresh_material()

            ui.button('Add Material', on_click=add_material, color='primary').classes('mt-2')

    # ── Visualizations (CFG content) ────────────────────────────────────
    ui.label('Visualizations').classes('font-semibold text-sm text-gray-500 uppercase tracking-wide mt-2')
    content_box = ui.column().classes('w-full gap-2 mb-4')

    def refresh_content():
        content_box.clear()
        with content_box, get_session() as session:
            items = list_by_concept(session, concept_id, published_only=not is_instructor)
            if not items:
                ui.label('No visualizations yet.' if is_instructor else 'No visualizations published yet.') \
                    .classes('text-sm text-gray-400')
            for item in items:
                with ui.row().classes('w-full items-center gap-3 border-b border-gray-100 py-2'):
                    ui.label(item.title).classes('flex-1 text-sm font-medium truncate')
                    ui.button('View →', on_click=lambda iid=item.id: ui.navigate.to(f'/content/{iid}')) \
                        .props('flat dense size=sm')
                    if is_instructor:
                        ui.switch(
                            value=item.published,
                            on_change=lambda e, iid=item.id: toggle_content(iid, e.value),
                        ).props('dense size=sm').tooltip('Published')
                        ui.button('Delete', on_click=lambda iid=item.id: remove_content(iid)) \
                            .props('flat dense size=sm color=negative')

    def toggle_content(item_id: int, published: bool):
        with get_session() as session:
            items = list_by_concept(session, concept_id)
            item = next((x for x in items if x.id == item_id), None)
            if item is not None:
                set_content_published(session, item, published)
        ui.notify('Published.' if published else 'Unpublished.', color='positive')
        refresh_content()

    def remove_content(item_id: int):
        with get_session() as session:
            items = list_by_concept(session, concept_id)
            item = next((x for x in items if x.id == item_id), None)
            if item is not None:
                delete_content_item(session, item)
        ui.notify('Deleted.', color='warning')
        refresh_content()

    refresh_content()

    if is_instructor:
        ui.button('+ Add Visualization', on_click=lambda: ui.navigate.to('/instructor/content')) \
            .props('flat dense size=sm').classes('mb-4 -mt-2')

    # ── Practice Questions ───────────────────────────────────────────────
    ui.label('Practice Questions').classes('font-semibold text-sm text-gray-500 uppercase tracking-wide mt-2')
    question_box = ui.column().classes('w-full gap-2 mb-2')

    def refresh_questions():
        question_box.clear()
        with question_box, get_session() as session:
            questions = list_questions(session, concept_id=concept_id, published_only=not is_instructor)
            if not questions:
                ui.label('No practice questions yet.' if is_instructor else 'No practice questions published yet.') \
                    .classes('text-sm text-gray-400')
            for q in questions:
                with ui.row().classes('w-full items-center gap-3 border-b border-gray-100 py-2'):
                    ui.label(q.prompt).classes('flex-1 text-sm truncate')
                    ui.button('Open →', on_click=lambda qid=q.id: ui.navigate.to(f'/practice/{qid}')) \
                        .props('flat dense size=sm')
                    if is_instructor:
                        ui.switch(
                            value=q.published,
                            on_change=lambda e, qid=q.id: toggle_question(qid, e.value),
                        ).props('dense size=sm').tooltip('Published')
                        ui.button('Delete', on_click=lambda qid=q.id: remove_question(qid)) \
                            .props('flat dense size=sm color=negative')

    def toggle_question(question_id: int, published: bool):
        with get_session() as session:
            qs = list_questions(session, concept_id=concept_id)
            q = next((x for x in qs if x.id == question_id), None)
            if q is not None:
                set_question_published(session, q, published)
        ui.notify('Published.' if published else 'Unpublished.', color='positive')
        refresh_questions()

    def remove_question(question_id: int):
        with get_session() as session:
            qs = list_questions(session, concept_id=concept_id)
            q = next((x for x in qs if x.id == question_id), None)
            if q is not None:
                delete_question(session, q)
        ui.notify('Deleted.', color='warning')
        refresh_questions()

    refresh_questions()

    if is_instructor:
        ui.button('+ Add via Practice Questions (from code)', on_click=lambda: ui.navigate.to('/instructor/practice')) \
            .props('flat dense size=sm').classes('mb-4 -mt-2')

        # ── Chat & Generate (RAG, scoped to this topic) ─────────────────
        ui.label('Chat & Generate').classes('font-semibold text-sm text-gray-500 uppercase tracking-wide mt-2')
        ui.label("Grounded in this topic's material above — ask questions, or ask it to write quiz questions.") \
            .classes('text-xs text-gray-400 mb-2')

        if not config.AI_ENABLED:
            with ui.card().classes('w-full p-3 mb-4 bg-amber-50'):
                ui.label('AI chat is not configured — add an AI provider key to codeflow/.env to enable it (see .env.example).') \
                    .classes('text-sm text-amber-700')
        else:
            chat_log = ui.column().classes('w-full gap-3 p-3 border border-gray-200 rounded-lg mb-2') \
                .style('min-height:200px;max-height:420px;overflow-y:auto;background:#f8fafc')

            def render_question_card(q: dict):
                with ui.card().classes('w-full p-3 gap-1 bg-white'):
                    ui.label(q['prompt']).classes('text-sm font-medium')
                    for choice in q['choices']:
                        mark = '✓ ' if choice == q['answer'] else '• '
                        color = 'text-green-600 font-semibold' if choice == q['answer'] else 'text-gray-600'
                        ui.label(f'{mark}{choice}').classes(f'text-xs {color}')
                    if q['explanation']:
                        ui.label(q['explanation']).classes('text-xs text-gray-400 italic')

                    def save():
                        with get_session() as session:
                            create_question(
                                session, prompt=q['prompt'], choices=q['choices'], answer=q['answer'],
                                explanation=q['explanation'], created_by_id=user_id, concept_id=concept_id,
                            )
                        ui.notify('Saved to Practice Questions as a draft.', color='positive')
                        refresh_questions()

                    ui.button('Save to Practice Questions', on_click=save, color='primary').props('dense size=sm')

            def render_chat():
                chat_log.clear()
                with chat_log:
                    if not chat_state['chat']:
                        ui.label('Ask about this topic, or ask it to generate quiz questions.') \
                            .classes('text-xs text-gray-400')
                    for turn in chat_state['chat']:
                        align = 'items-end' if turn['role'] == 'user' else 'items-start'
                        bubble = 'bg-[#0969da] text-white' if turn['role'] == 'user' else 'bg-white'
                        with ui.column().classes(f'w-full {align} gap-1'):
                            ui.label(turn['content']).classes(
                                f'{bubble} rounded-lg px-3 py-2 text-sm max-w-2xl whitespace-pre-wrap')
                            for q in turn.get('questions', []):
                                with ui.column().classes('w-full max-w-2xl'):
                                    render_question_card(q)

            render_chat()

            with ui.row().classes('w-full gap-2 items-end mb-4'):
                msg_input = ui.textarea(label='Message').classes('flex-1 font-mono text-xs').props('rows=2 outlined')
                send_btn = ui.button('Send', color='primary')

            async def send():
                text = msg_input.value.strip()
                if not text:
                    return
                send_btn.props('loading')
                msg_input.set_value('')

                with get_session() as session:
                    materials = list_materials(session, concept_id=concept_id)
                    context = retrieve_context(materials, text)

                history = [{'role': t['role'], 'content': t['content']} for t in chat_state['chat']]
                chat_state['chat'].append({'role': 'user', 'content': text})
                render_chat()

                try:
                    reply = await chat_completion(history + [{'role': 'user', 'content': text}], context)
                    questions = parse_questions(reply)
                    chat_state['chat'].append({'role': 'assistant', 'content': reply, 'questions': questions})
                except AIServiceError as e:
                    chat_state['chat'].append({'role': 'assistant', 'content': f'⚠ {e}'})
                finally:
                    send_btn.props(remove='loading')
                    render_chat()

            send_btn.on_click(send)

    ui.separator().classes('my-4')
    back_target = '/instructor/topics' if is_instructor else '/student/topics'
    ui.button('← Back to Topics', on_click=lambda: ui.navigate.to(back_target))
