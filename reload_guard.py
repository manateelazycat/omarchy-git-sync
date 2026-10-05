"""Briefly suspend Omarchy's plugin watcher while committing a prepared batch.

The watchdog owns pidfds, so a reused PID can never receive a signal. It runs
in a separate session and restores watching even if the updating process dies.
Omarchy itself restarts the watcher one second after it exits.
"""
from __future__ import annotations

import contextlib
import os
from pathlib import Path
import select
import shutil
import signal
import subprocess
import sys
import time


def watcher_pids(plugins_dir):
    expected = ["-m", "-r", "-q", "-e", "close_write,create,delete,move",
                "--format", "%w%f", str(Path(plugins_dir).absolute())]
    found = []
    for process in Path("/proc").iterdir():
        if not process.name.isdigit():
            continue
        try:
            if process.stat().st_uid != os.getuid():
                continue
            args = [os.fsdecode(arg) for arg in (process / "cmdline").read_bytes().split(b"\0") if arg]
            if not args or Path(args[0]).name != "inotifywait" or args[1:] != expected:
                continue
            status = (process / "status").read_text().splitlines()
            if next(line.split()[1] for line in status if line.startswith("State:")) in ("T", "t"):
                continue  # Do not take over a watcher paused by somebody else.
            parent_id = next(line.split()[1] for line in status if line.startswith("PPid:"))
            parent = Path("/proc") / parent_id
            if parent.stat().st_uid != os.getuid() or (parent / "exe").resolve().name not in ("quickshell", "qs"):
                continue
            found.append(int(process.name))
        except (OSError, StopIteration):
            continue
    return found


def reload_plugins():
    if not shutil.which("omarchy-shell"):
        return False
    try:
        result = subprocess.run(["omarchy-shell", "shell", "rescanPlugins"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


class ReloadGuard:
    def __init__(self, plugins_dir, enabled=True, timeout=30):
        self.plugins_dir = plugins_dir
        self.enabled = enabled
        self.timeout = timeout
        self.control = None
        self.helper = None
        self.deadline = 0
        self.mode = "none" if not enabled else "coalesced"
        self.changed = False
        self.reloaded = False

    def __enter__(self):
        if not self.enabled or not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
            return self
        fds = []
        control_read = control_write = ready_read = ready_write = None
        try:
            for pid in watcher_pids(self.plugins_dir):
                try:
                    fd = os.pidfd_open(pid)
                    # Verify identity again after acquiring the stable process handle.
                    if pid not in watcher_pids(self.plugins_dir):
                        os.close(fd)
                        continue
                    fds.append(fd)
                except ProcessLookupError:
                    continue
            if not fds:
                return self
            control_read, control_write = os.pipe()
            ready_read, ready_write = os.pipe()
            self.helper = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "watchdog", str(self.timeout),
                 str(control_read), str(ready_write), *map(str, fds)],
                pass_fds=(control_read, ready_write, *fds), start_new_session=True,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            os.close(control_read)
            control_read = None
            os.close(ready_write)
            ready_write = None
            if not select.select([ready_read], [], [], 3)[0] or os.read(ready_read, 16) != b"ready":
                raise OSError("Watcher suspension unavailable")
            self.control = control_write
            control_write = None
            self.mode = "controlled"
            self.deadline = time.monotonic() + self.timeout - 3
        except OSError:
            # Closing the pipe makes any already-started watchdog restore watching.
            if control_write is not None:
                os.close(control_write)
                control_write = None
            if self.helper:
                self.helper.wait(timeout=self.timeout + 10)
                self.helper = None
        finally:
            for fd in [*fds, control_read, control_write, ready_read, ready_write]:
                if fd is not None:
                    os.close(fd)
        return self

    def ensure_active(self):
        if self.mode == "controlled" and (time.monotonic() >= self.deadline or self.helper.poll() is not None):
            raise RuntimeError("自动重载保护已结束，剩余插件未安装，请重试")

    def __exit__(self, exc_type, exc, traceback):
        if self.control is not None:
            try:
                os.write(self.control, b"reload" if self.changed else b"done")
            except BrokenPipeError:
                pass
            finally:
                os.close(self.control)
                self.control = None
            # The guardian discards queued events, then asks Omarchy to reload once.
            self.reloaded = self.helper.wait(timeout=12) == 0 and self.changed
        elif self.enabled and self.changed:
            # Hosts without the known watcher still get a single explicit reload.
            self.reloaded = reload_plugins()


def watchdog(timeout, control, ready, fds):
    stopped = []
    should_reload = False
    try:
        for fd in fds:
            try:
                signal.pidfd_send_signal(fd, signal.SIGSTOP)
                stopped.append(fd)
            except ProcessLookupError:
                continue
        if not stopped:
            os.write(ready, b"unavailable")
            return 0
        os.write(ready, b"ready")
        os.close(ready)
        ready = None
        if select.select([control], [], [], timeout)[0]:
            command = os.read(control, 16)
            should_reload = command != b"done"  # EOF means the updater died.
        else:
            should_reload = True
    finally:
        # Resume-and-read would replay all queued file events. Exiting the exact
        # watcher instead discards them; Omarchy's existing restart timer restores it.
        for fd in stopped:
            with contextlib.suppress(ProcessLookupError):
                signal.pidfd_send_signal(fd, signal.SIGKILL)
        for fd in [control, ready, *fds]:
            if fd is not None:
                os.close(fd)
    return 0 if not should_reload or reload_plugins() else 1


if __name__ == "__main__":
    if len(sys.argv) >= 6 and sys.argv[1] == "watchdog":
        sys.exit(watchdog(float(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]),
                          [int(fd) for fd in sys.argv[5:]]))
    sys.exit("Internal watcher guardian")
