# voice_output.py — Kokoro TTS voice synthesis for Whiskers

import re
import threading

import config


def _clean_for_speech(text):
    """Strip action words and asterisk-wrapped expressions before TTS.

    Removes patterns like *purr*, *wink*, *nuzzle* and standalone
    'Purr...' at the start of sentences. Keeps the text readable
    in the bubble but clean for spoken audio.
    """
    # Remove asterisk-wrapped action words: *purr*, *winks*, etc.
    text = re.sub(r'\*[^*]+\*', '', text)
    # Remove parenthesized actions: (rubs against screen), (laughs), etc.
    text = re.sub(r'\([^)]*\)', '', text)
    # Remove standalone Purr.../Meow... at start of sentences
    text = re.sub(r'(?i)\b(purr|meow|mew|hiss)\b\.{0,3}\s*', '', text)
    # Collapse multiple spaces
    text = re.sub(r'  +', ' ', text).strip()
    return text

# Try to load pyttsx3 (fallback TTS) — import at module level so it's always available
_HAS_PYTTSX3 = False
try:
    import pyttsx3
    _HAS_PYTTSX3 = True
except ImportError:
    pass

# Try to load Kokoro TTS (primary) — deferred init to avoid blocking import
_USE_KOKORO = False
_kokoro_available = False
_pipeline = None

try:
    from kokoro import KPipeline
    import sounddevice as sd
    import numpy as np
    _kokoro_available = True
    print('[INFO] Kokoro TTS libraries found. Pipeline will init on first speak().')
except ImportError as e:
    print(f'[WARNING] Kokoro TTS not available ({e}).')
    if _HAS_PYTTSX3:
        print('[INFO] Falling back to pyttsx3.')
    else:
        print('[ERROR] pyttsx3 also not available. Voice output will be disabled.')


def _ensure_kokoro():
    """Lazily initialise the Kokoro pipeline on first use (avoids blocking import)."""
    global _pipeline, _USE_KOKORO
    if _USE_KOKORO:
        return True
    if not _kokoro_available:
        return False
    try:
        _pipeline = KPipeline(lang_code=config.KOKORO_LANG)
        _USE_KOKORO = True
        print('[INFO] Kokoro TTS pipeline initialised.')
        return True
    except Exception as e:
        print(f'[WARNING] Kokoro TTS pipeline init failed: {e}')
        return False


# Flag to allow stopping speech mid-playback
_stop_flag = threading.Event()

# Current voice — mutable at runtime via set_voice()
_current_voice = config.KOKORO_VOICE


def _speak_kokoro(text, on_start=None, on_finish=None):
    """Speak using Kokoro TTS with sounddevice playback."""
    _stop_flag.clear()
    started = False
    text = _clean_for_speech(text)

    if not text or _pipeline is None:
        if on_start:
            on_start()
        if on_finish:
            on_finish()
        return

    try:
        generator = _pipeline(
            text,
            voice=_current_voice,
            speed=config.KOKORO_SPEED
        )

        for _, _, audio in generator:
            if _stop_flag.is_set():
                break
            # Fire on_start just before the first audio chunk plays
            if not started:
                started = True
                if on_start:
                    on_start()
            sd.play(audio, samplerate=24000, blocking=True)

    except Exception as e:
        print(f'[ERROR] Kokoro TTS playback failed: {e}')
    finally:
        if on_finish:
            on_finish()


# Cached pyttsx3 engine — init is 200–500 ms on Windows SAPI5; reuse the
# same engine across every speak() so back-to-back replies don't stall.
_pyttsx3_engine = None


def _get_pyttsx3_engine():
    global _pyttsx3_engine
    if _pyttsx3_engine is None:
        _pyttsx3_engine = pyttsx3.init()
        rate = _pyttsx3_engine.getProperty('rate')
        _pyttsx3_engine.setProperty('rate', int(rate * config.KOKORO_SPEED))
    return _pyttsx3_engine


def _speak_pyttsx3(text, on_start=None, on_finish=None):
    """Speak using pyttsx3 as a fallback."""
    text = _clean_for_speech(text)
    try:
        engine = _get_pyttsx3_engine()

        if on_start:
            on_start()

        engine.say(text)
        engine.runAndWait()

    except Exception as e:
        print(f'[ERROR] pyttsx3 playback failed: {e}')
    finally:
        if on_finish:
            on_finish()


def speak(text: str, on_start=None, on_finish=None):
    """Convert text to speech and play it. Runs in a daemon thread.

    Args:
        text: The text to speak.
        on_start: Callback when audio begins (e.g. trigger TALK animation).
        on_finish: Callback when audio ends (e.g. revert to IDLE animation).
    """
    if _ensure_kokoro():
        target = _speak_kokoro
    elif _HAS_PYTTSX3:
        target = _speak_pyttsx3
    else:
        print(f'[WARNING] No TTS available. Skipping speech: "{text[:60]}..."')
        if on_start:
            on_start()
        if on_finish:
            on_finish()
        return

    thread = threading.Thread(
        target=target,
        args=(text, on_start, on_finish),
        daemon=True
    )
    thread.start()


def speak_sync(text: str, on_start=None):
    """Convert text to speech and play it. Blocks until audio finishes.

    Args:
        text: The text to speak.
        on_start: Optional callback fired just before the first audio chunk plays.
    """
    if not text or not text.strip():
        return
    if _ensure_kokoro():
        _speak_kokoro(text, on_start=on_start)
    elif _HAS_PYTTSX3:
        _speak_pyttsx3(text, on_start=on_start)
    else:
        print(f'[WARNING] No TTS available. Skipping speech: "{text[:60]}..."')


def stop_speaking():
    """Stop any currently playing speech."""
    _stop_flag.set()
    if _USE_KOKORO:
        try:
            sd.stop()
        except Exception:
            pass


def set_voice(voice_name: str):
    """Change the TTS voice at runtime. Takes effect on the next speak() call."""
    global _current_voice
    _current_voice = voice_name
    print(f'[INFO] Voice changed to: {voice_name}')


def get_voice() -> str:
    """Return the currently selected voice name."""
    return _current_voice
