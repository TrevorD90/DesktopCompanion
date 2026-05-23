# config.py — Configuration for the Socratic tutor.
#
# Pure constants live here; runtime-mutable settings live in
# data/user_settings.json (accessed via load/save/get/set_setting below).

# --- Wake word ------------------------------------------------------------

WAKE_WORD_SENSITIVITY = 0.5
WAKE_WORD_MODELS = [
    ('alexa_v0.1',       'Alexa'),
    ('hey_mycroft_v0.1', 'Hey Mycroft'),
    ('hey_jarvis_v0.1',  'Hey Jarvis'),
    ('hey_rhasspy_v0.1', 'Hey Rhasspy'),
]

# --- Whisper STT ----------------------------------------------------------

WHISPER_MODEL = 'tiny'        # tiny (fast) / base / small (more accurate)
WHISPER_LANGUAGE = 'en'

# --- Anthropic Claude -----------------------------------------------------

ANTHROPIC_MODEL = 'claude-haiku-4-5-20251001'

# --- Kokoro TTS -----------------------------------------------------------

KOKORO_VOICE = 'af_bella'
KOKORO_SPEED = 0.95           # Slightly slower; easier for a child to follow.
KOKORO_LANG = 'a'             # 'a' = American English
KOKORO_VOICES = [
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

# --- User settings persistence -------------------------------------------

SETTINGS_FILE = 'data/user_settings.json'


def _default_user_settings():
    return {
        'anthropic_api_key': '',
        'wake_word_model': 'hey_jarvis_v0.1',
        'wake_word_sensitivity': WAKE_WORD_SENSITIVITY,
        # TTS — ElevenLabs is the default for inflection quality. Falls back
        # to Kokoro (local, unlimited) on quota errors or missing key.
        'tts_provider': 'elevenlabs',          # 'elevenlabs' or 'kokoro'
        'elevenlabs_api_key': '',
        'elevenlabs_voice_id': '',             # blank = auto-pick first from /v1/voices
        'elevenlabs_model': 'eleven_multilingual_v2',
        'kokoro_voice': KOKORO_VOICE,
        'known_speakers': ['Rowan', 'Liam'],
        'name_pronunciations': {'Rowan': 'Roe-when'},
    }


# ElevenLabs models exposed in the settings UI. eleven_v3 is highest-quality
# but slower; eleven_flash_v2_5 is fastest. multilingual_v2 is the balanced default.
ELEVENLABS_MODELS = [
    ('eleven_multilingual_v2', 'Multilingual v2 (balanced, recommended)'),
    ('eleven_v3',              'v3 (best inflection, slower)'),
    ('eleven_flash_v2_5',      'Flash v2.5 (fastest, lower quality)'),
    ('eleven_turbo_v2_5',      'Turbo v2.5 (fast, mid quality)'),
]


def load_user_settings():
    """Load user settings from JSON file. Creates default if missing."""
    import json, os
    os.makedirs(os.path.dirname(SETTINGS_FILE), exist_ok=True)
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        for k, v in _default_user_settings().items():
            data.setdefault(k, v)
        return data
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
    settings = load_user_settings()
    return settings.get(key, default)


def set_setting(key, value):
    settings = load_user_settings()
    settings[key] = value
    save_user_settings(settings)
