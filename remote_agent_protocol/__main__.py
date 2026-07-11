"""``python -m remote_agent_protocol`` launches the desktop control panel."""

import sys

from remote_agent_protocol import process_guard
from remote_agent_protocol.web_gui import WebVoiceApp

if __name__ == "__main__":
    if not process_guard.acquire_single_instance_lock():
        print("Remote Agent Protocol is already running.")
        sys.exit(1)
    process_guard.close_previous_instance()
    process_guard.write_lock()
    try:
        WebVoiceApp().run()
    finally:
        process_guard.release_lock()
