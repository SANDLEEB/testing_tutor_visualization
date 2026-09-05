"""
Build a call graph from Python source: functions as nodes, calls as edges.
"""
import ast
from typing import Dict, List, Set, Any


class CallGraphBuilder:
    def build(self, source: str) -> Dict[str, Any]:
        try:
            tree = ast.parse(source)
        except SyntaxError as e:
            return self._empty(str(e))

        # Collect all function definitions
        funcs: Dict[str, Dict] = {}
        self._collect_funcs(tree, funcs, parent=None)

        # Collect calls within each function
        edges: List[Dict] = []
        seen_edges: Set = set()

        for fname, info in funcs.items():
            calls = self._collect_calls(info['node'])
            for callee in calls:
                if callee in funcs:
                    key = (fname, callee)
                    if key not in seen_edges:
                        seen_edges.add(key)
                        edges.append({'from': fname, 'to': callee,
                                      'label': '', 'type': 'call'})

        # Detect self-loops (recursion)
        for e in edges:
            if e['from'] == e['to']:
                e['type'] = 'recursive'

        # Assign integer IDs
        node_list = list(funcs.values())
        id_map    = {n['name']: i for i, n in enumerate(node_list)}

        nodes_out = []
        for n in node_list:
            ftype = self._classify(n['name'], edges, id_map)
            nodes_out.append({
                'id':    id_map[n['name']],
                'label': n['name'],
                'type':  ftype,
                'code':  f"def {n['name']}({', '.join(n['args'])})",
                'lines': n['lines'],
                'vars_def': [],
                'vars_use': [],
            })

        edges_out = []
        for e in edges:
            if e['from'] in id_map and e['to'] in id_map:
                edges_out.append({
                    'from':  id_map[e['from']],
                    'to':    id_map[e['to']],
                    'label': e['label'],
                    'type':  e['type'],
                })

        return {
            'nodes': nodes_out,
            'edges': edges_out,
            'metadata': {
                'name': 'Call Graph',
                'source_code': source,
                'paths': [],
                'prime_paths': [],
                'du_chains': {},
            },
        }

    def _collect_funcs(self, tree, funcs: dict, parent: str | None):
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = [a.arg for a in node.args.args]
                lines = list(range(node.lineno, node.end_lineno + 1)) \
                    if hasattr(node, 'end_lineno') else [node.lineno]
                funcs[node.name] = {
                    'name': node.name,
                    'args': args,
                    'lines': lines,
                    'node': node,
                    'parent': parent,
                }

    def _collect_calls(self, func_node: ast.AST) -> List[str]:
        calls = []
        for node in ast.walk(func_node):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    calls.append(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    calls.append(node.func.attr)
        return calls

    def _classify(self, name: str, edges: List[Dict], id_map: Dict) -> str:
        has_callers  = any(e['to']   == name for e in edges)
        has_callees  = any(e['from'] == name for e in edges)
        is_recursive = any(e['from'] == name and e['to'] == name for e in edges)
        if is_recursive:
            return 'recursive'
        if not has_callers:
            return 'entry'
        if not has_callees:
            return 'leaf'
        return 'stmt'

    def _empty(self, msg: str) -> Dict:
        return {
            'nodes': [{'id': 0, 'label': f'Error: {msg}', 'type': 'exception',
                       'code': msg, 'lines': [], 'vars_def': [], 'vars_use': []}],
            'edges': [],
            'metadata': {'name': 'Call Graph', 'source_code': msg,
                         'paths': [], 'prime_paths': [], 'du_chains': {}},
        }

