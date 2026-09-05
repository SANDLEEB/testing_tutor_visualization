"""Read-only Content Library viewer (/content/{item_id}).

Renders the CFG animation from graph data that was generated and stored
at authoring time — nothing is re-parsed here. Unpublished items are only
visible to their creator or an admin (instructor preview).
"""
import json
from nicegui import app, ui

from auth.db import get_session
from auth.gateway import require_course
from core.content_service import get_content_item

_CANVAS_W = 760
_CANVAS_H = 520
_CANVAS_STYLE = (
    'width:100%;height:100%;outline:none;border-radius:8px;'
    'background:#f8fafc;border:1px solid #e2e8f0;'
)
_CANVAS_ID = 'content-view-canvas'


def _init_script(graph_json: str) -> str:
    return f'''<script>
cfgWhenReady('{_CANVAS_ID}', function() {{
  window.cfgAnim = cfgInit('{_CANVAS_ID}', {graph_json}, {{ layout: 'layered' }});
  window.cfgAnim.onCoverageUpdate(function() {{
    const nc = window.cfgAnim.getCoveragePercent('node');
    const ec = window.cfgAnim.getCoveragePercent('edge');
    const ncEl = document.getElementById('content-nc');
    const ecEl = document.getElementById('content-ec');
    if (ncEl) ncEl.textContent = nc + '%';
    if (ecEl) ecEl.textContent = ec + '%';
  }});
  setTimeout(function() {{
    const paths = window.cfgAnim.graphData.metadata.paths || [];
    if (paths.length) window.cfgAnim.animateCoverage(paths);
  }}, 800);
}});
</script>'''


def create_content_view_page(item_id: int):
    user = app.storage.user
    course_id = require_course(user)
    if course_id is None:
        return

    with get_session() as session:
        item = get_content_item(session, item_id)
        if item is None:
            ui.label('Content not found.').classes('text-red-500 text-lg m-4')
            return
        is_owner_or_admin = user.get('user_id') == item.created_by_id or user.get('role') == 'admin'
        if not item.published and not is_owner_or_admin:
            ui.label('This content has not been published yet.').classes('text-red-500 text-lg m-4')
            return
        item_course_id = item.concepts[0].concept.course_id if item.concepts else None
        if item_course_id != course_id:
            ui.label('Content not found.').classes('text-red-500 text-lg m-4')
            return
        title = item.title
        published = item.published
        graph_json = json.dumps(item.graph_data)

    ui.label(title).classes('text-2xl font-bold mb-1')
    if not published:
        ui.label('Draft preview — not visible to students until published.').classes('text-sm text-amber-500 mb-2')

    with ui.row().classes('w-full gap-4').style('min-height:560px'):
        with ui.column().classes('flex-1 p-2'):
            ui.html(
                f'<canvas id="{_CANVAS_ID}" width="{_CANVAS_W}" height="{_CANVAS_H}" '
                f'style="{_CANVAS_STYLE}"></canvas>'
            )

        with ui.column().classes('p-4 gap-3').style('width:220px;min-width:220px'):
            ui.label('Animation').classes('font-semibold text-sm text-gray-500 uppercase tracking-wide')
            with ui.row().classes('flex-wrap gap-1'):
                async def play():
                    await ui.run_javascript(f'''
                      const a = cfgGetAnimator("{_CANVAS_ID}");
                      if (!a) return;
                      if (a.isPlaying) {{ a.pause(); }}
                      else if (a.steps.length > 0 && a.currentStep < a.steps.length - 1) {{ a.resume(); }}
                      else {{
                        const paths = a.graphData.metadata.paths || [];
                        if (paths.length) a.animateCoverage(paths);
                      }}
                    ''')
                ui.button('▶', on_click=play).props('flat dense').tooltip('Play / Pause')
                ui.button('⏭', on_click=lambda: ui.run_javascript(f'cfgGetAnimator("{_CANVAS_ID}")?.stepForward()')).props('flat dense').tooltip('Step →')
                ui.button('⏮', on_click=lambda: ui.run_javascript(f'cfgGetAnimator("{_CANVAS_ID}")?.stepBack()')).props('flat dense').tooltip('Step ←')
                ui.button('↺', on_click=lambda: ui.run_javascript(f'cfgGetAnimator("{_CANVAS_ID}")?.reset()')).props('flat dense').tooltip('Reset')

            ui.separator()
            ui.label('Coverage').classes('font-semibold text-sm text-gray-500 uppercase tracking-wide')
            with ui.row().classes('items-center justify-between'):
                ui.label('Node coverage').classes('text-xs')
                ui.html('<span id="content-nc" style="font-weight:600;color:#3b82f6">0%</span>')
            with ui.row().classes('items-center justify-between'):
                ui.label('Edge coverage').classes('text-xs')
                ui.html('<span id="content-ec" style="font-weight:600;color:#22c55e">0%</span>')

    ui.add_body_html(_init_script(graph_json))
    if user.get('role') in ('faculty', 'admin'):
        ui.button('← Back to Manage Content', on_click=lambda: ui.navigate.to('/instructor/content')).classes('mt-4')
    else:
        ui.button('← Back to Library', on_click=lambda: ui.navigate.to('/student/content')).classes('mt-4')
