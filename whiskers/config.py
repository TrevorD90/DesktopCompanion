# config.py — All configurable settings for Whiskers

# Student profile
STUDENT_NAME = "your child's name"      # Change this
STUDENT_GRADE = '4th grade'             # Change this
STUDENT_AGE = 9                         # Change this

# Wake word
WAKE_WORD = 'hey whiskers'              # Phrase to activate the cat
WAKE_WORD_SENSITIVITY = 0.5             # 0.0 (never) to 1.0 (always)

# Available openwakeword models (model_id, display_phrase)
WAKE_WORD_MODELS = [
    ('alexa_v0.1',       'Alexa'),
    ('hey_mycroft_v0.1', 'Hey Mycroft'),
    ('hey_jarvis_v0.1',  'Hey Jarvis'),
    ('hey_rhasspy_v0.1', 'Hey Rhasspy'),
]

# Quiet words to exit conversation mode
QUIET_WORDS = ['goodbye', 'bye', "that's all", 'bye whiskers', 'see you later']

# Whisper speech recognition
WHISPER_MODEL = 'base.en'               # base.en (English-only, ~150MB, strong accuracy for kid voices).
                                        # Alternatives: tiny / base / small.en / medium.en
WHISPER_LANGUAGE = 'en'

# AI providers
AI_PROVIDERS = [
    ('ollama',    'Local (Ollama)'),
    ('openai',    'OpenAI (Cloud)'),
    ('anthropic', 'Anthropic Claude (Cloud)'),
]

# Ollama AI brain
OLLAMA_BASE_URL = 'http://localhost:11434'
OLLAMA_MODEL = 'llama3.2:3b'            # or gemma3:9b, phi4:3.8b
OLLAMA_TIMEOUT = 60

# Cloud AI models
OPENAI_MODEL = 'gpt-4o-mini'
ANTHROPIC_MODEL = 'claude-haiku-4-5-20251001'

# Kokoro TTS
KOKORO_VOICE = 'af_bella'               # Warm female voice (default)
KOKORO_SPEED = 0.95                     # Slightly slower for children
KOKORO_LANG = 'a'                       # 'a' = American English
KOKORO_VOICES = [                        # Available voices for the settings panel
    # American Female
    ('af_alloy',    'Alloy (American Female)'),
    ('af_aoede',    'Aoede (American Female)'),
    ('af_bella',    'Bella (American Female)'),
    ('af_heart',    'Heart (American Female)'),
    ('af_jessica',  'Jessica (American Female)'),
    ('af_kore',     'Kore (American Female)'),
    ('af_nicole',   'Nicole (American Female)'),
    ('af_nova',     'Nova (American Female)'),
    ('af_river',    'River (American Female)'),
    ('af_sarah',    'Sarah (American Female)'),
    ('af_sky',      'Sky (American Female)'),
    # American Male
    ('am_adam',     'Adam (American Male)'),
    ('am_echo',     'Echo (American Male)'),
    ('am_eric',     'Eric (American Male)'),
    ('am_fenrir',   'Fenrir (American Male)'),
    ('am_liam',     'Liam (American Male)'),
    ('am_michael',  'Michael (American Male)'),
    ('am_onyx',     'Onyx (American Male)'),
    ('am_puck',     'Puck (American Male)'),
    # British Female
    ('bf_alice',    'Alice (British Female)'),
    ('bf_emma',     'Emma (British Female)'),
    ('bf_isabella', 'Isabella (British Female)'),
    ('bf_lily',     'Lily (British Female)'),
    # British Male
    ('bm_daniel',   'Daniel (British Male)'),
    ('bm_fable',    'Fable (British Male)'),
    ('bm_george',   'George (British Male)'),
    ('bm_lewis',    'Lewis (British Male)'),
]

# Cat window
CAT_SCALE = 3                           # Sprite scaling factor (1-5)
CAT_START_X = 100                       # Initial X position
CAT_START_Y = 600                       # Initial Y position
CAT_WALK_SPEED = 3                      # Pixels per frame
CAT_FPS = 12                            # Animation frames per second

# Sprite file paths (user supplies GIF files)
SPRITE_IDLE      = 'assets/sprites/idle.gif'
SPRITE_WALK_R    = 'assets/sprites/walk_right.gif'
SPRITE_WALK_L    = 'assets/sprites/walk_left.gif'
SPRITE_SLEEP     = 'assets/sprites/sleep.gif'
SPRITE_LISTEN    = 'assets/sprites/listen.gif'
SPRITE_THINK     = 'assets/sprites/think.gif'
SPRITE_TALK      = 'assets/sprites/talk.gif'
SPRITE_HAPPY     = 'assets/sprites/happy.gif'

# Animation manager
ANIMATIONS_CONFIG = 'data/animations.json'

# System tray icon
TRAY_ICON_PATH = 'assets/icon.png'

# Memory / data paths
MEMORY_FILE = 'data/student_memory.json'
LOG_FILE    = 'data/conversation_log.json'

# User settings persistence
SETTINGS_FILE = 'data/user_settings.json'


def _default_user_settings():
    return {
        'ai_provider': 'ollama',
        'openai_api_key': '',
        'anthropic_api_key': '',
        'wake_word_model': 'hey_jarvis_v0.1',
        'wake_word_sensitivity': WAKE_WORD_SENSITIVITY,
        'quiet_words': list(QUIET_WORDS),
        # Per-animation FPS overrides keyed by animation/transition name.
        # Unset keys inherit CAT_FPS — see CatWindow._current_anim_fps.
        'animation_fps': {},
        # Microphone input device. None = system default; int = sd.query_devices() index.
        'input_device_index': None,
    }


def load_user_settings():
    """Load user settings from JSON file. Creates default if missing."""
    import json, os
    os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        # Fill in any missing keys from defaults
        for k, v in _default_user_settings().items():
            data.setdefault(k, v)
        return data
    else:
        data = _default_user_settings()
        save_user_settings(data)
        return data


def save_user_settings(settings):
    """Save user settings to JSON file."""
    import json, os
    os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)


def get_setting(key, default=None):
    """Get a single setting value."""
    settings = load_user_settings()
    return settings.get(key, default)


def set_setting(key, value):
    """Set a single setting value and save."""
    settings = load_user_settings()
    settings[key] = value
    save_user_settings(settings)


# Speech bubble
BUBBLE_MAX_WIDTH = 320                  # Pixels
BUBBLE_FONT_SIZE = 12
BUBBLE_BG_COLOR  = '#FFFDE7'
BUBBLE_TEXT_COLOR = '#1A1A2E'
BUBBLE_DURATION  = 8000                 # ms to show bubble