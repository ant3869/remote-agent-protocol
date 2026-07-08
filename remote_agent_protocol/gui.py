"""Jess -- polished desktop control panel (Tkinter).

Native, fast, no Electron clown car. The audio path remains local mic->speakers;
this GUI is only a controller/observer around VoiceSession. All visual styling
comes from the shared design system in gui_theme.
"""

import asyncio
import queue
import threading
import time
from pathlib import Path
from tkinter import BOTH, END, LEFT, RIGHT, Button, Frame, Label, StringVar, Tk, X, filedialog, ttk

from remote_agent_protocol import (
    app_state,
    dashboard,
    diagnostics,
    logging_setup,
    multimodal_prompt,
    ollama_models,
    persona_config,
    personas,
    tts_factory,
    voicebox,
    voices,
    wake_word,
)
from remote_agent_protocol import config as cfg
from remote_agent_protocol import gui_theme as theme
from remote_agent_protocol.gui_agents import AgentsPanel
from remote_agent_protocol.gui_config import ConfigPanel
from remote_agent_protocol.gui_memory import MemoryPanel
from remote_agent_protocol.gui_setup import SetupWizard
from remote_agent_protocol.session import VoiceSession

logging_setup.setup_logging(cfg.DEBUG_MODE)

_SESSION_TONES = {"ready": "ok", "failed": "danger", "building": "accent", "starting": "accent"}
_LATENCY_KEYS = ("stt", "llm", "tts", "total")


def agent_stream_line(evt: dict) -> str | None:
    """The conversation feed line for one agent_job event, or None to skip.

    Only ``progress`` events narrate what the agent is doing; ``started`` and
    ``finished`` already have their own transcript lines, and ``output`` is raw
    stdout kept to the Agents panel. Returns ``"<agent>: <action>"`` or None.
    """
    if evt.get("event") != "progress":
        return None
    action = (evt.get("action") or evt.get("state") or "").strip()
    if not action:
        return None
    return f"{evt.get('agent', 'agent')}: {action}"


class VoiceGUI:
    """The whole app: builds the window, owns the session + its thread."""

    def __init__(self) -> None:
        """Build the window, panels, and the (not-yet-running) voice session."""
        self._events: queue.Queue[dict] = queue.Queue()
        self._persona_config = persona_config.load_config()
        self._personas = persona_config.effective_personas(personas.PERSONAS, self._persona_config)
        self._app_state = app_state.load_state(cfg.APP_STATE_FILE)
        boot_name = app_state.resolve_persona_name(
            self._app_state.persona, self._persona_names(), cfg.DEFAULT_PERSONA_NAME
        )
        self._persona = self._persona_by_name(boot_name)
        self._session = VoiceSession(self._persona, on_event=self._events.put)
        self._session.set_manual_prompt_mode(True)
        self._voice_mode = multimodal_prompt.normalize_voice_mode(self._app_state.voice_mode)
        self._session.set_voice_mode(self._voice_mode)
        self._session.set_voicebox_warmup_personas(
            persona_config.voicebox_personas(personas.PERSONAS, self._persona_config)
        )
        # The last explicit tool-user pick outranks the persona's own default.
        if self._app_state.tool_user in cfg.AGENT_BACKENDS:
            self._session.set_default_agent_backend(self._app_state.tool_user)
        self._thread: threading.Thread | None = None
        self._syncing = False
        # Held delegations awaiting approval, oldest first; the bar shows [0].
        self._pending_confirms: list[dict] = []
        # Last agent-stream line written to the transcript, to dedupe tool spam.
        self._last_agent_stream = ""
        self._draft_attachments: list[multimodal_prompt.PromptAttachment] = []
        self._draft_voice_text = ""
        self._draft_context_signals: list[str] = []
        self._draft_send_reason = "manual_send"
        self._draft_touched_at = time.monotonic()
        self._voice_map = self._build_voice_map()
        self._voice_labels = {v: k for k, v in self._voice_map.items()}
        self._models = self._model_choices()
        self._latency = dashboard.LatencyState()
        self._server_status_text = "checking"

        self._build_window()
        self._memory = MemoryPanel(self.root, self._session, self._append_sys)
        self._agents = AgentsPanel(self.root, self._session, self._append_agent)
        self._setup_wizard = SetupWizard(self.root, self._session)
        self._config_panel = ConfigPanel(
            self.root,
            self._voice_map,
            self._models,
            self._session.agent_backends(),
            self._reload_persona_config,
        )

    # -- data -----------------------------------------------------------------

    def _model_choices(self) -> list[str]:
        extra = [cfg.LLM_MODEL] + [p.model for p in self._personas if p.model]
        live = ollama_models.available(cfg.OLLAMA_HOST)
        return sorted(set(live) | set(extra))

    def _persona_model(self, persona: personas.Persona) -> str:
        return persona.model_name(cfg.LLM_MODEL)

    def _persona_names(self) -> list[str]:
        return [p.name for p in self._personas]

    def _persona_by_name(self, name: str) -> personas.Persona:
        for persona in self._personas:
            if persona.name == name:
                return persona
        return self._personas[0]

    def _build_voice_map(self) -> dict[str, str]:
        return dict(voices.labelled() + voicebox.labelled_profiles())

    # -- UI construction ------------------------------------------------------

    def _build_window(self) -> None:
        self.root = Tk()
        self.root.title(cfg.APP_NAME)
        self.root.configure(bg=theme.BG)
        self.root.geometry("1320x800+40+40")
        self.root.minsize(1040, 680)

        theme.init_style(self.root)
        self._build_shell()
        theme.enable_dark_title_bar(self.root)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.bind("<Control-m>", lambda _e: self._toggle_mute())
        self.root.bind("<Control-k>", lambda _e: self._agents.open())
        self.root.bind("<Control-l>", lambda _e: self.message_entry.focus_set())

    def _build_shell(self) -> None:
        shell = Frame(self.root, bg=theme.BG)
        shell.pack(fill=BOTH, expand=True)

        self.topbar = Frame(shell, bg=theme.BG, height=78, padx=28, pady=16)
        self.topbar.pack(fill=X)
        self.topbar.pack_propagate(False)
        self._build_topbar()

        body = Frame(shell, bg=theme.BG, padx=18)
        body.pack(fill=BOTH, expand=True, pady=(0, 18))

        self.sidebar_panel = theme.panel(body, bg=theme.SURFACE, pad=16, radius=24, glow=True)
        self.sidebar_panel.configure(width=286)
        self.sidebar_panel.pack(side=LEFT, fill="y", padx=(0, 18))
        self.sidebar_panel.pack_propagate(False)
        self.sidebar = self.sidebar_panel.body

        self.main = Frame(body, bg=theme.BG)
        self.main.pack(side=LEFT, fill=BOTH, expand=True)

        self._build_sidebar()
        self._build_main_area()

    def _build_topbar(self) -> None:
        brand = Frame(self.topbar, bg=theme.BG)
        brand.pack(side=LEFT)
        Label(
            brand, text=cfg.APP_NAME, bg=theme.BG, fg=theme.FG, font=("Segoe UI Semibold", 18)
        ).pack(anchor="w")
        Label(
            brand,
            text="Premium local AI control center",
            bg=theme.BG,
            fg=theme.SUBTLE,
            font=theme.FONT_SMALL,
        ).pack(anchor="w")

        # Packed right-to-left so they read: Session · Ollama · TTS · Agents.
        self.agents_pill = theme.StatusPill(self.topbar, "Agents")
        self.agents_pill.pack(side=RIGHT, padx=(8, 0))
        self.tts_pill = theme.StatusPill(self.topbar, "TTS")
        self.tts_pill.pack(side=RIGHT, padx=(8, 0))
        self.health_pill = theme.StatusPill(self.topbar, "Ollama")
        self.health_pill.pack(side=RIGHT, padx=(8, 0))
        self.session_pill = theme.StatusPill(self.topbar, "Session")
        self.session_pill.pack(side=RIGHT, padx=(8, 0))
        self.session_pill.set("starting", "warn")
        self.health_pill.set("checking", "neutral")
        self.tts_pill.set("checking", "neutral")
        self.agents_pill.set("idle", "neutral")

    def _build_sidebar(self) -> None:
        self._build_mic_card()
        self._build_telemetry()

        nav_panel = theme.panel(self.sidebar, bg=theme.CARD, border=theme.BORDER, pad=12, radius=18)
        nav_panel.pack(fill=X, pady=(theme.GAP, 0))
        nav = nav_panel.body
        theme.section_label(nav, "Panels", bg=theme.CARD).pack(anchor="w", pady=(0, 8))
        for text, command in (
            ("Memory", lambda: self._memory.open()),
            ("Agents", lambda: self._agents.open()),
            ("Setup wizard", lambda: self._setup_wizard.open()),
            ("Persona settings", lambda: self._config_panel.open()),
        ):
            theme.button(nav, text, command, kind="ghost", anchor="w").pack(fill=X, pady=2)

        actions_panel = theme.panel(
            self.sidebar, bg=theme.CARD, border=theme.BORDER, pad=12, radius=18
        )
        actions_panel.pack(fill=X, pady=(theme.GAP, 0))
        actions = actions_panel.body
        theme.section_label(actions, "Actions", bg=theme.CARD).pack(anchor="w", pady=(0, 8))
        for text, command in (
            ("New chat", self._restart),
            ("Free VRAM", self._free_vram),
            ("Start Ollama", self._restart_ollama),
            ("Export diagnostics", self._export_diagnostics),
        ):
            theme.button(actions, text, command, kind="ghost", anchor="w").pack(fill=X, pady=2)
        theme.button(
            actions, "Reboot session", self._restart_session, kind="danger", anchor="w"
        ).pack(fill=X, pady=(6, 0))

        Label(
            self.sidebar,
            text="Ctrl+L  message   Ctrl+K  agents\nCtrl+M  microphone",
            bg=theme.SURFACE,
            fg=theme.DIM,
            justify="left",
            font=theme.FONT_MONO_SMALL,
        ).pack(side="bottom", anchor="w", pady=(theme.GAP, 0))

    def _build_mic_card(self) -> None:
        mic_panel = theme.panel(
            self.sidebar,
            bg=theme.GLOW_CARD,
            border=theme.BORDER_ACTIVE,
            pad=16,
            radius=22,
            glow=True,
        )
        mic_panel.pack(fill=X)
        card = mic_panel.body
        header = Frame(card, bg=theme.GLOW_CARD)
        header.pack(fill=X, pady=(0, theme.GAP_SM))
        Label(
            header,
            text=self._persona.name,
            bg=theme.GLOW_CARD,
            fg=theme.FG,
            font=theme.FONT_H2,
        ).pack(side=LEFT)
        Label(header, text="online", bg=theme.GLOW_CARD, fg=theme.OK, font=theme.FONT_SMALL).pack(
            side=RIGHT
        )
        self.dot = Label(
            card, text="●", bg=theme.GLOW_CARD, fg=theme.SPEAK_OFF, font=("Segoe UI Light", 40)
        )
        self.dot.pack(anchor="center")
        self.status = Label(
            card,
            text="Warming up models...",
            bg=theme.GLOW_CARD,
            fg=theme.SUBTLE,
            wraplength=190,
            justify="center",
            font=theme.FONT_STRONG,
        )
        self.status.pack(anchor="center", pady=(0, 12))
        controls = Frame(card, bg=theme.GLOW_CARD)
        controls.pack(fill=X)
        # Built directly (not via the factory): its colours flip with mute state.
        self.mute_btn = Button(
            controls,
            text="Mic — live",
            command=self._toggle_mute,
            bg=theme.ACCENT,
            fg=theme.ON_ACCENT,
            activebackground=theme.ACCENT_HOVER,
            activeforeground=theme.ON_ACCENT,
            relief="flat",
            bd=0,
            pady=8,
            cursor="hand2",
            font=theme.FONT_STRONG,
            highlightthickness=0,
        )
        self.mute_btn.pack(fill=X)
        self.mode_btn = Button(
            card,
            text="Mode: Free Talk",
            command=self._cycle_voice_mode,
            bg=theme.SURFACE,
            fg=theme.FG,
            activebackground=theme.BORDER,
            activeforeground=theme.FG,
            relief="flat",
            bd=0,
            pady=8,
            cursor="hand2",
            font=theme.FONT_STRONG,
            highlightthickness=0,
        )
        self.mode_btn.pack(fill=X, pady=(8, 0))
        self.ptt_btn = Button(
            card,
            text="Hold to talk",
            bg=theme.SURFACE,
            fg=theme.DIM,
            activebackground=theme.ACCENT,
            activeforeground=theme.ON_ACCENT,
            relief="flat",
            bd=0,
            pady=7,
            cursor="hand2",
            font=theme.FONT_STRONG,
            highlightthickness=0,
        )
        self.ptt_btn.pack(fill=X, pady=(8, 0))
        self.ptt_btn.bind("<ButtonPress-1>", self._ptt_down)
        self.ptt_btn.bind("<ButtonRelease-1>", self._ptt_up)
        self._apply_voice_mode_ui()

    def _build_telemetry(self) -> None:
        telemetry_panel = theme.panel(
            self.sidebar, bg=theme.CARD, border=theme.BORDER, pad=12, radius=18
        )
        telemetry_panel.pack(fill=X, pady=(theme.GAP, 0))
        self.telemetry_body = telemetry_panel.body
        theme.section_label(self.telemetry_body, "Telemetry", bg=theme.CARD).pack(
            anchor="w", pady=(0, 8)
        )
        self._lat_labels: dict[str, Label] = {}
        for key in _LATENCY_KEYS:
            self._lat_labels[key] = self._metric(key.upper() if key != "total" else "Total", "—")
        self.tts_label = self._metric("TTS", cfg.TTS_BACKEND)
        self.wake_label = self._metric("Wake", self._wake_status().message)

    def _build_main_area(self) -> None:
        self._build_header_card()
        self._build_status_dashboard()
        self._build_agent_strip()
        self._build_confirm_bar()
        self._build_transcript_card()
        self._build_composer_card()

    def _build_confirm_bar(self) -> None:
        """An amber-bordered banner shown only while a delegation awaits approval."""
        self.confirm_bar = Frame(
            self.main,
            bg=theme.CARD,
            padx=14,
            pady=10,
            highlightthickness=1,
            highlightbackground=theme.WARN,
        )
        Label(self.confirm_bar, text="⚠", bg=theme.CARD, fg=theme.WARN, font=("Segoe UI", 12)).pack(
            side=LEFT, padx=(0, 10)
        )
        self.confirm_text = Label(
            self.confirm_bar,
            text="",
            bg=theme.CARD,
            fg=theme.FG,
            font=theme.FONT_STRONG,
            wraplength=620,
            justify="left",
        )
        self.confirm_text.pack(side=LEFT, fill=X, expand=True)
        theme.button(self.confirm_bar, "Approve", self._approve_confirm, kind="primary").pack(
            side=LEFT, padx=(10, 6)
        )
        theme.button(self.confirm_bar, "Deny", self._deny_confirm, kind="danger").pack(side=LEFT)
        # Not packed until a confirmation actually arrives.

    def _wake_status(self) -> wake_word.WakeWordStatus:
        return wake_word.preflight(
            wake_word.settings_from_config(
                cfg,
                enabled=(
                    cfg.WAKE_WORD_ENABLED
                    or self._voice_mode == multimodal_prompt.VOICE_MODE_WAKE_WORD
                ),
            )
        )

    def _build_header_card(self) -> None:
        hero_panel = theme.panel(
            self.main, bg=theme.CARD, border=theme.BORDER_LIGHT, pad=18, radius=24, glow=True
        )
        hero_panel.pack(fill=X, pady=(0, theme.GAP))
        header = hero_panel.body
        top = Frame(header, bg=theme.CARD)
        top.pack(fill=X, pady=(0, theme.GAP))
        title_stack = Frame(top, bg=theme.CARD)
        title_stack.pack(side=LEFT, fill=X, expand=True)
        self.persona_title = Label(
            title_stack,
            text=self._persona.name,
            bg=theme.CARD,
            fg=theme.FG,
            font=("Segoe UI Semibold", 24),
        )
        self.persona_title.pack(anchor="w")
        self.blurb = Label(
            title_stack,
            text=self._persona.blurb,
            bg=theme.CARD,
            fg=theme.SUBTLE,
            font=theme.FONT_BODY,
        )
        self.blurb.pack(anchor="w", pady=(3, 0))
        mode_badge = Frame(
            top,
            bg=theme.SELECT_BG,
            padx=14,
            pady=8,
            highlightthickness=1,
            highlightbackground=theme.BORDER_ACTIVE,
        )
        mode_badge.pack(side=RIGHT)
        Label(mode_badge, text="●", bg=theme.SELECT_BG, fg=theme.CYAN, font=("Segoe UI", 9)).pack(
            side=LEFT, padx=(0, 8)
        )
        Label(
            mode_badge,
            text="Voice control active",
            bg=theme.SELECT_BG,
            fg=theme.FG,
            font=theme.FONT_STRONG,
        ).pack(side=LEFT)

        row = Frame(header, bg=theme.CARD)
        row.pack(fill=X)
        self._labeled_combo(
            row,
            "Persona",
            self._persona_names(),
            self._persona.name,
            self._on_persona_pick,
            12,
            "persona_box",
        )
        self._labeled_combo(
            row,
            "Tool user",
            self._session.agent_backends(),
            self._session.default_agent_backend(),
            self._on_tool_pick,
            12,
            "tool_box",
        )
        self._labeled_combo(
            row,
            "Model",
            self._models,
            self._persona_model(self._persona),
            self._on_model_pick,
            14,
            "model_box",
        )
        self._labeled_combo(
            row,
            "Voice",
            list(self._voice_map.keys()),
            self._voice_labels.get(self._persona.voice, ""),
            self._on_voice_pick,
            16,
            "voice_box",
        )

    def _build_status_dashboard(self) -> None:
        outer = theme.panel(self.main, bg=theme.CARD, border=theme.BORDER, pad=16, radius=22)
        outer.pack(fill=X, pady=(0, theme.GAP))
        panel = outer.body
        header = Frame(panel, bg=theme.CARD)
        header.pack(fill=X, pady=(0, theme.GAP_SM))
        Label(
            header,
            text="System dashboard",
            bg=theme.CARD,
            fg=theme.FG,
            font=theme.FONT_H2,
        ).pack(side=LEFT)
        Label(
            header,
            text="voice · model · memory · server",
            bg=theme.CARD,
            fg=theme.DIM,
            font=theme.FONT_SMALL,
        ).pack(side=RIGHT)

        grid = Frame(panel, bg=theme.CARD)
        grid.pack(fill=X)
        self.dashboard_labels: dict[str, Label] = {}
        items = (
            ("mode", "◉", "Current mode", "info"),
            ("model", "◆", "Model", "accent"),
            ("memory", "✦", "Memory", "ok" if cfg.MEMORY_ENABLED else "neutral"),
            ("server", "●", "Server", "neutral"),
        )
        for index, (key, icon, title, tone) in enumerate(items):
            tile_panel = theme.panel(
                grid, bg=theme.ELEVATED_BG, border=theme.BORDER, pad=12, radius=16
            )
            tile_panel.grid(row=0, column=index, sticky="ew", padx=(0 if index == 0 else 10, 0))
            grid.columnconfigure(index, weight=1)
            tile = tile_panel.body
            top = Frame(tile, bg=theme.ELEVATED_BG)
            top.pack(fill=X)
            Label(
                top,
                text=icon,
                bg=theme.ELEVATED_BG,
                fg=theme.TONES[tone],
                font=("Segoe UI Semibold", 12),
            ).pack(side=LEFT, padx=(0, 8))
            Label(
                top,
                text=title.upper(),
                bg=theme.ELEVATED_BG,
                fg=theme.DIM,
                font=theme.FONT_SECTION,
            ).pack(side=LEFT)
            row = Frame(tile, bg=theme.ELEVATED_BG)
            row.pack(fill=X, pady=(6, 0))
            Label(
                row,
                text="●",
                bg=theme.ELEVATED_BG,
                fg=theme.TONES[tone],
                font=("Segoe UI", 9),
            ).pack(side=LEFT, padx=(0, 8))
            value = Label(
                row,
                text="—",
                bg=theme.ELEVATED_BG,
                fg=theme.FG,
                font=theme.FONT_STRONG,
                anchor="w",
            )
            value.pack(side=LEFT, fill=X, expand=True)
            self.dashboard_labels[key] = value
        self._refresh_status_dashboard()

    def _refresh_status_dashboard(self) -> None:
        if not hasattr(self, "dashboard_labels"):
            return
        labels = {
            multimodal_prompt.VOICE_MODE_WAKE_WORD: "Wake Word",
            multimodal_prompt.VOICE_MODE_FREE_TALK: "Free Talk",
            multimodal_prompt.VOICE_MODE_PUSH_TO_TALK: "Push To Talk",
        }
        self.dashboard_labels["mode"].configure(text=labels[self._voice_mode])
        self.dashboard_labels["model"].configure(text=self.model_box.get())
        memory = "Short + semantic" if cfg.MEMORY_ENABLED and cfg.MEM0_ENABLED else "Local only"
        self.dashboard_labels["memory"].configure(text=memory)
        self.dashboard_labels["server"].configure(text=self._server_status_text)

    def _build_agent_strip(self) -> None:
        outer = theme.panel(self.main, bg=theme.CARD, border=theme.BORDER, pad=14, radius=20)
        outer.pack(fill=X, pady=(0, theme.GAP))
        strip = outer.body
        header = Frame(strip, bg=theme.CARD)
        header.pack(fill=X, pady=(0, theme.GAP_SM))
        Label(header, text="Agent fabric", bg=theme.CARD, fg=theme.FG, font=theme.FONT_H2).pack(
            side=LEFT
        )
        Label(
            header,
            text="local tool users ready for delegation",
            bg=theme.CARD,
            fg=theme.DIM,
            font=theme.FONT_SMALL,
        ).pack(side=RIGHT)
        chips = Frame(strip, bg=theme.CARD)
        chips.pack(fill=X)
        for backend in self._session.agent_backends():
            elevated = "yolo" in backend
            selected = backend == self._session.default_agent_backend()
            chip_panel = theme.panel(
                chips,
                bg=theme.SELECT_BG if selected else theme.ELEVATED_BG,
                border=theme.BORDER_ACTIVE if selected else theme.BORDER,
                pad=10,
                radius=16,
            )
            chip_panel.pack(side=LEFT, fill=X, expand=True, padx=(0, theme.GAP_SM))
            chip = chip_panel.body
            Label(
                chip,
                text="●",
                bg=theme.SELECT_BG if selected else theme.ELEVATED_BG,
                fg=theme.DANGER if elevated else theme.ACCENT,
                font=("Segoe UI", 7),
            ).pack(side=LEFT, padx=(0, 6))
            name = Frame(chip, bg=theme.SELECT_BG if selected else theme.ELEVATED_BG)
            name.pack(side=LEFT)
            Label(
                name,
                text=backend,
                bg=theme.SELECT_BG if selected else theme.ELEVATED_BG,
                fg=theme.FG,
                font=("Segoe UI Semibold", 9),
                justify="left",
            ).pack(anchor="w")
            Label(
                name,
                text=self._session.agent_machine(backend),
                bg=theme.SELECT_BG if selected else theme.ELEVATED_BG,
                fg=theme.SUBTLE if selected else theme.DIM,
                font=("Cascadia Mono", 8),
                justify="left",
            ).pack(anchor="w")

    def _build_transcript_card(self) -> None:
        outer = theme.panel(self.main, bg=theme.CARD, border=theme.BORDER_LIGHT, pad=0, radius=24)
        self.transcript_card = outer
        outer.pack(fill=BOTH, expand=True, pady=(0, theme.GAP))
        card = outer.body
        card.configure(padx=0, pady=0)
        header = Frame(card, bg=theme.CARD, padx=18, pady=14)
        header.pack(fill=X)
        Label(
            header,
            text="Transcription chat",
            bg=theme.CARD,
            fg=theme.FG,
            font=("Segoe UI Semibold", 14),
        ).pack(side=LEFT)
        Label(
            header,
            text="voice · text · agent events",
            bg=theme.CARD,
            fg=theme.DIM,
            font=theme.FONT_SMALL,
        ).pack(side=RIGHT)

        self.log = theme.scrolled_text(card, font=theme.FONT_BODY, padx=22, pady=18)
        self.log.frame.configure(highlightthickness=0)
        self.log.frame.pack(fill=BOTH, expand=True, padx=1, pady=(0, 1))
        self.log.tag_config(
            "name_user", foreground=theme.USER, font=theme.FONT_NAME, spacing1=16, lmargin1=8
        )
        self.log.tag_config(
            "name_assistant",
            foreground=theme.BOT,
            font=theme.FONT_NAME,
            spacing1=16,
            lmargin1=8,
        )
        self.log.tag_config("body", foreground=theme.FG, spacing3=8, lmargin1=8, lmargin2=8)
        self.log.tag_config("sys", foreground=theme.DIM, spacing1=10, font=theme.FONT_SMALL)
        self.log.tag_config("agent", foreground=theme.CYAN, spacing1=10, font=theme.FONT_SMALL)
        # Live agent progress feed: dimmer than milestone lines, tucked in so a
        # chatty agent doesn't shout over the conversation.
        self.log.tag_config(
            "agent_stream", foreground=theme.DIM, spacing1=1, lmargin1=18, font=theme.FONT_SMALL
        )
        self.log.configure(state="disabled")
        self._append_sys(
            "Ready when the mic comes online. Voice transcripts and replies land here."
        )

    def _build_composer_card(self) -> None:
        composer_panel = theme.panel(
            self.main, bg=theme.CARD, border=theme.BORDER_LIGHT, pad=16, radius=22, glow=True
        )
        composer_panel.pack(side="bottom", fill=X)
        card = composer_panel.body
        top = Frame(card, bg=theme.CARD)
        top.pack(fill=X)
        Label(top, text="Command composer", bg=theme.CARD, fg=theme.FG, font=theme.FONT_H2).pack(
            side=LEFT
        )
        Label(
            top,
            text="voice transcripts appear above",
            bg=theme.CARD,
            fg=theme.DIM,
            font=theme.FONT_SMALL,
        ).pack(side=RIGHT)

        self.voice_preview_var = StringVar(value="Voice: waiting")
        Label(
            card,
            textvariable=self.voice_preview_var,
            bg=theme.CARD,
            fg=theme.SUBTLE,
            font=theme.FONT_SMALL,
            anchor="w",
            justify="left",
            wraplength=900,
        ).pack(fill=X, pady=(6, 8))

        row = Frame(card, bg=theme.CARD)
        row.pack(fill=X)
        self.type_var = StringVar()
        self.message_entry = theme.entry(
            row,
            textvariable=self.type_var,
            placeholder="Type a message — Enter sends",
            bg=theme.INSET,
            font=theme.FONT_BODY,
        )
        self.message_entry.pack(side=LEFT, fill=X, expand=True, ipady=10, padx=(0, 10))
        self.message_entry.bind("<Return>", lambda _e: self._send_typed())
        self.message_entry.bind("<KeyRelease>", lambda _e: self._mark_context_changed())
        theme.button(row, "Send", self._send_typed, kind="primary").pack(side=LEFT, padx=(0, 6))
        theme.button(row, "Delegate", self._delegate_typed).pack(side=LEFT, padx=(0, 6))
        self.context_toggle_btn = theme.button(
            row, "Context", self._toggle_context_details, kind="ghost"
        )
        self.context_toggle_btn.pack(side=LEFT)

        self.context_details = Frame(card, bg=theme.CARD)

        self.notes_text = theme.scrolled_text(
            self.context_details, font=theme.FONT_SMALL, padx=10, pady=7
        )
        self.notes_text.configure(height=3)
        self.notes_text.frame.pack(fill=X, pady=(0, 8))
        self.notes_text.bind("<KeyRelease>", lambda _e: self._mark_context_changed())

        attach_row = Frame(self.context_details, bg=theme.CARD)
        attach_row.pack(fill=X, pady=(0, 6))
        self.attach_ref_var = StringVar()
        self.attach_note_var = StringVar()
        self.attach_ref_entry = theme.entry(
            attach_row,
            textvariable=self.attach_ref_var,
            placeholder="Paste a link or file path",
            bg=theme.SURFACE,
            font=theme.FONT_SMALL,
        )
        self.attach_ref_entry.pack(side=LEFT, fill=X, expand=True, ipady=5, padx=(0, 6))
        self.attach_note_entry = theme.entry(
            attach_row,
            textvariable=self.attach_note_var,
            placeholder="Attachment note",
            bg=theme.SURFACE,
            font=theme.FONT_SMALL,
        )
        self.attach_note_entry.pack(side=LEFT, fill=X, expand=True, ipady=5, padx=(0, 6))
        theme.button(attach_row, "Add", self._add_reference, kind="ghost").pack(
            side=LEFT, padx=(0, 6)
        )
        theme.button(attach_row, "File", self._pick_attachment, kind="ghost").pack(side=LEFT)

        self.attachment_list = theme.listbox(self.context_details, height=3, font=theme.FONT_SMALL)
        self.attachment_list.pack(fill=X, pady=(0, 8))
        self._render_attachments()
        theme.button(
            self.context_details,
            "Remove selected context item",
            self._remove_attachment,
            kind="ghost",
        ).pack(anchor="w")
        Label(
            self.context_details,
            text="Optional notes, links, images, and files are bundled only when you send.",
            bg=theme.CARD,
            fg=theme.DIM,
            font=theme.FONT_SMALL,
        ).pack(anchor="w", pady=(6, 0))
        self._context_details_open = False

    # -- small UI helpers -----------------------------------------------------

    def _metric(self, label: str, value: str) -> Label:
        parent = getattr(self, "telemetry_body", self.sidebar)
        row = Frame(parent, bg=parent.cget("background"), pady=2)
        row.pack(fill=X)
        Label(
            row,
            text=label.upper(),
            bg=parent.cget("background"),
            fg=theme.DIM,
            font=theme.FONT_SECTION,
        ).pack(side=LEFT)
        widget = Label(
            row,
            text=value,
            bg=parent.cget("background"),
            fg=theme.SUBTLE,
            justify="right",
            anchor="e",
            wraplength=140,
            font=theme.FONT_MONO_SMALL,
        )
        widget.pack(side=RIGHT, fill=X, expand=True)
        return widget

    def _labeled_combo(self, parent, label, values, current, callback, width, attr) -> ttk.Combobox:
        box = Frame(parent, bg=theme.CARD)
        box.pack(side=LEFT, fill=X, expand=True, padx=(0, theme.GAP))
        Label(box, text=label.upper(), bg=theme.CARD, fg=theme.DIM, font=theme.FONT_SECTION).pack(
            anchor="w"
        )
        combo = ttk.Combobox(box, values=values, state="readonly", width=width)
        combo.set(current)
        combo.pack(anchor="w", fill=X, pady=(4, 0))
        combo.bind("<<ComboboxSelected>>", callback)
        setattr(self, attr, combo)
        return combo

    def _update_latency(self) -> None:
        for key in _LATENCY_KEYS:
            value = self._latency.values.get(key)
            self._lat_labels[key].configure(text="—" if value is None else f"{value:.2f}s")

    def _toggle_context_details(self) -> None:
        self._context_details_open = not self._context_details_open
        if self._context_details_open:
            self.context_details.pack(fill=X, pady=(theme.GAP, 0))
            self.context_toggle_btn.configure(text="Hide context")
        else:
            self.context_details.pack_forget()
            self.context_toggle_btn.configure(text="Context")

    # -- widget callbacks -----------------------------------------------------

    def _reload_persona_config(self) -> None:
        self._persona_config = persona_config.load_config()
        self._personas = persona_config.effective_personas(personas.PERSONAS, self._persona_config)
        self._voice_map = self._build_voice_map()
        self._voice_labels = {v: k for k, v in self._voice_map.items()}
        self.persona_box.configure(values=self._persona_names())
        self.voice_box.configure(values=list(self._voice_map.keys()))
        self._persona = self._persona_by_name(self.persona_box.get())
        self._session.set_voicebox_warmup_personas(
            persona_config.voicebox_personas(personas.PERSONAS, self._persona_config)
        )
        self._append_sys("Config reloaded; Voicebox warmup targets refreshed.")

    def _on_persona_pick(self, _evt=None) -> None:
        if self._syncing:
            return
        persona = self._persona_by_name(self.persona_box.get())
        self._persona = persona
        self._session.set_persona(persona)  # applies the persona's tool_user too
        if persona.tool_user:
            self.tool_box.set(persona.tool_user)
        self._syncing = True
        self.voice_box.set(self._voice_labels.get(persona.voice, ""))
        self.model_box.set(self._persona_model(persona))
        self._syncing = False
        self.persona_title.configure(text=persona.name)
        self.blurb.configure(text=persona.blurb)
        self._refresh_status_dashboard()
        self._append_sys(f"Now speaking as {persona.name}.")
        self._save_app_state()

    def _on_voice_pick(self, _evt=None) -> None:
        if self._syncing:
            return
        voice = self._voice_map.get(self.voice_box.get())
        if voice:
            self._session.set_voice(voice)
            self._append_sys(f"Voice → {voice}")

    def _on_tool_pick(self, _evt=None) -> None:
        backend = self.tool_box.get()
        if backend:
            self._session.set_default_agent_backend(backend)
            self._append_agent(f"Default tool user → {backend}")
            self._save_app_state()

    def _save_app_state(self) -> None:
        app_state.save_state(
            cfg.APP_STATE_FILE,
            app_state.AppState(
                persona=self._persona.name,
                tool_user=self._session.default_agent_backend(),
                voice_mode=self._voice_mode,
            ),
        )

    def _on_model_pick(self, _evt=None) -> None:
        if self._syncing:
            return
        model = self.model_box.get()
        if model:
            self._session.set_model(model)
            self._refresh_status_dashboard()
            self._append_sys(f"Model → {model} (first reply may reload)")

    def _restart(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", END)
        self.log.configure(state="disabled")
        self._session.restart_conversation()
        self._append_sys("Conversation restarted.")
        self._set_status("Restarting...", theme.WARN)

    def _toggle_mute(self) -> None:
        self.muted = not getattr(self, "muted", False)
        self._session.set_muted(self.muted)
        if self.muted:
            self.mute_btn.configure(
                text="Mic — muted",
                bg=theme.DANGER,
                fg=theme.ON_DANGER,
                activebackground=theme.DANGER,
                activeforeground=theme.ON_DANGER,
            )
        else:
            self.mute_btn.configure(
                text="Mic — live",
                bg=theme.ACCENT,
                fg=theme.ON_ACCENT,
                activebackground=theme.ACCENT_HOVER,
                activeforeground=theme.ON_ACCENT,
            )
        self._set_status(
            "Mic muted — she can't hear you" if self.muted else "Listening...",
            theme.DIM if self.muted else theme.FG,
        )

    def _cycle_voice_mode(self) -> None:
        modes = multimodal_prompt.VOICE_MODES
        self._voice_mode = modes[(modes.index(self._voice_mode) + 1) % len(modes)]
        self._session.set_voice_mode(self._voice_mode)
        self._apply_voice_mode_ui()
        self._save_app_state()

    def _apply_voice_mode_ui(self) -> None:
        labels = {
            multimodal_prompt.VOICE_MODE_WAKE_WORD: "Mode: Wake Word",
            multimodal_prompt.VOICE_MODE_FREE_TALK: "Mode: Free Talk",
            multimodal_prompt.VOICE_MODE_PUSH_TO_TALK: "Mode: Push To Talk",
        }
        self.mode_btn.configure(text=labels[self._voice_mode])
        self._refresh_status_dashboard()
        ptt = self._voice_mode == multimodal_prompt.VOICE_MODE_PUSH_TO_TALK
        self.ptt_btn.configure(
            fg=theme.FG if ptt else theme.DIM,
            text="Hold to talk" if ptt else "Push to talk inactive",
        )
        if ptt:
            self._set_status("Push To Talk — hold the button to speak", theme.SUBTLE)
        elif self._voice_mode == multimodal_prompt.VOICE_MODE_WAKE_WORD:
            self._set_status("Wake Word — listening passively", theme.SUBTLE)
        else:
            self._set_status("Listening...", theme.FG)

    def _ptt_down(self, _evt=None) -> None:
        if self._voice_mode != multimodal_prompt.VOICE_MODE_PUSH_TO_TALK:
            return
        self._session.set_push_to_talk(True)
        self.ptt_btn.configure(bg=theme.ACCENT, fg=theme.ON_ACCENT)
        self._set_status("Recording push-to-talk...", theme.FG)

    def _ptt_up(self, _evt=None) -> None:
        if self._voice_mode != multimodal_prompt.VOICE_MODE_PUSH_TO_TALK:
            return
        self._session.set_push_to_talk(False)
        self.ptt_btn.configure(bg=theme.SURFACE, fg=theme.FG)
        self._set_status("Push To Talk — hold the button to speak", theme.SUBTLE)

    def _send_typed(self) -> None:
        bundle = self._current_bundle()
        if not self._bundle_has_content(bundle):
            return
        self._session.send_multimodal_prompt(bundle)
        self._clear_multimodal_draft()

    def _delegate_typed(self) -> None:
        bundle = self._current_bundle()
        if not self._bundle_has_content(bundle):
            return
        text = bundle.agent_prompt()
        backend = self._session.default_agent_backend()
        self._append_message(
            "user", f"You → {backend}", bundle.final_user_instruction or "Shared prompt"
        )
        self._session.start_agent_task(backend, text)
        self._clear_multimodal_draft()

    def _current_bundle(self) -> multimodal_prompt.MultimodalPromptBundle:
        bundle = multimodal_prompt.MultimodalPromptBundle(user_id=cfg.MEM0_USER_ID)
        bundle.voice_mode = self._voice_mode
        bundle.send_reason = self._draft_send_reason
        bundle.context_signals = self._current_context_signals(self._draft_voice_text)
        if self._draft_voice_text:
            bundle.add_voice_transcript(self._draft_voice_text)
        bundle.set_text(self.notes_text.get("1.0", END))
        if not theme.placeholder_active(self.message_entry):
            bundle.set_final_instruction(self.type_var.get())
        for attachment in self._draft_attachments:
            bundle.add_attachment(attachment)
        return bundle

    @staticmethod
    def _bundle_has_content(bundle: multimodal_prompt.MultimodalPromptBundle) -> bool:
        return bool(
            bundle.voice.transcript
            or bundle.text.raw_text
            or bundle.final_user_instruction
            or bundle.attachments
        )

    def _clear_multimodal_draft(self) -> None:
        self._draft_voice_text = ""
        self._draft_context_signals = []
        self._draft_send_reason = "manual_send"
        self.voice_preview_var.set("Voice: waiting")
        self.notes_text.delete("1.0", END)
        self.type_var.set("")
        self._draft_attachments.clear()
        self._render_attachments()
        self._sync_context_active()

    def _composer_text(self) -> str:
        chunks = [self.notes_text.get("1.0", END)]
        if not theme.placeholder_active(self.message_entry):
            chunks.append(self.type_var.get())
        if not theme.placeholder_active(self.attach_ref_entry):
            chunks.append(self.attach_ref_var.get())
        return "\n".join(chunks)

    def _current_context_signals(self, voice_text: str = "") -> list[str]:
        text = "\n".join(part for part in (voice_text, self._composer_text()) if part)
        return multimodal_prompt.context_signals(
            text,
            has_attachments=bool(self._draft_attachments),
            draft_active=self._composer_has_context(),
        )

    def _composer_has_context(self) -> bool:
        if self._draft_attachments or self._draft_voice_text:
            return True
        return bool(self._composer_text().strip())

    def _mark_context_changed(self, signal: str = "draft_active") -> None:
        self._draft_touched_at = time.monotonic()
        if signal not in self._draft_context_signals:
            self._draft_context_signals.append(signal)
        self._sync_context_active()

    def _sync_context_active(self) -> None:
        signals = self._current_context_signals(self._draft_voice_text)
        self._draft_context_signals = signals
        self._session.set_context_active(bool(signals))

    def _expire_context_draft(self) -> None:
        if (
            self._composer_has_context()
            and time.monotonic() - self._draft_touched_at > cfg.CONTEXT_DRAFT_TIMEOUT_SECS
        ):
            self._clear_multimodal_draft()
            self._set_status("Context draft expired; Free Talk is normal again.", theme.SUBTLE)
        self.root.after(10000, self._expire_context_draft)

    def _add_reference(self) -> None:
        if theme.placeholder_active(self.attach_ref_entry):
            return
        reference = self.attach_ref_var.get().strip()
        if not reference:
            return
        note = (
            "" if theme.placeholder_active(self.attach_note_entry) else self.attach_note_var.get()
        )
        self._draft_attachments.append(
            multimodal_prompt.attachment_from_reference(reference, note=note)
        )
        self._mark_context_changed("attachment")
        self.attach_ref_var.set("")
        self.attach_note_var.set("")
        self._render_attachments()

    def _pick_attachment(self) -> None:
        path = filedialog.askopenfilename()
        if not path:
            return
        note = (
            "" if theme.placeholder_active(self.attach_note_entry) else self.attach_note_var.get()
        )
        self._draft_attachments.append(multimodal_prompt.attachment_from_reference(path, note=note))
        self._mark_context_changed("attachment")
        self.attach_note_var.set("")
        self._render_attachments()

    def _remove_attachment(self) -> None:
        selection = self.attachment_list.curselection()
        if not selection:
            return
        index = selection[0]
        if 0 <= index < len(self._draft_attachments):
            del self._draft_attachments[index]
            self._render_attachments()
            self._sync_context_active()

    def _render_attachments(self) -> None:
        self.attachment_list.delete(0, END)
        if not self._draft_attachments:
            theme.set_list_placeholder(self.attachment_list, "No attachments yet")
            return
        for attachment in self._draft_attachments:
            note = f" — {attachment.user_note}" if attachment.user_note else ""
            self.attachment_list.insert(END, f"{attachment.type}: {attachment.reference}{note}")

    # -- confirmation gate ----------------------------------------------------

    def _push_confirm(self, evt: dict) -> None:
        self._pending_confirms.append(evt)
        self._render_confirm_bar()

    def _drop_confirm(self, token: str | None) -> None:
        self._pending_confirms = [
            evt for evt in self._pending_confirms if evt.get("token") != token
        ]
        self._render_confirm_bar()

    def _render_confirm_bar(self) -> None:
        """Show the oldest pending confirmation, or hide the bar if none left."""
        if not self._pending_confirms:
            self.confirm_bar.pack_forget()
            return
        evt = self._pending_confirms[0]
        agent = evt.get("agent", "?")
        machine = evt.get("machine", "local")
        task = evt.get("task", "")
        reason = evt.get("reason", "")
        transcript = evt.get("transcript", "")
        queued = (
            f"  (+{len(self._pending_confirms) - 1} more)"
            if len(self._pending_confirms) > 1
            else ""
        )
        lines = [f"Run on {machine}/{agent}  →  {task}{queued}"]
        if reason:
            lines.append(f"Why: {reason}")
        if transcript:
            lines.append(f'Heard: "{transcript}"')
        self.confirm_text.configure(text="\n".join(lines))
        self.confirm_bar.pack(fill=X, pady=(0, theme.GAP), before=self.transcript_card)

    def _hide_confirm(self) -> None:
        self._pending_confirms.clear()
        self.confirm_bar.pack_forget()

    def _current_confirm_token(self) -> str | None:
        return self._pending_confirms[0].get("token") if self._pending_confirms else None

    def _approve_confirm(self) -> None:
        token = self._current_confirm_token()
        if token:
            self._session.approve_agent_task(token)
        self._drop_confirm(token)

    def _deny_confirm(self) -> None:
        token = self._current_confirm_token()
        if token:
            self._session.deny_agent_task(token)
        self._drop_confirm(token)

    # -- event pump -----------------------------------------------------------

    def _pump(self) -> None:
        try:
            while True:
                self._handle_event(self._events.get_nowait())
        except queue.Empty:
            pass
        self.root.after(80, self._pump)

    def _handle_event(self, evt: dict) -> None:
        kind = evt.get("type")
        if kind == "transcript":
            role = evt.get("role", "assistant")
            who = "You" if role == "user" else self._persona.name
            self._append_message(role, who, evt.get("text", ""))
            if role == "user" and not getattr(self, "muted", False):
                self._set_status("Thinking...", theme.SUBTLE)
        elif kind == "draft_voice":
            text = evt.get("text", "").strip()
            if text:
                self._draft_voice_text = text
                self._draft_context_signals = multimodal_prompt.context_signals(
                    text,
                    has_attachments=bool(self._draft_attachments),
                    draft_active=self._composer_has_context(),
                )
                self.voice_preview_var.set(f"Voice: {text}")
                self._mark_context_changed("voice")
            if evt.get("intent") == "send":
                self._draft_send_reason = "voice_send_intent"
                self._send_typed()
            elif evt.get("intent") == "hold":
                self._set_status("Holding draft...", theme.SUBTLE)
            elif evt.get("intent") == "cancel":
                self._clear_multimodal_draft()
                self._set_status("Context draft cleared.", theme.SUBTLE)
        elif kind == "speaking":
            speaking = bool(evt.get("value"))
            self.dot.configure(fg=theme.SPEAK_ON if speaking else theme.SPEAK_OFF)
            if not getattr(self, "muted", False):
                self._set_status("Speaking..." if speaking else "Listening...", theme.FG)
        elif kind == "metric":
            self._latency.update(evt.get("bucket", ""), evt.get("kind", ""), evt.get("value", 0.0))
            self._update_latency()
        elif kind == "turn":
            if evt.get("event") == "user_stopped":
                self._latency.mark_user_turn_complete()
            elif evt.get("event") == "bot_started":
                self._latency.mark_bot_started()
                self._update_latency()
        elif kind == "health":
            ok = bool(evt.get("ok"))
            self._server_status_text = evt.get("label", "Ollama ?")
            self.health_pill.set(self._server_status_text, "ok" if ok else "danger")
            self._refresh_status_dashboard()
        elif kind == "tts_health":
            ok = bool(evt.get("ok"))
            self.tts_pill.set(evt.get("label", "TTS ?"), "ok" if ok else "danger")
        elif kind == "wake":
            self._update_wake_chip(evt)
        elif kind == "lifecycle_ws":
            if evt.get("state") == "degraded":
                self._append_sys(
                    f"-- lifecycle WebSocket unavailable: {evt.get('error', 'bind failed')} --"
                )
        elif kind == "sys":
            self._append_sys(evt.get("text", ""))
        elif kind == "memory":
            self._memory.handle_event(evt)
        elif kind == "agent_job":
            self._agents.handle_event(evt)
            self._refresh_agents_badge()
            self._stream_agent_event(evt)
        elif kind == "agent_confirm":
            self._push_confirm(evt)
        elif kind == "agent_confirm_resolved":
            self._drop_confirm(evt.get("token"))
        elif kind == "session":
            state = evt.get("state", "unknown")
            self.session_pill.set(state, _SESSION_TONES.get(state, "neutral"))
            if state == "ready":
                self._set_status("Listening...", theme.FG)
            elif state == "failed":
                self._set_status("Pipeline failed — use Reboot session", theme.DANGER)
            elif state == "stopped":
                self._set_status("Session stopped — use Reboot session", theme.WARN)
            self._refresh_status_dashboard()

    def _append(self, tag: str, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert(END, text + "\n", tag)
        self.log.see(END)
        self.log.configure(state="disabled")

    def _append_message(self, role: str, who: str, text: str) -> None:
        """Chat-style block: coloured speaker name line, then the message body."""
        self.log.configure(state="normal")
        self.log.insert(END, f"{who}\n", f"name_{role}")
        self.log.insert(END, text + "\n", "body")
        self.log.see(END)
        self.log.configure(state="disabled")

    def _append_sys(self, text: str) -> None:
        self._append("sys", text)

    def _append_agent(self, text: str) -> None:
        self._append("agent", text)

    def _stream_agent_event(self, evt: dict) -> None:
        """Mirror an agent's live progress into the conversation as a feed line.

        The started/finished lines are already written by the Agents panel; this
        fills the gap in between so a delegated job reads as a running narration
        ("code-puppy: checking for an open Command Prompt window") instead of a
        silent wait punctuated by a single result. Raw stdout stays in the
        Agents panel's detail pane -- only the distilled per-step action is
        surfaced here, and consecutive duplicates (tool spam) are dropped.
        """
        line = agent_stream_line(evt)
        if not line or line == self._last_agent_stream:
            return
        self._last_agent_stream = line
        self._append("agent_stream", line)

    def _set_status(self, text: str, colour: str = theme.SUBTLE) -> None:
        self.status.configure(text=text, fg=colour)

    def _refresh_agents_badge(self) -> None:
        count = self._agents.running_count()
        self.agents_pill.set(
            self._agents.active_summary() or "idle", "accent" if count else "neutral"
        )

    def _update_wake_chip(self, evt: dict) -> None:
        state = evt.get("state", "")
        model = evt.get("model", "")
        if state == "armed":
            self.wake_label.configure(text=f"armed — say '{model}'", fg=theme.CYAN)
        elif state == "awake":
            window = evt.get("window_secs")
            self.wake_label.configure(
                text=f"awake ({window:.0f}s window)" if window else "awake", fg=theme.OK
            )
        elif state == "bypass":
            self.wake_label.configure(text="engine failed — always listening", fg=theme.DANGER)
        elif state == "inactive":
            self.wake_label.configure(text="inactive", fg=theme.DIM)

    # -- background pollers ---------------------------------------------------

    def _poll_health(self) -> None:
        def worker() -> None:
            health = dashboard.ollama_health(cfg.OLLAMA_HOST)
            self._events.put({"type": "health", "ok": health.ok, "label": health.label})

        threading.Thread(target=worker, daemon=True).start()
        self.root.after(3000, self._poll_health)

    def _restart_ollama(self) -> None:
        self._append_sys("Starting Ollama tray app...")
        self._set_status("Starting Ollama...", theme.WARN)

        def worker() -> None:
            try:
                dashboard.start_ollama_app()
                self._events.put({"type": "health", "ok": False, "label": "Ollama starting..."})
            except Exception as e:
                self._events.put({"type": "sys", "text": f"Couldn't start Ollama: {e}"})

        threading.Thread(target=worker, daemon=True).start()

    def _free_vram(self) -> None:
        self._append_sys("Unloading Ollama models to free VRAM...")
        self._set_status("Freeing VRAM...", theme.WARN)

        def worker() -> None:
            try:
                count = dashboard.stop_loaded_models(cfg.OLLAMA_HOST)
                msg = f"Unloaded {count} Ollama model(s)."
            except Exception as e:
                msg = f"Couldn't unload models: {e}"
            self._events.put({"type": "sys", "text": msg})

        threading.Thread(target=worker, daemon=True).start()

    def _poll_tts_health(self) -> None:
        def worker() -> None:
            health = dashboard.tts_health(
                cfg.TTS_BACKEND,
                voicebox_url=voicebox.base_url(),
                has_cartesia_key=bool(tts_factory.load_env_value("CARTESIA_API_KEY")),
            )
            self._events.put({"type": "tts_health", "ok": health.ok, "label": health.label})

        threading.Thread(target=worker, daemon=True).start()
        self.root.after(5000, self._poll_tts_health)

    def _export_diagnostics(self) -> None:
        self._append_sys("Building diagnostics bundle...")

        def worker() -> None:
            try:
                health = dashboard.ollama_health(cfg.OLLAMA_HOST)
                tts = dashboard.tts_health(
                    cfg.TTS_BACKEND,
                    voicebox_url=voicebox.base_url(),
                    has_cartesia_key=bool(tts_factory.load_env_value("CARTESIA_API_KEY")),
                )
                report = diagnostics.build_report(
                    session_snapshot=self._session.export_snapshot(),
                    tts_backend=cfg.TTS_BACKEND,
                    ollama={"ok": health.ok, "label": health.label},
                    tts={"ok": tts.ok, "label": tts.label},
                    latency_line=dashboard.format_latency_line(self._latency),
                    jobs=self._agents.recent_jobs(),
                    devices=diagnostics.audio_devices(),
                )
                path = diagnostics.write_bundle(cfg.DATA_DIR, report)
                msg = f"Diagnostics written to {path}"
            except Exception as e:
                msg = f"Diagnostics export failed: {e}"
            self._events.put({"type": "sys", "text": msg})

        threading.Thread(target=worker, daemon=True).start()

    def _restart_session(self) -> None:
        """Tear down a dead/stopped session and boot a fresh one in-place."""
        self._append_sys("Rebooting voice session...")
        self._set_status("Rebooting session...", theme.WARN)
        self._hide_confirm()
        try:
            self._session.shutdown()
        except Exception:
            pass
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._session = VoiceSession(self._persona, on_event=self._events.put)
        self._session.set_manual_prompt_mode(True)
        self._session.set_voice_mode(self._voice_mode)
        self._sync_context_active()
        self._session.set_voicebox_warmup_personas(
            persona_config.voicebox_personas(personas.PERSONAS, self._persona_config)
        )
        # Keep the visible mic state truthful: a muted UI must stay muted
        # through the rebuild (the new session applies it when the gate exists).
        self._session.set_muted(getattr(self, "muted", False))
        # Re-point the panels at the new session (they cache the reference).
        self._memory.rebind_session(self._session)
        self._agents.rebind_session(self._session)
        self._thread = threading.Thread(target=self._boot_thread, daemon=True)
        self._thread.start()

    # -- lifecycle ------------------------------------------------------------

    def _boot_thread(self) -> None:
        async def _boot() -> None:
            self._events.put({"type": "session", "state": "building"})
            self._session.build()
            self._events.put({"type": "speaking", "value": False})
            self._events.put({"type": "session", "state": "ready"})
            await self._session.run()
            self._events.put({"type": "session", "state": "stopped"})

        try:
            asyncio.run(_boot())
        except Exception as e:
            self._events.put({"type": "session", "state": "failed"})
            self._events.put(
                {"type": "transcript", "role": "assistant", "text": f"[pipeline crashed: {e}]"}
            )

    def _on_close(self) -> None:
        self._set_status("Shutting down...", theme.WARN)
        self._session.shutdown()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self.root.destroy()

    def run(self) -> None:
        """Boot the voice session thread and enter the Tk main loop."""
        self.muted = False
        self._thread = threading.Thread(target=self._boot_thread, daemon=True)
        self._thread.start()
        self.root.after(80, self._pump)
        self.root.after(500, self._poll_health)
        self.root.after(1200, self._poll_tts_health)
        self.root.after(10000, self._expire_context_draft)
        self.root.mainloop()


if __name__ == "__main__":
    VoiceGUI().run()
