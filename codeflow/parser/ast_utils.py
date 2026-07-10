"""AST walking helpers — variable def/use extraction."""
import ast
from typing import List, Tuple


def get_vars_defined(node: ast.AST) -> List[str]:
    """Return variable names defined by this statement."""
    names = []
    if isinstance(node, ast.Assign):
        for t in node.targets:
            names.extend(_collect_names(t))
    elif isinstance(node, ast.AugAssign):
        names.extend(_collect_names(node.target))
    elif isinstance(node, ast.AnnAssign) and node.value is not None:
        names.extend(_collect_names(node.target))
    elif isinstance(node, ast.For):
        names.extend(_collect_names(node.target))
    elif isinstance(node, ast.FunctionDef):
        names.append(node.name)
    elif isinstance(node, ast.AsyncFunctionDef):
        names.append(node.name)
    elif isinstance(node, ast.ClassDef):
        names.append(node.name)
    elif isinstance(node, (ast.Import, ast.ImportFrom)):
        for alias in node.names:
            names.append(alias.asname or alias.name.split('.')[0])
    return names


def get_vars_used(node: ast.AST) -> List[str]:
    """Return variable names *read* by this statement (not assigned)."""
    used = []
    if isinstance(node, ast.Assign):
        used.extend(_collect_load_names(node.value))
        for t in node.targets:
            used.extend(_collect_load_names(t))   # subscripts etc.
    elif isinstance(node, ast.AugAssign):
        used.extend(_collect_load_names(node.value))
        used.extend(_collect_load_names(node.target))
    elif isinstance(node, ast.AnnAssign) and node.value:
        used.extend(_collect_load_names(node.value))
    elif isinstance(node, ast.Expr):
        used.extend(_collect_load_names(node.value))
    elif isinstance(node, ast.Return) and node.value:
        used.extend(_collect_load_names(node.value))
    elif isinstance(node, ast.If):
        used.extend(_collect_load_names(node.test))
    elif isinstance(node, ast.While):
        used.extend(_collect_load_names(node.test))
    elif isinstance(node, ast.For):
        used.extend(_collect_load_names(node.iter))
    elif isinstance(node, ast.Assert):
        used.extend(_collect_load_names(node.test))
    elif isinstance(node, ast.Delete):
        for t in node.targets:
            used.extend(_collect_load_names(t))
    return list(set(used))


def _collect_names(node: ast.AST) -> List[str]:
    """Names being stored to (LHS of assignment, for-target, etc.)."""
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, (ast.Tuple, ast.List)):
        names = []
        for elt in node.elts:
            names.extend(_collect_names(elt))
        return names
    if isinstance(node, ast.Starred):
        return _collect_names(node.value)
    return []


def _collect_load_names(node: ast.AST) -> List[str]:
    """All Name nodes in Load context within an expression."""
    names = []
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
            names.append(child.id)
    return names


def source_lines(node: ast.AST) -> List[int]:
    """Line numbers spanned by this AST node."""
    lines = set()
    for child in ast.walk(node):
        if hasattr(child, 'lineno'):
            lines.add(child.lineno)
    return sorted(lines)


def stmt_label(node: ast.AST, source: str = '') -> str:
    """Short human-readable label for an AST statement node."""
    if source:
        src_lines = source.splitlines()
        if hasattr(node, 'lineno'):
            line = src_lines[node.lineno - 1].strip() if node.lineno <= len(src_lines) else ''
            if len(line) > 50:
                line = line[:47] + '…'
            return line
    return type(node).__name__
