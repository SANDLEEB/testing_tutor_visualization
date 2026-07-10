"""Quiz / Exercise Mode page (/quiz)."""
import json
import random
from nicegui import ui
from graphs.examples import EXAMPLE_KEYS, get_example

_CW, _CH = 680, 380
_STYLE = 'width:100%;outline:none;border-radius:8px;background:#f8fafc;border:1px solid #e2e8f0;'


# ── Question bank ─────────────────────────────────────────────────────────

def _make_questions() -> list:
    questions = []

    # Type 1: node type identification
    for key in ['if_else', 'while_sum', 'triangle', 'fizzbuzz']:
        ex    = get_example(key)
        gd    = ex['graph_data']
        nodes = gd['nodes']
        dec   = [n for n in nodes if n['type'] == 'decision']
        if dec:
            n = random.choice(dec)
            questions.append({
                'type':        'node_type',
                'key':         key,
                'graph':       gd,
                'question':    f'What type is node {n["id"]} ("{n["label"][:30]}")?',
                'choices':     ['Entry/Exit', 'Statement', 'Decision', 'Exception'],
                'answer':      'Decision',
                'explanation': 'Decision nodes have True/False branches — they represent if/while/for conditions.',
            })

    # Type 2: DU pair identification
    for key in ['if_else', 'while_sum']:
        ex  = get_example(key)
        gd  = ex['graph_data']
        du  = gd['metadata'].get('du_chains', {})
        for var, pairs in du.items():
            if pairs:
                p       = pairs[0]
                other   = [n['id'] for n in gd['nodes'] if n['id'] != p['use']]
                choices = list({p['use']} | set(random.sample(other, min(3, len(other)))))
                random.shuffle(choices)
                questions.append({
                    'type':        'du_pair',
                    'key':         key,
                    'graph':       gd,
                    'question':    f'For variable "{var}", which node id contains a USE?',
                    'choices':     [str(c) for c in choices[:4]],
                    'answer':      str(p['use']),
                    'explanation': (f'Node {p["use"]} reads "{var}" after it was defined at node {p["def"]}.'),
                })
                break

    # Type 3: valid path selection
    for key in ['if_else', 'linear_search']:
        ex    = get_example(key)
        gd    = ex['graph_data']
        paths = gd['metadata'].get('paths', [])
        if len(paths) >= 2:
            correct = paths[0]
            wrong1  = list(reversed(correct))
            wrong2  = correct[:max(1, len(correct)-1)]
            choices_raw = [correct, wrong1, wrong2]
            if len(paths) > 2:
                choices_raw.append(paths[1])
            choices = [json.dumps(c) for c in choices_raw[:4]]
            random.shuffle(choices)
            questions.append({
                'type':        'path_select',
                'key':         key,
                'graph':       gd,
                'question':    'Which is a valid simple path from Entry to Exit?',
                'choices':     choices,
                'answer':      json.dumps(correct),
                'explanation': f'The path {correct} follows the control flow edges from Entry to Exit.',
            })

    # Type 4: node coverage count
    for key in ['if_else']:
        ex  = get_example(key)
        gd  = ex['graph_data']
        n   = len(gd['nodes'])
        opts = list({str(n), str(n-1), str(n+1), str(max(n-2,1))})
        random.shuffle(opts)
        questions.append({
            'type':        'coverage_count',
            'key':         key,
            'graph':       gd,
            'question':    'How many nodes must be visited to achieve 100% Node Coverage?',
            'choices':     opts[:4],
            'answer':      str(n),
            'explanation': f'Node Coverage requires all {n} nodes to be executed at least once.',
        })

    random.shuffle(questions)
    return questions[:10]


# ── Page ──────────────────────────────────────────────────────────────────

def create_quiz_page():
    questions = _make_questions()
    state = {'idx': 0, 'score': 0, 'answers': [], 'done': False}

    # Header
    with ui.row().classes('w-full items-center justify-between px-4 py-3 border-b border-gray-200'):
        ui.label('Quiz Mode').classes('text-xl font-bold text-blue-600')
        progress_label = ui.label('Question 1 / 10').classes('text-sm text-gray-500')
        score_label    = ui.label('Score: 0').classes('text-sm font-semibold text-green-600')

    main = ui.column().classes('w-full px-4 py-4 gap-4')

    def _render():
        main.clear()
        if state['done'] or state['idx'] >= len(questions):
            _render_summary(main, state, questions, _restart)
            return

        q   = questions[state['idx']]
        gd  = q['graph']
        gj  = json.dumps(gd)

        progress_label.set_text(f'Question {state["idx"]+1} / {len(questions)}')
        score_label.set_text(f'Score: {state["score"]}')

        answered = state['answers'][state['idx']] if state['idx'] < len(state['answers']) else None
        canvas_id = f'quiz-canvas-{state["idx"]}'

        with main:
            with ui.card().classes('w-full'):
                ui.label(f'Q{state["idx"]+1}. {q["question"]}').classes('text-base font-semibold')

            # Canvas — init via timer so it runs after Vue mounts the element
            ui.html(f'<canvas id="{canvas_id}" width="{_CW}" height="{_CH}" style="{_STYLE}height:{_CH}px;"></canvas>')

            async def _init_canvas(cid=canvas_id, data=gj):
                await ui.run_javascript(
                    f'cfgWhenReady("{cid}", function(){{ window.quizAnim = cfgInit("{cid}", {data}, {{layout:"layered"}}); }});'
                )

            ui.timer(0.15, _init_canvas, once=True)

            feedback = ui.label('').classes('text-sm font-semibold min-h-5')

            with ui.grid(columns=2).classes('w-full gap-2'):
                for choice in q['choices']:
                    display = choice
                    if q['type'] == 'path_select':
                        try:
                            display = ' → '.join(str(x) for x in json.loads(choice))
                        except Exception:
                            pass

                    is_correct_choice = str(choice) == str(q['answer'])
                    btn_color = 'white'
                    if answered:
                        btn_color = 'positive' if is_correct_choice else (
                            'negative' if answered == str(choice) else 'white'
                        )

                    def on_choose(c=choice):
                        if state['idx'] < len(state['answers']):
                            return
                        correct = str(c) == str(q['answer'])
                        state['answers'].append(str(c))
                        if correct:
                            state['score'] += 1
                        _render()

                    ui.button(str(display)[:80], on_click=on_choose) \
                        .props(f'color={btn_color} outline').classes('text-xs text-left')

            if answered:
                correct_ans = str(q['answer'])
                if q['type'] == 'path_select':
                    try:
                        correct_ans = ' → '.join(str(x) for x in json.loads(q['answer']))
                    except Exception:
                        pass
                was_correct = answered == str(q['answer'])
                msg = ('✓ Correct! ' if was_correct else f'✗ Incorrect. Correct: {correct_ans}. ') + q.get('explanation', '')
                color = 'text-green-600' if was_correct else 'text-red-600'
                with main:
                    ui.label(msg).classes(f'text-sm {color}')

            with ui.row().classes('gap-3 mt-2'):
                if state['idx'] > 0:
                    ui.button('← Back', on_click=lambda: _nav(-1)).props('flat')
                if answered is not None:
                    label = 'See Results' if state['idx'] >= len(questions) - 1 else 'Next →'
                    ui.button(label, on_click=lambda: _nav(1), color='primary')
                else:
                    ui.button('Skip', on_click=_skip).props('flat color=grey')

    def _nav(delta: int):
        state['idx'] = max(0, state['idx'] + delta)
        if state['idx'] >= len(questions):
            state['done'] = True
        _render()

    def _skip():
        if len(state['answers']) <= state['idx']:
            state['answers'].append(None)
        _nav(1)

    def _restart():
        state.update({'idx': 0, 'score': 0, 'answers': [], 'done': False})
        questions[:] = _make_questions()
        _render()

    _render()


def _render_summary(container, state: dict, questions: list, restart_fn):
    total = len(questions)
    score = state['score']
    pct   = round(score / total * 100) if total else 0
    color = 'text-green-600' if pct >= 70 else 'text-amber-500' if pct >= 40 else 'text-red-500'

    with container:
        with ui.card().classes('w-full items-center py-8 gap-3 text-center'):
            ui.label('Quiz Complete!').classes('text-2xl font-bold text-blue-600')
            ui.label(f'{score} / {total}').classes(f'text-4xl font-bold {color}')
            ui.label(f'{pct}% correct').classes('text-lg text-gray-500')
            msg = ('Perfect score!' if pct == 100
                   else 'Good work! Review the questions you missed.' if pct >= 70
                   else 'Keep practising — try the CFG and DU Chain pages for more detail.')
            ui.label(msg).classes('text-sm text-gray-500')

        ui.label('Review').classes('text-lg font-semibold mt-2')
        for i, q in enumerate(questions):
            given   = state['answers'][i] if i < len(state['answers']) else None
            correct = given is not None and str(given) == str(q['answer'])
            icon    = '✓' if correct else ('✗' if given else '—')
            clr     = 'text-green-600' if correct else 'text-red-500' if given else 'text-gray-400'
            with ui.row().classes('items-start gap-2 text-sm border-b border-gray-100 py-1'):
                ui.label(icon).classes(f'w-5 font-bold {clr}')
                ui.label(f'Q{i+1}: {q["question"][:80]}').classes('flex-1 text-xs')

        ui.button('Restart Quiz', on_click=restart_fn, color='primary').classes('mt-4')
