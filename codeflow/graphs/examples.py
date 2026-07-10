"""
10 pre-computed example programs with their CFG/call-graph data.
Each entry has keys: name, description, source, graph_data, call_graph_data (optional).
graph_data is the full dict expected by GraphAnimator.
"""

from parser.cfg_builder import CFGBuilder
from parser.du_analyzer import analyze_du
from parser.dominator import compute_dominators
from parser.call_graph import CallGraphBuilder
from graphs.serializer import GraphSerializer


# ─── Source snippets ──────────────────────────────────────────────────────

SOURCES = {
    'if_else': '''\
x = int(input())
if x > 0:
    y = x
else:
    y = -x
print(y)
''',

    'while_sum': '''\
numbers = [1, 2, 3, 4, 5]
total = 0
i = 0
while i < len(numbers):
    total = total + numbers[i]
    i = i + 1
print(total)
''',

    'triangle': '''\
a = int(input())
b = int(input())
c = int(input())
if a == b and b == c:
    kind = "equilateral"
elif a == b or b == c or a == c:
    kind = "isosceles"
else:
    kind = "scalene"
print(kind)
''',

    'linear_search': '''\
def linear_search(lst, target):
    for i in range(len(lst)):
        if lst[i] == target:
            return i
    return -1
''',

    'try_except': '''\
def safe_divide(a, b):
    try:
        result = a / b
        return result
    except ZeroDivisionError:
        return None
    finally:
        print("done")
''',

    'factorial': '''\
def factorial(n):
    if n <= 1:
        return 1
    else:
        return n * factorial(n - 1)
''',

    'bubble_sort': '''\
def bubble_sort(arr):
    n = len(arr)
    for i in range(n):
        for j in range(0, n - i - 1):
            if arr[j] > arr[j + 1]:
                arr[j], arr[j + 1] = arr[j + 1], arr[j]
    return arr
''',

    'binary_search': '''\
def binary_search(arr, target):
    low = 0
    high = len(arr) - 1
    while low <= high:
        mid = (low + high) // 2
        if arr[mid] == target:
            return mid
        elif arr[mid] < target:
            low = mid + 1
        else:
            high = mid - 1
    return -1
''',

    'class_methods': '''\
class Stack:
    def __init__(self):
        self.items = []

    def push(self, item):
        self.items.append(item)

    def pop(self):
        if not self.items:
            return None
        return self.items.pop()

    def peek(self):
        if self.items:
            return self.items[-1]
        return None
''',

    'fizzbuzz': '''\
def fizzbuzz(n):
    result = []
    for i in range(1, n + 1):
        if i % 15 == 0:
            result.append("FizzBuzz")
        elif i % 3 == 0:
            result.append("Fizz")
        elif i % 5 == 0:
            result.append("Buzz")
        else:
            result.append(str(i))
    return result
''',
}

DESCRIPTIONS = {
    'if_else':       'Simple if-else — absolute value of x',
    'while_sum':     'While loop — sum of an array',
    'triangle':      'Nested if — triangle classifier (equilateral / isosceles / scalene)',
    'linear_search': 'For loop with early return — linear search',
    'try_except':    'Try-except-finally — safe division',
    'factorial':     'Recursive function — factorial(n)',
    'bubble_sort':   'Nested loops — bubble sort',
    'binary_search': 'While loop with branching — binary search',
    'class_methods': 'Class with 3 methods — stack (call graph demo)',
    'fizzbuzz':      'For loop with multiple conditions — FizzBuzz',
}


def _build_example(key: str) -> dict:
    src  = SOURCES[key]
    cfg  = CFGBuilder().build(src)
    cfg  = GraphSerializer.inject_layout_hints(cfg, 'layered')
    cfg  = analyze_du(cfg)
    cfg  = compute_dominators(cfg)
    cfg['metadata']['name'] = DESCRIPTIONS[key]
    cg   = CallGraphBuilder().build(src)
    cg   = GraphSerializer.inject_layout_hints(cg, 'layered')
    return {
        'name':        DESCRIPTIONS[key],
        'key':         key,
        'source':      src,
        'graph_data':  cfg,
        'call_graph':  cg,
    }


# Lazy-build cache so we don't parse at import time
_CACHE: dict = {}


def get_example(key: str) -> dict:
    if key not in _CACHE:
        _CACHE[key] = _build_example(key)
    return _CACHE[key]


def get_all_examples() -> list:
    return [get_example(k) for k in SOURCES]


# Pre-build at module level for fast first-load
def _preload():
    for k in SOURCES:
        try:
            get_example(k)
        except Exception as exc:
            print(f'[examples] warning: could not build "{k}": {exc}')


EXAMPLES = {k: None for k in SOURCES}  # populated lazily via get_example()

EXAMPLE_KEYS  = list(SOURCES.keys())
EXAMPLE_NAMES = [DESCRIPTIONS[k] for k in EXAMPLE_KEYS]
