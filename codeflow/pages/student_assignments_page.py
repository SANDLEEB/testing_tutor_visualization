"""Student Assignments (/student/assignments) — list + /student/assignments/{id} — work on one."""
import asyncio
import html
import json

from nicegui import app, ui

from auth.db import get_session
from auth.gateway import require_course
from core.assignment_service import (
    get_assignment, latest_submission, list_assignments, list_submissions, mark_assignment_opened,
    submit_and_grade,
)
from core.coverage_viz import COVERAGE_COLORS, render_covered_source
from core.models import AssignmentLanguage, AssignmentType
from core.sandbox_service import TESTKIT_H, check_test_suite_compiles

_TYPE_LABELS = {
    AssignmentType.write_tests: 'Write tests from scratch',
    AssignmentType.evaluate_tests: 'Evaluate a given test suite',
}

_LANGUAGE_LABELS = {
    AssignmentLanguage.python: 'Python',
    AssignmentLanguage.cpp: 'C++',
}

_CODE_LANG = {
    AssignmentLanguage.python: 'python',
    AssignmentLanguage.cpp: 'cpp',
}

_TEST_HINTS = {
    AssignmentLanguage.python: 'must `from solution import ...`',
    AssignmentLanguage.cpp: (
        'must `#include "solution.h"`, then either `#include "testkit.h"` and use '
        'TEST()/ASSERT_*() (see reference below), or `#include <gtest/gtest.h>` and write '
        'standard Google Test (TEST(Suite, Name), EXPECT_*/ASSERT_*)'
    ),
}

# Loosely GitHub-flavored: blue for identity/accent (matches the app's global
# theme — see main.py's ACCENT), green/red/orange/gray for pass/fail/partial/
# neutral state.
_ACCENT = '#0969da'
_ACCENT_BG = '#ddf4ff'


def _submission_to_dict(sub) -> dict:
    return {
        'id': sub.id, 'status': sub.status.value, 'score': sub.score,
        'tests_passed': sub.tests_passed, 'feedback': sub.feedback,
        'concept_feedback': sub.concept_feedback, 'ai_feedback': sub.ai_feedback,
        'lines_covered': sub.lines_covered, 'lines_missed': sub.lines_missed,
        'branches_covered': sub.branches_covered, 'branches_missed': sub.branches_missed,
        'line_coverage_percent': sub.line_coverage_percent,
        'branch_coverage_percent': sub.branch_coverage_percent,
        'total_coverage_percent': sub.total_coverage_percent,
        'covered_lines': sub.covered_lines, 'partial_lines': sub.partial_lines,
        'uncovered_lines': sub.uncovered_lines,
        'statement_coverage_percent': sub.statement_coverage_percent,
        'function_coverage': sub.function_coverage or [],
        'assert_covered': sub.assert_covered, 'assert_total': sub.assert_total,
        'assert_coverage_percent': sub.assert_coverage_percent,
        'detected_issues': sub.detected_issues or [],
        'call_graph_data': sub.call_graph_data,
        'created_at': sub.created_at,
        'duration_seconds': (
            round((sub.created_at - sub.started_at).total_seconds())
            if sub.started_at is not None else None
        ),
    }


def _coverage_color(pct: float | None) -> str:
    """Same red / orange / green bands the reference-suite screenshots used:
    solid red below 50%, orange in the middle, green at 80%+."""
    if pct is None:
        return '#9ca3af'
    if pct < 50:
        return '#ef4444'
    if pct < 80:
        return '#f97316'
    return '#22c55e'


def _history_badge(d: dict) -> tuple[str, str]:
    """(label, color) shown in the Submission History table's status cell."""
    if d['status'] == 'error':
        return 'Error', '#ef4444'
    if d['status'] == 'pending_review':
        return 'Pending', '#9ca3af'
    pct = d['total_coverage_percent']
    return (f'{pct:.0f}%' if pct is not None else '—'), _coverage_color(pct)


def _grade_sync(assignment_id: int, user_id: int, code: str) -> dict:
    """Runs the (blocking, subprocess-based) sandbox grading — call via asyncio.to_thread
    so a single grading run doesn't stall the event loop for every connected user."""
    with get_session() as session:
        assignment = get_assignment(session, assignment_id)
        submission = submit_and_grade(session, assignment, student_id=user_id, submitted_code=code)
        return _submission_to_dict(submission)


def create_student_assignments_list_page():
    course_id = require_course(app.storage.user)
    if course_id is None:
        return

    ui.colors(primary=_ACCENT)  # matches the app-wide accent (main.py); explicit here in case this page's palette ever diverges
    ui.label('Assignments').classes('text-2xl font-bold mb-1')
    ui.label('Published assignments from your instructor.').classes('text-sm text-gray-500 mb-4')

    user_id = app.storage.user['user_id']
    with get_session() as session:
        assignments = list_assignments(session, published_only=True, course_id=course_id, is_practice=False)
        if not assignments:
            ui.label('No assignments published yet.').classes('text-sm text-gray-400')
            return

        with ui.column().classes('w-full gap-2'):
            for a in assignments:
                sub = latest_submission(session, a.id, user_id)
                status = f'{sub.score:.0f}%' if sub and sub.score is not None else \
                    ('Pending review' if sub else 'Not started')
                with ui.row().classes('w-full items-center gap-3 border-b border-gray-100 py-3') \
                        .style('cursor:pointer').on('click', lambda aid=a.id: ui.navigate.to(f'/student/assignments/{aid}')):
                    ui.label(a.title).classes('flex-1 text-sm font-medium')
                    ui.label(_LANGUAGE_LABELS[a.language]).classes('text-xs w-20').style(f'color:{_ACCENT}')
                    ui.label(_TYPE_LABELS[a.type]).classes('text-xs text-gray-500 w-48')
                    ui.label(status).classes('text-sm font-semibold w-32')
                    ui.button('Open →', color='primary').props('flat dense size=sm')


def create_student_assignment_detail_page(assignment_id: int):
    course_id = require_course(app.storage.user)
    if course_id is None:
        return
    user_id = app.storage.user['user_id']

    with get_session() as session:
        a = get_assignment(session, assignment_id)
        if a is None or not a.published or a.course_id != course_id:
            ui.label('Assignment not found.').classes('text-red-500 text-lg m-4')
            return
        title, description, atype, alang = a.title, a.description, a.type, a.language
        source_code, given_test_code = a.source_code, a.given_test_code
        concept_name = a.concept.name if a.concept_id else None
        subs = list_submissions(session, assignment_id, student_id=user_id)  # newest first
        history = [_submission_to_dict(s) for s in subs]
        prior_code = subs[0].submitted_code if subs else ''
        # Opening the page starts a new attempt clock — used for SubmissionTime duration.
        mark_assignment_opened(session, assignment_id, user_id)

    ui.colors(primary=_ACCENT)  # matches the app-wide accent (main.py); explicit here in case this page's palette ever diverges
    ui.add_head_html('''
    <style>
      /* See the comment above code_input: without this, the test-suite textarea
         renders far shorter than its panel, wasting the space below it. */
      .test-suite-input, .test-suite-input .q-field__inner, .test-suite-input .q-field__control,
      .test-suite-input .q-field__control-container { height: 100% !important; }
    </style>
    ''')
    code_lang = _CODE_LANG[alang]
    compiler_name = 'g++' if alang == AssignmentLanguage.cpp else 'python'

    # ── Header ───────────────────────────────────────────────────────────
    with ui.row().classes('items-center gap-1.5 mb-0.5'):
        ui.label('Testing Tutor').classes('text-xs font-semibold uppercase tracking-widest text-gray-400')
        ui.label('/').classes('text-gray-300')
        ui.label('Assignment').classes('text-xs font-semibold uppercase tracking-widest').style(f'color:{_ACCENT}')
    ui.label(title).classes('text-xl font-semibold mb-1')
    if description:
        ui.label(description).classes('text-sm text-gray-600 mb-2')
    with ui.row().classes('items-center gap-2 mb-4'):
        for badge_text in filter(None, [_TYPE_LABELS[atype], _LANGUAGE_LABELS[alang], concept_name]):
            ui.label(badge_text).classes('text-[11px] font-semibold px-2.5 py-0.5 rounded-full') \
                .style(f'background:{_ACCENT_BG};color:{_ACCENT}')

    # ── Code panels — source (read-only) | test suite (editable) ──────────
    with ui.row().classes('w-full gap-0 border border-gray-200 rounded').style('height:400px'):
        with ui.column().classes('flex-1 gap-0 h-full border-r border-gray-200').style('min-width:0'):
            with ui.row().classes('w-full items-center gap-2 px-3 py-2 border-b border-gray-200 shrink-0'):
                ui.label('Source Code').classes('text-xs font-semibold uppercase tracking-widest text-gray-400')
                ui.label(_LANGUAGE_LABELS[alang]).classes('ml-auto text-xs font-mono text-gray-400')
            with ui.column().classes('flex-1 w-full overflow-auto'):
                ui.code(source_code, language=code_lang).classes('w-full text-xs')
                if atype == AssignmentType.evaluate_tests and given_test_code:
                    ui.label('Given test suite').classes('text-xs font-semibold uppercase tracking-widest text-gray-400 px-3 mt-2')
                    ui.code(given_test_code, language=code_lang).classes('w-full text-xs')
                if alang == AssignmentLanguage.cpp:
                    with ui.expansion('Testing framework reference (testkit.h)').classes('w-full mt-2 px-1'):
                        ui.code(TESTKIT_H, language='cpp').classes('w-full text-xs')

        with ui.column().classes('flex-1 gap-0 h-full').style('min-width:0'):
            hint = _TEST_HINTS[alang]
            panel_title = 'Your Test Suite' if atype == AssignmentType.write_tests else "Your Evaluation"
            with ui.row().classes('w-full items-center gap-2 px-3 py-2 border-b border-gray-200 shrink-0'):
                ui.label(panel_title).classes('text-xs font-semibold uppercase tracking-widest text-gray-400')
                ui.label(_LANGUAGE_LABELS[alang]).classes('ml-auto text-xs font-mono text-gray-400')
            # Quasar's outlined q-field only sizes its OWN wrapper to h-full — the nested
            # q-field__inner/control/control-container divs default to content height, so
            # the actual <textarea> inside stays small and everything below it goes to
            # waste. Force the whole chain to 100% so the textarea fills the panel.
            code_input = ui.textarea(value=prior_code).classes('w-full h-full flex-1 font-mono text-xs test-suite-input') \
                .props('outlined hide-bottom-space input-style="font-family:\'JetBrains Mono\',monospace;height:100%"')
    ui.label(hint).classes('text-[11px] text-gray-400 mt-1')

    # ── Compile / Submit action bar ─────────────────────────────────────
    compile_state = {'ok': False}
    with ui.row().classes('w-full items-center gap-2 mt-3'):
        compile_btn = ui.button(f'Compile ({compiler_name})', icon='terminal').props('outline')
        submit_btn = ui.button('Submit for Feedback', color='primary').props('disable')
        compile_status = ui.label("Not compiled yet — compile before submitting.").classes('text-xs text-gray-400')
    compile_output_box = ui.column().classes('w-full gap-1 mt-1')

    def mark_dirty():
        if compile_state['ok']:
            compile_state['ok'] = False
            submit_btn.props('disable')
            compile_status.text = 'Edited since last compile — compile again before submitting.'
            compile_status.classes(replace='text-xs text-amber-600')

    code_input.on_value_change(mark_dirty)

    async def compile_check():
        code = code_input.value.strip()
        if not code:
            ui.notify('Write something first.', color='warning')
            return
        compile_btn.props('loading')
        compile_output_box.clear()
        try:
            result = await asyncio.to_thread(check_test_suite_compiles, alang, source_code, code)
        finally:
            compile_btn.props(remove='loading')
        if result.ok:
            compile_state['ok'] = True
            submit_btn.props(remove='disable')
            compile_status.text = 'Compiled successfully — ready to submit.'
            compile_status.classes(replace='text-xs font-semibold').style(f'color:#1a7f37')
        else:
            compile_state['ok'] = False
            submit_btn.props('disable')
            compile_status.text = 'Compile failed — fix the errors below and compile again.'
            compile_status.classes(replace='text-xs font-semibold text-red-600')
            with compile_output_box:
                ui.html(f'<pre style="white-space:pre-wrap;font-size:11px;background:#0f172a;'
                        f'color:#ffa198;padding:8px;border-radius:6px;max-height:240px;overflow-y:auto;margin:0">'
                        f'{html.escape(result.output)}</pre>')

    compile_btn.on_click(compile_check)

    # ── Submission History — every attempt, oldest first, color-coded by total coverage ──
    ui.label('Submission History').classes('font-semibold text-sm text-gray-500 uppercase tracking-wide mt-5')
    history_box = ui.column().classes('w-full gap-1')

    # ── Result panel — the currently-selected submission's full detail ──
    result_box = ui.column().classes('w-full gap-1 mt-4')

    state = {'selected_id': history[0]['id'] if history else None}

    def render_history():
        history_box.clear()
        if not history:
            with history_box:
                ui.label('No submissions yet — write your tests and submit below.').classes('text-xs text-gray-400')
            return
        chronological = list(reversed(history))  # oldest first, like the reference table
        with history_box:
            with ui.row().classes('w-full items-center gap-3 text-[11px] text-gray-400 uppercase font-semibold px-2'):
                ui.label('#').classes('w-8')
                ui.label('Submitted').classes('w-40')
                ui.label('Tests').classes('w-20')
                ui.label('Coverage').classes('flex-1')
            for i, d in enumerate(chronological, start=1):
                badge_label, badge_color = _history_badge(d)
                selected = d['id'] == state['selected_id']
                row_style = f'background:{_ACCENT_BG}' if selected else ''
                with ui.row().classes('w-full items-center gap-3 border-b border-gray-100 py-1.5 px-2') \
                        .style(f'cursor:pointer;{row_style}').on('click', lambda d=d: select(d['id'])):
                    ui.label(str(i)).classes('w-8 text-xs text-gray-400')
                    ui.label(d['created_at'].strftime('%b %d, %I:%M %p')).classes('w-40 text-xs text-gray-500')
                    ui.label('—' if d['tests_passed'] is None else ('Pass' if d['tests_passed'] else 'Fail')) \
                        .classes('w-20 text-xs ' + ('text-green-600' if d['tests_passed'] else 'text-red-500'))
                    with ui.row().classes('flex-1 items-center gap-2'):
                        ui.label(badge_label).classes('text-xs font-bold px-2 py-0.5 rounded') \
                            .style(f'background:{badge_color}22;color:{badge_color}')

    def select(submission_id: int):
        state['selected_id'] = submission_id
        render_history()
        render_result(next(d for d in history if d['id'] == submission_id))

    def _coverage_bar(pct_label: str, pct: float | None, sub_label: str):
        color = _coverage_color(pct)
        with ui.column().classes('gap-0.5'):
            with ui.row().classes('items-center justify-between w-full'):
                ui.label(pct_label).classes('text-xs text-gray-600')
                ui.label(sub_label).classes('text-[11px] text-gray-400')
            with ui.element('div').classes('w-full rounded').style('background:#e5e7eb;height:6px'):
                width = 0 if pct is None else max(0, min(100, pct))
                ui.element('div').classes('rounded').style(f'background:{color};height:6px;width:{width}%')

    def render_issues_section(issues: list[dict], submission_id: int, call_graph_data: dict | None):
        """Categorized rule-based findings (core/test_quality_service.py) — a clickable
        issue list next to an animated graph panel with three tabs:
          - CFG Playback: engine.js's playPath() steps the covered path node-by-node
            (particle travel), with the issue's uncovered nodes shown red the whole time.
          - Def-Use Chains: animateDuChain() draws each real def→use pair (from
            parser/du_analyzer.py, already baked into the issue's graph_data).
          - Call Graph: whole-program view (Python only — no C++ call-graph builder
            exists) with never-called functions in red; issue-independent, so it's
            available even for issues with no graph_data (Assertion Misuse,
            Insufficient Method Coverage).
        C++ has neither a CFG nor a call graph, so it falls back to a plain
        "Affected lines" callout, same as before."""
        ui.label('Detected Issues').classes('text-xs font-semibold text-gray-500 uppercase tracking-wide mt-5')
        n = len(issues)
        ui.label(f"{n} issue{'s' if n != 1 else ''} detected") \
            .classes('text-[11px] font-semibold px-2.5 py-0.5 rounded-full inline-block mt-1 mb-2') \
            .style('background:#ffebe9;color:#cf222e;border:1px solid #ffcecb;width:fit-content')

        issue_state = {'selected': issues[0]['id'], 'tab': 'cfg'}
        canvas_id = f'issues-cfg-{submission_id}'

        with ui.row().classes('w-full gap-3 items-start border border-gray-200 rounded'):
            list_col = ui.column().classes('gap-0 border-r border-gray-200') \
                .style('width:260px;min-width:260px;flex-shrink:0;max-height:440px;overflow-y:auto')
            detail_col = ui.column().classes('flex-1 gap-2 p-3').style('min-width:0')

        def render_list():
            list_col.clear()
            with list_col:
                for i, issue in enumerate(issues, start=1):
                    selected = issue['id'] == issue_state['selected']
                    color = issue['category_color']
                    row_bg = f'{color}14' if selected else 'transparent'
                    row_border = color if selected else 'transparent'
                    with ui.row().classes('w-full items-start gap-2 px-3 py-2 cursor-pointer') \
                            .style(f'background:{row_bg};border-left:3px solid {row_border}') \
                            .on('click', lambda issue=issue: select_issue(issue)):
                        ui.icon('error' if issue['severity'] == 'error' else 'warning', color=color) \
                            .classes('text-sm mt-0.5')
                        with ui.column().classes('gap-0 min-w-0'):
                            with ui.row().classes('items-center gap-1.5'):
                                ui.label(f'{i:02d}').classes('text-[11px] font-mono text-gray-400')
                                ui.label(issue['category']).classes('text-xs font-semibold') \
                                    .style(f"color:{color if selected else '#1f2328'}")
                            preview = issue['what_it_is'].split('.')[0].strip() + '.'
                            ui.label(preview).classes('text-[11px] text-gray-500 leading-snug')

        def tab_script(issue: dict, tab: str) -> str:
            if tab == 'callgraph':
                graph_json = json.dumps(call_graph_data['graph_data'])
                covered = json.dumps(call_graph_data['covered_node_ids'])
                uncovered = json.dumps(call_graph_data['uncovered_node_ids'])
                body = f'''
  const a = cfgInit('{canvas_id}', {graph_json}, {{ layout: 'layered' }});
  a.highlightNodes({covered}, 'visited');
  a.highlightNodes({uncovered}, 'error');'''
            elif tab == 'duchain':
                graph_json = json.dumps(issue['graph_data'])
                pairs = [{'def': p['def'], 'use': p['use']} for p in issue['graph_data']['metadata'].get('du_pairs', [])]
                body = f'''
  const a = cfgInit('{canvas_id}', {graph_json}, {{ layout: 'layered' }});
  const pairs = {json.dumps(pairs)};
  pairs.forEach(function(p) {{ a.animateDuChain(p.def, [p.use]); }});'''
            else:  # 'cfg'
                graph_json = json.dumps(issue['graph_data'])
                uncovered = json.dumps(issue['uncovered_node_ids'])
                covered = json.dumps(issue['covered_node_ids'])
                path = json.dumps(issue['covered_path'])
                # playPath() calls reset() internally, which would wipe out an
                # uncovered-node highlight made beforehand — so highlight AFTER
                # starting playback, not before (playPath itself is synchronous;
                # the actual step timers haven't fired yet at this point).
                body = f'''
  const a = cfgInit('{canvas_id}', {graph_json}, {{ layout: 'layered' }});
  const path = {path};
  if (path.length) {{ a.playPath(path); a.highlightNodes({uncovered}, 'error'); }}
  else {{ a.highlightNodes({uncovered}, 'error'); a.highlightNodes({covered}, 'visited'); }}'''
            return f"cfgWhenReady('{canvas_id}', function() {{{body}\n}});"

        def render_detail(issue: dict):
            detail_col.clear()
            with detail_col:
                ui.label(issue['category']).classes('text-xs font-semibold px-2.5 py-0.5 rounded-full inline-block') \
                    .style(f"width:fit-content;background:{issue['category_color']}14;"
                           f"color:{issue['category_color']};border:1px solid {issue['category_color']}44")

                tabs = [
                    ('cfg', 'CFG Playback', bool(issue['graph_data'])),
                    ('duchain', 'Def-Use Chains', bool(issue['graph_data'])),
                    ('callgraph', 'Call Graph', call_graph_data is not None),
                ]
                available = [t for t in tabs if t[2]]
                if issue_state['tab'] not in {t[0] for t in available}:
                    issue_state['tab'] = available[0][0] if available else 'cfg'
                active_tab = issue_state['tab']

                if available:
                    with ui.row().classes('items-center gap-1'):
                        for key, label, _ in available:
                            props = 'flat dense no-caps size=sm ' + ('color=primary' if key == active_tab else 'color=grey-7')
                            ui.button(label, on_click=lambda issue=issue, key=key: select_tab(issue, key)).props(props)

                if active_tab == 'duchain' and issue['graph_data'] and not issue['graph_data']['metadata'].get('du_pairs'):
                    ui.label(
                        "No local variable definitions to trace in this function — it only "
                        "uses its parameters directly, with no intermediate assignment."
                    ).classes('text-xs text-gray-400 italic py-4')
                elif available:
                    ui.html(
                        f'<canvas id="{canvas_id}" width="760" height="520" '
                        f'style="width:100%;height:520px;outline:none;border-radius:8px;'
                        f'background:#f8fafc;border:1px solid #e2e8f0;"></canvas>'
                    )
                    with ui.row().classes('items-center gap-1'):
                        ui.button(icon='replay', on_click=lambda: ui.run_javascript(
                            f"cfgGetAnimator('{canvas_id}')?.reset();")).props('flat dense round size=sm').tooltip('Reset')
                        ui.button(icon='skip_previous', on_click=lambda: ui.run_javascript(
                            f"cfgGetAnimator('{canvas_id}')?.stepBack();")).props('flat dense round size=sm').tooltip('Step back')
                        ui.button(icon='play_arrow', on_click=lambda: ui.run_javascript(f'''
const a = cfgGetAnimator('{canvas_id}');
if (a) {{ if (a.isPlaying) a.pause(); else a.resume(); }}
''')).props('flat dense round size=sm').tooltip('Play / Pause')
                        ui.button(icon='skip_next', on_click=lambda: ui.run_javascript(
                            f"cfgGetAnimator('{canvas_id}')?.stepForward();")).props('flat dense round size=sm').tooltip('Step forward')
                        ui.select({0.5: '0.5×', 1: '1×', 2: '2×', 4: '4×'}, value=1,
                                  on_change=lambda e: ui.run_javascript(
                                      f"cfgGetAnimator('{canvas_id}')?.setSpeed({e.value});")) \
                            .props('dense outlined size=sm').style('width:70px')
                    ui.run_javascript(tab_script(issue, active_tab))
                else:
                    lines = issue['affected_source_lines'] or issue['affected_test_lines']
                    if lines:
                        kind = 'source' if issue['affected_source_lines'] else 'test file'
                        preview = ', '.join(str(l) for l in lines[:10])
                        more = f' and {len(lines) - 10} more' if len(lines) > 10 else ''
                        ui.label(f'Affected {kind} lines: {preview}{more}').classes('text-xs text-gray-500 italic')

                with ui.row().classes('w-full items-start gap-0 border border-gray-200 rounded mt-1'):
                    with ui.column().classes('flex-1 gap-1 p-3 border-r border-gray-100'):
                        ui.label('WHAT IT IS').classes('text-[10px] font-semibold text-gray-400 uppercase tracking-wide')
                        ui.label(issue['what_it_is']).classes('text-sm text-gray-800 leading-relaxed')
                    with ui.column().classes('flex-1 gap-1 p-3 border-r border-gray-100'):
                        ui.label('WHY IT WEAKENS THE SUITE').classes('text-[10px] font-semibold text-gray-400 uppercase tracking-wide')
                        ui.label(issue['why_it_weakens']).classes('text-sm text-gray-800 leading-relaxed')
                    with ui.column().classes('flex-1 gap-1 p-3'):
                        ui.label('SYMPTOMS').classes('text-[10px] font-semibold text-gray-400 uppercase tracking-wide')
                        for s in issue['symptoms']:
                            with ui.row().classes('items-start gap-1.5 flex-nowrap'):
                                ui.label('▸').classes('text-xs font-bold').style(f"color:{issue['category_color']}")
                                ui.label(s).classes('text-sm text-gray-800')

        def select_issue(issue: dict):
            issue_state['selected'] = issue['id']
            render_list()
            render_detail(issue)

        def select_tab(issue: dict, tab: str):
            issue_state['tab'] = tab
            render_detail(issue)

        render_list()
        render_detail(issues[0])

    def render_result(r: dict):
        result_box.clear()
        with result_box:
            if r['status'] == 'graded':
                color = 'text-green-600' if r['tests_passed'] else 'text-red-500'
                note = '' if r['tests_passed'] else ' (tests failing — score is 0 until they pass)'
                ui.label(f"Score: {r['score']:.0f}%{note}").classes(f'text-lg font-bold {color}')

                if r['line_coverage_percent'] is not None or r['branch_coverage_percent'] is not None:
                    lc, lm = r['lines_covered'] or 0, r['lines_missed'] or 0
                    bc, bm = r['branches_covered'] or 0, r['branches_missed'] or 0
                    ac, at = r['assert_covered'], r['assert_total']

                    with ui.grid(columns=4).classes('w-full gap-4 mt-2'):
                        _coverage_bar('Line', r['line_coverage_percent'], f"{lc}/{lc + lm}")
                        _coverage_bar('Branch', r['branch_coverage_percent'], f"{bc}/{bc + bm}")
                        _coverage_bar('Statement', r['statement_coverage_percent'], '')
                        _coverage_bar(
                            'Assert', r['assert_coverage_percent'],
                            f"{ac}/{at}" if ac is not None and at is not None else '',
                        )
                    ui.label(
                        f"Overall coverage: {r['total_coverage_percent']}%"
                        + (f" · Time on this attempt: {r['duration_seconds'] // 60}m"
                           f"{r['duration_seconds'] % 60:02d}s" if r['duration_seconds'] is not None else '')
                    ).classes('text-xs text-gray-400 mt-2')

                    if r['function_coverage']:
                        ui.label('Coverage by method / function').classes('text-xs text-gray-500 font-semibold mt-3')
                        with ui.column().classes('w-full gap-1.5 mt-1'):
                            for fn in r['function_coverage']:
                                _coverage_bar(
                                    fn['name'], fn['line_percent'],
                                    f"{fn['lines_covered']}/{fn['lines_total']} lines",
                                )

                    ui.label('Coverage by line').classes('text-xs text-gray-500 font-semibold mt-3')
                    with ui.row().classes('items-center gap-3 text-[11px] text-gray-500 mb-1'):
                        for status, label in [('covered', 'Executed'), ('partial', 'Branch missed'), ('uncovered', 'Never run')]:
                            border, _ = COVERAGE_COLORS[status]
                            with ui.row().classes('items-center gap-1'):
                                ui.element('div').style(f'width:10px;height:10px;background:{border};border-radius:2px')
                                ui.label(label)
                    ui.html(render_covered_source(source_code, r['covered_lines'], r['partial_lines'], r['uncovered_lines'])) \
                        .classes('w-full border border-gray-200 rounded').style('max-height:280px;overflow-y:auto')
                    ui.label(
                        "Condition (MC/DC-style) coverage isn't measured — coverage.py/gcov don't "
                        "track per-sub-expression outcomes, so only line and branch coverage are shown."
                    ).classes('text-[11px] text-gray-300 italic mt-1')

                    if r['detected_issues']:
                        render_issues_section(r['detected_issues'], r['id'], r['call_graph_data'])
            elif r['status'] == 'error':
                ui.label('Your submission timed out during grading.').classes('text-red-500 text-sm')
            else:
                ui.label('Submitted — pending instructor review.').classes('text-amber-600 text-sm')

            if r['feedback']:
                ui.label('Output').classes('text-xs text-gray-500 font-semibold mt-3')
                ui.html(f'<pre style="white-space:pre-wrap;font-size:11px;background:#0f172a;'
                        f'color:#e2e8f0;padding:8px;border-radius:6px;max-height:300px;overflow-y:auto">'
                        f'{html.escape(r["feedback"])}</pre>')

            # Conceptual feedback goes last — it's a reflective summary, read after the numbers.
            if r.get('concept_feedback'):
                ui.label("Compared to the instructor's reference suite").classes('text-xs text-gray-500 font-semibold mt-3')
                ui.label(r['concept_feedback']).classes('text-sm rounded p-2 whitespace-pre-line') \
                    .style(f'color:{_ACCENT};background:{_ACCENT_BG}')

            if r.get('ai_feedback'):
                with ui.row().classes('items-center gap-1.5 mt-3'):
                    ui.label('AI Feedback').classes('text-xs text-gray-500 font-semibold')
                    ui.label('AI').classes('text-[10px] font-bold px-1.5 py-0.5 rounded') \
                        .style('background:#f3e8ff;color:#9333ea')
                ui.label(r['ai_feedback']).classes('text-sm rounded p-2 whitespace-pre-line') \
                    .style('color:#9333ea;background:#f3e8ff')

    render_history()
    if history:
        render_result(history[0])

    async def submit():
        code = code_input.value.strip()
        if not code:
            ui.notify('Write something first.', color='warning')
            return
        submit_btn.props('loading')
        try:
            result = await asyncio.to_thread(_grade_sync, assignment_id, user_id, code)
            history.insert(0, result)
            state['selected_id'] = result['id']
            render_history()
            render_result(result)
            ui.notify('Graded.' if result['status'] == 'graded' else 'Submitted.', color='positive')
        finally:
            submit_btn.props(remove='loading')

    submit_btn.on_click(submit)
