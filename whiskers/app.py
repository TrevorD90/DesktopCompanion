"""Flask app — voice-activated Socratic tutor for Rowan.

Single-user localhost server. Browser talks to this app via JSON POSTs and
subscribes to /events for live updates (transcript, mic status, tutor
sentences, audio URLs, problem/speaker state changes).
"""

import json
import os
import queue
import sys
import threading
import time
import uuid
from typing import Optional

from flask import Flask, Response, jsonify, request, send_from_directory, stream_with_context

import config
import coursework_loader
import error_log
import tutor_brain
import voice_output
from event_bus import bus
from tutor_session import session
from voice_input import VoiceInput


# --- Paths ----------------------------------------------------------------

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(APP_ROOT, 'static')
DATA_DIR = os.path.join(APP_ROOT, 'data')
TTS_DIR = os.path.join(DATA_DIR, 'tts_cache')
UPLOADS_DIR = os.path.join(DATA_DIR, 'uploads')
os.makedirs(TTS_DIR, exist_ok=True)
os.makedirs(UPLOADS_DIR, exist_ok=True)

# Single boot-time session id. Restart = new session = new TTS cache dir.
SESSION_ID = uuid.uuid4().hex[:8]
SESSION_TTS_DIR = os.path.join(TTS_DIR, SESSION_ID)
SESSION_UPLOAD_DIR = os.path.join(UPLOADS_DIR, SESSION_ID)
os.makedirs(SESSION_TTS_DIR, exist_ok=True)
os.makedirs(SESSION_UPLOAD_DIR, exist_ok=True)


# --- App ------------------------------------------------------------------

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path='/static')


# --- Voice input wiring ---------------------------------------------------

_voice = None  # populated in init_voice()


def _on_wake_word():
    bus.publish('mic_status', state='wake_detected')


def _on_recording_start():
    bus.publish('mic_status', state='recording')


def _on_recording_stop():
    bus.publish('mic_status', state='idle')


def _publish_error(source: str, message):
    """Record an error in the ring buffer AND announce it on the event bus.
    Use this anywhere the user should know something went wrong."""
    error_log.log(source, message)
    bus.publish('error', source=source, message=str(message), count=error_log.count())


def _on_speech_transcribed(text: str):
    """Voice in -> tutor pipeline. Runs on the voice_input STT thread."""
    if not text or not text.strip():
        return
    # Tag with who spoke (current session speaker, default Rowan).
    bus.publish('transcript', role='user', speaker=session.current_speaker, text=text)

    # Quiet-word pre-filter: pause the conversation loop. No tutor call.
    if session.is_quiet_word(text):
        session.set_paused(True)
        bus.publish('transcript', role='system', speaker='system',
                    text='(conversation paused — click the mic when you want to keep going)')
        bus.publish('session_paused', reason='quiet_word')
        return

    threading.Thread(target=_handle_user_turn, args=(text,), daemon=True).start()


def init_voice():
    global _voice
    _voice = VoiceInput(
        on_wake_word=_on_wake_word,
        on_recording_start=_on_recording_start,
        on_speech_transcribed=_on_speech_transcribed,
        on_recording_stop=_on_recording_stop,
    )
    _voice.start()


# --- Tutor turn pipeline --------------------------------------------------

_tts_seq_lock = threading.Lock()
_tts_seq = 0


def _next_tts_basename():
    global _tts_seq
    with _tts_seq_lock:
        _tts_seq += 1
        return _tts_seq, f'{_tts_seq:06d}'


def _audio_duration_seconds(path: str) -> float:
    """Return playback duration of a WAV or MP3 file. Falls back to a small
    estimate so the auto-relisten timer never blocks forever."""
    ext = os.path.splitext(path)[1].lower()
    if ext == '.wav':
        try:
            import soundfile as sf
            info = sf.info(path)
            if info.samplerate > 0:
                return float(info.frames) / float(info.samplerate)
        except Exception:
            pass
        try:
            # Kokoro outputs 24 kHz mono PCM16: 2 bytes/sample.
            return max(0.5, os.path.getsize(path) / (24000 * 2))
        except Exception:
            return 1.5
    if ext == '.mp3':
        # 128 kbps MP3 ≈ 16 KB/s.
        try:
            return max(0.5, os.path.getsize(path) / 16000.0)
        except Exception:
            return 2.0
    return 1.5


def _handle_user_turn(user_text: str):
    """Stream the tutor's response: emit each sentence as both a transcript
    event AND a synthesized audio URL. After the response finishes playing,
    auto-re-arm the mic for continuous conversation (unless paused)."""
    total_audio = 0.0
    try:
        sentences_iter = tutor_brain.stream_turn(session, user_text)
        for sentence in sentences_iter:
            bus.publish('transcript', role='assistant', speaker='Tutor', text=sentence)
            seq, basename = _next_tts_basename()
            result = voice_output.synthesize_audio(sentence, SESSION_TTS_DIR, basename)
            if result:
                filename = result['filename']
                url = f'/tts/{SESSION_ID}/{filename}'
                duration = _audio_duration_seconds(os.path.join(SESSION_TTS_DIR, filename))
                total_audio += duration
                bus.publish('audio', url=url, seq=seq, text=sentence, duration=duration,
                            provider=result.get('provider'), voice=result.get('voice'))
            else:
                _publish_error('tts_synth', f'No audio produced for: {sentence[:80]}')
                bus.publish('audio_error', seq=seq, text=sentence)
        bus.publish('turn_complete', total_audio=total_audio)
    except Exception as e:
        _publish_error('tutor_turn', e)
        return

    # Auto-re-arm the mic so the conversation keeps flowing without the user
    # having to click. Wait for the audio queue on the browser to finish
    # playing, plus a small buffer so the mic doesn't pick up the speakers.
    if session.paused or _voice is None:
        return
    if total_audio <= 0:
        # No TTS played — re-arm almost immediately.
        wait = 0.5
    else:
        # Start-of-audio latency on the browser is ~150ms; mic warm-up ~200ms.
        # 1.0s post-audio buffer gives Rowan a beat to breathe before speaking.
        wait = total_audio + 1.0
    time.sleep(wait)

    # Recheck — user may have paused or another turn may have already started.
    if session.paused or _voice is None or _voice.is_listening():
        return
    _voice.start_conversation_listen()


# --- Routes ---------------------------------------------------------------

@app.route('/')
def index():
    return send_from_directory(STATIC_DIR, 'index.html')


@app.route('/events')
def events():
    """SSE stream of all backend events."""
    def stream():
        q = bus.subscribe()
        try:
            # On connect, send a snapshot so the UI has initial state.
            snapshot = {
                'type': 'snapshot',
                'speaker': session.current_speaker,
                'known_speakers': session.known_speakers(),
                'problems': session.problem_list(),
                'session_id': SESSION_ID,
            }
            yield f'data: {json.dumps(snapshot)}\n\n'

            # Heartbeat every 15s keeps the connection alive through proxies.
            last_beat = time.time()
            while True:
                try:
                    event = q.get(timeout=15)
                    yield f'data: {json.dumps(event)}\n\n'
                except queue.Empty:
                    yield ': keepalive\n\n'
                    last_beat = time.time()
        except GeneratorExit:
            pass
        finally:
            bus.unsubscribe(q)

    resp = Response(stream_with_context(stream()), mimetype='text/event-stream')
    resp.headers['Cache-Control'] = 'no-cache'
    resp.headers['X-Accel-Buffering'] = 'no'
    return resp


@app.route('/api/mic/start', methods=['POST'])
def api_mic_start():
    if _voice is None:
        return jsonify({'error': 'voice input not initialized'}), 503
    # Explicit user gesture also clears any quiet-word pause.
    if session.paused:
        session.set_paused(False)
        bus.publish('session_resumed', reason='user_click')
    _voice.start_conversation_listen()
    return jsonify({'ok': True})


@app.route('/api/mic/stop', methods=['POST'])
def api_mic_stop():
    # User-driven pause. RealtimeSTT auto-terminates on silence so we
    # can't truly interrupt a recording in flight, but we set paused so the
    # auto-re-arm at the end of the next turn (if any) will not fire.
    session.set_paused(True)
    bus.publish('session_paused', reason='user_click')
    bus.publish('mic_status', state='idle')
    return jsonify({'ok': True})


@app.route('/api/upload', methods=['POST'])
def api_upload():
    """Accept a PDF/image/text file OR a `text` form field."""
    pasted = (request.form.get('text') or '').strip()
    if pasted:
        problems = coursework_loader.load_from_text(pasted)
        session.set_problems(problems)
        return jsonify(session.problem_list())

    file = request.files.get('file')
    if not file or not file.filename:
        return jsonify({'error': 'no file or text provided'}), 400

    safe_name = os.path.basename(file.filename).replace('\\', '_').replace('/', '_')
    dest_path = os.path.join(SESSION_UPLOAD_DIR, safe_name)
    file.save(dest_path)

    try:
        problems = coursework_loader.load_from_upload(dest_path)
    except Exception as e:
        return jsonify({'error': str(e)}), 400

    session.set_problems(problems)
    return jsonify(session.problem_list())


@app.route('/api/problem/list', methods=['GET'])
def api_problem_list():
    return jsonify(session.problem_list())


@app.route('/api/problem/set', methods=['POST'])
def api_problem_set():
    body = request.get_json(silent=True) or {}
    index = body.get('index')
    if not isinstance(index, int):
        return jsonify({'error': 'index must be int'}), 400
    session.set_current_index(index)
    return jsonify(session.problem_list())


@app.route('/api/speaker/list', methods=['GET'])
def api_speaker_list():
    return jsonify({
        'current': session.current_speaker,
        'known': session.known_speakers(),
    })


@app.route('/api/speaker/set', methods=['POST'])
def api_speaker_set():
    body = request.get_json(silent=True) or {}
    name = (body.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'name required'}), 400
    ok = session.set_current_speaker(name)
    if not ok and name != session.current_speaker:
        return jsonify({'error': f'unknown speaker: {name}'}), 400
    return jsonify({'current': session.current_speaker, 'known': session.known_speakers()})


@app.route('/api/speaker/add', methods=['POST'])
def api_speaker_add():
    body = request.get_json(silent=True) or {}
    name = (body.get('name') or '').strip()
    pronunciation = (body.get('pronunciation') or '').strip()
    if not name:
        return jsonify({'error': 'name required'}), 400

    known = config.get_setting('known_speakers', ['Rowan', 'Liam']) or ['Rowan']
    if name not in known:
        known.append(name)
        config.set_setting('known_speakers', known)

    if pronunciation:
        table = config.get_setting('name_pronunciations', {}) or {}
        table[name] = pronunciation
        config.set_setting('name_pronunciations', table)

    return jsonify({'known': session.known_speakers()})


@app.route('/api/message', methods=['POST'])
def api_message():
    """Typed input — mirrors the voice path."""
    body = request.get_json(silent=True) or {}
    text = (body.get('text') or '').strip()
    if not text:
        return jsonify({'error': 'text required'}), 400
    bus.publish('transcript', role='user', speaker=session.current_speaker, text=text)
    threading.Thread(target=_handle_user_turn, args=(text,), daemon=True).start()
    return jsonify({'ok': True})


@app.route('/api/settings', methods=['GET', 'POST'])
def api_settings():
    if request.method == 'GET':
        s = config.load_user_settings()
        out = dict(s)
        # Redact secrets — return whether each is set, not the value.
        anth_key = s.get('anthropic_api_key') or ''
        out['anthropic_api_key'] = '***' if anth_key else ''
        out['anthropic_api_key_set'] = bool(anth_key)
        el_key = s.get('elevenlabs_api_key') or ''
        out['elevenlabs_api_key'] = '***' if el_key else ''
        out['elevenlabs_api_key_set'] = bool(el_key)
        out['available_voices'] = [v[0] for v in config.KOKORO_VOICES]
        out['available_models'] = [{'id': m[0], 'name': m[1]} for m in config.ELEVENLABS_MODELS]
        return jsonify(out)

    body = request.get_json(silent=True) or {}
    updated = []
    for key in ('anthropic_api_key', 'wake_word_sensitivity', 'kokoro_voice',
                'known_speakers', 'name_pronunciations',
                'tts_provider', 'elevenlabs_api_key', 'elevenlabs_voice_id',
                'elevenlabs_model'):
        if key in body:
            config.set_setting(key, body[key])
            updated.append(key)

    # Apply runtime side-effects of changed settings
    if 'anthropic_api_key' in updated:
        tutor_brain.reset_client()
    if 'kokoro_voice' in updated:
        voice_output.set_voice(body['kokoro_voice'])
    if 'wake_word_sensitivity' in updated and _voice is not None:
        _voice.set_sensitivity(float(body['wake_word_sensitivity']))
    if 'elevenlabs_api_key' in updated:
        voice_output.reset_elevenlabs_client()

    return jsonify({'updated': updated})


@app.route('/api/elevenlabs/voices', methods=['GET'])
def api_elevenlabs_voices():
    """List the user's ElevenLabs voices so the settings UI can populate a picker."""
    voices = voice_output.list_elevenlabs_voices()
    return jsonify({'voices': voices})


@app.route('/api/errors', methods=['GET'])
def api_errors():
    return jsonify({'entries': error_log.entries(), 'count': error_log.count()})


@app.route('/api/errors/clear', methods=['POST'])
def api_errors_clear():
    error_log.clear()
    bus.publish('errors_cleared')
    return jsonify({'ok': True, 'count': 0})


@app.route('/api/errors/log', methods=['POST'])
def api_errors_log():
    """Client-side errors push here so they land in the same ring buffer."""
    body = request.get_json(silent=True) or {}
    source = (body.get('source') or 'client').strip()[:64]
    message = (body.get('message') or '').strip()[:2000]
    level = (body.get('level') or 'error').strip()[:16]
    if not message:
        return jsonify({'error': 'message required'}), 400
    error_log.log(source, message, level=level)
    bus.publish('error', source=source, message=message, count=error_log.count())
    return jsonify({'ok': True, 'count': error_log.count()})


@app.route('/api/elevenlabs/usage', methods=['GET'])
def api_elevenlabs_usage():
    """Report current month character usage against the configured key.

    Tries /v1/user/subscription, falls back to /v1/user (which contains
    subscription as a nested object) since some scoped API keys may have
    different access. On 401, returns a hint about the required 'User' scope.
    """
    key = (config.get_setting('elevenlabs_api_key', '') or '').strip()
    if not key:
        return jsonify({'error': 'no api key'}), 400

    import requests
    headers = {'xi-api-key': key, 'Accept': 'application/json'}

    def parse_sub(sub: dict):
        used = int(sub.get('character_count') or 0)
        limit = int(sub.get('character_limit') or 0)
        remaining = max(0, limit - used)
        pct = (used / limit * 100.0) if limit > 0 else 0.0
        return {
            'used': used,
            'limit': limit,
            'remaining': remaining,
            'pct': round(pct, 1),
            'next_reset_unix': sub.get('next_character_count_reset_unix'),
            'tier': sub.get('tier') or '',
        }

    # Try the dedicated subscription endpoint first.
    try:
        r = requests.get('https://api.elevenlabs.io/v1/user/subscription',
                         headers=headers, timeout=10)
        if r.status_code == 200:
            return jsonify(parse_sub(r.json()))
        first_status = r.status_code
        first_body = r.text[:200]
    except Exception as e:
        first_status, first_body = 0, str(e)

    # Fallback: /v1/user (some scoped keys allow this but not /subscription).
    try:
        r = requests.get('https://api.elevenlabs.io/v1/user',
                         headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json()
            sub = data.get('subscription') or {}
            if sub:
                return jsonify(parse_sub(sub))
    except Exception:
        pass

    # Both failed. Return a helpful error.
    if first_status == 401:
        return jsonify({
            'error': 'HTTP 401 — key lacks User scope',
            'hint': "Your ElevenLabs API key doesn't have permission to read user/subscription. "
                    "Create a new key in your ElevenLabs dashboard with 'User: Read' scope, "
                    "or use the workspace API key.",
        }), 401
    return jsonify({'error': f'HTTP {first_status}', 'detail': first_body}), first_status or 500


@app.route('/tts/<session_id>/<filename>')
def serve_tts(session_id, filename):
    if session_id != SESSION_ID:
        return jsonify({'error': 'unknown session'}), 404
    # Let Flask infer the mimetype from the extension (.wav or .mp3).
    return send_from_directory(SESSION_TTS_DIR, filename)


@app.route('/uploads/<session_id>/<path:filename>')
def serve_upload(session_id, filename):
    if session_id != SESSION_ID:
        return jsonify({'error': 'unknown session'}), 404
    return send_from_directory(SESSION_UPLOAD_DIR, filename)


@app.route('/api/problem/<problem_id>/image')
def serve_problem_image(problem_id):
    """Serve the image bytes for a problem by id."""
    p = None
    for prob in session._problems:
        if prob.id == problem_id:
            p = prob
            break
    if p is None or not p.has_image:
        return jsonify({'error': 'not found'}), 404
    folder = os.path.dirname(p.image_path)
    name = os.path.basename(p.image_path)
    return send_from_directory(folder, name)


# --- Startup --------------------------------------------------------------

def main():
    print(f'[INFO] Whiskers tutor session: {SESSION_ID}')
    print(f'[INFO] TTS cache: {SESSION_TTS_DIR}')
    print(f'[INFO] Uploads:   {SESSION_UPLOAD_DIR}')

    # Apply persisted voice if present.
    voice_setting = config.get_setting('kokoro_voice')
    if voice_setting:
        voice_output.set_voice(voice_setting)

    init_voice()

    print('[INFO] Open http://127.0.0.1:5000 in your browser.')
    app.run(host='127.0.0.1', port=5000, threaded=True, debug=False)


if __name__ == '__main__':
    main()
