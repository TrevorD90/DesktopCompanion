# voice_input.py — Wake word detection + speech-to-text for Whiskers

import os
import threading
import time

import config

# Trust the Silero VAD repo so torch.hub.load doesn't prompt for interactive input.
# Without this, RealtimeSTT crashes with EOFError when running without a terminal.
try:
    import torch.hub as _torch_hub
    _orig_load = _torch_hub.load
    def _trusted_load(*args, **kwargs):
        kwargs.setdefault('trust_repo', True)
        return _orig_load(*args, **kwargs)
    _torch_hub.load = _trusted_load
except ImportError:
    pass

# Detect available libraries
_HAS_OPENWAKEWORD = False
_HAS_REALTIMESTT = False
_HAS_KEYBOARD = False

try:
    import openwakeword
    from openwakeword.model import Model as OWWModel
    _HAS_OPENWAKEWORD = True
except ImportError:
    pass

try:
    from RealtimeSTT import AudioToTextRecorder
    _HAS_REALTIMESTT = True
except ImportError:
    pass

try:
    import keyboard
    _HAS_KEYBOARD = True
except ImportError:
    pass

# Audio capture for wake word detection
try:
    import pyaudio
    _HAS_PYAUDIO = True
except ImportError:
    _HAS_PYAUDIO = False


class VoiceInput:
    """Two-phase voice input: wake word detection, then active recording + transcription.

    Phase 1: openwakeword listens for the wake phrase (lightweight, always-on).
    Phase 2: RealtimeSTT records and transcribes after wake word trigger.
    Fallback: F9 hotkey if openwakeword is unavailable.
    """

    def __init__(self, on_wake_word, on_recording_start,
                 on_speech_transcribed, on_recording_stop):
        self._on_wake_word = on_wake_word
        self._on_recording_start = on_recording_start
        self._on_speech_transcribed = on_speech_transcribed
        self._on_recording_stop = on_recording_stop

        self._running = False
        self._listening = False
        self._thread = None
        self._stt_recorder = None
        self._stt_ready = threading.Event()
        self._stt_init_started = False
        self._oww_model = None
        self._use_hotkey = False
        self._conversation_mode = False
        self._sensitivity = config.WAKE_WORD_SENSITIVITY

    def _init_stt(self):
        """Pre-load the RealtimeSTT recorder so it's ready when needed."""
        if self._stt_init_started:
            return
        self._stt_init_started = True
        if not _HAS_REALTIMESTT:
            self._stt_ready.set()
            return
        try:
            print('[INFO] Loading speech recognition model (one-time)...', flush=True)
            stt_kwargs = dict(
                model=config.WHISPER_MODEL,
                language=config.WHISPER_LANGUAGE,
                spinner=False,
                silero_sensitivity=0.4,
                post_speech_silence_duration=0.8,
            )
            device_index = config.get_setting('input_device_index')
            if device_index is not None:
                stt_kwargs['input_device_index'] = int(device_index)
            self._stt_recorder = AudioToTextRecorder(**stt_kwargs)
            print('[INFO] Speech recognition ready.', flush=True)
        except Exception as e:
            # Print a full traceback, not just str(e) — library-level errors
            # often have opaque str representations (numeric errnos, empty
            # strings) that obscure the real cause. Logged at ERROR because
            # this permanently disables voice input for the session.
            import traceback
            print(f'[ERROR] RealtimeSTT init FAILED: {type(e).__name__}: {e}', flush=True)
            traceback.print_exc()
            self._stt_recorder = None
        self._stt_ready.set()

    def wait_stt_ready(self, timeout=30):
        """Block until the STT recorder is initialized. Returns True if ready."""
        return self._stt_ready.wait(timeout=timeout)

    def start(self):
        """Begin background listening (wake word or F9 fallback)."""
        self._running = True

        # Surface uncaught exceptions in background threads — Python's default
        # hook writes to stderr, which pythonw.exe may silently swallow under
        # IntelliJ's Run window. Route to stdout via print() so they always show.
        def _log_thread_exc(args):
            import traceback as _tb
            print(f'[ERROR] Uncaught in {args.thread.name}: '
                  f'{args.exc_type.__name__}: {args.exc_value}', flush=True)
            _tb.print_tb(args.exc_traceback)
        threading.excepthook = _log_thread_exc

        # Pre-load STT in background so first F9 press is fast
        if _HAS_REALTIMESTT:
            threading.Thread(target=self._init_stt, daemon=True,
                             name='STT-Init').start()

        if _HAS_OPENWAKEWORD and _HAS_PYAUDIO:
            print('[INFO] Wake word detection active. Say "Hey Whiskers" to start.')
            self._thread = threading.Thread(target=self._wake_word_loop, daemon=True)
            self._thread.start()
        elif _HAS_KEYBOARD:
            # Fallback: F9 hotkey
            self._use_hotkey = True
            print('[INFO] Wake word not available. Press F9 to talk to Whiskers.')
            keyboard.on_press_key('F9', lambda _: self._on_hotkey_pressed())
        else:
            print('[WARNING] No wake word or keyboard library available.')
            print('[INFO] Voice input disabled. Install openwakeword or keyboard package.')

    def _wake_word_loop(self):
        """Phase 1: continuously listen for the wake word using openwakeword."""
        # Load wake word model from user settings
        settings = config.load_user_settings()
        model_id = settings.get('wake_word_model', 'hey_jarvis_v0.1')
        self._sensitivity = settings.get('wake_word_sensitivity', config.WAKE_WORD_SENSITIVITY)
        device_index = settings.get('input_device_index')
        print(f'[INFO] Using wake word model: {model_id}')

        try:
            self._oww_model = OWWModel(
                wakeword_models=[model_id],
                inference_framework='onnx'
            )
        except Exception as e:
            print(f'[WARNING] openwakeword model load failed: {e}')
            print('[INFO] Falling back to F9 hotkey.')
            if _HAS_KEYBOARD:
                self._use_hotkey = True
                keyboard.on_press_key('F9', lambda _: self._on_hotkey_pressed())
                print('[INFO] Press F9 to talk to Whiskers.')
            return

        # Open audio stream for wake word detection.
        # openwakeword is trained on 16 kHz mono; we must feed it at that rate.
        # WASAPI-only devices (e.g. Intel Smart Sound's WASAPI entry) reject
        # arbitrary rates — fall back to the system default mic for wake word
        # if the user's chosen device won't open at 16 kHz.
        pa = pyaudio.PyAudio()
        stream = None
        try:
            open_kwargs = dict(
                format=pyaudio.paInt16,
                channels=1,
                rate=16000,
                input=True,
                frames_per_buffer=1280,  # 80 ms chunks at 16 kHz
            )
            if device_index is not None:
                open_kwargs['input_device_index'] = int(device_index)

            try:
                stream = pa.open(**open_kwargs)
            except Exception as e:
                dev_hint = (f'device #{device_index}' if device_index is not None
                            else 'system default')
                print(f'[WARNING] Could not open {dev_hint} at 16 kHz for wake word ({e}).')
                if device_index is not None:
                    # Retry on the system default mic — MME usually accepts 16 kHz.
                    open_kwargs.pop('input_device_index', None)
                    try:
                        stream = pa.open(**open_kwargs)
                        print('[INFO] Wake word falling back to system default mic. '
                              'STT (F9 / conversation) still uses your selected device.')
                    except Exception as e2:
                        print(f'[ERROR] System default also failed at 16 kHz: {e2}')
                        stream = None

            if stream is None:
                print('[INFO] Falling back to F9 hotkey (no wake-word audio stream).')
                if _HAS_KEYBOARD:
                    self._use_hotkey = True
                    keyboard.on_press_key('F9', lambda _: self._on_hotkey_pressed())
                    print('[INFO] Press F9 to talk to Whiskers.')
                return

            print('[INFO] Listening for wake word...')

            import numpy as np

            while self._running:
                try:
                    audio_data = stream.read(1280, exception_on_overflow=False)
                    audio_np = np.frombuffer(audio_data, dtype=np.int16)

                    self._oww_model.predict(audio_np)

                    for _model_name, score in self._oww_model.prediction_buffer.items():
                        if score[-1] > self._sensitivity:
                            self._oww_model.reset()
                            self._handle_wake()
                            time.sleep(1.0)
                            break

                except Exception as e:
                    if self._running:
                        print(f'[ERROR] Wake word loop: {e}')
                        time.sleep(0.1)
        finally:
            if stream is not None:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
            try:
                pa.terminate()
            except Exception:
                pass

    def _on_hotkey_pressed(self):
        """Handle F9 keypress as wake word substitute."""
        if not self._listening:
            self._handle_wake()

    def _handle_wake(self):
        """Called when wake word is detected (or F9 pressed). Starts Phase 2."""
        if self._listening:
            return  # Already recording, ignore
        self._conversation_mode = False  # Fresh wake word trigger
        self._listening = True
        self._on_wake_word()
        self._on_recording_start()

        thread = threading.Thread(target=self._record_and_transcribe, daemon=True)
        thread.start()

    def start_conversation_listen(self):
        """Start recording without requiring a wake word (conversation mode).

        Called by main.py after the AI finishes speaking to continue the conversation.
        """
        if not self._running or self._listening:
            return
        self._conversation_mode = True
        self._listening = True
        self._on_recording_start()
        thread = threading.Thread(target=self._record_and_transcribe, daemon=True)
        thread.start()

    def _record_and_transcribe(self):
        """Phase 2: Use the pre-loaded RealtimeSTT recorder to capture and transcribe."""
        if not _HAS_REALTIMESTT:
            print('[WARNING] RealtimeSTT not installed. Cannot transcribe speech.')
            self._on_recording_stop()
            self._listening = False
            return

        # Wait for the background init to finish (up to 30s)
        self._stt_ready.wait(timeout=30)

        if self._stt_recorder is None:
            print('[ERROR] Speech recognition unavailable.')
            self._on_recording_stop()
            self._listening = False
            return

        try:
            text = self._stt_recorder.text()

            self._on_recording_stop()
            self._listening = False

            if text and text.strip():
                self._on_speech_transcribed(text.strip())
            elif self._conversation_mode and self._running:
                # Empty transcription in conversation mode — listen again after a brief pause
                time.sleep(0.3)
                if self._conversation_mode and self._running:
                    self.start_conversation_listen()

        except Exception as e:
            print(f'[ERROR] RealtimeSTT recording failed: {e}')
            self._on_recording_stop()
            self._listening = False

    def stop(self):
        """Cleanly shut down all listening."""
        self._running = False
        self._listening = False

        if self._use_hotkey and _HAS_KEYBOARD:
            try:
                keyboard.unhook_all()
            except Exception:
                pass

        if self._stt_recorder:
            try:
                self._stt_recorder.shutdown()
            except Exception:
                pass
            self._stt_recorder = None

    def set_sensitivity(self, value):
        """Update sensitivity threshold in real-time."""
        self._sensitivity = value

    @property
    def in_conversation_mode(self):
        return self._conversation_mode

    def is_listening(self) -> bool:
        """Return True if actively recording the student's speech."""
        return self._listening
