"""Coverage Criteria page (/coverage)."""
import json
from nicegui import ui
from parser.cfg_builder import CFGBuilder
from graphs.examples import EXAMPLE_KEYS, EXAMPLE_NAMES, get_example
from graphs.serializer import GraphSerializer

_DEFAULT = 'if_else'
_CW, _CH = 800, 440
_STYLE = 'width:100%;height:100%;outline:none;border-radius:8px;background:#f8fafc;border:1px solid #e2e8f0;'

CRITERIA = {
    'NC':  'Node Coverage',
    'EC':  'Edge Coverage',
    'EPC': 'Edge-Pair Coverage',
    'PPC': 'Prime Path Coverage',
}

SUBSUMPTION_HTML = '''
<div style="display:flex;align-items:center;gap:8px;padding:8px 0;font-size:12px;flex-wrap:wrap;">
  <span style="background:#dbeafe;color:#1e40af;padding:3px 10px;border-radius:20px;font-weight:600;">NC</span>
  <span style="color:#94a3b8">⊂</span>
  <span style="background:#dcfce7;color:#14532d;padding:3px 10px;border-radius:20px;font-weight:600;">EC</span>
  <span style="color:#94a3b8">⊂</span>
  <span style="background:#fef3c7;color:#92400e;padding:3px 10px;border-radius:20px;font-weight:600;">EPC</span>
  <span style="color:#94a3b8">⊂</span>
  <span style="background:#fce7f3;color:#831843;padding:3px 10px;border-radius:20px;font-weight:600;">PPC</span>
  <span style="color:#64748b;font-size:11px;margin-left:8px;">Each criterion subsumes all criteria to its left</span>
</div>
'''


def _compute_requirements(gd: dict, criterion: str) -> list:
    nodes       = gd['nodes']
    edges       = gd['edges']
    paths       = gd['metadata'].get('paths', [])
    prime_paths = gd['metadata'].get('prime_paths', [])

    if criterion == 'NC':
        return [[n['id']] for n in nodes]
    if criterion == 'EC':
        return [[e['from'], e['to']] for e in edges]
    if criterion == 'EPC':
        reqs: list = []
        adj: dict = {}
        for e in edges:
            adj.setdefault(e['from'], []).append(e['to'])
        for e in edges:
            for nxt in adj.get(e['to'], []):
                reqs.append([e['from'], e['to'], nxt])
        return reqs
    if criterion == 'PPC':
        return prime_paths if prime_paths else paths
    return []


def _req_in_path(req: list, path: list) -> bool:
    if not req or not path:
        return False
    if len(req) == 1:
        return req[0] in path
    for i in range(len(path) - len(req) + 1):
        if path[i:i + len(req)] == req:
            return True
    return False


def create_coverage_page():
    initial_code = get_example(_DEFAULT)['source']
    gd  = CFGBuilder().build(initial_code)
    gd  = GraphSerializer.inject_layout_hints(gd, 'layered')
    gj  = json.dumps(gd)

    state = {'gd': gd}

    with ui.column().classes('w-full gap-4 p-4'):

        # ── Top row ───────────────────────────────────────────────────────
        with ui.row().classes('w-full gap-4'):

            # Code panel
            with ui.column().classes('gap-3').style('width:280px;min-width:280px'):
                ui.label('Code').classes('text-sm font-semibold text-gray-500 uppercase')
                editor = ui.textarea(value=initial_code) \
                    .classes('w-full font-mono text-xs').props('rows=10 outlined dense')
                example_sel = ui.select(
                    {k: n for k, n in zip(EXAMPLE_KEYS, EXAMPLE_NAMES)},
                    label='Example', value=_DEFAULT,
                ).classes('w-full')

                def load_ex():
                    ex = get_example(example_sel.value)
                    editor.set_value(ex['source'])
                ui.button('Load Example', on_click=load_ex, color='secondary').classes('w-full')
                parse_btn = ui.button('Parse CFG', color='primary').classes('w-full')

            # Canvas
            with ui.column().classes('flex-1'):
                ui.html(f'<canvas id="cov-canvas" width="{_CW}" height="{_CH}" style="{_STYLE}"></canvas>')

            # Controls
            with ui.column().classes('gap-3').style('width:220px;min-width:220px'):
                ui.label('Criterion').classes('text-sm font-semibold text-gray-500 uppercase')
                crit_sel    = ui.radio(CRITERIA, value='NC').classes('text-sm')
                animate_btn = ui.button('▶ Animate Requirements', color='primary').classes('w-full')

                ui.separator()
                with ui.row().classes('justify-between text-xs'):
                    ui.label('Node coverage')
                    ui.html('<span id="cov-nc" style="color:#3b82f6;font-weight:600">0%</span>')
                with ui.row().classes('justify-between text-xs'):
                    ui.label('Edge coverage')
                    ui.html('<span id="cov-ec" style="color:#22c55e;font-weight:600">0%</span>')

        # ── Subsumption hierarchy ─────────────────────────────────────────
        with ui.card().classes('w-full'):
            ui.label('Subsumption Hierarchy').classes('font-semibold text-sm mb-1')
            ui.html(SUBSUMPTION_HTML)

        # ── Requirements table + test-suite builder ───────────────────────
        with ui.row().classes('w-full gap-4'):

            with ui.column().classes('flex-1 gap-2'):
                req_label     = ui.label('Required Elements (NC)').classes('font-semibold text-sm text-gray-600')
                req_container = ui.column().classes('w-full gap-1 text-xs')

            with ui.column().classes('gap-2').style('width:300px'):
                ui.label('Test Suite Builder').classes('font-semibold text-sm text-gray-600')
                ui.label('Add test paths (comma-separated node IDs per line):') \
                    .classes('text-xs text-gray-400')
                test_input = ui.textarea(placeholder='0,1,2,4\n0,1,3,4') \
                    .props('outlined rows=4').classes('w-full font-mono text-xs')
                cov_result = ui.column().classes('w-full gap-1')

                def check_coverage():
                    lines = [l.strip() for l in test_input.value.splitlines() if l.strip()]
                    cov_result.clear()
                    with cov_result:
                        for crit in CRITERIA:
                            reqs = _compute_requirements(state['gd'], crit)
                            try:
                                test_paths = [[int(x) for x in l.split(',')] for l in lines]
                            except ValueError:
                                continue
                            satisfied = sum(
                                1 for req in reqs
                                if any(_req_in_path(req, tp) for tp in test_paths)
                            )
                            pct   = round(satisfied / max(len(reqs), 1) * 100)
                            color = '#22c55e' if pct == 100 else '#f59e0b' if pct > 50 else '#ef4444'
                            with ui.row().classes('justify-between items-center text-xs'):
                                ui.label(CRITERIA[crit])
                                ui.html(f'<span style="color:{color};font-weight:600">{pct}%</span>')

                ui.button('Check Coverage', on_click=check_coverage, color='teal').classes('w-full')

    # ── Canvas init script ────────────────────────────────────────────────
    ui.add_body_html(f'''<script>
cfgWhenReady('cov-canvas', function() {{
  window.covAnim = cfgInit('cov-canvas', {gj}, {{ layout: 'layered' }});
  window.covAnim.onCoverageUpdate(function() {{
    const nc = window.covAnim.getCoveragePercent('node');
    const ec = window.covAnim.getCoveragePercent('edge');
    const ncEl = document.getElementById('cov-nc'); if (ncEl) ncEl.textContent = nc + '%';
    const ecEl = document.getElementById('cov-ec'); if (ecEl) ecEl.textContent = ec + '%';
  }});
  // Auto-demo: animate node-coverage paths on load
  setTimeout(function() {{
    const paths = (window.covAnim.graphData.metadata.paths || []);
    if (paths.length) window.covAnim.animateCoverage(paths);
  }}, 900);
}});
</script>''')

    # ── Helpers ───────────────────────────────────────────────────────────

    def _render_requirements():
        crit = crit_sel.value
        req_label.set_text(f'Required Elements ({crit})')
        reqs = _compute_requirements(state['gd'], crit)
        req_container.clear()
        with req_container:
            if not reqs:
                ui.label('No requirements computed.').classes('text-gray-400')
                return
            for i, r in enumerate(reqs[:30]):
                with ui.row().classes('items-center gap-2 border-b border-gray-100 py-0.5'):
                    ui.label(f'{i+1}.').classes('w-6 text-gray-400')
                    ui.label(' → '.join(str(x) for x in r)).classes('font-mono text-blue-600 text-xs')

    crit_sel.on('update:model-value', lambda _: _render_requirements())

    async def on_parse():
        code   = editor.value
        new_gd = CFGBuilder().build(code)
        new_gd = GraphSerializer.inject_layout_hints(new_gd, 'layered')
        state['gd'] = new_gd
        gj2 = json.dumps(new_gd)
        await ui.run_javascript(f'''
          window.covAnim = cfgInit('cov-canvas', {gj2}, {{ layout: 'layered' }});
          window.covAnim.onCoverageUpdate(function() {{
            const nc = window.covAnim.getCoveragePercent('node');
            const ec = window.covAnim.getCoveragePercent('edge');
            const ncEl = document.getElementById('cov-nc'); if (ncEl) ncEl.textContent = nc + '%';
            const ecEl = document.getElementById('cov-ec'); if (ecEl) ecEl.textContent = ec + '%';
          }});
        ''')
        _render_requirements()

    async def on_animate():
        crit  = crit_sel.value
        reqs  = _compute_requirements(state['gd'], crit)
        paths = [r for r in reqs if len(r) >= 2]
        if not paths:
            paths = [[r[0], r[0]] for r in reqs]
        await ui.run_javascript(f'const a=cfgGetAnimator("cov-canvas"); if(a) a.animateCoverage({json.dumps(paths)})')

    parse_btn.on('click', on_parse)
    animate_btn.on('click', on_animate)
    _render_requirements()
