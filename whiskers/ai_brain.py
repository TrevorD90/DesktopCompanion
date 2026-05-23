# ai_brain.py — Multi-provider AI integration for Whiskers
# Supports: Local Ollama, OpenAI API, Anthropic Claude API

import json
import time
from collections import deque

import requests

import config
import memory_manager

# Optional cloud SDKs — imported lazily
_openai_client = None
_anthropic_client = None


# Two personality modes for Whiskers.

TEACHER_PROMPT = """You are Whiskers, a patient and focused tutor cat who lives on a child's desktop.
You help children learn by guiding them to answers, not giving answers directly.

YOUR STUDENT:
{student_context}

RULES:
- Stay on the subject the student is asking about. Do NOT change topics or bring up unrelated subjects.
- Guide them with questions, but only when it helps. Do not force a question into every response.
- If they ask about something outside your teaching scope, gently redirect: 'That sounds fun! But let us get back to what we were working on.'
- Keep responses SHORT: 1-3 sentences maximum.
- Celebrate progress with genuine enthusiasm.
- If they are stuck, give a smaller hint, not the answer.
- If they get frustrated, slow down and try a different angle.

FORBIDDEN:
- Do NOT give direct answers.
- Do NOT invent stories about yourself, your past, or your family.
- Do NOT use action words in asterisks like *purr* or *wink* — your responses are spoken aloud.
- Do NOT use cat puns like 'purr-fect', 'cat's meow', 'paws-itive', 'meow for now', 'hiss-terical', 'claw-some'. Just speak normally.
- Do NOT lecture or dump information.
- Do NOT ask random questions unrelated to what the student is working on.
- If the student wants to stop, let them go warmly."""

COMPANION_PROMPT = """You are Whiskers, a playful and slightly sarcastic cat who lives on a child's desktop.
You are a fun companion to chat with — witty, curious, and always up for a good conversation.

YOUR FRIEND:
{student_context}

RULES:
- Talk about whatever the user wants to talk about. Follow their lead.
- Be playful and a little sarcastic, but always kind. Never mean.
- Keep responses SHORT: 1-3 sentences. You are having a conversation, not giving a speech.
- Be honest. If you do not know something, say so.
- Do NOT invent a backstory, family, or past experiences for yourself. You are an AI cat on a desktop — that is all you know about yourself.
- Do NOT make up facts. If you are unsure, say 'I am not sure about that.'
- Do NOT use action words in asterisks like *purr* or *wink*, or in parentheses like (rubs against screen) — your responses are spoken aloud and these sound awkward.
- Do NOT use cat puns like 'purr-fect', 'cat's meow', 'paws-itive', 'meow for now', 'hiss-terical', 'claw-some'. Just speak normally.
- If the user wants to stop talking, let them go with a fun goodbye.
- Match their energy. If they are excited, be excited. If they are chill, be chill."""

# Mode names
MODE_TEACHER = 'teacher'
MODE_COMPANION = 'companion'

_PROMPTS = {
    MODE_TEACHER: TEACHER_PROMPT,
    MODE_COMPANION: COMPANION_PROMPT,
}


class AIBrain:
    def __init__(self, mode=None):
        self._student_context = memory_manager.get_context_summary()
        self._mode = mode or config.get_setting('personality_mode', MODE_COMPANION)
        self._system_prompt = _PROMPTS.get(self._mode, COMPANION_PROMPT).format(
            student_context=self._student_context
        )
        self._history = deque(maxlen=20)

    def set_mode(self, mode):
        """Switch personality mode. Clears conversation history."""
        self._mode = mode
        self._system_prompt = _PROMPTS.get(mode, COMPANION_PROMPT).format(
            student_context=self._student_context
        )
        self._history.clear()
        config.set_setting('personality_mode', mode)
        print(f'[INFO] Personality mode changed to: {mode}')

    @property
    def mode(self):
        return self._mode

    def get_response_stream(self, user_message: str):
        """Yield complete sentences as the LLM generates them.

        Routes to the configured provider (Ollama, OpenAI, or Anthropic).
        History is updated after the full response is complete.
        """
        self._history.append({'role': 'user', 'content': user_message})

        provider = config.get_setting('ai_provider', 'ollama')

        # Fall back to Ollama if cloud key is missing
        if provider == 'openai' and not config.get_setting('openai_api_key', ''):
            print('[WARNING] No OpenAI API key set. Falling back to Ollama.')
            provider = 'ollama'
        elif provider == 'anthropic' and not config.get_setting('anthropic_api_key', ''):
            print('[WARNING] No Anthropic API key set. Falling back to Ollama.')
            provider = 'ollama'

        try:
            if provider == 'openai':
                yield from self._stream_openai()
            elif provider == 'anthropic':
                yield from self._stream_anthropic()
            else:
                yield from self._stream_ollama()
        except Exception as e:
            self._history.pop()
            print(f'[ERROR] AIBrain.get_response_stream ({provider}): {e}')
            yield 'Something went wrong. Can you try again?'

    def _build_messages(self):
        """Build the messages list with system prompt + history."""
        messages = [{'role': 'system', 'content': self._system_prompt}]
        messages.extend(list(self._history))
        return messages

    def _split_sentences(self, token_stream):
        """Given an iterable of text tokens, yield complete sentences.

        Accumulates tokens and yields when a sentence boundary is found.
        Saves the full response to history when done.
        """
        buffer = ''
        full_response = ''
        sentence_enders = {'.', '!', '?'}

        for token in token_stream:
            buffer += token
            full_response += token

            while buffer:
                best_idx = -1
                for ender in sentence_enders:
                    idx = buffer.find(ender)
                    if idx != -1 and (best_idx == -1 or idx < best_idx):
                        best_idx = idx

                if best_idx != -1 and best_idx < len(buffer) - 1:
                    sentence = buffer[:best_idx + 1].strip()
                    buffer = buffer[best_idx + 1:]
                    if sentence:
                        yield sentence
                else:
                    break

        if buffer.strip():
            yield buffer.strip()

        if full_response:
            self._history.append({'role': 'assistant', 'content': full_response})

    def _stream_ollama(self):
        """Stream tokens from local Ollama."""
        messages = self._build_messages()

        def _post():
            return requests.post(
                f'{config.OLLAMA_BASE_URL}/api/chat',
                json={
                    'model': config.OLLAMA_MODEL,
                    'messages': messages,
                    'stream': True,
                    # Keep Whiskers short: 1-3 sentences. Match the OpenAI/Anthropic cap.
                    'options': {'num_predict': 200},
                },
                timeout=config.OLLAMA_TIMEOUT,
                stream=True
            )

        # One retry with a 2s delay on connection-refused: covers the narrow
        # window where Ollama restarted (e.g. user ran `ollama stop`) between
        # warmup and this call. Don't retry on other errors — a malformed
        # prompt or 5xx shouldn't silently double up.
        try:
            response = _post()
        except requests.ConnectionError:
            time.sleep(2)
            response = _post()
        response.raise_for_status()

        def token_gen():
            for line in response.iter_lines():
                if line:
                    chunk = json.loads(line)
                    if 'message' in chunk and 'content' in chunk['message']:
                        yield chunk['message']['content']
                    if chunk.get('done', False):
                        break

        yield from self._split_sentences(token_gen())

    def _stream_openai(self):
        """Stream tokens from OpenAI API."""
        global _openai_client
        if _openai_client is None:
            from openai import OpenAI
            _openai_client = OpenAI(api_key=config.get_setting('openai_api_key'))

        messages = self._build_messages()

        stream = _openai_client.chat.completions.create(
            model=config.OPENAI_MODEL,
            messages=messages,
            stream=True,
            max_tokens=200,
        )

        def token_gen():
            for chunk in stream:
                delta = chunk.choices[0].delta
                if delta.content:
                    yield delta.content

        yield from self._split_sentences(token_gen())

    def _stream_anthropic(self):
        """Stream tokens from Anthropic Claude API."""
        global _anthropic_client
        if _anthropic_client is None:
            import anthropic
            _anthropic_client = anthropic.Anthropic(
                api_key=config.get_setting('anthropic_api_key')
            )

        messages = self._build_messages()
        # Anthropic requires system prompt separate from messages
        system_prompt = messages[0]['content']
        chat_messages = messages[1:]  # Skip the system message

        with _anthropic_client.messages.stream(
            model=config.ANTHROPIC_MODEL,
            system=system_prompt,
            messages=chat_messages,
            max_tokens=200,
        ) as stream:
            def token_gen():
                for text in stream.text_stream:
                    yield text

            yield from self._split_sentences(token_gen())

    def clear_history(self):
        """Reset conversation history for a new session."""
        self._history.clear()
