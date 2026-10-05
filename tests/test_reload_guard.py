import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reload_guard import ReloadGuard


class ReloadGuardTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        binary = self.root / "bin"
        binary.mkdir()
        self.log = self.root / "reload.log"
        stub = binary / "omarchy-shell"
        stub.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$OMARCHY_GIT_SYNC_GUARD_TEST_LOG"\n')
        stub.chmod(0o755)
        env = patch.dict(os.environ, {
            "PATH": str(binary) + os.pathsep + os.environ["PATH"],
            "OMARCHY_GIT_SYNC_GUARD_TEST_LOG": str(self.log),
        })
        env.start()
        self.addCleanup(env.stop)

    def process(self):
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        def cleanup():
            if process.poll() is None:
                process.kill()
            process.wait(timeout=3)
        self.addCleanup(cleanup)
        return process

    def test_controlled_guard_stops_exact_process_and_reloads_once(self):
        watcher = self.process()
        with patch("reload_guard.watcher_pids", return_value=[watcher.pid]):
            with ReloadGuard(self.root) as guard:
                self.assertEqual(guard.mode, "controlled")
                state = (Path('/proc') / str(watcher.pid) / 'status').read_text()
                self.assertIn('State:\tT', state)
                guard.ensure_active()
                guard.changed = True
            self.assertTrue(guard.reloaded)
        self.assertEqual(watcher.wait(timeout=3), -signal.SIGKILL)
        self.assertEqual(self.log.read_text().splitlines(), ["shell rescanPlugins"])

    def test_no_changes_do_not_reload_and_unrelated_process_is_untouched(self):
        unrelated = self.process()
        with patch("reload_guard.watcher_pids", return_value=[]):
            with ReloadGuard(self.root) as guard:
                self.assertEqual(guard.mode, "coalesced")
        self.assertIsNone(unrelated.poll())
        self.assertFalse(self.log.exists())

    def test_disabled_guard_does_not_signal_or_reload(self):
        with patch("reload_guard.watcher_pids") as discover:
            with ReloadGuard(self.root, enabled=False) as guard:
                guard.changed = True
            discover.assert_not_called()
        self.assertFalse(self.log.exists())

    def test_unknown_watcher_fallback_reloads_once_without_touching_other_processes(self):
        unrelated = self.process()
        with patch("reload_guard.watcher_pids", return_value=[]):
            with ReloadGuard(self.root) as guard:
                guard.changed = True
        self.assertEqual(guard.mode, "coalesced")
        self.assertTrue(guard.reloaded)
        self.assertIsNone(unrelated.poll())
        self.assertEqual(self.log.read_text().splitlines(), ["shell rescanPlugins"])

    def test_timeout_restores_watchability_and_prevents_further_installs(self):
        watcher = self.process()
        with patch("reload_guard.watcher_pids", return_value=[watcher.pid]):
            with ReloadGuard(self.root, timeout=1) as guard:
                self.assertEqual(watcher.wait(timeout=3), -signal.SIGKILL)
                with self.assertRaises(RuntimeError):
                    guard.ensure_active()
        self.assertEqual(self.log.read_text().splitlines(), ["shell rescanPlugins"])

    def test_killed_updater_cannot_leave_a_watcher_stopped(self):
        watcher = self.process()
        ready = self.root / "ready"
        code = '''import sys,time
from pathlib import Path
from unittest.mock import patch
from reload_guard import ReloadGuard
with patch("reload_guard.watcher_pids", return_value=[int(sys.argv[1])]):
    with ReloadGuard(Path(sys.argv[2]), timeout=3) as guard:
        guard.changed = True
        Path(sys.argv[3]).touch()
        time.sleep(30)
'''
        worker = subprocess.Popen([sys.executable, "-c", code, str(watcher.pid), str(self.root), str(ready)],
                                  cwd=Path(__file__).resolve().parents[1], start_new_session=True)
        self.addCleanup(lambda: worker.kill() if worker.poll() is None else None)
        deadline = time.monotonic() + 4
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(ready.exists())
        worker.kill()
        worker.wait(timeout=3)
        self.assertEqual(watcher.wait(timeout=4), -signal.SIGKILL)
        deadline = time.monotonic() + 2
        while not self.log.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(self.log.read_text().splitlines(), ["shell rescanPlugins"])


if __name__ == "__main__":
    unittest.main()
