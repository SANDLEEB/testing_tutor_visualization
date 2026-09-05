"""Worked Example viewer (/examples/{example_id}) — shared by the instructor's "Preview"
link and student browsing, same pattern as content_view_page.py / practice_view_page.py.

Shows source code (with a CFG animation alongside it, for Python), the example test
suite, its real measured coverage rendered line-by-line, and the explanation —
everything computed once at authoring time (core/example_service.py), not live here.
"""
import json

from nicegui import app, ui

from auth.db import get_session
from auth.gateway import require_course
from core.coverage_viz import COVERAGE_COLORS, render_covered_source
from core.example_service import get_example
from core.models import AssignmentLanguage

_CANVAS_W = 620
_CANVAS_H = 420
_CANVAS_STYLE = (
    'width:100%;height:100%;outline:none;border-radius:8px;'
    'background:#f8fafc;border:1px solid #e2e8f0;'
)
_CANVAS_ID = 'example-view-canvas'

_LANGUAGE_LABELS = {
    AssignmentLanguage.python: 'Python',
    AssignmentLanguage.cpp: 'C++',
}
_CODE_LANG = {
    AssignmentLanguage.python: 'python',
    AssignmentLanguage.cpp: 'cpp',
}


def _init_script(graph_json: str) -> str:
    return f'''<script>
cfgWhenReady('{_CANVAS_ID}', function() {{
  window.exampleAnim = cfgInit('{_CANVAS_ID}', {graph_json}, {{ layout: 'layered' }});
  setTimeout(function() {{
    const paths = window.exampleAnim.graphData.metadata.paths || [];
    if (paths.length) window.exampleAnim.animateCoverage(paths);
  }}, 800);
}});
</script>'''


def create_example_view_page(example_id: int):
    user = app.storage.user
    course_id = require_course(user)
    if course_id is None:
        return

    with get_session() as session:
        ex = get_example(session, example_id)
        if ex is None:
            ui.label('Example not found.').classes('text-red-500 text-lg m-4')
            return
        is_owner_or_admin = user.get('user_id') == ex.created_by_id or user.get('role') == 'admin'
        if not ex.published and not is_owner_or_admin:
            ui.label('This example has not been published yet.').classes('text-red-500 text-lg m-4')
            return
        if ex.concept.course_id != course_id:
            ui.label('Example not found.').classes('text-red-500 text-lg m-4')
            return

        title, language, published = ex.title, ex.language, ex.published
        topic_name = ex.concept.name
        source_code, test_code, explanation = ex.source_code, ex.test_code, ex.explanation
        graph_json = json.dumps(ex.graph_data) if ex.graph_data else None
        tests_passed = ex.tests_passed
        lines_covered, lines_missed = ex.lines_covered, ex.lines_missed
        branches_covered, branches_missed = ex.branches_covered, ex.branches_missed
        line_pct, branch_pct, total_pct = ex.line_coverage_percent, ex.branch_coverage_percent, ex.total_coverage_percent
        covered_lines, partial_lines, uncovered_lines = ex.covered_lines, ex.partial_lines, ex.uncovered_lines

    code_lang = _CODE_LANG[language]

    ui.label(title).classes('text-2xl font-bold mb-1')
    with ui.row().classes('items-center gap-2 mb-2'):
        ui.label(_LANGUAGE_LABELS[language]).classes('text-xs font-semibold text-[#0969da] uppercase')
        ui.label('·').classes('text-xs text-gray-400')
        ui.label(topic_name).classes('text-xs font-semibold text-[#0969da] uppercase')
        if tests_passed is False:
            ui.label('·').classes('text-xs text-gray-400')
            ui.label('⚠ example tests currently fail').classes('text-xs font-semibold text-red-500 uppercase')
    if not published:
        ui.label('Draft preview — not visible to students until published.').classes('text-sm text-amber-500 mb-2')

    if explanation:
        with ui.card().classes('w-full p-4 mb-4 bg-[#fce4e8]'):
            ui.label('How this example works').classes('text-xs font-semibold text-[#0969da] uppercase tracking-wide mb-1')
            ui.label(explanation).classes('text-sm text-gray-800 whitespace-pre-line')

    with ui.row().classes('w-full gap-4 items-start'):
        with ui.column().classes('flex-1 gap-2'):
            ui.label('Source code').classes('font-semibold text-sm text-gray-500 uppercase tracking-wide')
            ui.code(source_code, language=code_lang).classes('w-full text-xs')

            if graph_json:
                ui.label('Control Flow Graph').classes('font-semibold text-sm text-gray-500 uppercase tracking-wide mt-3')
                ui.html(f'<canvas id="{_CANVAS_ID}" width="{_CANVAS_W}" height="{_CANVAS_H}" style="{_CANVAS_STYLE}"></canvas>')
                ui.add_body_html(_init_script(graph_json))

        with ui.column().classes('flex-1 gap-2'):
            ui.label('Example test suite').classes('font-semibold text-sm text-gray-500 uppercase tracking-wide')
            ui.code(test_code, language=code_lang).classes('w-full text-xs')

            ui.label('Coverage').classes('font-semibold text-sm text-gray-500 uppercase tracking-wide mt-3')
            if line_pct is not None or branch_pct is not None:
                with ui.grid(columns=2).classes('w-full gap-x-4 gap-y-0 text-xs'):
                    lc, lm = lines_covered or 0, lines_missed or 0
                    ui.label(f'Lines: {lc}/{lc + lm} covered ({line_pct}%)').classes('text-gray-600')
                    bc, bm = branches_covered or 0, branches_missed or 0
                    ui.label(f'Branches: {bc}/{bc + bm} covered ({branch_pct}%)').classes('text-gray-600')
                ui.label(f'Total coverage: {total_pct}%').classes('text-xs text-gray-400 mt-1')

                with ui.row().classes('items-center gap-3 text-[11px] text-gray-500 mt-2 mb-1'):
                    for status, label in [('covered', 'Executed'), ('partial', 'Branch missed'), ('uncovered', 'Never run')]:
                        border, _ = COVERAGE_COLORS[status]
                        with ui.row().classes('items-center gap-1'):
                            ui.element('div').style(f'width:10px;height:10px;background:{border};border-radius:2px')
                            ui.label(label)
                ui.html(render_covered_source(source_code, covered_lines, partial_lines, uncovered_lines)) \
                    .classes('w-full border border-gray-200 rounded').style('max-height:280px;overflow-y:auto')
            else:
                ui.label('Coverage could not be measured for this example.').classes('text-xs text-gray-400')

    ui.separator().classes('my-4')
    if user.get('role') in ('faculty', 'admin'):
        ui.button('← Back to Examples', on_click=lambda: ui.navigate.to('/instructor/examples'))
    else:
        ui.button('← Back to Examples', on_click=lambda: ui.navigate.to('/student/examples'))
