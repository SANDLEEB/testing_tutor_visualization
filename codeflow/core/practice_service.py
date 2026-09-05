"""Adaptive Practice Engine (authoring half): turn instructor-submitted source
code into a graph-grounded practice question — node-type identification,
def-use pairs, test-path selection, or coverage counting — the same four
question styles Quiz Mode already generates from static examples
(graphs/examples.py), but from any code an instructor pastes in, persisted,
and publishable like everything else in the Core Database.
"""
import json
import random

from sqlalchemy import select
from sqlalchemy.orm import Session

import config
from core.ai_service import rewrite_practice_question
from core.models import Concept, Question, QuestionSource, QuestionType
from graphs.serializer import GraphSerializer
from parser.cfg_builder import CFGBuilder
from parser.du_analyzer import analyze_du


class PracticeGenerationError(Exception):
    """Raised when the requested question type has no basis in the given source code."""


_NODE_TYPE_DISPLAY = {
    'entry': 'Entry/Exit', 'exit': 'Entry/Exit',
    'stmt': 'Statement', 'decision': 'Decision', 'exception': 'Exception',
}
_NODE_TYPE_CHOICES = ['Entry/Exit', 'Statement', 'Decision', 'Exception']


def _gen_node_type(gd: dict) -> dict:
    candidates = [n for n in gd['nodes'] if n['type'] in ('decision', 'stmt', 'exception')] or gd['nodes']
    if not candidates:
        raise PracticeGenerationError('No nodes found in the generated graph.')
    n = random.choice(candidates)
    answer = _NODE_TYPE_DISPLAY[n['type']]
    explanation = f'Node {n["id"]} ("{n["label"][:40]}") is a {answer.lower()} node.'
    if answer == 'Decision':
        explanation += ' Decision nodes have True/False branches — they represent if/while/for conditions.'
    return {
        'prompt': f'What type is node {n["id"]} ("{n["label"][:40]}")?',
        'choices': _NODE_TYPE_CHOICES,
        'answer': answer,
        'explanation': explanation,
        'hint': 'Look at how many outgoing edges the node has, and whether they\'re labeled True/False.',
    }


def _gen_du_pair(gd: dict) -> dict:
    du = gd['metadata'].get('du_chains', {})
    usable = [(var, pairs[0]) for var, pairs in du.items() if pairs]
    if not usable:
        raise PracticeGenerationError(
            'No def-use pairs found — try code where a variable is defined once and used later.'
        )
    var, pair = random.choice(usable)
    other = [n['id'] for n in gd['nodes'] if n['id'] != pair['use']]
    choices = list({pair['use']} | set(random.sample(other, min(3, len(other)))))
    random.shuffle(choices)
    return {
        'prompt': f'For variable "{var}", which node id contains a USE that this definition reaches?',
        'choices': [str(c) for c in choices[:4]],
        'answer': str(pair['use']),
        'explanation': f'Node {pair["use"]} reads "{var}" after it was defined at node {pair["def"]}.',
        'hint': f'Trace "{var}" forward from its definition at node {pair["def"]} along the control-flow edges.',
    }


def _gen_path_select(gd: dict) -> dict:
    paths = gd['metadata'].get('paths', [])
    if len(paths) < 2:
        raise PracticeGenerationError('Need at least 2 distinct Entry-to-Exit paths — try code with a branch or loop.')

    correct = random.choice(paths)
    others = [p for p in paths if p != correct]
    distractors = random.sample(others, min(2, len(others)))
    distractors.append(list(reversed(correct)))
    if len(correct) > 2:
        distractors.append(correct[:-1])

    seen = {json.dumps(correct)}
    uniq = []
    for d in distractors:
        key = json.dumps(d)
        if key not in seen:
            seen.add(key)
            uniq.append(d)

    choices_raw = [correct] + uniq[:3]
    random.shuffle(choices_raw)
    return {
        'prompt': 'Which is a valid simple path from Entry to Exit?',
        'choices': [json.dumps(c) for c in choices_raw],
        'answer': json.dumps(correct),
        'explanation': f'The path {correct} follows real control-flow edges from Entry to Exit.',
        'hint': 'Check that each consecutive pair of nodes is actually connected by an edge, '
                'and that the path starts at Entry and ends at Exit.',
    }


def _gen_coverage_count(gd: dict) -> dict:
    n = len(gd['nodes'])
    if n < 2:
        raise PracticeGenerationError('Graph is too small to ask a coverage question about.')
    opts = list({str(n), str(max(n - 1, 1)), str(n + 1), str(max(n - 2, 1))})
    random.shuffle(opts)
    return {
        'prompt': 'How many nodes must be visited to achieve 100% Node Coverage?',
        'choices': opts[:4],
        'answer': str(n),
        'explanation': f'Node Coverage requires all {n} nodes to be executed at least once.',
        'hint': 'Count every node in the graph, including Entry and Exit.',
    }


_GENERATORS = {
    QuestionType.node_type: _gen_node_type,
    QuestionType.du_pair: _gen_du_pair,
    QuestionType.path_select: _gen_path_select,
    QuestionType.coverage_count: _gen_coverage_count,
}


def supported_types() -> list[QuestionType]:
    return list(_GENERATORS)


def summarize_graph(gd: dict) -> str:
    """Compact textual description of a graph, for grounding LLM feedback without a full JSON dump."""
    node_bits = [f'{n["id"]}={n["label"][:30]!r} ({n["type"]})' for n in gd['nodes']]
    edge_bits = [f'{e["from"]}->{e["to"]}' + (f' ({e["label"]})' if e['label'] else '') for e in gd['edges']]
    return f"Nodes: {', '.join(node_bits)}. Edges: {', '.join(edge_bits)}."


def generate_practice_question(source_code: str, qtype: QuestionType) -> dict:
    """Build graph_data from *source_code* and generate a *qtype* question over it.

    Returns {prompt, choices, answer, explanation, hint, graph_data}.
    Raises PracticeGenerationError if the source doesn't parse, or has no basis
    for the requested question type (e.g. no branches for a path question).
    """
    generator = _GENERATORS.get(qtype)
    if generator is None:
        raise PracticeGenerationError(f'Unsupported practice question type: {qtype}')

    gd = CFGBuilder().build(source_code)
    if gd['metadata'].get('name') == 'Parse Error':
        raise PracticeGenerationError(gd['metadata'].get('source_code', 'Invalid Python source.'))

    if qtype == QuestionType.du_pair:
        gd = analyze_du(gd)

    fields = generator(gd)
    gd = GraphSerializer.inject_layout_hints(gd, 'layered')
    return {**fields, 'graph_data': gd}


async def generate_practice_question_ai(source_code: str, qtype: QuestionType) -> dict:
    """Same as generate_practice_question, then — if a provider is configured — rewords the
    prompt/explanation/hint via ai_service.rewrite_practice_question. `await`ed directly from an
    async page handler (same pattern as ai_service.generate_source_code's callers in
    instructor_assignments_page.py), not run via asyncio.run: this can be called while NiceGUI's
    own event loop is already running, where asyncio.run would raise. choices/answer/graph_data
    are never touched by the rewrite — only the wording changes.
    """
    fields = generate_practice_question(source_code, qtype)
    if not config.AI_ENABLED:
        return fields
    graph_data = fields['graph_data']
    rewritten = await rewrite_practice_question(
        qtype=qtype.value, source_code=source_code, graph_summary=summarize_graph(graph_data),
        prompt=fields['prompt'], choices=fields['choices'], answer=fields['answer'],
        explanation=fields['explanation'], hint=fields['hint'],
    )
    return {**rewritten, 'graph_data': graph_data}


def create_practice_question(
    session: Session, *, qtype: QuestionType, source_code: str, concept_id: int, created_by_id: int,
    fields: dict | None = None,
) -> Question:
    """*concept_id* must be an existing topic (Concept) — topics are only ever created on
    the instructor Topics page (core/bkt_service.get_or_create_concept), never as a side
    effect of authoring a practice question, so this never creates one itself.

    fields, if given, is an already-generated {prompt, choices, answer, explanation, hint,
    graph_data} dict (e.g. from the awaited generate_practice_question_ai) — otherwise generated
    fresh via generate_practice_question. Accepting pre-generated fields keeps this function
    itself synchronous (a plain DB write); the async AI-rewrite step happens in the page handler.
    """
    result = fields if fields is not None else generate_practice_question(source_code, qtype)
    question = Question(
        type=qtype, source=QuestionSource.template_generated, concept_id=concept_id,
        prompt=result['prompt'], choices=result['choices'], answer=result['answer'],
        explanation=result['explanation'], hint=result['hint'], graph_data=result['graph_data'],
        created_by_id=created_by_id,
    )
    session.add(question)
    session.flush()
    return question


def list_practice_questions(
    session: Session, *, created_by_id: int | None = None, concept_id: int | None = None,
    course_id: int | None = None, published_only: bool = False,
) -> list[Question]:
    stmt = select(Question).where(Question.source == QuestionSource.template_generated)
    if created_by_id is not None:
        stmt = stmt.where(Question.created_by_id == created_by_id)
    if concept_id is not None:
        stmt = stmt.where(Question.concept_id == concept_id)
    if course_id is not None:
        stmt = stmt.join(Concept, Concept.id == Question.concept_id).where(Concept.course_id == course_id)
    if published_only:
        stmt = stmt.where(Question.published.is_(True))
    stmt = stmt.order_by(Question.created_at.desc())
    return list(session.scalars(stmt))
