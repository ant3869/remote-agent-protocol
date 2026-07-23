import io
import sqlite3
import tempfile
import unittest
import wave
from pathlib import Path

from remote_agent_protocol import voicebox


class VoiceboxTests(unittest.TestCase):
    def test_load_profiles_from_db_includes_cloned_and_preset(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "voicebox.db"
            con = sqlite3.connect(db)
            con.execute(
                "create table profiles (id text, name text, description text, language text, "
                "voice_type text, preset_engine text, preset_voice_id text, default_engine text, personality text)"
            )
            con.execute(
                "insert into profiles values (?,?,?,?,?,?,?,?,?)",
                (
                    "p1",
                    "My Clone",
                    "desc",
                    "en",
                    "cloned",
                    None,
                    None,
                    "qwen_custom_voice",
                    "sassy",
                ),
            )
            con.execute(
                "insert into profiles values (?,?,?,?,?,?,?,?,?)",
                ("p2", "Bella", "", "en", "preset", "kokoro", "af_bella", "kokoro", None),
            )
            con.commit()
            con.close()

            profiles = voicebox.load_profiles(db)

            self.assertEqual([p.id for p in profiles], ["p1", "p2"])
            self.assertEqual(profiles[0].label, "Voicebox cloned - My Clone")
            self.assertEqual(profiles[1].voice_ref, "voicebox:p2")

    def test_voice_ref_helpers(self):
        self.assertTrue(voicebox.is_voicebox_ref("voicebox:abc"))
        self.assertEqual(voicebox.profile_id_from_ref("voicebox:abc"), "abc")
        self.assertEqual(voicebox.profile_id_from_ref("af_heart"), "")

    def test_backend_for_voice_infers_voicebox_refs(self):
        self.assertEqual(voicebox.backend_for_voice("voicebox:abc", "kokoro"), "voicebox")
        self.assertEqual(voicebox.backend_for_voice("af_heart", "kokoro"), "kokoro")

    def test_wav_bytes_to_pcm_extracts_audio_and_rate(self):
        raw = b"\x01\x00\x02\x00" * 10
        wav_data = _wav_bytes(raw)

        pcm, rate = voicebox.wav_bytes_to_pcm(wav_data)

        self.assertEqual(pcm, raw)
        self.assertEqual(rate, 24000)

    def test_wav_stream_parser_handles_split_chunks(self):
        raw = b"\x01\x00\x02\x00" * 20
        parser = voicebox.WavStreamParser()
        chunks = []
        wav_data = _wav_bytes(raw)

        for index in range(0, len(wav_data), 7):
            chunks.extend(parser.feed(wav_data[index : index + 7]))
        chunks.extend(parser.finish())

        self.assertEqual(b"".join(chunk for chunk, _rate in chunks), raw)
        self.assertTrue(all(rate == 24000 for _chunk, rate in chunks))

    def test_stop_server_terminates_process_started_by_app(self):
        proc = FakeProcess()
        voicebox._SERVER_PROC = proc

        voicebox.stop_server()

        self.assertTrue(proc.terminated)
        self.assertFalse(proc.killed)
        self.assertIsNone(voicebox._SERVER_PROC)

    def test_stop_server_escalates_to_kill_when_process_hangs(self):
        proc = FakeProcess(hang=True)
        voicebox._SERVER_PROC = proc

        voicebox.stop_server(timeout=0.01)

        self.assertTrue(proc.terminated)
        self.assertTrue(proc.killed)
        self.assertIsNone(voicebox._SERVER_PROC)


def _wav_bytes(raw: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(raw)
    return buf.getvalue()


class FakeProcess:
    def __init__(self, *, hang: bool = False):
        self.hang = hang
        self.terminated = False
        self.killed = False

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True
        self.hang = False

    def wait(self, timeout=None):
        if self.hang:
            raise voicebox.subprocess.TimeoutExpired("voicebox", timeout)
        return 0


if __name__ == "__main__":
    unittest.main()
