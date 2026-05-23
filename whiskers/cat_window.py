# cat_window.py — Animated sprite window for Whiskers (tkinter)

import os
import random
import threading
import time
import tkinter as tk
from enum import Enum

from PIL import Image, ImageTk

import config

try:
    import win32gui
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False


class CatState(str, Enum):
    IDLE       = 'IDLE'
    WALK_RIGHT = 'WALK_RIGHT'
    WALK_LEFT  = 'WALK_LEFT'
    RUN_RIGHT  = 'RUN_RIGHT'
    RUN_LEFT   = 'RUN_LEFT'
    SLEEP      = 'SLEEP'
    LISTEN     = 'LISTEN'
    THINK      = 'THINK'
    TALK       = 'TALK'
    HAPPY      = 'HAPPY'


# States that flip horizontally (use the same animation as their right-facing counterpart)
_LEFT_STATES = {CatState.WALK_LEFT, CatState.RUN_LEFT}


class CatWindow:
    def __init__(self, animation_manager=None, on_settings_callback=None):
        """
        Args:
            animation_manager: AnimationManager instance for variant resolution.
                               If None, falls back to orange rectangles for all states.
            on_settings_callback: Called when user clicks Settings in right-click menu.
        """
        self._anim_manager = animation_manager
        self._on_settings_cb = on_settings_callback
        self._root = None
        self._label = None
        self._state = CatState.IDLE
        self._pil_frames_by_state = {}     # state -> list of PIL Images (current variant)
        self._photo_frames_by_state = {}   # state -> list of ImageTk.PhotoImage (current variant)
        self._current_frame_idx = 0
        self._window_x = config.CAT_START_X
        self._window_y = config.CAT_START_Y
        self._drag_offset_x = 0
        self._drag_offset_y = 0
        self._running = False
        self._thread = None
        self._last_interaction_time = time.time()
        self._roam_timer_id = None
        self._anim_timer_id = None
        self._sleep_check_id = None
        self._happy_revert_id = None
        # Transition animation state
        self._in_transition = False
        self._transition_target = None
        self._transition_tk_frames = []
        self._current_transition_key = None  # e.g. "idle_to_walk" while _in_transition
        self._current_transition_variant_fps = None  # fps from the picked transition variant, or None
        # Per-state fps from the currently-picked variant (None if variant has no override)
        self._variant_fps_by_state = {}
        # Per-animation FPS overrides (name -> fps). Missing key = inherit config.CAT_FPS.
        self._animation_fps_overrides = dict(config.get_setting('animation_fps', {}) or {})
        # Expose root for speech_bubble / control_panel Toplevel creation
        self.root = None
        # Event that signals when the tkinter root is ready
        self._root_ready = threading.Event()

    def _init_window(self):
        """Initialise the tkinter window (must run in the tkinter thread)."""
        self._root = tk.Tk()
        self.root = self._root
        self._root_ready.set()
        self._root.overrideredirect(True)
        self._root.attributes('-topmost', True)
        self._root.wm_attributes('-transparentcolor', 'black')
        self._root.configure(bg='black')
        self._root.geometry(f'+{self._window_x}+{self._window_y}')

        self._label = tk.Label(self._root, bg='black', bd=0, highlightthickness=0)
        self._label.pack()

        # Mouse drag bindings — no ButtonRelease handler needed; the drag
        # position is already applied incrementally in _on_drag_motion.
        self._label.bind('<Button-1>', self._on_drag_start)
        self._label.bind('<B1-Motion>', self._on_drag_motion)

        # Right-click context menu
        self._menu = tk.Menu(self._root, tearoff=0)
        self._menu.add_command(label='Settings', command=self._on_settings)
        self._menu.add_separator()
        self._menu.add_command(label='Quit', command=self.stop)
        self._label.bind('<Button-3>', self._on_right_click)

        # Any click wakes the cat
        self._label.bind('<Button-1>', self._on_click, add='+')

        # Load sprites for all states using the animation manager
        self._load_all_sprites()

        # Start animation loop
        self._advance_animation_frame()

        # Start roaming behaviour
        self._schedule_roam()

        # Start sleep check
        self._check_sleep()

    def _load_all_sprites(self):
        """Load sprite frames for every CatState using the AnimationManager."""
        for state in CatState:
            self._pick_variant_for_state(state)

    def _pick_variant_for_state(self, state):
        """Randomly select a variant's frames for the given state.

        For LEFT states, loads the right-facing variant and flips horizontally.
        Falls back to orange rectangles if no animation manager or no variants found.
        """
        if self._anim_manager is None:
            self._pil_frames_by_state[state] = self._fallback_frames()
            self._variant_fps_by_state[state] = None
            self._convert_frames_for_state(state)
            return

        anim_type = self._anim_manager.get_animation_type_for_state(state.value)
        variant = self._anim_manager.resolve_variant(anim_type)

        if variant is None:
            self._pil_frames_by_state[state] = self._fallback_frames()
            self._variant_fps_by_state[state] = None
        else:
            frames = self._anim_manager.load_variant_frames(variant, config.CAT_SCALE)
            # Flip horizontally for left-facing states
            if state in _LEFT_STATES:
                frames = [f.transpose(Image.FLIP_LEFT_RIGHT) for f in frames]
            self._pil_frames_by_state[state] = frames
            self._variant_fps_by_state[state] = variant.get('fps')

        self._convert_frames_for_state(state)

    def _convert_frames_for_state(self, state):
        """Convert PIL frames to ImageTk.PhotoImage for the given state."""
        self._photo_frames_by_state[state] = [
            ImageTk.PhotoImage(frame) for frame in self._pil_frames_by_state[state]
        ]

    def _fallback_frames(self):
        """Create a colored rectangle placeholder."""
        size = 16 * config.CAT_SCALE
        img = Image.new('RGBA', (size, size), (255, 165, 0, 255))
        return [img]

    def _advance_animation_frame(self):
        """Advance the animation by one frame and reposition the cat window."""
        if not self._running or not self._root:
            return

        # Use transition frames if playing a transition, otherwise normal state frames
        if self._in_transition:
            frames = self._transition_tk_frames
        else:
            frames = self._photo_frames_by_state.get(self._state, [])

        if frames:
            if self._in_transition:
                # Transition: play once (no wrap), then finish
                if self._current_frame_idx >= len(frames):
                    self._finish_transition()
                    # Re-schedule and return — the new state will display next tick
                    delay = max(1, 1000 // self._current_anim_fps())
                    self._anim_timer_id = self._root.after(delay, self._advance_animation_frame)
                    return
                self._label.configure(image=frames[self._current_frame_idx])
                self._current_frame_idx += 1
            else:
                # Normal looping animation
                self._current_frame_idx = self._current_frame_idx % len(frames)
                self._label.configure(image=frames[self._current_frame_idx])
                self._current_frame_idx += 1

        # Move the cat if walking or running
        if self._state == CatState.WALK_RIGHT:
            self._window_x += config.CAT_WALK_SPEED
        elif self._state == CatState.WALK_LEFT:
            self._window_x -= config.CAT_WALK_SPEED
        elif self._state == CatState.RUN_RIGHT:
            self._window_x += config.CAT_WALK_SPEED * 2
        elif self._state == CatState.RUN_LEFT:
            self._window_x -= config.CAT_WALK_SPEED * 2

        # Bounce off screen edges — flip direction only, don't pick a new variant
        # (re-resolving the variant at every wall would randomly swap the sprite)
        screen_w = self._root.winfo_screenwidth()
        screen_h = self._root.winfo_screenheight()
        cat_width = self._label.winfo_reqwidth() or 48
        cat_height = self._label.winfo_reqheight() or 48

        if self._window_x <= 0:
            self._window_x = 0
            if self._state == CatState.WALK_LEFT:
                self._flip_direction_only(CatState.WALK_RIGHT)
            elif self._state == CatState.RUN_LEFT:
                self._flip_direction_only(CatState.RUN_RIGHT)
        elif self._window_x >= screen_w - cat_width:
            self._window_x = screen_w - cat_width
            if self._state == CatState.WALK_RIGHT:
                self._flip_direction_only(CatState.WALK_LEFT)
            elif self._state == CatState.RUN_RIGHT:
                self._flip_direction_only(CatState.RUN_LEFT)

        # Clamp vertical position
        if self._window_y < 0:
            self._window_y = 0
        elif self._window_y > screen_h - cat_height:
            self._window_y = screen_h - cat_height

        self._root.geometry(f'+{self._window_x}+{self._window_y}')

        delay = max(1, 1000 // self._current_anim_fps())
        self._anim_timer_id = self._root.after(delay, self._advance_animation_frame)

    def _flip_direction_only(self, new_state):
        """Change facing direction without re-randomizing the animation variant.

        Keeps frame index and existing frames for the opposite direction intact
        by re-using the PIL frames from the current state and flipping them.
        Used only for wall bounces between WALK_LEFT/WALK_RIGHT (or RUN variants).
        """
        current_frames = self._pil_frames_by_state.get(self._state)
        if current_frames:
            flipped = [f.transpose(Image.FLIP_LEFT_RIGHT) for f in current_frames]
            self._pil_frames_by_state[new_state] = flipped
            self._convert_frames_for_state(new_state)
            # Carry over the variant's fps so the flipped direction plays at the same speed
            self._variant_fps_by_state[new_state] = self._variant_fps_by_state.get(self._state)
        self._state = new_state
        # Preserve frame index modulo length so the walk cycle continues smoothly

    def _set_state_internal(self, new_state):
        """Change state, playing a transition animation if one exists.

        If a transition is currently playing and a new state change is requested,
        the transition is interrupted and the new state is applied immediately.
        Ignores no-op changes (same state) to avoid re-randomizing the variant.
        """
        old_state = self._state

        # No-op: don't reshuffle the variant just because caller re-set the same state
        if old_state == new_state and not self._in_transition:
            return

        # If mid-transition, cancel it and go straight to new state
        if self._in_transition:
            self._in_transition = False
            self._transition_target = None
            self._transition_tk_frames = []
            self._current_transition_key = None
            self._current_transition_variant_fps = None

        # Check if a transition animation exists between the old and new types
        if self._anim_manager and old_state != new_state:
            old_type = self._anim_manager.get_animation_type_for_state(old_state.value)
            new_type = self._anim_manager.get_animation_type_for_state(new_state.value)

            if old_type != new_type:
                transition_variant = self._anim_manager.resolve_transition(old_type, new_type)
                if transition_variant:
                    self._current_transition_key = f"{old_type}_to_{new_type}"
                    self._play_transition(transition_variant, new_state)
                    return

        # No transition available — instant swap
        self._state = new_state
        self._current_frame_idx = 0
        self._pick_variant_for_state(new_state)

    def _play_transition(self, transition_variant, target_state):
        """Play a one-shot transition animation, then enter the target state."""
        frames = self._anim_manager.load_variant_frames(transition_variant, config.CAT_SCALE)

        # Flip horizontally if the target is a left-facing state
        if target_state in _LEFT_STATES:
            frames = [f.transpose(Image.FLIP_LEFT_RIGHT) for f in frames]

        self._transition_tk_frames = [ImageTk.PhotoImage(f) for f in frames]
        self._transition_target = target_state
        self._in_transition = True
        self._current_frame_idx = 0
        self._current_transition_variant_fps = transition_variant.get('fps')

    def _finish_transition(self):
        """Called when the transition animation finishes. Enter the target state."""
        self._in_transition = False
        target = self._transition_target
        self._transition_target = None
        self._transition_tk_frames = []
        self._current_transition_key = None
        self._current_transition_variant_fps = None

        if target is not None:
            self._state = target
            self._current_frame_idx = 0
            self._pick_variant_for_state(target)

    def _current_anim_fps(self):
        """Effective FPS for the currently-playing animation or transition.

        Precedence (highest first): per-variant fps > per-animation-name
        override (user_settings.json) > config.CAT_FPS. Clamped to [1, 60].
        """
        if self._in_transition:
            fps = self._current_transition_variant_fps
            if not isinstance(fps, int) or fps < 1:
                name = self._current_transition_key
                fps = self._animation_fps_overrides.get(name) if name else None
        else:
            fps = self._variant_fps_by_state.get(self._state)
            if not isinstance(fps, int) or fps < 1:
                if self._anim_manager is not None:
                    name = self._anim_manager.get_animation_type_for_state(self._state.value)
                    fps = self._animation_fps_overrides.get(name) if name else None
                else:
                    fps = None
        if not isinstance(fps, int) or fps < 1:
            fps = config.CAT_FPS
        return max(1, min(60, fps))

    def set_animation_fps(self, name, fps):
        """Set or clear a per-animation FPS override and persist to user_settings.json.

        Pass fps=None to remove the override (revert to config.CAT_FPS).
        """
        if fps is None:
            self._animation_fps_overrides.pop(name, None)
        else:
            self._animation_fps_overrides[name] = max(1, min(60, int(fps)))
        config.set_setting('animation_fps', dict(self._animation_fps_overrides))

    # States the cat can be in while "roaming" (wandering freely, not driven by voice)
    _WANDER_STATES = (
        CatState.IDLE, CatState.WALK_LEFT, CatState.WALK_RIGHT,
        CatState.RUN_LEFT, CatState.RUN_RIGHT,
    )

    def _schedule_roam(self):
        """Schedule the next random roaming state change in 8-15 seconds."""
        if not self._running or not self._root:
            return
        delay = random.randint(8000, 15000)
        self._roam_timer_id = self._root.after(delay, self._pick_random_wander_state)

    def _pick_random_wander_state(self):
        """Pick a new IDLE/WALK/RUN state while the cat is wandering.

        Does NOT fire while the cat is asleep — sleep ends only on user interaction
        (click/drag/voice), otherwise the sleep cycle would be broken by this timer.
        """
        if not self._running or not self._root:
            return

        if self._state in self._WANDER_STATES:
            # Weighted random: 40% idle, 30% walk (L/R), 20% run (L/R), 10% idle again
            choices = [
                CatState.IDLE, CatState.IDLE,
                CatState.WALK_LEFT, CatState.WALK_RIGHT,
                CatState.WALK_LEFT, CatState.WALK_RIGHT,
                CatState.RUN_LEFT, CatState.RUN_RIGHT,
                CatState.IDLE, CatState.IDLE,
            ]
            new_state = random.choice(choices)
            self._set_state_internal(new_state)

            # Try to sit on a window border when idle
            self._try_sit_on_window_top()

        self._schedule_roam()

    def _try_sit_on_window_top(self):
        """Use win32gui to detect open windows and perch the cat on a top border."""
        if not HAS_WIN32 or self._state != CatState.IDLE:
            return

        candidate_rects = []

        def enum_handler(hwnd, results):
            if win32gui.IsWindowVisible(hwnd):
                try:
                    rect = win32gui.GetWindowRect(hwnd)
                    width = rect[2] - rect[0]
                    height = rect[3] - rect[1]
                    if width > 100 and height > 100 and rect[1] > 0:
                        results.append(rect)
                except Exception:
                    pass

        try:
            win32gui.EnumWindows(enum_handler, candidate_rects)
        except Exception:
            return

        if not candidate_rects:
            return

        # 30% chance to sit on a window
        if random.random() < 0.3:
            rect = random.choice(candidate_rects)
            cat_width = self._label.winfo_reqwidth() or 48
            cat_height = self._label.winfo_reqheight() or 48
            new_x = rect[0] + random.randint(0, max(0, rect[2] - rect[0] - cat_width))
            new_y = rect[1] - cat_height
            if new_y >= 0:
                self._window_x = new_x
                self._window_y = new_y

    def _check_sleep(self):
        """Check if 5 minutes have passed without interaction; if so, fall asleep."""
        if not self._running or not self._root:
            return

        elapsed = time.time() - self._last_interaction_time
        if elapsed >= 300 and self._state in self._WANDER_STATES:
            self._set_state_internal(CatState.SLEEP)

        self._sleep_check_id = self._root.after(10000, self._check_sleep)

    def _wake_cat_from_rest(self):
        """Wake the cat from sleep or reset its interaction timer from any state.

        Also cancels any pending happy-revert timer so an interaction mid-happy
        doesn't later snap back to IDLE unexpectedly.
        """
        self._last_interaction_time = time.time()
        if self._happy_revert_id:
            try:
                self._root.after_cancel(self._happy_revert_id)
            except Exception:
                pass
            self._happy_revert_id = None
        waking_from = (CatState.SLEEP,) + self._WANDER_STATES
        if self._state in waking_from and self._state != CatState.IDLE:
            self._set_state_internal(CatState.IDLE)

    # --- Drag / click handlers ---

    def _on_drag_start(self, event):
        self._drag_offset_x = event.x
        self._drag_offset_y = event.y
        self._wake_cat_from_rest()

    def _on_drag_motion(self, event):
        self._window_x = self._root.winfo_x() + event.x - self._drag_offset_x
        self._window_y = self._root.winfo_y() + event.y - self._drag_offset_y
        self._root.geometry(f'+{self._window_x}+{self._window_y}')

    def _on_click(self, event):
        self._wake_cat_from_rest()

    def _on_right_click(self, event):
        self._menu.post(event.x_root, event.y_root)

    def _on_settings(self):
        """Open the settings/control panel."""
        if self._on_settings_cb:
            self._on_settings_cb()
        else:
            print('[INFO] Settings dialog not yet implemented.')

    # --- Public methods ---

    def set_state(self, state: str):
        """Change the cat's animation state. Thread-safe via root.after().

        Picks a new random variant for the target state, unless already in it.
        """
        new_state = CatState(state)

        def apply_on_tk_thread():
            self._set_state_internal(new_state)
            self._last_interaction_time = time.time()

        if self._root:
            self._root.after(0, apply_on_tk_thread)

    def get_position(self):
        """Return the current (x, y) position of the cat window."""
        return (self._window_x, self._window_y)

    def set_position(self, x, y):
        """Move the cat window to the given position. Thread-safe."""
        def apply_on_tk_thread():
            self._window_x = x
            self._window_y = y
            if self._root:
                self._root.geometry(f'+{self._window_x}+{self._window_y}')

        if self._root:
            self._root.after(0, apply_on_tk_thread)

    def show_happy_then_revert_to_idle(self):
        """Play the HAPPY animation, then revert to IDLE after 2 seconds."""
        def apply_on_tk_thread():
            self._set_state_internal(CatState.HAPPY)
            self._last_interaction_time = time.time()
            if self._happy_revert_id:
                self._root.after_cancel(self._happy_revert_id)
            self._happy_revert_id = self._root.after(2000, self._revert_from_happy_to_idle)

        if self._root:
            self._root.after(0, apply_on_tk_thread)

    def _revert_from_happy_to_idle(self):
        """Revert from HAPPY back to IDLE after the 2-second celebration window."""
        self._happy_revert_id = None
        self._set_state_internal(CatState.IDLE)

    def reload_sprites(self):
        """Reload all sprite variants from the animation manager. Thread-safe.

        Call this after adding/removing animations from the control panel.
        """
        def apply_on_tk_thread():
            self._load_all_sprites()

        if self._root:
            self._root.after(0, apply_on_tk_thread)

    def start(self):
        """Start the cat window in its own thread."""
        self._running = True
        self._root_ready.clear()
        self._thread = threading.Thread(target=self._run_tk_mainloop, daemon=True)
        self._thread.start()

    def wait_ready(self, timeout=5.0):
        """Block until the tkinter root is initialized. Returns True if ready."""
        return self._root_ready.wait(timeout=timeout)

    def _run_tk_mainloop(self):
        """Thread target: initialise and run the tkinter mainloop."""
        self._init_window()
        self._root.mainloop()

    def stop(self):
        """Cleanly shut down the cat window, cancelling all pending timers."""
        self._running = False

        def destroy_on_tk_thread():
            for timer_id in (self._roam_timer_id, self._anim_timer_id,
                             self._sleep_check_id, self._happy_revert_id):
                if timer_id:
                    try:
                        self._root.after_cancel(timer_id)
                    except Exception:
                        pass
            self._roam_timer_id = None
            self._anim_timer_id = None
            self._sleep_check_id = None
            self._happy_revert_id = None
            self._root.destroy()

        if self._root:
            self._root.after(0, destroy_on_tk_thread)
