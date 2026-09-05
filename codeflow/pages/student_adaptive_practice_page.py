"""Adaptive Practice (/student/practice/adaptive).

Uses Bayesian Knowledge Tracing (core/bkt_service.py) to pick the next
question from whichever concept the student is currently weakest in,
loops after every answer, and shows live per-concept mastery as it goes.
"""
import asyncio
import json

from nicegui import app, ui

import config
from auth.db import get_session
from auth.gateway import require_course
from core.ai_service import AIServiceError, generate_answer_feedback
from core.bkt_service import list_mastery, pick_next_question, record_attempt_and_update
from core.practice_service import summarize_graph
from core.question_service import get_question, record_attempt

_CANVAS_W = 640
_CANVAS_H = 420
_CANVAS_STYLE = (
    'width:100%;height:100%;outline:none;border-radius:8px;'
    'background:#f8fafc;border:1px solid #e2e8f0;'
)


def _display_choice(qtype: str, choice: str) -> str:
    if qtype == 'path_select':
        try:
            return ' → '.join(str(x) for x in json.loads(choice))
        except (json.JSONDecodeError, TypeError):
            return choice
    return choice


def _mastery_sync(user_id: int, course_id: int) -> list[dict]:
    with get_session() as session:
        rows = list_mastery(session, user_id, course_id)
        return [{'name': r['concept'].name, 'mastery': r['mastery'], 'is_mastered': r['is_mastered']} for r in rows]


def _pick_sync(user_id: int, course_id: int) -> dict | None:
    with get_session() as session:
        q = pick_next_question(session, user_id, course_id)
        if q is None:
            return None
        return {
            'id': q.id, 'prompt': q.prompt, 'choices': q.choices, 'hint': q.hint,
            'qtype': q.type.value, 'graph_data': q.graph_data,
            'concept_name': q.concept.name if q.concept_id else None,
        }


def _answer_sync(question_id: int, user_id: int, given_answer: str) -> dict:
    with get_session() as session:
        q = get_question(session, question_id)
        is_correct = given_answer == q.answer
        record_attempt(session, question_id=question_id, user_id=user_id,
                        given_answer=given_answer, is_correct=is_correct)
        mastery_before = mastery_after = None
        if q.concept_id is not None:
            mastery_before, mastery_after = record_attempt_and_update(
                session, user_id=user_id, concept_id=q.concept_id, is_correct=is_correct,
            )
        return {
            'is_correct': is_correct, 'correct_answer': q.answer, 'explanation': q.explanation,
            'prompt': q.prompt, 'qtype': q.type.value, 'graph_data': q.graph_data,
            'mastery_before': mastery_before, 'mastery_after': mastery_after,
        }


def create_adaptive_practice_page():
    course_id = require_course(app.storage.user)
    if course_id is None:
        return
    user_id = app.storage.user['user_id']

    ui.label('Adaptive Practice').classes('text-2xl font-bold mb-1')
    ui.label("Picks whichever concept you're weakest in and serves the next question automatically.") \
        .classes('text-sm text-gray-500 mb-4')

    mastery_row = ui.row().classes('w-full gap-3 flex-wrap mb-4')
    question_box = ui.column().classes('w-full')

    async def refresh_mastery():
        rows = await asyncio.to_thread(_mastery_sync, user_id, course_id)
        mastery_row.clear()
        with mastery_row:
            for r in rows:
                bg = 'bg-green-50' if r['is_mastered'] else 'bg-gray-50'
                text = 'text-green-700' if r['is_mastered'] else 'text-gray-600'
                with ui.column().classes(f'{bg} rounded-lg px-3 py-2 gap-1').style('min-width:150px'):
                    ui.label(r['name']).classes(f'text-xs font-semibold {text}')
                    ui.label(f"{round(r['mastery'] * 100)}%").classes(f'text-lg font-bold {text}')
                    ui.linear_progress(value=r['mastery'], show_value=False).classes('mt-0.5')

    async def render_question(q: dict):
        question_box.clear()
        with question_box, ui.card().classes('w-full p-4'):
            if q['concept_name']:
                ui.label(q['concept_name']).classes('text-xs font-semibold text-[#0969da] uppercase mb-1')
            ui.label(q['prompt']).classes('text-lg font-bold mb-3')

            with ui.row().classes('w-full gap-4 items-start'):
                graph_json = json.dumps(q['graph_data']) if q['graph_data'] else None
                if graph_json:
                    canvas_id = f'adaptive-canvas-{q["id"]}'
                    with ui.column().classes('flex-1 p-2'):
                        ui.html(
                            f'<canvas id="{canvas_id}" width="{_CANVAS_W}" height="{_CANVAS_H}" '
                            f'style="{_CANVAS_STYLE}"></canvas>'
                        )
                    # This page renders question-by-question after the initial load (via
                    # ui.timer / button clicks), not during the synchronous page build —
                    # add_body_html only takes effect on that initial build, so a script
                    # tag added here would silently never run. run_javascript executes
                    # immediately over the websocket regardless of when it's called.
                    await ui.run_javascript(
                        f'cfgWhenReady("{canvas_id}", function() {{ '
                        f'cfgInit("{canvas_id}", {graph_json}, {{ layout: "layered" }}); }});'
                    )

                with ui.column().classes('gap-2').style('width:340px;min-width:340px'):
                    hint_box = ui.column().classes('w-full')
                    if q['hint']:
                        def show_hint():
                            hint_box.clear()
                            with hint_box:
                                ui.label(q['hint']).classes('text-xs text-amber-700 bg-amber-50 p-2 rounded')
                        ui.button('Show Hint', on_click=show_hint).props('flat dense size=sm color=amber')

                    choices_box = ui.column().classes('w-full gap-2 mt-2')
                    result_box = ui.column().classes('w-full gap-1 mt-3')
                    answered = {'done': False}

                    async def choose(choice: str):
                        if answered['done']:
                            return
                        answered['done'] = True
                        choices_box.set_visibility(False)
                        result = await asyncio.to_thread(_answer_sync, q['id'], user_id, choice)

                        result_box.clear()
                        with result_box:
                            color = 'text-green-600' if result['is_correct'] else 'text-red-500'
                            icon = '✓ Correct' if result['is_correct'] else '✗ Incorrect'
                            ui.label(icon).classes(f'text-lg font-bold {color}')
                            if not result['is_correct']:
                                ui.label(f"Correct answer: {_display_choice(q['qtype'], result['correct_answer'])}") \
                                    .classes('text-sm text-gray-600')
                            ui.label(result['explanation']).classes('text-sm text-gray-500')

                            if result['mastery_before'] is not None:
                                delta = result['mastery_after'] - result['mastery_before']
                                arrow = '↑' if delta > 0 else ('↓' if delta < 0 else '→')
                                dcolor = 'text-green-600' if delta > 0 else ('text-red-500' if delta < 0 else 'text-gray-500')
                                ui.label(
                                    f"Concept mastery: {round(result['mastery_before'] * 100)}% "
                                    f"{arrow} {round(result['mastery_after'] * 100)}%"
                                ).classes(f'text-xs {dcolor} font-semibold mt-1')

                            feedback_box = ui.column().classes('w-full mt-2')
                            if config.AI_ENABLED and result['graph_data']:
                                with feedback_box:
                                    ui.spinner(size='sm')
                                    ui.label('Getting AI feedback...').classes('text-xs text-gray-400')
                                try:
                                    feedback = await generate_answer_feedback(
                                        prompt=result['prompt'],
                                        graph_summary=summarize_graph(result['graph_data']),
                                        correct_answer=_display_choice(q['qtype'], result['correct_answer']),
                                        student_answer=_display_choice(q['qtype'], choice),
                                        is_correct=result['is_correct'],
                                    )
                                    feedback_box.clear()
                                    with feedback_box:
                                        ui.label('AI Feedback').classes('text-xs font-semibold text-[#0969da] uppercase mt-1')
                                        ui.label(feedback).classes('text-sm text-gray-700 bg-[#fce4e8] p-2 rounded')
                                except AIServiceError:
                                    feedback_box.clear()

                            ui.button('Next Question →', on_click=load_next, color='primary').classes('mt-3')

                        await refresh_mastery()

                    with choices_box:
                        for choice in q['choices']:
                            ui.button(_display_choice(q['qtype'], choice)[:80], on_click=lambda c=choice: choose(c)) \
                                .props('outline').classes('w-full text-left justify-start')

    async def load_next():
        question_box.clear()
        with question_box:
            ui.spinner(size='lg')
        q = await asyncio.to_thread(_pick_sync, user_id, course_id)
        if q is None:
            question_box.clear()
            with question_box:
                ui.label('No published practice questions available yet — check back soon.') \
                    .classes('text-gray-400')
            return
        await render_question(q)

    async def start():
        await refresh_mastery()
        await load_next()

    ui.timer(0.05, start, once=True)
