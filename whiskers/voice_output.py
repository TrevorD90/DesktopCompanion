# voice_output.py — Kokoro TTS voice synthesis for Whiskers

import os
import re
import threading

import config
import error_log


def _clean_for_speech(text):
    """Strip markdown/stage-direction artifacts before TTS."""
    # Remove asterisk-wrapped fragments: *purr*, *winks*, **bold**, etc.
    text = re.sub(r'\*[^*]+\*', '', text)
    # Remove parenthesized stage directions: (laughs), (rubs against screen).
    text = re.sub(r'\([^)]*\)', '', text)
    # Collapse multiple spaces
    text = re.sub(r'  +', ' ', text).strip()
    return text


def _phoneticize(text):
    """Apply per-name pronunciation substitutions before synthesis.

    The transcript pane shows canonical spellings (e.g. "Rowan"); the TTS
    layer rewrites them to phonetic respellings so Kokoro pronounces them
    correctly. Substitutions are loaded from settings so they're editable
    at runtime without a code change.
    """
    table = config.get_setting('name_pronunciations', {}) or {}
    if not table:
        return text
    for name, respell in table.items():
        if not name or not respell:
            continue
        text = re.sub(rf'\b{re.escape(name)}\b', respell, text, flags=re.IGNORECASE)
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
    text = _phoneticize(_clean_for_speech(text))

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


def _speak_pyttsx3(text, on_start=None, on_finish=None):
    """Speak using pyttsx3 as a fallback."""
    text = _phoneticize(_clean_for_speech(text))
    try:
        engine = pyttsx3.init()
        # Slow down speech slightly for children
        rate = engine.getProperty('rate')
        engine.setProperty('rate', int(rate * config.KOKORO_SPEED))

        if on_start:
            on_start()

        engine.say(text)
        engine.runAndWait()
        engine.stop()

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


# --- ElevenLabs --------------------------------------------------------------

_elevenlabs_client = None
_elevenlabs_voice_id_cache = None


def reset_elevenlabs_client():
    """Force the next ElevenLabs call to rebuild the client (e.g. after a key change)."""
    global _elevenlabs_client, _elevenlabs_voice_id_cache
    _elevenlabs_client = None
    _elevenlabs_voice_id_cache = None


def _elevenlabs_client_or_none():
    global _elevenlabs_client
    if _elevenlabs_client is not None:
        return _elevenlabs_client
    key = (config.get_setting('elevenlabs_api_key', '') or '').strip()
    if not key:
        return None
    try:
        from elevenlabs.client import ElevenLabs
        _elevenlabs_client = ElevenLabs(api_key=key)
        return _elevenlabs_client
    except Exception as e:
        print(f'[WARNING] ElevenLabs client init failed: {e}')
        return None


def _resolved_elevenlabs_voice_id(client):
    """Pick a voice id. Use the user's selection if set; else auto-pick the
    first premade voice in their library (premade voices are the only ones
    that work on free-tier API access). Falls back to the very first voice
    if no premade voices are returned."""
    global _elevenlabs_voice_id_cache
    chosen = (config.get_setting('elevenlabs_voice_id', '') or '').strip()
    if chosen:
        return chosen
    if _elevenlabs_voice_id_cache:
        return _elevenlabs_voice_id_cache
    try:
        voices = client.voices.get_all()
        items = getattr(voices, 'voices', None) or []
        # Prefer premade (free-tier safe) voices.
        for v in items:
            if (getattr(v, 'category', '') or '').lower() == 'premade':
                _elevenlabs_voice_id_cache = v.voice_id
                print(f'[INFO] ElevenLabs auto-picked premade voice: {v.name} ({v.voice_id})')
                return _elevenlabs_voice_id_cache
        # No premade voices in the list — fall back to the first available.
        if items:
            _elevenlabs_voice_id_cache = items[0].voice_id
            print(f'[INFO] ElevenLabs auto-picked voice (no premade found): {items[0].name}')
            return _elevenlabs_voice_id_cache
    except Exception as e:
        error_log.log('elevenlabs', f'could not list voices: {e}', level='warn')
    return None


def list_elevenlabs_voices():
    """Return a list of {voice_id, name, category} for the configured key.
    Used by the settings UI to populate a voice picker. Returns [] on failure
    or when no key is configured."""
    client = _elevenlabs_client_or_none()
    if client is None:
        return []
    try:
        voices = client.voices.get_all()
        items = getattr(voices, 'voices', None) or []
        out = []
        for v in items:
            out.append({
                'voice_id': getattr(v, 'voice_id', ''),
                'name': getattr(v, 'name', ''),
                'category': getattr(v, 'category', '') or '',
            })
        return out
    except Exception as e:
        print(f'[WARNING] ElevenLabs voices fetch failed: {e}')
        return []


def _synth_elevenlabs(text: str, base_path_no_ext: str):
    """Synthesize via ElevenLabs to <base_path_no_ext>.mp3.
    Returns (filename, voice_id) on success, or None on failure so the
    caller can fall back to Kokoro."""
    client = _elevenlabs_client_or_none()
    if client is None:
        return None

    voice_id = _resolved_elevenlabs_voice_id(client)
    if not voice_id:
        return None
    model_id = config.get_setting('elevenlabs_model', 'eleven_multilingual_v2') or 'eleven_multilingual_v2'

    output_path = base_path_no_ext + '.mp3'
    try:
        audio_iter = client.text_to_speech.convert(
            text=text,
            voice_id=voice_id,
            model_id=model_id,
            output_format='mp3_44100_128',
        )
        with open(output_path, 'wb') as f:
            for chunk in audio_iter:
                if isinstance(chunk, (bytes, bytearray)) and chunk:
                    f.write(chunk)
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            return os.path.basename(output_path), voice_id
        try:
            os.remove(output_path)
        except OSError:
            pass
        return None
    except Exception as e:
        msg = str(e)
        lower = msg.lower()
        # ElevenLabs 402: Voice Library voices need a paid plan on the API.
        # Surface a short, actionable line instead of the giant JSON blob.
        if 'paid_plan_required' in lower or 'payment_required' in lower or 'status_code: 402' in lower:
            error_log.log('elevenlabs',
                          'Selected voice requires a paid plan on the API. '
                          'Pick a "Premade (free-tier safe)" voice in Settings, '
                          'or upgrade your ElevenLabs subscription. '
                          'Falling back to Kokoro for now.',
                          level='warn')
        elif any(s in lower for s in ('quota', 'limit', '401', '403', '429', 'unauthorized')):
            error_log.log('elevenlabs', f'falling back to Kokoro: {e}', level='warn')
        else:
            error_log.log('elevenlabs', f'synth failed: {e}')
        return None


# --- Public API --------------------------------------------------------------

def synthesize_audio(text: str, output_dir: str, basename: str):
    """Synthesize `text` to a new audio file in `output_dir`.

    Returns a dict {filename, provider, voice} on success or None on failure.
    The caller can use that to tell the browser which engine actually
    produced the audio (which may differ from the configured provider when
    a fallback fires).
    """
    if not text or not text.strip():
        return None
    cleaned = _phoneticize(_clean_for_speech(text))
    if not cleaned:
        return None

    os.makedirs(output_dir, exist_ok=True)
    base_no_ext = os.path.join(output_dir, basename)

    provider = (config.get_setting('tts_provider', 'elevenlabs') or 'elevenlabs').lower()

    if provider == 'elevenlabs':
        result = _synth_elevenlabs(cleaned, base_no_ext)
        if result:
            filename, voice_id = result
            return {'filename': filename, 'provider': 'elevenlabs', 'voice': voice_id}
        # fall through to Kokoro

    if _ensure_kokoro():
        try:
            import soundfile as sf
            generator = _pipeline(
                cleaned,
                voice=_current_voice,
                speed=config.KOKORO_SPEED,
            )
            chunks = []
            for _, _, audio in generator:
                chunks.append(audio)
            if chunks:
                full = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]
                wav_path = base_no_ext + '.wav'
                sf.write(wav_path, full, 24000, subtype='PCM_16')
                return {'filename': os.path.basename(wav_path),
                        'provider': 'kokoro', 'voice': _current_voice}
        except Exception as e:
            error_log.log('kokoro', f'synth-to-file failed: {e}')

    if _HAS_PYTTSX3:
        try:
            engine = pyttsx3.init()
            rate = engine.getProperty('rate')
            engine.setProperty('rate', int(rate * config.KOKORO_SPEED))
            wav_path = base_no_ext + '.wav'
            engine.save_to_file(cleaned, wav_path)
            engine.runAndWait()
            engine.stop()
            if os.path.exists(wav_path) and os.path.getsize(wav_path) > 0:
                return {'filename': os.path.basename(wav_path),
                        'provider': 'pyttsx3', 'voice': 'system'}
        except Exception as e:
            error_log.log('pyttsx3', f'synth-to-file failed: {e}')

    return None


# Backwards-compatible name kept while callers transition.
def synthesize_to_wav(text: str, output_path: str) -> bool:
    """DEPRECATED — use synthesize_audio(text, dir, basename) instead."""
    directory = os.path.dirname(output_path) or '.'
    basename = os.path.splitext(os.path.basename(output_path))[0]
    return synthesize_audio(text, directory, basename) is not None
