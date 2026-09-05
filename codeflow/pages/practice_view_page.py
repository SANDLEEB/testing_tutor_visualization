"""Practice question viewer/answerer (/practice/{question_id}).

Shared by the instructor's "Preview" link and the student practice flow.
Renders the question's own graph, lets the student pick an answer, then
shows the deterministic correct/incorrect result plus an LLM-generated,
graph-grounded comparison against the instructor's reference answer
(falls back to the static explanation if AI chat isn't configured).
"""
import asyncio
import json

from nicegui import app, ui

import config
from auth.db import get_session
from auth.gateway import require_course
from core.ai_service import AIServiceError, generate_answer_feedback
from core.bkt_service import record_attempt_and_update
from core.practice_service import summarize_graph
from core.question_service import get_question, record_attempt

_CANVAS_W = 700
_CANVAS_H = 480
_CANVAS_STYLE = (
    'width:100%;height:100%;outline:none;border-radius:8px;'
    'background:#f8fafc;border:1px solid #e2e8f0;'
)
_CANVAS_ID = 'practice-view-canvas'


def _init_script(graph_json: str) -> str:
    return f'''<script>
cfgWhenReady('{_CANVAS_ID}', function() {{
  window.practiceAnim = cfgInit('{_CANVAS_ID}', {graph_json}, {{ layout: 'layered' }});
}});
</script>'''


def _display_choice(qtype: str, choice: str) -> str:
    if qtype == 'path_select':
        try:
            return ' → '.join(str(x) for x in json.loads(choice))
        except (json.JSONDecodeError, TypeError):
            return choice
    return choice


def _feedback_sync(question_id: int, user_id: int, given_answer: str) -> dict:
    """Deterministic grading + attempt persistence (sync, DB-bound) — the LLM
    call is made separately (async) so it doesn't block behind this."""
    with get_session() as session:
        q = get_question(session, question_id)
        is_correct = given_answer == q.answer
        record_attempt(session, question_id=question_id, user_id=user_id,
                        given_answer=given_answer, is_correct=is_correct)

        mastery_before = mastery_after = concept_name = None
        if q.concept_id is not None:
            concept_name = q.concept.name
            mastery_before, mastery_after = record_attempt_and_update(
                session, user_id=user_id, concept_id=q.concept_id, is_correct=is_correct,
            )

        return {
            'is_correct': is_correct, 'correct_answer': q.answer, 'explanation': q.explanation,
            'prompt': q.prompt, 'qtype': q.type.value, 'graph_data': q.graph_data,
            'concept_name': concept_name, 'mastery_before': mastery_before, 'mastery_after': mastery_after,
        }


def create_practice_view_page(question_id: int):
    user = app.storage.user
    course_id = require_course(user)
    if course_id is None:
        return
    user_id = user['user_id']

    with get_session() as session:
        q = get_question(session, question_id)
        if q is None:
            ui.label('Question not found.').classes('text-red-500 text-lg m-4')
            return
        is_owner_or_admin = user.get('user_id') == q.created_by_id or user.get('role') == 'admin'
        if not q.published and not is_owner_or_admin:
            ui.label('This question has not been published yet.').classes('text-red-500 text-lg m-4')
            return
        if q.concept_id is None or q.concept.course_id != course_id:
            ui.label('Question not found.').classes('text-red-500 text-lg m-4')
            return
        prompt, choices, hint, qtype = q.prompt, q.choices, q.hint, q.type.value
        published = q.published
        graph_json = json.dumps(q.graph_data) if q.graph_data else None

    if not published:
        ui.label('Draft preview — not visible to students until published.').classes('text-sm text-amber-500 mb-2')
    ui.label(prompt).classes('text-xl font-bold mb-3')

    with ui.row().classes('w-full gap-4 items-start'):
        if graph_json:
            with ui.column().classes('flex-1 p-2'):
                ui.html(
                    f'<canvas id="{_CANVAS_ID}" width="{_CANVAS_W}" height="{_CANVAS_H}" '
                    f'style="{_CANVAS_STYLE}"></canvas>'
                )
                ui.add_body_html(_init_script(graph_json))

        with ui.column().classes('gap-2').style('width:340px;min-width:340px'):
            hint_box = ui.column().classes('w-full')

            def show_hint():
                hint_box.clear()
                with hint_box:
                    ui.label(hint or 'No hint for this question.').classes('text-xs text-amber-700 bg-amber-50 p-2 rounded')

            if hint:
                ui.button('Show Hint', on_click=show_hint).props('flat dense size=sm color=amber')

            choices_box = ui.column().classes('w-full gap-2 mt-2')
            result_box = ui.column().classes('w-full gap-1 mt-3')

            answered = {'done': False}

            async def choose(choice: str):
                if answered['done']:
                    return
                answered['done'] = True
                choices_box.set_visibility(False)

                result = await asyncio.to_thread(_feedback_sync, question_id, user_id, choice)

                result_box.clear()
                with result_box:
                    color = 'text-green-600' if result['is_correct'] else 'text-red-500'
                    icon = '✓ Correct' if result['is_correct'] else '✗ Incorrect'
                    ui.label(icon).classes(f'text-lg font-bold {color}')
                    if not result['is_correct']:
                        ui.label(f"Correct answer: {_display_choice(qtype, result['correct_answer'])}") \
                            .classes('text-sm text-gray-600')
                    ui.label(result['explanation']).classes('text-sm text-gray-500')

                    feedback_box = ui.column().classes('w-full mt-2')
                    if config.AI_ENABLED and result['graph_data']:
                        with feedback_box:
                            ui.spinner(size='sm')
                            ui.label('Getting AI feedback...').classes('text-xs text-gray-400')
                        try:
                            feedback = await generate_answer_feedback(
                                prompt=result['prompt'],
                                graph_summary=summarize_graph(result['graph_data']),
                                correct_answer=_display_choice(qtype, result['correct_answer']),
                                student_answer=_display_choice(qtype, choice),
                                is_correct=result['is_correct'],
                            )
                            feedback_box.clear()
                            with feedback_box:
                                ui.label('AI Feedback').classes('text-xs font-semibold text-[#0969da] uppercase mt-1')
                                ui.label(feedback).classes('text-sm text-gray-700 bg-[#fce4e8] p-2 rounded')
                        except AIServiceError:
                            feedback_box.clear()

            with choices_box:
                for choice in choices:
                    ui.button(_display_choice(qtype, choice)[:80], on_click=lambda c=choice: choose(c)) \
                        .props('outline').classes('w-full text-left justify-start')

    if user.get('role') in ('faculty', 'admin'):
        ui.button('← Back to Practice Questions', on_click=lambda: ui.navigate.to('/instructor/practice')).classes('mt-4')
    else:
        ui.button('← Back to Practice Questions', on_click=lambda: ui.navigate.to('/student/practice')).classes('mt-4')
