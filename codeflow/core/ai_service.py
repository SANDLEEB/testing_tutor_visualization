"""AI Question Authoring Service.

RAG over instructor-uploaded CourseMaterial: chunk the material, retrieve the
chunks most relevant to the instructor's chat message, ground an LLM chat
completion in that context, and parse any quiz questions the model proposes
so the instructor can save them to the Question bank.

Three LLM providers are supported, all through the same OpenAI-style
{'role', 'content'} message list — tried in priority order Groq, then Gemini,
then OpenRouter, whichever is configured first. See _call_llm.

Retrieval is deliberately simple (lexical term overlap, no embeddings/vector
DB) — good enough for a handful of course documents, and it keeps this
service free of extra infra dependencies. Swap `_score_chunk` for an
embedding-based ranker later without touching the rest of the pipeline.
"""
import json
import re

import httpx

import config
from core.models import CourseMaterial

_WORD_RE = re.compile(r"[a-zA-Z']+")

_SYSTEM_PROMPT = """You are an assistant helping a software-testing instructor \
author quiz questions from their course material.

Answer the instructor's question normally. If — and only if — they ask you to \
generate quiz questions, include a fenced ```json code block containing a JSON \
array of question objects, each shaped exactly like:
{"prompt": "...", "choices": ["...", "...", "...", "..."], "answer": "<one of choices, verbatim>", "explanation": "..."}
Only use facts from the provided course material context. If the context doesn't \
cover something, say so instead of inventing it."""


class AIServiceError(Exception):
    """Raised when the configured LLM provider's call fails, or none is configured."""


def chunk_text(text: str, chunk_size: int = 180, overlap: int = 30) -> list[str]:
    """Split text into overlapping word-count chunks."""
    words = text.split()
    if not words:
        return []
    chunks = []
    step = max(chunk_size - overlap, 1)
    for start in range(0, len(words), step):
        chunk = ' '.join(words[start:start + chunk_size])
        if chunk:
            chunks.append(chunk)
        if start + chunk_size >= len(words):
            break
    return chunks


def _score_chunk(chunk: str, query_terms: set[str]) -> float:
    chunk_terms = set(w.lower() for w in _WORD_RE.findall(chunk))
    if not chunk_terms or not query_terms:
        return 0.0
    overlap = query_terms & chunk_terms
    return len(overlap) / len(query_terms)


def retrieve_context(materials: list[CourseMaterial], query: str, top_k: int = 5) -> str:
    """Return the top_k most relevant chunks across *materials*, labelled by source."""
    query_terms = set(w.lower() for w in _WORD_RE.findall(query))
    scored: list[tuple[float, str, str]] = []  # (score, material_title, chunk)
    for material in materials:
        for chunk in chunk_text(material.content_text):
            score = _score_chunk(chunk, query_terms)
            if score > 0:
                scored.append((score, material.title, chunk))

    scored.sort(key=lambda t: t[0], reverse=True)
    top = scored[:top_k]
    if not top:
        return ''
    return '\n\n'.join(f'[Source: {title}]\n{chunk}' for _, title, chunk in top)


async def _call_gemini(messages: list[dict]) -> str:
    """Low-level Gemini (Google AI Studio) call. Gemini's REST shape differs from the
    OpenAI-style *messages* list we use everywhere else: system prompts are a separate
    systemInstruction field, not a message with role='system', and the assistant role is
    called 'model' rather than 'assistant' — translated here so every other function in
    this module stays provider-agnostic.
    """
    system_parts = [m['content'] for m in messages if m['role'] == 'system']
    contents = [
        {'role': 'model' if m['role'] == 'assistant' else 'user', 'parts': [{'text': m['content']}]}
        for m in messages if m['role'] != 'system'
    ]
    payload = {'contents': contents}
    if system_parts:
        payload['systemInstruction'] = {'parts': [{'text': '\n\n'.join(system_parts)}]}

    url = f'https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_MODEL}:generateContent'
    headers = {'Content-Type': 'application/json', 'x-goog-api-key': config.GEMINI_API_KEY}

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise AIServiceError(f'Gemini returned {e.response.status_code}: {e.response.text[:300]}') from e
    except httpx.HTTPError as e:
        raise AIServiceError(f'Could not reach Gemini: {e}') from e

    data = resp.json()
    try:
        parts = data['candidates'][0]['content']['parts']
        return ''.join(p.get('text', '') for p in parts)
    except (KeyError, IndexError) as e:
        raise AIServiceError(f'Unexpected Gemini response shape: {data}') from e


async def _call_groq(messages: list[dict]) -> str:
    """Low-level Groq chat completion call — OpenAI-compatible, identical request/response
    shape to OpenRouter, just a different endpoint/key."""
    payload = {'model': config.GROQ_MODEL, 'messages': messages}
    headers = {
        'Authorization': f'Bearer {config.GROQ_API_KEY}',
        'Content-Type': 'application/json',
    }

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                'https://api.groq.com/openai/v1/chat/completions', json=payload, headers=headers,
            )
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise AIServiceError(f'Groq returned {e.response.status_code}: {e.response.text[:300]}') from e
    except httpx.HTTPError as e:
        raise AIServiceError(f'Could not reach Groq: {e}') from e

    data = resp.json()
    try:
        return data['choices'][0]['message']['content']
    except (KeyError, IndexError) as e:
        raise AIServiceError(f'Unexpected Groq response shape: {data}') from e


async def _call_openrouter(messages: list[dict]) -> str:
    """Low-level OpenRouter chat completion call — *messages* must include the system prompt."""
    payload = {'model': config.OPENROUTER_MODEL, 'messages': messages}
    headers = {
        'Authorization': f'Bearer {config.OPENROUTER_API_KEY}',
        'Content-Type': 'application/json',
        'X-Title': 'Testing Tutor',
    }

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                'https://openrouter.ai/api/v1/chat/completions', json=payload, headers=headers,
            )
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise AIServiceError(f'OpenRouter returned {e.response.status_code}: {e.response.text[:300]}') from e
    except httpx.HTTPError as e:
        raise AIServiceError(f'Could not reach OpenRouter: {e}') from e

    data = resp.json()
    try:
        return data['choices'][0]['message']['content']
    except (KeyError, IndexError) as e:
        raise AIServiceError(f'Unexpected OpenRouter response shape: {data}') from e


async def _call_llm(messages: list[dict]) -> str:
    """Routes to whichever provider is configured — Groq first, then Gemini, then
    OpenRouter. Every other function in this module calls this instead of a provider
    function directly.
    """
    if config.GROQ_ENABLED:
        return await _call_groq(messages)
    if config.GEMINI_ENABLED:
        return await _call_gemini(messages)
    if config.OPENROUTER_ENABLED:
        return await _call_openrouter(messages)
    raise AIServiceError(
        'No AI provider configured — add GROQ_API_KEY, GEMINI_API_KEY, or OPENROUTER_API_KEY to codeflow/.env to enable this.'
    )


async def chat_completion(messages: list[dict], context: str) -> str:
    """Send *messages* to the LLM, grounded in *context*, and return the reply text."""
    system_content = _SYSTEM_PROMPT
    if context:
        system_content += f'\n\nCourse material context:\n{context}'
    else:
        system_content += '\n\nNo course material context was retrieved for this message.'
    return await _call_llm([{'role': 'system', 'content': system_content}, *messages])


_TEST_GEN_SYSTEM_PROMPT_PY = """You write pytest test suites for a software-testing course. \
Given a Python source module, write a thorough test suite covering normal cases, edge \
cases, and error cases. The suite must `from solution import ...` to reach the code under \
test (it will be graded against a file literally named solution.py). Respond with ONLY a \
single fenced ```python code block containing the complete test file — no other prose."""

_TEST_GEN_SYSTEM_PROMPT_CPP = """You write C++ test suites for a software-testing course, \
using a small header-only test framework (testkit.h) — NOT Google Test or Catch2. Given a \
C++ header (solution.h), write a thorough test suite covering normal cases, edge cases, and \
error cases, using exactly this API:
  #include "solution.h"
  #include "testkit.h"
  TEST(some_name) { ASSERT_EQ(expr, expected); ASSERT_TRUE(cond); ASSERT_THROWS(expr, ExceptionType); }
Do not write main() or #include <gtest/gtest.h> — testkit.h supplies main(). Respond with ONLY \
a single fenced ```cpp code block containing the complete test file — no other prose."""

_SOURCE_GEN_SYSTEM_PROMPT_PY = """You write example source code for a software-testing course \
assignment. Given a short description of what it should do, write a complete, well-formed Python \
module — solution.py — that a student will write pytest tests against. Include enough branching \
and edge-case logic (conditionals, loops, error handling as appropriate) that there's something \
meaningful to test; avoid trivial one-liners. Respond with ONLY a single fenced ```python code \
block containing the complete file — no other prose."""

_SOURCE_GEN_SYSTEM_PROMPT_CPP = """You write example source code for a software-testing course \
assignment, in C++. Given a short description of what it should do, write a complete, header-only \
C++ header — solution.h — with full inline function/class definitions (no separate .cpp), that a \
student will write tests against. Include enough branching and edge-case logic that there's \
something meaningful to test; avoid trivial one-liners. Respond with ONLY a single fenced ```cpp \
code block containing the complete header — no other prose."""

_CODE_BLOCK_RE = re.compile(r'```(?:python|cpp|c\+\+)?\s*(.*?)```', re.DOTALL)


async def generate_source_code(description: str, language: str = 'python') -> str:
    """Ask the LLM to draft assignment source code from a plain-language description; returns
    raw source code. *language* is 'python' or 'cpp' (matches core.models.AssignmentLanguage).
    The instructor is expected to review and fix this before it's ever saved — it's a draft,
    not a finished solution, and the compile check on save will catch anything broken.
    """
    is_cpp = language == 'cpp'
    system_prompt = _SOURCE_GEN_SYSTEM_PROMPT_CPP if is_cpp else _SOURCE_GEN_SYSTEM_PROMPT_PY
    reply = await _call_llm([
        {'role': 'system', 'content': system_prompt},
        {'role': 'user', 'content': description},
    ])
    match = _CODE_BLOCK_RE.search(reply)
    return match.group(1).strip() if match else reply.strip()


async def generate_test_suite(source_code: str, language: str = 'python') -> str:
    """Ask the LLM to write a test suite for *source_code*; returns raw source code.

    *language* is 'python' or 'cpp' (matches core.models.AssignmentLanguage values).
    """
    is_cpp = language == 'cpp'
    system_prompt = _TEST_GEN_SYSTEM_PROMPT_CPP if is_cpp else _TEST_GEN_SYSTEM_PROMPT_PY
    fence = 'cpp' if is_cpp else 'python'
    reply = await _call_llm([
        {'role': 'system', 'content': system_prompt},
        {'role': 'user', 'content': f'```{fence}\n{source_code}\n```'},
    ])
    match = _CODE_BLOCK_RE.search(reply)
    return match.group(1).strip() if match else reply.strip()


_EXAMPLE_EXPLANATION_SYSTEM_PROMPT = """You are a software-testing tutor writing a worked-example \
walkthrough for students who are about to practice writing tests themselves, possibly for the very \
first time. Given source code, an example test suite for it, and that suite's real coverage results, \
explain, in plain prose (no code fences, no headers):
1. What the source code does, briefly.
2. How the test suite works — walk through what each test actually checks, and why.
3. Why it achieves the coverage it does — if there are lines it never reaches, name them and explain \
what case is being skipped, honestly (don't pretend the example is more complete than it is).
4. 2-3 general, transferable principles of good test-suite design that this example illustrates \
(e.g. testing boundary cases, one clear assertion focus per test, covering the error/exception path).
Write for someone who has never written a test suite before. 150-300 words."""


async def generate_example_explanation(
    *, source_code: str, test_code: str, language: str, line_coverage_percent: float | None,
    branch_coverage_percent: float | None, missing_lines: list[int],
) -> str:
    """Ask the LLM to write the student-facing walkthrough for a worked example — grounded
    in the example's own real, already-measured coverage (core/example_service.py), not
    something the LLM has to guess at.
    """
    src_lines = source_code.splitlines()
    if missing_lines:
        missing_excerpt = '\n'.join(
            f'{n}: {src_lines[n - 1]}' for n in sorted(missing_lines) if 0 < n <= len(src_lines)
        )
    else:
        missing_excerpt = '(none — every line is executed)'

    user_content = (
        f'Language: {language}\n'
        f'Source code:\n```\n{source_code}\n```\n'
        f'Example test suite:\n```\n{test_code}\n```\n'
        f'Line coverage: {line_coverage_percent if line_coverage_percent is not None else "n/a"}%\n'
        f'Branch coverage: {branch_coverage_percent if branch_coverage_percent is not None else "n/a"}%\n'
        f'Lines never executed by this test suite:\n{missing_excerpt}\n'
        'Write the walkthrough now.'
    )
    return await _call_llm([
        {'role': 'system', 'content': _EXAMPLE_EXPLANATION_SYSTEM_PROMPT},
        {'role': 'user', 'content': user_content},
    ])


_FEEDBACK_SYSTEM_PROMPT = """You are a software-testing tutor giving a student brief, specific \
feedback right after they answer a practice question about a control-flow graph. In 2-4 \
sentences, compare their answer to the correct one, referencing the actual graph structure \
you're given. Be encouraging but precise about what they got right or wrong — don't just \
restate "correct" or "incorrect", explain the reasoning a student could learn from."""


async def generate_answer_feedback(
    *, prompt: str, graph_summary: str, correct_answer: str, student_answer: str, is_correct: bool,
) -> str:
    """Ask the LLM for a short, graph-grounded comparison of the student's answer to the correct one."""
    user_content = (
        f'Question: {prompt}\n'
        f'Graph structure: {graph_summary}\n'
        f'Correct answer: {correct_answer}\n'
        f'Student answered: {student_answer}\n'
        f'Their answer was {"correct" if is_correct else "incorrect"}.\n'
        'Give your feedback now.'
    )
    return await _call_llm([
        {'role': 'system', 'content': _FEEDBACK_SYSTEM_PROMPT},
        {'role': 'user', 'content': user_content},
    ])


_ASSIGNMENT_FEEDBACK_SYSTEM_PROMPT = """You are a software-testing tutor giving a student \
feedback on a test suite they just submitted for grading, by comparing it against the \
instructor's own reference test suite for the same source code. You'll be given the source \
code, the student's test suite, real already-measured results (pass/fail, score, line/branch \
coverage), and specifically which lines the INSTRUCTOR's reference suite exercises that the \
STUDENT's does not — ground your feedback in those actual lines, never guess or invent \
results. Be encouraging but specific: name what their tests do well, and for the lines the \
instructor's suite reaches but theirs doesn't, say what behavior or edge case there still \
needs a test. 100-200 words, plain prose, no headers or code fences.

After your prose feedback, on its own line, include a fenced json code block choosing which of \
the given missed line numbers are most worth showing the student in an animated diagram — pick \
the 1-5 most pedagogically important ones (or all of them if there are 5 or fewer). Use ONLY \
line numbers from the "Lines the instructor's suite reaches but the student's doesn't" list you \
were given — never invent one that wasn't in it. Shape it exactly like:
```json
{"highlight_lines": [5, 7]}
```"""

_HIGHLIGHT_JSON_RE = re.compile(r'```json\s*(\{.*?\})\s*```', re.DOTALL)


def _parse_highlight_lines(reply_text: str, candidate_lines: list[int]) -> list[int]:
    """Extract the LLM's chosen subset of candidate_lines from its reply, discarding anything
    it wasn't actually given — the graph-building code downstream trusts this list completely,
    so a hallucinated line number must never survive this filter.
    """
    match = _HIGHLIGHT_JSON_RE.search(reply_text)
    if not match:
        return []
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return []
    picked = data.get('highlight_lines') if isinstance(data, dict) else None
    if not isinstance(picked, list):
        return []
    valid = set(candidate_lines)
    return sorted({n for n in picked if isinstance(n, int) and n in valid})


async def generate_assignment_feedback(
    *, source_code: str, submitted_test_code: str, language: str, tests_passed: bool | None,
    score: float | None, line_coverage_percent: float | None, branch_coverage_percent: float | None,
    missed_lines: list[int],
) -> tuple[str, list[int]]:
    """Ask the LLM for tutoring feedback on a student's Assignment submission, grounded in that
    submission's real GradeResult (core/sandbox_service.py) AND in missed_lines — the gap
    between the instructor's reference suite and the student's own (core/assignment_service.py
    _compare_against_reference), not just raw coverage. Returns (prose, highlighted_lines): the
    LLM's own pick of which missed_lines are worth animating, already validated against
    missed_lines itself — never asked to guess at coverage, correctness, or invent a line
    number it wasn't given.
    """
    src_lines = source_code.splitlines()
    if missed_lines:
        missed_excerpt = '\n'.join(
            f'{n}: {src_lines[n - 1]}' for n in sorted(missed_lines) if 0 < n <= len(src_lines)
        )
    else:
        missed_excerpt = '(none — the student already covers everything the reference suite does)'

    user_content = (
        f'Language: {language}\n'
        f'Source code:\n```\n{source_code}\n```\n'
        f"Student's test suite:\n```\n{submitted_test_code}\n```\n"
        f'Tests passed: {"n/a" if tests_passed is None else tests_passed}\n'
        f'Score: {"n/a" if score is None else f"{score:.0f}%"}\n'
        f'Line coverage: {line_coverage_percent if line_coverage_percent is not None else "n/a"}%\n'
        f'Branch coverage: {branch_coverage_percent if branch_coverage_percent is not None else "n/a"}%\n'
        f"Lines the instructor's suite reaches but the student's doesn't:\n{missed_excerpt}\n"
        'Give your feedback now.'
    )
    reply = await _call_llm([
        {'role': 'system', 'content': _ASSIGNMENT_FEEDBACK_SYSTEM_PROMPT},
        {'role': 'user', 'content': user_content},
    ])
    highlighted = _parse_highlight_lines(reply, missed_lines)
    prose = _HIGHLIGHT_JSON_RE.sub('', reply).strip()
    return prose, highlighted


_PRACTICE_QUESTION_STYLE_DESC = {
    'node_type': "identifying what type of CFG node the question is asking about (Entry/Exit, Statement, Decision, or Exception)",
    'du_pair': "tracing a definition-use pair — which node uses a variable that was defined earlier",
    'path_select': "picking a valid Entry-to-Exit path through the control-flow graph",
    'coverage_count': "counting how many nodes must be visited for 100% node coverage",
}

_PRACTICE_REWRITE_SYSTEM_PROMPT = """You are rewriting an auto-generated control-flow-graph \
practice question — about {style_desc} — so it reads more naturally and teaches better, WITHOUT \
changing what's being asked or what's correct. You'll be given the source code, a description of \
the graph structure, and the exact prompt/choices/answer/explanation/hint already computed from \
that real graph. The choices and the correct answer are FIXED ground truth and must keep their \
exact original meaning — you may rephrase the surrounding question text, but never invent a new \
choice, drop one, or change which one is correct. The choices themselves are shown to the student \
as separate buttons below your prompt text, so do NOT list or restate them inside the prompt — \
just ask the question. Rewrite only the "prompt" (the question itself), \
"explanation", and "hint" fields to be clearer and more instructive for a student seeing this kind \
of question for the first time. Respond with ONLY a single fenced ```json code block shaped exactly \
like: {{"prompt": "...", "explanation": "...", "hint": "..."}}"""

_PRACTICE_REWRITE_JSON_RE = re.compile(r'```json\s*(\{.*?\})\s*```', re.DOTALL)


async def rewrite_practice_question(
    *, qtype: str, source_code: str, graph_summary: str, prompt: str, choices: list[str],
    answer: str, explanation: str, hint: str,
) -> dict:
    """Ask the LLM to rewrite a template-generated practice question's prompt/explanation/hint
    into more natural, pedagogically richer prose — grounded in the real graph and the exact
    choices/answer already computed by core.practice_service's deterministic generators, which
    this never touches: the LLM can make the question read better, never change what's correct.
    Returns the original {prompt, choices, answer, explanation, hint} dict unchanged if the LLM
    call fails or returns something unusable — callers always get a valid question either way.
    """
    original = {'prompt': prompt, 'choices': choices, 'answer': answer, 'explanation': explanation, 'hint': hint}
    style_desc = _PRACTICE_QUESTION_STYLE_DESC.get(qtype, 'a control-flow-graph practice question')
    system_prompt = _PRACTICE_REWRITE_SYSTEM_PROMPT.format(style_desc=style_desc)
    user_content = (
        f'Source code:\n```\n{source_code}\n```\n'
        f'Graph structure: {graph_summary}\n'
        f'Current prompt: {prompt}\n'
        f'Choices (fixed, do not change): {choices}\n'
        f'Correct answer (fixed, do not change): {answer}\n'
        f'Current explanation: {explanation}\n'
        f'Current hint: {hint}\n'
        'Rewrite the prompt, explanation, and hint now.'
    )
    try:
        reply = await _call_llm([
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_content},
        ])
    except AIServiceError:
        return original

    match = _PRACTICE_REWRITE_JSON_RE.search(reply)
    if not match:
        return original
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return original
    if not isinstance(data, dict):
        return original

    new_prompt = data.get('prompt')
    new_explanation = data.get('explanation')
    new_hint = data.get('hint')
    if not (isinstance(new_prompt, str) and new_prompt.strip()):
        return original
    return {
        'prompt': new_prompt.strip(),
        'choices': choices,
        'answer': answer,
        'explanation': new_explanation.strip() if isinstance(new_explanation, str) and new_explanation.strip() else explanation,
        'hint': new_hint.strip() if isinstance(new_hint, str) and new_hint.strip() else hint,
    }


_JSON_BLOCK_RE = re.compile(r'```json\s*(\[.*?\])\s*```', re.DOTALL)
_BARE_ARRAY_RE = re.compile(r'(\[\s*\{.*?\}\s*\])', re.DOTALL)


def parse_questions(reply_text: str) -> list[dict]:
    """Extract well-formed question dicts from an LLM reply, if any are present."""
    match = _JSON_BLOCK_RE.search(reply_text) or _BARE_ARRAY_RE.search(reply_text)
    if not match:
        return []
    try:
        candidates = json.loads(match.group(1))
    except json.JSONDecodeError:
        return []

    questions = []
    for c in candidates if isinstance(candidates, list) else []:
        if not isinstance(c, dict):
            continue
        prompt = c.get('prompt')
        choices = c.get('choices')
        answer = c.get('answer')
        if not (isinstance(prompt, str) and prompt.strip()):
            continue
        if not (isinstance(choices, list) and len(choices) >= 2):
            continue
        if answer not in choices:
            continue
        questions.append({
            'prompt': prompt.strip(),
            'choices': [str(x) for x in choices],
            'answer': str(answer),
            'explanation': str(c.get('explanation', '')).strip(),
        })
    return questions
