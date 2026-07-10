"""
Def-Use chain and DU-path analysis using reaching-definitions data-flow.
Operates on the graph-data dict produced by CFGBuilder.
"""
from typing import Dict, List, Set, Tuple, Any


def analyze_du(graph_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Augments *graph_data* with DU-chain information and returns an
    extended dict.  Adds 'du_chains' key to metadata.
    """
    nodes = graph_data['nodes']
    edges = graph_data['edges']

    # Build adjacency structures
    adj:     Dict[int, List[int]] = {n['id']: [] for n in nodes}
    pred:    Dict[int, List[int]] = {n['id']: [] for n in nodes}
    for e in edges:
        adj[e['from']].append(e['to'])
        pred[e['to']].append(e['from'])

    # Collect all variables defined anywhere
    all_vars: Set[str] = set()
    for n in nodes:
        all_vars.update(n.get('vars_def', []))

    # ── Reaching definitions (iterative fixed-point) ─────────────────────
    # A definition is (var, node_id).
    # gen[n]  = {(v, n) for v in vars_def[n]}
    # kill[n] = {(v, m) for v in vars_def[n], m != n} — all other defs of same var
    all_defs: Set[Tuple[str, int]] = set()
    for n in nodes:
        for v in n.get('vars_def', []):
            all_defs.add((v, n['id']))

    gen:  Dict[int, Set] = {}
    kill: Dict[int, Set] = {}
    for n in nodes:
        nid = n['id']
        gen[nid]  = {(v, nid) for v in n.get('vars_def', [])}
        kill[nid] = {(v, m) for (v, m) in all_defs
                     if v in n.get('vars_def', []) and m != nid}

    rd_in:  Dict[int, Set] = {n['id']: set() for n in nodes}
    rd_out: Dict[int, Set] = {n['id']: set() for n in nodes}

    changed = True
    while changed:
        changed = False
        for n in nodes:
            nid = n['id']
            new_in: Set = set()
            for p in pred[nid]:
                new_in |= rd_out[p]
            new_out = gen[nid] | (new_in - kill[nid])
            if new_out != rd_out[nid] or new_in != rd_in[nid]:
                rd_in[nid]  = new_in
                rd_out[nid] = new_out
                changed = True

    # ── DU-pairs ─────────────────────────────────────────────────────────
    # For each use in node u of variable v, find all (v, d) in rd_in[u].
    du_chains: Dict[str, List[Dict]] = {}
    du_pairs: List[Dict] = []

    for u_node in nodes:
        uid = u_node['id']
        for v in u_node.get('vars_use', []):
            for (rv, def_nid) in rd_in[uid]:
                if rv == v:
                    if v not in du_chains:
                        du_chains[v] = []
                    pair = {'def': def_nid, 'use': uid, 'var': v}
                    if pair not in du_chains[v]:
                        du_chains[v].append(pair)
                    du_pairs.append(pair)

    # ── DU-paths (def-clear paths between def and use) ───────────────────
    du_paths: List[Dict] = []
    for pair in du_pairs:
        paths = _def_clear_paths(pair['def'], pair['use'], pair['var'], adj, nodes)
        pair['paths'] = paths

    import copy
    result = copy.deepcopy(graph_data)
    result['metadata']['du_chains'] = du_chains
    result['metadata']['du_pairs']  = du_pairs

    # Tag nodes with def/use info for the UI
    du_node_roles: Dict[int, Dict[str, List[str]]] = {}
    for v, pairs in du_chains.items():
        for p in pairs:
            d, u = p['def'], p['use']
            du_node_roles.setdefault(d, {'def': [], 'use': []})['def'].append(v)
            du_node_roles.setdefault(u, {'def': [], 'use': []})['use'].append(v)

    for n in result['nodes']:
        roles = du_node_roles.get(n['id'], {})
        n['du_def'] = list(set(roles.get('def', [])))
        n['du_use'] = list(set(roles.get('use', [])))

    return result


def _def_clear_paths(def_id: int, use_id: int, var: str,
                     adj: Dict[int, List[int]],
                     nodes: List[Dict]) -> List[List[int]]:
    """
    Find all simple paths from def_id to use_id that are def-clear w.r.t. var
    (i.e. the variable is not re-defined at any intermediate node).
    """
    def_nodes = {n['id'] for n in nodes if var in n.get('vars_def', [])}

    paths: List[List[int]] = []
    stack = [(def_id, [def_id], {def_id})]

    while stack:
        cur, path, visited = stack.pop()
        if cur == use_id and len(path) > 1:
            paths.append(list(path))
            continue
        if len(paths) >= 50:
            break
        for nxt in adj.get(cur, []):
            if nxt in visited:
                continue
            # Def-clear: intermediate nodes must not redefine var
            if nxt != use_id and nxt in def_nodes and nxt != def_id:
                continue
            stack.append((nxt, path + [nxt], visited | {nxt}))

    return paths
