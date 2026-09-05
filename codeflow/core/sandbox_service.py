"""Sandboxed execution of student-submitted test suites — Python (pytest +
coverage.py) or C++ (g++ + gcov), selected by the assignment's `language`.

SECURITY NOTE — read before pointing this at submissions you don't trust:
this is a best-effort *local* sandbox, not a hardened one. It runs the
student's code as a subprocess with a CPU/wall-clock/memory limit, a
stripped environment (no DATABASE_URL / SESSION_SECRET / API keys), and —
on macOS, via `sandbox-exec` — a Seatbelt profile that denies network access
and restricts writes to its own temp directory. That protection is enforced
by the kernel, not the language runtime, so it applies the same way to a
compiled C++ binary as to the Python interpreter. It does **not** run in a
separate container or VM, and it runs as the same OS user as the app, so a
determined adversarial submitter could still read anything that user can
read on disk. That's an acceptable bar for local dev / a classroom where
students aren't actively trying to break out; it is NOT a substitute for
real isolation (Docker, gVisor, Firecracker, a hosted execution sandbox)
before grading submissions from people you don't trust.
"""
import json
import os
import platform
import re
import resource
import shutil
import subprocess
import sys
import tempfile
import textwrap
from dataclasses import dataclass
from pathlib import Path

from core.models import AssignmentLanguage

_TIMEOUT_SECONDS = 15
_CPU_SECONDS = 10
_MEMORY_BYTES = 512 * 1024 * 1024  # 512MB
_OUTPUT_LIMIT = 4000

_PROJECT_ROOT = Path(__file__).resolve().parent.parent  # codeflow/ — holds .env, source, config.py

# Broad-allow-then-deny-specifics rather than "deny project_root, re-allow .venv":
# subpath rules for .venv (nested inside project_root) and project_root overlap,
# and macOS Seatbelt's rule-precedence for overlapping subpaths isn't reliably
# "most specific wins" in practice — testing it directly broke importing the
# venv's own interpreter. Denying the specific secret file by pattern instead
# sidesteps that ambiguity entirely.
_SEATBELT_PROFILE = textwrap.dedent('''\
    (version 1)
    (deny default)
    (allow process-fork)
    (allow process-exec)
    (allow file-read*)
    (deny file-read* (regex #"\\.env(\\.[a-zA-Z0-9_]+)?$"))
    (deny file-write*)
    (allow file-write* (subpath "{workdir}"))
    (allow file-write-data (literal "/dev/null"))
    (allow file-read-data (literal "/dev/null") (literal "/dev/urandom") (literal "/dev/random"))
    (allow sysctl-read)
    (deny network*)
''')


@dataclass
class GradeResult:
    ran: bool                    # False if the sandbox itself failed to launch
    timed_out: bool
    tests_passed: bool           # exit code 0 (and at least one test collected/run)
    lines_covered: int | None
    lines_missed: int | None
    line_coverage_percent: float | None
    branches_covered: int | None
    branches_missed: int | None
    branch_coverage_percent: float | None
    # Per-line classification for coverage visualization + conceptual feedback grounding:
    # covered = executed and (no branch or all branches taken); partial = executed but at
    # least one branch out of it was never taken (e.g. an `if` that only ever went one way);
    # uncovered = never executed at all.
    covered_lines: list[int]
    partial_lines: list[int]
    uncovered_lines: list[int]
    # Statement coverage: for both coverage.py and gcov, a "statement" and a "line" are the
    # same trackable unit — this is the same underlying number as line_coverage_percent,
    # exposed under its own name because it's a distinct, commonly-reported metric that
    # instructors expect to see labeled on its own, not because it's independently measured.
    statement_coverage_percent: float | None
    # Per-function/method breakdown: [{'name': str, 'lines_covered': int, 'lines_total': int,
    # 'line_percent': float}, ...]. Python: exact, from coverage.py's own per-function report.
    # C++: from gcov's annotated output, demangled via c++filt — functions never called at
    # all don't get a gcov entry, so they simply won't appear here (documented limitation,
    # not fabricated as 0%).
    function_coverage: list[dict]
    # How many of the test suite's own assert statements actually executed — catches a test
    # that throws/returns before reaching some of its assertions. Measured by running
    # coverage on the test file itself too, not guessed.
    assert_covered: int | None
    assert_total: int | None
    assert_coverage_percent: float | None
    score: float                 # 0..100 — blended line+branch % if tests_passed else 0
    output: str                  # truncated combined stdout/stderr, for feedback
    # Per-test line coverage: {test_name: [covered_line_numbers]} — which lines THAT ONE
    # test hit, not the aggregate. Feeds the Superficial Modifications detector (two tests
    # with the same covered-node-set add no fault-detection value). Python: free — riding
    # along on the same coverage run via coverage.py's dynamic_context setting, no extra
    # process. C++: a genuinely separate multi-run pass (see get_cpp_per_test_coverage) —
    # gcov has no equivalent "which test hit this line" tagging, so None here always;
    # callers needing C++ per-test data call that function directly instead.
    test_line_coverage: dict[str, list[int]] | None = None

    @property
    def total_coverage_percent(self) -> float | None:
        """Blended line+branch coverage — averages whichever of the two are available."""
        parts = [p for p in (self.line_coverage_percent, self.branch_coverage_percent) if p is not None]
        return round(sum(parts) / len(parts), 1) if parts else None


def _limit_resources():
    resource.setrlimit(resource.RLIMIT_CPU, (_CPU_SECONDS, _CPU_SECONDS))
    try:
        resource.setrlimit(resource.RLIMIT_AS, (_MEMORY_BYTES, _MEMORY_BYTES))
    except (ValueError, OSError):
        pass  # RLIMIT_AS isn't enforced on every platform — best effort


def _sandbox_env(workdir: Path) -> dict:
    return {
        'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
        'HOME': str(workdir),
        'TMPDIR': str(workdir),
        'LANG': 'en_US.UTF-8',
        'PYTHONDONTWRITEBYTECODE': '1',
    }


def _wrap_with_seatbelt(cmd: list[str], workdir: Path) -> list[str]:
    if platform.system() != 'Darwin' or not shutil.which('sandbox-exec'):
        return cmd
    profile = _SEATBELT_PROFILE.format(workdir=workdir)
    return ['sandbox-exec', '-p', profile, *cmd]


def _run(
    cmd: list[str], workdir: Path, timeout: int = _TIMEOUT_SECONDS, extra_env: dict | None = None,
) -> subprocess.CompletedProcess | None:
    env = _sandbox_env(workdir)
    if extra_env:
        env.update(extra_env)
    try:
        return subprocess.run(
            _wrap_with_seatbelt(cmd, workdir),
            cwd=workdir,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            preexec_fn=_limit_resources,
        )
    except subprocess.TimeoutExpired:
        return None


@dataclass
class CompileCheckResult:
    ok: bool
    output: str  # empty when ok


def _timeout_result(seconds: int) -> GradeResult:
    return GradeResult(
        ran=True, timed_out=True, tests_passed=False,
        lines_covered=None, lines_missed=None, line_coverage_percent=None,
        branches_covered=None, branches_missed=None, branch_coverage_percent=None,
        covered_lines=[], partial_lines=[], uncovered_lines=[],
        statement_coverage_percent=None, function_coverage=[],
        assert_covered=None, assert_total=None, assert_coverage_percent=None,
        score=0.0, output=f'Execution timed out after {seconds}s.',
    )


def _no_coverage_result(*, ran: bool, tests_passed: bool, output: str) -> GradeResult:
    return GradeResult(
        ran=ran, timed_out=False, tests_passed=tests_passed,
        lines_covered=None, lines_missed=None, line_coverage_percent=None,
        branches_covered=None, branches_missed=None, branch_coverage_percent=None,
        covered_lines=[], partial_lines=[], uncovered_lines=[],
        statement_coverage_percent=None, function_coverage=[],
        assert_covered=None, assert_total=None, assert_coverage_percent=None,
        score=0.0, output=output,
    )


_PY_ASSERT_LINE_RE = re.compile(r'^\s*assert\b')
# Matches both testkit.h's ASSERT_TRUE/EQ/THROWS and real gtest's broader
# ASSERT_*/EXPECT_* family (EQ, NE, LT, STREQ, THROW, ...).
_CPP_ASSERT_LINE_RE = re.compile(r'\b(?:ASSERT|EXPECT)_\w+\s*\(')
_GTEST_INCLUDE_RE = re.compile(r'#include\s*[<"]gtest/gtest\.h[>"]')


def _assert_line_numbers(test_code: str, pattern: re.Pattern) -> list[int]:
    return [i for i, line in enumerate(test_code.splitlines(), start=1) if pattern.search(line)]


# ── Python: pytest + coverage.py ────────────────────────────────────────────

_COVERAGERC = '''\
[run]
branch = True
source = solution,test_submission
dynamic_context = test_function
'''


def _run_python_suite(source_code: str, test_code: str, workdir: Path) -> GradeResult:
    (workdir / 'solution.py').write_text(source_code)
    (workdir / 'test_submission.py').write_text(test_code)
    (workdir / '.coveragerc').write_text(_COVERAGERC)

    # dynamic_context tags every covered line with which test hit it — read back below
    # into GradeResult.test_line_coverage — at no extra cost: same single run already
    # needed for grading. Both files in source= so the test file's own execution is
    # tracked too, needed for assert-statement coverage, not just solution.py's.
    run_proc = _run(
        [sys.executable, '-I', '-m', 'coverage', 'run', '--rcfile=.coveragerc',
         '-m', 'pytest', 'test_submission.py', '-q', '--tb=short', '--no-header'],
        workdir,
    )
    if run_proc is None:
        return _timeout_result(_TIMEOUT_SECONDS)

    output = (run_proc.stdout + run_proc.stderr)[:_OUTPUT_LIMIT]
    tests_passed = run_proc.returncode == 0

    lines_covered = lines_missed = line_pct = statement_pct = None
    branches_covered = branches_missed = branch_pct = None
    covered_lines: list[int] = []
    partial_lines: list[int] = []
    uncovered_lines: list[int] = []
    function_coverage: list[dict] = []
    assert_covered = assert_total = assert_pct = None
    test_line_coverage: dict[str, list[int]] = {}

    json_proc = _run(
        [sys.executable, '-I', '-m', 'coverage', 'json', '--show-contexts', '-o', 'coverage.json'], workdir,
    )
    coverage_json_path = workdir / 'coverage.json'
    if json_proc is not None and coverage_json_path.exists():
        try:
            data = json.loads(coverage_json_path.read_text())
            # Read solution.py's own summary, not the global totals — totals now blends in
            # test_submission.py too (tracked so its own assert-line coverage is available),
            # and mixing that into "the assignment's coverage" would misreport it.
            file_report = data.get('files', {}).get('solution.py', {})
            totals = file_report.get('summary', {})
            lines_covered = totals.get('covered_lines')
            lines_missed = totals.get('missing_lines')
            if 'percent_covered' in totals:
                line_pct = round(totals['percent_covered'], 1)
                statement_pct = round(totals.get('percent_statements_covered', totals['percent_covered']), 1)
            branches_covered = totals.get('covered_branches')
            branches_missed = totals.get('missing_branches')
            if totals.get('num_branches'):
                branch_pct = round(totals['percent_branches_covered'], 1)

            uncovered_lines = list(file_report.get('missing_lines', []))
            executed = set(file_report.get('executed_lines', []))
            # A line is "partial" if it's a branch point (an `if`/`while`/etc.) that ran but
            # didn't take every direction out of it — e.g. an `if` whose else was never hit.
            partial_source_lines = {edge[0] for edge in file_report.get('missing_branches', [])}
            partial_lines = sorted(executed & partial_source_lines)
            covered_lines = sorted(executed - partial_source_lines)

            # Per-function breakdown — coverage.py already computes this; '' is module-level
            # (top-of-file) code, not a real function, so it's excluded.
            for name, fn in file_report.get('functions', {}).items():
                if not name:
                    continue
                fn_summary = fn.get('summary', {})
                fn_total = fn_summary.get('num_statements', 0)
                fn_covered = fn_summary.get('covered_lines', 0)
                function_coverage.append({
                    'name': name, 'lines_covered': fn_covered, 'lines_total': fn_total,
                    'line_percent': round(fn_covered / fn_total * 100, 1) if fn_total else 0.0,
                })

            # Assert-statement coverage: which assert lines in the test file itself executed.
            test_report = data.get('files', {}).get('test_submission.py', {})
            test_executed = set(test_report.get('executed_lines', []))
            assert_lines = _assert_line_numbers(test_code, _PY_ASSERT_LINE_RE)
            if assert_lines:
                assert_total = len(assert_lines)
                assert_covered = sum(1 for n in assert_lines if n in test_executed)
                assert_pct = round(assert_covered / assert_total * 100, 1)

            # Per-test line coverage, from dynamic_context's per-line context tags —
            # 'test_submission.test_name' (or '' for module-level/import-time code,
            # which isn't attributable to any one test and is dropped here).
            for line_str, ctxs in file_report.get('contexts', {}).items():
                for ctx in ctxs:
                    if not ctx:
                        continue
                    test_name = ctx.rsplit('.', 1)[-1]
                    test_line_coverage.setdefault(test_name, []).append(int(line_str))
        except (json.JSONDecodeError, KeyError):
            pass

    result = GradeResult(
        ran=True, timed_out=False, tests_passed=tests_passed,
        lines_covered=lines_covered, lines_missed=lines_missed, line_coverage_percent=line_pct,
        branches_covered=branches_covered, branches_missed=branches_missed, branch_coverage_percent=branch_pct,
        covered_lines=covered_lines, partial_lines=partial_lines, uncovered_lines=uncovered_lines,
        statement_coverage_percent=statement_pct, function_coverage=function_coverage,
        assert_covered=assert_covered, assert_total=assert_total, assert_coverage_percent=assert_pct,
        score=0.0, output=output, test_line_coverage=test_line_coverage or None,
    )
    result.score = result.total_coverage_percent if (tests_passed and result.total_coverage_percent is not None) else 0.0
    return result


def _check_python_source(source_code: str, workdir: Path) -> CompileCheckResult:
    """Syntax-check *source_code* on its own — compiles to bytecode without executing
    any top-level code, so this is safe to run on instructor-submitted source before
    it's ever paired with a test suite."""
    (workdir / 'solution.py').write_text(source_code)
    proc = _run([sys.executable, '-I', '-m', 'py_compile', 'solution.py'], workdir)
    if proc is None:
        return CompileCheckResult(ok=False, output=f'Compile check timed out after {_TIMEOUT_SECONDS}s.')
    if proc.returncode != 0:
        return CompileCheckResult(ok=False, output=(proc.stdout + proc.stderr).strip()[:_OUTPUT_LIMIT])
    return CompileCheckResult(ok=True, output='')


def _check_python_test_suite(source_code: str, test_code: str, workdir: Path) -> CompileCheckResult:
    """Confirms *test_code* imports cleanly against *source_code* and pytest can collect
    its tests — the Python equivalent of "does this compile" (no test bodies are run)."""
    (workdir / 'solution.py').write_text(source_code)
    (workdir / 'test_submission.py').write_text(test_code)
    proc = _run(
        [sys.executable, '-I', '-m', 'pytest', 'test_submission.py', '--collect-only', '-q', '--no-header'],
        workdir,
    )
    if proc is None:
        return CompileCheckResult(ok=False, output=f'Compile check timed out after {_TIMEOUT_SECONDS}s.')
    if proc.returncode != 0:
        return CompileCheckResult(ok=False, output=(proc.stdout + proc.stderr).strip()[:_OUTPUT_LIMIT])
    return CompileCheckResult(ok=True, output='')


# ── C++: g++ + a tiny header-only test framework + gcov ─────────────────────

# Injected alongside the student's test file — nothing for them to write, just
# `#include "testkit.h"`. Shown to students on the assignment page as a
# reference for what TEST()/ASSERT_*() are available.
TESTKIT_H = textwrap.dedent('''\
    #pragma once
    #include <cstdlib>
    #include <functional>
    #include <iostream>
    #include <string>
    #include <vector>

    struct _TestCase { std::string name; std::function<void()> fn; };
    inline std::vector<_TestCase>& _testkit_registry() {
        static std::vector<_TestCase> tests;
        return tests;
    }
    struct _TestRegistrar {
        _TestRegistrar(const std::string& name, std::function<void()> fn) {
            _testkit_registry().push_back({name, fn});
        }
    };
    struct AssertionFailure { std::string message; };

    #define TEST(name) \\
        void name(); \\
        static _TestRegistrar _registrar_##name(#name, name); \\
        void name()

    #define ASSERT_TRUE(cond) \\
        if (!(cond)) throw AssertionFailure{"ASSERT_TRUE failed: " #cond}
    #define ASSERT_EQ(a, b) \\
        if (!((a) == (b))) throw AssertionFailure{"ASSERT_EQ failed: " #a " != " #b}
    #define ASSERT_THROWS(expr, exc_type) \\
        do { \\
            bool _threw = false; \\
            try { expr; } catch (const exc_type&) { _threw = true; } \\
            if (!_threw) throw AssertionFailure{"ASSERT_THROWS failed: " #expr " did not throw " #exc_type}; \\
        } while (0)

    int main() {
        const char* _filter = std::getenv("TESTKIT_FILTER");
        int passed = 0, failed = 0;
        for (auto& t : _testkit_registry()) {
            if (_filter && t.name != _filter) continue;
            try {
                t.fn();
                std::cout << "PASS " << t.name << "\\n";
                passed++;
            } catch (const AssertionFailure& e) {
                std::cout << "FAIL " << t.name << ": " << e.message << "\\n";
                failed++;
            } catch (const std::exception& e) {
                std::cout << "FAIL " << t.name << ": uncaught exception: " << e.what() << "\\n";
                failed++;
            } catch (...) {
                std::cout << "FAIL " << t.name << ": unknown exception\\n";
                failed++;
            }
        }
        std::cout << "\\n" << passed << " passed, " << failed << " failed\\n";
        return failed == 0 ? 0 : 1;
    }
''')

_GCOV_LINES_RE = re.compile(r"Lines executed:([\d.]+)% of (\d+)")
_GCOV_BRANCHES_RE = re.compile(r"Branches executed:([\d.]+)% of (\d+)")
_GCOV_ANNOTATED_LINE_RE = re.compile(r'^\s*(#####|-|\d+):\s*(\d+):')
_GCOV_BRANCH_TAKEN_RE = re.compile(r'^branch\s+\d+\s+taken\s+([\d.]+)%')
_GCOV_FUNC_HEADER_RE = re.compile(r'^function (\S+) called (\d+) returned \d+% blocks executed \d+%')
_XCODE_NOISE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}.*(xcodebuild|DVT\w+):', re.MULTILINE)


def _demangle(name: str) -> str:
    cxxfilt = shutil.which('c++filt')
    if not cxxfilt:
        return name
    try:
        proc = subprocess.run([cxxfilt, '-n', name], capture_output=True, text=True, timeout=5)
        return proc.stdout.strip() or name
    except (subprocess.TimeoutExpired, OSError):
        return name


def _parse_gcov_function_coverage(gcov_text: str) -> list[dict]:
    """Per-function line coverage from an annotated `gcov -b` dump. gcov only emits a
    `function NAME called ...` header for functions that were called at least once — a
    function that's never invoked at all simply has no header and won't appear here,
    rather than being reported as a fabricated 0%.
    """
    functions: list[dict] = []
    current: dict | None = None

    for raw in gcov_text.splitlines():
        m = _GCOV_FUNC_HEADER_RE.match(raw)
        if m:
            if current is not None:
                functions.append(current)
            current = {'name': _demangle(m.group(1)), 'lines_covered': 0, 'lines_total': 0}
            continue
        lm = _GCOV_ANNOTATED_LINE_RE.match(raw)
        if lm and current is not None:
            marker = lm.group(1)
            if marker == '-':
                continue
            current['lines_total'] += 1
            if marker != '#####':
                current['lines_covered'] += 1

    if current is not None:
        functions.append(current)

    for fn in functions:
        fn['line_percent'] = round(fn['lines_covered'] / fn['lines_total'] * 100, 1) if fn['lines_total'] else 0.0
    return functions


def _parse_gcov_annotated_lines(gcov_text: str) -> tuple[list[int], list[int], list[int]]:
    """Walk an un-annotated `gcov` (no -n) source dump and classify each executable line
    as covered / partial (executed but a branch out of it was never taken) / uncovered —
    same three-way split as the Python path, from a different tool's output format."""
    covered: list[int] = []
    partial_candidates: set[int] = set()
    uncovered: list[int] = []
    current_line: int | None = None

    for raw in gcov_text.splitlines():
        m = _GCOV_ANNOTATED_LINE_RE.match(raw)
        if m:
            marker, lineno = m.group(1), int(m.group(2))
            if marker == '-':
                current_line = None  # not an executable line — ignore any branch lines under it
            elif marker == '#####':
                current_line = lineno
                uncovered.append(lineno)
            else:
                current_line = lineno
                covered.append(lineno)
            continue
        bm = _GCOV_BRANCH_TAKEN_RE.match(raw)
        if bm and current_line is not None and float(bm.group(1)) == 0.0:
            partial_candidates.add(current_line)

    partial = sorted(partial_candidates & set(covered))
    covered = sorted(set(covered) - partial_candidates)
    return covered, partial, sorted(uncovered)


def _cpp_compiler() -> str | None:
    return shutil.which('g++') or shutil.which('clang++')


_gtest_prefix_cache: Path | None | bool = False  # False = not looked up yet, None = not found


def _gtest_prefix() -> Path | None:
    """Locate an installed Google Test (via Homebrew) — checked once per process.
    Returns the Homebrew formula prefix (containing include/gtest and lib/libgtest.a),
    or None if it's not installed on this machine."""
    global _gtest_prefix_cache
    if _gtest_prefix_cache is not False:
        return _gtest_prefix_cache
    _gtest_prefix_cache = None
    brew = shutil.which('brew')
    if brew:
        proc = subprocess.run([brew, '--prefix', 'googletest'], capture_output=True, text=True, timeout=10)
        if proc.returncode == 0:
            candidate = Path(proc.stdout.strip())
            if (candidate / 'include' / 'gtest' / 'gtest.h').exists():
                _gtest_prefix_cache = candidate
    return _gtest_prefix_cache


def _gtest_compile_flags(test_code: str) -> tuple[list[str], list[str]] | str:
    """If *test_code* `#include`s real gtest, returns (extra_compile_args, extra_link_args)
    to build against it — or an error string if gtest isn't available on this server.
    Returns ([], []) when the test doesn't use gtest at all (e.g. testkit.h instead)."""
    if not _GTEST_INCLUDE_RE.search(test_code):
        return [], []
    prefix = _gtest_prefix()
    if prefix is None:
        return "This test suite #include<gtest/gtest.h> but Google Test isn't installed on this server."
    return ['-I', str(prefix / 'include')], ['-L', str(prefix / 'lib'), '-lgtest', '-lgtest_main', '-pthread']


def _clean_compiler_output(text: str) -> str:
    """Strip macOS Xcode-toolchain noise (DARWIN_USER_CACHE_DIR warnings etc.)
    that shows up because the sandbox points HOME at a throwaway temp dir."""
    return '\n'.join(line for line in text.splitlines() if not _XCODE_NOISE_RE.match(line)).strip()


def _run_cpp_suite(source_code: str, test_code: str, workdir: Path) -> GradeResult:
    compiler = _cpp_compiler()
    if compiler is None:
        return _no_coverage_result(
            ran=False, tests_passed=False,
            output='No C++ compiler (g++/clang++) available on the server.',
        )

    gtest_flags = _gtest_compile_flags(test_code)
    if isinstance(gtest_flags, str):
        return _no_coverage_result(ran=False, tests_passed=False, output=gtest_flags)
    gtest_compile_args, gtest_link_args = gtest_flags

    (workdir / 'solution.h').write_text(source_code)
    (workdir / 'testkit.h').write_text(TESTKIT_H)
    (workdir / 'test_submission.cpp').write_text(test_code)

    compile_proc = _run(
        [compiler, '-std=c++17', '-O0', '-fprofile-arcs', '-ftest-coverage',
         '-I', str(workdir), *gtest_compile_args,
         '-o', 'test_binary', 'test_submission.cpp', *gtest_link_args],
        workdir,
    )
    if compile_proc is None:
        return _timeout_result(_TIMEOUT_SECONDS)
    if compile_proc.returncode != 0:
        return _no_coverage_result(
            ran=True, tests_passed=False,
            output=('Compile error:\n' + _clean_compiler_output(compile_proc.stderr))[:_OUTPUT_LIMIT],
        )

    run_proc = _run([str(workdir / 'test_binary')], workdir)
    if run_proc is None:
        return _timeout_result(_TIMEOUT_SECONDS)

    output = (run_proc.stdout + run_proc.stderr)[:_OUTPUT_LIMIT]
    tests_passed = run_proc.returncode == 0 and 'PASS' in run_proc.stdout

    lines_covered = lines_missed = line_pct = statement_pct = None
    branches_covered = branches_missed = branch_pct = None
    covered_lines: list[int] = []
    partial_lines: list[int] = []
    uncovered_lines: list[int] = []
    function_coverage: list[dict] = []
    assert_covered = assert_total = assert_pct = None

    # Notes-file naming differs by toolchain (plain GCC: test_submission.gcno;
    # Apple Clang: <binary>-test_submission.gcno) — glob instead of guessing.
    gcno_files = list(workdir.glob('*.gcno'))
    if gcno_files:
        gcov_proc = _run([shutil.which('gcov') or 'gcov', '-b', '-n', gcno_files[0].name], workdir)
        if gcov_proc is not None:
            for block in gcov_proc.stdout.split("File '"):
                if block.startswith('solution.h'):
                    lm = _GCOV_LINES_RE.search(block)
                    if lm:
                        line_pct = round(float(lm.group(1)), 1)
                        statement_pct = line_pct  # same granularity under gcov — see GradeResult docstring
                        total_lines = int(lm.group(2))
                        lines_covered = round(line_pct / 100 * total_lines)
                        lines_missed = total_lines - lines_covered
                    bm = _GCOV_BRANCHES_RE.search(block)
                    if bm:
                        branch_pct = round(float(bm.group(1)), 1)
                        total_branches = int(bm.group(2))
                        branches_covered = round(branch_pct / 100 * total_branches)
                        branches_missed = total_branches - branches_covered
                    break

        # Separate annotated-source pass (with per-line branch-taken%) to recover which
        # specific lines of solution.h are covered / partial / uncovered, for the coverage
        # visualization and the conceptual-feedback prompt — and, from the same output,
        # the per-function breakdown.
        annotate_proc = _run([shutil.which('gcov') or 'gcov', '-b', gcno_files[0].name], workdir)
        gcov_file = workdir / 'solution.h.gcov'
        if annotate_proc is not None and gcov_file.exists():
            gcov_text = gcov_file.read_text()
            covered_lines, partial_lines, uncovered_lines = _parse_gcov_annotated_lines(gcov_text)
            function_coverage = _parse_gcov_function_coverage(gcov_text)

        # Assert-statement coverage: gcov annotates every source file it touched in the same
        # pass above, including the student's own test file — no extra compile/run needed.
        test_gcov_file = workdir / 'test_submission.cpp.gcov'
        if test_gcov_file.exists():
            test_covered, test_partial, _ = _parse_gcov_annotated_lines(test_gcov_file.read_text())
            test_covered_set = set(test_covered) | set(test_partial)  # partial still means "executed"
            assert_lines = _assert_line_numbers(test_code, _CPP_ASSERT_LINE_RE)
            if assert_lines:
                assert_total = len(assert_lines)
                assert_covered = sum(1 for n in assert_lines if n in test_covered_set)
                assert_pct = round(assert_covered / assert_total * 100, 1)

    result = GradeResult(
        ran=True, timed_out=False, tests_passed=tests_passed,
        lines_covered=lines_covered, lines_missed=lines_missed, line_coverage_percent=line_pct,
        branches_covered=branches_covered, branches_missed=branches_missed, branch_coverage_percent=branch_pct,
        covered_lines=covered_lines, partial_lines=partial_lines, uncovered_lines=uncovered_lines,
        statement_coverage_percent=statement_pct, function_coverage=function_coverage,
        assert_covered=assert_covered, assert_total=assert_total, assert_coverage_percent=assert_pct,
        score=0.0, output=output,
    )
    result.score = result.total_coverage_percent if (tests_passed and result.total_coverage_percent is not None) else 0.0
    return result


_CPP_TESTKIT_NAME_RE = re.compile(r'\bTEST\s*\(\s*(\w+)\s*\)\s*\{')
_CPP_GTEST_NAME_RE = re.compile(r'\b(?:TEST|TEST_F)\s*\(\s*(\w+)\s*,\s*(\w+)\s*\)\s*\{')


def get_cpp_per_test_coverage(source_code: str, test_code: str) -> dict[str, list[int]]:
    """{test_name: [covered_lines]} — real per-test coverage for C++, kept as a
    genuinely SEPARATE pass from _run_cpp_suite (the actual grading path) so a bug
    here can never affect a student's score. gcov has no built-in "which test hit
    this line" tagging the way coverage.py's dynamic_context does (see
    _run_python_suite) — the only way to get it is to re-run the same compiled
    binary once per test, filtered to just that one, deleting the accumulated
    .gcda counters between runs (they'd otherwise sum across every run of the same
    binary). Best-effort: returns {} on any failure along the way, or if there
    are fewer than 2 tests (nothing to compare for Superficial Modifications).
    """
    compiler = _cpp_compiler()
    if compiler is None:
        return {}
    gtest_flags = _gtest_compile_flags(test_code)
    if isinstance(gtest_flags, str):
        return {}
    gtest_compile_args, gtest_link_args = gtest_flags
    using_gtest = bool(gtest_link_args)

    if using_gtest:
        test_names = [f'{suite}.{name}' for suite, name in _CPP_GTEST_NAME_RE.findall(test_code)]
    else:
        test_names = _CPP_TESTKIT_NAME_RE.findall(test_code)
    if len(test_names) < 2:
        return {}

    with tempfile.TemporaryDirectory(prefix='codeflow-pertest-') as tmp:
        workdir = Path(tmp).resolve()
        (workdir / 'solution.h').write_text(source_code)
        (workdir / 'testkit.h').write_text(TESTKIT_H)
        (workdir / 'test_submission.cpp').write_text(test_code)

        compile_proc = _run(
            [compiler, '-std=c++17', '-O0', '-fprofile-arcs', '-ftest-coverage',
             '-I', str(workdir), *gtest_compile_args,
             '-o', 'test_binary', 'test_submission.cpp', *gtest_link_args],
            workdir,
        )
        if compile_proc is None or compile_proc.returncode != 0:
            return {}

        result: dict[str, list[int]] = {}
        for name in test_names:
            for gcda in workdir.glob('*.gcda'):
                gcda.unlink()
            cmd = [str(workdir / 'test_binary')]
            extra_env = None
            if using_gtest:
                cmd.append(f'--gtest_filter={name}')
            else:
                extra_env = {'TESTKIT_FILTER': name}
            run_proc = _run(cmd, workdir, extra_env=extra_env)
            if run_proc is None:
                continue
            gcno_files = list(workdir.glob('*.gcno'))
            if not gcno_files:
                continue
            annotate_proc = _run([shutil.which('gcov') or 'gcov', '-b', gcno_files[0].name], workdir)
            gcov_file = workdir / 'solution.h.gcov'
            if annotate_proc is not None and gcov_file.exists():
                covered, partial, _ = _parse_gcov_annotated_lines(gcov_file.read_text())
                result[name] = sorted(set(covered) | set(partial))
        return result


def _check_cpp_source(source_code: str, workdir: Path) -> CompileCheckResult:
    """Syntax/semantic-checks solution.h on its own, independent of any test file —
    `-fsyntax-only` compiles far enough to catch real errors without producing a binary."""
    compiler = _cpp_compiler()
    if compiler is None:
        return CompileCheckResult(ok=False, output='No C++ compiler (g++/clang++) available on the server.')
    (workdir / 'solution.h').write_text(source_code)
    (workdir / '_compile_check.cpp').write_text('#include "solution.h"\nint main() { return 0; }\n')
    proc = _run(
        [compiler, '-std=c++17', '-fsyntax-only', '-I', str(workdir), '_compile_check.cpp'],
        workdir,
    )
    if proc is None:
        return CompileCheckResult(ok=False, output=f'Compile check timed out after {_TIMEOUT_SECONDS}s.')
    if proc.returncode != 0:
        return CompileCheckResult(ok=False, output=_clean_compiler_output(proc.stderr)[:_OUTPUT_LIMIT])
    return CompileCheckResult(ok=True, output='')


def _check_cpp_test_suite(source_code: str, test_code: str, workdir: Path) -> CompileCheckResult:
    """Compiles test_code against source_code + testkit.h exactly as grading would —
    no binary is run, just the same compile step _run_cpp_suite does before executing it."""
    compiler = _cpp_compiler()
    if compiler is None:
        return CompileCheckResult(ok=False, output='No C++ compiler (g++/clang++) available on the server.')
    gtest_flags = _gtest_compile_flags(test_code)
    if isinstance(gtest_flags, str):
        return CompileCheckResult(ok=False, output=gtest_flags)
    gtest_compile_args, _gtest_link_args = gtest_flags
    (workdir / 'solution.h').write_text(source_code)
    (workdir / 'testkit.h').write_text(TESTKIT_H)
    (workdir / 'test_submission.cpp').write_text(test_code)
    proc = _run(
        [compiler, '-std=c++17', '-fsyntax-only', '-I', str(workdir), *gtest_compile_args, 'test_submission.cpp'],
        workdir,
    )
    if proc is None:
        return CompileCheckResult(ok=False, output=f'Compile check timed out after {_TIMEOUT_SECONDS}s.')
    if proc.returncode != 0:
        return CompileCheckResult(ok=False, output=_clean_compiler_output(proc.stderr)[:_OUTPUT_LIMIT])
    return CompileCheckResult(ok=True, output='')


# ── Dispatcher ───────────────────────────────────────────────────────────────

def run_test_suite(language: AssignmentLanguage, source_code: str, test_code: str) -> GradeResult:
    """Run *test_code* against *source_code* under the given language's toolchain.

    Python: test_code must `from solution import ...` (solution.py).
    C++: test_code must `#include "solution.h"`, plus either `#include "testkit.h"`
    and use TEST(name){...} / ASSERT_TRUE / ASSERT_EQ / ASSERT_THROWS (see TESTKIT_H),
    or `#include <gtest/gtest.h>` and write standard Google Test — detected by which
    header the test file includes (see _gtest_compile_flags), compiled/linked against
    the Homebrew `googletest` install on this server.
    """
    with tempfile.TemporaryDirectory(prefix='codeflow-grade-') as tmp:
        # macOS puts TMPDIR behind a symlink (/var/folders -> /private/var/folders);
        # sandbox-exec's subpath matching is on the resolved path, so match it too.
        workdir = Path(tmp).resolve()
        if language == AssignmentLanguage.cpp:
            return _run_cpp_suite(source_code, test_code, workdir)
        return _run_python_suite(source_code, test_code, workdir)


def check_source_compiles(language: AssignmentLanguage, source_code: str) -> CompileCheckResult:
    """Validate *source_code* on its own — Python: syntax-compiles cleanly; C++: solution.h
    compiles standalone via -fsyntax-only. Meant to be called at assignment-authoring time
    so an instructor finds out about a broken solution immediately, rather than a student
    hitting a confusing error at submission time that isn't actually their fault.
    """
    with tempfile.TemporaryDirectory(prefix='codeflow-check-') as tmp:
        workdir = Path(tmp).resolve()
        if language == AssignmentLanguage.cpp:
            return _check_cpp_source(source_code, workdir)
        return _check_python_source(source_code, workdir)


def check_test_suite_compiles(language: AssignmentLanguage, source_code: str, test_code: str) -> CompileCheckResult:
    """Validate that *test_code* compiles/imports cleanly against *source_code* — the same
    compile step grading would do, without running anything. For authoring-time validation
    of a reference or given test suite.
    """
    with tempfile.TemporaryDirectory(prefix='codeflow-check-') as tmp:
        workdir = Path(tmp).resolve()
        if language == AssignmentLanguage.cpp:
            return _check_cpp_test_suite(source_code, test_code, workdir)
        return _check_python_test_suite(source_code, test_code, workdir)
