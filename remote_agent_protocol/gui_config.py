"""Persona configuration editor for Jess GUI."""

from __future__ import annotations

from tkinter import BOTH, END, RIGHT, Frame, Label, StringVar, Toplevel, X, ttk

from remote_agent_protocol import config as cfg
from remote_agent_protocol import gui_theme as theme
from remote_agent_protocol import persona_config, personas


class ConfigPanel:
    """Persona-override editor window; saves to persona_overrides.json."""

    def __init__(self, root, voice_labels, model_choices, tool_users, on_saved):
        """Initialize the panel.

        Args:
            root: The main Tk window that owns this Toplevel.
            voice_labels: Display label -> voice ref map for the voice picker.
            model_choices: Ollama model names for the LLM picker.
            tool_users: Agent backend names for the tool-user picker.
            on_saved: Called after a save so the main window can reload.
        """
        self.root = root
        self._voice_labels = voice_labels
        self._model_choices = model_choices
        self._tool_users = tool_users
        self._on_saved = on_saved
        self.win = None
        self._config = persona_config.load_config()
        self._current_name = personas.names()[0]

    def open(self) -> None:
        """Show the config window, re-reading overrides from disk."""
        if self.win is not None and self.win.winfo_exists():
            self.win.lift()
            return
        # Re-read overrides so external edits (or another panel's save) show up.
        self._config = persona_config.load_config()
        self.win = Toplevel(self.root)
        self.win.title("Persona settings")
        self.win.configure(bg=theme.BG)
        self.win.geometry(f"860x680+{self.root.winfo_rootx() + 110}+{self.root.winfo_rooty() + 50}")
        self.win.minsize(720, 560)
        theme.enable_dark_title_bar(self.win)
        self._build()
        self._load_persona(self._current_name)

    def _build(self) -> None:
        header = Frame(self.win, bg=theme.BG, padx=20, pady=14)
        header.pack(fill=X)
        Label(
            header, text="Persona settings", bg=theme.BG, fg=theme.FG, font=theme.FONT_TITLE
        ).pack(anchor="w")
        Label(
            header,
            text="Overrides layer on the built-in personas and save to persona_overrides.json.",
            bg=theme.BG,
            fg=theme.SUBTLE,
            font=theme.FONT_SMALL,
        ).pack(anchor="w")

        toolbar_panel = theme.panel(
            self.win, bg=theme.CARD, border=theme.BORDER_LIGHT, pad=14, radius=22
        )
        toolbar_panel.pack(fill=X, padx=20)
        toolbar = toolbar_panel.body
        Label(toolbar, text="PERSONA", bg=theme.CARD, fg=theme.DIM, font=theme.FONT_SECTION).pack(
            side="left", padx=(0, 8)
        )
        self.persona_var = StringVar(value=self._current_name)
        self.persona_box = ttk.Combobox(
            toolbar,
            textvariable=self.persona_var,
            values=personas.names(),
            state="readonly",
            width=20,
        )
        self.persona_box.pack(side="left")
        self.persona_box.bind(
            "<<ComboboxSelected>>", lambda _e: self._load_persona(self.persona_var.get())
        )
        theme.button(toolbar, "Save persona", self._save_current, kind="primary").pack(side=RIGHT)

        body_panel = theme.panel(
            self.win, bg=theme.CARD, border=theme.BORDER_LIGHT, pad=theme.PAD, radius=24, glow=True
        )
        body_panel.pack(fill=BOTH, expand=True, padx=20, pady=14)
        body = body_panel.body

        self.voice_var = StringVar()
        self.backend_var = StringVar()
        self.voice_model_var = StringVar()
        self.model_var = StringVar()
        self.tool_var = StringVar()
        self.blurb_var = StringVar()

        self._combo(body, "Voice", self.voice_var, list(self._voice_labels.keys()), 0, 0, 54)
        self._combo(
            body, "Voice backend", self.backend_var, ["kokoro", "voicebox", "cartesia"], 0, 1, 16
        )
        self._combo(
            body,
            "Voice model",
            self.voice_model_var,
            ["", "0.6B", "1.7B", "1B", "3B", cfg.VOICEBOX_DEFAULT_MODEL],
            1,
            0,
            20,
        )
        self._combo(body, "LLM model", self.model_var, [""] + self._model_choices, 1, 1, 26)
        self._combo(body, "Tool user", self.tool_var, [""] + self._tool_users, 2, 0, 20)

        Label(body, text="BLURB", bg=theme.CARD, fg=theme.DIM, font=theme.FONT_SECTION).grid(
            row=6, column=0, sticky="w", pady=(theme.GAP, 0)
        )
        theme.entry(body, textvariable=self.blurb_var, font=theme.FONT).grid(
            row=7, column=0, columnspan=2, sticky="ew", pady=(4, 0), ipady=4
        )

        Label(
            body,
            text="PERSONALITY / SYSTEM PROMPT CORE",
            bg=theme.CARD,
            fg=theme.DIM,
            font=theme.FONT_SECTION,
        ).grid(row=8, column=0, sticky="w", pady=(theme.GAP, 0))
        self.personality = theme.scrolled_text(body, font=theme.FONT_BODY, padx=12, pady=10)
        self.personality.frame.grid(row=9, column=0, columnspan=2, sticky="nsew", pady=(4, 8))
        self.status = Label(body, text="", bg=theme.CARD, fg=theme.DIM, font=theme.FONT_SMALL)
        self.status.grid(row=10, column=0, columnspan=2, sticky="w")
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(9, weight=1)

    def _combo(self, parent, label, var, values, row, col, width):
        box = Frame(parent, bg=theme.CARD)
        box.grid(row=row * 2, column=col, sticky="ew", padx=(0, theme.GAP), pady=(4, 0))
        Label(box, text=label.upper(), bg=theme.CARD, fg=theme.DIM, font=theme.FONT_SECTION).pack(
            anchor="w"
        )
        ttk.Combobox(box, textvariable=var, values=values, width=width).pack(
            anchor="w", fill="x", pady=(4, 0)
        )

    def _base(self, name: str) -> personas.Persona:
        return personas.by_name(name)

    def _load_persona(self, name: str) -> None:
        self._current_name = name
        base = self._base(name)
        effective = persona_config.apply_override(base, self._config.personas.get(name))
        voice_label = next(
            (label for label, ref in self._voice_labels.items() if ref == effective.voice), ""
        )
        self.voice_var.set(voice_label)
        self.backend_var.set(effective.voice_backend)
        self.voice_model_var.set(effective.voice_model or "")
        self.model_var.set(effective.model or "")
        self.tool_var.set(effective.tool_user or "")
        self.blurb_var.set(effective.blurb)
        self.personality.delete("1.0", END)
        self.personality.insert("1.0", effective.personality)
        self.status.configure(
            text=f"Editing {name} — Save writes persona_overrides.json", fg=theme.DIM
        )

    def _save_current(self) -> None:
        name = self._current_name
        voice_ref = self._voice_labels.get(self.voice_var.get(), self._base(name).voice)
        override = persona_config.PersonaOverride(
            voice=voice_ref,
            voice_backend=self.backend_var.get().strip() or "kokoro",
            voice_model=self.voice_model_var.get().strip() or None,
            personality=self.personality.get("1.0", END).strip(),
            blurb=self.blurb_var.get().strip(),
            model=self.model_var.get().strip() or None,
            tool_user=self.tool_var.get().strip() or None,
        )
        if not persona_config.valid_tool_user(override.tool_user):
            self.status.configure(text=f"Unknown tool user: {override.tool_user}", fg=theme.DANGER)
            return
        self._config.personas[name] = override
        persona_config.save_config(self._config)
        self.status.configure(
            text=f"Saved {name}. Restart may be needed for backend swaps.", fg=theme.OK
        )
        self._on_saved()
