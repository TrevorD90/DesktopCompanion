"""Session state for the Socratic tutor.

Owns:
- The list of Problems from the most recent coursework upload.
- The current problem index.
- The current speaker (default: Rowan).
- Speaker auto-revert timer (back to Rowan after 60s of silence).
- A pre-filter that intercepts navigation and self-identification utterances
  before they reach Claude.
"""

import re
import threading
import time
from collections import deque
from typing import List, Optional, Tuple

from coursework_loader import Problem
from event_bus import bus
import config


DEFAULT_SPEAKER = 'Rowan'
BROTHER_AUTO_REVERT_SECONDS = 60


# --- Pre-filter regexes ---------------------------------------------------

# Problem navigation
_NEXT_RE     = re.compile(r"^\s*(?:go to |move to )?(?:next|the next)(?: problem)?\s*[.!?]?\s*$", re.IGNORECASE)
_PREV_RE     = re.compile(r"^\s*(?:go back|previous|prior)(?: problem)?\s*[.!?]?\s*$", re.IGNORECASE)
_JUMP_RE     = re.compile(r"^\s*(?:i'?m on|i am on|go to|jump to|problem|number)\s+(?:problem\s+|number\s+)?(\d+)\s*[.!?]?\s*$", re.IGNORECASE)

# Speaker self-identification — anchored to the start to minimize false positives.
# Allows an optional "me " filler between the verb and the name to catch
# "It's me Liam" alongside "It's Liam" / "I'm Liam" / "This is Liam".
_SELFID_GREETING_RE = re.compile(
    r"^\s*(?:hi|hey|hello)[,!]?\s*(?:it'?s|this is|i'?m|i am)\s+(?:me\s+)?(?P<name>[a-zA-Z]+)\b",
    re.IGNORECASE,
)
_SELFID_PLAIN_RE = re.compile(
    r"^\s*(?:it'?s|this is|i'?m|i am)\s+(?:me\s+)?(?P<name>[a-zA-Z]+)\b",
    re.IGNORECASE,
)
_SELFID_TRAILING_RE = re.compile(
    r"^\s*(?P<name>[a-zA-Z]+)\s+(?:here|speaking)\b",
    re.IGNORECASE,
)
# Special-case: "it's me" with no name claims Rowan back (only meaningful when
# current speaker isn't already Rowan).
_SELFID_ITSME_RE = re.compile(r"^\s*it'?s me\b\s*[.!?]?\s*$", re.IGNORECASE)


def _strip_selfid_prefix(utterance: str) -> str:
    """Remove the matched self-id phrase so the remainder is sent to Claude."""
    for pat in (_SELFID_GREETING_RE, _SELFID_PLAIN_RE, _SELFID_TRAILING_RE):
        m = pat.match(utterance)
        if m:
            return utterance[m.end():].lstrip(' ,.;:!?-').strip()
    return ''


# --- Session --------------------------------------------------------------

class TutorSession:
    def __init__(self):
        self._lock = threading.Lock()
        self._problems: List[Problem] = []
        self._current_index: Optional[int] = None
        self._last_attached_problem_id: Optional[str] = None

        self._current_speaker: str = DEFAULT_SPEAKER
        # When current_speaker is not Rowan, this records the last utterance
        # time so an auto-revert thread can fire after 60s of silence.
        self._last_brother_activity: float = 0.0
        self._auto_revert_thread: Optional[threading.Thread] = None
        self._auto_revert_stop = threading.Event()

        # Continuous conversation mode: when False, the mic does NOT auto
        # re-arm after the tutor finishes speaking. Toggled by quiet-words
        # ("bye", "stop", etc.) or by an explicit pause from the UI.
        self._paused: bool = False

        # Conversation history: list of {"role": ..., "content": ...} dicts.
        # content may be a string OR a list of content blocks (for vision).
        self._history = deque(maxlen=20)

    # --- Problems ---------------------------------------------------------

    def set_problems(self, problems: List[Problem]):
        with self._lock:
            self._problems = list(problems)
            self._current_index = 0 if self._problems else None
            self._last_attached_problem_id = None
            self._history.clear()
        self._emit_problem_changed()

    def problem_list(self):
        with self._lock:
            return {
                'problems': [p.to_dict() for p in self._problems],
                'current_index': self._current_index,
            }

    def current_problem(self) -> Optional[Problem]:
        with self._lock:
            if self._current_index is None or not self._problems:
                return None
            return self._problems[self._current_index]

    def set_current_index(self, index: int) -> bool:
        with self._lock:
            if not self._problems:
                return False
            new_index = max(0, min(index, len(self._problems) - 1))
            if new_index == self._current_index:
                return False
            self._current_index = new_index
        self._emit_problem_changed()
        return True

    def _emit_problem_changed(self):
        info = self.problem_list()
        bus.publish('problem_changed', **info)

    # --- Speaker ----------------------------------------------------------

    @property
    def current_speaker(self) -> str:
        return self._current_speaker

    def known_speakers(self) -> List[str]:
        speakers = config.get_setting('known_speakers', [DEFAULT_SPEAKER, 'Liam']) or [DEFAULT_SPEAKER]
        # Always include Rowan first
        out = [DEFAULT_SPEAKER]
        for s in speakers:
            if s and s != DEFAULT_SPEAKER and s not in out:
                out.append(s)
        return out

    def _canonical_speaker_name(self, raw_name: str) -> Optional[str]:
        """Case-insensitive match against known_speakers. Returns canonical
        spelling or None if not a known speaker."""
        if not raw_name:
            return None
        lower = raw_name.lower()
        for known in self.known_speakers():
            if known.lower() == lower:
                return known
        return None

    def set_current_speaker(self, name: str) -> bool:
        canonical = self._canonical_speaker_name(name)
        if not canonical:
            return False
        if canonical == self._current_speaker:
            return False
        self._current_speaker = canonical
        if canonical == DEFAULT_SPEAKER:
            self._stop_auto_revert()
        else:
            self._start_auto_revert()
        bus.publish('speaker_changed', speaker=canonical, known=self.known_speakers())
        return True

    def _note_brother_activity(self):
        if self._current_speaker != DEFAULT_SPEAKER:
            self._last_brother_activity = time.time()

    def _start_auto_revert(self):
        self._stop_auto_revert()
        self._last_brother_activity = time.time()
        self._auto_revert_stop = threading.Event()

        def loop():
            while not self._auto_revert_stop.is_set():
                if self._current_speaker == DEFAULT_SPEAKER:
                    return
                if time.time() - self._last_brother_activity >= BROTHER_AUTO_REVERT_SECONDS:
                    self.set_current_speaker(DEFAULT_SPEAKER)
                    return
                self._auto_revert_stop.wait(2.0)

        self._auto_revert_thread = threading.Thread(target=loop, daemon=True)
        self._auto_revert_thread.start()

    def _stop_auto_revert(self):
        if self._auto_revert_stop is not None:
            self._auto_revert_stop.set()

    # --- Utterance pre-filter --------------------------------------------

    def handle_utterance(self, utterance: str) -> Tuple[str, Optional[str]]:
        """Run pre-filters on the raw utterance.

        Returns (action, payload) where action is one of:
          - 'consumed_navigation' — utterance was a problem-nav command;
            payload is a short synthetic user message to inject into history
            (e.g. "[Rowan is now on problem 3. Help him with this one.]").
          - 'speaker_switched' — utterance was a self-id; payload is the
            remaining utterance (possibly empty) to send to Claude as a
            normal turn.
          - 'pass_through' — utterance is normal; payload is None and the
            caller should send the original utterance to Claude.
        """
        text = utterance.strip()
        if not text:
            return ('pass_through', None)

        # 1) Speaker self-id. Check first because "I'm Liam" should always
        # trigger speaker switch even if the rest of the sentence happens to
        # look like a nav command.
        m = _SELFID_GREETING_RE.match(text) or _SELFID_PLAIN_RE.match(text) or _SELFID_TRAILING_RE.match(text)
        if m:
            claimed = m.group('name')
            canonical = self._canonical_speaker_name(claimed)
            if canonical:
                self.set_current_speaker(canonical)
                remainder = _strip_selfid_prefix(text)
                return ('speaker_switched', remainder)
            # Unknown name — ignore, let it pass through to Claude as normal content.

        if _SELFID_ITSME_RE.match(text) and self._current_speaker != DEFAULT_SPEAKER:
            self.set_current_speaker(DEFAULT_SPEAKER)
            return ('speaker_switched', '')

        # 2) Problem navigation
        if self._problems and self._current_index is not None:
            if _NEXT_RE.match(text):
                changed = self.set_current_index(self._current_index + 1)
                msg = (f"[Rowan is now on problem {self._current_index + 1}. Help him with this one.]"
                       if changed else f"[Rowan is already on the last problem.]")
                return ('consumed_navigation', msg)
            if _PREV_RE.match(text):
                changed = self.set_current_index(self._current_index - 1)
                msg = (f"[Rowan is now on problem {self._current_index + 1}. Help him with this one.]"
                       if changed else f"[Rowan is already on the first problem.]")
                return ('consumed_navigation', msg)
            jm = _JUMP_RE.match(text)
            if jm:
                target = int(jm.group(1)) - 1
                changed = self.set_current_index(target)
                msg = (f"[Rowan is now on problem {self._current_index + 1}. Help him with this one.]"
                       if changed else f"[Rowan is already on problem {self._current_index + 1}.]")
                return ('consumed_navigation', msg)

        # Track activity for auto-revert
        self._note_brother_activity()
        return ('pass_through', None)

    # --- History ---------------------------------------------------------

    def append_user(self, content):
        self._history.append({'role': 'user', 'content': content})

    def append_assistant(self, text: str):
        if text:
            self._history.append({'role': 'assistant', 'content': text})

    def pop_last(self):
        if self._history:
            self._history.pop()

    def history_list(self):
        return list(self._history)

    def mark_problem_attached(self, problem_id: Optional[str]):
        self._last_attached_problem_id = problem_id

    @property
    def last_attached_problem_id(self):
        return self._last_attached_problem_id

    def clear(self):
        self._history.clear()

    # --- Pause state ------------------------------------------------------

    @property
    def paused(self) -> bool:
        return self._paused

    def set_paused(self, paused: bool):
        self._paused = bool(paused)

    @staticmethod
    def is_quiet_word(utterance: str) -> bool:
        """Match against the user's quiet-words list (lowercase, substring)."""
        if not utterance:
            return False
        text = utterance.strip().lower().rstrip('.!?')
        defaults = ['bye', 'goodbye', "that's all", 'see you later', 'see ya',
                    "i'm done", 'stop listening', 'stop', 'pause', 'quiet']
        words = config.get_setting('quiet_words', defaults) or defaults
        for w in words:
            wl = (w or '').strip().lower()
            if not wl:
                continue
            # Match if the utterance IS the quiet word, starts with it, or
            # ends with it. Keeps "stop" from triggering on "stop and think".
            if text == wl or text.startswith(wl + ' ') or text.endswith(' ' + wl):
                return True
        return False


# Singleton — one tutor session per app process.
session = TutorSession()
