"""
Guard against silent breakage of the mem0 dedup override.

``mem0_setup.create_memory_service`` subclasses the upstream
``Mem0MemoryService`` and overrides two of its *private* methods to stop the
context ballooning with stacked memory blocks. If a pipecat upgrade renames or
removes those methods, the override becomes dead code and the bug returns with
no error. This test fails loudly the moment the surface it depends on changes.

Skipped when mem0 (and its heavy deps) aren't installed.
"""

import unittest

try:
    from pipecat.services.mem0.memory import Mem0MemoryService

    HAVE_MEM0 = True
except Exception:  # pragma: no cover - optional dependency
    HAVE_MEM0 = False


@unittest.skipUnless(HAVE_MEM0, "mem0 service not installed")
class Mem0OverrideGuardTests(unittest.TestCase):
    def test_overridden_methods_still_exist_upstream(self):
        for name in ("_store_messages", "_enhance_context_with_memories"):
            self.assertTrue(
                hasattr(Mem0MemoryService, name),
                f"Mem0MemoryService.{name} vanished -- mem0_setup override is now dead code",
            )

    def test_input_params_has_expected_fields(self):
        params = Mem0MemoryService.InputParams(
            search_limit=5,
            search_threshold=0.1,
            system_prompt="x",
            add_as_system_message=True,
            position=1,
        )
        self.assertEqual(params.search_limit, 5)
        self.assertEqual(params.position, 1)


if __name__ == "__main__":
    unittest.main()
