"""CFG Visualization page (/cfg)."""
import json
from nicegui import ui, app as _app
from parser.cfg_builder import CFGBuilder
from graphs.examples import EXAMPLE_KEYS, EXAMPLE_NAMES, get_example
from graphs.serializer import GraphSerializer


_DEFAULT_EXAMPLE = 'if_else'
_CANVAS_W = 760
_CANVAS_H = 520

_CANVAS_STYLE = (
    'width:100%;height:100%;outline:none;border-radius:8px;'
    'background:#f8fafc;border:1px solid #e2e8f0;'
)


def _init_script(canvas_id: str, graph_json: str) -> str:
    """Returns a <script> block for ui.add_body_html()."""
    return f'''<script>
cfgWhenReady('{canvas_id}', function() {{
  window.cfgAnim = cfgInit('{canvas_id}', {graph_json}, {{ layout: 'layered' }});
  window.cfgAnim.onLog(function(msg) {{
    const log = document.getElementById('cfg-log');
    if (!log) return;
    const line = document.createElement('div');
    line.textContent = msg;
    line.style.cssText = 'padding:2px 0;border-bottom:1px solid #1e293b;';
    log.appendChild(line);
    log.scrollTop = log.scrollHeight;
  }});
  window.cfgAnim.onCoverageUpdate(function() {{
    const nc = window.cfgAnim.getCoveragePercent('node');
    const ec = window.cfgAnim.getCoveragePercent('edge');
    const ncEl = document.getElementById('cfg-nc');
    const ecEl = document.getElementById('cfg-ec');
    if (ncEl) ncEl.textContent = nc + '%';
    if (ecEl) ecEl.textContent = ec + '%';
  }});
  // Auto-demo: play through all paths once so the canvas is alive on load
  setTimeout(function() {{
    const paths = window.cfgAnim.graphData.metadata.paths || [];
    if (paths.length) window.cfgAnim.animateCoverage(paths);
  }}, 800);
}});
</script>'''


def create_cfg_page():
    ui.add_head_html('''
    <style>
      .cfg-panel { background:#fff; border:1px solid #e2e8f0; border-radius:8px; }
      .log-console {
        background:#0f172a; color:#94a3b8; font-family:monospace;
        font-size:11px; padding:8px; overflow-y:auto; border-radius:6px; height:180px;
      }
    </style>
    ''')

    initial_code = _app.storage.user.get('quick_code', '') or \
                   get_example(_DEFAULT_EXAMPLE)['source']

    graph_data = CFGBuilder().build(initial_code)
    graph_data = GraphSerializer.inject_layout_hints(graph_data, 'layered')
    graph_json = json.dumps(graph_data)
    paths      = graph_data['metadata'].get('paths', [])

    state = {'code': initial_code, 'graph_data': graph_data, 'paths': paths}

    with ui.row().classes('w-full gap-0 h-full').style('min-height:600px'):

        # ── Left panel ────────────────────────────────────────────────────
        with ui.column().classes('cfg-panel p-4 gap-3').style('width:280px;min-width:280px;overflow-y:auto'):
            ui.label('Code Editor').classes('font-semibold text-sm text-gray-500 uppercase tracking-wide')

            editor = ui.textarea(value=initial_code) \
                .classes('w-full font-mono text-xs') \
                .props('rows=14 outlined dense')

            example_sel = ui.select(
                {k: n for k, n in zip(EXAMPLE_KEYS, EXAMPLE_NAMES)},
                label='Load example', value=_DEFAULT_EXAMPLE,
            ).classes('w-full')

            def load_example():
                ex = get_example(example_sel.value)
                editor.set_value(ex['source'])

            ui.button('Load Example', on_click=load_example, color='secondary').classes('w-full')
            parse_btn = ui.button('Parse & Generate CFG', color='primary').classes('w-full')

            ui.separator()
            ui.label('Test Paths').classes('font-semibold text-sm text-gray-500 uppercase tracking-wide')
            paths_container = ui.column().classes('w-full gap-1 text-xs')
            path_note = ui.label('Parse code to see paths.').classes('text-xs text-gray-400')

        # ── Center canvas ─────────────────────────────────────────────────
        with ui.column().classes('flex-1 gap-0 p-2'):
            ui.html(
                f'<canvas id="cfg-canvas" width="{_CANVAS_W}" height="{_CANVAS_H}" '
                f'style="{_CANVAS_STYLE}"></canvas>'
            )

        # ── Right panel ───────────────────────────────────────────────────
        with ui.column().classes('cfg-panel p-4 gap-3').style('width:220px;min-width:220px;overflow-y:auto'):
            ui.label('Animation').classes('font-semibold text-sm text-gray-500 uppercase tracking-wide')

            with ui.row().classes('flex-wrap gap-1'):
                async def play_toggle():
                    await ui.run_javascript('''
                      const a = cfgGetAnimator("cfg-canvas");
                      if (!a) return;
                      if (a.isPlaying) {
                        a.pause();
                      } else if (a.steps.length > 0 && a.currentStep < a.steps.length - 1) {
                        a.resume();
                      } else {
                        const paths = a.graphData.metadata.paths || [];
                        if (paths.length) a.animateCoverage(paths);
                      }
                    ''')
                ui.button('▶', on_click=play_toggle).props('flat dense').tooltip('Play / Pause')
                ui.button('⏸', on_click=lambda: ui.run_javascript('cfgGetAnimator("cfg-canvas")?.pause()')).props('flat dense').tooltip('Pause')
                ui.button('⏭', on_click=lambda: ui.run_javascript('cfgGetAnimator("cfg-canvas")?.stepForward()')).props('flat dense').tooltip('Step →')
                ui.button('⏮', on_click=lambda: ui.run_javascript('cfgGetAnimator("cfg-canvas")?.stepBack()')).props('flat dense').tooltip('Step ←')
                ui.button('↺', on_click=lambda: ui.run_javascript('cfgGetAnimator("cfg-canvas")?.reset()')).props('flat dense').tooltip('Reset')

            speed_label = ui.label('Speed: 1×').classes('text-xs text-gray-500')

            def set_speed(v):
                speed_label.set_text(f'Speed: {v}×')
                ui.run_javascript(f'cfgGetAnimator("cfg-canvas")?.setSpeed({v})')

            ui.slider(min=0.5, max=5, step=0.5, value=1, on_change=lambda e: set_speed(e.value)).classes('w-full')

            ui.separator()
            ui.label('Coverage').classes('font-semibold text-sm text-gray-500 uppercase tracking-wide')
            with ui.row().classes('items-center justify-between'):
                ui.label('Node coverage').classes('text-xs')
                ui.html('<span id="cfg-nc" style="font-weight:600;color:#3b82f6">0%</span>')
            with ui.row().classes('items-center justify-between'):
                ui.label('Edge coverage').classes('text-xs')
                ui.html('<span id="cfg-ec" style="font-weight:600;color:#22c55e">0%</span>')

            ui.separator()
            ui.label('Execution Log').classes('font-semibold text-sm text-gray-500 uppercase tracking-wide')
            ui.html('<div id="cfg-log" class="log-console"></div>')

            ui.separator()

            async def export_png():
                await ui.run_javascript('''
                  const c = document.getElementById("cfg-canvas");
                  const a = document.createElement("a");
                  a.href = c.toDataURL("image/png");
                  a.download = "cfg.png"; a.click();
                ''')

            ui.button('Download PNG', on_click=export_png, color='secondary').classes('w-full text-xs')

    # Inject canvas init script (after all HTML is laid out)
    ui.add_body_html(_init_script('cfg-canvas', graph_json))

    # ── Parse button ──────────────────────────────────────────────────────

    async def on_parse():
        code = editor.value
        state['code'] = code
        gd = CFGBuilder().build(code)
        gd = GraphSerializer.inject_layout_hints(gd, 'layered')
        state['graph_data'] = gd
        state['paths']      = gd['metadata'].get('paths', [])
        gj = json.dumps(gd)

        await ui.run_javascript(f'''
          window.cfgAnim = cfgInit('cfg-canvas', {gj}, {{ layout: 'layered' }});
          window.cfgAnim.onLog(function(msg) {{
            const log = document.getElementById('cfg-log');
            if (!log) return;
            const line = document.createElement('div');
            line.textContent = msg;
            line.style.cssText = 'padding:2px 0;border-bottom:1px solid #1e293b;';
            log.appendChild(line); log.scrollTop = log.scrollHeight;
          }});
          window.cfgAnim.onCoverageUpdate(function() {{
            const nc = window.cfgAnim.getCoveragePercent('node');
            const ec = window.cfgAnim.getCoveragePercent('edge');
            document.getElementById('cfg-nc').textContent = nc + '%';
            document.getElementById('cfg-ec').textContent = ec + '%';
          }});
        ''')

        paths_container.clear()
        path_note.set_text('')
        for i, path in enumerate(state['paths'][:20]):
            label_str = f'Path {i+1}: {" → ".join(str(n) for n in path)}'

            async def play_path(p=path):
                await ui.run_javascript(f'const a=cfgGetAnimator("cfg-canvas"); if(a) a.playPath({json.dumps(p)})')

            with paths_container:
                with ui.row().classes('items-center gap-1'):
                    ui.label(label_str).classes('text-xs flex-1 truncate').tooltip(label_str)
                    ui.button('▶', on_click=play_path).props('flat dense size=xs')

        if not state['paths']:
            path_note.set_text('No simple paths found.')

    parse_btn.on('click', on_parse)

    # Build initial path list
    for i, path in enumerate(paths[:20]):
        label_str = f'Path {i+1}: {" → ".join(str(n) for n in path)}'

        async def play_path_init(p=path):
            await ui.run_javascript(f'const a=cfgGetAnimator("cfg-canvas"); if(a) a.playPath({json.dumps(p)})')

        with paths_container:
            with ui.row().classes('items-center gap-1'):
                ui.label(label_str).classes('text-xs flex-1 truncate').tooltip(label_str)
                ui.button('▶', on_click=play_path_init).props('flat dense size=xs')

    if not paths:
        path_note.set_text('No simple paths found.')
