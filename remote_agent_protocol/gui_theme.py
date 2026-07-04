"""Design system for Remote Agent Protocol -- tokens, shared ttk styling, widget factories.

Every window (the main shell and the toplevel panels) builds from these parts
so the app reads as one product: calm dark neutrals, a single teal accent,
semantic status colours, and a consistent spacing rhythm.
"""

from __future__ import annotations

import ctypes
from tkinter import Button, Entry, Frame, Label, Listbox, Text, ttk

# -- colour tokens -------------------------------------------------------------

BG = "#0a0d13"  # app background
SURFACE = "#10151f"  # topbar / sidebar / field fills
CARD = "#151b29"  # raised panels
CARD_HOVER = "#1d2536"  # hover states + controls resting on a card
INSET = "#0c1018"  # recessed wells: transcript, logs, lists
BORDER = "#232c3f"
BORDER_LIGHT = "#2f3a52"

FG = "#e8eef8"  # primary text
SUBTLE = "#94a3b8"  # secondary text
DIM = "#5f6c82"  # tertiary text, section labels

ACCENT = "#3ecfb2"
ACCENT_HOVER = "#5fe0c6"
ON_ACCENT = "#062720"  # text on accent fills

USER = "#82b8ff"  # user speaker colour
BOT = "#f2ab66"  # assistant speaker colour
OK = "#4ade80"
WARN = "#f5c04e"
DANGER = "#f8717a"
ON_DANGER = "#2b0d12"
DANGER_BG = "#291720"  # resting fill for destructive buttons
DANGER_BG_HOVER = "#3a202c"
SELECT_BG = "#20344d"  # list/text selection

# Aliases kept so the palette names used across the app stay stable.
MUTED = WARN
CYAN = ACCENT_HOVER
SPEAK_ON = OK
SPEAK_OFF = "#33415b"

# -- type + spacing tokens -------------------------------------------------------

FONT = ("Segoe UI", 10)
FONT_SMALL = ("Segoe UI", 9)
FONT_BODY = ("Segoe UI", 11)
FONT_STRONG = ("Segoe UI Semibold", 10)
FONT_TITLE = ("Segoe UI Semibold", 15)
FONT_H2 = ("Segoe UI Semibold", 12)
FONT_SUBTITLE = ("Segoe UI", 9)
FONT_SECTION = ("Segoe UI Semibold", 8)
FONT_NAME = ("Segoe UI Semibold", 11)
FONT_MONO = ("Cascadia Mono", 10)
FONT_MONO_SMALL = ("Cascadia Mono", 9)

PAD = 16  # card interior padding
GAP = 12  # between cards / sections
GAP_SM = 8
GAP_XS = 4

TONES = {
    "neutral": DIM,
    "ok": OK,
    "warn": WARN,
    "danger": DANGER,
    "accent": ACCENT,
}

# -- window chrome ---------------------------------------------------------------


def enable_dark_title_bar(window) -> None:
    """Ask Windows DWM for a dark title bar; silently a no-op elsewhere."""
    try:
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(1)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (20; 19 pre-20H1)
            if (
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)
                )
                == 0
            ):
                break
    except Exception:
        pass


def init_style(root) -> None:
    """Apply the shared ttk styling once per Tk root (toplevels inherit it)."""
    root.option_add("*Font", FONT)
    root.option_add("*TCombobox*Listbox.background", CARD)
    root.option_add("*TCombobox*Listbox.foreground", FG)
    root.option_add("*TCombobox*Listbox.selectBackground", SELECT_BG)
    root.option_add("*TCombobox*Listbox.selectForeground", FG)
    root.option_add("*TCombobox*Listbox.borderWidth", 0)

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG, foreground=FG)
    style.configure(
        "TCombobox",
        fieldbackground=CARD_HOVER,
        background=CARD_HOVER,
        foreground=FG,
        arrowcolor=SUBTLE,
        bordercolor=BORDER,
        lightcolor=CARD_HOVER,
        darkcolor=CARD_HOVER,
        insertcolor=FG,
        padding=6,
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", CARD_HOVER)],
        foreground=[("readonly", FG)],
        selectbackground=[("readonly", CARD_HOVER)],
        selectforeground=[("readonly", FG)],
        bordercolor=[("focus", ACCENT)],
    )
    style.configure(
        "TEntry",
        fieldbackground=SURFACE,
        foreground=FG,
        insertcolor=FG,
        bordercolor=BORDER,
        lightcolor=SURFACE,
        darkcolor=SURFACE,
        padding=8,
    )
    style.map("TEntry", bordercolor=[("focus", ACCENT)])
    style.configure(
        "Vertical.TScrollbar",
        background=CARD_HOVER,
        troughcolor=INSET,
        bordercolor=INSET,
        lightcolor=CARD_HOVER,
        darkcolor=CARD_HOVER,
        arrowcolor=DIM,
        gripcount=0,
        relief="flat",
    )
    style.map("Vertical.TScrollbar", background=[("active", BORDER_LIGHT)])
    style.configure("TButton", background=CARD_HOVER, foreground=FG, borderwidth=0, padding=(12, 7))
    style.map("TButton", background=[("active", BORDER_LIGHT)])


# -- widget factories --------------------------------------------------------------


def card(parent, *, bg=CARD, pad=PAD) -> Frame:
    """A raised panel with the standard 1px border."""
    return Frame(
        parent, bg=bg, padx=pad, pady=pad, highlightthickness=1, highlightbackground=BORDER
    )


def section_label(parent, text: str, *, bg=SURFACE) -> Label:
    """Small uppercase group heading."""
    return Label(parent, text=text.upper(), bg=bg, fg=DIM, font=FONT_SECTION)


def button(parent, text: str, command, *, kind: str = "default", anchor="center") -> Button:
    """Flat button with hover feedback. Kinds: default, primary, danger, ghost."""
    if kind == "primary":
        colors = (ACCENT, ON_ACCENT, ACCENT_HOVER, ON_ACCENT)
    elif kind == "danger":
        colors = (DANGER_BG, DANGER, DANGER_BG_HOVER, DANGER)
    elif kind == "ghost":
        try:
            base = parent.cget("background")
        except Exception:
            base = BG
        colors = (base, SUBTLE, CARD_HOVER, FG)
    else:
        colors = (CARD_HOVER, FG, BORDER_LIGHT, FG)
    base_bg, base_fg, hover_bg, hover_fg = colors
    btn = Button(
        parent,
        text=text,
        command=command,
        bg=base_bg,
        fg=base_fg,
        activebackground=hover_bg,
        activeforeground=hover_fg,
        relief="flat",
        bd=0,
        padx=14,
        pady=7,
        cursor="hand2",
        font=FONT,
        anchor=anchor,
        highlightthickness=0,
    )
    btn.bind("<Enter>", lambda _e: btn.configure(bg=hover_bg, fg=hover_fg))
    btn.bind("<Leave>", lambda _e: btn.configure(bg=base_bg, fg=base_fg))
    return btn


def entry(
    parent, *, textvariable=None, placeholder: str | None = None, bg=SURFACE, font=FONT_BODY
) -> Entry:
    """Flat entry with a focus ring and optional placeholder text."""
    widget = Entry(
        parent,
        textvariable=textvariable,
        bg=bg,
        fg=FG,
        relief="flat",
        insertbackground=FG,
        font=font,
        highlightthickness=1,
        highlightbackground=BORDER,
        highlightcolor=ACCENT,
        selectbackground=SELECT_BG,
        selectforeground=FG,
    )
    if placeholder:
        _attach_placeholder(widget, placeholder)
    return widget


def _attach_placeholder(widget: Entry, text: str) -> None:
    def show(_e=None) -> None:
        if not widget.get():
            widget._placeholder = True
            widget.insert(0, text)
            widget.configure(fg=DIM)

    def hide(_e=None) -> None:
        if getattr(widget, "_placeholder", False):
            widget._placeholder = False
            widget.delete(0, "end")
            widget.configure(fg=FG)

    widget.bind("<FocusIn>", hide, add="+")
    widget.bind("<FocusOut>", show, add="+")
    show()


def placeholder_active(widget) -> bool:
    """True while an entry is displaying its placeholder instead of user text."""
    return bool(getattr(widget, "_placeholder", False))


def listbox(parent, **kwargs) -> Listbox:
    """List styled as a recessed well; rows are recoloured by callers as needed."""
    defaults = dict(
        bg=INSET,
        fg=FG,
        selectbackground=SELECT_BG,
        selectforeground=FG,
        activestyle="none",
        relief="flat",
        bd=0,
        highlightthickness=1,
        highlightbackground=BORDER,
        highlightcolor=BORDER_LIGHT,
        font=FONT,
    )
    defaults.update(kwargs)
    return Listbox(parent, **defaults)


def set_list_placeholder(box: Listbox, text: str) -> None:
    """Show a dim empty-state row in a list that has no real rows."""
    box.insert("end", f"  {text}")
    box.itemconfig(0, fg=DIM)


def scrolled_text(parent, *, font=FONT_BODY, bg=INSET, padx=14, pady=12) -> Text:
    """Text widget + themed scrollbar. Pack/grid the returned widget's `.frame`."""
    frame = Frame(parent, bg=bg, highlightthickness=1, highlightbackground=BORDER)
    text = Text(
        frame,
        wrap="word",
        bg=bg,
        fg=FG,
        insertbackground=FG,
        font=font,
        relief="flat",
        padx=padx,
        pady=pady,
        bd=0,
        highlightthickness=0,
        selectbackground=SELECT_BG,
        selectforeground=FG,
    )
    bar = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
    text.configure(yscrollcommand=bar.set)
    bar.pack(side="right", fill="y")
    text.pack(side="left", fill="both", expand=True)
    text.frame = frame
    return text


class StatusPill(Frame):
    """Compact health chip: coloured dot, dim title, value. Update via `set()`."""

    def __init__(self, parent, title: str) -> None:
        """Build the pill with a dim uppercase title and a neutral dot.

        Args:
            parent: Container widget.
            title: Short label shown before the value (uppercased).
        """
        super().__init__(
            parent, bg=CARD, padx=10, pady=5, highlightthickness=1, highlightbackground=BORDER
        )
        self._dot = Label(self, text="●", bg=CARD, fg=DIM, font=("Segoe UI", 8))
        self._dot.pack(side="left", padx=(0, 7))
        Label(self, text=title.upper(), bg=CARD, fg=DIM, font=FONT_SECTION).pack(side="left")
        self._value = Label(self, text="—", bg=CARD, fg=FG, font=("Segoe UI Semibold", 9))
        self._value.pack(side="left", padx=(7, 0))

    def set(self, text: str, tone: str = "neutral") -> None:
        """Set the pill's value text and dot colour by semantic tone."""
        self._dot.configure(fg=TONES.get(tone, DIM))
        self._value.configure(text=text)
