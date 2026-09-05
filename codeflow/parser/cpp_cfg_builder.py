"""Build a Control Flow Graph from a single C++ function/method body.

There's no real C++ parser anywhere in this codebase (see parser/cpp_utils.py's
docstring), so this is a hand-rolled recursive-descent scanner over the *text* of
one already-extracted function body (see cpp_function_bodies() below for how a
body gets extracted from a whole class) — brace-matching for blocks, paren-matching
for conditions, `;` at depth 0 for simple-statement boundaries. It deliberately
mirrors parser/cfg_builder.py's CFGBuilder node/edge vocabulary exactly (same
`type`s, same `flow`/`true-branch`/`false-branch`/`back-edge`/`exception` edge
types, same output shape) so every downstream consumer — GraphSerializer,
du_analyzer, the JS animation engine — works unchanged on either language.

Scope: if/else if/else, while, for (loop head treated as one decision node,
folding init/cond/incr together — an approximation, not exact semantics),
throw, return, break, continue, try/catch, and plain statements. Constructs
outside this (switch, lambdas, templates, range-based oddities) aren't
specially recognized and just fall through as opaque "stmt" nodes rather than
raising — same "best-effort, documented" spirit as the existing C++ heuristics.
"""
import re

from .cpp_utils import matching_brace, matching_paren, strip_comments

_KEYWORD_RE = re.compile(r'\b(if|else|while|for|throw|return|break|continue|try|catch)\b')
_ASSIGN_RE = re.compile(r'\b([A-Za-z_]\w*)\s*(?:\+=|-=|\*=|/=|%=|=(?!=))')
_COMPOUND_ASSIGN_RE = re.compile(r'\b([A-Za-z_]\w*)\s*(?:\+=|-=|\*=|/=|%=)')
_IDENT_RE = re.compile(r'\b([A-Za-z_]\w*)\b')
_STRING_LITERAL_RE = re.compile(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'')
_CPP_KEYWORDS_FOR_VARS = {
    'if', 'else', 'while', 'for', 'return', 'throw', 'break', 'continue', 'const',
    'static', 'void', 'int', 'double', 'float', 'bool', 'char', 'auto', 'class',
    'struct', 'public', 'private', 'protected', 'new', 'delete', 'this', 'true',
    'false', 'nullptr', 'std', 'string', 'vector', 'map', 'try', 'catch', 'switch',
    'case', 'default', 'sizeof', 'using', 'namespace', 'template', 'typename',
    'unsigned', 'long', 'short', 'size_t', 'inline', 'virtual', 'override',
}

_METHOD_RE = re.compile(r'\b(?:[\w:<>,\s*&]+?)\s+(\w+)\s*\(([^()]*)\)\s*(?:const)?\s*\{')
_METHOD_EXCLUDE_NAMES = {'if', 'for', 'while', 'switch', 'catch'}


def cpp_function_bodies(source_code: str) -> list[tuple[str, int, int, str]]:
    """[(name, body_start_line, body_end_line, body_text), ...] for every
    method-shaped `{...}` block in *source_code* — same signature regex the
    existing C++ heuristics in core/test_quality_service.py already use for
    Insufficient Method Coverage, reused here so the CFG builder finds the same
    functions those heuristics do. body_text spans just inside the braces
    (excludes the signature line itself), 1-indexed original line numbers."""
    clean = strip_comments(source_code)
    out = []
    for m in _METHOD_RE.finditer(clean):
        name = m.group(1)
        if name in _METHOD_EXCLUDE_NAMES or name.startswith('operator') or name.startswith('~'):
            continue
        open_pos = m.end() - 1
        close_pos = matching_brace(clean, open_pos)
        body_start_line = clean.count('\n', 0, open_pos) + 2  # first line INSIDE the brace
        body_end_line = clean.count('\n', 0, close_pos) + 1
        body_text = source_code[open_pos + 1:close_pos]
        # The opening brace's own line ends in a newline that's INSIDE this slice
        # (open_pos+1 is right after `{`) — strip exactly that one so body_text's
        # local line 1 actually IS body_start_line, not the blank line before it.
        if body_text.startswith('\r\n'):
            body_text = body_text[2:]
        elif body_text.startswith('\n'):
            body_text = body_text[1:]
        out.append((name, body_start_line, body_end_line, body_text))
    return out


def _extract_def_use(stmt_text: str) -> tuple[list[str], list[str]]:
    # Blank out string/char literals first — "Score must be within range" would
    # otherwise tokenize into a pile of fake identifiers ("Score", "must", ...).
    code = _STRING_LITERAL_RE.sub(lambda m: ' ' * len(m.group(0)), stmt_text)
    defs = {m.group(1) for m in _ASSIGN_RE.finditer(code)}
    compound = {m.group(1) for m in _COMPOUND_ASSIGN_RE.finditer(code)}
    idents = {m.group(1) for m in _IDENT_RE.finditer(code)} - _CPP_KEYWORDS_FOR_VARS
    uses = (idents - defs) | compound
    return sorted(defs), sorted(uses)


class CppCFGBuilder:
    def __init__(self):
        self._nodes: dict[int, dict] = {}
        self._edges: list[dict] = []
        self._nid = 0
        self._src = ''

    def build(self, body_text: str) -> dict:
        """*body_text* is one function/method body's text (the span strictly
        inside its `{ }`), as returned by cpp_function_bodies(). Local line 1 is
        body_text's own first line — callers map back to original file lines the
        same way core/test_quality_service.py's _local_function_cfg does."""
        self.__init__()
        self._src = strip_comments(body_text)

        entry = self._new_node('entry', 'Entry', '', [])
        exit_ = self._new_node('exit', 'Exit', '', [])
        exits = self._visit_block(0, len(self._src), [entry], exit_, None)
        for eid in exits:
            self._add_edge(eid, exit_)
        return self._to_dict(body_text)

    # ── node / edge helpers (mirrors parser/cfg_builder.py exactly) ────────

    def _new_node(self, ntype: str, label: str, code: str, lines: list[int],
                  vars_def: list[str] | None = None, vars_use: list[str] | None = None) -> int:
        nid = self._nid
        self._nid += 1
        self._nodes[nid] = {
            'id': nid, 'type': ntype, 'label': label, 'code': code, 'lines': lines,
            'vars_def': vars_def or [], 'vars_use': vars_use or [],
        }
        return nid

    def _add_edge(self, from_id: int, to_id: int, label: str = '', etype: str = 'flow') -> None:
        if any(e['from'] == from_id and e['to'] == to_id and e['label'] == label for e in self._edges):
            return
        self._edges.append({'from': from_id, 'to': to_id, 'label': label, 'type': etype})

    def _first_in_branch(self, fallback_id: int) -> int:
        edges_from = [e for e in self._edges if e['from'] == fallback_id]
        return edges_from[-1]['to'] if edges_from else fallback_id

    def _line_of(self, pos: int) -> int:
        return self._src.count('\n', 0, pos) + 1

    def _skip_ws(self, pos: int, end: int) -> int:
        while pos < end and self._src[pos].isspace():
            pos += 1
        return pos

    # ── block/statement scanning ────────────────────────────────────────────

    def _visit_block(self, start: int, end: int, entries: list[int], exit_id: int, loop_ctx) -> list[int]:
        pos = start
        current = entries
        while True:
            pos = self._skip_ws(pos, end)
            if pos >= end:
                break
            if not current:
                break  # dead code after this point — nothing left to attach to
            pos, current = self._visit_one(pos, end, current, exit_id, loop_ctx)
        return current

    def _visit_one(self, pos: int, end: int, entries: list[int], exit_id: int, loop_ctx) -> tuple[int, list[int]]:
        text = self._src
        if text[pos] == '{':
            close = matching_brace(text, pos)
            exits = self._visit_block(pos + 1, close, entries, exit_id, loop_ctx)
            return close + 1, exits

        m = _KEYWORD_RE.match(text, pos)
        kw = m.group(1) if m and m.start() == pos else None

        if kw == 'if':
            return self._visit_if(pos, end, entries, exit_id, loop_ctx)
        if kw == 'while':
            return self._visit_while(pos, end, entries, exit_id, loop_ctx)
        if kw == 'for':
            return self._visit_for(pos, end, entries, exit_id, loop_ctx)
        if kw == 'try':
            return self._visit_try(pos, end, entries, exit_id, loop_ctx)
        if kw == 'throw':
            return self._visit_throw(pos, end, entries)
        if kw == 'return':
            return self._visit_return(pos, end, entries, exit_id)
        if kw == 'break':
            return self._visit_break(pos, end, entries, loop_ctx)
        if kw == 'continue':
            return self._visit_continue(pos, end, entries, loop_ctx)
        return self._visit_simple(pos, end, entries)

    def _visit_body_or_single(self, pos: int, end: int, entries: list[int],
                               exit_id: int, loop_ctx) -> tuple[int, list[int]]:
        """A statement's body: either a `{ ... }` block, or (braceless if/while) a
        single statement — both forms are legal C++."""
        pos = self._skip_ws(pos, end)
        if pos < end and self._src[pos] == '{':
            close = matching_brace(self._src, pos)
            return close + 1, self._visit_block(pos + 1, close, entries, exit_id, loop_ctx)
        return self._visit_one(pos, end, entries, exit_id, loop_ctx)

    # ── individual constructs (mirrors _visit_if/_visit_while/... in cfg_builder.py) ──

    def _visit_if(self, pos: int, end: int, entries: list[int], exit_id: int, loop_ctx) -> tuple[int, list[int]]:
        text = self._src
        paren_open = text.index('(', pos)
        paren_close = matching_paren(text, paren_open)
        cond_text = text[paren_open + 1:paren_close].strip()
        line = self._line_of(pos)
        label = cond_text if len(cond_text) <= 40 else cond_text[:40] + '…'
        _, vars_use = _extract_def_use(cond_text)
        cond_id = self._new_node('decision', label, text[pos:paren_close + 1], [line], [], vars_use)
        for e in entries:
            self._add_edge(e, cond_id)

        body_end_pos, true_exits = self._visit_body_or_single(paren_close + 1, end, [cond_id], exit_id, loop_ctx)
        self._add_edge(cond_id, self._first_in_branch(cond_id), 'T', 'true-branch')

        next_pos = self._skip_ws(body_end_pos, end)
        else_m = re.match(r'\belse\b', text[next_pos:next_pos + 10] if next_pos < end else '')
        if else_m:
            after_else = self._skip_ws(next_pos + else_m.end(), end)
            if re.match(r'\bif\b', text[after_else:after_else + 3]):
                final_pos, false_exits = self._visit_if(after_else, end, [cond_id], exit_id, loop_ctx)
            else:
                final_pos, false_exits = self._visit_body_or_single(after_else, end, [cond_id], exit_id, loop_ctx)
            # _first_in_branch(cond_id) always resolves to "the target of the most
            # recently added edge from cond_id" — since visiting the false branch
            # (just above) added a fresh edge from cond_id before this call, it now
            # correctly points at the false branch's own first node, not the true
            # branch's (which it pointed at right after the 'T' line above).
            self._add_edge(cond_id, self._first_in_branch(cond_id), 'F', 'false-branch')
            return final_pos, true_exits + false_exits

        return body_end_pos, true_exits + [cond_id]  # no else: false branch just falls through past the if

    def _visit_while(self, pos: int, end: int, entries: list[int], exit_id: int, loop_ctx) -> tuple[int, list[int]]:
        text = self._src
        paren_open = text.index('(', pos)
        paren_close = matching_paren(text, paren_open)
        cond_text = text[paren_open + 1:paren_close].strip()
        line = self._line_of(pos)
        label = cond_text if len(cond_text) <= 40 else cond_text[:40] + '…'
        _, vars_use = _extract_def_use(cond_text)
        head_id = self._new_node('decision', label, text[pos:paren_close + 1], [line], [], vars_use)
        for e in entries:
            self._add_edge(e, head_id)

        inner = _LoopCtx(head_id)
        body_end_pos, body_exits = self._visit_body_or_single(paren_close + 1, end, [head_id], exit_id, inner)
        for be in body_exits + inner.continue_ids:
            self._add_edge(be, head_id, '', 'back-edge')

        loop_exit = self._new_node('stmt', '(loop exit)', '', [line])
        self._add_edge(head_id, loop_exit, 'F', 'false-branch')
        return body_end_pos, [loop_exit] + inner.exit_ids

    def _visit_for(self, pos: int, end: int, entries: list[int], exit_id: int, loop_ctx) -> tuple[int, list[int]]:
        text = self._src
        paren_open = text.index('(', pos)
        paren_close = matching_paren(text, paren_open)
        header_text = text[paren_open + 1:paren_close].strip()
        line = self._line_of(pos)
        label = 'for (' + (header_text if len(header_text) <= 32 else header_text[:32] + '…') + ')'
        _, vars_use = _extract_def_use(header_text)
        head_id = self._new_node('decision', label, text[pos:paren_close + 1], [line], [], vars_use)
        for e in entries:
            self._add_edge(e, head_id)

        inner = _LoopCtx(head_id)
        body_end_pos, body_exits = self._visit_body_or_single(paren_close + 1, end, [head_id], exit_id, inner)
        for be in body_exits + inner.continue_ids:
            self._add_edge(be, head_id, '', 'back-edge')

        loop_exit = self._new_node('stmt', '(loop exit)', '', [line])
        self._add_edge(head_id, loop_exit, 'F', 'false-branch')
        return body_end_pos, [loop_exit] + inner.exit_ids

    def _visit_try(self, pos: int, end: int, entries: list[int], exit_id: int, loop_ctx) -> tuple[int, list[int]]:
        text = self._src
        brace_pos = self._skip_ws(pos + 3, end)
        close = matching_brace(text, brace_pos)
        try_exits = self._visit_block(brace_pos + 1, close, entries, exit_id, loop_ctx)

        cursor = self._skip_ws(close + 1, end)
        all_exits = list(try_exits)
        while re.match(r'\bcatch\b', text[cursor:cursor + 6]):
            paren_open = text.index('(', cursor)
            paren_close = matching_paren(text, paren_open)
            line = self._line_of(cursor)
            catch_type = text[paren_open + 1:paren_close].strip()
            handler_id = self._new_node('exception', f'catch ({catch_type[:30]})', text[cursor:paren_close + 1], [line])
            for e in entries:
                self._add_edge(e, handler_id, 'exc', 'exception')
            body_start = self._skip_ws(paren_close + 1, end)
            handler_close = matching_brace(text, body_start)
            handler_exits = self._visit_block(body_start + 1, handler_close, [handler_id], exit_id, loop_ctx)
            all_exits.extend(handler_exits)
            cursor = self._skip_ws(handler_close + 1, end)

        return cursor, all_exits

    def _visit_throw(self, pos: int, end: int, entries: list[int]) -> tuple[int, list[int]]:
        semi = self._src.index(';', pos)
        code = self._src[pos:semi + 1].strip()
        line = self._line_of(pos)
        label = 'throw ' + code[len('throw'):].strip().rstrip(';')
        label = label if len(label) <= 45 else label[:45] + '…'
        _, vars_use = _extract_def_use(code)
        nid = self._new_node('exception', label, code, [line], [], vars_use)
        for e in entries:
            self._add_edge(e, nid)
        return semi + 1, []  # no fall-through — matches Python's _visit_raise

    def _visit_return(self, pos: int, end: int, entries: list[int], exit_id: int) -> tuple[int, list[int]]:
        semi = self._src.index(';', pos)
        code = self._src[pos:semi + 1].strip()
        line = self._line_of(pos)
        label = code if len(code) <= 45 else code[:45] + '…'
        _, vars_use = _extract_def_use(code)
        nid = self._new_node('stmt', label, code, [line], [], vars_use)
        for e in entries:
            self._add_edge(e, nid)
        self._add_edge(nid, exit_id)
        return semi + 1, []

    def _visit_break(self, pos: int, end: int, entries: list[int], loop_ctx) -> tuple[int, list[int]]:
        semi = self._src.index(';', pos)
        line = self._line_of(pos)
        nid = self._new_node('stmt', 'break', 'break;', [line])
        for e in entries:
            self._add_edge(e, nid)
        if loop_ctx:
            loop_ctx.exit_ids.append(nid)
        return semi + 1, []

    def _visit_continue(self, pos: int, end: int, entries: list[int], loop_ctx) -> tuple[int, list[int]]:
        semi = self._src.index(';', pos)
        line = self._line_of(pos)
        nid = self._new_node('stmt', 'continue', 'continue;', [line])
        for e in entries:
            self._add_edge(e, nid)
        if loop_ctx:
            loop_ctx.continue_ids.append(nid)
        return semi + 1, []

    def _visit_simple(self, pos: int, end: int, entries: list[int]) -> tuple[int, list[int]]:
        """A plain statement (assignment, declaration, expression/call) up to its
        top-level `;` — parens/brace-init-lists don't count toward "top-level"."""
        text = self._src
        i = pos
        depth = 0
        while i < end:
            ch = text[i]
            if ch in '([{':
                depth += 1
            elif ch in ')]}':
                depth -= 1
            elif ch == ';' and depth <= 0:
                break
            i += 1
        semi = min(i, end - 1) if i >= end else i
        code = text[pos:semi + 1].strip()
        line = self._line_of(pos)
        label = code if len(code) <= 45 else code[:45] + '…'
        vars_def, vars_use = _extract_def_use(code)
        nid = self._new_node('stmt', label, code, [line], vars_def, vars_use)
        for e in entries:
            self._add_edge(e, nid)
        return semi + 1, [nid]

    # ── output ───────────────────────────────────────────────────────────

    def _to_dict(self, source: str) -> dict:
        nodes = list(self._nodes.values())
        edges = list(self._edges)
        paths = self._find_simple_paths()
        prime_paths = self._find_prime_paths(paths)
        return {
            'nodes': nodes,
            'edges': edges,
            'metadata': {
                'name': 'CFG', 'source_code': source,
                'paths': paths, 'prime_paths': prime_paths, 'du_chains': {},
            },
        }

    def _find_simple_paths(self) -> list[list[int]]:
        adj: dict[int, list[int]] = {n: [] for n in self._nodes}
        for e in self._edges:
            if e['type'] != 'back-edge':
                adj[e['from']].append(e['to'])
        entry_id, exit_id = 0, 1
        paths: list[list[int]] = []

        def dfs(node, path, visited):
            if len(paths) > 100:
                return
            if node == exit_id:
                paths.append(path[:])
                return
            for nxt in adj.get(node, []):
                if nxt not in visited:
                    visited.add(nxt)
                    path.append(nxt)
                    dfs(nxt, path, visited)
                    path.pop()
                    visited.discard(nxt)

        dfs(entry_id, [entry_id], {entry_id})
        return paths

    def _find_prime_paths(self, paths: list[list[int]]) -> list[list[int]]:
        prime = []
        for p in paths:
            dominated = False
            for q in paths:
                if p is not q and len(q) > len(p):
                    for i in range(len(q) - len(p) + 1):
                        if q[i:i + len(p)] == p:
                            dominated = True
                            break
                if dominated:
                    break
            if not dominated:
                prime.append(p)
        return prime


class _LoopCtx:
    def __init__(self, head_id: int):
        self.head_id = head_id
        self.exit_ids: list[int] = []
        self.continue_ids: list[int] = []
