"""
Convert graph objects (dicts produced by parser modules) to clean JSON
suitable for the JS GraphAnimator.  Also applies auto-layout hints.
"""
import json
import math
from typing import Any, Dict, List


class GraphSerializer:

    @staticmethod
    def to_json(graph_data: Dict[str, Any], indent: int = None) -> str:
        return json.dumps(graph_data, indent=indent)

    @staticmethod
    def inject_layout_hints(graph_data: Dict[str, Any],
                            algorithm: str = 'layered') -> Dict[str, Any]:
        """
        Inject normalised x/y position hints (0..1) into nodes so the JS
        engine can use them directly with setNodePositions().
        Uses a Python-side layered layout for CFGs.
        """
        import copy
        data   = copy.deepcopy(graph_data)
        nodes  = data['nodes']
        edges  = data['edges']

        if not nodes:
            return data

        if algorithm == 'layered':
            positions = _layered_layout(nodes, edges)
        else:
            positions = _circle_layout(nodes)

        for n in nodes:
            pos = positions.get(n['id'], {'x': 0.5, 'y': 0.5})
            n['x'] = round(pos['x'], 4)
            n['y'] = round(pos['y'], 4)

        return data


# ─── Layout algorithms ────────────────────────────────────────────────────

def _layered_layout(nodes: List[Dict], edges: List[Dict]) -> Dict[int, Dict]:
    ids  = [n['id'] for n in nodes]
    adj  = {i: [] for i in ids}
    for e in edges:
        if e['type'] not in ('back-edge', 'recursive') and e['from'] in adj:
            adj[e['from']].append(e['to'])

    # Longest-path rank via Bellman-Ford-style relaxation — only correct/
    # guaranteed-terminating for a DAG. CFGs mark loops as 'back-edge' and
    # are excluded above, but call graphs only label direct self-recursion
    # as 'recursive' — a longer cycle (mutual recursion, A calls B calls A)
    # isn't labeled at all and would relax forever. Bellman-Ford only ever
    # needs |V|-1 passes for a true DAG, so bounding the loop is both the
    # standard termination check and a hard safety net against any cycle.
    rank = {}
    entry = next((n['id'] for n in nodes if n['type'] == 'entry'), ids[0])
    rank[entry] = 0

    for _ in range(len(ids) + 1):
        changed = False
        for nid in ids:
            for child in adj.get(nid, []):
                new_r = rank.get(nid, 0) + 1
                if rank.get(child, -1) < new_r:
                    rank[child] = new_r
                    changed = True
        if not changed:
            break

    for nid in ids:
        if nid not in rank:
            rank[nid] = 0

    by_rank: Dict[int, List[int]] = {}
    for nid, r in rank.items():
        by_rank.setdefault(r, []).append(nid)

    max_rank = max(by_rank.keys(), default=0)

    positions: Dict[int, Dict] = {}
    for r, nids in by_rank.items():
        n    = len(nids)
        y    = (r + 0.5) / (max_rank + 1) if max_rank > 0 else 0.5
        for i, nid in enumerate(nids):
            x = (i + 0.5) / n
            positions[nid] = {'x': x, 'y': y}

    return positions


def _circle_layout(nodes: List[Dict]) -> Dict[int, Dict]:
    n = len(nodes)
    if n == 0:
        return {}
    positions = {}
    for i, node in enumerate(nodes):
        angle = 2 * math.pi * i / n - math.pi / 2
        positions[node['id']] = {
            'x': 0.5 + 0.4 * math.cos(angle),
            'y': 0.5 + 0.4 * math.sin(angle),
        }
    return positions
