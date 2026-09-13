"""Test-session setup that must run before any test module imports config.

Module level, not a fixture: pytest imports every conftest.py in a directory
before it imports the test modules inside it, which is what makes this run
early enough. A fixture would run after collection has already imported
``remote_agent_protocol.config`` in test modules that do so at their own
top level, by which point ``config.AGENT_BACKENDS`` is already frozen.
"""

import os

# "mock" is disabled in a live session by default (see config.py) so it can
# never silently swallow real work. The test suite is exactly the deliberate
# debug context that flag exists for: mock is the one backend guaranteed
# present without a real CLI installed, and a large share of the suite uses
# it as a safe placeholder for testing unrelated plumbing (persona
# persistence, tool-user validation, voice_probe corpus runs) rather than
# mock's own dispatch behavior.
os.environ.setdefault("AGENT_MOCK_BACKEND_ENABLED", "1")
