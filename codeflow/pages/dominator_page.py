"""Dominator Tree page (/dominators)."""
import json
from nicegui import ui
from parser.cfg_builder import CFGBuilder
from parser.dominator import compute_dominators
from graphs.examples import EXAMPLE_KEYS, EXAMPLE_NAMES, get_example
from graphs.serializer import GraphSerializer

_DEFAULT = 'while_sum'
_CW, _CH = 800, 520
_STYLE = 'width:100%;height:100%;outline:none;border-radius:8px;background:#f8fafc;border:1px solid #e2e8f0;'


def _build_dom_tree_graph(gd: dict) -> dict:
    """Convert dominator-tree edges into a graph-data dict for GraphAnimator."""
    tree_edges = gd['metadata'].get('dom_tree_edges', [])
    node_ids   = {n['id'] for n in gd['nodes']}
    return {
        'nodes': list(gd['nodes']),
        'edges': [
            {'from': e['from'], 'to': e['to'], 'label': '', 'type': 'flow'}
            for e in tree_edges
            if e['from'] in node_ids and e['to'] in node_ids
        ],
        'metadata': {**gd['metadata'], 'name': 'Dominator Tree'},
    }


def create_dominator_page():
    initial_code = get_example(_DEFAULT)['source']
    gd  = CFGBuilder().build(initial_code)
    gd  = GraphSerializer.inject_layout_hints(gd, 'layered')
    gd  = compute_dominators(gd)
    dom = _build_dom_tree_graph(gd)
    dom = GraphSerializer.inject_layout_hints(dom, 'layered')

    state = {'gd': gd}

    with ui.row().classes('w-full gap-0').style('min-height:620px'):

        # ── Left controls ─────────────────────────────────────────────────
        with ui.column().classes('p-4 gap-3 border-r border-gray-200').style('width:260px;min-width:260px;overflow-y:auto'):
            ui.label('Code').classes('text-sm font-semibold text-gray-500 uppercase')

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

            parse_btn = ui.button('Parse & Compute Dominators', color='primary').classes('w-full')

            ui.separator()
            dom_mode = ui.radio(
                {'forward': 'Forward dominators', 'post': 'Post-dominators'},
                value='forward',
            ).classes('text-sm')

            ui.separator()
            ui.label('Concept').classes('text-sm font-semibold text-gray-500 uppercase')
            ui.html('''
              <div style="font-size:12px;color:#64748b;line-height:1.7;padding:4px 0;">
                <b>Node A dominates node B</b> if <em>every</em> path from Entry
                to B must pass through A.<br><br>
                Click any node on the CFG to highlight its full dominator set.
              </div>
            ''')

        # ── Split canvas (CFG left, dom-tree right) ───────────────────────
        with ui.column().classes('flex-1 p-2'):
            ui.html(f'''
<div style="display:flex;gap:4px;width:100%;height:{_CH}px;">
  <div style="flex:1;position:relative;">
    <div style="position:absolute;top:6px;left:8px;font-size:11px;color:#64748b;font-weight:600;z-index:2;">CFG</div>
    <canvas id="dom-cfg-canvas" width="{_CW//2}" height="{_CH}" style="{_STYLE}"></canvas>
  </div>
  <div style="flex:1;position:relative;">
    <div style="position:absolute;top:6px;left:8px;font-size:11px;color:#64748b;font-weight:600;z-index:2;">Dominator Tree</div>
    <canvas id="dom-tree-canvas" width="{_CW//2}" height="{_CH}" style="{_STYLE}"></canvas>
  </div>
</div>''')

    # ── Canvas init scripts ───────────────────────────────────────────────
    cfg_json = json.dumps(gd)
    dom_json = json.dumps(dom)

    ui.add_body_html(f'''<script>
cfgWhenReady('dom-cfg-canvas', function() {{
  const _idom = {cfg_json}.metadata.idom || {{}};
  window.domCfgAnim  = cfgInit('dom-cfg-canvas',  {cfg_json},  {{ layout: 'layered' }});
  cfgWhenReady('dom-tree-canvas', function() {{
    window.domTreeAnim = cfgInit('dom-tree-canvas', {dom_json}, {{ layout: 'tree' }});
  }});
  window.domCfgAnim.onNodeClick(function(node) {{
    const dominated = [];
    let cur = node.id;
    for (let i = 0; i < 50; i++) {{
      dominated.push(cur);
      const p = _idom[cur];
      if (p === undefined || p === cur) break;
      cur = p;
    }}
    window.domCfgAnim.reset();
    window.domCfgAnim.highlightNodes(dominated, 'highlighted');
  }});
}});
</script>''')

    # ── Parse handler ─────────────────────────────────────────────────────

    async def on_parse():
        code    = editor.value
        new_gd  = CFGBuilder().build(code)
        new_gd  = GraphSerializer.inject_layout_hints(new_gd, 'layered')
        new_gd  = compute_dominators(new_gd)
        new_dom = _build_dom_tree_graph(new_gd)
        new_dom = GraphSerializer.inject_layout_hints(new_dom, 'layered')
        state['gd'] = new_gd

        if dom_mode.value == 'post':
            post_idom = new_gd['metadata'].get('post_idom', {})
            new_dom['edges'] = [
                {'from': p, 'to': c, 'label': '', 'type': 'flow'}
                for c, p in post_idom.items() if c != p
            ]
            new_dom['metadata'] = {**new_dom['metadata'], 'idom': post_idom}

        cj = json.dumps(new_gd)
        dj = json.dumps(new_dom)

        await ui.run_javascript(f'''
          window.domCfgAnim  = cfgInit('dom-cfg-canvas',  {cj}, {{ layout: 'layered' }});
          window.domTreeAnim = cfgInit('dom-tree-canvas', {dj}, {{ layout: 'tree' }});
          window.domCfgAnim.onNodeClick(function(node) {{
            const idomMap = ({cj}).metadata.idom || {{}};
            const dominated = [];
            let cur = node.id;
            for (let i = 0; i < 50; i++) {{
              dominated.push(cur);
              const p = idomMap[cur];
              if (p === undefined || p === cur) break;
              cur = p;
            }}
            window.domCfgAnim.reset();
            window.domCfgAnim.highlightNodes(dominated, 'highlighted');
          }});
        ''')

    parse_btn.on('click', on_parse)
