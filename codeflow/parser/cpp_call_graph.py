"""Build a call graph from C++ source: methods as nodes, calls between them as
edges. Mirrors parser/call_graph.py's CallGraphBuilder output shape exactly, but
regex-based (no C++ parser exists in this codebase — see parser/cpp_utils.py)
instead of ast-based. Method discovery reuses the same signature regex as
parser/cpp_cfg_builder.py's cpp_function_bodies(), so this finds exactly the same
set of functions the CFG builder and the Insufficient-Method-Coverage detector do.
"""
import re

from .cpp_cfg_builder import cpp_function_bodies
from .cpp_utils import strip_comments

_CALL_RE = re.compile(r'\b(?:this\s*->\s*)?([A-Za-z_]\w*)\s*\(')
_KEYWORDS = {
    'if', 'for', 'while', 'switch', 'catch', 'sizeof', 'return', 'new', 'delete',
    'static_cast', 'dynamic_cast', 'const_cast', 'reinterpret_cast',
}


class CppCallGraphBuilder:
    def build(self, source: str) -> dict:
        clean = strip_comments(source)
        funcs = cpp_function_bodies(clean)
        if not funcs:
            return self._empty('No functions found.')

        names = {name for name, *_ in funcs}
        id_map = {name: i for i, name in enumerate(sorted(names))}

        edges: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for name, _start, _end, body in funcs:
            for m in _CALL_RE.finditer(body):
                callee = m.group(1)
                if callee in _KEYWORDS or callee == name or callee not in names:
                    continue
                key = (name, callee)
                if key not in seen:
                    seen.add(key)
                    edges.append({'from': name, 'to': callee, 'label': '', 'type': 'call'})

        for e in edges:
            if e['from'] == e['to']:
                e['type'] = 'recursive'

        nodes_out = []
        for name in id_map:
            start_line = next(s for n, s, _e, _b in funcs if n == name)
            ftype = self._classify(name, edges)
            nodes_out.append({
                'id': id_map[name], 'label': name, 'type': ftype,
                'code': f'{name}(...)', 'lines': [start_line], 'vars_def': [], 'vars_use': [],
            })

        edges_out = [
            {'from': id_map[e['from']], 'to': id_map[e['to']], 'label': e['label'], 'type': e['type']}
            for e in edges if e['from'] in id_map and e['to'] in id_map
        ]

        return {
            'nodes': nodes_out, 'edges': edges_out,
            'metadata': {'name': 'Call Graph', 'source_code': source,
                         'paths': [], 'prime_paths': [], 'du_chains': {}},
        }

    def _classify(self, name: str, edges: list[dict]) -> str:
        has_callers = any(e['to'] == name for e in edges)
        has_callees = any(e['from'] == name for e in edges)
        is_recursive = any(e['from'] == name and e['to'] == name for e in edges)
        if is_recursive:
            return 'recursive'
        if not has_callers:
            return 'entry'
        if not has_callees:
            return 'leaf'
        return 'stmt'

    def _empty(self, msg: str) -> dict:
        return {
            'nodes': [{'id': 0, 'label': f'Error: {msg}', 'type': 'exception',
                       'code': msg, 'lines': [], 'vars_def': [], 'vars_use': []}],
            'edges': [],
            'metadata': {'name': 'Call Graph', 'source_code': msg,
                         'paths': [], 'prime_paths': [], 'du_chains': {}},
        }
