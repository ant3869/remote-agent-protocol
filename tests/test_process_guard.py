import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from remote_agent_protocol import process_guard


def _win32():
    return patch.object(process_guard.sys, "platform", "win32")


def _missing_path():
    path = process_guard.cfg.DATA_DIR / "test_jess_never_created.pid"
    if path.exists():
        path.unlink()
    return path


def _write(content: str):
    path = process_guard.cfg.DATA_DIR / "test_jess_lock.pid"
    path.write_text(content)
    return path


def _completed(stdout: str = ""):
    return SimpleNamespace(stdout=stdout, returncode=0)


class CloseInstanceTests(unittest.TestCase):
    def test_no_op_when_lock_file_is_missing(self) -> None:
        with _win32(), patch("subprocess.run") as run:
            process_guard.close_previous_instance(lock_file=_missing_path())
        run.assert_not_called()

    def test_no_op_when_lock_file_has_garbage(self) -> None:
        lock_file = _write("not-a-pid")
        with _win32(), patch("subprocess.run") as run:
            process_guard.close_previous_instance(lock_file=lock_file)
        run.assert_not_called()

    def test_no_op_on_non_windows_platforms(self) -> None:
        lock_file = _write("12345")
        with patch.object(process_guard.sys, "platform", "linux"), patch("subprocess.run") as run:
            process_guard.close_previous_instance(lock_file=lock_file)
        run.assert_not_called()

    def test_skips_a_pid_whose_command_line_does_not_match(self) -> None:
        # Guards against PID reuse: some unrelated process now has that PID.
        lock_file = _write("4242")
        with _win32(), patch("subprocess.run", return_value=_completed("notepad.exe\n")) as run:
            process_guard.close_previous_instance(lock_file=lock_file)
        self.assertEqual(run.call_count, 1)  # only the CIM query, never taskkill

    def test_kills_the_process_tree_when_identity_matches(self) -> None:
        lock_file = _write("4242")
        cim_output = _completed("python.exe -u -m remote_agent_protocol\n")
        with _win32(), patch("subprocess.run", side_effect=[cim_output, _completed()]) as run:
            process_guard.close_previous_instance(lock_file=lock_file)
        self.assertEqual(run.call_count, 2)
        kill_args = run.call_args_list[1].args[0]
        self.assertEqual(kill_args, ["taskkill", "/PID", "4242", "/T", "/F"])

    def test_kill_failure_does_not_raise(self) -> None:
        lock_file = _write("4242")
        cim_output = _completed("python.exe -m remote_agent_protocol.terminal\n")
        with _win32(), patch("subprocess.run", side_effect=[cim_output, OSError("boom")]):
            process_guard.close_previous_instance(lock_file=lock_file)  # must not raise


class SingleInstanceLockTests(unittest.TestCase):
    def test_acquires_lock_when_no_other_instance_holds_it(self) -> None:
        process_guard._mutex_handle = None
        kernel32 = SimpleNamespace(
            CreateMutexW=lambda *_a: 42,
            GetLastError=lambda: 0,
            CloseHandle=lambda _h: None,
        )
        with _win32(), patch.object(process_guard.ctypes, "windll", SimpleNamespace(kernel32=kernel32)):
            self.assertTrue(process_guard.acquire_single_instance_lock("test-mutex"))
        self.assertEqual(process_guard._mutex_handle, 42)
        process_guard._mutex_handle = None

    def test_refuses_when_another_instance_already_holds_it(self) -> None:
        process_guard._mutex_handle = None
        closed: list[int] = []
        kernel32 = SimpleNamespace(
            CreateMutexW=lambda *_a: 42,
            GetLastError=lambda: process_guard._ERROR_ALREADY_EXISTS,
            CloseHandle=closed.append,
        )
        with _win32(), patch.object(process_guard.ctypes, "windll", SimpleNamespace(kernel32=kernel32)):
            self.assertFalse(process_guard.acquire_single_instance_lock("test-mutex"))
        self.assertIsNone(process_guard._mutex_handle)
        self.assertEqual(closed, [42])

    def test_no_op_on_non_windows_platforms(self) -> None:
        with patch.object(process_guard.sys, "platform", "linux"):
            self.assertTrue(process_guard.acquire_single_instance_lock("test-mutex"))


class LockFileTests(unittest.TestCase):
    def test_write_lock_records_our_own_pid(self) -> None:
        lock_file = _missing_path()
        process_guard.write_lock(lock_file=lock_file)
        self.assertEqual(int(lock_file.read_text()), os.getpid())
        lock_file.unlink()

    def test_release_lock_removes_the_file(self) -> None:
        lock_file = _write(str(os.getpid()))
        process_guard.release_lock(lock_file=lock_file)
        self.assertFalse(lock_file.exists())

    def test_release_lock_does_not_remove_another_process_lock(self) -> None:
        lock_file = _write("999")
        process_guard.release_lock(lock_file=lock_file)
        self.assertTrue(lock_file.exists())
        lock_file.unlink()

    def test_close_previous_instance_does_not_kill_current_process(self) -> None:
        lock_file = _write(str(os.getpid()))
        with _win32(), patch("subprocess.run") as run:
            process_guard.close_previous_instance(lock_file=lock_file)
        run.assert_not_called()
        lock_file.unlink()

    def test_release_lock_is_a_no_op_when_already_gone(self) -> None:
        process_guard.release_lock(lock_file=_missing_path())  # must not raise


if __name__ == "__main__":
    unittest.main()
