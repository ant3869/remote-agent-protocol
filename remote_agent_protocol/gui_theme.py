"""Design system for Remote Agent Protocol -- tokens, shared ttk styling, widget factories."""

from __future__ import annotations

import ctypes
from tkinter import Button, Canvas, Entry, Frame, Label, Listbox, Text, ttk

# -- colour tokens -------------------------------------------------------------

APP_BG = "#05070c"  # near-black app background
PANEL_BG = "#0b1220"  # topbar / sidebar / panel background
CARD_BG = "#111827"  # raised cards
ELEVATED_BG = "#172033"  # controls resting on a card
GLOW_BG = "#0e2138"
INSET_BG = "#080d16"  # transcript, logs, lists
BORDER = "#243149"
BORDER_LIGHT = "#334563"
BORDER_ACTIVE = "#22d3ee"

TEXT_PRIMARY = "#f4f7fb"
TEXT_SECONDARY = "#b7c3d7"
TEXT_MUTED = "#728197"

BLUE = "#3b82f6"
BLUE_HOVER = "#60a5fa"
CYAN = "#22d3ee"
FOCUS_RING = "#38bdf8"
GLOW = "#134a66"
ON_ACCENT = "#03111f"

SUCCESS = "#22c55e"
WARNING = "#f59e0b"
ERROR = "#fb7185"
DISABLED = "#3b475c"
INFO = "#38bdf8"
SELECT_BG = "#12365d"

BG = APP_BG
SURFACE = PANEL_BG
CARD = CARD_BG
CARD_HOVER = ELEVATED_BG
GLOW_CARD = GLOW_BG
INSET = INSET_BG

FG = TEXT_PRIMARY
SUBTLE = TEXT_SECONDARY
DIM = TEXT_MUTED

ACCENT = BLUE
ACCENT_HOVER = CYAN

USER = BLUE_HOVER
BOT = CYAN
OK = SUCCESS
WARN = WARNING
DANGER = ERROR
ON_DANGER = "#2a0710"
DANGER_BG = "#2a101b"
DANGER_BG_HOVER = "#3b1626"

# Aliases kept so the palette names used across the app stay stable.
MUTED = DISABLED
SPEAK_ON = OK
SPEAK_OFF = DISABLED

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
RADIUS = 18

TONES = {
    "neutral": DIM,
    "ok": OK,
    "warn": WARN,
    "danger": DANGER,
    "accent": ACCENT,
    "info": INFO,
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
    style.map("TEntry", bordercolor=[("focus", FOCUS_RING)])
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
    style.map(
        "TButton",
        background=[("disabled", SURFACE), ("active", BORDER_LIGHT)],
        foreground=[("disabled", DIM)],
    )


# -- widget factories --------------------------------------------------------------


def _rounded_rect(canvas: Canvas, x1: int, y1: int, x2: int, y2: int, radius: int, **kwargs):
    points = [
        x1 + radius,
        y1,
        x2 - radius,
        y1,
        x2,
        y1,
        x2,
        y1 + radius,
        x2,
        y2 - radius,
        x2,
        y2,
        x2 - radius,
        y2,
        x1 + radius,
        y2,
        x1,
        y2,
        x1,
        y2 - radius,
        x1,
        y1 + radius,
        x1,
        y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


class RoundedPanel(Frame):
    """Canvas-backed rounded panel with an inner body frame."""

    def __init__(
        self,
        parent,
        *,
        bg: str = CARD,
        border: str = BORDER,
        radius: int = RADIUS,
        pad: int = PAD,
        glow: bool = False,
    ) -> None:
        """Create the panel and expose `.body` as the content container."""
        super().__init__(parent, bg=parent.cget("background"), highlightthickness=0, bd=0)
        self._bg = bg
        self._border = border
        self._radius = radius
        self._pad = pad
        self._glow = glow
        self.canvas = Canvas(self, bg=self.cget("background"), highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.body = Frame(self.canvas, bg=bg, padx=pad, pady=pad)
        self._window = self.canvas.create_window(pad, pad, anchor="nw", window=self.body)
        self.canvas.bind("<Configure>", self._draw)
        self.body.bind("<Configure>", self._sync_size)

    def _sync_size(self, _event=None) -> None:
        self.canvas.configure(
            width=max(1, self.body.winfo_reqwidth() + self._pad * 2),
            height=max(1, self.body.winfo_reqheight() + self._pad * 2),
        )

    def _draw(self, event=None) -> None:
        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()
        if width <= 2 or height <= 2:
            return
        self.canvas.delete("panel")
        if self._glow:
            _rounded_rect(
                self.canvas,
                3,
                4,
                width - 3,
                height - 2,
                self._radius + 2,
                fill=GLOW,
                outline="",
                tags="panel",
            )
        _rounded_rect(
            self.canvas,
            1,
            1,
            width - 2,
            height - 2,
            self._radius,
            fill=self._bg,
            outline=self._border,
            width=1,
            tags="panel",
        )
        self.canvas.tag_lower("panel")
        self.canvas.coords(self._window, self._pad, self._pad)
        self.canvas.itemconfigure(
            self._window, width=max(1, width - self._pad * 2), height=max(1, height - self._pad * 2)
        )


def panel(parent, *, bg=CARD, border=BORDER, pad=PAD, radius=RADIUS, glow=False) -> RoundedPanel:
    """Rounded elevated panel; add children to `.body`."""
    return RoundedPanel(parent, bg=bg, border=border, pad=pad, radius=radius, glow=glow)


def card(parent, *, bg=CARD, pad=PAD) -> Frame:
    """A raised panel with the standard 1px border."""
    return Frame(
        parent, bg=bg, padx=pad, pady=pad, highlightthickness=1, highlightbackground=BORDER
    )


def section_label(parent, text: str, *, bg=SURFACE) -> Label:
    """Small uppercase group heading."""
    return Label(parent, text=text.upper(), bg=bg, fg=DIM, font=FONT_SECTION)


def button(parent, text: str, command, *, kind: str = "default", anchor="center") -> Button:
    """Flat button with hover feedback."""
    if kind == "primary":
        colors = (ACCENT, ON_ACCENT, ACCENT_HOVER, ON_ACCENT)
    elif kind == "selected":
        colors = (SELECT_BG, FG, BORDER_LIGHT, FG)
    elif kind == "warning":
        colors = ("#2b2111", WARN, "#3d2d13", WARN)
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
        highlightthickness=1,
        highlightbackground=base_bg,
        highlightcolor=FOCUS_RING,
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
