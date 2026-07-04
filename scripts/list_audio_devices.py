"""
List all audio devices PyAudio can see, so you can pin Jess to the right mic.

Run:  .venv\\Scripts\\python list_audio_devices.py

If Jess can't hear you, find your real microphone in the INPUT list below, note
its index, and set MIC_DEVICE_INDEX = <that number> in config.py. Same idea for
SPEAKER_DEVICE_INDEX if the wrong speaker is used.
"""

import pyaudio

pa = pyaudio.PyAudio()

try:
    default_in = pa.get_default_input_device_info().get("index")
except Exception:
    default_in = None
try:
    default_out = pa.get_default_output_device_info().get("index")
except Exception:
    default_out = None

print("\n=== INPUT devices (microphones) ===")
for i in range(pa.get_device_count()):
    info = pa.get_device_info_by_index(i)
    if info.get("maxInputChannels", 0) > 0:
        tag = "  <-- DEFAULT" if i == default_in else ""
        print(f"  [{i:2}] {info['name']}  ({int(info['maxInputChannels'])} ch){tag}")

print("\n=== OUTPUT devices (speakers) ===")
for i in range(pa.get_device_count()):
    info = pa.get_device_info_by_index(i)
    if info.get("maxOutputChannels", 0) > 0:
        tag = "  <-- DEFAULT" if i == default_out else ""
        print(f"  [{i:2}] {info['name']}  ({int(info['maxOutputChannels'])} ch){tag}")

print("\nSet MIC_DEVICE_INDEX / SPEAKER_DEVICE_INDEX in config.py to override.")
pa.terminate()
