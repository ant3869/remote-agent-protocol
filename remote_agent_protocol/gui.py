"""Jess -- polished desktop control panel (Tkinter).

Native, fast, no Electron clown car. The audio path remains local mic->speakers;
this GUI is only a controller/observer around VoiceSession. All visual styling
comes from the shared design system in gui_theme.
"""

import asyncio
import queue
import threading
from pathlib import Path
from tkinter import BOTH, END, LEFT, RIGHT, Button, Frame, Label, StringVar, Tk, X, ttk

from remote_agent_protocol import (
    app_state,
    dashboard,
    diagnostics,
    logging_setup,
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
from remote_agent_protocol.session import VoiceSession

logging_setup.setup_logging(cfg.DEBUG_MODE)

_SESSION_TONES = {"ready": "ok", "failed": "danger", "building": "warn", "starting": "warn"}
_LATENCY_KEYS = ("stt", "llm", "tts", "total")


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
        self._voice_map = self._build_voice_map()
        self._voice_labels = {v: k for k, v in self._voice_map.items()}
        self._models = self._model_choices()
        self._latency = dashboard.LatencyState()

        self._build_window()
        self._memory = MemoryPanel(self.root, self._session, self._append_sys)
        self._agents = AgentsPanel(self.root, self._session, self._append_agent)
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
        self.root.geometry("1280x760+40+40")
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

        self.topbar = Frame(shell, bg=theme.SURFACE, height=64, padx=20, pady=12)
        self.topbar.pack(fill=X)
        self.topbar.pack_propagate(False)
        self._build_topbar()

        body = Frame(shell, bg=theme.BG)
        body.pack(fill=BOTH, expand=True)

        self.sidebar = Frame(body, bg=theme.SURFACE, width=240, padx=14, pady=14)
        self.sidebar.pack(side=LEFT, fill="y")
        self.sidebar.pack_propagate(False)

        self.main = Frame(body, bg=theme.BG, padx=20, pady=16)
        self.main.pack(side=LEFT, fill=BOTH, expand=True)

        self._build_sidebar()
        self._build_main_area()

    def _build_topbar(self) -> None:
        brand = Frame(self.topbar, bg=theme.SURFACE)
        brand.pack(side=LEFT)
        Label(brand, text=cfg.APP_NAME, bg=theme.SURFACE, fg=theme.FG, font=theme.FONT_TITLE).pack(
            anchor="w"
        )
        Label(
            brand,
            text="Local-first voice agent switchboard",
            bg=theme.SURFACE,
            fg=theme.DIM,
            font=theme.FONT_SUBTITLE,
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

        theme.section_label(self.sidebar, "Panels").pack(anchor="w", pady=(theme.GAP, 6))
        for text, command in (
            ("Memory", lambda: self._memory.open()),
            ("Agents", lambda: self._agents.open()),
            ("Persona settings", lambda: self._config_panel.open()),
        ):
            theme.button(self.sidebar, text, command, kind="ghost", anchor="w").pack(fill=X, pady=1)

        theme.section_label(self.sidebar, "Actions").pack(anchor="w", pady=(theme.GAP, 6))
        for text, command in (
            ("New chat", self._restart),
            ("Free VRAM", self._free_vram),
            ("Start Ollama", self._restart_ollama),
            ("Export diagnostics", self._export_diagnostics),
        ):
            theme.button(self.sidebar, text, command, kind="ghost", anchor="w").pack(fill=X, pady=1)
        theme.button(
            self.sidebar, "Reboot session", self._restart_session, kind="danger", anchor="w"
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
        card = theme.card(self.sidebar, pad=12)
        card.pack(fill=X)
        self.dot = Label(
            card, text="●", bg=theme.CARD, fg=theme.SPEAK_OFF, font=("Segoe UI Light", 30)
        )
        self.dot.pack(anchor="center")
        self.status = Label(
            card,
            text="Warming up models...",
            bg=theme.CARD,
            fg=theme.SUBTLE,
            wraplength=190,
            justify="center",
            font=theme.FONT_SMALL,
        )
        self.status.pack(anchor="center", pady=(0, 10))
        # Built directly (not via the factory): its colours flip with mute state.
        self.mute_btn = Button(
            card,
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

    def _build_telemetry(self) -> None:
        theme.section_label(self.sidebar, "Telemetry").pack(anchor="w", pady=(theme.GAP, 6))
        self._lat_labels: dict[str, Label] = {}
        for key in _LATENCY_KEYS:
            self._lat_labels[key] = self._metric(key.upper() if key != "total" else "Total", "—")
        self.tts_label = self._metric("TTS", cfg.TTS_BACKEND)
        self.wake_label = self._metric("Wake", self._wake_status().message)

    def _build_main_area(self) -> None:
        self._build_header_card()
        self._build_agent_strip()
        self._build_confirm_bar()
        self._build_composer_card()
        self._build_transcript_card()

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
        return wake_word.preflight(wake_word.settings_from_config(cfg))

    def _build_header_card(self) -> None:
        header = Frame(self.main, bg=theme.BG)
        header.pack(fill=X, pady=(0, theme.GAP))
        self.persona_title = Label(
            header,
            text=self._persona.name,
            bg=theme.BG,
            fg=theme.FG,
            font=("Segoe UI Semibold", 18),
        )
        self.persona_title.pack(side=LEFT)
        self.blurb = Label(
            header, text=self._persona.blurb, bg=theme.BG, fg=theme.SUBTLE, font=theme.FONT_SMALL
        )
        self.blurb.pack(side=RIGHT)

        controls = theme.card(self.main, pad=14)
        controls.pack(fill=X, pady=(0, theme.GAP))
        row = Frame(controls, bg=theme.CARD)
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

    def _build_agent_strip(self) -> None:
        strip = Frame(self.main, bg=theme.BG)
        strip.pack(fill=X, pady=(0, theme.GAP))
        theme.section_label(strip, "Agent backends", bg=theme.BG).pack(
            side=LEFT, padx=(2, theme.GAP)
        )
        for backend in self._session.agent_backends():
            elevated = "yolo" in backend
            chip = Frame(
                strip,
                bg=theme.CARD,
                padx=10,
                pady=5,
                highlightthickness=1,
                highlightbackground=theme.BORDER,
            )
            chip.pack(side=LEFT, padx=(0, theme.GAP_SM))
            Label(
                chip,
                text="●",
                bg=theme.CARD,
                fg=theme.DANGER if elevated else theme.ACCENT,
                font=("Segoe UI", 7),
            ).pack(side=LEFT, padx=(0, 6))
            name = Frame(chip, bg=theme.CARD)
            name.pack(side=LEFT)
            Label(
                name,
                text=backend,
                bg=theme.CARD,
                fg=theme.FG,
                font=("Segoe UI Semibold", 9),
                justify="left",
            ).pack(anchor="w")
            Label(
                name,
                text=self._session.agent_machine(backend),
                bg=theme.CARD,
                fg=theme.DIM,
                font=("Cascadia Mono", 8),
                justify="left",
            ).pack(anchor="w")

    def _build_transcript_card(self) -> None:
        card = theme.card(self.main, pad=0)
        self.transcript_card = card
        card.pack(fill=BOTH, expand=True, pady=(0, theme.GAP))
        header = Frame(card, bg=theme.CARD, padx=14, pady=10)
        header.pack(fill=X)
        Label(header, text="Conversation", bg=theme.CARD, fg=theme.FG, font=theme.FONT_H2).pack(
            side=LEFT
        )
        Label(
            header,
            text="voice · text · agent events",
            bg=theme.CARD,
            fg=theme.DIM,
            font=theme.FONT_SMALL,
        ).pack(side=RIGHT)

        self.log = theme.scrolled_text(card, font=theme.FONT_BODY, padx=16, pady=12)
        self.log.frame.configure(highlightthickness=0)
        self.log.frame.pack(fill=BOTH, expand=True, padx=1, pady=(0, 1))
        self.log.tag_config("name_user", foreground=theme.USER, font=theme.FONT_NAME, spacing1=12)
        self.log.tag_config(
            "name_assistant", foreground=theme.BOT, font=theme.FONT_NAME, spacing1=12
        )
        self.log.tag_config("body", foreground=theme.FG, spacing3=2)
        self.log.tag_config("sys", foreground=theme.DIM, spacing1=8, font=theme.FONT_SMALL)
        self.log.tag_config("agent", foreground=theme.WARN, spacing1=8, font=theme.FONT_SMALL)
        self.log.configure(state="disabled")
        self._append_sys("Jess is warming up — speak once the mic goes live, or type below.")

    def _build_composer_card(self) -> None:
        card = theme.card(self.main, pad=12)
        card.pack(side="bottom", fill=X)
        row = Frame(card, bg=theme.CARD)
        row.pack(fill=X)
        self.type_var = StringVar()
        self.message_entry = theme.entry(
            row,
            textvariable=self.type_var,
            placeholder="Message Jess — Enter to send",
            bg=theme.SURFACE,
        )
        self.message_entry.pack(side=LEFT, fill=X, expand=True, ipady=8, padx=(0, 10))
        self.message_entry.bind("<Return>", lambda _e: self._send_typed())
        theme.button(row, "Send", self._send_typed, kind="primary").pack(side=LEFT, padx=(0, 6))
        theme.button(row, "Delegate", self._delegate_typed).pack(side=LEFT)
        Label(
            card,
            text="Delegate runs the message on the selected tool user instead of Jess.",
            bg=theme.CARD,
            fg=theme.DIM,
            font=theme.FONT_SMALL,
        ).pack(anchor="w", pady=(6, 0))

    # -- small UI helpers -----------------------------------------------------

    def _metric(self, label: str, value: str) -> Label:
        row = Frame(self.sidebar, bg=theme.SURFACE, pady=2)
        row.pack(fill=X)
        Label(
            row, text=label.upper(), bg=theme.SURFACE, fg=theme.DIM, font=theme.FONT_SECTION
        ).pack(side=LEFT)
        widget = Label(
            row,
            text=value,
            bg=theme.SURFACE,
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
            ),
        )

    def _on_model_pick(self, _evt=None) -> None:
        if self._syncing:
            return
        model = self.model_box.get()
        if model:
            self._session.set_model(model)
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
            theme.WARN if self.muted else theme.FG,
        )

    def _send_typed(self) -> None:
        if theme.placeholder_active(self.message_entry):
            return
        text = self.type_var.get().strip()
        if not text:
            return
        self.type_var.set("")
        self._session.send_text(text)

    def _delegate_typed(self) -> None:
        if theme.placeholder_active(self.message_entry):
            return
        text = self.type_var.get().strip()
        if not text:
            return
        self.type_var.set("")
        backend = self._session.default_agent_backend()
        self._append_message("user", f"You → {backend}", text)
        self._session.start_agent_task(backend, text)

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
            self.health_pill.set(evt.get("label", "Ollama ?"), "ok" if ok else "danger")
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
            self.wake_label.configure(text=f"armed — say '{model}'", fg=theme.WARN)
        elif state == "awake":
            window = evt.get("window_secs")
            self.wake_label.configure(
                text=f"awake ({window:.0f}s window)" if window else "awake", fg=theme.OK
            )
        elif state == "bypass":
            self.wake_label.configure(text="engine failed — always listening", fg=theme.DANGER)

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
        self.root.mainloop()


if __name__ == "__main__":
    VoiceGUI().run()
