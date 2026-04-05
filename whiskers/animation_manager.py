# animation_manager.py — Animation registry and variant resolution for Whiskers

import json
import os
import random
import shutil
import threading
from glob import glob

from PIL import Image

import config

# All recognized animation types
ANIMATION_TYPES = [
    'idle', 'walk', 'run', 'jump', 'teach',
    'sleep', 'listen', 'think', 'talk', 'happy'
]

# All meaningful transition keys (from_type_to_to_type)
TRANSITION_KEYS = [
    'sleep_to_idle',     # Wake up: laying → sitting
    'idle_to_sleep',     # Fall asleep: sitting → laying
    'idle_to_walk',      # Start walking: sit → stand → step
    'walk_to_idle',      # Stop walking: step → stand → sit
    'idle_to_run',       # Start running: crouch → sprint
    'run_to_idle',       # Stop running: skid → stand → sit
    'walk_to_run',       # Speed up: walk → run
    'run_to_walk',       # Slow down: run → walk
    'idle_to_listen',    # Perk up ears
    'listen_to_idle',    # Relax ears
    'idle_to_talk',      # Open mouth / prepare to speak
    'talk_to_idle',      # Close mouth
    'idle_to_happy',     # Start celebrating
    'happy_to_idle',     # Land from jump
    'think_to_talk',     # Lightbulb moment → start speaking
]

# Default mapping from CatState values to animation types
DEFAULT_STATE_TYPE_MAP = {
    'IDLE': 'idle',
    'WALK_RIGHT': 'walk',
    'WALK_LEFT': 'walk',
    'RUN_RIGHT': 'run',
    'RUN_LEFT': 'run',
    'SLEEP': 'sleep',
    'LISTEN': 'listen',
    'THINK': 'think',
    'TALK': 'talk',
    'HAPPY': 'happy',
}

# Fallback chain: if a type has no variants, try these instead
DEFAULT_FALLBACKS = {
    'sleep': 'idle',
    'listen': 'idle',
    'think': 'idle',
    'talk': 'teach',
    'happy': 'jump',
    'run': 'walk',
    'jump': 'idle',
    'teach': 'idle',
}

# Old flat sprite paths from config.py (for auto-migration)
_OLD_SPRITE_MAP = {
    'idle': 'SPRITE_IDLE',
    'walk': 'SPRITE_WALK_R',
    'sleep': 'SPRITE_SLEEP',
    'listen': 'SPRITE_LISTEN',
    'think': 'SPRITE_THINK',
    'talk': 'SPRITE_TALK',
    'happy': 'SPRITE_HAPPY',
}


def _default_config():
    """Create the default animations.json structure."""
    return {
        'animations': {t: {'variants': []} for t in ANIMATION_TYPES},
        'transitions': {k: {'variants': []} for k in TRANSITION_KEYS},
        'state_type_map': dict(DEFAULT_STATE_TYPE_MAP),
        'fallbacks': dict(DEFAULT_FALLBACKS),
    }


class AnimationManager:
    """Manages animation variants, resolves states to sprite frames."""

    def __init__(self, config_path=None):
        self._config_path = config_path or config.ANIMATIONS_CONFIG
        self._lock = threading.Lock()
        self._data = self._load_config()
        self._migrate_old_sprites()

    # --- Config persistence ---

    def _load_config(self):
        """Load animations.json or create default if missing."""
        os.makedirs(os.path.dirname(self._config_path), exist_ok=True)
        if os.path.exists(self._config_path):
            with open(self._config_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            # Ensure all types exist (forward-compatible)
            for t in ANIMATION_TYPES:
                if t not in data.get('animations', {}):
                    data.setdefault('animations', {})[t] = {'variants': []}
            # Ensure all transition keys exist
            if 'transitions' not in data:
                data['transitions'] = {}
            for k in TRANSITION_KEYS:
                if k not in data['transitions']:
                    data['transitions'][k] = {'variants': []}
            return data
        else:
            data = _default_config()
            self._save_config(data)
            return data

    def _save_config(self, data=None):
        """Write current config to disk."""
        if data is None:
            data = self._data
        os.makedirs(os.path.dirname(self._config_path), exist_ok=True)
        with open(self._config_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def save_config(self):
        """Public save (thread-safe)."""
        with self._lock:
            self._save_config()

    # --- Migration from old flat sprite files ---

    def _migrate_old_sprites(self):
        """Detect old flat sprite files and import them as default variants."""
        migrated = False
        for anim_type, config_attr in _OLD_SPRITE_MAP.items():
            old_path = getattr(config, config_attr, None)
            if old_path and os.path.exists(old_path):
                # Check if this type already has variants
                variants = self._data['animations'].get(anim_type, {}).get('variants', [])
                # Only migrate if no variants exist yet
                already_migrated = any(v['name'] == f'{anim_type}_default' for v in variants)
                if not already_migrated:
                    dest_dir = os.path.join('assets', 'sprites', anim_type, f'{anim_type}_default')
                    os.makedirs(dest_dir, exist_ok=True)
                    dest_file = os.path.join(dest_dir, os.path.basename(old_path))
                    if not os.path.exists(dest_file):
                        shutil.copy2(old_path, dest_file)
                    variant = {
                        'name': f'{anim_type}_default',
                        'path': dest_dir,
                        'format': 'gif',
                        'file': dest_file,
                    }
                    self._data['animations'][anim_type]['variants'].append(variant)
                    migrated = True
                    print(f'[MIGRATE] Imported {old_path} as {anim_type}_default')

        if migrated:
            self._save_config()

    # --- Variant resolution ---

    def get_animation_type_for_state(self, state_name):
        """Map a CatState string value to an animation type."""
        return self._data.get('state_type_map', DEFAULT_STATE_TYPE_MAP).get(
            state_name, 'idle'
        )

    def resolve_variant(self, anim_type, _depth=0):
        """Pick a random variant for the given type, following fallback chain if empty.

        Returns a variant dict or None if nothing found (even after fallbacks).
        """
        if _depth > 10:
            return None  # Prevent infinite loops in misconfigured fallbacks

        with self._lock:
            variants = self._data['animations'].get(anim_type, {}).get('variants', [])

        if variants:
            return random.choice(variants)

        # Follow fallback chain
        fallback = self._data.get('fallbacks', DEFAULT_FALLBACKS).get(anim_type)
        if fallback:
            return self.resolve_variant(fallback, _depth + 1)

        return None

    # --- Frame loading ---

    def load_variant_frames(self, variant, scale):
        """Load PIL Image frames from a variant (GIF or PNG folder).

        Args:
            variant: dict with 'format', 'path', and optionally 'file' keys
            scale: integer scale factor for sprites

        Returns:
            List of PIL.Image objects, or fallback frames if loading fails.
        """
        try:
            fmt = variant.get('format', 'frames')

            if fmt == 'gif':
                gif_path = variant.get('file') or ''
                if not gif_path or not os.path.exists(gif_path):
                    # Try to find any GIF in the variant path
                    gifs = glob(os.path.join(variant['path'], '*.gif'))
                    if gifs:
                        gif_path = gifs[0]
                    else:
                        return self._fallback_frames(scale)
                return self._load_gif(gif_path, scale)

            elif fmt == 'frames':
                return self._load_frame_folder(variant['path'], scale)

            else:
                return self._fallback_frames(scale)

        except Exception as e:
            print(f'[WARNING] Failed to load variant {variant.get("name", "?")}: {e}')
            return self._fallback_frames(scale)

    def _load_gif(self, path, scale):
        """Extract all frames from an animated GIF, scaled."""
        frames = []
        img = Image.open(path)
        try:
            while True:
                frame = img.copy().convert('RGBA')
                w, h = frame.size
                frame = frame.resize((w * scale, h * scale), Image.NEAREST)
                frames.append(frame)
                img.seek(img.tell() + 1)
        except EOFError:
            pass
        return frames if frames else self._fallback_frames(scale)

    def _load_frame_folder(self, folder_path, scale):
        """Load numbered PNG/image frames from a folder, sorted by name."""
        if not os.path.isdir(folder_path):
            return self._fallback_frames(scale)

        # Collect all image files, sorted by name
        extensions = ('*.png', '*.jpg', '*.jpeg', '*.bmp', '*.gif')
        image_files = []
        for ext in extensions:
            image_files.extend(glob(os.path.join(folder_path, ext)))
        image_files.sort()

        if not image_files:
            return self._fallback_frames(scale)

        frames = []
        for img_path in image_files:
            try:
                img = Image.open(img_path).convert('RGBA')
                w, h = img.size
                img = img.resize((w * scale, h * scale), Image.NEAREST)
                frames.append(img)
            except Exception as e:
                print(f'[WARNING] Could not load frame {img_path}: {e}')

        return frames if frames else self._fallback_frames(scale)

    def _fallback_frames(self, scale):
        """Create a colored rectangle placeholder."""
        size = 16 * scale
        img = Image.new('RGBA', (size, size), (255, 165, 0, 255))
        return [img]

    # --- Variant management (used by control panel) ---

    def register_variant(self, anim_type, variant_name, source_path):
        """Copy sprites from source_path to the proper directory and register.

        Args:
            anim_type: one of ANIMATION_TYPES
            variant_name: user-chosen name for this variant
            source_path: path to folder containing sprite frames (PNGs or GIF)

        Returns:
            The destination path where sprites were copied.
        """
        if anim_type not in ANIMATION_TYPES:
            raise ValueError(f'Unknown animation type: {anim_type}')

        # Sanitize variant name
        safe_name = ''.join(c if c.isalnum() or c in '_-' else '_' for c in variant_name)
        dest_dir = os.path.join('assets', 'sprites', anim_type, safe_name)
        os.makedirs(dest_dir, exist_ok=True)

        # Determine format by checking source contents
        source_gifs = glob(os.path.join(source_path, '*.gif'))
        source_images = (
            glob(os.path.join(source_path, '*.png')) +
            glob(os.path.join(source_path, '*.jpg')) +
            glob(os.path.join(source_path, '*.bmp'))
        )

        if source_images:
            # Prefer individual frames (PNGs/JPGs) over GIFs
            fmt = 'frames'
            for img_file in sorted(source_images):
                shutil.copy2(img_file, dest_dir)
            gif_dest = None
        elif source_gifs:
            # GIF-based variant
            fmt = 'gif'
            for gif in source_gifs:
                shutil.copy2(gif, dest_dir)
            gif_dest = os.path.join(dest_dir, os.path.basename(source_gifs[0]))
        else:
            raise ValueError(f'No image files found in {source_path}')

        # Build variant entry
        variant = {
            'name': safe_name,
            'path': dest_dir,
            'format': fmt,
        }
        if fmt == 'gif' and gif_dest:
            variant['file'] = gif_dest

        with self._lock:
            self._data['animations'][anim_type]['variants'].append(variant)
            self._save_config()

        print(f'[INFO] Registered animation variant: {anim_type}/{safe_name}')
        return dest_dir

    def remove_variant(self, anim_type, variant_name):
        """Remove a variant from the registry (does not delete files)."""
        with self._lock:
            variants = self._data['animations'].get(anim_type, {}).get('variants', [])
            self._data['animations'][anim_type]['variants'] = [
                v for v in variants if v['name'] != variant_name
            ]
            self._save_config()
        print(f'[INFO] Removed animation variant: {anim_type}/{variant_name}')

    def list_variants(self, anim_type):
        """Return list of variant dicts for the given type."""
        with self._lock:
            return list(self._data['animations'].get(anim_type, {}).get('variants', []))

    def get_all_types_summary(self):
        """Return {type: variant_count} for UI display."""
        with self._lock:
            return {
                t: len(self._data['animations'].get(t, {}).get('variants', []))
                for t in ANIMATION_TYPES
            }

    # --- Transition management ---

    def resolve_transition(self, from_type, to_type):
        """Check if a transition animation exists for from_type → to_type.

        Returns a random variant dict, or None if no transition exists.
        """
        key = f'{from_type}_to_{to_type}'
        with self._lock:
            variants = self._data.get('transitions', {}).get(key, {}).get('variants', [])
        if variants:
            return random.choice(variants)
        return None

    def register_transition(self, transition_key, variant_name, source_path):
        """Copy sprites and register a transition variant.

        Args:
            transition_key: one of TRANSITION_KEYS (e.g. 'idle_to_walk')
            variant_name: user-chosen name
            source_path: folder with sprite frames

        Returns:
            The destination path.
        """
        if transition_key not in TRANSITION_KEYS:
            raise ValueError(f'Unknown transition key: {transition_key}')

        safe_name = ''.join(c if c.isalnum() or c in '_-' else '_' for c in variant_name)
        dest_dir = os.path.join('assets', 'sprites', 'transitions', transition_key, safe_name)
        os.makedirs(dest_dir, exist_ok=True)

        # Detect format and copy files (same logic as register_variant)
        source_gifs = glob(os.path.join(source_path, '*.gif'))
        source_images = (
            glob(os.path.join(source_path, '*.png')) +
            glob(os.path.join(source_path, '*.jpg')) +
            glob(os.path.join(source_path, '*.bmp'))
        )

        if source_images:
            fmt = 'frames'
            for img_file in sorted(source_images):
                shutil.copy2(img_file, dest_dir)
            gif_dest = None
        elif source_gifs:
            fmt = 'gif'
            for gif in source_gifs:
                shutil.copy2(gif, dest_dir)
            gif_dest = os.path.join(dest_dir, os.path.basename(source_gifs[0]))
        else:
            raise ValueError(f'No image files found in {source_path}')

        variant = {
            'name': safe_name,
            'path': dest_dir,
            'format': fmt,
        }
        if fmt == 'gif' and gif_dest:
            variant['file'] = gif_dest

        with self._lock:
            self._data['transitions'][transition_key]['variants'].append(variant)
            self._save_config()

        print(f'[INFO] Registered transition: {transition_key}/{safe_name}')
        return dest_dir

    def remove_transition(self, transition_key, variant_name):
        """Remove a transition variant from the registry."""
        with self._lock:
            variants = self._data.get('transitions', {}).get(transition_key, {}).get('variants', [])
            self._data['transitions'][transition_key]['variants'] = [
                v for v in variants if v['name'] != variant_name
            ]
            self._save_config()
        print(f'[INFO] Removed transition: {transition_key}/{variant_name}')

    def list_transitions(self, transition_key):
        """Return list of variant dicts for the given transition key."""
        with self._lock:
            return list(
                self._data.get('transitions', {}).get(transition_key, {}).get('variants', [])
            )

    def get_all_transitions_summary(self):
        """Return {transition_key: variant_count} for UI display."""
        with self._lock:
            return {
                k: len(self._data.get('transitions', {}).get(k, {}).get('variants', []))
                for k in TRANSITION_KEYS
            }
