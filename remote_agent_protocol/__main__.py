"""``python -m remote_agent_protocol`` launches the desktop control panel."""

from remote_agent_protocol import process_guard
from remote_agent_protocol.gui import VoiceGUI

if __name__ == "__main__":
    process_guard.close_previous_instance()
    process_guard.write_lock()
    try:
        VoiceGUI().run()
    finally:
        process_guard.release_lock()
