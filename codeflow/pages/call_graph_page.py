"""Call Graph page (/callgraph)."""
import json
from nicegui import ui
from parser.call_graph import CallGraphBuilder
from graphs.examples import EXAMPLE_KEYS, EXAMPLE_NAMES, get_example
from graphs.serializer import GraphSerializer

_DEFAULT = 'class_methods'
_CW, _CH = 800, 480
_STYLE = 'width:100%;height:100%;outline:none;border-radius:8px;background:#f8fafc;border:1px solid #e2e8f0;'


def create_call_graph_page():
    initial_code = get_example(_DEFAULT)['source']
    cg  = CallGraphBuilder().build(initial_code)
    cg  = GraphSerializer.inject_layout_hints(cg, 'layered')
    cgj = json.dumps(cg)

    state = {'cg': cg}

    with ui.row().classes('w-full gap-0').style('min-height:600px'):

        # ── Left panel ────────────────────────────────────────────────────
        with ui.column().classes('p-4 gap-3 border-r border-gray-200').style('width:280px;min-width:280px;overflow-y:auto'):
            ui.label('Code').classes('text-sm font-semibold text-gray-500 uppercase')

            editor = ui.textarea(value=initial_code) \
                .classes('w-full font-mono text-xs').props('rows=14 outlined dense')

            example_sel = ui.select(
                {k: n for k, n in zip(EXAMPLE_KEYS, EXAMPLE_NAMES)},
                label='Example', value=_DEFAULT,
            ).classes('w-full')

            def load_ex():
                ex = get_example(example_sel.value)
                editor.set_value(ex['source'])
            ui.button('Load Example', on_click=load_ex, color='secondary').classes('w-full')

            parse_btn = ui.button('Parse & Build Call Graph', color='primary').classes('w-full')

            ui.separator()
            ui.label('Simulate').classes('text-sm font-semibold text-gray-500 uppercase')
            sim_btn = ui.button('▶ Simulate Execution', color='teal').classes('w-full')

            ui.separator()
            ui.label('Legend').classes('text-sm font-semibold text-gray-500 uppercase')
            for color, label in [
                ('#22c55e', 'Entry (no callers)'),
                ('#3b82f6', 'Leaf (no callees)'),
                ('#f59e0b', 'Recursive'),
                ('#94a3b8', 'Internal / utility'),
            ]:
                with ui.row().classes('items-center gap-2 text-xs'):
                    ui.html(f'<div style="width:12px;height:12px;border-radius:50%;background:{color}"></div>')
                    ui.label(label)

        # ── Canvas + stack vis ────────────────────────────────────────────
        with ui.column().classes('flex-1 gap-2 p-2'):
            ui.html(f'<canvas id="cg-canvas" width="{_CW}" height="{_CH}" style="{_STYLE}"></canvas>')
            ui.html('''
<div id="cg-stack-vis" style="
  background:#0f172a;color:#e2e8f0;font-family:monospace;font-size:12px;
  padding:10px;border-radius:6px;height:160px;overflow-y:auto;border:1px solid #334155;">
  <div style="color:#64748b;margin-bottom:6px;">Call Stack</div>
  <div id="cg-stack-frames"></div>
</div>''')

    # ── Canvas init + stack helpers ───────────────────────────────────────
    ui.add_body_html(f'''<script>
window._cgStackFrames = [];
window.cgPushFrame = function(name) {{ window._cgStackFrames.push(name); _cgRenderStack(); }};
window.cgPopFrame  = function()     {{ window._cgStackFrames.pop();      _cgRenderStack(); }};
function _cgRenderStack() {{
  const el = document.getElementById('cg-stack-frames');
  if (!el) return;
  el.innerHTML = window._cgStackFrames.slice().reverse().map((f, i) => {{
    const color = i === 0 ? '#3b82f6' : '#64748b';
    return '<div style="border-left:3px solid ' + color + ';padding:2px 6px;margin:2px 0;">' + f + '()</div>';
  }}).join('');
}}
cfgWhenReady('cg-canvas', function() {{
  window.cgAnim = cfgInit('cg-canvas', {cgj}, {{ layout: 'force' }});
  window.cgAnim.onNodeClick(function(node) {{
    const adj = {{}};
    window.cgAnim.graphData.edges.forEach(e => {{ (adj[e.from]=adj[e.from]||[]).push(e.to); }});
    const visited = new Set();
    const q = [node.id];
    while(q.length){{ const c=q.shift(); if(visited.has(c))continue; visited.add(c); (adj[c]||[]).forEach(n=>q.push(n)); }}
    window.cgAnim.reset();
    window.cgAnim.highlightNodes([...visited], 'highlighted');
    window.cgAnim.nodeStates[node.id] = 'active';
  }});
  // Auto-demo: highlight the entry node and its callees on load
  setTimeout(function() {{
    const a = window.cgAnim;
    const entry = a.graphData.nodes.find(n => n.type === 'entry');
    if (!entry) return;
    const adj = {{}};
    a.graphData.edges.forEach(e => {{ (adj[e.from]=adj[e.from]||[]).push(e.to); }});
    const visited = new Set();
    const q = [entry.id];
    while(q.length){{ const c=q.shift(); if(visited.has(c))continue; visited.add(c); (adj[c]||[]).forEach(n=>q.push(n)); }}
    a.highlightNodes([...visited], 'highlighted');
    a.nodeStates[entry.id] = 'active';
  }}, 900);
}});
</script>''')

    # ── Handlers ──────────────────────────────────────────────────────────

    async def on_parse():
        code   = editor.value
        new_cg = CallGraphBuilder().build(code)
        new_cg = GraphSerializer.inject_layout_hints(new_cg, 'layered')
        state['cg'] = new_cg
        cj = json.dumps(new_cg)
        await ui.run_javascript(f'''
          window.cgAnim = cfgInit('cg-canvas', {cj}, {{ layout: 'force' }});
          window.cgAnim.onNodeClick(function(node) {{
            const adj = {{}};
            const data = window.cgAnim.graphData;
            data.edges.forEach(e => {{ (adj[e.from]=adj[e.from]||[]).push(e.to); }});
            const visited = new Set();
            const q = [node.id];
            while(q.length){{ const c=q.shift(); if(visited.has(c))continue; visited.add(c); (adj[c]||[]).forEach(n=>q.push(n)); }}
            window.cgAnim.reset();
            window.cgAnim.highlightNodes([...visited], 'highlighted');
            window.cgAnim.nodeStates[node.id] = 'active';
          }});
        ''')

    async def on_simulate():
        cg    = state['cg']
        nodes = cg['nodes']
        edges = cg['edges']

        entry = next((n for n in nodes if n['type'] == 'entry'), None)
        if not entry:
            return

        adj: dict = {}
        for e in edges:
            adj.setdefault(e['from'], []).append(e['to'])

        id_to_label = {n['id']: n['label'] for n in nodes}
        seq: list = []
        visited: set = set()

        def dfs(nid, depth=0):
            if depth > 10 or nid in visited:
                return
            visited.add(nid)
            seq.append(('push', nid, id_to_label.get(nid, str(nid))))
            for child in adj.get(nid, []):
                dfs(child, depth + 1)
            seq.append(('pop', nid, ''))

        dfs(entry['id'])

        js_cmds = []
        for action, nid, name in seq:
            if action == 'push':
                js_cmds.append(f'cgPushFrame("{name}"); cgAnim.nodeStates[{nid}]="active";')
            else:
                js_cmds.append(f'cgPopFrame(); cgAnim.nodeStates[{nid}]="visited";')

        delay_js = ''.join(
            f'setTimeout(function(){{ {cmd} }}, {i * 450});'
            for i, cmd in enumerate(js_cmds)
        )
        await ui.run_javascript(
            f'window._cgStackFrames=[]; _cgRenderStack(); {delay_js}'
        )

    parse_btn.on('click', on_parse)
    sim_btn.on('click', on_simulate)
