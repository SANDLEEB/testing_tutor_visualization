"""
Build a Control Flow Graph from Python source code using the ast module.

Returns a dict compatible with GraphAnimator's JSON format.
"""
import ast
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from .ast_utils import get_vars_defined, get_vars_used, source_lines, stmt_label


@dataclass
class CFGNode:
    id: int
    type: str          # entry | exit | stmt | decision | exception
    label: str
    code: str
    lines: List[int]
    vars_def: List[str]
    vars_use: List[str]


@dataclass
class CFGEdge:
    from_id: int
    to_id: int
    label: str
    type: str          # flow | true-branch | false-branch | back-edge | exception


@dataclass
class _LoopCtx:
    """Tracks break/continue targets while visiting loop bodies."""
    head_id: int
    exit_ids: List[int] = field(default_factory=list)  # collected break destinations
    continue_ids: List[int] = field(default_factory=list)  # collected continue destinations


class CFGBuilder:
    def __init__(self):
        self._nodes: Dict[int, CFGNode] = {}
        self._edges: List[CFGEdge]      = []
        self._nid  = 0
        self._src  = ''

    # ── public ────────────────────────────────────────────────────────────

    def build(self, source: str) -> Dict[str, Any]:
        """Parse *source* and return a graph-data dict for the JS engine."""
        self.__init__()
        self._src = source

        try:
            tree = ast.parse(source)
        except SyntaxError as e:
            return self._error_graph(str(e))

        entry = self._new_node('entry', 'Entry', '')
        exit_ = self._new_node('exit', 'Exit', '')

        exits = self._visit_stmts(tree.body, [entry], exit_, None)
        for eid in exits:
            self._add_edge(eid, exit_, '')

        return self._to_dict(source)

    # ── node / edge helpers ───────────────────────────────────────────────

    def _new_node(self, ntype: str, label: str, code: str,
                  lines: List[int] = None, ast_node=None) -> int:
        nid = self._nid; self._nid += 1
        vdef = get_vars_defined(ast_node) if ast_node else []
        vuse = get_vars_used(ast_node)    if ast_node else []
        self._nodes[nid] = CFGNode(
            id=nid, type=ntype, label=label, code=code,
            lines=lines or [], vars_def=vdef, vars_use=vuse,
        )
        return nid

    def _add_edge(self, from_id: int, to_id: int,
                  label: str = '', etype: str = 'flow') -> None:
        # Avoid exact duplicates
        if any(e.from_id == from_id and e.to_id == to_id and e.label == label
               for e in self._edges):
            return
        self._edges.append(CFGEdge(from_id, to_id, label, etype))

    # ── statement dispatch ────────────────────────────────────────────────

    def _visit_stmts(self, stmts, entries: List[int],
                     exit_id: int, loop_ctx: Optional[_LoopCtx]) -> List[int]:
        """Process a list of statements; return open exit endpoints."""
        current = entries
        for stmt in stmts:
            if not current:
                break
            current = self._visit_stmt(stmt, current, exit_id, loop_ctx)
        return current

    def _visit_stmt(self, stmt, entries: List[int],
                    exit_id: int, loop_ctx: Optional[_LoopCtx]) -> List[int]:
        if isinstance(stmt, ast.If):
            return self._visit_if(stmt, entries, exit_id, loop_ctx)
        if isinstance(stmt, ast.While):
            return self._visit_while(stmt, entries, exit_id, loop_ctx)
        if isinstance(stmt, ast.For):
            return self._visit_for(stmt, entries, exit_id, loop_ctx)
        if isinstance(stmt, ast.Try):
            return self._visit_try(stmt, entries, exit_id, loop_ctx)
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return self._visit_funcdef(stmt, entries, exit_id, loop_ctx)
        if isinstance(stmt, ast.ClassDef):
            return self._visit_classdef(stmt, entries, exit_id, loop_ctx)
        if isinstance(stmt, ast.With):
            return self._visit_with(stmt, entries, exit_id, loop_ctx)
        if isinstance(stmt, ast.Return):
            return self._visit_return(stmt, entries, exit_id, loop_ctx)
        if isinstance(stmt, ast.Break):
            return self._visit_break(stmt, entries, loop_ctx)
        if isinstance(stmt, ast.Continue):
            return self._visit_continue(stmt, entries, loop_ctx)
        if isinstance(stmt, ast.Raise):
            return self._visit_raise(stmt, entries, exit_id, loop_ctx)
        # Simple statement (Assign, AugAssign, Expr, Assert, Delete, Pass, …)
        return self._visit_simple(stmt, entries)

    def _visit_simple(self, stmt, entries: List[int]) -> List[int]:
        code  = stmt_label(stmt, self._src)
        label = self._short_label(stmt)
        lines = source_lines(stmt)
        nid   = self._new_node('stmt', label, code, lines, stmt)
        for e in entries:
            self._add_edge(e, nid)
        return [nid]

    def _visit_if(self, stmt, entries: List[int],
                  exit_id: int, loop_ctx) -> List[int]:
        test_code  = stmt_label(stmt, self._src)
        test_label = self._cond_label(stmt.test)
        lines      = [stmt.lineno] if hasattr(stmt, 'lineno') else []
        cond_id    = self._new_node('decision', test_label, test_code, lines, stmt)
        for e in entries:
            self._add_edge(e, cond_id)

        # True branch
        true_exits  = self._visit_stmts(stmt.body,   [cond_id], exit_id, loop_ctx)
        self._add_edge(cond_id, self._first_in_branch(stmt.body, cond_id), 'T', 'true-branch')

        # False / elif / else branch
        orelse = stmt.orelse
        if orelse:
            false_exits = self._visit_stmts(orelse, [cond_id], exit_id, loop_ctx)
            if orelse and not isinstance(orelse[0], ast.If):
                self._add_edge(cond_id, self._first_in_branch(orelse, cond_id), 'F', 'false-branch')
            else:
                self._add_edge(cond_id, self._first_in_branch(orelse, cond_id), 'F', 'false-branch')
        else:
            false_exits = [cond_id]

        return true_exits + false_exits

    def _visit_while(self, stmt, entries: List[int],
                     exit_id: int, loop_ctx) -> List[int]:
        test_code  = stmt_label(stmt, self._src)
        test_label = self._cond_label(stmt.test)
        lines      = [stmt.lineno] if hasattr(stmt, 'lineno') else []
        head_id    = self._new_node('decision', test_label, test_code, lines, stmt)
        for e in entries:
            self._add_edge(e, head_id)

        inner_ctx = _LoopCtx(head_id=head_id)
        body_exits = self._visit_stmts(stmt.body, [head_id], exit_id, inner_ctx)

        # Back-edges from loop body end + collectedcontinues
        for be in body_exits + inner_ctx.continue_ids:
            self._add_edge(be, head_id, '', 'back-edge')

        # Loop-exit node (false branch)
        loop_exit = self._new_node('stmt', '(loop exit)', '', lines)
        self._add_edge(head_id, loop_exit, 'F', 'false-branch')

        return [loop_exit] + inner_ctx.exit_ids

    def _visit_for(self, stmt, entries: List[int],
                   exit_id: int, loop_ctx) -> List[int]:
        iter_code  = stmt_label(stmt, self._src)
        iter_label = f'for {ast.unparse(stmt.target)} in …'
        lines      = [stmt.lineno] if hasattr(stmt, 'lineno') else []
        head_id    = self._new_node('decision', iter_label, iter_code, lines, stmt)
        for e in entries:
            self._add_edge(e, head_id)

        inner_ctx  = _LoopCtx(head_id=head_id)
        body_exits = self._visit_stmts(stmt.body, [head_id], exit_id, inner_ctx)

        for be in body_exits + inner_ctx.continue_ids:
            self._add_edge(be, head_id, '', 'back-edge')

        loop_exit = self._new_node('stmt', '(loop exit)', '', lines)
        self._add_edge(head_id, loop_exit, 'F', 'false-branch')

        return [loop_exit] + inner_ctx.exit_ids

    def _visit_try(self, stmt, entries: List[int],
                   exit_id: int, loop_ctx) -> List[int]:
        lines = [stmt.lineno] if hasattr(stmt, 'lineno') else []

        # Try body
        try_exits = self._visit_stmts(stmt.body, entries, exit_id, loop_ctx)
        all_exits = []

        # Exception handlers
        for handler in stmt.handlers:
            hname   = f'except {ast.unparse(handler.type) if handler.type else "…"}'
            h_id    = self._new_node('exception', hname, hname, lines)
            # Edge from all try nodes to handler (represent potential exception)
            for e in entries:
                self._add_edge(e, h_id, 'exc', 'exception')
            h_exits = self._visit_stmts(handler.body, [h_id], exit_id, loop_ctx)
            all_exits.extend(h_exits)

        # Else clause (no exception)
        if stmt.orelse:
            else_exits = self._visit_stmts(stmt.orelse, try_exits, exit_id, loop_ctx)
            all_exits.extend(else_exits)
        else:
            all_exits.extend(try_exits)

        # Finally clause
        if stmt.finalbody:
            fin_exits = self._visit_stmts(stmt.finalbody, all_exits, exit_id, loop_ctx)
            return fin_exits

        return all_exits

    def _visit_funcdef(self, stmt, entries: List[int],
                       exit_id: int, loop_ctx) -> List[int]:
        label = f'def {stmt.name}(…)'
        lines = [stmt.lineno] if hasattr(stmt, 'lineno') else []
        nid   = self._new_node('stmt', label, label, lines, stmt)
        for e in entries:
            self._add_edge(e, nid)
        return [nid]

    def _visit_classdef(self, stmt, entries, exit_id, loop_ctx):
        label = f'class {stmt.name}'
        lines = [stmt.lineno] if hasattr(stmt, 'lineno') else []
        nid   = self._new_node('stmt', label, label, lines, stmt)
        for e in entries:
            self._add_edge(e, nid)
        return [nid]

    def _visit_with(self, stmt, entries, exit_id, loop_ctx):
        items  = ', '.join(ast.unparse(i.context_expr) for i in stmt.items)
        label  = f'with {items}'
        lines  = [stmt.lineno] if hasattr(stmt, 'lineno') else []
        nid    = self._new_node('stmt', label, label, lines, stmt)
        for e in entries:
            self._add_edge(e, nid)
        return self._visit_stmts(stmt.body, [nid], exit_id, loop_ctx)

    def _visit_return(self, stmt, entries, exit_id, loop_ctx):
        code  = stmt_label(stmt, self._src)
        label = f'return {ast.unparse(stmt.value) if stmt.value else ""}'
        lines = [stmt.lineno] if hasattr(stmt, 'lineno') else []
        nid   = self._new_node('stmt', label, code, lines, stmt)
        for e in entries:
            self._add_edge(e, nid)
        self._add_edge(nid, exit_id, '', 'flow')
        return []  # no fall-through

    def _visit_break(self, stmt, entries, loop_ctx):
        lines = [stmt.lineno] if hasattr(stmt, 'lineno') else []
        nid   = self._new_node('stmt', 'break', 'break', lines)
        for e in entries:
            self._add_edge(e, nid)
        if loop_ctx:
            loop_ctx.exit_ids.append(nid)
        return []

    def _visit_continue(self, stmt, entries, loop_ctx):
        lines = [stmt.lineno] if hasattr(stmt, 'lineno') else []
        nid   = self._new_node('stmt', 'continue', 'continue', lines)
        for e in entries:
            self._add_edge(e, nid)
        if loop_ctx:
            loop_ctx.continue_ids.append(nid)
        return []

    def _visit_raise(self, stmt, entries, exit_id, loop_ctx):
        code  = stmt_label(stmt, self._src)
        label = f'raise {ast.unparse(stmt.exc) if stmt.exc else ""}'
        lines = [stmt.lineno] if hasattr(stmt, 'lineno') else []
        nid   = self._new_node('exception', label, code, lines, stmt)
        for e in entries:
            self._add_edge(e, nid)
        self._add_edge(nid, exit_id, '', 'exception')
        return []

    # ── label helpers ─────────────────────────────────────────────────────

    def _cond_label(self, test_node) -> str:
        try:
            s = ast.unparse(test_node)
            return s[:40] + '…' if len(s) > 40 else s
        except Exception:
            return 'condition'

    def _short_label(self, stmt) -> str:
        try:
            s = ast.unparse(stmt)
            first_line = s.splitlines()[0]
            return first_line[:45] + '…' if len(first_line) > 45 else first_line
        except Exception:
            return type(stmt).__name__

    def _first_in_branch(self, stmts, fallback_id: int) -> int:
        """Return the node id that will be first in a branch (for edge labelling)."""
        # We've already added that node, so we can peek at current _nid - 1
        # Actually we need to look at what was created: edges added after cond_id
        # This is called after visiting the branch, so we look at edges
        edges_from_fallback = [e for e in self._edges if e.from_id == fallback_id]
        if edges_from_fallback:
            return edges_from_fallback[-1].to_id
        return fallback_id

    def _error_graph(self, msg: str) -> dict:
        return {
            'nodes': [
                {'id': 0, 'type': 'entry', 'label': 'Entry', 'code': '', 'lines': [], 'vars_def': [], 'vars_use': []},
                {'id': 1, 'type': 'exception', 'label': f'SyntaxError: {msg[:60]}', 'code': msg, 'lines': [], 'vars_def': [], 'vars_use': []},
                {'id': 2, 'type': 'exit', 'label': 'Exit', 'code': '', 'lines': [], 'vars_def': [], 'vars_use': []},
            ],
            'edges': [
                {'from': 0, 'to': 1, 'label': '', 'type': 'flow'},
                {'from': 1, 'to': 2, 'label': '', 'type': 'exception'},
            ],
            'metadata': {'name': 'Parse Error', 'source_code': msg, 'paths': [], 'prime_paths': [], 'du_chains': {}},
        }

    def _to_dict(self, source: str) -> dict:
        nodes = [
            {
                'id': n.id, 'type': n.type, 'label': n.label,
                'code': n.code, 'lines': n.lines,
                'vars_def': n.vars_def, 'vars_use': n.vars_use,
            }
            for n in self._nodes.values()
        ]
        edges = [
            {'from': e.from_id, 'to': e.to_id, 'label': e.label, 'type': e.type}
            for e in self._edges
        ]
        paths       = self._find_simple_paths()
        prime_paths = self._find_prime_paths(paths)
        return {
            'nodes': nodes,
            'edges': edges,
            'metadata': {
                'name': 'CFG',
                'source_code': source,
                'paths': paths,
                'prime_paths': prime_paths,
                'du_chains': {},
            },
        }

    # ── path finding ─────────────────────────────────────────────────────

    def _find_simple_paths(self) -> List[List[int]]:
        """All simple paths from entry (id=0) to exit."""
        adj: Dict[int, List[int]] = {n: [] for n in self._nodes}
        for e in self._edges:
            if e.type != 'back-edge':
                adj[e.from_id].append(e.to_id)

        entry_id = 0
        exit_id  = 1
        paths: List[List[int]] = []

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

    def _find_prime_paths(self, paths: List[List[int]]) -> List[List[int]]:
        """Prime paths: simple paths not a proper subpath of any other."""
        prime = []
        for p in paths:
            dominated = False
            for q in paths:
                if p is not q and len(q) > len(p):
                    # Check if p appears as a sub-sequence in q
                    for i in range(len(q) - len(p) + 1):
                        if q[i:i + len(p)] == p:
                            dominated = True
                            break
                if dominated:
                    break
            if not dominated:
                prime.append(p)
        return prime
