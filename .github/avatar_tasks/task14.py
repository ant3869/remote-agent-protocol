from __future__ import annotations

import base64
import gzip
import shutil
import subprocess
import zipfile
from pathlib import Path

PATCH = "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"

patch = gzip.decompress(base64.b64decode(PATCH))
subprocess.run(["git", "apply", "--whitespace=error-all", "-"], input=patch, check=True)
subprocess.run(["python", "-m", "ruff", "format", "tests/test_web_gui.py"], check=True)
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
    "remote_agent_protocol/web_app/avatar/model-loader.js",
    "remote_agent_protocol/web_app/assets/avatars/butler/metadata.json",
    "remote_agent_protocol/web_app/vendor/three/addons/loaders/GLTFLoader.js",
]
with zipfile.ZipFile(wheel) as archive:
    names = set(archive.namelist())
missing = [name for name in required if name not in names]
if missing:
    raise RuntimeError(f"wheel is missing reviewed avatar assets: {missing}")

Path(__file__).unlink()
subprocess.run(
    [
        "git", "add",
        "docs/superpowers/specs/2026-07-11-animated-butler-avatar-design.md",
        "remote_agent_protocol/web_app/app.js",
        "remote_agent_protocol/web_app/assets/avatars/butler/metadata.json",
        "remote_agent_protocol/web_app/avatar/avatar-controller.js",
        "remote_agent_protocol/web_app/avatar/avatar-entry.js",
        "remote_agent_protocol/web_app/avatar/avatar-scene.js",
        "remote_agent_protocol/web_app/avatar/model-loader.js",
        "remote_agent_protocol/web_app/styles.css",
        "tests/js/avatar-controller.test.mjs", "tests/js/model-loader.test.mjs",
        "tests/test_web_gui.py", ".github/avatar_tasks/task14.py",
    ],
    check=True,
)
subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
subprocess.run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], check=True)
subprocess.run(["git", "commit", "-m", "fix(avatar): close final lifecycle and GLB review gaps"], check=True)
subprocess.run(["git", "pull", "--rebase", "origin", "feature/animated-butler-avatar"], check=True)
subprocess.run(["git", "push", "origin", "HEAD:feature/animated-butler-avatar"], check=True)
print("FINAL REVIEW FIXES DONE: sleeping, scene reload, morphs, skeletal idle, and wheel verification passed")
