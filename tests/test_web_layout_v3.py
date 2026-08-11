import re
from collections import Counter
from pathlib import Path

WEB_APP = Path(__file__).parents[1] / "remote_agent_protocol" / "web_app"
HTML = (WEB_APP / "index.html").read_text(encoding="utf-8")
APP_JS = (WEB_APP / "app.js").read_text(encoding="utf-8")


def test_literal_app_id_references_exist_once():
    html_ids = re.findall(r'id="([^"]+)"', HTML)
    literal_refs = set(re.findall(r'\$\(["\']([^"\']+)["\']\)', APP_JS))

    assert not sorted(literal_refs - set(html_ids))
    assert not sorted(key for key, count in Counter(html_ids).items() if count > 1)


def test_v4_command_frame_and_landmarks_are_wired():
    assert '<link rel="stylesheet" href="/layout-v4.css" />' in HTML
    assert '/layout-v3.css' not in HTML
    assert '<script src="/ui-shell-state.js"></script>' in HTML
    assert '<script src="/app.js"></script>' in HTML
    assert HTML.index('/input-control-state.js') < HTML.index('/ui-shell-state.js') < HTML.index('/avatar.js') < HTML.index('/app.js')
    assert 'class="command-header"' in HTML
    assert 'class="destination-rail"' in HTML
    assert 'class="context-rail"' in HTML
    assert 'class="task-canvas workspace"' in HTML
    assert 'class="runtime-statusline"' in HTML
    assert HTML.count('data-context-view=') == 7
    assert HTML.count('data-settings-section=') == 5
    assert 'id="appTitle"' in HTML
    assert 'id="healthCluster"' in HTML
    assert 'id="memoryLayout"' in HTML
    assert 'id="paletteCloseBtn"' in HTML
    assert 'aria-modal="true"' in HTML
    assert 'aria-current="page"' in HTML
    assert 'role="group" aria-label="Memory scope"' in HTML
    assert 'id="setupNextBtn"' in HTML
    assert 'document.createElement("button")' in APP_JS
    assert 'card.setAttribute("aria-pressed"' in APP_JS
    assert 'pttControl.keyDown' in APP_JS
    assert 'option.classList.toggle("active"' in APP_JS
    assert 'field.disabled = field.id === "personaTone"' in APP_JS


def test_status_renderer_emits_semantic_topology():
    assert 'class="status-flow"' in APP_JS
    assert 'class="status-inspection"' in APP_JS
    assert 'class="status-incidents"' in APP_JS
    assert 'class="status-resource-stack"' in APP_JS


def test_command_frame_exposes_accessible_structure_and_state():
    assert '<h1 id="appTitle">' in HTML
    assert '<h2 id="heroPersona">' in HTML
    assert '<h2>General</h2>' in HTML
    assert '<h2>Voice and TTS</h2>' in HTML
    assert '<strong class="context-title">Settings</strong>' in HTML
    assert 'class="destination-rail" aria-label="Application navigation"' in HTML
    assert 'class="context-rail" aria-label="View context"' in HTML
    assert 'class="agent-jobs-panel" aria-label="Operation queue"' in HTML
    assert 'class="persona-inspector" aria-label="Persona configuration"' in HTML
    assert 'class="memory-detail" aria-label="Selected memory"' in HTML
    assert 'item.setAttribute("aria-current", "page")' in HTML
    assert 'item.removeAttribute("aria-current")' in HTML


def test_new_files_stay_cohesive_and_under_project_limit():
    for name in ("index.html", "layout-v4.css", "ui-shell-state.js"):
        lines = (WEB_APP / name).read_text(encoding="utf-8").splitlines()
        assert len(lines) <= 600, f"{name} grew to {len(lines)} lines"
