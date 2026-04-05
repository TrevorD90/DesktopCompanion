# Whiskers — AI Tutor Cat

A desktop companion cat that teaches children using the Socratic method. Whiskers lives on your desktop as an animated pixel-art cat, listens for voice input, thinks with a local AI, and speaks back — always guiding with questions, never giving direct answers.

**100% local. 100% private. No data leaves your machine.**

---

## Prerequisites

- **Python 3.11+** — [python.org](https://python.org)
- **Ollama** — [ollama.com](https://ollama.com) (Windows installer)
- **espeak-ng** — [github.com/espeak-ng](https://github.com/espeak-ng/espeak-ng/releases) (needed for Kokoro TTS)

## Setup

### 1. Install Ollama and download a model

```bash
ollama pull llama3.2:8b
```

This downloads the AI model (~5 GB, one-time).

### 2. Install Python dependencies

```bash
cd whiskers
pip install -r requirements.txt
```

### 3. Get cat sprites

Whiskers needs animated GIF files for each state. Save them in `assets/sprites/` with these exact names:

| File | Description |
|------|-------------|
| `idle.gif` | Cat sitting and blinking |
| `walk_right.gif` | Cat walking to the right |
| `walk_left.gif` | Cat walking to the left (can mirror walk_right in code) |
| `sleep.gif` | Cat sleeping |
| `listen.gif` | Cat with perked ears (can reuse idle.gif) |
| `think.gif` | Cat with thoughtful expression (can reuse idle.gif) |
| `talk.gif` | Cat with open mouth / animated talking |
| `happy.gif` | Cat jumping or celebrating |

**Free sprite sources:**

| Source | URL | Notes |
|--------|-----|-------|
| OpenGameArt.org | opengameart.org/content/cat-sprites | Free, no attribution for some packs |
| itch.io | itch.io/game-assets/free/tag-cats | Many free pixel art cat packs |
| Elthen's Cat Sprites | elthen.itch.io/2d-pixel-art-cat-sprites | Idle, walk, sleep, paw |
| pyCatAI | github.com/R37r0-Gh057/pyCatAI-pet | Sprites already bundled |

If any sprite is missing, Whiskers will display a coloured rectangle placeholder and still run.

### 4. Configure

Open `config.py` and set:

```python
STUDENT_NAME = 'Your Child Name'   # Your child's name
STUDENT_GRADE = '4th grade'        # Their grade level
STUDENT_AGE = 9                    # Their age
```

You can also change the AI model:

```python
OLLAMA_MODEL = 'llama3.2:8b'      # See model table below
```

## Running

### Start Ollama (keep this terminal open):

```bash
ollama serve
```

### Start Whiskers:

```bash
python main.py
```

## Usage

- **Say "Hey Whiskers"** to activate the cat and start talking
- **Press F9** as a fallback if wake word detection isn't available
- **Drag the cat** anywhere on your desktop with the mouse
- **Right-click** for a context menu (Settings, Quit)
- **Press Ctrl+C** in the terminal to quit

## Choosing an AI Model

| Model | RAM Needed | Quality | Best For |
|-------|-----------|---------|----------|
| `phi4:3.8b` | 4-5 GB | Good | Low-end PC, fast responses |
| `llama3.2:8b` | 6-8 GB | Great | **Recommended** — best balance |
| `gemma3:9b` | 8-10 GB | Excellent | If you have 16 GB RAM |
| `llama3.3:70b` | 40+ GB | Best | High-end PC with good GPU |

Change the model in `config.py`:

```python
OLLAMA_MODEL = 'phi4:3.8b'  # or any model name from `ollama list`
```

Then pull it: `ollama pull phi4:3.8b`

## Customisation

### Changing the Voice

Change `KOKORO_VOICE` in `config.py`:

| Voice | Description |
|-------|-------------|
| `af_bella` | Warm American female (default, recommended) |
| `af_heart` | Gentle American female |
| `am_michael` | Calm American male |
| `bf_emma` | Warm British female |
| `bm_george` | Friendly British male |

Full list: `python -c "from kokoro import KPipeline; help(KPipeline)"`

### Changing the Wake Word

Update `WAKE_WORD` in `config.py`. Good options: `hey whiskers`, `yo whiskers`, `kitty help`, `hey teacher`. Shorter phrases (2-3 words) work best.

### Personalising the Teaching Style

Edit the `SYSTEM_PROMPT` in `ai_brain.py` — add lines to the `YOUR STUDENT` section with your child's favourite subjects, hobbies, or learning challenges.

### Adding Subjects to Memory

Edit `data/student_memory.json` directly. Copy the structure under `math` and paste it with a new subject name. Whiskers picks it up on next launch.

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Cat does not appear | Check sprite files exist in `assets/sprites/` with correct names |
| No voice output | Check espeak-ng is installed. Run: `python -c "from kokoro import KPipeline"` |
| No speech recognition | Check microphone is set as default in Windows Sound settings |
| Wake word not working | Use F9 fallback. Try: `pip install openwakeword --force-reinstall` |
| Ollama connection error | Ensure `ollama serve` is running in a terminal before launching |
| Response is too slow | Switch to a smaller model — try `phi4:3.8b` in config.py |
| Cat freezes | Reduce `CAT_FPS` in config.py from 12 to 8 |

---

**Whiskers AI Tutor Cat v1.0**
Built with Python, Ollama, Kokoro TTS, RealtimeSTT, and tkinter — 100% local, 100% private.
