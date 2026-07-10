"""Def-Use Chains page (/du)."""
import json
from nicegui import ui
from parser.cfg_builder import CFGBuilder
from parser.du_analyzer import analyze_du
from graphs.examples import EXAMPLE_KEYS, EXAMPLE_NAMES, get_example
from graphs.serializer import GraphSerializer

_DEFAULT = 'if_else'
_CW, _CH = 760, 520
_STYLE = 'width:100%;height:100%;outline:none;border-radius:8px;background:#f8fafc;border:1px solid #e2e8f0;'


def create_du_page():
    initial_code = get_example(_DEFAULT)['source']
    gd   = CFGBuilder().build(initial_code)
    gd   = GraphSerializer.inject_layout_hints(gd, 'layered')
    gd   = analyze_du(gd)
    gjson = json.dumps(gd)

    state = {'gd': gd, 'selected_var': None}

    with ui.row().classes('w-full gap-0').style('min-height:600px'):

        # ── Left panel ────────────────────────────────────────────────────
        with ui.column().classes('p-4 gap-3 border-r border-gray-200').style('width:280px;min-width:280px;overflow-y:auto'):
            ui.label('Code Editor').classes('text-sm font-semibold text-gray-500 uppercase')

            editor = ui.textarea(value=initial_code) \
                .classes('w-full font-mono text-xs').props('rows=12 outlined dense')

            example_sel = ui.select(
                {k: n for k, n in zip(EXAMPLE_KEYS, EXAMPLE_NAMES)},
                label='Example', value=_DEFAULT,
            ).classes('w-full')

            def load_ex():
                ex = get_example(example_sel.value)
                editor.set_value(ex['source'])
            ui.button('Load Example', on_click=load_ex, color='secondary').classes('w-full')

            parse_btn     = ui.button('Parse & Build DU Chains', color='primary').classes('w-full')

            ui.separator()
            ui.label('Variable').classes('text-sm font-semibold text-gray-500 uppercase')
            var_sel       = ui.select([], label='Select variable').classes('w-full')
            highlight_btn = ui.button('Highlight DU Chain', color='teal').classes('w-full')
            animate_btn   = ui.button('Animate DU Path',   color='orange').classes('w-full')

            ui.separator()
            gen_btn = ui.button('Generate Test Requirements', color='deep-purple').classes('w-full text-xs')

        # ── Canvas ────────────────────────────────────────────────────────
        with ui.column().classes('flex-1 p-2'):
            ui.html(f'<canvas id="du-canvas" width="{_CW}" height="{_CH}" style="{_STYLE}"></canvas>')

        # ── Right panel ───────────────────────────────────────────────────
        with ui.column().classes('p-4 gap-3 border-l border-gray-200').style('width:260px;min-width:260px;overflow-y:auto'):
            ui.label('DU-Pairs').classes('text-sm font-semibold text-gray-500 uppercase')
            table_container = ui.column().classes('w-full gap-1 text-xs')

            ui.separator()
            ui.label('Requirements').classes('text-sm font-semibold text-gray-500 uppercase')
            req_area = ui.textarea(label='All-DU-Paths requirements') \
                .props('outlined readonly rows=8').classes('w-full font-mono text-xs')

    # Canvas init script
    ui.add_body_html(f'''<script>
cfgWhenReady('du-canvas', function() {{
  window.duAnim = cfgInit('du-canvas', {gjson}, {{ layout: 'layered' }});
  // Auto-demo: animate first variable's DU chain on load
  setTimeout(function() {{
    const a = window.duAnim;
    if (!a) return;
    const du = (a.graphData.metadata.du_chains || {{}});
    const vars = Object.keys(du);
    if (!vars.length) return;
    const pairs = du[vars[0]] || [];
    if (!pairs.length) return;
    const defId  = pairs[0].def;
    const useIds = [...new Set(pairs.map(p => p.use))];
    a.animateDuChain(defId, useIds);
  }}, 900);
}});
</script>''')

    # ── Helpers ───────────────────────────────────────────────────────────

    def _rebuild_ui(gd):
        state['gd'] = gd
        du_chains = gd['metadata'].get('du_chains', {})
        all_vars  = list(du_chains.keys())
        var_sel.options = all_vars
        if all_vars:
            var_sel.set_value(all_vars[0])
        _rebuild_table(gd, all_vars[0] if all_vars else None)

    def _rebuild_table(gd, var=None):
        table_container.clear()
        du_chains = gd['metadata'].get('du_chains', {})
        pairs_to_show = du_chains.get(var, []) if var else [
            p for pairs in du_chains.values() for p in pairs
        ]
        if not pairs_to_show:
            with table_container:
                ui.label('No DU-pairs found.').classes('text-gray-400 text-xs')
            return
        with table_container:
            with ui.row().classes('font-semibold text-xs'):
                ui.label('Var').classes('w-12')
                ui.label('Def').classes('w-12')
                ui.label('Use').classes('w-12')
                ui.label('Paths').classes('flex-1')
            for p in pairs_to_show[:40]:
                with ui.row().classes('border-b border-gray-100 py-0.5 text-xs'):
                    ui.label(p['var']).classes('w-12 text-amber-600')
                    ui.label(f'n{p["def"]}').classes('w-12 text-blue-500')
                    ui.label(f'n{p["use"]}').classes('w-12 text-teal-500')
                    ui.label(str(len(p.get('paths', [])))).classes('flex-1')

    # ── Handlers ──────────────────────────────────────────────────────────

    async def on_parse():
        code = editor.value
        gd   = CFGBuilder().build(code)
        gd   = GraphSerializer.inject_layout_hints(gd, 'layered')
        gd   = analyze_du(gd)
        gj   = json.dumps(gd)
        await ui.run_javascript(f'window.duAnim = cfgInit("du-canvas", {gj}, {{layout:"layered"}});')
        _rebuild_ui(gd)

    async def on_highlight():
        gd  = state['gd']
        var = var_sel.value
        if not var:
            return
        pairs   = gd['metadata'].get('du_chains', {}).get(var, [])
        def_ids = list({p['def'] for p in pairs})
        use_ids = list({p['use'] for p in pairs})
        await ui.run_javascript(f'''
        (function() {{
          const a = cfgGetAnimator("du-canvas");
          if (!a) return;
          a.reset();
          {json.dumps(def_ids)}.forEach(id => {{ a.nodeStates[id] = "def"; }});
          {json.dumps(use_ids)}.forEach(id => {{ a.nodeStates[id] = "use"; }});
        }})();
        ''')
        _rebuild_table(gd, var)

    async def on_animate_du():
        gd    = state['gd']
        var   = var_sel.value
        if not var:
            return
        pairs  = gd['metadata'].get('du_chains', {}).get(var, [])
        if not pairs:
            return
        def_id  = pairs[0]['def']
        use_ids = list({p['use'] for p in pairs})
        await ui.run_javascript(f'''
        (function() {{
          const a = cfgGetAnimator("du-canvas");
          if (!a) return;
          a.reset();
          a.animateDuChain({def_id}, {json.dumps(use_ids)});
        }})();
        ''')

    def on_gen_req():
        gd        = state['gd']
        du_chains = gd['metadata'].get('du_chains', {})
        lines     = ['All-DU-Paths Requirements:', '']
        for var, pairs in du_chains.items():
            lines.append(f'Variable: {var}')
            for p in pairs:
                lines.append(f'  ({p["def"]}, {p["use"]}) — {len(p.get("paths", []))} def-clear path(s)')
        req_area.set_value('\n'.join(lines))

    parse_btn.on('click', on_parse)
    highlight_btn.on('click', on_highlight)
    animate_btn.on('click', on_animate_du)
    gen_btn.on('click', on_gen_req)

    _rebuild_ui(gd)
