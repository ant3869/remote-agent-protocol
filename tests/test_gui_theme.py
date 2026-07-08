from pathlib import Path

from remote_agent_protocol import gui_theme as theme
from remote_agent_protocol.gui_memory import MemoryPanel
from remote_agent_protocol.gui_setup import SetupWizard


class _FakeText:
    def __init__(self):
        self.value = ""

    def configure(self, **_kwargs):
        return None

    def delete(self, *_args):
        self.value = ""

    def insert(self, *_args):
        self.value = _args[-1]


class _FakeList:
    def __init__(self, selection):
        self._selection = selection

    def curselection(self):
        return self._selection


def test_theme_exposes_dark_blue_control_center_tokens():
    assert theme.APP_BG == "#05070c"
    assert theme.PANEL_BG == theme.SURFACE
    assert theme.BLUE == theme.ACCENT
    assert theme.CYAN == theme.ACCENT_HOVER
    assert theme.FOCUS_RING == "#38bdf8"
    assert theme.WARNING == theme.WARN
    assert theme.MUTED == theme.DISABLED
    assert theme.BOT == theme.CYAN
    assert theme.BORDER_ACTIVE == theme.CYAN
    assert theme.RADIUS >= 18


def test_theme_exposes_rounded_panel_component():
    assert theme.RoundedPanel.__name__ == "RoundedPanel"
    assert callable(theme.panel)


def test_setup_wizard_is_available_from_gui_layer():
    assert SetupWizard.__name__ == "SetupWizard"


def test_gui_theme_has_no_obsolete_gold_accent_values():
    source = Path("remote_agent_protocol/gui_theme.py").read_text(encoding="utf-8")
    obsolete = {"#f2ab66", "#f5c04e", "gold", "yellow"}
    assert obsolete.isdisjoint(source.lower().split())


def test_memory_detail_panel_renders_selected_row():
    panel = MemoryPanel.__new__(MemoryPanel)
    panel.detail_text = _FakeText()
    panel.semantic_list = _FakeList((0,))
    panel._semantic_rows = [
        {"id": "abc123", "score": 0.91, "memory": "User likes blue dashboards.", "topic": "ui"}
    ]

    panel._show_selected_memory()

    assert "ABC123" not in panel.detail_text.value
    assert "abc123" in panel.detail_text.value
    assert "User likes blue dashboards." in panel.detail_text.value
    assert "TOPIC" in panel.detail_text.value
