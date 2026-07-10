"""
CodeFlow Visualizer — NiceGUI entry point.
Run: python main.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from nicegui import ui, app

from pages.home          import create_home_page
from pages.cfg_page      import create_cfg_page
from pages.du_page       import create_du_page
from pages.dominator_page import create_dominator_page
from pages.call_graph_page import create_call_graph_page
from pages.coverage_page import create_coverage_page
from pages.quiz_page     import create_quiz_page

# ── Static files & global CSS/JS ────────────────────────────────────────────

app.add_static_files('/static', os.path.join(os.path.dirname(__file__), 'static'))

GLOBAL_HEAD = '''
<script src="/static/engine.js"></script>
<style>
  /* Reset & base */
  *, *::before, *::after { box-sizing: border-box; }
  body { margin:0; font-family: "Inter", system-ui, sans-serif; }

  /* Sidebar */
  .sidebar {
    width: 240px; min-width: 240px;
    background: #0f172a;
    color: #e2e8f0;
    display: flex; flex-direction: column;
    height: 100vh; position: fixed; left: 0; top: 0; z-index: 100;
    overflow-y: auto;
  }
  .sidebar-logo {
    padding: 20px 16px 12px;
    font-size: 18px; font-weight: 700; color: #60a5fa;
    border-bottom: 1px solid #1e293b;
    display: flex; align-items: center; gap: 10px;
  }
  .sidebar-section {
    padding: 12px 16px 4px;
    font-size: 10px; font-weight: 600; letter-spacing: .08em;
    color: #475569; text-transform: uppercase;
  }
  .sidebar-item {
    display: flex; align-items: center; gap: 10px;
    padding: 9px 16px; font-size: 13px; color: #94a3b8;
    text-decoration: none; border-radius: 6px; margin: 1px 8px;
    cursor: pointer; transition: background .15s, color .15s;
  }
  .sidebar-item:hover { background: #1e293b; color: #e2e8f0; }
  .sidebar-item.active { background: #1d4ed8; color: #fff; }
  .sidebar-icon { width: 20px; text-align: center; font-size: 15px; }
  .sidebar-footer {
    margin-top: auto; padding: 12px 16px;
    border-top: 1px solid #1e293b; font-size: 11px; color: #475569;
  }

  /* Main content offset for sidebar */
  .page-content {
    margin-left: 240px;
    min-height: 100vh;
    background: #f8fafc;
  }

  /* Dark mode */
  body.dark .page-content { background: #0f172a; }

  /* Responsive */
  @media (max-width: 1100px) {
    .sidebar { transform: translateX(-240px); }
    .page-content { margin-left: 0; }
  }
</style>
'''

NAV_ITEMS = [
    ('Concepts', [
        ('⬡', 'Control Flow Graph', '/cfg'),
        ('⇆', 'Def-Use Chains',     '/du'),
        ('⊆', 'Dominator Tree',     '/dominators'),
        ('⬢', 'Call Graph',          '/callgraph'),
    ]),
    ('Analysis', [
        ('✓', 'Coverage Criteria',  '/coverage'),
        ('?', 'Quiz Mode',          '/quiz'),
    ]),
    ('', [
        ('⌂', 'Home',               '/'),
    ]),
]


def sidebar_html(current_path: str) -> str:
    items_html = ''
    for section, links in NAV_ITEMS:
        if section:
            items_html += f'<div class="sidebar-section">{section}</div>'
        for icon, label, path in links:
            active = 'active' if path == current_path else ''
            items_html += f'''
              <a class="sidebar-item {active}" href="{path}">
                <span class="sidebar-icon">{icon}</span>
                <span>{label}</span>
              </a>'''

    return f'''
<div class="sidebar" id="app-sidebar">
  <div class="sidebar-logo">
    <span>🔬</span>
    <span>CodeFlow</span>
  </div>
  {items_html}
  <div class="sidebar-footer">
    CodeFlow Visualizer · v1.0<br>
    Software Testing Education
    <div style="margin-top:6px;">
      <button onclick="document.body.classList.toggle('dark')"
              style="background:#1e293b;color:#94a3b8;border:1px solid #334155;
                     padding:3px 10px;border-radius:4px;font-size:11px;cursor:pointer;">
        ☀ / ☾ Toggle Theme
      </button>
    </div>
  </div>
</div>
'''


def create_layout(current_path: str, page_fn):
    """Wrap a page-content function in the shared sidebar + header shell."""
    ui.add_head_html(GLOBAL_HEAD)
    ui.add_body_html(sidebar_html(current_path))

    with ui.element('div').classes('page-content'):
        with ui.element('div').style('padding:16px'):
            page_fn()


# ── Routes ───────────────────────────────────────────────────────────────────

@ui.page('/')
def home():
    create_layout('/', create_home_page)


@ui.page('/cfg')
def cfg():
    create_layout('/cfg', create_cfg_page)


@ui.page('/du')
def du():
    create_layout('/du', create_du_page)


@ui.page('/dominators')
def dominators():
    create_layout('/dominators', create_dominator_page)


@ui.page('/callgraph')
def callgraph():
    create_layout('/callgraph', create_call_graph_page)


@ui.page('/coverage')
def coverage():
    create_layout('/coverage', create_coverage_page)


@ui.page('/quiz')
def quiz():
    create_layout('/quiz', create_quiz_page)


# ── Launch ───────────────────────────────────────────────────────────────────

if __name__ in {'__main__', '__mp_main__'}:
    ui.run(
        port=8080,
        title='CodeFlow Visualizer',
        favicon='🔬',
        storage_secret='codeflow-secret-2024',
        show=False,
    )
