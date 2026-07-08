"""``python -m remote_agent_protocol`` launches the desktop control panel."""

from remote_agent_protocol import process_guard
from remote_agent_protocol.web_gui import WebVoiceApp

if __name__ == "__main__":
    process_guard.close_previous_instance()
    process_guard.write_lock()
    try:
        WebVoiceApp().run()
    finally:
        process_guard.release_lock()
