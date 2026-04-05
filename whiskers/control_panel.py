# control_panel.py — Settings/Control panel window for Whiskers

import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import config
import voice_output
from animation_manager import ANIMATION_TYPES, TRANSITION_KEYS


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

        # Mouse wheel scrolling
        def _on_mousewheel(event):
            self._canvas.yview_scroll(int(-1 * (event.delta / 120)), 'units')

        self._canvas.bind_all('<MouseWheel>', _on_mousewheel)

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

        self._mic_test_btn = ttk.Button(mic_frame, text='Test Microphone',
                                        command=self._on_test_mic)
        self._mic_test_btn.pack(side=tk.LEFT, padx=(0, 8))

        self._mic_status_label = ttk.Label(mic_frame, text='')
        self._mic_status_label.pack(side=tk.LEFT)

        # Level bar to show live audio level
        self._mic_level = ttk.Progressbar(mic_frame, orient=tk.HORIZONTAL,
                                          length=150, mode='determinate',
                                          maximum=100)
        self._mic_level.pack(side=tk.LEFT, padx=(8, 0))

        self._mic_testing = False
        self._mic_stream = None

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
        self._remove_btn.pack(side=tk.LEFT)

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
                   command=self._on_remove_transition).pack(side=tk.LEFT)

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

    def _on_test_mic(self):
        """Toggle a 5-second microphone test with live level display."""
        if self._mic_testing:
            self._stop_mic_test()
            return

        # Try to open an audio input stream
        try:
            import sounddevice as _sd
        except ImportError:
            self._mic_status_label.config(text='sounddevice not installed', foreground='red')
            return

        try:
            # Query default input device to confirm one exists
            dev = _sd.query_devices(kind='input')
            dev_name = dev['name']
        except Exception:
            self._mic_status_label.config(text='No microphone found', foreground='red')
            self._mic_level['value'] = 0
            return

        self._mic_testing = True
        self._mic_test_btn.config(text='Stop')
        self._mic_status_label.config(text=f'Listening... ({dev_name})', foreground='green')

        import numpy as _np

        def audio_callback(indata, frames, time_info, status):
            # Compute RMS level as a percentage (0-100)
            rms = float(_np.sqrt(_np.mean(indata ** 2)))
            # Scale: typical speech RMS ~0.01-0.1, clamp to 0-100
            level = min(100, int(rms * 1000))
            # Schedule UI update on the tkinter thread
            try:
                self._root.after(0, lambda l=level: self._mic_level.configure(value=l))
            except Exception:
                pass

        try:
            self._mic_stream = _sd.InputStream(
                samplerate=16000, channels=1, dtype='float32',
                blocksize=1600,  # 100ms chunks
                callback=audio_callback
            )
            self._mic_stream.start()
        except Exception as e:
            self._mic_status_label.config(text=f'Error: {e}', foreground='red')
            self._mic_testing = False
            self._mic_test_btn.config(text='Test Microphone')
            return

        # Auto-stop after 10 seconds
        self._mic_auto_stop_id = self._root.after(10000, self._stop_mic_test)

    def _stop_mic_test(self):
        """Stop the microphone test."""
        self._mic_testing = False
        self._mic_test_btn.config(text='Test Microphone')

        if hasattr(self, '_mic_auto_stop_id') and self._mic_auto_stop_id:
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
        """Open dialog to add a new animation variant."""
        # Create a small dialog for type selection + folder picking
        dialog = tk.Toplevel(self._window)
        dialog.title('Add Animation')
        dialog.geometry('360x180')
        dialog.resizable(False, False)
        dialog.transient(self._window)
        dialog.grab_set()

        frame = ttk.Frame(dialog, padding=15)
        frame.pack(fill=tk.BOTH, expand=True)

        # Animation type selector
        ttk.Label(frame, text='Animation Type:').grid(row=0, column=0, sticky=tk.W, pady=4)
        type_var = tk.StringVar(value=ANIMATION_TYPES[0])
        type_combo = ttk.Combobox(frame, textvariable=type_var,
                                  values=ANIMATION_TYPES, state='readonly', width=18)
        type_combo.grid(row=0, column=1, sticky=tk.W, pady=4, padx=(8, 0))

        # Variant name
        ttk.Label(frame, text='Variant Name:').grid(row=1, column=0, sticky=tk.W, pady=4)
        name_var = tk.StringVar()
        name_entry = ttk.Entry(frame, textvariable=name_var, width=20)
        name_entry.grid(row=1, column=1, sticky=tk.W, pady=4, padx=(8, 0))

        # Folder path
        ttk.Label(frame, text='Sprite Folder:').grid(row=2, column=0, sticky=tk.W, pady=4)
        path_var = tk.StringVar()
        path_entry = ttk.Entry(frame, textvariable=path_var, width=20, state='readonly')
        path_entry.grid(row=2, column=1, sticky=tk.W, pady=4, padx=(8, 0))

        def browse():
            folder = filedialog.askdirectory(
                title='Select folder with sprite frames',
                parent=dialog
            )
            if folder:
                path_var.set(folder)

        browse_btn = ttk.Button(frame, text='Browse...', command=browse)
        browse_btn.grid(row=2, column=2, padx=(4, 0), pady=4)

        def submit():
            anim_type = type_var.get()
            variant_name = name_var.get().strip()
            source_path = path_var.get().strip()

            if not variant_name:
                messagebox.showwarning('Missing Name', 'Please enter a variant name.',
                                       parent=dialog)
                return
            if not source_path or not os.path.isdir(source_path):
                messagebox.showwarning('Missing Folder', 'Please select a valid sprite folder.',
                                       parent=dialog)
                return

            try:
                if 'add_animation' in self._callbacks:
                    self._callbacks['add_animation'](anim_type, variant_name, source_path)
                self.refresh_animation_list()
                dialog.destroy()
            except Exception as e:
                messagebox.showerror('Error', f'Failed to add animation:\n{e}',
                                     parent=dialog)

        ttk.Button(frame, text='Add', command=submit).grid(
            row=3, column=0, columnspan=3, pady=(12, 0))

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
        """Open dialog to add a new transition variant."""
        dialog = tk.Toplevel(self._window)
        dialog.title('Add Transition Animation')
        dialog.geometry('400x180')
        dialog.resizable(False, False)
        dialog.transient(self._window)
        dialog.grab_set()

        frame = ttk.Frame(dialog, padding=15)
        frame.pack(fill=tk.BOTH, expand=True)

        # Transition key selector
        ttk.Label(frame, text='Transition:').grid(row=0, column=0, sticky=tk.W, pady=4)
        key_var = tk.StringVar(value=TRANSITION_KEYS[0])
        key_combo = ttk.Combobox(frame, textvariable=key_var,
                                 values=TRANSITION_KEYS, state='readonly', width=22)
        key_combo.grid(row=0, column=1, sticky=tk.W, pady=4, padx=(8, 0))

        # Variant name
        ttk.Label(frame, text='Variant Name:').grid(row=1, column=0, sticky=tk.W, pady=4)
        name_var = tk.StringVar()
        ttk.Entry(frame, textvariable=name_var, width=24).grid(
            row=1, column=1, sticky=tk.W, pady=4, padx=(8, 0))

        # Folder path
        ttk.Label(frame, text='Sprite Folder:').grid(row=2, column=0, sticky=tk.W, pady=4)
        path_var = tk.StringVar()
        ttk.Entry(frame, textvariable=path_var, width=24, state='readonly').grid(
            row=2, column=1, sticky=tk.W, pady=4, padx=(8, 0))

        def browse():
            folder = filedialog.askdirectory(
                title='Select folder with transition sprite frames',
                parent=dialog
            )
            if folder:
                path_var.set(folder)

        ttk.Button(frame, text='Browse...', command=browse).grid(
            row=2, column=2, padx=(4, 0), pady=4)

        def submit():
            transition_key = key_var.get()
            variant_name = name_var.get().strip()
            source_path = path_var.get().strip()

            if not variant_name:
                messagebox.showwarning('Missing Name', 'Please enter a variant name.',
                                       parent=dialog)
                return
            if not source_path or not os.path.isdir(source_path):
                messagebox.showwarning('Missing Folder', 'Please select a valid sprite folder.',
                                       parent=dialog)
                return

            try:
                if 'add_transition' in self._callbacks:
                    self._callbacks['add_transition'](transition_key, variant_name, source_path)
                self.refresh_transition_list()
                dialog.destroy()
            except Exception as e:
                messagebox.showerror('Error', f'Failed to add transition:\n{e}',
                                     parent=dialog)

        ttk.Button(frame, text='Add', command=submit).grid(
            row=3, column=0, columnspan=3, pady=(12, 0))

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
