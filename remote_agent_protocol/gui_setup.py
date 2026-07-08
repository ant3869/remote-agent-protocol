"""Setup wizard window for the desktop GUI."""

from __future__ import annotations

from tkinter import BOTH, LEFT, RIGHT, Frame, Label, Toplevel, X

from remote_agent_protocol import config as cfg
from remote_agent_protocol import gui_theme as theme


class SetupWizard:
    """Polished setup/status walkthrough for first-run and diagnostics checks."""

    def __init__(self, root, session) -> None:
        """Initialize the wizard with the owning root and live session facade."""
        self._root = root
        self._session = session
        self._window: Toplevel | None = None
        self._step = 0
        self._body: Frame | None = None
        self._pages = (
            ("Voice stack", "Confirm the local audio path and voice interaction mode."),
            ("AI runtime", "Check the model, tool agents, and local server connection."),
            ("Memory", "Review local transcript and semantic-memory readiness."),
            ("Ready", "Launch into the assistant control center."),
        )

    def open(self) -> None:
        """Show the setup wizard, creating its window on first open."""
        if self._window is not None and self._window.winfo_exists():
            self._window.lift()
            return
        win = Toplevel(self._root)
        self._window = win
        win.title("Setup Wizard")
        win.geometry(f"960x680+{self._root.winfo_rootx() + 130}+{self._root.winfo_rooty() + 60}")
        win.minsize(820, 560)
        win.configure(bg=theme.BG)
        theme.enable_dark_title_bar(win)
        win.protocol("WM_DELETE_WINDOW", lambda: (setattr(self, "_window", None), win.destroy()))

        header = Frame(win, bg=theme.BG, padx=28, pady=22)
        header.pack(fill=X)
        Label(
            header,
            text="Remote Agent Protocol Setup",
            bg=theme.BG,
            fg=theme.FG,
            font=("Segoe UI Semibold", 22),
        ).pack(anchor="w")
        Label(
            header,
            text="A dark blue control-center setup flow for voice, models, agents, and memory.",
            bg=theme.BG,
            fg=theme.SUBTLE,
            font=theme.FONT_BODY,
        ).pack(anchor="w", pady=(4, 0))

        shell = theme.panel(
            win, bg=theme.CARD, border=theme.BORDER_LIGHT, pad=18, radius=28, glow=True
        )
        shell.pack(fill=BOTH, expand=True, padx=28, pady=(0, 22))
        self._body = shell.body

        footer = Frame(win, bg=theme.BG, padx=28)
        footer.pack(fill=X, pady=(0, 22))
        self._back_btn = theme.button(footer, "Back", self._back, kind="ghost")
        self._back_btn.pack(side=LEFT)
        self._next_btn = theme.button(footer, "Next", self._next, kind="primary")
        self._next_btn.pack(side=RIGHT)
        self._render()

    def _render(self) -> None:
        if self._body is None:
            return
        for child in self._body.winfo_children():
            child.destroy()
        title, subtitle = self._pages[self._step]
        Label(
            self._body,
            text=f"{self._icon()}  {title}",
            bg=theme.CARD,
            fg=theme.FG,
            font=("Segoe UI Semibold", 24),
        ).pack(anchor="w")
        Label(
            self._body,
            text=subtitle,
            bg=theme.CARD,
            fg=theme.SUBTLE,
            font=theme.FONT_BODY,
        ).pack(anchor="w", pady=(4, 18))

        if self._step == 0:
            self._option_grid(
                (
                    ("Free Talk", "Always-on conversational mode", "selected"),
                    ("Wake Word", "Passive listening until activation", "info"),
                    ("Push To Talk", "Manual capture for precise turns", "neutral"),
                )
            )
            self._requirements((("Microphone", "Configured", "ok"), ("TTS", cfg.TTS_BACKEND, "ok")))
        elif self._step == 1:
            backends = ", ".join(self._session.agent_backends())
            self._requirements(
                (
                    ("Ollama host", cfg.OLLAMA_HOST, "info"),
                    ("Default model", cfg.LLM_MODEL, "ok"),
                    (
                        "Tool agents",
                        backends or "No agents configured",
                        "warn" if not backends else "ok",
                    ),
                )
            )
        elif self._step == 2:
            self._option_grid(
                (
                    ("Short-term", "Conversation transcript context", "selected"),
                    (
                        "Semantic",
                        "Pinned long-term facts",
                        "selected" if cfg.MEM0_ENABLED else "neutral",
                    ),
                    ("Private", "Local state under data/", "info"),
                )
            )
            self._requirements(
                (
                    (
                        "Transcript memory",
                        "Enabled" if cfg.MEMORY_ENABLED else "Disabled",
                        "ok" if cfg.MEMORY_ENABLED else "warn",
                    ),
                    (
                        "Semantic memory",
                        "Enabled" if cfg.MEM0_ENABLED else "Disabled",
                        "ok" if cfg.MEM0_ENABLED else "warn",
                    ),
                )
            )
        else:
            tips = theme.panel(
                self._body, bg=theme.GLOW_CARD, border=theme.BORDER_ACTIVE, pad=18, radius=20
            )
            tips.pack(fill=BOTH, expand=True)
            body = tips.body
            Label(
                body, text="Quick tips", bg=theme.GLOW_CARD, fg=theme.CYAN, font=theme.FONT_H2
            ).pack(anchor="w")
            for text in (
                "Use the sidebar to switch memory, agents, and persona settings.",
                "Context attachments stay collapsed until you open Context in the composer.",
                "Status pills show session, Ollama, TTS, and agent readiness.",
            ):
                Label(
                    body, text=f"• {text}", bg=theme.GLOW_CARD, fg=theme.FG, font=theme.FONT_BODY
                ).pack(anchor="w", pady=(10, 0))

        self._back_btn.configure(state="disabled" if self._step == 0 else "normal")
        self._next_btn.configure(text="Start" if self._step == len(self._pages) - 1 else "Next")

    def _icon(self) -> str:
        return ("◉", "◆", "✦", "✓")[self._step]

    def _option_grid(self, items: tuple[tuple[str, str, str], ...]) -> None:
        grid = Frame(self._body, bg=theme.CARD)
        grid.pack(fill=X, pady=(0, theme.GAP))
        for title, text, tone in items:
            selected = tone == "selected"
            card = theme.panel(
                grid,
                bg=theme.SELECT_BG if selected else theme.ELEVATED_BG,
                border=theme.BORDER_ACTIVE if selected else theme.BORDER,
                pad=14,
                radius=18,
            )
            card.pack(fill=X, pady=(0, theme.GAP_SM))
            body = card.body
            body_bg = theme.SELECT_BG if selected else theme.ELEVATED_BG
            Label(body, text=title, bg=body_bg, fg=theme.FG, font=theme.FONT_H2).pack(anchor="w")
            Label(body, text=text, bg=body_bg, fg=theme.SUBTLE, font=theme.FONT_SMALL).pack(
                anchor="w", pady=(6, 0)
            )

    def _requirements(self, rows: tuple[tuple[str, str, str], ...]) -> None:
        panel = theme.panel(
            self._body, bg=theme.ELEVATED_BG, border=theme.BORDER, pad=16, radius=20
        )
        panel.pack(fill=X)
        body = panel.body
        Label(
            body, text="Requirements", bg=theme.ELEVATED_BG, fg=theme.FG, font=theme.FONT_H2
        ).pack(anchor="w", pady=(0, 8))
        for label, value, tone in rows:
            row = Frame(body, bg=theme.ELEVATED_BG, pady=6)
            row.pack(fill=X)
            Label(
                row, text="●", bg=theme.ELEVATED_BG, fg=theme.TONES[tone], font=("Segoe UI", 9)
            ).pack(side=LEFT, padx=(0, 10))
            Label(row, text=label, bg=theme.ELEVATED_BG, fg=theme.FG, font=theme.FONT_STRONG).pack(
                side=LEFT
            )
            Label(
                row, text=value, bg=theme.ELEVATED_BG, fg=theme.SUBTLE, font=theme.FONT_SMALL
            ).pack(side=RIGHT)

    def _back(self) -> None:
        self._step = max(0, self._step - 1)
        self._render()

    def _next(self) -> None:
        if self._step >= len(self._pages) - 1:
            if self._window is not None:
                self._window.destroy()
                self._window = None
            return
        self._step += 1
        self._render()
