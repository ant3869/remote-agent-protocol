import tempfile
import unittest
from pathlib import Path

from remote_agent_protocol import memory


class MemoryFileTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.path = Path(self._dir.name) / "memory.json"

    def tearDown(self):
        self._dir.cleanup()

    def test_roundtrip_preserves_messages(self):
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hey you"},
        ]

        memory.save_memory(self.path, messages)

        self.assertEqual(memory.load_memory(self.path, 10), messages)

    def test_save_leaves_no_temp_file_behind(self):
        memory.save_memory(self.path, [{"role": "user", "content": "hi"}])

        leftovers = list(self.path.parent.glob("*.tmp"))
        self.assertEqual(leftovers, [])

    def test_load_trims_to_last_max_messages(self):
        messages = [{"role": "user", "content": str(i)} for i in range(10)]
        memory.save_memory(self.path, messages)

        loaded = memory.load_memory(self.path, 3)

        self.assertEqual([m["content"] for m in loaded], ["7", "8", "9"])

    def test_missing_file_starts_fresh(self):
        self.assertEqual(memory.load_memory(self.path, 10), [])

    def test_corrupt_file_starts_fresh(self):
        self.path.write_text("{ not json", encoding="utf-8")

        self.assertEqual(memory.load_memory(self.path, 10), [])

    def test_non_list_payload_is_ignored(self):
        self.path.write_text('{"role": "user"}', encoding="utf-8")

        self.assertEqual(memory.load_memory(self.path, 10), [])


if __name__ == "__main__":
    unittest.main()
