import asyncio
import json
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

from remote_agent_protocol import config as cfg
from remote_agent_protocol import openai_bridge


class OpenAIBridgePayloadTests(unittest.TestCase):
    def test_latest_user_text_reads_plain_content(self):
        payload = {
            "messages": [
                {"role": "system", "content": "ignore"},
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "reply"},
                {"role": "user", "content": "latest"},
            ]
        }

        self.assertEqual(openai_bridge._latest_user_text(payload), "latest")

    def test_latest_user_text_reads_multimodal_text_parts(self):
        payload = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "hello"},
                        {"type": "image_url", "image_url": {"url": "ignored"}},
                        {"type": "text", "text": "world"},
                    ],
                }
            ]
        }

        self.assertEqual(openai_bridge._latest_user_text(payload), "hello\nworld")

    def test_chat_completion_shape(self):
        payload = openai_bridge._chat_completion("hi", "remote-agent-protocol")

        self.assertEqual(payload["object"], "chat.completion")
        self.assertEqual(payload["choices"][0]["message"]["content"], "hi")
        self.assertEqual(payload["choices"][0]["finish_reason"], "stop")

    def test_runtime_stream_yields_before_the_brain_finishes(self):
        release_tail = threading.Event()

        class FakeBrain:
            async def complete_stream(self, text):
                assert text == "hello"
                yield "First sentence."
                while not release_tail.is_set():
                    await asyncio.sleep(0.01)
                yield "Second sentence."

        runtime = openai_bridge.BrainBridgeRuntime.__new__(
            openai_bridge.BrainBridgeRuntime
        )
        runtime.loop = asyncio.new_event_loop()
        runtime.brain = FakeBrain()
        runtime._thread = threading.Thread(target=runtime._run_loop, daemon=True)
        runtime._thread.start()
        try:
            pieces = runtime.stream("hello", timeout=1)
            self.assertEqual(next(pieces), "First sentence.")
            self.assertFalse(release_tail.is_set())
            release_tail.set()
            self.assertEqual(list(pieces), ["Second sentence."])
        finally:
            runtime.loop.call_soon_threadsafe(runtime.loop.stop)
            runtime._thread.join(timeout=1)

    def test_streaming_http_request_never_calls_blocking_complete(self):
        class FakeRuntime:
            def complete(self, _text):
                raise AssertionError("streaming request called blocking complete")

            def stream(self, text):
                self.streamed_text = text
                return iter(["First sentence.", "Second sentence."])

        runtime = FakeRuntime()
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0), openai_bridge._handler_class(runtime)
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            body = json.dumps(
                {
                    "model": "remote-agent-protocol",
                    "stream": True,
                    "messages": [{"role": "user", "content": "hello"}],
                }
            ).encode()
            request = urllib.request.Request(
                f"http://127.0.0.1:{server.server_address[1]}/v1/chat/completions",
                data=body,
                headers={
                    "Authorization": f"Bearer {cfg.S2S_BRIDGE_API_KEY}",
                    "Content-Type": "application/json",
                },
            )
            response = urllib.request.urlopen(request, timeout=2)
            stream = response.read().decode()
        finally:
            server.shutdown()
            server.server_close()

        self.assertEqual(runtime.streamed_text, "hello")
        self.assertIn('"content": "First sentence."', stream)
        self.assertIn('"content": "Second sentence."', stream)
        self.assertTrue(stream.endswith("data: [DONE]\n\n"))

    def test_headless_stream_handler_disables_nagle(self):
        handler = openai_bridge._handler_class(object())

        self.assertTrue(handler.disable_nagle_algorithm)

    def test_stream_chunk_shape(self):
        payload = openai_bridge._stream_chunk("chunk-id", "model", 123, {"content": "hi"})

        self.assertEqual(payload["object"], "chat.completion.chunk")
        self.assertEqual(payload["choices"][0]["delta"], {"content": "hi"})
        self.assertIsNone(payload["choices"][0]["finish_reason"])


if __name__ == "__main__":
    unittest.main()
