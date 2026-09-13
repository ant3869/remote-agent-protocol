"""voice_probe -- a repeatable text-driven test harness for the voice mediator.

Feed a deliberate corpus of text prompts (a stand-in for spoken requests)
through the exact routing/delegation/confirmation brain the voice path uses,
score each decision against a grounded expectation, classify every failure, and
emit a diagnostic report. See ``voice_probe/README.md``.
"""

import os

# The corpus dispatches to "mock" for a safe, deterministic agent (see
# README.md); it's excluded from a live session's cfg.AGENT_BACKENDS by
# default, so this harness -- a debug tool by nature -- opts back in for
# itself rather than requiring every invocation to set the env var by hand.
# Runs before runner/classifiers import config, since Python always executes
# a package's __init__ before its submodules.
os.environ.setdefault("AGENT_MOCK_BACKEND_ENABLED", "1")
