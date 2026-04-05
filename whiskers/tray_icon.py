# tray_icon.py — System tray icon for Whiskers

import os
import threading

from PIL import Image, ImageDraw

import config

try:
    import pystray
    from pystray import MenuItem, Menu
    HAS_PYSTRAY = True
except ImportError:
    HAS_PYSTRAY = False
    print('[WARNING] pystray not installed. System tray icon disabled.')
    print('[INFO] Install with: pip install pystray')


def _create_fallback_icon():
    """Generate a simple 64x64 cat-face icon using Pillow."""
    size = 64
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Face circle
    draw.ellipse([8, 16, 56, 60], fill=(255, 165, 0, 255))

    # Left ear (triangle)
    draw.polygon([(12, 20), (6, 2), (26, 14)], fill=(255, 165, 0, 255))
    # Right ear
    draw.polygon([(52, 20), (58, 2), (38, 14)], fill=(255, 165, 0, 255))

    # Inner ears
    draw.polygon([(14, 20), (10, 6), (24, 16)], fill=(255, 200, 100, 255))
    draw.polygon([(50, 20), (54, 6), (40, 16)], fill=(255, 200, 100, 255))

    # Eyes
    draw.ellipse([18, 28, 28, 40], fill=(30, 30, 30, 255))
    draw.ellipse([36, 28, 46, 40], fill=(30, 30, 30, 255))

    # Eye highlights
    draw.ellipse([21, 30, 25, 34], fill=(255, 255, 255, 255))
    draw.ellipse([39, 30, 43, 34], fill=(255, 255, 255, 255))

    # Nose
    draw.polygon([(30, 42), (34, 42), (32, 46)], fill=(255, 130, 130, 255))

    # Mouth
    draw.arc([26, 44, 32, 52], start=0, end=180, fill=(80, 50, 50, 255), width=1)
    draw.arc([32, 44, 38, 52], start=0, end=180, fill=(80, 50, 50, 255), width=1)

    return img


def _load_icon():
    """Load the tray icon image, or generate a fallback."""
    if os.path.exists(config.TRAY_ICON_PATH):
        try:
            return Image.open(config.TRAY_ICON_PATH).resize((64, 64))
        except Exception:
            pass
    return _create_fallback_icon()


class TrayIcon:
    """System tray icon with Start/Stop/Settings/Quit menu."""

    def __init__(self, on_start, on_stop, on_settings, on_quit):
        """
        Args:
            on_start: Called when user clicks "Start Cat"
            on_stop: Called when user clicks "Stop Cat"
            on_settings: Called when user clicks "Settings..."
            on_quit: Called when user clicks "Quit"
        """
        self._on_start = on_start
        self._on_stop = on_stop
        self._on_settings = on_settings
        self._on_quit = on_quit
        self._icon = None
        self._cat_running = False

        if not HAS_PYSTRAY:
            return

        icon_image = _load_icon()

        self._icon = pystray.Icon(
            name='Whiskers',
            icon=icon_image,
            title='Whiskers AI Tutor Cat',
            menu=Menu(
                MenuItem('Start Cat', self._handle_start,
                         enabled=lambda item: not self._cat_running),
                MenuItem('Stop Cat', self._handle_stop,
                         enabled=lambda item: self._cat_running),
                Menu.SEPARATOR,
                MenuItem('Settings...', self._handle_settings),
                Menu.SEPARATOR,
                MenuItem('Quit', self._handle_quit),
            )
        )

    def _handle_start(self, icon, item):
        self._on_start()

    def _handle_stop(self, icon, item):
        self._on_stop()

    def _handle_settings(self, icon, item):
        self._on_settings()

    def _handle_quit(self, icon, item):
        self._on_quit()

    def start(self):
        """Run the tray icon in a daemon thread."""
        if not HAS_PYSTRAY or not self._icon:
            print('[INFO] Tray icon not available. Use Ctrl+C to quit.')
            return

        thread = threading.Thread(target=self._icon.run, daemon=True)
        thread.start()
        print('[INFO] System tray icon active.')

    def stop(self):
        """Remove the tray icon."""
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                pass

    def update_status(self, running: bool):
        """Update the cat running state (affects menu item availability)."""
        self._cat_running = running
        # pystray auto-refreshes enabled state via lambda checks
