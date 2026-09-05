"""Shared low-level C++ text-scanning helpers — no real parser exists for C++ in
this codebase, so every C++ static-analysis pass (core/test_quality_service.py's
heuristic detectors, parser/cpp_cfg_builder.py) is regex/brace-matching over the
source text. These are the primitives they all share.
"""
import re

_COMMENT_RE = re.compile(r'/\*.*?\*/|//[^\n]*', re.DOTALL)


def strip_comments(source: str) -> str:
    """Blank out /* */ and // comments — replacing non-newline characters with
    spaces, so line numbers and character offsets stay exactly aligned with the
    original — so a comment that happens to contain code-shaped text ("if (",
    a stray brace, "@param score (0-100)") can't be mistaken for real code by
    any of the scanners below."""
    return _COMMENT_RE.sub(lambda m: re.sub(r'[^\n]', ' ', m.group(0)), source)


def matching_brace(text: str, open_pos: int) -> int:
    """Index of the `}` that closes the `{` at *open_pos* (depth-matched).
    Returns len(text) if unclosed — callers should treat that as "rest of file"."""
    return _matching(text, open_pos, '{', '}')


def matching_paren(text: str, open_pos: int) -> int:
    """Index of the `)` that closes the `(` at *open_pos* (depth-matched)."""
    return _matching(text, open_pos, '(', ')')


def _matching(text: str, open_pos: int, open_ch: str, close_ch: str) -> int:
    depth = 0
    i = open_pos
    while i < len(text):
        if text[i] == open_ch:
            depth += 1
        elif text[i] == close_ch:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return len(text)
