# control_panel.py — Settings/Control panel window for Whiskers

import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import config
import voice_output
from animation_manager import ANIMATION_TYPES, TRANSITION_KEYS, inspect_gif

# Probe sounddevice once at import. _SD_IMPORT_ERROR set => package or
# PortAudio DLL missing (pip-install fix); _SD_IMPORT_ERROR None => package
# loaded, so per-call errors are hardware/OS-level and need different guidance.
_sd = None
_SD_IMPORT_ERROR = None
try:
    import sounddevice as _sd
except Exception as e:
    _SD_IMPORT_ERROR = e
    print(f'[WARNING] sounddevice unavailable ({e}). Mic test disabled.')


def _classify_mic_error(exc, has_any_input, device_index):
    """Map a sounddevice/PortAudio exception to a user-actionable message."""
    msg = str(exc).lower()
    if not has_any_input:
        return ('No microphone detected. Plug in a mic, or enable an input '
                'device in Windows Sound settings.')
    if device_index is not None:
        return f'Saved device #{device_index} unavailable — pick another below.'
    if 'portaudio' in msg or 'host error' in msg or 'hostapi' in msg:
        return (f'Audio system error — is Windows Audio service running? '
                f'({exc})')
    return f'Audio error: {exc}'


class ControlPanel:
    """Settings window as a tkinter Toplevel (shares the cat window's root)."""

    def __init__(self, root, callbacks):
        """
        Args:
            root: The CatWindow's tk.Tk() root.
            callbacks: dict with keys:
                'start'          — callable()  start the cat
                'stop'           — callable()  stop the cat
                'add_animation'  — callable(anim_type, variant_name, source_path)
                'remove_animation' — callable(anim_type, variant_name)
                'change_voice'   — callable(voice_name)
                'get_anim_summary' — callable() -> dict[str, int]
                'get_anim_variants' — callable(anim_type) -> list[dict]
                'set_animation_fps' — callable(name: str, fps: int | None)
                    Sets a per-animation FPS override; pass None to clear.
        """
        self._root = root
        self._callbacks = callbacks

        # Build the window
        self._window = tk.Toplevel(root)
        self._window.title('Whiskers — Settings')
        self._window.geometry('540x700')
        self._window.resizable(False, True)
        self._window.protocol('WM_DELETE_WINDOW', self._on_close)

        # Don't show on creation — wait for explicit show()
        self._window.withdraw()

        # Scrollable container
        self._outer_frame = ttk.Frame(self._window)
        self._outer_frame.pack(fill=tk.BOTH, expand=True)

        self._canvas = tk.Canvas(self._outer_frame, highlightthickness=0)
        self._scrollbar = ttk.Scrollbar(self._outer_frame, orient=tk.VERTICAL,
                                        command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._scrollbar.set)

        self._scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._scroll_frame = ttk.Frame(self._canvas)
        self._canvas_window = self._canvas.create_window((0, 0), window=self._scroll_frame,
                                                          anchor='nw')

        def _on_scroll_configure(event):
            self._canvas.configure(scrollregion=self._canvas.bbox('all'))

        def _on_canvas_configure(event):
            self._canvas.itemconfig(self._canvas_window, width=event.width)

        self._scroll_frame.bind('<Configure>', _on_scroll_configure)
        self._canvas.bind('<Configure>', _on_canvas_configure)

        # Mouse wheel scrolling — bind only when pointer is over the panel,
        # otherwise the global bind_all() hijacks scroll events for every
        # other Toplevel (cat window, speech bubble) in this process.
        def _on_mousewheel(event):
            self._canvas.yview_scroll(int(-1 * (event.delta / 120)), 'units')

        def _bind_wheel_on_enter(_event):
            self._canvas.bind_all('<MouseWheel>', _on_mousewheel)

        def _unbind_wheel_on_leave(_event):
            try:
                self._canvas.unbind_all('<MouseWheel>')
            except Exception:
                pass

        self._canvas.bind('<Enter>', _bind_wheel_on_enter)
        self._canvas.bind('<Leave>', _unbind_wheel_on_leave)

        # Initialize lazy-created attributes up front so cleanup code is simpler.
        self._mic_auto_stop_id = None

        self._build_ui()

    def _build_ui(self):
        """Construct all UI elements."""
        main = ttk.Frame(self._scroll_frame, padding=10)
        main.pack(fill=tk.BOTH, expand=True)

        # --- Start / Stop section ---
        ctrl_frame = ttk.LabelFrame(main, text='Cat Control', padding=8)
        ctrl_frame.pack(fill=tk.X, pady=(0, 10))

        self._start_btn = ttk.Button(ctrl_frame, text='Start Cat',
                                     command=self._on_start)
        self._start_btn.pack(side=tk.LEFT, padx=(0, 8))

        self._stop_btn = ttk.Button(ctrl_frame, text='Stop Cat',
                                    command=self._on_stop)
        self._stop_btn.pack(side=tk.LEFT)

        # --- Personality section ---
        persona_frame = ttk.LabelFrame(main, text='Personality', padding=8)
        persona_frame.pack(fill=tk.X, pady=(0, 10))

        settings = config.load_user_settings()
        current_mode = settings.get('personality_mode', 'companion')

        self._mode_var = tk.StringVar(value=current_mode)

        self._companion_rb = ttk.Radiobutton(
            persona_frame, text='Companion — playful, sarcastic, talks about anything',
            variable=self._mode_var, value='companion',
            command=self._on_mode_changed
        )
        self._companion_rb.pack(anchor=tk.W, pady=(0, 4))

        self._teacher_rb = ttk.Radiobutton(
            persona_frame, text='Teacher — focused tutor, guides with questions',
            variable=self._mode_var, value='teacher',
            command=self._on_mode_changed
        )
        self._teacher_rb.pack(anchor=tk.W)

        # --- AI Provider section ---
        provider_frame = ttk.LabelFrame(main, text='AI Provider', padding=8)
        provider_frame.pack(fill=tk.X, pady=(0, 10))

        prov_row = ttk.Frame(provider_frame)
        prov_row.pack(fill=tk.X, pady=(0, 6))

        ttk.Label(prov_row, text='Provider:').pack(side=tk.LEFT, padx=(0, 8))

        provider_ids = [p[0] for p in config.AI_PROVIDERS]

        current_provider = settings.get('ai_provider', 'ollama')
        self._provider_var = tk.StringVar(value=current_provider)
        self._provider_combo = ttk.Combobox(
            prov_row,
            textvariable=self._provider_var,
            values=provider_ids,
            state='readonly',
            width=22
        )
        self._provider_combo.pack(side=tk.LEFT, padx=(0, 8))
        self._provider_combo.bind('<<ComboboxSelected>>', self._on_provider_changed)

        self._provider_label = ttk.Label(prov_row, text='')
        self._provider_label.pack(side=tk.LEFT)
        self._update_provider_label()

        # API key row (hidden for Ollama)
        self._key_row = ttk.Frame(provider_frame)
        self._key_row.pack(fill=tk.X, pady=(0, 4))

        ttk.Label(self._key_row, text='API Key:').pack(side=tk.LEFT, padx=(0, 8))

        self._api_key_var = tk.StringVar()
        self._api_key_entry = ttk.Entry(self._key_row, textvariable=self._api_key_var,
                                        width=36, show='*')
        self._api_key_entry.pack(side=tk.LEFT, padx=(0, 8))
        self._api_key_entry.bind('<FocusOut>', self._on_api_key_changed)
        self._api_key_entry.bind('<Return>', self._on_api_key_changed)

        self._load_api_key_for_provider(current_provider)
        self._toggle_key_row(current_provider)

        # --- Voice section ---
        voice_frame = ttk.LabelFrame(main, text='Voice', padding=8)
        voice_frame.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(voice_frame, text='TTS Voice:').pack(side=tk.LEFT, padx=(0, 8))

        voice_ids = [v[0] for v in config.KOKORO_VOICES]
        voice_labels = [v[1] for v in config.KOKORO_VOICES]

        self._voice_var = tk.StringVar(value=voice_output.get_voice())
        self._voice_combo = ttk.Combobox(
            voice_frame,
            textvariable=self._voice_var,
            values=voice_ids,
            state='readonly',
            width=20
        )
        self._voice_combo.pack(side=tk.LEFT, padx=(0, 8))
        self._voice_combo.bind('<<ComboboxSelected>>', self._on_voice_changed)

        self._test_btn = ttk.Button(voice_frame, text='Test', width=6,
                                    command=self._on_test_voice)
        self._test_btn.pack(side=tk.LEFT, padx=(0, 8))

        # Display label for the selected voice
        self._voice_label = ttk.Label(voice_frame, text='')
        self._voice_label.pack(side=tk.LEFT)
        self._update_voice_label()

        # --- Microphone test section ---
        mic_frame = ttk.LabelFrame(main, text='Microphone', padding=8)
        mic_frame.pack(fill=tk.X, pady=(0, 10))

        # Input device selector row
        dev_row = ttk.Frame(mic_frame)
        dev_row.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(dev_row, text='Input Device:').pack(side=tk.LEFT, padx=(0, 6))

        self._mic_devices = self._list_input_devices()  # [(index_or_None, label), ...]
        self._mic_device_var = tk.StringVar()
        self._mic_device_combo = ttk.Combobox(
            dev_row, textvariable=self._mic_device_var,
            values=[label for _, label in self._mic_devices],
            state='readonly', width=42,
        )
        self._mic_device_combo.pack(side=tk.LEFT)
        self._mic_device_combo.bind('<<ComboboxSelected>>', self._on_mic_device_changed)

        ttk.Button(dev_row, text='Refresh',
                   command=self._refresh_mic_devices).pack(side=tk.LEFT, padx=(6, 0))

        # Test button + status row
        test_row = ttk.Frame(mic_frame)
        test_row.pack(fill=tk.X)

        self._mic_test_btn = ttk.Button(test_row, text='Test Microphone',
                                        command=self._on_test_mic)
        self._mic_test_btn.pack(side=tk.LEFT, padx=(0, 8))

        self._mic_status_label = ttk.Label(test_row, text='')
        self._mic_status_label.pack(side=tk.LEFT)

        # Level bar to show live audio level
        self._mic_level = ttk.Progressbar(test_row, orient=tk.HORIZONTAL,
                                          length=150, mode='determinate',
                                          maximum=100)
        self._mic_level.pack(side=tk.LEFT, padx=(8, 0))

        self._mic_testing = False
        self._mic_stream = None
        self._mic_auto_stop_id = None

        # Populate the dropdown from the persisted setting
        self._sync_mic_device_selection()

        # If sounddevice couldn't load at all, there's no point letting the
        # user click Test — surface the package-install hint up front.
        if _sd is None:
            self._mic_test_btn.config(state='disabled')
            self._mic_device_combo.config(state='disabled')
            self._mic_status_label.config(
                text='Python package missing — run: pip install sounddevice',
                foreground='red')

        # --- Wake Word section ---
        wake_frame = ttk.LabelFrame(main, text='Wake Word', padding=8)
        wake_frame.pack(fill=tk.X, pady=(0, 10))

        # Model dropdown row
        model_row = ttk.Frame(wake_frame)
        model_row.pack(fill=tk.X, pady=(0, 6))

        ttk.Label(model_row, text='Wake Model:').pack(side=tk.LEFT, padx=(0, 8))

        model_ids = [m[0] for m in config.WAKE_WORD_MODELS]

        settings = config.load_user_settings()
        current_model = settings.get('wake_word_model', 'hey_jarvis_v0.1')

        self._wake_model_var = tk.StringVar(value=current_model)
        self._wake_model_combo = ttk.Combobox(
            model_row,
            textvariable=self._wake_model_var,
            values=model_ids,
            state='readonly',
            width=22
        )
        self._wake_model_combo.pack(side=tk.LEFT, padx=(0, 8))
        self._wake_model_combo.bind('<<ComboboxSelected>>', self._on_wake_model_changed)

        self._wake_phrase_label = ttk.Label(model_row, text='')
        self._wake_phrase_label.pack(side=tk.LEFT)
        self._update_wake_phrase_label()

        ttk.Label(wake_frame, text='(Change takes effect on next Start/Stop cycle)',
                  foreground='gray').pack(anchor=tk.W, pady=(0, 6))

        # Sensitivity slider row
        sens_row = ttk.Frame(wake_frame)
        sens_row.pack(fill=tk.X, pady=(0, 6))

        ttk.Label(sens_row, text='Sensitivity:').pack(side=tk.LEFT, padx=(0, 8))

        current_sensitivity = settings.get('wake_word_sensitivity', config.WAKE_WORD_SENSITIVITY)
        self._sensitivity_var = tk.DoubleVar(value=current_sensitivity)
        self._sensitivity_scale = ttk.Scale(
            sens_row,
            from_=0.0, to=1.0,
            variable=self._sensitivity_var,
            orient=tk.HORIZONTAL,
            length=200,
            command=self._on_sensitivity_changed
        )
        self._sensitivity_scale.pack(side=tk.LEFT, padx=(0, 8))

        self._sensitivity_label = ttk.Label(sens_row, text=f'{current_sensitivity:.2f}')
        self._sensitivity_label.pack(side=tk.LEFT)

        # Quiet words row
        quiet_row = ttk.Frame(wake_frame)
        quiet_row.pack(fill=tk.X, pady=(0, 4))

        ttk.Label(quiet_row, text='Quiet Words:').pack(side=tk.LEFT, padx=(0, 8))

        current_quiet = settings.get('quiet_words', config.QUIET_WORDS)
        self._quiet_words_var = tk.StringVar(value=', '.join(current_quiet))
        quiet_entry = ttk.Entry(quiet_row, textvariable=self._quiet_words_var, width=32)
        quiet_entry.pack(side=tk.LEFT, padx=(0, 8))
        quiet_entry.bind('<FocusOut>', self._on_quiet_words_changed)
        quiet_entry.bind('<Return>', self._on_quiet_words_changed)

        ttk.Label(wake_frame, text='Comma-separated phrases to end a conversation',
                  foreground='gray').pack(anchor=tk.W)

        # --- Animation Manager section ---
        anim_frame = ttk.LabelFrame(main, text='Animations', padding=8)
        anim_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        # Treeview showing animation types and their variants
        tree_frame = ttk.Frame(anim_frame)
        tree_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        self._tree = ttk.Treeview(tree_frame, columns=('count',), height=12)
        self._tree.heading('#0', text='Animation Type / Variant')
        self._tree.heading('count', text='Variants')
        self._tree.column('#0', width=320)
        self._tree.column('count', width=80, anchor=tk.CENTER)

        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL,
                                  command=self._tree.yview)
        self._tree.configure(yscrollcommand=scrollbar.set)

        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Buttons below the tree
        btn_frame = ttk.Frame(anim_frame)
        btn_frame.pack(fill=tk.X)

        self._add_btn = ttk.Button(btn_frame, text='Add Animation',
                                   command=self._on_add_animation)
        self._add_btn.pack(side=tk.LEFT, padx=(0, 8))

        self._remove_btn = ttk.Button(btn_frame, text='Remove Selected',
                                      command=self._on_remove_animation)
        self._remove_btn.pack(side=tk.LEFT, padx=(0, 8))

        self._edit_fps_btn = ttk.Button(btn_frame, text='Edit FPS…',
                                        command=self._on_edit_variant_fps)
        self._edit_fps_btn.pack(side=tk.LEFT, padx=(0, 8))

        self._import_sheet_btn = ttk.Button(btn_frame, text='Import Sprite Sheet…',
                                            command=self._on_import_sprite_sheet)
        self._import_sheet_btn.pack(side=tk.LEFT)

        # --- Transitions section ---
        trans_frame = ttk.LabelFrame(main, text='Transition Animations (between states)', padding=8)
        trans_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        trans_tree_frame = ttk.Frame(trans_frame)
        trans_tree_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        self._trans_tree = ttk.Treeview(trans_tree_frame, columns=('count',), height=8)
        self._trans_tree.heading('#0', text='Transition / Variant')
        self._trans_tree.heading('count', text='Variants')
        self._trans_tree.column('#0', width=320)
        self._trans_tree.column('count', width=80, anchor=tk.CENTER)

        trans_scrollbar = ttk.Scrollbar(trans_tree_frame, orient=tk.VERTICAL,
                                        command=self._trans_tree.yview)
        self._trans_tree.configure(yscrollcommand=trans_scrollbar.set)

        self._trans_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        trans_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        trans_btn_frame = ttk.Frame(trans_frame)
        trans_btn_frame.pack(fill=tk.X)

        ttk.Button(trans_btn_frame, text='Add Transition',
                   command=self._on_add_transition).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(trans_btn_frame, text='Remove Selected',
                   command=self._on_remove_transition).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(trans_btn_frame, text='Edit FPS…',
                   command=self._on_edit_transition_variant_fps).pack(side=tk.LEFT)

        # --- Frame Speeds section ---
        fps_frame = ttk.LabelFrame(main, text='Frame Speeds', padding=8)
        fps_frame.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(fps_frame,
                  text=f'Per-animation FPS (default {config.CAT_FPS}). Range 1–60.',
                  foreground='gray').pack(anchor=tk.W, pady=(0, 6))

        overrides = config.get_setting('animation_fps', {}) or {}
        self._anim_fps_vars = {}  # name -> tk.IntVar

        rows_frame = ttk.Frame(fps_frame)
        rows_frame.pack(fill=tk.X)

        # Two columns for compactness: base animations on the left, transitions on the right
        left_col = ttk.Frame(rows_frame)
        left_col.pack(side=tk.LEFT, anchor=tk.N, fill=tk.X, expand=True, padx=(0, 12))
        right_col = ttk.Frame(rows_frame)
        right_col.pack(side=tk.LEFT, anchor=tk.N, fill=tk.X, expand=True)

        ttk.Label(left_col, text='Animations', font=('TkDefaultFont', 9, 'bold')) \
            .pack(anchor=tk.W, pady=(0, 4))
        for name in ANIMATION_TYPES:
            self._build_fps_row(left_col, name, overrides)

        ttk.Label(right_col, text='Transitions', font=('TkDefaultFont', 9, 'bold')) \
            .pack(anchor=tk.W, pady=(0, 4))
        for name in TRANSITION_KEYS:
            self._build_fps_row(right_col, name, overrides)

    def _build_fps_row(self, parent, name, overrides):
        """Create one row: label + spinbox + 'fps' + Reset button."""
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=1)

        ttk.Label(row, text=name, width=18, anchor=tk.W).pack(side=tk.LEFT)

        current = overrides.get(name, config.CAT_FPS)
        try:
            current = int(current)
        except (TypeError, ValueError):
            current = config.CAT_FPS
        var = tk.IntVar(value=current)
        self._anim_fps_vars[name] = var

        spin = ttk.Spinbox(row, from_=1, to=60, width=4, textvariable=var,
                           command=lambda n=name: self._on_fps_changed(n))
        spin.pack(side=tk.LEFT, padx=(0, 4))
        spin.bind('<Return>', lambda _e, n=name: self._on_fps_changed(n))
        spin.bind('<FocusOut>', lambda _e, n=name: self._on_fps_changed(n))

        ttk.Label(row, text='fps', foreground='gray').pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(row, text='Reset', width=6,
                   command=lambda n=name: self._on_fps_reset(n)).pack(side=tk.LEFT)

    def _on_fps_changed(self, name):
        """User edited a frame-speed spinbox. Clamp, push to CatWindow, persist."""
        var = self._anim_fps_vars.get(name)
        if var is None:
            return
        try:
            fps = int(var.get())
        except (tk.TclError, ValueError):
            # Invalid entry — restore previous persisted value
            overrides = config.get_setting('animation_fps', {}) or {}
            var.set(int(overrides.get(name, config.CAT_FPS)))
            return
        fps = max(1, min(60, fps))
        if fps != var.get():
            var.set(fps)  # reflect the clamp back to the UI
        cb = self._callbacks.get('set_animation_fps')
        if cb:
            cb(name, fps)

    def _on_fps_reset(self, name):
        """Clear a per-animation override so it inherits config.CAT_FPS."""
        cb = self._callbacks.get('set_animation_fps')
        if cb:
            cb(name, None)
        var = self._anim_fps_vars.get(name)
        if var is not None:
            var.set(config.CAT_FPS)

    def _on_close(self):
        """Hide window on close instead of destroying it."""
        if self._mic_testing:
            self._stop_mic_test()
        self._window.withdraw()

    def show(self):
        """Show the control panel window and refresh data."""
        self.refresh_animation_list()
        self.refresh_transition_list()
        self._voice_var.set(voice_output.get_voice())
        self._update_voice_label()
        # Refresh personality and wake word settings
        settings = config.load_user_settings()
        self._mode_var.set(settings.get('personality_mode', 'companion'))
        current_provider = settings.get('ai_provider', 'ollama')
        self._provider_var.set(current_provider)
        self._update_provider_label()
        self._load_api_key_for_provider(current_provider)
        self._toggle_key_row(current_provider)
        self._wake_model_var.set(settings.get('wake_word_model', 'hey_jarvis_v0.1'))
        self._update_wake_phrase_label()
        self._sensitivity_var.set(settings.get('wake_word_sensitivity', config.WAKE_WORD_SENSITIVITY))
        self._sensitivity_label.config(text=f'{self._sensitivity_var.get():.2f}')
        current_quiet = settings.get('quiet_words', config.QUIET_WORDS)
        self._quiet_words_var.set(', '.join(current_quiet))
        # Sync mic device dropdown with persisted setting
        self._sync_mic_device_selection()
        # Sync frame-speed spinboxes with persisted overrides (in case they
        # were changed outside the panel, e.g. via a settings-file edit)
        fps_overrides = settings.get('animation_fps', {}) or {}
        for name, var in getattr(self, '_anim_fps_vars', {}).items():
            try:
                var.set(int(fps_overrides.get(name, config.CAT_FPS)))
            except (TypeError, ValueError):
                var.set(config.CAT_FPS)
        self._window.deiconify()
        self._window.lift()
        self._window.focus_force()

    def hide(self):
        """Hide the control panel window."""
        self._window.withdraw()

    # --- Callbacks ---

    def _on_start(self):
        if 'start' in self._callbacks:
            self._callbacks['start']()

    def _on_stop(self):
        if 'stop' in self._callbacks:
            self._callbacks['stop']()

    def _on_mode_changed(self):
        mode = self._mode_var.get()
        if 'change_mode' in self._callbacks:
            self._callbacks['change_mode'](mode)

    def _on_provider_changed(self, event=None):
        provider = self._provider_var.get()
        self._update_provider_label()
        self._load_api_key_for_provider(provider)
        self._toggle_key_row(provider)
        if 'change_provider' in self._callbacks:
            self._callbacks['change_provider'](provider)

    def _update_provider_label(self):
        current = self._provider_var.get()
        for pid, plabel in config.AI_PROVIDERS:
            if pid == current:
                self._provider_label.config(text=f'  ({plabel})')
                return
        self._provider_label.config(text='')

    def _toggle_key_row(self, provider):
        """Show/hide the API key row based on provider."""
        if provider == 'ollama':
            self._key_row.pack_forget()
        else:
            self._key_row.pack(fill=tk.X, pady=(0, 4))

    def _load_api_key_for_provider(self, provider):
        """Load the saved API key for the given provider."""
        if provider == 'openai':
            key = config.get_setting('openai_api_key', '')
        elif provider == 'anthropic':
            key = config.get_setting('anthropic_api_key', '')
        else:
            key = ''
        self._api_key_var.set(key)

    def _on_api_key_changed(self, event=None):
        provider = self._provider_var.get()
        key = self._api_key_var.get().strip()
        if provider == 'openai':
            config.set_setting('openai_api_key', key)
        elif provider == 'anthropic':
            config.set_setting('anthropic_api_key', key)

    def _on_voice_changed(self, event=None):
        voice_name = self._voice_var.get()
        self._update_voice_label()
        if 'change_voice' in self._callbacks:
            self._callbacks['change_voice'](voice_name)

    def _on_test_voice(self):
        """Play a short sample with the currently selected voice."""
        voice_name = self._voice_var.get()
        # Temporarily switch to the selected voice, speak a sample, then restore
        previous = voice_output.get_voice()
        voice_output.set_voice(voice_name)
        voice_output.speak("Hi there! I'm your AI companion. How does this voice sound?")
        voice_output.set_voice(previous)

    def _list_input_devices(self):
        """Return [(device_index_or_None, display_label), ...] for all input devices.

        Dropdown is self-describing when audio is unusable: a missing package
        or empty enumeration shows a diagnostic entry instead of a bare
        'System default' that the user might click optimistically.
        """
        if _sd is None:
            return [(None, 'sounddevice not installed')]
        out = [(None, 'System default')]
        try:
            for i, d in enumerate(_sd.query_devices()):
                if d.get('max_input_channels', 0) > 0:
                    out.append(
                        (i, f"{i}: {d['name']} ({d['max_input_channels']} ch)"))
        except Exception as e:
            print(f'[WARNING] Could not list input devices: {e}')
            return [(None, 'No input devices detected')]
        if len(out) == 1:
            return [(None, 'No input devices detected')]
        return out

    def _refresh_mic_devices(self):
        """Re-enumerate input devices (e.g. after plugging in a USB mic)."""
        self._mic_devices = self._list_input_devices()
        self._mic_device_combo['values'] = [label for _, label in self._mic_devices]
        self._sync_mic_device_selection()

    def _sync_mic_device_selection(self):
        """Match combobox text to the persisted input_device_index."""
        current = config.get_setting('input_device_index', None)
        for i, (idx, _label) in enumerate(self._mic_devices):
            if idx == current:
                self._mic_device_combo.current(i)
                return
        # Configured index not present — show "System default" and warn
        if self._mic_devices:
            self._mic_device_combo.current(0)
        if current is not None:
            self._mic_status_label.config(
                text=f'Saved device #{current} not found — using default',
                foreground='orange')

    def _on_mic_device_changed(self, _event=None):
        """User picked a different input device from the dropdown."""
        i = self._mic_device_combo.current()
        if i < 0 or i >= len(self._mic_devices):
            return
        idx, _label = self._mic_devices[i]
        cb = self._callbacks.get('change_mic_device')
        if cb:
            cb(idx)

    def _on_test_mic(self):
        """Toggle a 10-second microphone test with live level display."""
        if self._mic_testing:
            self._stop_mic_test()
            return

        if _sd is None:
            self._mic_status_label.config(
                text='Python package missing — run: pip install sounddevice',
                foreground='red')
            return

        device_index = config.get_setting('input_device_index')

        try:
            if device_index is None:
                dev = _sd.query_devices(kind='input')
            else:
                dev = _sd.query_devices(int(device_index))
            dev_name = dev['name']
        except Exception as e:
            # Figure out *why* the query failed to give actionable guidance.
            has_any_input = False
            try:
                for d in _sd.query_devices():
                    if d.get('max_input_channels', 0) > 0:
                        has_any_input = True
                        break
            except Exception:
                pass
            self._mic_status_label.config(
                text=_classify_mic_error(e, has_any_input, device_index),
                foreground='red')
            self._mic_level['value'] = 0
            return

        self._mic_testing = True
        self._mic_test_btn.config(text='Stop')
        self._mic_status_label.config(text=f'Listening... ({dev_name})', foreground='green')

        import numpy as _np

        def audio_callback(indata, frames, time_info, status):
            rms = float(_np.sqrt(_np.mean(indata ** 2)))
            level = min(100, int(rms * 1000))
            try:
                self._root.after(0, lambda l=level: self._mic_level.configure(value=l))
            except Exception:
                pass

        # Use the device's native sample rate. WASAPI (and some WDM-KS) entries
        # reject arbitrary rates with PaErrorCode -9997 — every device accepts
        # its default_samplerate, so we follow that and derive blocksize from it.
        samplerate = int(dev.get('default_samplerate') or 16000)
        blocksize = max(1, samplerate // 10)  # ~100 ms callback cadence

        try:
            stream_kwargs = dict(
                samplerate=samplerate, channels=1, dtype='float32',
                blocksize=blocksize, callback=audio_callback,
            )
            if device_index is not None:
                stream_kwargs['device'] = int(device_index)
            self._mic_stream = _sd.InputStream(**stream_kwargs)
            self._mic_stream.start()
        except Exception as e:
            self._mic_status_label.config(
                text=f'Could not open {dev_name}: {e}. It may be in use by another app.',
                foreground='red')
            self._mic_testing = False
            self._mic_test_btn.config(text='Test Microphone')
            return

        # Auto-stop after 10 seconds
        self._mic_auto_stop_id = self._root.after(10000, self._stop_mic_test)

    def _stop_mic_test(self):
        """Stop the microphone test."""
        self._mic_testing = False
        self._mic_test_btn.config(text='Test Microphone')

        if self._mic_auto_stop_id:
            try:
                self._root.after_cancel(self._mic_auto_stop_id)
            except Exception:
                pass
            self._mic_auto_stop_id = None

        if self._mic_stream:
            try:
                self._mic_stream.stop()
                self._mic_stream.close()
            except Exception:
                pass
            self._mic_stream = None

        self._mic_level['value'] = 0
        self._mic_status_label.config(text='Test complete', foreground='')

    def _update_voice_label(self):
        """Update the display label next to the voice combobox."""
        current = self._voice_var.get()
        for vid, vlabel in config.KOKORO_VOICES:
            if vid == current:
                self._voice_label.config(text=f'  ({vlabel})')
                return
        self._voice_label.config(text='')

    # --- Wake word callbacks ---

    def _on_wake_model_changed(self, event=None):
        model_id = self._wake_model_var.get()
        self._update_wake_phrase_label()
        if 'change_wake_word' in self._callbacks:
            self._callbacks['change_wake_word'](model_id)

    def _update_wake_phrase_label(self):
        current = self._wake_model_var.get()
        for mid, phrase in config.WAKE_WORD_MODELS:
            if mid == current:
                self._wake_phrase_label.config(text=f'  Say: "{phrase}"')
                return
        self._wake_phrase_label.config(text='')

    def _on_sensitivity_changed(self, value=None):
        val = self._sensitivity_var.get()
        self._sensitivity_label.config(text=f'{val:.2f}')
        if 'change_sensitivity' in self._callbacks:
            self._callbacks['change_sensitivity'](val)

    def _on_quiet_words_changed(self, event=None):
        text = self._quiet_words_var.get()
        if 'change_quiet_words' in self._callbacks:
            self._callbacks['change_quiet_words'](text)

    def _on_add_animation(self):
        """Open dialog to add a new animation variant (folder of frames OR single GIF)."""
        dialog = tk.Toplevel(self._window)
        dialog.title('Add Animation')
        dialog.geometry('420x280')
        dialog.resizable(False, False)
        dialog.transient(self._window)
        dialog.grab_set()

        frame = ttk.Frame(dialog, padding=15)
        frame.pack(fill=tk.BOTH, expand=True)

        # Animation type
        ttk.Label(frame, text='Animation Type:').grid(row=0, column=0, sticky=tk.W, pady=4)
        type_var = tk.StringVar(value=ANIMATION_TYPES[0])
        ttk.Combobox(frame, textvariable=type_var, values=ANIMATION_TYPES,
                     state='readonly', width=18) \
            .grid(row=0, column=1, sticky=tk.W, pady=4, padx=(8, 0), columnspan=2)

        # Variant name
        ttk.Label(frame, text='Variant Name:').grid(row=1, column=0, sticky=tk.W, pady=4)
        name_var = tk.StringVar()
        ttk.Entry(frame, textvariable=name_var, width=20) \
            .grid(row=1, column=1, sticky=tk.W, pady=4, padx=(8, 0), columnspan=2)

        # Source radio
        ttk.Label(frame, text='Source:').grid(row=2, column=0, sticky=tk.W, pady=4)
        source_var = tk.StringVar(value='folder')
        radio_frame = ttk.Frame(frame)
        radio_frame.grid(row=2, column=1, sticky=tk.W, pady=4, padx=(8, 0), columnspan=2)
        ttk.Radiobutton(radio_frame, text='Folder of frames', variable=source_var,
                        value='folder').pack(side=tk.LEFT)
        ttk.Radiobutton(radio_frame, text='Single GIF file', variable=source_var,
                        value='gif').pack(side=tk.LEFT, padx=(10, 0))

        # Path row
        path_label = ttk.Label(frame, text='Sprite Folder:')
        path_label.grid(row=3, column=0, sticky=tk.W, pady=4)
        path_var = tk.StringVar()
        ttk.Entry(frame, textvariable=path_var, width=28, state='readonly') \
            .grid(row=3, column=1, sticky=tk.W, pady=4, padx=(8, 0))

        # GIF metadata (shown only when a multi-frame GIF is selected)
        frames_label = ttk.Label(frame, text='', foreground='gray')
        frames_label.grid(row=4, column=1, sticky=tk.W, padx=(8, 0))
        fps_label = ttk.Label(frame, text='FPS:')
        fps_var = tk.IntVar(value=config.CAT_FPS)
        fps_spin = ttk.Spinbox(frame, from_=1, to=60, width=4, textvariable=fps_var)
        fps_hint = ttk.Label(frame, text='fps', foreground='gray')
        fps_label.grid(row=5, column=0, sticky=tk.W, pady=4)
        fps_spin.grid(row=5, column=1, sticky=tk.W, pady=4, padx=(8, 0))
        fps_hint.grid(row=5, column=2, sticky=tk.W, pady=4)
        fps_label.grid_remove()
        fps_spin.grid_remove()
        fps_hint.grid_remove()

        # State carried back into submit()
        gif_meta = {'frame_count': 0}

        def refresh_gif_meta():
            """Inspect the currently-picked GIF and show/hide the FPS row."""
            gif_meta['frame_count'] = 0
            frames_label.configure(text='')
            fps_label.grid_remove()
            fps_spin.grid_remove()
            fps_hint.grid_remove()
            if source_var.get() != 'gif':
                return
            p = path_var.get().strip()
            if not p or not os.path.isfile(p):
                return
            count, hint = inspect_gif(p)
            gif_meta['frame_count'] = count
            if count <= 0:
                frames_label.configure(text='(could not read GIF)')
                return
            frames_label.configure(text=f'Frames: {count}')
            if count >= 2:
                fps_var.set(int(hint))
                fps_label.grid()
                fps_spin.grid()
                fps_hint.grid()

        def browse():
            if source_var.get() == 'gif':
                picked = filedialog.askopenfilename(
                    title='Select a GIF file',
                    parent=dialog,
                    filetypes=[('GIF images', '*.gif')],
                )
            else:
                picked = filedialog.askdirectory(
                    title='Select folder with sprite frames',
                    parent=dialog,
                )
            if picked:
                path_var.set(picked)
                refresh_gif_meta()

        ttk.Button(frame, text='Browse...', command=browse) \
            .grid(row=3, column=2, padx=(4, 0), pady=4)

        def on_source_changed(*_):
            path_var.set('')
            if source_var.get() == 'gif':
                path_label.configure(text='GIF File:')
            else:
                path_label.configure(text='Sprite Folder:')
            refresh_gif_meta()

        source_var.trace_add('write', on_source_changed)

        def submit():
            anim_type = type_var.get()
            variant_name = name_var.get().strip()
            source_path = path_var.get().strip()
            source_type = source_var.get()

            if not variant_name:
                messagebox.showwarning('Missing Name', 'Please enter a variant name.',
                                       parent=dialog)
                return

            try:
                if source_type == 'gif':
                    if not source_path or not os.path.isfile(source_path):
                        messagebox.showwarning('Missing GIF',
                                               'Please select a valid GIF file.',
                                               parent=dialog)
                        return
                    fps = int(fps_var.get()) if gif_meta['frame_count'] >= 2 else None
                    cb = self._callbacks.get('add_gif_animation')
                    if cb is None:
                        raise RuntimeError('add_gif_animation callback not wired')
                    cb(anim_type, variant_name, source_path, fps)
                else:
                    if not source_path or not os.path.isdir(source_path):
                        messagebox.showwarning('Missing Folder',
                                               'Please select a valid sprite folder.',
                                               parent=dialog)
                        return
                    if 'add_animation' in self._callbacks:
                        self._callbacks['add_animation'](anim_type, variant_name, source_path)
                self.refresh_animation_list()
                dialog.destroy()
            except Exception as e:
                messagebox.showerror('Error', f'Failed to add animation:\n{e}',
                                     parent=dialog)

        ttk.Button(frame, text='Add', command=submit).grid(
            row=6, column=0, columnspan=3, pady=(12, 0))

    def _on_edit_variant_fps(self):
        """Open dialog to edit the per-variant FPS of the selected animation variant."""
        selected = self._tree.selection()
        if not selected:
            messagebox.showinfo('No Selection', 'Select a variant to edit.',
                                parent=self._window)
            return
        item_id = selected[0]
        parent_id = self._tree.parent(item_id)
        if not parent_id:
            messagebox.showinfo('Select Variant',
                                'Please select a specific variant, not an animation type.',
                                parent=self._window)
            return
        anim_type = self._tree.item(parent_id, 'text').lower()
        variant_name = self._tree.item(item_id, 'text')
        self._open_edit_fps_dialog(anim_type, variant_name, is_transition=False)

    def _on_edit_transition_variant_fps(self):
        """Open dialog to edit the per-variant FPS of the selected transition variant."""
        selected = self._trans_tree.selection()
        if not selected:
            messagebox.showinfo('No Selection', 'Select a transition variant to edit.',
                                parent=self._window)
            return
        item_id = selected[0]
        parent_id = self._trans_tree.parent(item_id)
        if not parent_id:
            messagebox.showinfo('Select Variant',
                                'Please select a specific variant, not a transition key.',
                                parent=self._window)
            return
        transition_key = self._trans_tree.item(parent_id, 'text')
        variant_name = self._trans_tree.item(item_id, 'text')
        self._open_edit_fps_dialog(transition_key, variant_name, is_transition=True)

    def _open_edit_fps_dialog(self, group_key, variant_name, is_transition):
        """Small modal to set or clear the 'fps' override on a single variant."""
        # Look up the current fps (if any) for display.
        current_fps = None
        get_variants = (self._callbacks.get('get_transition_variants')
                        if is_transition
                        else self._callbacks.get('get_anim_variants'))
        if get_variants is not None:
            for v in get_variants(group_key) or []:
                if v.get('name') == variant_name:
                    current_fps = v.get('fps')
                    break

        dialog = tk.Toplevel(self._window)
        dialog.title('Edit Variant FPS')
        dialog.geometry('340x160')
        dialog.resizable(False, False)
        dialog.transient(self._window)
        dialog.grab_set()

        frame = ttk.Frame(dialog, padding=15)
        frame.pack(fill=tk.BOTH, expand=True)

        kind = 'Transition' if is_transition else 'Animation'
        ttk.Label(frame, text=f'{kind}: {group_key}').pack(anchor=tk.W)
        ttk.Label(frame, text=f'Variant: {variant_name}').pack(anchor=tk.W, pady=(0, 6))

        inherited_note = (f'(inherits {config.CAT_FPS} fps)' if current_fps is None
                          else f'(currently {current_fps} fps)')
        ttk.Label(frame, text=inherited_note, foreground='gray').pack(anchor=tk.W)

        row = ttk.Frame(frame)
        row.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(row, text='FPS:').pack(side=tk.LEFT)
        fps_var = tk.IntVar(value=int(current_fps) if isinstance(current_fps, int)
                            else config.CAT_FPS)
        ttk.Spinbox(row, from_=1, to=60, width=4, textvariable=fps_var) \
            .pack(side=tk.LEFT, padx=(6, 0))

        btns = ttk.Frame(frame)
        btns.pack(fill=tk.X, pady=(12, 0))

        def apply_fps(value):
            cb = self._callbacks.get('update_variant_fps')
            if cb is None:
                messagebox.showerror('Not wired',
                                     'update_variant_fps callback is missing.',
                                     parent=dialog)
                return
            try:
                cb(group_key, variant_name, value, is_transition)
                dialog.destroy()
            except Exception as e:
                messagebox.showerror('Error', f'Failed to update FPS:\n{e}',
                                     parent=dialog)

        ttk.Button(btns, text='OK',
                   command=lambda: apply_fps(int(fps_var.get()))) \
            .pack(side=tk.LEFT)
        ttk.Button(btns, text='Clear override',
                   command=lambda: apply_fps(None)) \
            .pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(btns, text='Cancel',
                   command=dialog.destroy).pack(side=tk.RIGHT)

    def _on_remove_animation(self):
        """Remove the selected variant from the treeview."""
        selected = self._tree.selection()
        if not selected:
            messagebox.showinfo('No Selection', 'Select a variant to remove.',
                                parent=self._window)
            return

        item_id = selected[0]
        parent_id = self._tree.parent(item_id)

        if not parent_id:
            # Selected a type header, not a variant
            messagebox.showinfo('Select Variant',
                                'Please select a specific variant, not an animation type.',
                                parent=self._window)
            return

        anim_type = self._tree.item(parent_id, 'text').lower()
        variant_name = self._tree.item(item_id, 'text')

        if messagebox.askyesno('Confirm Remove',
                               f'Remove "{variant_name}" from {anim_type}?',
                               parent=self._window):
            if 'remove_animation' in self._callbacks:
                self._callbacks['remove_animation'](anim_type, variant_name)
            self.refresh_animation_list()

    def _on_import_sprite_sheet(self):
        """Open the visual sprite-sheet importer dialog."""
        get_manager = self._callbacks.get('get_anim_manager')
        if not get_manager:
            messagebox.showerror('Importer unavailable',
                                 'Animation manager is not wired up.',
                                 parent=self._window)
            return

        anim_manager = get_manager()
        if anim_manager is None:
            messagebox.showerror('Importer unavailable',
                                 'Animation manager has not been initialized yet.',
                                 parent=self._window)
            return

        # Imported lazily so the control panel can still render even if
        # numpy or PIL are somehow missing — only the importer needs them.
        from sprite_sheet_importer import SpriteSheetImporter

        def on_imported():
            self.refresh_animation_list()
            self.refresh_transition_list()
            notify = self._callbacks.get('sprite_sheet_imported')
            if notify:
                notify()

        SpriteSheetImporter(
            parent=self._window,
            animation_manager=anim_manager,
            on_imported=on_imported,
        )

    def refresh_animation_list(self):
        """Refresh the treeview with current animation data."""
        # Clear existing items
        for item in self._tree.get_children():
            self._tree.delete(item)

        # Get summary and populate
        get_summary = self._callbacks.get('get_anim_summary')
        get_variants = self._callbacks.get('get_anim_variants')
        if not get_summary or not get_variants:
            return

        summary = get_summary()
        for anim_type in ANIMATION_TYPES:
            count = summary.get(anim_type, 0)
            # Insert type as a parent node
            type_id = self._tree.insert('', tk.END,
                                        text=anim_type.capitalize(),
                                        values=(str(count),),
                                        open=True)

            # Insert each variant as a child
            variants = get_variants(anim_type)
            for v in variants:
                self._tree.insert(type_id, tk.END,
                                  text=v.get('name', '?'),
                                  values=('',))

    # --- Transition UI ---

    def _on_add_transition(self):
        """Open dialog to add a new transition variant (folder or single GIF)."""
        dialog = tk.Toplevel(self._window)
        dialog.title('Add Transition Animation')
        dialog.geometry('440x280')
        dialog.resizable(False, False)
        dialog.transient(self._window)
        dialog.grab_set()

        frame = ttk.Frame(dialog, padding=15)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text='Transition:').grid(row=0, column=0, sticky=tk.W, pady=4)
        key_var = tk.StringVar(value=TRANSITION_KEYS[0])
        ttk.Combobox(frame, textvariable=key_var, values=TRANSITION_KEYS,
                     state='readonly', width=22) \
            .grid(row=0, column=1, sticky=tk.W, pady=4, padx=(8, 0), columnspan=2)

        ttk.Label(frame, text='Variant Name:').grid(row=1, column=0, sticky=tk.W, pady=4)
        name_var = tk.StringVar()
        ttk.Entry(frame, textvariable=name_var, width=24) \
            .grid(row=1, column=1, sticky=tk.W, pady=4, padx=(8, 0), columnspan=2)

        ttk.Label(frame, text='Source:').grid(row=2, column=0, sticky=tk.W, pady=4)
        source_var = tk.StringVar(value='folder')
        radio_frame = ttk.Frame(frame)
        radio_frame.grid(row=2, column=1, sticky=tk.W, pady=4, padx=(8, 0), columnspan=2)
        ttk.Radiobutton(radio_frame, text='Folder of frames', variable=source_var,
                        value='folder').pack(side=tk.LEFT)
        ttk.Radiobutton(radio_frame, text='Single GIF file', variable=source_var,
                        value='gif').pack(side=tk.LEFT, padx=(10, 0))

        path_label = ttk.Label(frame, text='Sprite Folder:')
        path_label.grid(row=3, column=0, sticky=tk.W, pady=4)
        path_var = tk.StringVar()
        ttk.Entry(frame, textvariable=path_var, width=28, state='readonly') \
            .grid(row=3, column=1, sticky=tk.W, pady=4, padx=(8, 0))

        frames_label = ttk.Label(frame, text='', foreground='gray')
        frames_label.grid(row=4, column=1, sticky=tk.W, padx=(8, 0))
        fps_label = ttk.Label(frame, text='FPS:')
        fps_var = tk.IntVar(value=config.CAT_FPS)
        fps_spin = ttk.Spinbox(frame, from_=1, to=60, width=4, textvariable=fps_var)
        fps_hint = ttk.Label(frame, text='fps', foreground='gray')
        fps_label.grid(row=5, column=0, sticky=tk.W, pady=4)
        fps_spin.grid(row=5, column=1, sticky=tk.W, pady=4, padx=(8, 0))
        fps_hint.grid(row=5, column=2, sticky=tk.W, pady=4)
        fps_label.grid_remove()
        fps_spin.grid_remove()
        fps_hint.grid_remove()

        gif_meta = {'frame_count': 0}

        def refresh_gif_meta():
            gif_meta['frame_count'] = 0
            frames_label.configure(text='')
            fps_label.grid_remove()
            fps_spin.grid_remove()
            fps_hint.grid_remove()
            if source_var.get() != 'gif':
                return
            p = path_var.get().strip()
            if not p or not os.path.isfile(p):
                return
            count, hint = inspect_gif(p)
            gif_meta['frame_count'] = count
            if count <= 0:
                frames_label.configure(text='(could not read GIF)')
                return
            frames_label.configure(text=f'Frames: {count}')
            if count >= 2:
                fps_var.set(int(hint))
                fps_label.grid()
                fps_spin.grid()
                fps_hint.grid()

        def browse():
            if source_var.get() == 'gif':
                picked = filedialog.askopenfilename(
                    title='Select a GIF file',
                    parent=dialog,
                    filetypes=[('GIF images', '*.gif')],
                )
            else:
                picked = filedialog.askdirectory(
                    title='Select folder with transition sprite frames',
                    parent=dialog,
                )
            if picked:
                path_var.set(picked)
                refresh_gif_meta()

        ttk.Button(frame, text='Browse...', command=browse).grid(
            row=3, column=2, padx=(4, 0), pady=4)

        def on_source_changed(*_):
            path_var.set('')
            if source_var.get() == 'gif':
                path_label.configure(text='GIF File:')
            else:
                path_label.configure(text='Sprite Folder:')
            refresh_gif_meta()

        source_var.trace_add('write', on_source_changed)

        def submit():
            transition_key = key_var.get()
            variant_name = name_var.get().strip()
            source_path = path_var.get().strip()
            source_type = source_var.get()

            if not variant_name:
                messagebox.showwarning('Missing Name', 'Please enter a variant name.',
                                       parent=dialog)
                return

            try:
                if source_type == 'gif':
                    if not source_path or not os.path.isfile(source_path):
                        messagebox.showwarning('Missing GIF',
                                               'Please select a valid GIF file.',
                                               parent=dialog)
                        return
                    fps = int(fps_var.get()) if gif_meta['frame_count'] >= 2 else None
                    cb = self._callbacks.get('add_gif_transition')
                    if cb is None:
                        raise RuntimeError('add_gif_transition callback not wired')
                    cb(transition_key, variant_name, source_path, fps)
                else:
                    if not source_path or not os.path.isdir(source_path):
                        messagebox.showwarning('Missing Folder',
                                               'Please select a valid sprite folder.',
                                               parent=dialog)
                        return
                    if 'add_transition' in self._callbacks:
                        self._callbacks['add_transition'](transition_key, variant_name, source_path)
                self.refresh_transition_list()
                dialog.destroy()
            except Exception as e:
                messagebox.showerror('Error', f'Failed to add transition:\n{e}',
                                     parent=dialog)

        ttk.Button(frame, text='Add', command=submit).grid(
            row=6, column=0, columnspan=3, pady=(12, 0))

    def _on_remove_transition(self):
        """Remove the selected transition variant."""
        selected = self._trans_tree.selection()
        if not selected:
            messagebox.showinfo('No Selection', 'Select a transition variant to remove.',
                                parent=self._window)
            return

        item_id = selected[0]
        parent_id = self._trans_tree.parent(item_id)

        if not parent_id:
            messagebox.showinfo('Select Variant',
                                'Please select a specific variant, not a transition key.',
                                parent=self._window)
            return

        transition_key = self._trans_tree.item(parent_id, 'text')
        variant_name = self._trans_tree.item(item_id, 'text')

        if messagebox.askyesno('Confirm Remove',
                               f'Remove "{variant_name}" from {transition_key}?',
                               parent=self._window):
            if 'remove_transition' in self._callbacks:
                self._callbacks['remove_transition'](transition_key, variant_name)
            self.refresh_transition_list()

    def refresh_transition_list(self):
        """Refresh the transitions treeview."""
        for item in self._trans_tree.get_children():
            self._trans_tree.delete(item)

        get_summary = self._callbacks.get('get_transition_summary')
        get_variants = self._callbacks.get('get_transition_variants')
        if not get_summary or not get_variants:
            return

        summary = get_summary()
        for key in TRANSITION_KEYS:
            count = summary.get(key, 0)
            key_id = self._trans_tree.insert('', tk.END,
                                             text=key,
                                             values=(str(count),),
                                             open=False)

            variants = get_variants(key)
            for v in variants:
                self._trans_tree.insert(key_id, tk.END,
                                        text=v.get('name', '?'),
                                        values=('',))
