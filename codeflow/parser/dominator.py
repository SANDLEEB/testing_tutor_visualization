"""
Dominator tree computation using Cooper et al.'s iterative algorithm.
Also computes post-dominators and dominance frontiers.
"""
from typing import Dict, List, Optional, Set, Any


def compute_dominators(graph_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Adds dominator-tree information to *graph_data* and returns a new dict.
    New metadata keys:
      - idom:         { node_id: immediate_dominator_id }
      - dom_tree_edges: [ {from, to} ]
      - dom_frontier: { node_id: [frontier_ids] }
      - post_idom:    { node_id: immediate_post_dominator_id }
    """
    import copy
    result = copy.deepcopy(graph_data)
    nodes  = result['nodes']
    edges  = result['edges']

    ids    = [n['id'] for n in nodes]
    entry  = _find_node(nodes, 'entry')
    exit_  = _find_node(nodes, 'exit')

    if entry is None:
        return result

    # Forward dominators
    pred: Dict[int, List[int]] = {i: [] for i in ids}
    succ: Dict[int, List[int]] = {i: [] for i in ids}
    for e in edges:
        succ[e['from']].append(e['to'])
        pred[e['to']].append(e['from'])

    rpo     = _reverse_post_order(entry, succ, ids)
    idom    = _cooper_dominators(entry, rpo, pred)
    df      = _dominance_frontier(idom, succ, pred, ids)

    # Post-dominators (reverse graph, from exit)
    if exit_ is not None:
        rpo_rev   = _reverse_post_order(exit_, pred, ids)
        post_idom = _cooper_dominators(exit_, rpo_rev, succ)
    else:
        post_idom = {}

    # Build tree edges
    tree_edges = [{'from': parent, 'to': child}
                  for child, parent in idom.items() if parent != child]

    result['metadata']['idom']           = idom
    result['metadata']['dom_tree_edges'] = tree_edges
    result['metadata']['dom_frontier']   = {k: list(v) for k, v in df.items()}
    result['metadata']['post_idom']      = post_idom

    return result


# ─── Cooper's algorithm ────────────────────────────────────────────────────

def _cooper_dominators(entry: int, rpo: List[int],
                       pred: Dict[int, List[int]]) -> Dict[int, int]:
    """
    Cooper, Harvey, Kennedy (2001) — simple, fast dominator computation.
    Returns idom mapping: node_id -> immediate_dominator_id.
    """
    idom: Dict[int, Optional[int]] = {n: None for n in rpo}
    idom[entry] = entry
    rpo_index   = {n: i for i, n in enumerate(rpo)}

    changed = True
    while changed:
        changed = False
        for n in rpo:
            if n == entry:
                continue
            processed_preds = [p for p in pred.get(n, []) if idom[p] is not None]
            if not processed_preds:
                continue
            new_idom = processed_preds[0]
            for p in processed_preds[1:]:
                new_idom = _intersect(p, new_idom, idom, rpo_index)
            if idom[n] != new_idom:
                idom[n] = new_idom
                changed = True

    return {k: v for k, v in idom.items() if v is not None}


def _intersect(b1: int, b2: int,
               idom: Dict[int, Optional[int]],
               rpo_index: Dict[int, int]) -> int:
    f1, f2 = b1, b2
    while f1 != f2:
        while rpo_index.get(f1, 0) > rpo_index.get(f2, 0):
            f1 = idom[f1]  # type: ignore
        while rpo_index.get(f2, 0) > rpo_index.get(f1, 0):
            f2 = idom[f2]  # type: ignore
    return f1


def _reverse_post_order(start: int, adj: Dict[int, List[int]],
                        all_nodes: List[int]) -> List[int]:
    visited: Set[int] = set()
    post: List[int]   = []

    def dfs(n):
        visited.add(n)
        for s in adj.get(n, []):
            if s not in visited and s in set(all_nodes):
                dfs(s)
        post.append(n)

    import sys
    sys.setrecursionlimit(max(sys.getrecursionlimit(), len(all_nodes) + 100))
    dfs(start)
    return list(reversed(post))


def _dominance_frontier(idom: Dict[int, int],
                        succ: Dict[int, List[int]],
                        pred: Dict[int, List[int]],
                        all_nodes: List[int]) -> Dict[int, Set[int]]:
    df: Dict[int, Set[int]] = {n: set() for n in all_nodes}
    for n in all_nodes:
        preds = pred.get(n, [])
        if len(preds) >= 2:
            for p in preds:
                runner = p
                while runner != idom.get(n, n):
                    df.setdefault(runner, set()).add(n)
                    if runner == idom.get(runner, runner):
                        break
                    runner = idom.get(runner, runner)
    return df


def _find_node(nodes: List[Dict], ntype: str) -> Optional[int]:
    for n in nodes:
        if n['type'] == ntype:
            return n['id']
    return None


def get_dominator_set(node_id: int, idom: Dict[int, int]) -> List[int]:
    """All dominators of node_id (walking up the idom chain)."""
    result = []
    cur    = node_id
    while True:
        parent = idom.get(cur)
        if parent is None or parent == cur:
            result.append(cur)
            break
        result.append(cur)
        cur = parent
    return result
