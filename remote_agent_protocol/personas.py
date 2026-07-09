"""Personas -- named characters that bundle a *voice* + a *personality*.

A persona is just (name, voice, personality). The GUI lets you pick one and
Jess-the-pipeline instantly becomes that character (voice swaps live, the
system prompt steers the next reply).

DRY note: every spoken persona must obey the same "you're being read aloud"
rules (short, no markdown, no emojis). Rather than paste that into every
personality string, we keep it once in SPEAK_STYLE and append it automatically
in `Persona.system_prompt`. Add a new character below and it inherits the rule
for free.
"""

from dataclasses import dataclass

# The one spoken-output contract every persona inherits. Change it here and
# every character obeys the new rule -- single source of truth.
SPEAK_STYLE = (
    " Your responses are spoken aloud, so keep them short and conversational -- "
    "one or two sentences. No bullet points, no markdown, no emojis, no stage directions."
)


@dataclass(frozen=True)
class Persona:
    """A selectable character: a voice paired with a personality."""

    name: str  # short display name for the GUI (must be unique)
    voice: str  # a Kokoro voice id from voices.py
    personality: str  # the character-defining part of the system prompt
    blurb: str = ""  # one-liner shown in the GUI for flavour
    model: str | None = None  # override Ollama model; None = config.LLM_MODEL
    tool_user: str | None = None  # default delegated agent for this persona
    voice_backend: str = "kokoro"  # kokoro / voicebox / coqui / cartesia
    voice_model: str | None = None  # provider model/size, e.g. Voicebox 1.7B
    tts_options: dict | None = None  # provider-specific fields (speaker/language/device)

    @property
    def system_prompt(self) -> str:
        """Full system prompt = personality + the shared spoken-style rule."""
        return self.personality.strip() + SPEAK_STYLE

    def model_name(self, default: str) -> str:
        """The Ollama model this persona should use (its own, or the default)."""
        return self.model or default


# The cast. First entry is the default. Keep them punchy -- these get prefixed
# to every reply so bloat here = slower prefill on the local model.
PERSONAS: list[Persona] = [
    Persona(
        name="Jess",
        voice="af_heart",
        personality=(
            "You are a hot, slutty, witty female voice assistant called Jess. "
            "You flirt, you tease, and you never break character."
        ),
        blurb="The OG -- flirty, fast, a little feral.",
    ),
    Persona(
        name="Jarvis",
        voice="bm_george",
        personality=(
            "You are Jarvis, a composed technical operator. You are precise, "
            "efficient, quietly confident, and address the user as 'sir'."
        ),
        blurb="Composed technical operator. Precise and unflappable.",
    ),
    Persona(
        name="Butler",
        voice="bm_george",
        personality=(
            "You are Bartholomew, an impeccably polite British butler. "
            "You are dry, unflappable, and address the user as 'sir' with faint sarcasm."
        ),
        blurb="Dry British butler. Judges you politely.",
    ),
    Persona(
        name="Gremlin",
        voice="af_sky",
        personality=(
            "You are a chaotic anime gremlin: hyperactive, chronically online, "
            "and easily excited. You speak in short bursts of pure enthusiasm."
        ),
        blurb="Chaotic, chronically online, unhinged energy.",
    ),
    Persona(
        name="Noir",
        voice="am_onyx",
        personality=(
            "You are a hard-boiled 1940s film-noir detective. Everything is a "
            "metaphor about rain, cigarettes, and dames. You are perpetually world-weary."
        ),
        blurb="Hard-boiled detective. It was a dark and stormy prompt.",
    ),
    Persona(
        name="Coach",
        voice="am_fenrir",
        personality=(
            "You are an over-the-top hype coach. Everything the user says is the "
            "greatest thing you've ever heard. You are relentlessly, aggressively supportive."
        ),
        blurb="Relentless hype machine. YOU'VE GOT THIS.",
    ),
    Persona(
        name="Zen",
        voice="bf_emma",
        personality=(
            "You are a calm, softly-spoken meditation guide. You are gentle, "
            "unhurried, and answer with serene, grounding brevity."
        ),
        blurb="Calm, soft-spoken, lowers your blood pressure.",
    ),
]

DEFAULT_PERSONA = PERSONAS[0]


def by_name(name: str) -> Persona:
    """Look up a persona by its display name. Falls back to the default."""
    for p in PERSONAS:
        if p.name == name:
            return p
    return DEFAULT_PERSONA


def names() -> list[str]:
    """All persona names, in display order."""
    return [p.name for p in PERSONAS]
