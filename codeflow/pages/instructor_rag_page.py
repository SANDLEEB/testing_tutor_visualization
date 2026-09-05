"""AI Question Authoring (/instructor/rag).

Instructor uploads course material, chats with an LLM grounded in that
material (RAG), and saves any questions the model proposes into the
Question bank — publishing them makes them available for future quiz use.
"""
from nicegui import app, ui

import config
from auth.db import get_session
from auth.gateway import require_course
from core.ai_service import AIServiceError, chat_completion, parse_questions, retrieve_context
from core.bkt_service import get_or_create_concept, list_concept_names
from core.material_service import create_material, delete_material, list_materials, set_published as set_material_published
from core.question_service import create_question, delete_question, get_question, list_questions, set_published

_MAX_PASTE_CHARS = 200_000


def create_instructor_rag_page():
    course_id = require_course(app.storage.user)
    if course_id is None:
        return
    user_id = app.storage.user['user_id']
    state = {'selected_ids': set(), 'chat': []}  # chat item: {role, content, questions?}

    ui.label('AI Question Authoring').classes('text-2xl font-bold mb-1')
    ui.label('Upload course material, ask the assistant to write quiz questions from it, '
             'then save and publish the ones you want to keep.').classes('text-sm text-gray-500 mb-4')

    if not config.AI_ENABLED:
        with ui.card().classes('w-full p-4 mb-4 bg-amber-50'):
            ui.label('AI chat is not configured yet.').classes('font-semibold text-amber-700')
            ui.label('Add an AI provider key to codeflow/.env and restart the app to enable it (see .env.example). '
                      'Material upload below still works.').classes('text-sm text-amber-700')

    with ui.row().classes('w-full gap-4 items-start'):

        # ── Left: course material ───────────────────────────────────────
        with ui.column().classes('gap-3').style('width:320px;min-width:320px'):
            ui.label('Course Material').classes('font-semibold text-sm text-gray-500 uppercase tracking-wide')

            with get_session() as session:
                existing_topics = list_concept_names(session, course_id)
            topic_select = ui.select(
                existing_topics, label='Topic', with_input=True, new_value_mode='add-unique',
            ).classes('w-full')
            ui.label('Which topic this belongs to on the Topic Hub — also used for any questions you save below.') \
                .classes('text-xs text-gray-400 -mt-2')

            with ui.card().classes('w-full p-3 gap-2'):
                mat_title = ui.input(label='Title').classes('w-full')
                mat_text = ui.textarea(label='Paste text').classes('w-full font-mono text-xs').props('rows=6 outlined')
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
                    topic = (topic_select.value or '').strip()
                    mat_error.set_visibility(False)
                    if not topic:
                        mat_error.text = 'Name a topic for this material first.'
                        mat_error.set_visibility(True)
                        return
                    if not title or not text:
                        mat_error.text = 'Title and text are required.'
                        mat_error.set_visibility(True)
                        return
                    with get_session() as session:
                        concept = get_or_create_concept(session, topic, course_id)
                        create_material(
                            session, title=title, content_text=text[:_MAX_PASTE_CHARS],
                            uploaded_by_id=user_id, concept_id=concept.id,
                        )
                        # Deliberately not refreshing topic_select.options here — see the
                        # matching comment in instructor_practice_page.py's create() for why.
                    mat_title.set_value('')
                    mat_text.set_value('')
                    ui.notify('Material added as a draft.', color='positive')
                    refresh_materials()

                ui.button('Add Material', on_click=add_material, color='primary').classes('w-full')

            ui.label('Use as context').classes('font-semibold text-xs text-gray-500 uppercase tracking-wide mt-2')
            materials_container = ui.column().classes('w-full gap-1')

            def refresh_materials():
                materials_container.clear()
                with materials_container, get_session() as session:
                    materials = list_materials(session, uploaded_by_id=user_id, course_id=course_id)
                    if not materials:
                        ui.label('No material uploaded yet.').classes('text-xs text-gray-400')
                    for m in materials:
                        with ui.row().classes('w-full items-center gap-1'):
                            cb = ui.checkbox(m.title, value=m.id in state['selected_ids'])

                            def on_toggle(e, mid=m.id):
                                if e.value:
                                    state['selected_ids'].add(mid)
                                else:
                                    state['selected_ids'].discard(mid)

                            cb.on_value_change(on_toggle)
                            cb.classes('flex-1 text-xs')

                            ui.switch(
                                value=m.published,
                                on_change=lambda e, mid=m.id: toggle_material_published(mid, e.value),
                            ).props('dense size=xs').tooltip('Published — visible to students on the Topic Hub')

                            def on_delete(mid=m.id):
                                with get_session() as s:
                                    mat = list_materials(s, uploaded_by_id=user_id, course_id=course_id)
                                    target = next((x for x in mat if x.id == mid), None)
                                    if target is not None:
                                        delete_material(s, target)
                                state['selected_ids'].discard(mid)
                                refresh_materials()

                            ui.button(icon='delete', on_click=on_delete).props('flat dense size=xs color=negative')

            def toggle_material_published(material_id: int, published: bool):
                with get_session() as session:
                    materials = list_materials(session, uploaded_by_id=user_id, course_id=course_id)
                    target = next((x for x in materials if x.id == material_id), None)
                    if target is not None:
                        set_material_published(session, target, published)
                ui.notify('Published.' if published else 'Unpublished.', color='positive')

            refresh_materials()

        # ── Right: chat ──────────────────────────────────────────────────
        with ui.column().classes('flex-1 gap-2'):
            ui.label('Chat').classes('font-semibold text-sm text-gray-500 uppercase tracking-wide')
            chat_log = ui.column().classes('w-full gap-3 p-3 border border-gray-200 rounded-lg') \
                .style('min-height:360px;max-height:520px;overflow-y:auto;background:#f8fafc')

            def render_question_card(container, q: dict, material_id: int | None):
                with container, ui.card().classes('w-full p-3 gap-1 bg-white'):
                    ui.label(q['prompt']).classes('text-sm font-medium')
                    for choice in q['choices']:
                        mark = '✓ ' if choice == q['answer'] else '• '
                        color = 'text-green-600 font-semibold' if choice == q['answer'] else 'text-gray-600'
                        ui.label(f'{mark}{choice}').classes(f'text-xs {color}')
                    if q['explanation']:
                        ui.label(q['explanation']).classes('text-xs text-gray-400 italic')

                    def save():
                        topic = (topic_select.value or '').strip()
                        if not topic:
                            ui.notify('Set a Topic (left panel) before saving a question.', color='warning')
                            return
                        with get_session() as session:
                            concept = get_or_create_concept(session, topic, course_id)
                            create_question(
                                session, prompt=q['prompt'], choices=q['choices'], answer=q['answer'],
                                explanation=q['explanation'], created_by_id=user_id,
                                material_id=material_id, concept_id=concept.id,
                            )
                            # Deliberately not refreshing topic_select.options here — see the
                            # matching comment in instructor_practice_page.py's create() for why.
                        ui.notify('Saved to Question Bank as a draft.', color='positive')
                        refresh_questions()

                    ui.button('Save to Question Bank', on_click=save, color='primary').props('dense size=sm')

            def render_chat():
                chat_log.clear()
                with chat_log:
                    if not state['chat']:
                        ui.label('Ask the assistant about your uploaded material, or ask it to '
                                 'generate quiz questions.').classes('text-xs text-gray-400')
                    for turn in state['chat']:
                        align = 'items-end' if turn['role'] == 'user' else 'items-start'
                        bubble_color = 'bg-[#0969da] text-white' if turn['role'] == 'user' else 'bg-white'
                        with ui.column().classes(f'w-full {align} gap-1'):
                            ui.label(turn['content']).classes(f'{bubble_color} rounded-lg px-3 py-2 text-sm max-w-2xl whitespace-pre-wrap')
                            for q in turn.get('questions', []):
                                render_question_card(ui.column().classes('w-full max-w-2xl'), q, turn.get('material_id'))

            render_chat()

            with ui.row().classes('w-full gap-2 items-end'):
                msg_input = ui.textarea(label='Message').classes('flex-1 font-mono text-xs').props('rows=2 outlined')
                send_btn = ui.button('Send', color='primary')

            async def send():
                text = msg_input.value.strip()
                if not text or not config.AI_ENABLED:
                    return
                send_btn.props('loading')
                msg_input.set_value('')

                with get_session() as session:
                    materials = list_materials(session, uploaded_by_id=user_id, course_id=course_id)
                    selected = [m for m in materials if m.id in state['selected_ids']]
                    context = retrieve_context(selected, text)

                history = [{'role': t['role'], 'content': t['content']} for t in state['chat']]
                state['chat'].append({'role': 'user', 'content': text})
                render_chat()

                try:
                    reply = await chat_completion(history + [{'role': 'user', 'content': text}], context)
                    questions = parse_questions(reply)
                    material_id = selected[0].id if len(selected) == 1 else None
                    state['chat'].append({'role': 'assistant', 'content': reply, 'questions': questions,
                                           'material_id': material_id})
                except AIServiceError as e:
                    state['chat'].append({'role': 'assistant', 'content': f'⚠ {e}'})
                finally:
                    send_btn.props(remove='loading')
                    render_chat()

            send_btn.on_click(send)

    # ── Question bank ────────────────────────────────────────────────────
    ui.separator().classes('my-4')
    ui.label('Your Questions').classes('font-semibold text-sm text-gray-500 uppercase tracking-wide')
    questions_container = ui.column().classes('w-full gap-2')

    def refresh_questions():
        questions_container.clear()
        with questions_container, get_session() as session:
            questions = list_questions(session, created_by_id=user_id, course_id=course_id)
            if not questions:
                ui.label('No AI-authored questions yet — generate some in the chat above.') \
                    .classes('text-sm text-gray-400')
            for q in questions:
                with ui.row().classes('w-full items-center gap-3 border-b border-gray-100 py-2'):
                    ui.label(q.prompt).classes('flex-1 text-sm truncate')
                    ui.label(q.concept.name if q.concept_id else '—').classes('text-xs text-[#0969da] w-36 truncate')
                    ui.label(q.created_at.strftime('%Y-%m-%d')).classes('text-xs text-gray-400 w-24')
                    ui.switch(
                        'Published', value=q.published,
                        on_change=lambda e, qid=q.id: toggle_question_published(qid, e.value),
                    ).classes('w-36')
                    ui.button('Delete', on_click=lambda qid=q.id: delete_question_row(qid)) \
                        .props('flat dense size=sm color=negative')

    def toggle_question_published(question_id: int, published: bool):
        with get_session() as session:
            q = get_question(session, question_id)
            if q is not None:
                set_published(session, q, published)
        ui.notify('Published.' if published else 'Unpublished.', color='positive')

    def delete_question_row(question_id: int):
        with get_session() as session:
            q = get_question(session, question_id)
            if q is not None:
                delete_question(session, q)
        ui.notify('Deleted.', color='warning')
        refresh_questions()

    refresh_questions()
