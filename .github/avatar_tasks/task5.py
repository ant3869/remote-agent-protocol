from __future__ import annotations

import io
import json
import subprocess
import tarfile
import urllib.request
from pathlib import Path

root = Path("remote_agent_protocol/web_app/vendor/three")
url = "https://registry.npmjs.org/three/-/three-0.180.0.tgz"
data = urllib.request.urlopen(url, timeout=60).read()
with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
    files = {
        "package/LICENSE": "LICENSE",
        "package/build/three.module.min.js": "three.module.min.js",
        "package/examples/jsm/loaders/GLTFLoader.js": "addons/loaders/GLTFLoader.js",
        "package/examples/jsm/utils/BufferGeometryUtils.js": "addons/utils/BufferGeometryUtils.js",
    }
    for source, destination in files.items():
        target = root / destination
        target.parent.mkdir(parents=True, exist_ok=True)
        member = archive.extractfile(source)
        if member is None:
            raise RuntimeError(f"missing {source} in official Three.js tarball")
        target.write_bytes(member.read())
(root / "VERSION").write_text("0.180.0\n", encoding="utf-8")

loader = (root / "addons/loaders/GLTFLoader.js").read_text(encoding="utf-8")
if "../utils/BufferGeometryUtils.js" not in loader:
    raise RuntimeError("GLTFLoader no longer imports the expected BufferGeometryUtils path")

metadata_path = Path("remote_agent_protocol/web_app/assets/avatars/butler/metadata.json")
metadata_path.parent.mkdir(parents=True, exist_ok=True)
metadata_path.write_text(
    json.dumps(
        {
            "id": "butler",
            "label": "Butler",
            "model": None,
            "fallback": "procedural-butler",
            "scale": 1.0,
            "cameraTarget": [0, 1.55, 0],
            "controls": {
                "jaw": ["jawOpen", "JawOpen"],
                "blinkLeft": ["eyeBlinkLeft", "Blink_L"],
                "blinkRight": ["eyeBlinkRight", "Blink_R"],
            },
        },
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)

project_path = Path("pyproject.toml")
project = project_path.read_text(encoding="utf-8")
old = '''"remote_agent_protocol" = [
    "web_app/app.js",
    "web_app/index.html",
    "web_app/styles.css",
]'''
new = '''"remote_agent_protocol" = [
    "web_app/**/*",
]'''
if project.count(old) != 1:
    raise RuntimeError("expected the existing three-file package-data declaration")
project_path.write_text(project.replace(old, new, 1), encoding="utf-8")

test_path = Path("tests/test_web_gui.py")
tests = test_path.read_text(encoding="utf-8")
tests += '''


def test_avatar_vendor_and_metadata_files_are_declared():
    metadata = json.loads(
        (WEB_APP / "assets/avatars/butler/metadata.json").read_text(encoding="utf-8")
    )
    version = (WEB_APP / "vendor/three/VERSION").read_text(encoding="utf-8").strip()
    project = Path("pyproject.toml").read_text(encoding="utf-8")

    assert metadata["id"] == "butler"
    assert metadata["model"] is None
    assert metadata["fallback"] == "procedural-butler"
    assert version == "0.180.0"
    assert '"web_app/**/*"' in project
    assert (WEB_APP / "vendor/three/LICENSE").is_file()
    assert (WEB_APP / "vendor/three/three.module.min.js").is_file()
    assert (WEB_APP / "vendor/three/addons/loaders/GLTFLoader.js").is_file()
    assert (WEB_APP / "vendor/three/addons/utils/BufferGeometryUtils.js").is_file()
'''
test_path.write_text(tests, encoding="utf-8")

subprocess.run(["python", "-m", "ruff", "format", str(test_path)], check=True)
subprocess.run(
    [
        "python", "-m", "pytest",
        "tests/test_web_gui.py::test_avatar_vendor_and_metadata_files_are_declared",
        "-q", "--disable-warnings",
    ],
    check=True,
)
Path(__file__).unlink()
subprocess.run(
    [
        "git", "add", "pyproject.toml", "remote_agent_protocol/web_app/vendor",
        "remote_agent_protocol/web_app/assets", "tests/test_web_gui.py",
        ".github/avatar_tasks/task5.py",
    ],
    check=True,
)
subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
subprocess.run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], check=True)
subprocess.run(["git", "commit", "-m", "build(avatar): vendor Three.js and butler metadata"], check=True)
subprocess.run(["git", "push", "origin", "HEAD:feature/animated-butler-avatar"], check=True)
print("TASK 5 DONE: official Three.js r180 assets and metadata committed")
