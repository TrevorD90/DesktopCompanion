# sprite_sheet_importer.py — Visual sprite-sheet slicer for Whiskers
#
# Lets the user load a single multi-row sprite sheet, eyedrop the background
# color, drag rectangles around each animation row, tag each rectangle with
# its animation type (or transition key), and import all regions as sliced
# per-frame PNGs registered through the existing AnimationManager.
#
# Color-keying writes binary alpha (0 or 255) so tkinter's chroma-key
# transparentcolor='black' on the cat window doesn't produce dark halos.

import os
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import numpy as np
from PIL import Image, ImageTk

from animation_manager import ANIMATION_TYPES, TRANSITION_KEYS

# Preview canvas size budget — the sheet is scaled to fit inside this.
_MAX_PREVIEW_WIDTH = 900
_MAX_PREVIEW_HEIGHT = 520

# Sensible default for the grey-gradient tolerance slider.
_DEFAULT_TOLERANCE = 30
_MAX_TOLERANCE = 150


class SpriteSheetImporter:
    """Modal Toplevel that slices a multi-animation sprite sheet into variants."""

    def __init__(self, parent, animation_manager, on_imported=None):
        """
        Args:
            parent: A tk.Toplevel (control panel) to parent the modal to.
            animation_manager: The AnimationManager instance to register into.
            on_imported: Optional callable() invoked after a successful import.
        """
        self._parent = parent
        self._anim_manager = animation_manager
        self._on_imported = on_imported

        # Source-image state
        self._source_image = None          # PIL.Image (RGB) of the full sheet
        self._source_width = 0
        self._source_height = 0
        self._display_scale = 1.0          # source_px * scale = display_px
        self._display_photo = None         # ImageTk.PhotoImage (pinned on self)

        # Background-key state
        self._bg_color = None              # (r, g, b) tuple once picked
        self._eyedropper_armed = False

        # Region drawing state
        self._regions = []                 # list of region dicts; see _add_region
        self._drag_start = None            # (display_x, display_y) or None
        self._drag_rect_id = None          # canvas id of the in-progress rectangle

        self._build_window()

    # --- Window scaffolding ---

    def _build_window(self):
        win = tk.Toplevel(self._parent)
        win.title('Import Sprite Sheet')
        win.geometry('980x780')
        win.transient(self._parent)
        win.grab_set()
        win.protocol('WM_DELETE_WINDOW', self._on_cancel)
        self._window = win

        root_frame = ttk.Frame(win, padding=10)
        root_frame.pack(fill=tk.BOTH, expand=True)

        # Pack order matters: the footer and region table anchor to the
        # bottom of root_frame BEFORE the canvas so they're always visible,
        # no matter how tall the preview grows. The canvas then fills the
        # remaining space between the top rows and the bottom widgets.
        self._build_file_row(root_frame)
        self._build_background_row(root_frame)
        self._build_footer(root_frame)
        self._build_region_table(root_frame)
        self._build_canvas(root_frame)

    def _build_file_row(self, parent):
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(row, text='Sprite sheet:').pack(side=tk.LEFT, padx=(0, 6))

        self._path_var = tk.StringVar()
        entry = ttk.Entry(row, textvariable=self._path_var, state='readonly')
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        ttk.Button(row, text='Browse…', command=self._on_browse).pack(side=tk.LEFT)

    def _build_background_row(self, parent):
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=(0, 8))

        self._pick_bg_btn = ttk.Button(row, text='Pick Background',
                                       command=self._on_arm_eyedropper)
        self._pick_bg_btn.pack(side=tk.LEFT, padx=(0, 8))

        self._bg_swatch = tk.Label(row, text='  no color  ',
                                   relief=tk.SUNKEN, width=14)
        self._bg_swatch.pack(side=tk.LEFT, padx=(0, 12))

        ttk.Label(row, text='Tolerance:').pack(side=tk.LEFT)

        self._tolerance_var = tk.IntVar(value=_DEFAULT_TOLERANCE)
        self._tolerance_scale = ttk.Scale(
            row, from_=0, to=_MAX_TOLERANCE,
            variable=self._tolerance_var, orient=tk.HORIZONTAL, length=220,
            command=self._on_tolerance_changed,
        )
        self._tolerance_scale.pack(side=tk.LEFT, padx=(6, 6))

        self._tolerance_readout = ttk.Label(row, text=str(_DEFAULT_TOLERANCE), width=4)
        self._tolerance_readout.pack(side=tk.LEFT)

        ttk.Label(row, text='   (higher = more grey removed)',
                  foreground='gray').pack(side=tk.LEFT)

    def _build_canvas(self, parent):
        frame = ttk.LabelFrame(parent, text='Preview — drag to mark each animation row', padding=6)
        frame.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        self._canvas = tk.Canvas(frame,
                                 width=_MAX_PREVIEW_WIDTH,
                                 height=_MAX_PREVIEW_HEIGHT,
                                 bg='#202020', highlightthickness=0, cursor='tcross')
        self._canvas.pack(fill=tk.BOTH, expand=True)

        self._canvas.bind('<Button-1>', self._on_canvas_press)
        self._canvas.bind('<B1-Motion>', self._on_canvas_drag)
        self._canvas.bind('<ButtonRelease-1>', self._on_canvas_release)

    def _build_region_table(self, parent):
        frame = ttk.LabelFrame(parent, text='Regions', padding=6)
        frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(0, 8))

        columns = ('bucket', 'variant', 'frames', 'box')
        self._tree = ttk.Treeview(frame, columns=columns, show='headings', height=5)
        self._tree.heading('bucket', text='Animation / Transition')
        self._tree.heading('variant', text='Variant name')
        self._tree.heading('frames', text='Frames')
        self._tree.heading('box', text='x, y, w, h (source px)')
        self._tree.column('bucket', width=200)
        self._tree.column('variant', width=160)
        self._tree.column('frames', width=70, anchor=tk.CENTER)
        self._tree.column('box', width=220)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        btns = ttk.Frame(frame)
        btns.pack(side=tk.RIGHT, fill=tk.Y, padx=(6, 0))
        ttk.Button(btns, text='Remove',
                   command=self._on_remove_region).pack(fill=tk.X, pady=(0, 4))
        ttk.Button(btns, text='Clear all',
                   command=self._on_clear_regions).pack(fill=tk.X)

    def _build_footer(self, parent):
        row = ttk.Frame(parent)
        row.pack(side=tk.BOTTOM, fill=tk.X, pady=(4, 0))

        ttk.Button(row, text='Cancel', command=self._on_cancel).pack(side=tk.RIGHT)
        ttk.Button(row, text='Import All',
                   command=self._on_import_all).pack(side=tk.RIGHT, padx=(0, 8))

    # --- File loading ---

    def _on_browse(self):
        path = filedialog.askopenfilename(
            title='Select sprite sheet',
            parent=self._window,
            filetypes=[('Images', '*.png *.jpg *.jpeg *.bmp'), ('All files', '*.*')],
        )
        if not path:
            return

        try:
            image = Image.open(path).convert('RGB')
        except Exception as exc:
            messagebox.showerror('Invalid image',
                                 f'Could not open image:\n{exc}',
                                 parent=self._window)
            return

        self._path_var.set(path)
        self._source_image = image
        self._source_width, self._source_height = image.size

        # Reset region state when switching files — stale regions from the
        # previous sheet would point at wrong source coordinates.
        self._regions.clear()
        self._refresh_region_table()
        self._bg_color = None
        self._update_bg_swatch()

        self._render_preview()

    def _render_preview(self):
        if self._source_image is None:
            return

        self._display_scale = min(
            _MAX_PREVIEW_WIDTH / self._source_width,
            _MAX_PREVIEW_HEIGHT / self._source_height,
            1.0,
        )

        display_w = max(1, int(self._source_width * self._display_scale))
        display_h = max(1, int(self._source_height * self._display_scale))

        preview = self._source_image.resize((display_w, display_h), Image.LANCZOS)
        self._display_photo = ImageTk.PhotoImage(preview)

        self._canvas.delete('all')
        self._canvas.config(width=display_w, height=display_h)
        self._canvas.create_image(0, 0, image=self._display_photo, anchor='nw',
                                  tags=('sheet_preview',))
        self._redraw_region_overlays()

    # --- Background picker ---

    def _on_arm_eyedropper(self):
        if self._source_image is None:
            messagebox.showinfo('Load a sheet first',
                                'Pick a sprite sheet before sampling the background.',
                                parent=self._window)
            return
        self._eyedropper_armed = True
        self._canvas.config(cursor='crosshair')
        self._pick_bg_btn.config(text='Click a bg pixel…')

    def _sample_background(self, display_x, display_y):
        sx, sy = self._display_to_source(display_x, display_y)
        if sx is None:
            return
        try:
            pixel = self._source_image.getpixel((sx, sy))
        except Exception:
            return
        # Source is RGB-converted above, so pixel is a 3-tuple.
        self._bg_color = tuple(int(v) for v in pixel[:3])
        self._update_bg_swatch()

    def _update_bg_swatch(self):
        if self._bg_color is None:
            self._bg_swatch.config(text='  no color  ', bg=self._window.cget('bg'),
                                   fg='black')
            return
        r, g, b = self._bg_color
        hex_color = f'#{r:02x}{g:02x}{b:02x}'
        # Choose text color that's readable on the swatch
        luminance = 0.299 * r + 0.587 * g + 0.114 * b
        text_fg = 'black' if luminance > 140 else 'white'
        self._bg_swatch.config(text=f' {hex_color.upper()} ', bg=hex_color, fg=text_fg)

    def _on_tolerance_changed(self, _value):
        self._tolerance_readout.config(text=str(self._tolerance_var.get()))

    # --- Canvas interactions ---

    def _on_canvas_press(self, event):
        if self._source_image is None:
            return

        if self._eyedropper_armed:
            self._sample_background(event.x, event.y)
            self._eyedropper_armed = False
            self._canvas.config(cursor='tcross')
            self._pick_bg_btn.config(text='Pick Background')
            return

        self._drag_start = (event.x, event.y)
        self._drag_rect_id = self._canvas.create_rectangle(
            event.x, event.y, event.x, event.y,
            outline='#ff4081', width=2, dash=(4, 2),
        )

    def _on_canvas_drag(self, event):
        if self._drag_start is None or self._drag_rect_id is None:
            return
        x0, y0 = self._drag_start
        self._canvas.coords(self._drag_rect_id, x0, y0, event.x, event.y)

    def _on_canvas_release(self, event):
        if self._drag_start is None or self._drag_rect_id is None:
            return

        x0, y0 = self._drag_start
        x1, y1 = event.x, event.y
        self._canvas.delete(self._drag_rect_id)
        self._drag_start = None
        self._drag_rect_id = None

        # Normalize + convert to source coordinates
        display_left, display_top = min(x0, x1), min(y0, y1)
        display_right, display_bottom = max(x0, x1), max(y0, y1)

        if display_right - display_left < 4 or display_bottom - display_top < 4:
            return  # Treat tiny drags as accidental clicks, ignore

        sx0, sy0 = self._display_to_source(display_left, display_top)
        sx1, sy1 = self._display_to_source(display_right, display_bottom)
        if sx0 is None or sx1 is None:
            return

        # Clamp to image bounds just in case
        sx0 = max(0, min(sx0, self._source_width - 1))
        sy0 = max(0, min(sy0, self._source_height - 1))
        sx1 = max(1, min(sx1, self._source_width))
        sy1 = max(1, min(sy1, self._source_height))

        region = {
            'bucket': ANIMATION_TYPES[0],
            'variant': '',
            'frame_count': 1,
            'source_box': (sx0, sy0, sx1 - sx0, sy1 - sy0),
        }
        self._prompt_region_details(region)

    def _display_to_source(self, display_x, display_y):
        """Convert canvas display coords back to source image coords."""
        if self._display_scale <= 0:
            return None, None
        sx = int(round(display_x / self._display_scale))
        sy = int(round(display_y / self._display_scale))
        if sx < 0 or sy < 0 or sx >= self._source_width or sy >= self._source_height:
            return None, None
        return sx, sy

    def _source_box_to_display(self, source_box):
        x, y, w, h = source_box
        s = self._display_scale
        return (int(x * s), int(y * s), int((x + w) * s), int((y + h) * s))

    # --- Region CRUD ---

    def _prompt_region_details(self, region):
        """Open a small modal asking for bucket / variant / frame count."""
        dialog = tk.Toplevel(self._window)
        dialog.title('Label region')
        dialog.geometry('380x190')
        dialog.resizable(False, False)
        dialog.transient(self._window)
        dialog.grab_set()

        frame = ttk.Frame(dialog, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text='Assign to:').grid(row=0, column=0, sticky=tk.W, pady=4)
        # Prefix animation types and transition keys so both fit in one combobox
        bucket_choices = (
            [f'animation: {t}' for t in ANIMATION_TYPES]
            + [f'transition: {k}' for k in TRANSITION_KEYS]
        )
        bucket_var = tk.StringVar(value=bucket_choices[0])
        bucket_combo = ttk.Combobox(frame, textvariable=bucket_var,
                                    values=bucket_choices, state='readonly', width=32)
        bucket_combo.grid(row=0, column=1, sticky=tk.W, pady=4, padx=(6, 0))

        ttk.Label(frame, text='Variant name:').grid(row=1, column=0, sticky=tk.W, pady=4)
        variant_var = tk.StringVar()
        ttk.Entry(frame, textvariable=variant_var, width=34).grid(
            row=1, column=1, sticky=tk.W, pady=4, padx=(6, 0))

        ttk.Label(frame, text='Frame count:').grid(row=2, column=0, sticky=tk.W, pady=4)
        frame_count_var = tk.IntVar(value=1)
        ttk.Spinbox(frame, from_=1, to=64, textvariable=frame_count_var, width=6).grid(
            row=2, column=1, sticky=tk.W, pady=4, padx=(6, 0))

        _x, _y, _w, _h = region['source_box']
        ttk.Label(frame, text=f'Box: {_x}, {_y}, {_w}×{_h} px',
                  foreground='gray').grid(row=3, column=0, columnspan=2, sticky=tk.W, pady=(4, 8))

        def submit():
            raw_bucket = bucket_var.get()
            variant_name = variant_var.get().strip()
            try:
                frame_count = int(frame_count_var.get())
            except (ValueError, tk.TclError):
                frame_count = 0

            if not variant_name:
                messagebox.showwarning('Missing name', 'Enter a variant name.',
                                       parent=dialog)
                return
            if frame_count <= 0:
                messagebox.showwarning('Invalid frame count',
                                       'Frame count must be at least 1.',
                                       parent=dialog)
                return
            if raw_bucket.startswith('animation: '):
                bucket_kind = 'animation'
                bucket_value = raw_bucket[len('animation: '):]
            elif raw_bucket.startswith('transition: '):
                bucket_kind = 'transition'
                bucket_value = raw_bucket[len('transition: '):]
            else:
                messagebox.showwarning('Bad bucket', 'Pick an animation or transition bucket.',
                                       parent=dialog)
                return

            region.update({
                'bucket_kind': bucket_kind,
                'bucket': bucket_value,
                'variant': variant_name,
                'frame_count': frame_count,
            })
            self._regions.append(region)
            self._refresh_region_table()
            self._redraw_region_overlays()
            dialog.destroy()

        ttk.Button(frame, text='Add region', command=submit).grid(
            row=4, column=0, columnspan=2, pady=(4, 0))

    def _on_remove_region(self):
        selection = self._tree.selection()
        if not selection:
            return
        # Tree item iids are string indices of the region list; parse back to int.
        try:
            index = int(selection[0])
        except ValueError:
            return
        if 0 <= index < len(self._regions):
            del self._regions[index]
            self._refresh_region_table()
            self._redraw_region_overlays()

    def _on_clear_regions(self):
        if not self._regions:
            return
        if messagebox.askyesno('Clear regions',
                               'Remove all regions?', parent=self._window):
            self._regions.clear()
            self._refresh_region_table()
            self._redraw_region_overlays()

    def _refresh_region_table(self):
        for row in self._tree.get_children():
            self._tree.delete(row)
        for i, region in enumerate(self._regions):
            bucket_kind = region.get('bucket_kind', 'animation')
            label = f"{bucket_kind}: {region['bucket']}"
            x, y, w, h = region['source_box']
            self._tree.insert('', tk.END, iid=str(i), values=(
                label, region['variant'], region['frame_count'], f'{x}, {y}, {w}×{h}',
            ))

    def _redraw_region_overlays(self):
        # Remove any prior overlay items but keep the sheet image
        self._canvas.delete('region_overlay')
        for i, region in enumerate(self._regions, start=1):
            dx0, dy0, dx1, dy1 = self._source_box_to_display(region['source_box'])
            self._canvas.create_rectangle(dx0, dy0, dx1, dy1,
                                          outline='#4caf50', width=2,
                                          tags=('region_overlay',))
            label = f"{i}. {region['variant']} ({region['frame_count']})"
            self._canvas.create_text(dx0 + 4, dy0 + 2, text=label,
                                     anchor='nw', fill='#4caf50',
                                     font=('Arial', 9, 'bold'),
                                     tags=('region_overlay',))

    # --- Import / slice ---

    def _on_import_all(self):
        if self._source_image is None:
            messagebox.showwarning('No sheet', 'Load a sprite sheet first.',
                                   parent=self._window)
            return
        if not self._regions:
            messagebox.showwarning('No regions',
                                   'Draw at least one region to import.',
                                   parent=self._window)
            return
        if self._bg_color is None:
            messagebox.showwarning('Pick background',
                                   'Use "Pick Background" on a background pixel so '
                                   'it can be keyed out in the sliced frames.',
                                   parent=self._window)
            return

        tolerance = int(self._tolerance_var.get())

        # Validate all regions up front so we don't partially write on failure.
        for i, region in enumerate(self._regions, start=1):
            x, y, w, h = region['source_box']
            if w <= 0 or h <= 0:
                messagebox.showerror('Bad region',
                                     f'Region {i} has zero width or height.',
                                     parent=self._window)
                return
            if region['frame_count'] <= 0:
                messagebox.showerror('Bad frame count',
                                     f'Region {i} has frame count <= 0.',
                                     parent=self._window)
                return
            if w // region['frame_count'] <= 0:
                messagebox.showerror('Too many frames',
                                     f'Region {i} is {w}px wide but splits into '
                                     f'{region["frame_count"]} frames — each frame '
                                     'would be less than 1 pixel wide.',
                                     parent=self._window)
                return

        imported = 0
        errors = []
        for region in self._regions:
            try:
                frames = self._slice_region(region, tolerance)
                if region['bucket_kind'] == 'animation':
                    self._anim_manager.register_variant_from_pil_frames(
                        region['bucket'], region['variant'], frames,
                    )
                else:
                    self._anim_manager.register_transition_from_pil_frames(
                        region['bucket'], region['variant'], frames,
                    )
                imported += 1
            except Exception as exc:
                errors.append(f"{region['bucket']}/{region['variant']}: {exc}")

        if errors:
            messagebox.showerror('Import partially failed',
                                 'Imported {ok}/{total}. Errors:\n\n{details}'.format(
                                     ok=imported, total=len(self._regions),
                                     details='\n'.join(errors)),
                                 parent=self._window)
        else:
            messagebox.showinfo('Import complete',
                                f'Imported {imported} regions.',
                                parent=self._window)
            if self._on_imported:
                try:
                    self._on_imported()
                except Exception as exc:
                    print(f'[WARNING] on_imported callback raised: {exc}')
            self._window.destroy()

    def _slice_region(self, region, tolerance):
        """Crop, color-key, and horizontally split into per-frame PIL images."""
        x, y, w, h = region['source_box']
        cropped = self._source_image.crop((x, y, x + w, y + h))

        keyed = self._apply_binary_color_key(cropped, self._bg_color, tolerance)

        frame_count = region['frame_count']
        frame_w = w // frame_count
        frames = []
        for i in range(frame_count):
            fx0 = i * frame_w
            fx1 = fx0 + frame_w
            frames.append(keyed.crop((fx0, 0, fx1, h)))
        return frames

    def _apply_binary_color_key(self, rgb_image, bg_rgb, tolerance):
        """Return an RGBA copy where pixels within `tolerance` of bg_rgb are fully
        transparent and all other pixels are fully opaque (no partial alpha)."""
        arr = np.asarray(rgb_image.convert('RGB'), dtype=np.int16)
        bg = np.asarray(bg_rgb, dtype=np.int16).reshape(1, 1, 3)
        distance = np.sqrt(((arr - bg) ** 2).sum(axis=-1))
        alpha = np.where(distance <= tolerance, 0, 255).astype(np.uint8)
        rgba = np.dstack([arr.astype(np.uint8), alpha])
        return Image.fromarray(rgba, mode='RGBA')

    # --- Cancel ---

    def _on_cancel(self):
        self._window.grab_release()
        self._window.destroy()
