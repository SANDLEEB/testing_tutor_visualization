"""Home / Dashboard page."""
from nicegui import ui
from graphs.examples import EXAMPLE_KEYS, EXAMPLE_NAMES, get_example


CONCEPT_CARDS = [
    ('cfg',       '⬡', 'Control Flow Graph',
     'Visualise how execution flows through branches, loops, and exceptions.',
     '/cfg'),
    ('du',        '⇆', 'Def-Use Chains',
     'Track every variable definition and where it reaches a use.',
     '/du'),
    ('dom',       '⊆', 'Dominator Tree',
     'Discover which nodes every path must pass through.',
     '/dominators'),
    ('call',      '⬢', 'Call Graph',
     'See how functions call each other and simulate call stacks.',
     '/callgraph'),
    ('coverage',  '✓', 'Coverage Criteria',
     'Compare NC, EC, EPC, and Prime Path coverage side-by-side.',
     '/coverage'),
    ('quiz',      '?', 'Quiz Mode',
     'Test your understanding with interactive exercises and instant feedback.',
     '/quiz'),
]


def create_home_page():
    ui.add_head_html('''
    <style>
      .concept-card { transition: transform .15s, box-shadow .15s; cursor:pointer; }
      .concept-card:hover { transform:translateY(-3px); box-shadow:0 8px 24px rgba(0,0,0,.15); }
      .hero-input textarea { font-family:monospace; font-size:13px; }
    </style>
    ''')

    # ── Hero banner ──────────────────────────────────────────────────────
    with ui.column().classes('w-full items-center py-10 gap-2'):
        ui.label('CodeFlow Visualizer').classes('text-4xl font-bold text-blue-600')
        ui.label('Interactive software testing concepts through animated graph visualizations') \
            .classes('text-gray-500 text-lg text-center max-w-xl')

    ui.separator()

    # ── 6-card grid ──────────────────────────────────────────────────────
    with ui.grid(columns=3).classes('w-full gap-4 px-8 py-6'):
        for _, icon, title, desc, route in CONCEPT_CARDS:
            with ui.card().classes('concept-card p-4').on('click', lambda r=route: ui.navigate.to(r)):
                with ui.row().classes('items-center gap-3 mb-2'):
                    ui.label(icon).classes('text-3xl')
                    ui.label(title).classes('text-lg font-semibold')
                ui.label(desc).classes('text-sm text-gray-500')
                ui.button('Launch →', color='blue').classes('mt-3 text-sm') \
                    .on('click', lambda r=route: ui.navigate.to(r))

    ui.separator()

    # ── Quick-start input ─────────────────────────────────────────────────
    with ui.column().classes('w-full px-8 pb-8 gap-4'):
        ui.label('Quick Start').classes('text-xl font-semibold')
        ui.label('Paste any Python code and click Analyze All to jump straight to the CFG view.') \
            .classes('text-sm text-gray-500')

        code_input = ui.textarea(label='Python code', placeholder='def my_func():\n    ...') \
            .classes('hero-input w-full font-mono text-sm') \
            .props('rows=8 outlined')

        # Example selector
        with ui.row().classes('items-center gap-4 flex-wrap'):
            example_sel = ui.select(
                options={k: name for k, name in zip(EXAMPLE_KEYS, EXAMPLE_NAMES)},
                label='Load example',
                value=EXAMPLE_KEYS[0],
            ).classes('w-72')

            def load_example():
                ex = get_example(example_sel.value)
                code_input.set_value(ex['source'])

            ui.button('Load Example', on_click=load_example, color='secondary')

            def analyze_all():
                from nicegui import app
                app.storage.user['quick_code'] = code_input.value
                ui.navigate.to('/cfg')

            ui.button('Analyze All →', on_click=analyze_all, color='primary')

        # Pre-load default example
        ex0 = get_example(EXAMPLE_KEYS[0])
        code_input.set_value(ex0['source'])
