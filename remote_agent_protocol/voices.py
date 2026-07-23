"""Kokoro voice catalog: every voice the local TTS can speak with.

This used to live as a big comment block in config.py. It's data, not prose, so
it belongs in a real data structure the GUI dropdown (and anyone else) can use
directly. DRY: one list, consumed everywhere.

Each voice id follows Kokoro's `<lang><gender>_<name>` convention, e.g.
`af_heart` = American Female "heart", `bm_george` = British Male "george".
"""

# Grouped for a tidy, scannable dropdown. Order = how they'll appear in the GUI.
VOICE_GROUPS: dict[str, list[str]] = {
    "American Female": [
        "af_heart",
        "af_bella",
        "af_sarah",
        "af_nicole",
        "af_alloy",
        "af_aoede",
        "af_jessica",
        "af_kore",
        "af_nova",
        "af_river",
        "af_sky",
    ],
    "American Male": [
        "am_adam",
        "am_michael",
        "am_echo",
        "am_eric",
        "am_fenrir",
        "am_liam",
        "am_onyx",
        "am_puck",
        "am_santa",
    ],
    "British Female": ["bf_alice", "bf_emma", "bf_isabella", "bf_lily"],
    "British Male": ["bm_daniel", "bm_fable", "bm_george", "bm_lewis"],
    "French Female": ["ff_siwis"],
    "Italian": ["if_sara", "im_nicola"],
    "Japanese": ["jf_alpha", "jf_gongitsune", "jf_nezumi", "jf_tebukuro", "jm_kumo"],
    "Chinese": [
        "zf_xiaobei",
        "zf_xiaoni",
        "zf_xiaoxiao",
        "zf_xiaoyi",
        "zm_yunjian",
        "zm_yunxi",
        "zm_yunxia",
        "zm_yunyang",
    ],
    "Hindi": ["hf_alpha", "hf_beta", "hm_omega", "hm_psi"],
    "Polish": ["pf_dora", "pm_alex", "pm_santa"],
}

# Flat list of every voice id, handy for validation / iteration.
ALL_VOICES: list[str] = [v for group in VOICE_GROUPS.values() for v in group]


def is_valid(voice: str) -> bool:
    """True if `voice` is a real Kokoro voice id we know about."""
    return voice in ALL_VOICES


def group_of(voice: str) -> str | None:
    """Return the display group a voice belongs to, or None if unknown."""
    for group, voices in VOICE_GROUPS.items():
        if voice in voices:
            return group
    return None


def labelled() -> list[tuple[str, str]]:
    """Flat [(label, voice_id), ...] for a grouped dropdown.

    Labels read like "American Female - af_heart" so the GUI can show the
    grouping inline without needing a fancy tree widget. Cohesion over cleverness.
    """
    out: list[tuple[str, str]] = []
    for group, voices in VOICE_GROUPS.items():
        for v in voices:
            out.append((f"{group} - {v}", v))
    return out
