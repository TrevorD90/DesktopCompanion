# main.py — Entry point for Whiskers AI Tutor Cat
# Wires all components together and manages the application state machine.

import os
import signal
import sys
import threading
import time

# Print banner early so the user sees something immediately
print('=' * 50)
print('  WHISKERS — AI Tutor Cat')
print('  100% Local • 100% Private')
print('=' * 50)
print()
print('[INFO] Loading modules...')

# Check critical dependencies before heavy imports
_missing = []
for _mod in ['PIL', 'requests']:
    try:
        __import__(_mod)
    except ImportError:
        _missing.append(_mod)

if _missing:
    print(f'[ERROR] Missing required packages: {", ".join(_missing)}')
    print('[INFO] Run: pip install -r requirements.txt')
    sys.exit(1)

import requests

import config
import memory_manager
import voice_output
from ai_brain import AIBrain
from animation_manager import AnimationManager
from cat_window import CatWindow, CatState
from control_panel import ControlPanel
from speech_bubble import SpeechBubble
from tray_icon import TrayIcon
from voice_input import VoiceInput


# Application states (maps to design doc Table 5)
class AppState:
    ROAMING     = 'ROAMING'
    AWAKE       = 'AWAKE'
    LISTENING   = 'LISTENING'
    THINKING    = 'THINKING'
    SPEAKING    = 'SPEAKING'
    CELEBRATING = 'CELEBRATING'
    CONVERSING  = 'CONVERSING'


class Whiskers:
    """Main application orchestrator."""

    def __init__(self):
        self._state = AppState.ROAMING
        self._anim_manager = None
        self._cat = None
        self._bubble = None
        self._brain = None
        self._voice_in = None
        self._tray = None
        self._panel = None
        self._running = False
        self._cat_running = False
        self._awaiting_name = False
        self._convo_idle_timer = None
        self._in_conversation = False  # Tracks conversation mode across state changes

    # --- Ollama check ---

    def _check_ollama(self):
        """Verify Ollama is running by hitting GET /api/tags."""
        try:
            resp = requests.get(f'{config.OLLAMA_BASE_URL}/api/tags', timeout=5)
            resp.raise_for_status()
            print('[INFO] Ollama is running.')
            return True
        except Exception:
            print('[WARNING] Ollama is not running or unreachable at', config.OLLAMA_BASE_URL)
            print('[INFO] Whiskers will still start, but AI responses will be unavailable.')
            print('[INFO] Start Ollama with: ollama serve')
            return False

    # --- Voice input callbacks ---

    def _on_wake_word(self):
        """Callback: wake word detected."""
        if self._in_conversation:
            return  # Ignore wake word during active conversation
        self._state = AppState.AWAKE
        if self._cat:
            self._cat.set_state(CatState.LISTEN)
        if self._bubble:
            self._bubble.show('I heard you! Go ahead...')
        print('[EVENT] Wake word detected.')

    def _on_recording_start(self):
        """Callback: recording has begun."""
        self._cancel_convo_idle_timer()
        if self._state == AppState.CONVERSING:
            self._cat.set_state(CatState.LISTEN)
        self._state = AppState.LISTENING
        print('[EVENT] Recording started.')

    def _on_speech_transcribed(self, text):
        """Callback: speech has been transcribed. Process through AI."""
        print(f'[EVENT] Transcribed: "{text}"')

        # Handle first-launch name prompt
        if getattr(self, '_awaiting_name', False):
            self._awaiting_name = False
            # Save the name to memory
            name = text.strip().rstrip('.!?')
            # Strip common prefixes like "My name is", "I'm", "I am"
            for prefix in ["my name is ", "i'm ", "i am ", "it's ", "they call me "]:
                if name.lower().startswith(prefix):
                    name = name[len(prefix):]
                    break
            name = name.strip().title()
            if name:
                memory = memory_manager.load_memory()
                memory['student']['name'] = name
                memory_manager.save_memory(memory)
                # Rebuild AI brain with updated student context
                self._brain = AIBrain()
                print(f'[INFO] Student name set to: {name}')
                self._bubble.show(f"Nice to meet you, {name}! Say the wake word when you need help!")
            else:
                self._bubble.show("I didn't catch that. You can set your name in the config file!")
            self._state = AppState.ROAMING
            self._cat.set_state(CatState.IDLE)
            return

        # Check for quiet word to exit conversation mode
        if self._in_conversation:
            settings = config.load_user_settings()
            quiet_words = settings.get('quiet_words', config.QUIET_WORDS)
            text_lower = text.lower().strip()
            if any(qw.lower() in text_lower for qw in quiet_words):
                print('[EVENT] Quiet word detected. Ending conversation.')
                self._in_conversation = False
                self._cancel_convo_idle_timer()
                self._state = AppState.SPEAKING
                goodbye_msg = 'Okay, see you later! Say the wake word when you need me.'

                def on_bye_start():
                    if self._cat:
                        self._cat.set_state(CatState.TALK)
                    if self._bubble:
                        self._bubble.show_persistent(goodbye_msg)

                def on_bye_finish():
                    self._state = AppState.ROAMING
                    if self._cat:
                        self._cat.set_state(CatState.IDLE)
                    if self._bubble:
                        self._bubble.dismiss()

                voice_output.speak(goodbye_msg,
                                   on_start=on_bye_start,
                                   on_finish=on_bye_finish)
                return

        self._state = AppState.THINKING

        thread = threading.Thread(
            target=self._process_response,
            args=(text,),
            daemon=True
        )
        thread.start()

    def _on_recording_stop(self):
        """Callback: recording has stopped."""
        if self._state == AppState.LISTENING and self._cat and self._bubble:
            self._cat.set_state(CatState.THINK)
            self._bubble.show('Hmm, let me think...')

    def _process_response(self, transcribed_text):
        """Get AI response and speak it. Runs in a worker thread.

        Uses streaming to start speaking the first sentence faster,
        while collecting the rest in the background.
        """
        if not self._cat or not self._running:
            return

        cat = self._cat      # Local refs to avoid None between checks
        bubble = self._bubble

        if not cat or not bubble:
            return

        cat.set_state(CatState.THINK)
        bubble.show('Hmm, let me think...')

        # Collect all sentences from the streaming LLM response
        sentences = list(self._brain.get_response_stream(transcribed_text))
        full_response = ' '.join(sentences) if sentences else ''

        if not full_response:
            full_response = 'I got a little confused. Can you ask me again?'

        print(f'[EVENT] AI response: "{full_response[:80]}..."')

        if not self._running or not self._cat:
            return  # Cat was stopped while AI was generating

        # Speak the full response — bubble appears when audio starts
        self._state = AppState.SPEAKING

        def on_speak_start():
            cat.set_state(CatState.TALK)
            bubble.show_persistent(full_response)

        voice_output.speak_sync(full_response, on_start=on_speak_start)

        if not self._running or not self._cat:
            return  # Cat was stopped while speaking

        # After speaking — check for celebration then enter conversation mode
        celebration_words = ['excellent', 'that is right',
                             'amazing', 'wonderful', 'great job',
                             'you got it', 'fantastic']
        if any(w in full_response.lower() for w in celebration_words):
            self._state = AppState.CELEBRATING
            cat.show_happy_animation()
            time.sleep(2.0)

        if not self._running or not self._cat:
            return

        # Enter conversation mode
        self._enter_conversation_mode()

    # --- Conversation mode helpers ---

    def _enter_conversation_mode(self):
        """Enter conversation mode: listen for next question, roam after 30s idle."""
        self._in_conversation = True
        self._state = AppState.CONVERSING
        self._cat.set_state(CatState.LISTEN)
        self._bubble.show("I'm listening...")
        print('[EVENT] Conversation mode — waiting for next question or quiet word.')

        # Start 30s idle timer — if no speech, let cat roam while still listening
        self._cancel_convo_idle_timer()
        if self._cat and self._cat.root:
            self._convo_idle_timer = self._cat.root.after(
                30000, self._on_convo_idle_timeout
            )

        if self._voice_in:
            self._voice_in.start_conversation_listen()

    def _on_convo_idle_timeout(self):
        """30s passed with no user speech in conversation mode — let cat roam."""
        if self._state == AppState.CONVERSING and self._cat:
            self._cat.set_state(CatState.IDLE)
            if self._bubble:
                self._bubble.dismiss()
            print('[EVENT] Conversation idle — cat roaming but still listening.')

    def _cancel_convo_idle_timer(self):
        """Cancel the conversation idle timer if active."""
        timer_id = self._convo_idle_timer
        if timer_id is None:
            return
        self._convo_idle_timer = None
        try:
            if self._cat and self._cat.root:
                self._cat.root.after_cancel(timer_id)
        except Exception:
            pass

    def _on_mode_changed(self, mode):
        """Handle personality mode change from control panel."""
        if self._brain:
            self._brain.set_mode(mode)
        if self._bubble:
            label = 'Teacher' if mode == 'teacher' else 'Companion'
            self._bubble.show(f'Switched to {label} mode!')

    def _on_provider_changed(self, provider):
        """Handle AI provider change from control panel."""
        config.set_setting('ai_provider', provider)
        # Clear cached cloud clients so they reinitialize with new keys
        import ai_brain
        ai_brain._openai_client = None
        ai_brain._anthropic_client = None
        if self._brain:
            self._brain.clear_history()
        if self._bubble:
            for pid, plabel in config.AI_PROVIDERS:
                if pid == provider:
                    self._bubble.show(f'Switched to {plabel}!')
                    break
        print(f'[INFO] AI provider changed to: {provider}')

    # --- Cat lifecycle (start/stop without quitting the app) ---

    def start_cat(self):
        """Start the cat window, bubble, and voice input."""
        if self._cat_running:
            print('[INFO] Cat is already running.')
            return

        # Create and start cat window
        self._cat = CatWindow(
            animation_manager=self._anim_manager,
            on_settings_callback=self._show_settings
        )
        self._cat.start()
        self._cat.wait_ready()  # Block until tkinter root is initialized

        # Create speech bubble
        self._bubble = SpeechBubble(self._cat)

        # Create control panel (Toplevel on cat's root)
        self._panel = ControlPanel(self._cat.root, {
            'start': self.start_cat,
            'stop': self.stop_cat,
            'add_animation': self._on_animation_added,
            'remove_animation': self._on_animation_removed,
            'change_voice': self._on_voice_changed,
            'get_anim_summary': self._anim_manager.get_all_types_summary,
            'get_anim_variants': self._anim_manager.list_variants,
            'add_transition': self._on_transition_added,
            'remove_transition': self._on_transition_removed,
            'get_transition_summary': self._anim_manager.get_all_transitions_summary,
            'get_transition_variants': self._anim_manager.list_transitions,
            'change_mode': self._on_mode_changed,
            'change_provider': self._on_provider_changed,
            'change_wake_word': self._on_wake_word_changed,
            'change_sensitivity': self._on_sensitivity_changed,
            'change_quiet_words': self._on_quiet_words_changed,
        })

        # Start voice input
        self._voice_in = VoiceInput(
            on_wake_word=self._on_wake_word,
            on_recording_start=self._on_recording_start,
            on_speech_transcribed=self._on_speech_transcribed,
            on_recording_stop=self._on_recording_stop
        )
        self._voice_in.start()

        self._cat_running = True
        if self._tray:
            self._tray.update_status(True)

        # Show greeting — speak it aloud so the user hears the cat
        student_name = memory_manager.load_memory()['student']['name']
        if not student_name or student_name == "your child's name":
            greeting = "Hello! I'm Whiskers! What's your name?"
            self._awaiting_name = True

            def on_greeting_start():
                if self._cat:
                    self._cat.set_state(CatState.TALK)
                if self._bubble:
                    self._bubble.show(greeting)

            def on_greeting_finish():
                if not self._running or not self._cat:
                    return
                # Wait for STT to be ready before listening for the name
                if self._voice_in:
                    self._voice_in.wait_stt_ready()
                self._state = AppState.CONVERSING
                if self._cat:
                    self._cat.set_state(CatState.LISTEN)
                self._bubble.show("Go ahead, tell me your name!")
                if self._voice_in:
                    self._voice_in.start_conversation_listen()

            voice_output.speak(greeting,
                               on_start=on_greeting_start,
                               on_finish=on_greeting_finish)
        else:
            greeting = f'Hi {student_name}! I\'m Whiskers. Say the wake word when you need help!'

            def on_greeting_start():
                if self._cat:
                    self._cat.set_state(CatState.TALK)
                if self._bubble:
                    self._bubble.show(greeting)

            def on_greeting_finish():
                if self._cat:
                    self._cat.set_state(CatState.IDLE)

            voice_output.speak(greeting,
                               on_start=on_greeting_start,
                               on_finish=on_greeting_finish)

        print('[INFO] Cat started.')

    def stop_cat(self):
        """Stop the cat window, bubble, and voice input (tray stays alive)."""
        if not self._cat_running:
            print('[INFO] Cat is already stopped.')
            return

        if self._voice_in:
            self._voice_in.stop()
            self._voice_in = None
        voice_output.stop_speaking()
        if self._cat:
            self._cat.stop()
            self._cat = None
        self._bubble = None
        self._panel = None

        self._cat_running = False
        self._state = AppState.ROAMING
        if self._tray:
            self._tray.update_status(False)

        print('[INFO] Cat stopped.')

    # --- Control panel callbacks ---

    def _show_settings(self):
        """Show the control panel. Called from tray or right-click menu."""
        if not self._cat_running:
            # Auto-start cat so we have a tkinter root for the panel
            self.start_cat()

        if self._panel and self._cat and self._cat.root:
            self._cat.root.after(0, self._panel.show)

    def _on_voice_changed(self, voice_name):
        """Handle voice selection from control panel."""
        voice_output.set_voice(voice_name)

    def _on_animation_added(self, anim_type, variant_name, source_path):
        """Handle animation upload from control panel."""
        self._anim_manager.register_variant(anim_type, variant_name, source_path)
        if self._cat:
            self._cat.reload_sprites()

    def _on_animation_removed(self, anim_type, variant_name):
        """Handle animation removal from control panel."""
        self._anim_manager.remove_variant(anim_type, variant_name)
        if self._cat:
            self._cat.reload_sprites()

    def _on_transition_added(self, transition_key, variant_name, source_path):
        """Handle transition upload from control panel."""
        self._anim_manager.register_transition(transition_key, variant_name, source_path)

    def _on_transition_removed(self, transition_key, variant_name):
        """Handle transition removal from control panel."""
        self._anim_manager.remove_transition(transition_key, variant_name)

    def _on_wake_word_changed(self, model_id):
        """Handle wake word model selection from control panel."""
        config.set_setting('wake_word_model', model_id)
        print(f'[INFO] Wake word model changed to: {model_id}. Takes effect on next Start/Stop cycle.')

    def _on_sensitivity_changed(self, value):
        """Handle wake word sensitivity change from control panel."""
        config.set_setting('wake_word_sensitivity', value)
        if self._voice_in:
            self._voice_in.set_sensitivity(value)

    def _on_quiet_words_changed(self, quiet_words_text):
        """Handle quiet words text change from control panel."""
        words = [w.strip() for w in quiet_words_text.split(',') if w.strip()]
        config.set_setting('quiet_words', words)
        print(f'[INFO] Quiet words updated: {words}')

    # --- Shutdown ---

    def _shutdown(self, signum=None, frame=None):
        """Graceful shutdown handler."""
        if not self._running:
            return

        print('\n[INFO] Shutting down Whiskers...')
        self._running = False

        # Stop cat components
        self.stop_cat()

        # Stop tray icon
        if self._tray:
            self._tray.stop()

        # Save memory
        try:
            memory = memory_manager.load_memory()
            memory_manager.save_memory(memory)
            print('[INFO] Memory saved.')
        except Exception as e:
            print(f'[WARNING] Could not save memory: {e}')

        print('[INFO] Whiskers says goodbye!')
        sys.exit(0)

    # --- Main entry point ---

    def run(self):
        """Start Whiskers."""
        # Ensure working directory is the whiskers/ folder so relative paths resolve
        os.chdir(os.path.dirname(os.path.abspath(__file__)))

        self._running = True

        # Step 1: Create animation manager
        self._anim_manager = AnimationManager()
        print('[INFO] Animation manager loaded.')

        # Step 2: Check Ollama
        self._check_ollama()

        # Step 3: Load student memory
        memory = memory_manager.load_memory()
        student_name = memory['student']['name']
        print(f'[INFO] Student profile loaded: {student_name}')

        # Step 4: Initialise AI brain
        self._brain = AIBrain()
        print('[INFO] AI brain initialised.')

        # Step 5: Pre-warm TTS so the first greeting speaks immediately
        print('[INFO] Warming up TTS engine...')
        voice_output._ensure_kokoro()
        print('[INFO] TTS ready.')

        # Step 6: Start system tray icon
        self._tray = TrayIcon(
            on_start=self.start_cat,
            on_stop=self.stop_cat,
            on_settings=self._show_settings,
            on_quit=self._shutdown
        )
        self._tray.start()

        # Step 7: Start the cat (window + bubble + voice input)
        self.start_cat()

        print()
        print(f'[READY] Whiskers is awake and waiting for {student_name}!')
        print('[INFO] Say "Hey Whiskers" or press F9 to start talking.')
        print('[INFO] Use the system tray icon to access settings or quit.')
        print('[INFO] Press Ctrl+C to quit.')
        print()

        # Register shutdown handlers
        signal.signal(signal.SIGINT, self._shutdown)
        signal.signal(signal.SIGTERM, self._shutdown)

        # Main loop — keep the main thread alive
        try:
            while self._running:
                time.sleep(0.5)
        except KeyboardInterrupt:
            self._shutdown()


if __name__ == '__main__':
    app = Whiskers()
    app.run()
