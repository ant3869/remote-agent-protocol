from __future__ import annotations

import shutil
import subprocess
import zipfile
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


readme_path = Path("README.md")
architecture_path = Path("docs/architecture.md")
changelog_path = Path("CHANGELOG.md")
test_path = Path("tests/test_web_gui.py")

readme = readme_path.read_text(encoding="utf-8")
section = '''## Animated companion

The web Control Center includes an optional local Three.js butler companion. It reacts to wake-word detection, user speech, transcription and thinking, agent jobs, errors, and TTS speaking. Mouth movement uses a normalized envelope calculated from local TTS output; raw audio never reaches the browser.

Configure it under **Settings → Animated avatar**. Disabling the feature removes the WebGL renderer and audio-envelope connection. Motion can follow the operating-system reduced-motion preference or be explicitly set to reduced or normal.

Avatar assets live under `remote_agent_protocol/web_app/assets/avatars/<avatar-id>/`. The bundled `butler` metadata intentionally selects the procedural fallback. A future local `.glb` or `.gltf` model can be enabled by setting the metadata `model` field to a safe relative filename. Missing, unsafe, or malformed models fall back to the procedural butler without blocking the rest of the application.

'''
readme = replace_once(readme, "## 🧪 Code examples\n", section + "## 🧪 Code examples\n", "README avatar section")
readme_path.write_text(readme, encoding="utf-8")

architecture = architecture_path.read_text(encoding="utf-8")
architecture = replace_once(
    architecture,
    '''microphone -> [wake gate] -> STT -> intent router -> memory -> Ollama -> TTS -> speakers
                                      |                         |
                                      +-> AgentBridge ----------+-> spoken job updates
                                             |       |
                                             |       +-> main PC or configured remote launcher
                                             +-> loopback lifecycle WebSocket''',
    '''microphone -> [wake gate] -> STT -> intent router -> memory -> Ollama -> TTS -> AvatarAudioTap -> speakers
                                      |                         |              |
                                      +-> AgentBridge ----------+              +-> latest normalized envelope
                                             |       |                                  |
                                             |       +-> main PC or configured remote launcher
                                             +-> loopback lifecycle WebSocket            +-> loopback SSE -> avatar renderer''',
    "architecture runtime diagram",
)
architecture = replace_once(
    architecture,
    '''- `session.py` owns the Pipecat pipeline and exposes thread-safe commands to the
  GUI. The audio loop never calls Tk directly. `send_multimodal_prompt()` adds
  one assembled user message to the LLM context and runs one LLM turn.
''',
    '''- `session.py` owns the Pipecat pipeline and exposes thread-safe commands to the
  GUI. The audio loop never calls Tk directly. `send_multimodal_prompt()` adds
  one assembled user message to the LLM context and runs one LLM turn. The
  optional `AvatarAudioTap` observes TTS PCM after synthesis and before local
  output, publishing only normalized RMS/peak envelopes; it never mutates or
  delays the audio frame.
- `avatar_audio.py` defines the bounded latest-value envelope hub and SSE
  serialization. `WebVoiceApp` owns and closes one hub, while `VoiceSession`
  receives only its `publish` callback. Raw PCM never crosses the web boundary.
- `web_app/avatar/` is a zero-build ES-module runtime. It lazy-loads vendored
  Three.js only when enabled, renders the procedural butler or a safe local GLB,
  and owns expression, gaze, lip-sync, reduced-motion, fallback, and disposal
  behavior.
''',
    "architecture ownership notes",
)
architecture_path.write_text(architecture, encoding="utf-8")

changelog = changelog_path.read_text(encoding="utf-8")
changelog = replace_once(
    changelog,
    "### Added\n\n- Closing the console window",
    '''### Added

- The web Control Center now includes an optional, local-first animated butler
  companion with listening, thinking, speaking, agent-work, completion, and
  error states; natural blinking, gaze, restrained idle motion, and facial
  expressions; amplitude-driven lip-sync for every TTS backend including
  Coqui; persisted quality and motion controls; safe GLB/GLTF loading; and an
  accessible static fallback when Three.js, WebGL, or a model is unavailable.
- Closing the console window''',
    "changelog avatar entry",
)
changelog_path.write_text(changelog, encoding="utf-8")

tests = test_path.read_text(encoding="utf-8")
tests += '''


def test_package_data_covers_nested_avatar_assets():
    project = Path("pyproject.toml").read_text(encoding="utf-8")
    assert '"web_app/**/*"' in project
    for relative in [
        "avatar/avatar-entry.js",
        "avatar/avatar-scene.js",
        "assets/avatars/butler/metadata.json",
        "vendor/three/three.module.min.js",
        "vendor/three/addons/loaders/GLTFLoader.js",
        "vendor/three/addons/utils/BufferGeometryUtils.js",
    ]:
        assert (WEB_APP / relative).is_file(), relative
'''
test_path.write_text(tests, encoding="utf-8")

subprocess.run(["python", "-m", "ruff", "format", str(test_path)], check=True)
js_tests = sorted(str(path) for path in Path("tests/js").glob("*.test.mjs"))
subprocess.run(["node", "--test", *js_tests], check=True)
subprocess.run(
    [
        "python", "-m", "pytest",
        "tests/test_app_state.py", "tests/test_avatar_audio.py",
        "tests/test_session_processors.py", "tests/test_web_gui.py",
        "tests/test_avatar_settings.py",
        "-q", "--disable-warnings", "--maxfail=1",
    ],
    check=True,
)
subprocess.run(
    [
        "python", "-m", "pytest",
        "tests/test_agent_bridge.py", "tests/test_session_controls.py",
        "tests/test_tts_factory.py", "tests/test_coqui_tts.py",
        "tests/test_wake_word.py", "tests/test_web_gui.py",
        "-q", "--disable-warnings", "--maxfail=1",
    ],
    check=True,
)
subprocess.run(["python", "-m", "pip", "install", "-q", "build"], check=True)
shutil.rmtree("dist", ignore_errors=True)
subprocess.run(["python", "-m", "build", "--wheel"], check=True)
wheel = sorted(Path("dist").glob("*.whl"))[-1]
required = [
    "remote_agent_protocol/web_app/avatar/avatar-entry.js",
    "remote_agent_protocol/web_app/avatar/avatar-scene.js",
    "remote_agent_protocol/web_app/assets/avatars/butler/metadata.json",
    "remote_agent_protocol/web_app/vendor/three/three.module.min.js",
    "remote_agent_protocol/web_app/vendor/three/addons/loaders/GLTFLoader.js",
    "remote_agent_protocol/web_app/vendor/three/addons/utils/BufferGeometryUtils.js",
]
with zipfile.ZipFile(wheel) as archive:
    names = set(archive.namelist())
missing = [name for name in required if name not in names]
if missing:
    raise RuntimeError(f"wheel is missing avatar assets: {missing}")
print(f"WHEEL OK: {wheel.name} contains all required avatar assets")

Path(__file__).unlink()
subprocess.run(["git", "add", str(readme_path), str(architecture_path), str(changelog_path), str(test_path), ".github/avatar_tasks/task13.py"], check=True)
subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
subprocess.run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], check=True)
subprocess.run(["git", "commit", "-m", "docs(avatar): document companion operation and verification"], check=True)
subprocess.run(["git", "pull", "--rebase", "origin", "feature/animated-butler-avatar"], check=True)
subprocess.run(["git", "push", "origin", "HEAD:feature/animated-butler-avatar"], check=True)
print("TASK 13 DONE: docs, broader regressions, and wheel asset verification passed")
