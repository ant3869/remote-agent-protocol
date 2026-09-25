"""Test-session setup that must run before any test module imports config.

Module level, not a fixture: pytest imports every conftest.py in a directory
before it imports the test modules inside it, which is what makes this run
early enough. A fixture would run after collection has already imported
``remote_agent_protocol.config`` in test modules that do so at their own
top level, by which point ``config.AGENT_BACKENDS`` is already frozen.
"""

import os
import tempfile

# "mock" is disabled in a live session by default (see config.py) so it can
# never silently swallow real work. The test suite is exactly the deliberate
# debug context that flag exists for: mock is the one backend guaranteed
# present without a real CLI installed, and a large share of the suite uses
# it as a safe placeholder for testing unrelated plumbing (persona
# persistence, tool-user validation, voice_probe corpus runs) rather than
# mock's own dispatch behavior.
os.environ.setdefault("AGENT_MOCK_BACKEND_ENABLED", "1")

# Many tests construct a real VoiceSession/BrainSession without mocking its
# AgentConversationHub, which (Task 8) restores/saves a durable JSON store on
# construction and real dispatch. Left at its default path, a test run
# accumulates fictional test conversations in the developer's real data/
# directory -- and a store with pre-existing channels replays a restored
# event synchronously during __init__, which is exactly the scenario that
# surfaced two separate construction-order bugs during Task 8's own
# verification. Sandbox it the same way a real deployment never would.
#
# The path must be unique per test *process*, not just per test: a fixed
# filename under the shared system temp directory persists across separate
# pytest invocations (nothing ever deletes it), so the first run that ever
# creates a channel silently poisons every later run on the same machine --
# including an otherwise fully isolated single-test run -- with a spurious
# CHANNEL_RESTORED replay. A PID-suffixed name keeps every invocation
# hermetic while still being easy to find if one needs inspecting.
os.environ.setdefault(
    "CONVERSATION_STORE_PATH",
    os.path.join(tempfile.gettempdir(), f"rap_test_conversations_{os.getpid()}.json"),
)

# Same reasoning, for the model-provider registry (Phase C0): llm_endpoint's
# role-chain resolution lazily loads and caches one registry per process, so
# an unsandboxed path would let a real data/model_providers.json leak role
# assignments into every test that resolves BRAIN/INTENT/ORCHESTRATION/
# NARRATION without an explicit ``use_registry()`` override.
os.environ.setdefault(
    "MODEL_PROVIDERS_PATH",
    os.path.join(tempfile.gettempdir(), f"rap_test_model_providers_{os.getpid()}.json"),
)
