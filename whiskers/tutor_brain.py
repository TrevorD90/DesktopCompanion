"""Anthropic-backed tutor with vision attachment, sentence streaming,
and a post-response no-answer guardrail.
"""

import base64
import re
from typing import Iterator, List, Optional, Tuple

import config
import prompts
from coursework_loader import Problem
from tutor_session import TutorSession


_anthropic_client = None


def _client():
    global _anthropic_client
    if _anthropic_client is None:
        import anthropic
        api_key = config.get_setting('anthropic_api_key', '')
        if not api_key:
            raise RuntimeError('No Anthropic API key set. Add one in Settings.')
        _anthropic_client = anthropic.Anthropic(api_key=api_key)
    return _anthropic_client


def reset_client():
    """Force the next API call to rebuild the client (e.g. after key change)."""
    global _anthropic_client
    _anthropic_client = None


_ANSWER_PATTERNS = [
    re.compile(r"\bthe answer is\b", re.IGNORECASE),
    re.compile(r"\bequals?\s+-?\d", re.IGNORECASE),
    re.compile(r"=\s*-?\d+\.?\d*\s*[.!]?\s*$"),
    re.compile(r"\bit'?s\s+-?\d+(\.\d+)?\s*[.!]?\s*$", re.IGNORECASE),
]

_NUMERIC_PROBLEM_RE = re.compile(r"[\d+\-*/×÷=]")


def _looks_like_an_answer(response_text: str, problem_text: str) -> bool:
    if not problem_text or not _NUMERIC_PROBLEM_RE.search(problem_text):
        return False
    return any(p.search(response_text) for p in _ANSWER_PATTERNS)


def _build_user_content(text: str, problem: Optional[Problem],
                       attach_image: bool):
    """Build the user message content. Either a plain string or a list
    of content blocks (when attaching a vision image)."""
    if problem and attach_image and problem.has_image:
        with open(problem.image_path, 'rb') as f:
            b64 = base64.standard_b64encode(f.read()).decode('ascii')
        return [
            {
                'type': 'image',
                'source': {
                    'type': 'base64',
                    'media_type': problem.image_media_type or 'image/png',
                    'data': b64,
                },
            },
            {'type': 'text', 'text': text or 'Here is the worksheet.'},
        ]
    return text


def _split_sentences(token_stream: Iterator[str]) -> Iterator[Tuple[str, str]]:
    """Yield (sentence, full_text_so_far) for each completed sentence.

    The caller can read the final full_text from the last yielded tuple,
    or accumulate it themselves.
    """
    buffer = ''
    full = ''
    enders = {'.', '!', '?'}
    for token in token_stream:
        buffer += token
        full += token
        while buffer:
            best = -1
            for e in enders:
                i = buffer.find(e)
                if i != -1 and (best == -1 or i < best):
                    best = i
            if best != -1 and best < len(buffer) - 1:
                sentence = buffer[:best + 1].strip()
                buffer = buffer[best + 1:]
                if sentence:
                    yield (sentence, full)
            else:
                break
    if buffer.strip():
        yield (buffer.strip(), full)


def stream_turn(session: TutorSession, user_text: str) -> Iterator[str]:
    """Stream a tutor response sentence-by-sentence for the given user turn."""
    action, payload = session.handle_utterance(user_text)

    if action == 'consumed_navigation':
        session.append_user(payload)
        yield from _stream_with_guardrail(session)
        return

    if action == 'speaker_switched':
        remainder = (payload or '').strip()
        if not remainder:
            # No payload — synthesize a tiny local acknowledgement; no API call.
            speaker = session.current_speaker
            if speaker == 'Rowan':
                ack = "Welcome back, Rowan! Ready to keep going?"
            else:
                ack = f"Hey {speaker}! What's up?"
            session.append_assistant(ack)
            yield ack
            return
        session.append_user(remainder)
        yield from _stream_with_guardrail(session)
        return

    # pass_through
    problem = session.current_problem()
    needs_image = (
        problem is not None
        and problem.has_image
        and session.last_attached_problem_id != problem.id
    )
    user_content = _build_user_content(user_text, problem, attach_image=needs_image)
    session.append_user(user_content)
    if needs_image and problem is not None:
        session.mark_problem_attached(problem.id)
    yield from _stream_with_guardrail(session)


def _build_system_prompt_for_session(session: TutorSession) -> str:
    problem = session.current_problem()
    return prompts.build_system_prompt(
        speaker=session.current_speaker,
        problem_index=session._current_index,
        problem_count=len(session._problems),
        problem_text=(problem.text if problem else None),
        has_image=(problem.has_image if problem else False),
    )


def _stream_with_guardrail(session: TutorSession) -> Iterator[str]:
    """Stream a response, then check the no-answer guardrail. If it fires,
    pop the assistant turn and regenerate once with a stronger reminder."""
    system_prompt = _build_system_prompt_for_session(session)
    sentences, full = _call_and_stream(system_prompt, session.history_list())

    # Commit assistant turn first so streaming order is correct in transcript.
    session.append_assistant(full)
    for s in sentences:
        yield s

    speaker = session.current_speaker
    problem = session.current_problem()
    if speaker != 'Rowan' or not problem:
        return
    if not _looks_like_an_answer(full, problem.text or ''):
        return

    print('[GUARDRAIL] regenerated — draft contained an answer')
    # Discard the offending assistant turn, retry with reminder appended.
    session.pop_last()
    retry_system = system_prompt + '\n\n' + prompts.REGENERATE_REMINDER
    retry_sentences, retry_full = _call_and_stream(retry_system, session.history_list())

    if _looks_like_an_answer(retry_full, problem.text or ''):
        # Retry also failed — fall back to a canned safe response.
        session.append_assistant(prompts.GUARDRAIL_FALLBACK)
        yield prompts.GUARDRAIL_FALLBACK
        return

    session.append_assistant(retry_full)
    for s in retry_sentences:
        yield s


def _call_and_stream(system_prompt: str, history: list) -> Tuple[List[str], str]:
    """Make one Anthropic streaming call. Returns (sentences, full_text).

    Buffers sentences fully before returning — the SSE layer needs the
    sentence list so it can emit each as an event and synthesize TTS in
    order. Latency is acceptable because Anthropic streaming is fast
    relative to TTS synthesis (which dominates).
    """
    client = _client()
    sentences: List[str] = []
    full = ''
    with client.messages.stream(
        model=config.ANTHROPIC_MODEL,
        system=system_prompt,
        messages=history,
        max_tokens=300,
    ) as stream:
        def token_gen():
            for text in stream.text_stream:
                yield text

        for sentence, full_so_far in _split_sentences(token_gen()):
            sentences.append(sentence)
            full = full_so_far
    return sentences, full
