"""Remote Agent Protocol -- all user-facing settings live here.

Edit this file (or .env, for anything read via ``_env``) to swap models,
voices, personality, and VAD sensitivity. No need to touch application code.
"""

import getpass
import json
import os
from pathlib import Path

# Repository root (the directory that holds .env and VERSION). Anchoring
# paths here keeps `python -m remote_agent_protocol` working from any CWD.
_ROOT = Path(__file__).resolve().parent.parent
# All runtime state (conversation memory, vector store, app state, job
# history, logs, diagnostics) lives under data/ -- gitignored as a whole.
DATA_DIR = _ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _env(name: str, default: str) -> str:
    """Read simple KEY=value overrides from environment or .env."""
    value = os.getenv(name)
    if value:
        return value
    path = _ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, raw = stripped.split("=", 1)
            if key.strip() == name:
                return raw.strip().strip('"').strip("'")
    return default


def _env_bool(name: str, default: bool) -> bool:
    """Read a boolean env/.env flag with friendly values."""
    raw = _env(name, "true" if default else "false").strip().lower()
    return raw in {"1", "true", "yes", "y", "on"}


def _env_int_or_none(name: str) -> int | None:
    """Read an optional integer env/.env value; blank/missing means None."""
    raw = _env(name, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


def _parse_string_map(raw: str, name: str) -> dict[str, str]:
    """Parse an optional JSON object whose keys and values are strings."""
    if not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} must be valid JSON") from exc
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        raise ValueError(f"{name} must be a JSON object of string values")
    return value


def _parse_command_map(raw: str, name: str) -> dict[str, list[str]]:
    """Parse an optional JSON object of non-empty subprocess argument lists."""
    if not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} must be valid JSON") from exc
    if not isinstance(value, dict) or not all(
        isinstance(key, str)
        and isinstance(command, list)
        and command
        and all(isinstance(part, str) and part for part in command)
        for key, command in value.items()
    ):
        raise ValueError(f"{name} must be a JSON object of non-empty string arrays")
    return value


APP_NAME = "Remote Agent Protocol"
APP_TAGLINE = "voice switchboard for local and remote agents"


# ---------------------------------------------------------------------------
# Debug -- set DEBUG_MODE=true (env/.env) to see the full conversation flow in
# the terminal (VAD speech start/stop, transcriptions, LLM calls, MicGate
# state). Use this when she can't hear you / won't respond. Noisy but tells you
# exactly where the pipeline stalls. Off by default so normal runs stay quiet.
# ---------------------------------------------------------------------------
DEBUG_MODE = _env_bool("DEBUG_MODE", False)

# ---------------------------------------------------------------------------
# Audio devices -- leave unset to use the Windows default mic/speaker.
# If Jess grabs the wrong mic (common with webcams/headsets plugged in), run
#   .venv\Scripts\python scripts\list_audio_devices.py
# find your real mic's index, and set MIC_DEVICE_INDEX=<number> in .env
# (or edit the fallback here).
# ---------------------------------------------------------------------------
MIC_DEVICE_INDEX = _env_int_or_none("MIC_DEVICE_INDEX")
SPEAKER_DEVICE_INDEX = _env_int_or_none("SPEAKER_DEVICE_INDEX")

# ---------------------------------------------------------------------------
# Wake word -- when enabled AND openwakeword is installed, the session inserts
# a WakeWordGate ahead of STT: mic audio is dropped until you say the wake
# phrase, then the mic stays open for WAKE_WORD_ACTIVE_WINDOW_SECS (each bot
# reply refreshes the window so follow-ups don't need re-waking). If the engine
# or its model can't load, the session falls back to always-listening and says
# so in the transcript. Models auto-download to the openwakeword cache on first
# use (a few MB, one time).
# ---------------------------------------------------------------------------
WAKE_WORD_ENABLED = _env_bool("WAKE_WORD_ENABLED", False)
WAKE_WORD_ENGINE = _env("WAKE_WORD_ENGINE", "openwakeword")
WAKE_WORD_MODEL = _env("WAKE_WORD_MODEL", "hey_jarvis")
WAKE_WORD_THRESHOLD = float(_env("WAKE_WORD_THRESHOLD", "0.5"))
WAKE_WORD_ACTIVE_WINDOW_SECS = float(_env("WAKE_WORD_ACTIVE_WINDOW_SECS", "12"))
# Optional model -> persona overrides. When empty, locally installed wake
# models are matched to persona names (for example hey_jarvis -> Jarvis).
WAKE_WORD_PERSONAS = _parse_string_map(
    _env("WAKE_WORD_PERSONAS_JSON", ""), "WAKE_WORD_PERSONAS_JSON"
)
WAKE_WORD_SWITCH_TIMEOUT_SECS = float(_env("WAKE_WORD_SWITCH_TIMEOUT_SECS", "0.5"))

# ---------------------------------------------------------------------------
# STT (speech-to-text) -- how she hears you
# ---------------------------------------------------------------------------
# STT_ENGINE:
#   "whisper"   -- OpenAI Whisper via faster-whisper. WAY more accurate than
#                  Moonshine; on your RTX 5060 Ti the big models are still fast.
#                  This is the "amazing" transcription quality (a la Voicebox).
#   "moonshine" -- the original tiny ONNX model. Fastest + CPU-only, but the
#                  least accurate. Use if you want the GPU fully free.
#
# WHISPER_MODEL (friendly names, see stt_factory._MODEL_MAP):
#   "tiny" | "base" | "small" | "medium" | "large-v3"
#   "large-v3-turbo" (aka "turbo") -- BEST balance: near-large accuracy, fast on
#                                     GPU. ~1.6GB download on first run, ~1.5GB VRAM.
#   "distil-medium-en" -- English-only, small + fast, solid accuracy.
# WHISPER_DEVICE       : "cuda" (GPU, fast), "cpu", or "auto". Auto-falls back to
#                        CPU if the CUDA libs can't load.
# WHISPER_COMPUTE_TYPE : "float16" (GPU default), "int8" (CPU default), "int8_float16".
STT_ENGINE = _env("STT_ENGINE", "whisper")
WHISPER_MODEL = _env("WHISPER_MODEL", "large-v3-turbo")
WHISPER_DEVICE = _env("WHISPER_DEVICE", "cuda")
WHISPER_COMPUTE_TYPE = _env("WHISPER_COMPUTE_TYPE", "float16")

# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------
# Ollama serves any model you've registered via 'ollama create'.
# See models/README.md for how to load your own GGUFs from H:/Models.
#
# To list what's currently loaded in Ollama:
#   ollama list
#
# >>> FULL MODEL MAP: see docs/MODELS.md <<< (every GGUF on H:\Models, with flags)
#
# Already registered & usable right now:
#   "llama3.2:1b"    -- tiny, instant
#   "hermes-20b"     -- OpenAI-20B NEO Heretic Uncensored (current default, smart but 15GB)
#   "gemma-12b"      -- Gemma 4 12B AEON Abliterated
#   "llama3-heretic" -- Llama3.3 8B Thinking Heretic (NOTE: thinks-aloud)
#
# Pre-mapped voice-friendly picks (register first:
#   ollama create <name> -f models\<name>.Modelfile ):
#   "gemma-e4b-max"        -- 4.8GB uncensored, FAST -- great for snappy voice
#   "gemma-e4b-aggressive" -- 5.0GB uncensored, fast
#   "gemma-12b-huihui"     -- 6.9GB uncensored 12B -- best all-round (ralph's pick)
#   "gemma-12b-duo"        -- 8.0GB uncensored 12B
#   "granite-tiny"         -- 6.9GB IBM Granite, clean
#   "nemotron-4b"          -- 3.9GB NVIDIA, tiny+fast
#   "devstral-24b"         -- 12.6GB uncensored CODING model
#
# AVOID for voice (they speak their <think> monologue): qwen3*, deepseek*,
# anything 'Thinking'/'Reasoning'. See docs/MODELS.md for the full list + why.
LLM_MODEL = _env("LLM_MODEL", "gemma-e4b-max")

# ---------------------------------------------------------------------------
# Thinking / reasoning -- THE big latency lever for voice
# ---------------------------------------------------------------------------
# gemma-e4b-max (and most of your H:\Models picks) are "thinking" models: left
# unchecked they burn the ENTIRE response budget on a hidden <think> monologue
# you never hear, which starves the spoken answer and adds tens of seconds.
# Ollama honours OpenAI's `reasoning_effort` field on its /v1 endpoint, and the
# special value "none" switches thinking OFF entirely -> ~0.4s replies instead
# of ~40s. Set to None to leave the model's default (thinking ON) untouched.
#
# Measured on this box (RTX 5060 Ti, gemma-e4b-max):
#   thinking ON : ~40s, 300+ tokens (most never spoken)
#   thinking OFF: ~0.4s, ~25 tokens, clean 1-2 sentence reply
LLM_REASONING_EFFORT = "none"

# The OpenAI-compatible client needs /v1 while mem0 and health checks need the
# bare host. Keep one override so every Ollama consumer follows the same server.
OLLAMA_HOST = _env("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
OLLAMA_BASE_URL = f"{OLLAMA_HOST}/v1"

# ---------------------------------------------------------------------------
# Personality & voice -- now driven by PERSONAS
# ---------------------------------------------------------------------------
# Personalities and voices are no longer hardcoded here. They live as real
# data you can pick from at runtime (and swap live in the GUI):
#   * personas.py -- named characters = a voice + a personality (Jess, Butler,
#                    Gremlin, Noir, Coach, Zen). Add your own there.
#   * voices.py   -- the full Kokoro voice catalog (~50 voices), grouped.
#
# DEFAULT_PERSONA_NAME is who both the terminal demo and the GUI boot up as
# when there's no remembered pick. Must match a `name` in personas.PERSONAS.
DEFAULT_PERSONA_NAME = _env("DEFAULT_PERSONA_NAME", "Jess")

# The GUI remembers your last persona/tool-user picks here so a restart boots
# as the character you actually use. Set APP_STATE_FILE="" to always boot the
# default instead.
APP_STATE_FILE = _env("APP_STATE_FILE", str(DATA_DIR / "jess_app_state.json"))

# ---------------------------------------------------------------------------
# TTS backend -- how she speaks
# ---------------------------------------------------------------------------
# "kokoro"   = local/offline, current familiar voice catalog (fast, no API cost)
# "cartesia" = cloud neural TTS via Cartesia (API key in .env as CARTESIA_API_KEY)
# "voicebox" = local Voicebox app REST TTS, including cloned voice profiles
TTS_BACKEND = _env("TTS_BACKEND", "kokoro")
CARTESIA_MODEL = "sonic-3.5"
# "http" is safest/validated for new accounts; "websocket" is lower-latency but
# this account currently gets HTTP 401 on Pipecat's websocket endpoint.
CARTESIA_TRANSPORT = _env("CARTESIA_TRANSPORT", "http")
# Skylar - Friendly Guide (validated from /voices with your account)
CARTESIA_VOICE_ID = "db6b0ed5-d5d3-463d-ae85-518a07d3c2b4"
CARTESIA_SAMPLE_RATE = 24000
CARTESIA_MAX_BUFFER_DELAY_MS = 0
CARTESIA_SPEED = 1.0
CARTESIA_EMOTION = "excited"

VOICEBOX_BASE_URL = _env("VOICEBOX_BASE_URL", "http://127.0.0.1:18080")
VOICEBOX_SERVER_EXE = _env(
    "VOICEBOX_SERVER_EXE",
    str(_ROOT.parent / "Voicebox" / "voicebox-server.exe"),
)
VOICEBOX_DATA_DIR = _env(
    "VOICEBOX_DATA_DIR", str(Path.home() / "AppData" / "Roaming" / "sh.voicebox.app")
)
VOICEBOX_DEFAULT_ENGINE = _env("VOICEBOX_DEFAULT_ENGINE", "qwen")
VOICEBOX_DEFAULT_MODEL = _env("VOICEBOX_DEFAULT_MODEL", "1.7B")
VOICEBOX_SAMPLE_RATE = int(_env("VOICEBOX_SAMPLE_RATE", "24000"))
VOICEBOX_WARMUP_ENABLED = _env("VOICEBOX_WARMUP_ENABLED", "1").lower() not in {"0", "false", "no"}
VOICEBOX_WARMUP_TEXT = _env("VOICEBOX_WARMUP_TEXT", "hi")
VOICEBOX_WARMUP_DELAY_SECS = float(_env("VOICEBOX_WARMUP_DELAY_SECS", "8"))

# ---------------------------------------------------------------------------
# VAD (Voice Activity Detection) -- how the bot knows when you've stopped talking
# ---------------------------------------------------------------------------
# confidence  : 0.0-1.0. Higher = harder to trigger. Prevents speaker bleed.
# start_secs  : seconds of speech before a turn starts. Higher = less jumpy.
# stop_secs   : seconds of silence before your turn ends. Higher = more patient.
# NOTE: 0.85 was too strict -- quieter/farther speech never crossed the bar and
# Jess never registered a turn. 0.6 is a much more forgiving starting point.
# Lower = more sensitive (easier to trigger). Raise if she false-triggers on noise.
VAD_CONFIDENCE = 0.6
VAD_START_SECS = 0.2
VAD_STOP_SECS = 0.6

# ---------------------------------------------------------------------------
# Memory -- the bot remembers conversations across restarts
# ---------------------------------------------------------------------------
# MEMORY_ENABLED   : set False to go back to goldfish mode (forgets on exit).
# MEMORY_FILE      : where the conversation transcript is saved.
# MEMORY_MAX_MSGS  : only the last N messages are kept/reloaded. Keeps the
#                    context window from overflowing on a small local model.
#                    Set to 0 for unlimited (not recommended).
MEMORY_ENABLED = True
MEMORY_FILE = str(DATA_DIR / "jess_memory.json")
# 40 real turns is a lot of prompt for a CPU-bound local model to prefill every
# single inference. 24 keeps her snappy while still feeling like she remembers
# the conversation. Bump it back up if you miss the longer recall.
MEMORY_MAX_MSGS = 24

# One-shot kickoff instructions we feed the model at startup. These are
# DIRECTIONS to the model, not things the user actually said, so memory.py
# strips them before persisting (otherwise they pile up in the transcript).
KICKOFF_RETURNING = "The user just came back. Greet them warmly in one short sentence."
KICKOFF_FIRST = "Please introduce yourself briefly."

# ---------------------------------------------------------------------------
# Long-term semantic memory (mem0) -- the "heavy-duty" recall
# ---------------------------------------------------------------------------
# This is layered ON TOP of the transcript memory above. mem0 distills FACTS
# from your chats into a local vector DB and semantically recalls them later
# ("what pets do I have?" finds "has a dog named Pixel" even months on).
# 100% local: Ollama for the extractor LLM + embeddings, embedded Qdrant store.
#
# Requires (already installed): mem0ai, ollama python pkg, and the
# 'nomic-embed-text' Ollama model (ollama pull nomic-embed-text).
#
# MEM0_ENABLED        : master switch for semantic memory.
# MEM0_USER_ID        : whose memories these are (namespaces the vector store).
# MEM0_LLM_MODEL      : small/fast model used to EXTRACT facts (not the chat model).
# MEM0_EMBED_MODEL    : Ollama embedding model (768-dim).
# MEM0_COLLECTION     : Qdrant collection name.
# MEM0_QDRANT_PATH    : local folder for the embedded vector DB.
# MEM0_SEARCH_LIMIT   : max memories injected per turn.
# MEM0_SEARCH_THRESHOLD: min similarity (0-1) for a memory to count as relevant.
MEM0_ENABLED = True
MEM0_USER_ID = _env("MEM0_USER_ID", getpass.getuser().lower())
MEM0_LLM_MODEL = "llama3.2:1b"
MEM0_EMBED_MODEL = "nomic-embed-text"
MEM0_COLLECTION = "jess_memories"
MEM0_QDRANT_PATH = str(DATA_DIR / "jess_qdrant")
MEM0_SEARCH_LIMIT = 5
MEM0_SEARCH_THRESHOLD = 0.1

# ---------------------------------------------------------------------------
# Agent bridge -- delegate long tasks to external agents (Hermes/OpenClaw/...)
# WITHOUT blocking the voice loop. Jobs run as background subprocesses; Jess
# announces updates the moment they arrive (async through and through).
#
# AGENT_BACKENDS: name -> command template. Placeholders:
#   {task}   -> the task text        {python} -> this venv's python
# "mock" ships with the repo for testing. Add real agents once installed, e.g.
#   "hermes":   ["hermes", "-p", "{task}"]
#   "openclaw": ["openclaw", "run", "{task}"]
# (exact flags depend on the installed CLI -- adjust after `hermes --help`).
#
# AGENT_ANNOUNCE: when True, a finished/failed job injects an update into the
# conversation and Jess SPEAKS it immediately.
# ---------------------------------------------------------------------------
AGENT_BACKENDS = {
    "mock": ["{python}", "-u", str(_ROOT / "scripts" / "mock_agent.py"), "{task}"],
    # Hermes Agent (NousResearch) -- installed at %LOCALAPPDATA%\hermes.
    # -z = one-shot headless prompt; prints the answer to stdout and exits.
    # Verified: `hermes -z "Reply with exactly: BRIDGE OK"` -> BRIDGE OK (~11s).
    "hermes": ["hermes", "-z", "{task}"],
    # Same, but auto-approves tool calls (file edits, shell, browsing...).
    # Powerful and DANGEROUS -- pick it knowingly, don't make it the default.
    "hermes-yolo": ["hermes", "--yolo", "-z", "{task}"],
    # Code Puppy -- best for CODING tasks in a repo (pair with a working dir).
    # -p = one-shot prompt. Verified: `code-puppy -p "..."` -> answer, exit 0.
    "code-puppy": ["code-puppy", "-p", "{task}"],
    **_parse_command_map(_env("AGENT_BACKENDS_JSON", ""), "AGENT_BACKENDS_JSON"),
}
# Deterministic voice targets. Provider names are not guessed at runtime: each
# maps to the exact model key/flags supported by that agent's one-shot CLI.
AGENT_MODEL_TARGETS = {
    "code-puppy": {
        "openai": {
            "label": "OpenAI GPT-5.5",
            "args": ["--model", "chatgpt-gpt-5.5"],
        }
    },
    "hermes": {
        "openai": {
            "label": "OpenAI GPT-5.5",
            "args": ["--provider", "openai-api", "--model", "gpt-5.5"],
        }
    },
    "hermes-yolo": {
        "openai": {
            "label": "OpenAI GPT-5.5",
            "args": ["--provider", "openai-api", "--model", "gpt-5.5"],
        }
    },
}
_LOCAL_MACHINE = _env("AGENT_LOCAL_MACHINE", "Main PC")
AGENT_MACHINES = {
    **{name: _LOCAL_MACHINE for name in AGENT_BACKENDS},
    **_parse_string_map(_env("AGENT_MACHINES_JSON", ""), "AGENT_MACHINES_JSON"),
}
AGENT_ANNOUNCE = True

# Read-only lifecycle stream for local dashboards. The host is intentionally
# fixed to loopback; exposing job metadata on the LAN requires an authenticated
# protocol and is outside v1.
LIFECYCLE_WS_ENABLED = _env_bool("LIFECYCLE_WS_ENABLED", True)
LIFECYCLE_WS_HOST = "127.0.0.1"
LIFECYCLE_WS_PORT = int(_env("LIFECYCLE_WS_PORT", "8765"))
LIFECYCLE_WS_PATH = "/events"
LIFECYCLE_WS_QUEUE_SIZE = int(_env("LIFECYCLE_WS_QUEUE_SIZE", "64"))

# How long a delegated job may run before it's force-stopped, in seconds. Five
# minutes covers normal lookups while preventing a silent backend from hanging
# forever; set 0 only for deliberately unbounded interactive work.
AGENT_JOB_TIMEOUT_SECS = float(_env("AGENT_JOB_TIMEOUT_SECS", "300"))
# Grace period between a polite terminate() and a hard kill() when a job is
# cancelled or times out. Gives the agent a moment to flush output and exit.
AGENT_JOB_KILL_GRACE_SECS = float(_env("AGENT_JOB_KILL_GRACE_SECS", "3"))
# Silent jobs emit a UI heartbeat at this interval. Voice heartbeats are
# separately throttled so the transcript stays useful without becoming noisy.
AGENT_PROGRESS_INTERVAL_SECS = float(_env("AGENT_PROGRESS_INTERVAL_SECS", "30"))
AGENT_VOICE_PROGRESS_MIN_SECS = float(_env("AGENT_VOICE_PROGRESS_MIN_SECS", "20"))
AGENT_VOICE_PROGRESS_INTERVAL_SECS = float(_env("AGENT_VOICE_PROGRESS_INTERVAL_SECS", "60"))
# A structured completion marker is authoritative; give the CLI a moment to
# exit normally, then stop the stale wrapper process that caused the Paint bug.
AGENT_COMPLETION_GRACE_SECS = float(_env("AGENT_COMPLETION_GRACE_SECS", "2"))

# Finished jobs are appended here so the Agents panel isn't blank after every
# restart. Set AGENT_HISTORY_FILE="" to disable persistence entirely.
AGENT_HISTORY_FILE = _env("AGENT_HISTORY_FILE", str(DATA_DIR / "jess_agent_history.json"))

# Where delegated agents run when no explicit directory is given. Voice jobs
# used to inherit Jess's own working directory -- this repo -- which is how a
# mistranslated task ended with CodePuppy editing this codebase for five
# minutes (jess_runtime.log 2026-07-05 12:35). A neutral, gitignored sandbox
# keeps an agent from ever waking up inside its host's source tree.
AGENT_WORKSPACE_DIR = _env("AGENT_WORKSPACE_DIR", str(DATA_DIR / "agent_workspace"))

# Scope preamble prepended to every dispatched task (the status protocol is
# appended). A coding agent handed a vague task treats whatever directory it
# stands in as the thing to change; this tells it not to.
AGENT_SCOPE_PREAMBLE = (
    "[Scope: you are a general-purpose executor running one task for a "
    "voice-assistant host. Your working directory ({cwd}) is a scratch "
    "workspace, not the subject of the task -- do not modify its files unless "
    "the task explicitly asks for code changes there. If the task is ambiguous "
    "about where or on what to act, say so and stop instead of guessing.]"
)

# Repository whose working tree must never be silently modified by an agent
# job (this application's own source). Compared via `git status --porcelain`
# before and after every job; a difference flags the job and is announced out
# loud. Empty disables the check.
AGENT_HOST_REPO = _env("AGENT_HOST_REPO", str(_ROOT))
AGENT_HISTORY_MAX = int(_env("AGENT_HISTORY_MAX", "100"))

# Confirmation gate -- auto-parsed delegations (from natural-language voice or
# typed chat) can trigger real filesystem/web/shell actions. Before dispatching
# a *destructive* task, Jess holds the job and asks the user to confirm (spoken
# "yes"/"confirm", or the GUI Approve button). Picking an elevated backend (e.g.
# "hermes-yolo") is not itself a trigger -- selecting it already is the risk
# acknowledgment. Manual, deliberate dispatch (the Delegate button, the Agents
# panel) is never gated -- the click already IS the confirmation.
AGENT_CONFIRM_ENABLED = _env_bool("AGENT_CONFIRM_ENABLED", True)
# Some agent backends are one-shot CLIs: instead of actually doing the work,
# they sometimes print a "should I proceed? say confirm/cancel" gate of their
# own and exit. We resolve that by holding a fresh confirmation and relaunching
# on approval (see agent_bridge.requests_confirmation / session._hold_agent_confirmation).
# If the SAME agent does this this many times in a row with no real result in
# between, stop looping and tell the user instead of relaunching forever.
AGENT_CONFIRM_LOOP_LIMIT = int(_env("AGENT_CONFIRM_LOOP_LIMIT", "2"))
# Task text containing any of these verbs is treated as destructive and needs
# confirming regardless of which backend it targets.
AGENT_DESTRUCTIVE_WORDS = (
    "delete",
    "remove",
    " rm ",
    "erase",
    "wipe",
    "format",
    "install",  # covers "uninstall" too -- both mutate the system either way
    "uninstall",
    "drop",
    "destroy",
    "shutdown",
    "shut down",
    "reboot",
    "overwrite",
)

# Spoken-name -> backend for VOICE delegation ("Jess, tell Hermes to ...").
# Deterministic dispatch: parsed from your transcript by voice_commands.py,
# never left to the LLM's imagination (it WILL claim it delegated. It lied.)
# Want spoken "hermes" to have real tool powers? Point it at "hermes-yolo".
AGENT_SPOKEN_ALIASES = {
    "hermes": "hermes",
    "hermes yolo": "hermes-yolo",
    "laptop hermes": "laptop-hermes",
    "openclaw": "openclaw",
    "open claw": "openclaw",
    "code puppy": "code-puppy",
    "the code puppy": "code-puppy",
    "puppy": "code-puppy",
    "mock": "mock",
    "the mock agent": "mock",
}

# Implicit delegation: no agent named, but the request is clearly a real-world
# action ("write a file on my desktop", "search the web for X"). Parsed
# deterministically by voice_commands.parse_implicit_task -- chat requests
# ("write me a poem") never match and stay with Jess.
AGENT_AUTO_DELEGATE = True
AGENT_DEFAULT_BACKEND = _env("AGENT_DEFAULT_BACKEND", "hermes")

# ---------------------------------------------------------------------------
# Intent router -- the semantic net over user requests (intent_router.py).
# Tiers run cheapest first: explicit agent commands, then a small-talk gate
# (pure acknowledgments never classify), then vague capability references
# ("there's a package for X, forgot the name" -- shipped verbatim, held for
# confirmation), then the keyword net above (a free hit dispatches
# immediately), and ONLY then an LLM intent classifier -- a schema-constrained
# Ollama call that judges the user's GOAL, not their wording. Each decision is
# logged and emitted to the GUI as a "routing" event with intent, category,
# confidence, tier, and reason.
# ---------------------------------------------------------------------------
INTENT_ROUTER_ENABLED = _env_bool("INTENT_ROUTER_ENABLED", True)
# Any local Ollama model tag. Defaults to mem0's tiny model, which this setup
# already keeps on disk and which classifies a short utterance in well under
# a second -- classification is an easy task, it does not need the big voice
# model. Kept separate from the voice model so the two never fight for VRAM.
INTENT_MODEL = _env("INTENT_MODEL", "") or MEM0_LLM_MODEL
# Classifier budget per utterance; on timeout the utterance stays chat (the
# free tiers have already had their say), so a slow/busy Ollama can delay a
# turn by at most this much and never stalls the pipeline.
INTENT_TIMEOUT_SECS = float(_env("INTENT_TIMEOUT_SECS", "1.5"))
# At/above dispatch confidence a task runs. Between confirm and dispatch is
# the uncertain band: read-only lookups run anyway (a wrong lookup is
# harmless), state-changing tasks are held for a spoken yes/no. Below the
# band the utterance stays chat.
INTENT_DISPATCH_CONFIDENCE = 0.75
INTENT_CONFIRM_CONFIDENCE = 0.5

# Task template for vague capability references ("there's a package that does
# X, I forgot the name, make sure we have it"). The utterance ships verbatim:
# rewriting it is exactly how the YouTube-skill request became the generic
# task "Enable YouTube video watching on the computer" (jess_runtime.log
# 2026-07-05 12:35). The agent's job is identify -> verify -> only then install.
VAGUE_CAPABILITY_TASK_TEMPLATE = (
    "The user is referring to a specific package, skill, or tool they cannot "
    'name exactly. Their request, verbatim: "{utterance}". First identify the '
    "most likely candidates from that description (search if needed), then "
    "check whether this machine already has the best match, and install or "
    "enable it only if it is missing and clearly what they want. If no "
    "candidate is a clear match, report the options back instead of guessing. "
    "Do NOT reinterpret this as a request to perform the end task yourself. "
    "The capability may belong to the tool agent itself (a plugin, skill, or "
    "MCP server) rather than to any codebase -- check your own extension "
    "mechanisms first."
)

# LLM-driven fallback delegation. The deterministic parsers above can't cover
# every phrasing, so the persona is also told (LLM_DELEGATE_STYLE) to embed
# [[delegate: task]] in its reply whenever the user needs live information or a
# real-world action it can't perform. LLMDelegateTap strips the marker from the
# spoken text and dispatches the task through the same confirmation gate --
# dispatch still happens in code, the LLM only gets to *request* it.
AGENT_LLM_DELEGATE = _env_bool("AGENT_LLM_DELEGATE", True)
LLM_DELEGATE_STYLE = (
    " You have no internet access and cannot see files, apps, or live data "
    "yourself; a tool agent does real-world work for you. The ONLY way to "
    "engage it is to include the exact marker [[delegate: clear task "
    "description]] in your reply -- saying you are checking, dispatching, "
    "summoning, or verifying something does NOTHING unless that same reply "
    "carries the marker. When the user wants live information (weather, news, "
    "prices, places, directions, sports results) or an action you cannot do, "
    "never invent an answer and never pretend work is happening: say in one "
    "short sentence that you're sending it to your agent, and include the "
    "marker. If something cannot be done, say so plainly instead of promising."
)

# Nouns the persona may use for its tool agent (backend names and spoken
# aliases are matched too). If an LLM reply pairs one of these with a
# dispatch-style verb but carries no [[delegate: ...]] marker, nothing was
# actually sent -- the session holds the original request as a real pending
# confirmation instead of trusting a second LLM response to emit the marker.
AGENT_PROMISE_NOUNS = ("agent", "bat computer")

# Appended to the system prompt and refreshed on every user turn so the model
# knows the real date/time (it has no clock) and who its tool agent is.
RUNTIME_CONTEXT_TEMPLATE = "\n\nCurrent local date and time: {now}. Your tool agent is '{agent}'."

# What the LLM is told INSTEAD of your raw command once the job is actually
# dispatched -- so her acknowledgment is truthful, short, and in-character.
DELEGATION_ACK_PROMPT = (
    "[Voice delegation dispatched -- you just sent this task to agent "
    "'{agent}': {task}. Tell the user in ONE short sentence that it's "
    "running and you'll speak up when it finishes.]"
)
# How the update is handed to the LLM. Keep it short so the spoken reply is short.
AGENT_UPDATE_PROMPT = (
    "[Background task update -- relay this to the user in ONE short sentence, "
    "in your own voice: {update}]"
)
# Spoken when a delegation is HELD for confirmation instead of dispatched.
DELEGATION_CONFIRM_PROMPT = (
    "[A delegation needs the user's confirmation before it runs -- it could change "
    "files or the system. In ONE short sentence, tell them you're about to have "
    "agent '{agent}' do this: {task} -- and ask them to say 'confirm' to proceed or "
    "'cancel' to stop.]"
)
AGENT_CONFIRM_APPROVED_PROMPT = (
    "[The user confirmed. In ONE short sentence, tell them agent '{agent}' is now "
    "running the task and you'll speak up when it finishes.]"
)
AGENT_CONFIRM_DENIED_PROMPT = (
    "[The user cancelled the delegation to agent '{agent}'. Acknowledge in ONE short "
    "sentence that you did NOT run it.]"
)
# Task strings that mean the model parroted a prompt example instead of naming
# a real task; markers carrying one of these are ignored rather than dispatched.
DELEGATION_PLACEHOLDER_TASKS = ("clear task description", "task", "task description", "...")

# Marker that prefixes every memory block mem0 injects into the context. Shared
# by mem0_setup.py (which writes it) and memory.py (which strips stale copies on
# save) so the two can never drift apart. Without the strip, mem0 stacks a fresh
# block onto the context every turn and it bloats the prompt into oblivion.
MEM0_MEMORY_HEADER = "Here's what I remember about you from past chats:"

# Stable prefixes of every one-shot bracketed instruction the app injects into
# the context (delegation acks, agent updates/questions, confirmation flow).
# memory.py strips messages with these prefixes on save; anything injected via
# a template above MUST be covered here or it piles up in jess_memory.json
# across restarts. Enforced by tests/test_memory_strip.py.
EPHEMERAL_PROMPT_PREFIXES = (
    "[Voice delegation dispatched",
    "[Background task update",
    "[A background agent needs the user's input",
    "[A delegation needs the user's confirmation",
    "[The user confirmed.",
    "[The user cancelled the delegation",
    "[Correction --",
    "[Agent model control:",
)
