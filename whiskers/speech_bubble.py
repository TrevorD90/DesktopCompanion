# speech_bubble.py — Transparent speech bubble window for Whiskers

import tkinter as tk
import config


class SpeechBubble:
    """A transparent Toplevel window that displays text above the cat's head."""

    def __init__(self, cat_window):
        """
        Args:
            cat_window: The CatWindow instance. Used to get position and
                        the tkinter root for creating a Toplevel.
        """
        self._cat = cat_window
        self._toplevel = None
        self._canvas = None
        self._dismiss_timer = None
        self._fade_step = 0
        self._fade_timer = None
        self._visible = False

    def _ensure_toplevel(self):
        """Create the Toplevel window if it doesn't exist yet."""
        if self._toplevel is not None:
            return

        root = self._cat.root
        if root is None:
            return

        self._toplevel = tk.Toplevel(root)
        self._toplevel.overrideredirect(True)
        self._toplevel.attributes('-topmost', True)
        self._toplevel.wm_attributes('-transparentcolor', 'black')
        self._toplevel.configure(bg='black')
        self._toplevel.attributes('-alpha', 0.0)
        self._toplevel.withdraw()

        self._canvas = tk.Canvas(
            self._toplevel,
            bg='black',
            highlightthickness=0,
            bd=0
        )
        self._canvas.pack(fill=tk.BOTH, expand=True)

    def _draw_bubble(self, text):
        """Draw the rounded rectangle bubble with text and pointer triangle."""
        self._canvas.delete('all')

        max_w = config.BUBBLE_MAX_WIDTH
        font = ('Arial', config.BUBBLE_FONT_SIZE)
        padding = 12
        corner_r = 10
        pointer_h = 10  # Height of the downward triangle

        # Measure text by creating a temporary text item
        temp_id = self._canvas.create_text(
            0, 0,
            text=text,
            font=font,
            width=max_w - (padding * 2),
            anchor='nw'
        )
        bbox = self._canvas.bbox(temp_id)
        self._canvas.delete(temp_id)

        if not bbox:
            return 0, 0

        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        bubble_w = text_w + (padding * 2)
        bubble_h = text_h + (padding * 2)
        total_h = bubble_h + pointer_h

        # Resize canvas and window
        self._canvas.config(width=bubble_w, height=total_h)
        self._toplevel.geometry(f'{bubble_w}x{total_h}')

        bg = config.BUBBLE_BG_COLOR
        r = corner_r

        # Draw rounded rectangle (bubble body)
        # Top-left corner
        self._canvas.create_arc(0, 0, r * 2, r * 2,
                                start=90, extent=90, fill=bg, outline=bg)
        # Top-right corner
        self._canvas.create_arc(bubble_w - r * 2, 0, bubble_w, r * 2,
                                start=0, extent=90, fill=bg, outline=bg)
        # Bottom-left corner
        self._canvas.create_arc(0, bubble_h - r * 2, r * 2, bubble_h,
                                start=180, extent=90, fill=bg, outline=bg)
        # Bottom-right corner
        self._canvas.create_arc(bubble_w - r * 2, bubble_h - r * 2, bubble_w, bubble_h,
                                start=270, extent=90, fill=bg, outline=bg)
        # Fill center rectangles
        self._canvas.create_rectangle(r, 0, bubble_w - r, bubble_h,
                                      fill=bg, outline=bg)
        self._canvas.create_rectangle(0, r, bubble_w, bubble_h - r,
                                      fill=bg, outline=bg)

        # Draw downward pointer triangle (centered at bottom)
        cx = bubble_w // 2
        self._canvas.create_polygon(
            cx - 8, bubble_h,
            cx + 8, bubble_h,
            cx, bubble_h + pointer_h,
            fill=bg, outline=bg
        )

        # Draw the text
        self._canvas.create_text(
            padding, padding,
            text=text,
            font=font,
            fill=config.BUBBLE_TEXT_COLOR,
            width=max_w - (padding * 2),
            anchor='nw'
        )

        return bubble_w, total_h

    def _position_above_cat(self, bubble_w, bubble_h):
        """Position the bubble 10px above the cat's current position."""
        cat_x, cat_y = self._cat.get_position()
        # Center the bubble horizontally over the cat
        x = cat_x - (bubble_w // 4)
        y = cat_y - bubble_h - 10

        # Clamp to screen
        if x < 0:
            x = 0
        if y < 0:
            y = 0

        self._toplevel.geometry(f'+{x}+{y}')

    def _fade_in(self):
        """Gradually increase alpha from 0.0 to 1.0 over 200ms (10 steps)."""
        if self._fade_step >= 10:
            self._toplevel.attributes('-alpha', 1.0)
            return

        alpha = self._fade_step / 10.0
        self._toplevel.attributes('-alpha', alpha)
        self._fade_step += 1
        self._fade_timer = self._toplevel.after(20, self._fade_in)

    def _hide(self):
        """Hide the bubble window."""
        self._visible = False
        if self._toplevel:
            self._toplevel.attributes('-alpha', 0.0)
            self._toplevel.withdraw()

    def _show_internal(self, text, auto_dismiss=True):
        """Internal: draw and display the bubble."""
        self._ensure_toplevel()
        if not self._toplevel:
            return

        # Cancel any pending dismiss or fade timers
        if self._dismiss_timer:
            self._toplevel.after_cancel(self._dismiss_timer)
            self._dismiss_timer = None
        if self._fade_timer:
            self._toplevel.after_cancel(self._fade_timer)
            self._fade_timer = None

        # Draw the bubble content
        bubble_w, bubble_h = self._draw_bubble(text)
        if bubble_w == 0:
            return

        # Position above cat
        self._position_above_cat(bubble_w, bubble_h)

        # Show and fade in
        self._toplevel.deiconify()
        self._toplevel.attributes('-alpha', 0.0)
        self._fade_step = 0
        self._visible = True
        self._fade_in()

        # Schedule auto-dismiss only if requested
        if auto_dismiss:
            self._dismiss_timer = self._toplevel.after(config.BUBBLE_DURATION, self._hide)

    def show(self, text):
        """Display the speech bubble with auto-dismiss after BUBBLE_DURATION.

        Thread-safe — schedules on the tkinter thread.
        """
        root = self._cat.root
        if root:
            root.after(0, lambda: self._show_internal(text, auto_dismiss=True))

    def show_persistent(self, text):
        """Display the speech bubble without auto-dismiss. Stays until dismiss() is called.

        Thread-safe — schedules on the tkinter thread.
        """
        root = self._cat.root
        if root:
            root.after(0, lambda: self._show_internal(text, auto_dismiss=False))

    def dismiss(self):
        """Hide the bubble immediately. Thread-safe."""
        root = self._cat.root
        if root:
            root.after(0, self._hide)
