"""Rule-based test-quality heuristics — no LLM, no extra sandbox runs (except
Superficial Modifications' C++ path — see get_cpp_per_test_coverage's docstring
in sandbox_service.py for why that one genuinely needs extra runs).

Each detector works off the same GradeResult already computed for grading
(core/sandbox_service.py). Every detector is a documented heuristic, not a
full static analyzer. Python uses the real `ast` module throughout; C++ has no
parser anywhere in this codebase, so its CFG/Def-Use/Call-Graph/detector logic
is all regex + brace-matching (parser/cpp_cfg_builder.py, parser/cpp_call_graph.py,
parser/cpp_utils.py) — best-effort by construction, same spirit as gcov's own
documented limitation of never reporting never-called functions at all.

CFG note: neither parser/cfg_builder.py's CFGBuilder (Python) nor
parser/cpp_cfg_builder.py's CppCFGBuilder (C++) recurse into nested function/
class bodies — feeding either one a whole file just makes every `def`/method a
single opaque node, useless for pointing at a specific branch inside one. So
for every detector that's actually "about" a branch (Happy Path Bias, Lack of
Partitioning), this module extracts just the one relevant function's source
and builds a CFG for *that* alone (see `_local_functions`) — a small, focused
graph scoped to the function the issue is about, not a merged whole-file graph
(neither builder has cross-function edges to merge with anyway — that's what
the Call Graph tab is for). Assertion Misuse (about the test file) and
Insufficient Method Coverage (about a whole function never being called, not a
branch within one) have no natural CFG to show, so their `graph_data` stays
None — they're still visible on the Call Graph tab, which is issue-independent.
"""
import ast
import re
import textwrap

from core.models import AssignmentLanguage
from core.sandbox_service import GradeResult, get_cpp_per_test_coverage
from graphs.serializer import GraphSerializer
from parser.call_graph import CallGraphBuilder
from parser.cfg_builder import CFGBuilder
from parser.cpp_call_graph import CppCallGraphBuilder
from parser.cpp_cfg_builder import CppCFGBuilder, cpp_function_bodies
from parser.cpp_utils import matching_brace, strip_comments
from parser.du_analyzer import analyze_du

_CATEGORIES = {
    'assertion_misuse': {
        'category': 'Assertion Misuse', 'category_color': '#cf222e', 'severity': 'error',
    },
    'happy_path_bias': {
        'category': 'Happy Path Bias', 'category_color': '#bc4c00', 'severity': 'error',
    },
    'lack_of_partitioning': {
        'category': 'Lack of Partitioning', 'category_color': '#0969da', 'severity': 'error',
    },
    'insufficient_method_coverage': {
        'category': 'Insufficient Method Coverage', 'category_color': '#1a7f37', 'severity': 'warning',
    },
    'superficial_modifications': {
        'category': 'Superficial Modifications', 'category_color': '#8250df', 'severity': 'warning',
    },
    'overfocus_valid_inputs': {
        'category': 'Overfocus on Valid Inputs', 'category_color': '#9a6700', 'severity': 'warning',
    },
    'over_reliance_exception_testing': {
        'category': 'Over-Reliance on Exception Testing', 'category_color': '#0e7490', 'severity': 'warning',
    },
}

# Every issue dict carries these keys even when a detector has nothing to put in
# them (empty list / None) — keeps the shape uniform for the UI.
_ISSUE_DEFAULTS = {
    'affected_test_lines': [], 'affected_source_lines': [],
    'graph_data': None, 'covered_node_ids': [], 'uncovered_node_ids': [], 'covered_path': [],
}


def analyze_test_quality(
    *, language: AssignmentLanguage, source_code: str, test_code: str, result: GradeResult,
) -> list[dict]:
    """Returns 0-7 issue dicts, one per category that actually fired. Only runs once
    coverage was actually measured — same guard the result UI already uses."""
    if result.line_coverage_percent is None:
        return []

    # Per-test line coverage for Superficial Modifications — Python: free, already
    # riding along on result (see _run_python_suite's dynamic_context). C++: a
    # genuinely separate multi-run pass (get_cpp_per_test_coverage's docstring in
    # sandbox_service.py explains why) — only paid for detectors that need it.
    test_line_coverage = (
        result.test_line_coverage if language == AssignmentLanguage.python
        else get_cpp_per_test_coverage(source_code, test_code)
    ) or {}

    detectors = (
        _detect_assertion_misuse, _detect_happy_path_bias,
        _detect_lack_of_partitioning, _detect_insufficient_method_coverage,
        _detect_superficial_modifications, _detect_overfocus_valid_inputs,
        _detect_over_reliance_exception_testing,
    )
    issues = []
    for detector in detectors:
        issue = detector(
            language=language, source_code=source_code, test_code=test_code,
            result=result, test_line_coverage=test_line_coverage,
        )
        if issue is not None:
            merged = dict(_ISSUE_DEFAULTS)
            merged.update(issue)
            issues.append(merged)

    for i, issue in enumerate(issues, start=1):
        issue['id'] = i
    return issues


# ─── Shared helpers ───────────────────────────────────────────────────────────
# C++ brace-matching/comment-stripping live in parser/cpp_utils.py — shared with
# parser/cpp_cfg_builder.py, which needs the exact same primitives.


def _python_function_defs(source_code: str) -> list:
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return []
    return [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _local_function_cfg_python(source_code: str, func_node) -> tuple[dict, int]:
    """Extract just *func_node*'s source, dedent it, and build a CFG scoped to that
    one function — see module docstring for why (CFGBuilder doesn't recurse into
    def bodies). Returns (graph_data, line_offset): a line number in the ORIGINAL
    file maps to this graph's node line numbers via `local = original - line_offset`."""
    lines = source_code.splitlines()
    # Extract the BODY statements only, not the `def foo(...):` line itself — feeding
    # CFGBuilder the whole def would just re-hit the same "doesn't recurse into
    # FunctionDef" problem one level down, since CFGBuilder treats its own top-level
    # `def` statements as opaque too.
    start = func_node.body[0].lineno
    end = func_node.end_lineno or start
    snippet = textwrap.dedent('\n'.join(lines[start - 1:end]))
    graph_data = analyze_du(CFGBuilder().build(snippet))  # adds metadata.du_pairs, node du_def/du_use
    graph_data = GraphSerializer.inject_layout_hints(graph_data, 'layered')
    return graph_data, start - 1


def _local_function_cfg_cpp(body_text: str, body_start_line: int) -> tuple[dict, int]:
    """Same contract as _local_function_cfg_python, for a C++ method body already
    extracted by parser.cpp_cfg_builder.cpp_function_bodies()."""
    graph_data = analyze_du(CppCFGBuilder().build(body_text))
    graph_data = GraphSerializer.inject_layout_hints(graph_data, 'layered')
    return graph_data, body_start_line - 1


def _local_functions(language: AssignmentLanguage, source_code: str):
    """Yields (name, start_line, end_line, graph_data, offset) for every
    function/method in *source_code*, language-dispatched — the one place Happy
    Path Bias and Lack of Partitioning both iterate "every function, in order,
    with its own local CFG" without caring which language they're in."""
    if language == AssignmentLanguage.python:
        for func in _python_function_defs(source_code):
            graph_data, offset = _local_function_cfg_python(source_code, func)
            yield func.name, func.lineno, func.end_lineno or func.lineno, graph_data, offset
    else:
        for name, start, end, body in cpp_function_bodies(source_code):
            graph_data, offset = _local_function_cfg_cpp(body, start)
            yield name, start, end, graph_data, offset


def _local_node_ids(graph_data: dict, offset: int, original_lines, node_type: str | None = None) -> list[int]:
    local_lines = {ln - offset for ln in original_lines if ln > offset}
    return [
        n['id'] for n in graph_data['nodes']
        if (node_type is None or n['type'] == node_type) and local_lines.intersection(n['lines'])
    ]


def _full_covered_path(graph_data: dict, offset: int, uncovered_lines) -> list[int]:
    """One representative entry-to-exit path for the CFG Playback tab to animate —
    the longest of CFGBuilder's precomputed metadata.paths that avoids every node
    coverage.py/gcov explicitly reported as unreached. Deliberately NOT "must be in
    covered_lines": coverage.py doesn't instrument bare docstring statements (or
    other non-executable lines CFGBuilder still makes a node for), so requiring
    explicit coverage would break the path at the very first docstring node in any
    method. A node with no coverage signal either way is assumed reachable; only
    nodes coverage positively flagged as uncovered block a path."""
    local_uncovered = {ln - offset for ln in uncovered_lines if ln > offset}
    allowed = {n['id'] for n in graph_data['nodes'] if not (set(n['lines']) & local_uncovered)}
    candidates = [p for p in graph_data.get('metadata', {}).get('paths', []) if set(p) <= allowed]
    return max(candidates, key=len) if candidates else []


# ─── Assertion Misuse ────────────────────────────────────────────────────────
# Fully static — no coverage needed. A test that never asserts anything can
# never fail, no matter what the code under test does.

_PY_ASSERTION_RE = re.compile(r'(^\s*assert\b|\bpytest\.raises\(|\bself\.assert\w+\()', re.MULTILINE)
_CPP_ASSERTION_RE = re.compile(r'\b(?:ASSERT|EXPECT)_\w+\s*\(')
_CPP_TEST_HEADER_RE = re.compile(r'\b(?:TEST|TEST_F)\s*\(\s*[\w:]+\s*,\s*(\w+)\s*\)\s*\{')


def _python_test_functions(test_code: str) -> list[tuple[str, int, int, str]]:
    """[(name, start_line, end_line, body_text), ...] for every test_* function,
    module-level or nested in a class, 1-indexed inclusive lines."""
    lines = test_code.splitlines()
    out = []
    for node in _python_function_defs(test_code):
        if node.name.startswith('test'):
            start, end = node.lineno, node.end_lineno or node.lineno
            out.append((node.name, start, end, '\n'.join(lines[start - 1:end])))
    return out


def _cpp_test_functions(test_code: str) -> list[tuple[str, int, int, str]]:
    """Same shape as _python_test_functions, for TEST(...)/TEST_F(...) blocks —
    found via brace-matching (doesn't account for braces inside strings/comments,
    good enough for well-formed student test files)."""
    out = []
    for m in _CPP_TEST_HEADER_RE.finditer(test_code):
        open_pos = m.end() - 1
        close_pos = matching_brace(test_code, open_pos)
        start_line = test_code.count('\n', 0, m.start()) + 1
        end_line = test_code.count('\n', 0, close_pos) + 1
        out.append((m.group(1), start_line, end_line, test_code[open_pos:close_pos]))
    return out


def _detect_assertion_misuse(*, language, source_code, test_code, result, **_):
    if language == AssignmentLanguage.python:
        tests, pattern = _python_test_functions(test_code), _PY_ASSERTION_RE
    else:
        tests, pattern = _cpp_test_functions(test_code), _CPP_ASSERTION_RE

    offenders = [(name, start, end) for name, start, end, body in tests if not pattern.search(body)]
    if not offenders:
        return None

    names_preview = ', '.join(name for name, _, _ in offenders[:5])
    more = f' and {len(offenders) - 5} more' if len(offenders) > 5 else ''
    affected_lines = [ln for _, start, end in offenders for ln in range(start, end + 1)]

    issue = dict(_CATEGORIES['assertion_misuse'])
    issue.update({
        'what_it_is': (
            f"{names_preview}{more} exercise the code under test but never assert on the "
            f"result — the return value (or thrown exception) is discarded."
        ),
        'why_it_weakens': (
            "A test with no assertion can never fail. Even if the function under test is "
            "mutated to return the wrong value, this test still passes — it runs code "
            "without checking anything about the outcome."
        ),
        'symptoms': [f'{name} (lines {start}-{end}) has no assert/ASSERT/EXPECT call' for name, start, end in offenders[:5]],
        'affected_test_lines': affected_lines,
    })
    return issue


# ─── Happy Path Bias ─────────────────────────────────────────────────────────
# A guard/exception path in the source that's never exercised by any test.

_PY_RAISE_RE = re.compile(r'^\s*raise\b')
_CPP_THROW_RE = re.compile(r'\bthrow\b')


def _detect_happy_path_bias(*, language, source_code, test_code, result, **_):
    pattern = _PY_RAISE_RE if language == AssignmentLanguage.python else _CPP_THROW_RE
    src_lines = source_code.splitlines()
    scan_lines = (source_code if language == AssignmentLanguage.python else strip_comments(source_code)).splitlines()
    uncovered = set(result.uncovered_lines or [])
    flagged = [
        (i, src_lines[i - 1].strip())
        for i in range(1, len(src_lines) + 1)
        if pattern.search(scan_lines[i - 1]) and i in uncovered
    ]
    if not flagged:
        return None

    graph_data, covered_node_ids, uncovered_node_ids, covered_path = None, [], [], []
    flagged_lines = [ln for ln, _ in flagged]
    for _name, start, end, func_graph, offset in _local_functions(language, source_code):
        in_range = [ln for ln in flagged_lines if start <= ln <= end]
        if not in_range:
            continue
        graph_data = func_graph
        covered = set(result.covered_lines or []) | set(result.partial_lines or [])
        covered_node_ids = _local_node_ids(graph_data, offset, covered)
        uncovered_node_ids = _local_node_ids(graph_data, offset, in_range, node_type='exception')
        covered_path = _full_covered_path(graph_data, offset, result.uncovered_lines or [])
        break

    plural = 's' if len(flagged) != 1 else ''
    issue = dict(_CATEGORIES['happy_path_bias'])
    issue.update({
        'what_it_is': (
            f"No submitted test ever triggers the exception path{plural} at line{plural} "
            f"{', '.join(str(l) for l, _ in flagged)} — every test only exercises valid, in-range input."
        ),
        'why_it_weakens': (
            "Error-handling code is part of the function's contract. If a guard clause "
            "or exception were accidentally removed, or its condition flipped, every "
            "existing test would still pass without noticing."
        ),
        'symptoms': [f'Line {ln}: `{code}` never executes' for ln, code in flagged[:5]],
        'affected_source_lines': [ln for ln, _ in flagged],
        'graph_data': graph_data,
        'covered_node_ids': covered_node_ids,
        'uncovered_node_ids': uncovered_node_ids,
        'covered_path': covered_path,
    })
    return issue


# ─── Lack of Partitioning ────────────────────────────────────────────────────
# Whole branches of the source (not exception branches — those are Happy Path
# Bias's territory) that no test ever takes at all.

def _detect_lack_of_partitioning(*, language, source_code, test_code, result, **_):
    """Scoped to one function at a time (see module docstring) — the first function
    with at least one never-taken branch becomes this issue's visualization. Now
    backed by a real CFG for both languages (parser/cpp_cfg_builder.py gave C++ one
    too — this used to be a brace/regex approximation for C++ specifically)."""
    uncovered = set(result.uncovered_lines or [])
    covered = set(result.covered_lines or []) | set(result.partial_lines or [])
    for name, _start, _end, graph_data, offset in _local_functions(language, source_code):
        node_by_id = {n['id']: n for n in graph_data['nodes']}
        local_uncovered = {ln - offset for ln in uncovered if ln > offset}
        unreached = []
        for e in graph_data['edges']:
            if e['type'] not in ('true-branch', 'false-branch'):
                continue
            target = node_by_id.get(e['to'])
            if target is None or target['type'] == 'exception' or not target['lines']:
                continue
            if local_uncovered.issuperset(target['lines']):
                decision = node_by_id.get(e['from'])
                unreached.append((decision['label'] if decision else '?', e['label'], target))
        if not unreached:
            continue

        plural = 'es' if len(unreached) != 1 else ''
        issue = dict(_CATEGORIES['lack_of_partitioning'])
        issue.update({
            'what_it_is': (
                f"{len(unreached)} branch{plural} of `{name}` are never taken by any test — "
                f"whole input partitions are untested, not just a single missed line."
            ),
            'why_it_weakens': (
                "A mutation anywhere inside an untested branch survives every test in the "
                "suite, since nothing ever executes that code path."
            ),
            'symptoms': [f"`{dec}` → {branch} branch never taken (`{t['label']}`)" for dec, branch, t in unreached[:5]],
            'affected_source_lines': sorted({ln + offset for _, _, t in unreached for ln in t['lines']}),
            'graph_data': graph_data,
            'covered_node_ids': _local_node_ids(graph_data, offset, covered),
            'uncovered_node_ids': [t['id'] for _, _, t in unreached],
            'covered_path': _full_covered_path(graph_data, offset, result.uncovered_lines or []),
        })
        return issue
    return None


# ─── Insufficient Method Coverage ────────────────────────────────────────────
# Functions/methods defined in the source that no test ever calls at all.

def _cpp_method_names(source_code: str) -> set[str]:
    """Reuses cpp_function_bodies() — the same method-discovery pass the C++ CFG
    builder and call graph use — so this detector can never disagree with them
    about which functions exist in the source."""
    return {name for name, *_ in cpp_function_bodies(source_code)}


def _cpp_base_name(qualified_name: str) -> str:
    """C++ function_coverage names are demangled+qualified, e.g.
    'GradeProcessingSystem::scoreToGrade(int)' — strip the param list and any
    class/namespace qualifier down to the bare name so it's comparable against
    what _cpp_method_names extracts from the source."""
    return qualified_name.split('(')[0].rsplit('::', 1)[-1].strip()


def _python_base_name(qualified_name: str) -> str:
    """coverage.py's function_coverage names are dotted, e.g.
    'GradeCalculator.classify_grade' — strip the class qualifier down to the bare
    name so it's comparable against ast.FunctionDef.name (module-level functions
    like 'main' have no dot and pass through unchanged)."""
    return qualified_name.rsplit('.', 1)[-1]


def _detect_insufficient_method_coverage(*, language, source_code, test_code, result, **_):
    covered = [f for f in (result.function_coverage or []) if f.get('lines_covered', 0) > 0]
    if language == AssignmentLanguage.python:
        covered_names = {_python_base_name(f['name']) for f in covered}
        all_names = {n.name for n in _python_function_defs(source_code)}
    else:
        covered_names = {_cpp_base_name(f['name']) for f in covered}
        all_names = _cpp_method_names(source_code)

    untested = sorted(all_names - covered_names)
    if not untested:
        return None

    names_preview = ', '.join(untested[:5])
    more = ' and more' if len(untested) > 5 else ''
    plural = 's' if len(untested) != 1 else ''
    issue = dict(_CATEGORIES['insufficient_method_coverage'])
    issue.update({
        'what_it_is': f"{names_preview}{more} — function{plural} defined in the source that no test ever calls.",
        'why_it_weakens': (
            "An untested function is part of the code's public contract but has zero "
            "fault-detection coverage. It could be broken, return garbage, or crash, and "
            "nothing in the suite would notice."
        ),
        'symptoms': [f'{name}() never appears in the coverage report' for name in untested[:5]],
    })
    return issue


# ─── Superficial Modifications ───────────────────────────────────────────────
# Tests that look distinct (different names, different literal inputs) but
# exercise the exact same source lines as each other — no real added coverage,
# just bulk. Needs test_line_coverage (per-test source-line coverage), which is
# why analyze_test_quality only computes it for this one detector.

def _detect_superficial_modifications(*, language, source_code, test_code, result, test_line_coverage, **_):
    if not test_line_coverage:
        return None

    groups: dict[frozenset, list[str]] = {}
    for name, lines in test_line_coverage.items():
        if lines:
            groups.setdefault(frozenset(lines), []).append(name)
    duplicate_groups = [sorted(names) for names in groups.values() if len(names) > 1]
    if not duplicate_groups:
        return None

    tests = _python_test_functions(test_code) if language == AssignmentLanguage.python else _cpp_test_functions(test_code)
    locations = {name: (start, end) for name, start, end, _body in tests}
    affected_test_lines = sorted({
        ln for names in duplicate_groups for name in names if name in locations
        for ln in range(locations[name][0], locations[name][1] + 1)
    })

    preview = duplicate_groups[:5]
    more = f' and {len(duplicate_groups) - 5} more group(s)' if len(duplicate_groups) > 5 else ''
    plural = 's' if len(duplicate_groups) != 1 else ''
    issue = dict(_CATEGORIES['superficial_modifications'])
    issue.update({
        'what_it_is': (
            f"{len(duplicate_groups)} group{plural} of tests exercise the exact same source lines "
            f"as each other: " + '; '.join(', '.join(names) for names in preview) + more + '.'
        ),
        'why_it_weakens': (
            "Tests that only vary superficially — a different name, a different literal input — "
            "but exercise identical lines add no new fault-detection power over each other. The "
            "same effort spent on a genuinely different input would catch more bugs."
        ),
        'symptoms': [f"{', '.join(names)} all cover the identical set of source lines" for names in preview],
        'affected_test_lines': affected_test_lines,
    })
    return issue


# ─── Overfocus on Valid Inputs ───────────────────────────────────────────────
# The flip side of Happy Path Bias, but about the test file's own inputs rather
# than the source's branches: does the suite ever try a boundary value (zero,
# negative, empty, null) or does every test stick to typical, well-formed input?


def _python_test_literals(test_code: str) -> set:
    literals: set = set()
    for _name, _start, _end, body in _python_test_functions(test_code):
        try:
            tree = ast.parse(textwrap.dedent(body))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and not isinstance(node.value, bool) \
                    and (node.value is None or isinstance(node.value, (int, float, str))):
                literals.add(node.value)
    return literals


def _has_boundary_value(literals: set) -> bool:
    return any(
        v is None or v == '' or (isinstance(v, (int, float)) and v <= 0)
        for v in literals
    )


def _cpp_has_boundary_value(test_code: str) -> bool:
    numbers = re.findall(r'(?<![\w.])-?\d+(?:\.\d+)?', test_code)
    if any(float(n) <= 0 for n in numbers):
        return True
    return bool(re.search(r'""|\{\s*\}', test_code))


def _detect_overfocus_valid_inputs(*, language, source_code, test_code, result, test_line_coverage, **_):
    tests = _python_test_functions(test_code) if language == AssignmentLanguage.python else _cpp_test_functions(test_code)
    if len(tests) < 2:
        return None  # too little to draw a "never varies" conclusion from

    has_boundary = (
        _has_boundary_value(_python_test_literals(test_code)) if language == AssignmentLanguage.python
        else _cpp_has_boundary_value(test_code)
    )
    if has_boundary:
        return None

    names_preview = ', '.join(name for name, *_ in tests[:5])
    more = f' and {len(tests) - 5} more' if len(tests) > 5 else ''
    affected_test_lines = [ln for _, start, end, _ in tests for ln in range(start, end + 1)]

    issue = dict(_CATEGORIES['overfocus_valid_inputs'])
    issue.update({
        'what_it_is': (
            f"None of the {len(tests)} submitted tests ({names_preview}{more}) use a boundary input "
            f"— zero, a negative number, an empty string, or an equivalent edge value — every input "
            f"is a typical, well-formed value."
        ),
        'why_it_weakens': (
            "Boundary values are where off-by-one errors, sign mistakes, and unhandled empty cases "
            "actually surface. A suite that never tries one can pass cleanly against code that "
            "mishandles every edge case."
        ),
        'symptoms': [f'{name} only uses typical, non-boundary input values' for name, *_ in tests[:5]],
        'affected_test_lines': affected_test_lines,
    })
    return issue


# ─── Over-Reliance on Exception Testing ──────────────────────────────────────
# The inverse extreme of Happy Path Bias: most of the suite checks that an
# exception/error is raised, at the expense of verifying ordinary, successful
# behavior.

_PY_RAISES_RE = re.compile(r'\bpytest\.raises\(|\bself\.assertRaises\(')
_CPP_THROWS_RE = re.compile(r'\b(?:ASSERT_THROWS|EXPECT_THROW|EXPECT_ANY_THROW|ASSERT_ANY_THROW)\b')


def _detect_over_reliance_exception_testing(*, language, source_code, test_code, result, test_line_coverage, **_):
    if language == AssignmentLanguage.python:
        tests, pattern = _python_test_functions(test_code), _PY_RAISES_RE
    else:
        tests, pattern = _cpp_test_functions(test_code), _CPP_THROWS_RE

    if len(tests) < 2:
        return None

    exception_tests = [(name, start, end) for name, start, end, body in tests if pattern.search(body)]
    if len(exception_tests) / len(tests) < 0.5:
        return None

    names_preview = ', '.join(name for name, _, _ in exception_tests[:5])
    more = f' and {len(exception_tests) - 5} more' if len(exception_tests) > 5 else ''
    affected_test_lines = [ln for _, start, end in exception_tests for ln in range(start, end + 1)]

    issue = dict(_CATEGORIES['over_reliance_exception_testing'])
    issue.update({
        'what_it_is': (
            f"{len(exception_tests)} of {len(tests)} submitted tests ({names_preview}{more}) check "
            f"only that an exception/error is raised, rather than verifying a normal-case result."
        ),
        'why_it_weakens': (
            "A suite weighted this heavily toward exception checks under-tests ordinary, successful "
            "behavior — a mutation that breaks a normal (non-error) calculation could still pass "
            "every test in the suite."
        ),
        'symptoms': [f'{name} only asserts that an exception is raised' for name, _, _ in exception_tests[:5]],
        'affected_test_lines': affected_test_lines,
    })
    return issue


# ─── Instructor Comparison (rule-based and AI) ───────────────────────────────
# Not one of the 7 rule-based quality detectors above — this builds the same
# issue-dict shape (so it slots into the same Detected Issues list and reuses
# render_issues_section's CFG/Def-Use/Call-Graph tabs with zero UI changes),
# but visualizes the gap between the instructor's reference suite and the
# student's own, computed once in core/assignment_service.py's submit_and_grade
# and passed in here — either the full gap (rule-based mode) or the LLM's own
# chosen subset of it (AI mode, already validated by ai_service against the
# real gap before it ever reaches this function).

def build_comparison_issue(
    *, language: AssignmentLanguage, source_code: str, student_result: GradeResult,
    missed_lines: list[int], highlight_lines: list[int] | None,
    category: str, category_color: str, what_it_is: str, why_it_weakens: str,
) -> dict:
    to_highlight = set(highlight_lines) if highlight_lines else set(missed_lines)
    covered = set(student_result.covered_lines or []) | set(student_result.partial_lines or [])

    graph_data, covered_node_ids, uncovered_node_ids, covered_path = None, [], [], []
    for _name, start, end, func_graph, offset in _local_functions(language, source_code):
        in_range = [ln for ln in to_highlight if start <= ln <= end]
        if not in_range:
            continue
        graph_data = func_graph
        covered_node_ids = _local_node_ids(graph_data, offset, covered)
        uncovered_node_ids = _local_node_ids(graph_data, offset, in_range)
        covered_path = _full_covered_path(graph_data, offset, student_result.uncovered_lines or [])
        break

    issue = dict(_ISSUE_DEFAULTS)
    issue.update({
        'category': category, 'category_color': category_color, 'severity': 'warning',
        'what_it_is': what_it_is, 'why_it_weakens': why_it_weakens,
        'symptoms': [f'Line {ln}' for ln in sorted(to_highlight)[:5]],
        'affected_source_lines': sorted(to_highlight),
        'graph_data': graph_data, 'covered_node_ids': covered_node_ids,
        'uncovered_node_ids': uncovered_node_ids, 'covered_path': covered_path,
    })
    return issue


# ─── Call Graph (Python only) ────────────────────────────────────────────────
# Whole-program view — which functions call which, and which ones no test ever
# reaches at all. Unlike the per-issue CFGs, this isn't scoped to one function:
# CallGraphBuilder walks the whole file directly and isn't blocked by nested
# function bodies the way CFGBuilder is, so no local-extraction trick is needed.

def build_call_graph_view(*, language: AssignmentLanguage, source_code: str, result: GradeResult) -> dict | None:
    """Returns {'graph_data', 'covered_node_ids', 'uncovered_node_ids'}, or None if
    nothing was found (e.g. no functions detected at all)."""
    if language == AssignmentLanguage.python:
        graph_data = GraphSerializer.inject_layout_hints(CallGraphBuilder().build(source_code), 'layered')
        covered_names = {
            _python_base_name(f['name'])
            for f in (result.function_coverage or []) if f.get('lines_covered', 0) > 0
        }
    else:
        graph_data = GraphSerializer.inject_layout_hints(CppCallGraphBuilder().build(source_code), 'layered')
        covered_names = {
            _cpp_base_name(f['name'])
            for f in (result.function_coverage or []) if f.get('lines_covered', 0) > 0
        }

    covered_node_ids = [n['id'] for n in graph_data['nodes'] if n['label'] in covered_names]
    uncovered_node_ids = [n['id'] for n in graph_data['nodes'] if n['label'] not in covered_names]
    return {
        'graph_data': graph_data,
        'covered_node_ids': covered_node_ids,
        'uncovered_node_ids': uncovered_node_ids,
    }
