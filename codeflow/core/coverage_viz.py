"""Renders source code with per-line coverage coloring (green/orange/red) as an inline
HTML string — shared between the student assignment submission view
(pages/student_assignments_page.py) and the worked-examples viewer
(pages/student_examples_page.py), so both read the exact same covered/partial/uncovered
line lists the same way.
"""
import html

COVERAGE_COLORS = {
    'covered': ('#22c55e', 'rgba(34,197,94,.13)'),     # green — executed
    'partial': ('#f97316', 'rgba(249,115,22,.15)'),    # orange — executed, but a branch out of it wasn't
    'uncovered': ('#ef4444', 'rgba(239,68,68,.13)'),   # red — never executed
}


def render_covered_source(source_code: str, covered: list[int], partial: list[int], uncovered: list[int]) -> str:
    status_by_line = {}
    for n in covered:
        status_by_line[n] = 'covered'
    for n in partial:
        status_by_line[n] = 'partial'  # overrides — a branch line is more informative than plain "covered"
    for n in uncovered:
        status_by_line[n] = 'uncovered'

    rows = []
    for i, line in enumerate(source_code.splitlines(), start=1):
        status = status_by_line.get(i)
        border, bg = COVERAGE_COLORS.get(status, ('transparent', 'transparent'))
        rows.append(
            f'<div style="background:{bg};border-left:3px solid {border};padding:0 0 0 6px;white-space:pre">'
            f'<span style="opacity:.4;user-select:none;display:inline-block;width:26px">{i}</span>'
            f'{html.escape(line) if line.strip() else "&nbsp;"}</div>'
        )
    return '<pre style="margin:0;font-family:monospace;font-size:12px;line-height:1.6">' + ''.join(rows) + '</pre>'
