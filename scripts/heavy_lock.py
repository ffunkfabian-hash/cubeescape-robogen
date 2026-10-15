#!/usr/bin/env python3
"""One heavy run at a time on this Mac (1.20.3) - the queue shared with Cube Escape 0.59 and Cubegen 0.25.

Heavy steps of all three repositories wait for one lock file: here the full tier (generate_robot.py --tier full),
scripts/check_output.sh and unity/run_testbed.sh; in Cube Escape the full Unity regression and player measurements;
in Cubegen tools/unity_check.py. Two such runs at once left too little memory and slowed both down heavily.

    python3 scripts/heavy_lock.py run [--what LABEL] [--checkout PATH] -- <command …>   wait, run, release
    python3 scripts/heavy_lock.py status                                              who holds it right now

Contract (Cube Escape Tools/heavy_lock.py, Cubegen tools/unity_check.py - change it only together with them):
- an exclusive flock(2) on ~/Library/Caches/CubeEscape/heavy-run.lock, held by the waiting process for the whole heavy
  step; the file is never deleted;
- the holder writes one JSON line {"pid", "what", "checkout", "since"} into it; a waiting run prints it (and again every
  five minutes); Cube Escape's pause_sessions.py spares that pid and its children;
- the lock's file descriptor is never inherited (O_CLOEXEC): a background child (a Blender worker, caffeinate, Unity)
  would otherwise keep the lock after the run;
- CE_HEAVY_LOCK=0 skips the queue; CE_HEAVY_HELD=1 marks the children of a holder, so a heavy step inside another one
  (run_testbed.sh inside check_output.sh, a full run inside Cube Escape's queue) does not wait for its own parent.
CE_HEAVY_LOCK_FILE moves the lock (tests only). How it combines with the pause of other sessions: scripts/quiet.py.

Pure Python (no bpy): generate_robot.py imports it inside Blender.
"""
from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import time

DEFAULT = os.path.join(os.path.expanduser("~"), "Library", "Caches", "CubeEscape", "heavy-run.lock")
POLL_SECONDS = 2.0
REPORT_SECONDS = 300.0      # a waiting run says every five minutes that it still waits
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def lock_path() -> str:
    return os.environ.get("CE_HEAVY_LOCK_FILE") or DEFAULT


def skipped(env=None) -> str | None:
    """Why this process does not queue (None: it does)."""
    env = os.environ if env is None else env
    if env.get("CE_HEAVY_LOCK", "1").strip() == "0":
        return "CE_HEAVY_LOCK=0"
    if env.get("CE_HEAVY_HELD") == "1":
        return "the parent holds the lock (CE_HEAVY_HELD=1)"
    return None


def describe(text: str) -> str:
    """The holder line of the lock file in words (whatever tool wrote it)."""
    try:
        info = json.loads(text)
        return "%s (PID %s, %s, since %s)" % (info.get("what", "?"), info.get("pid", "?"), info.get("checkout", "?"),
                                              info.get("since", "?"))
    except (ValueError, AttributeError):
        return text.strip() or "unknown"


def holder() -> str | None:
    """The current holder in words, or None while nobody holds the lock."""
    try:
        import fcntl
        fd = os.open(lock_path(), os.O_RDONLY | os.O_CLOEXEC)
    except (ImportError, OSError):
        return None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return describe(os.pread(fd, 4096, 0).decode("utf-8", "replace"))
        fcntl.flock(fd, fcntl.LOCK_UN)
        return None
    finally:
        os.close(fd)


class HeavyLock:
    """Waits for and holds the lock: acquire() blocks until it is ours (or skipped), release() frees it - closing the
    file descriptor (also at process exit) frees it too. While held, os.environ has CE_HEAVY_HELD=1 for children.

    state() is one line for reports: "held, waited 12 s" / "skipped (CE_HEAVY_LOCK=0)" / ..."""

    def __init__(self, what: str, checkout: str | None = None, out=None, poll: float = POLL_SECONDS,
                 report: float = REPORT_SECONDS):
        self.what, self.checkout = what, checkout or ROOT
        self.out = out or (lambda line: print(line, flush=True))
        self.poll, self.report = poll, report
        self.fd: int | None = None
        self.skipped: str | None = None
        self.waited = 0.0
        self._previous_held: str | None = None

    @property
    def held(self) -> bool:
        return self.fd is not None

    def acquire(self) -> "HeavyLock":
        if self.fd is not None or self.skipped:
            return self
        self.skipped = skipped()
        try:
            import fcntl
        except ImportError:         # pragma: no cover - no flock (Windows): nothing to queue on
            self.skipped = self.skipped or "no flock on this system"
        if self.skipped:
            return self
        path = lock_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o644)     # never inherited (O_CLOEXEC, PEP 446)
        start = last = time.monotonic()
        announced = False
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    who = describe(os.pread(fd, 4096, 0).decode("utf-8", "replace"))
                    now = time.monotonic()
                    if not announced:
                        self.out("heavy lock: waiting for %s - held by %s (CE_HEAVY_LOCK=0 skips the queue)"
                                 % (path, who))
                        announced = True
                    elif now - last >= self.report:
                        self.out("heavy lock: still waiting after %.0f min for %s" % ((now - start) / 60, who))
                        last = now
                    time.sleep(self.poll)
        except BaseException:
            os.close(fd)
            raise
        self.waited = time.monotonic() - start
        if announced:
            self.out("heavy lock: free after %.0f s" % self.waited)
        info = {"pid": os.getpid(), "what": self.what, "checkout": self.checkout,
                "since": time.strftime("%Y-%m-%d %H:%M:%S")}
        os.ftruncate(fd, 0)
        os.pwrite(fd, (json.dumps(info) + "\n").encode(), 0)
        self.fd = fd
        self._previous_held = os.environ.get("CE_HEAVY_HELD")
        os.environ["CE_HEAVY_HELD"] = "1"
        return self

    def release(self) -> None:
        if self.fd is None:
            return
        if self._previous_held is None:
            os.environ.pop("CE_HEAVY_HELD", None)
        else:
            os.environ["CE_HEAVY_HELD"] = self._previous_held
        try:
            os.ftruncate(self.fd, 0)    # still holding it: nobody reads an old holder afterwards
        finally:
            os.close(self.fd)
            self.fd = None

    def state(self) -> str:
        if self.skipped:
            return "skipped (%s)" % self.skipped
        if self.fd is not None:
            return "held, waited %.0f s" % self.waited
        return "not held"


@contextlib.contextmanager
def held(what: str, checkout: str | None = None, out=None, poll: float = POLL_SECONDS):
    lock = HeavyLock(what, checkout, out, poll).acquire()
    try:
        yield lock
    finally:
        lock.release()


def run_forwarding(argv: list, env=None) -> int:
    """Runs argv and waits for it; TERM/HUP/INT for this process go on to it (a tool's time limit), so the lock this
    process holds is never free while the heavy step still runs."""
    child = subprocess.Popen(argv, env=env)

    def forward(signum, _frame):
        if child.poll() is None:
            child.send_signal(signum)

    previous = {}
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        try:
            previous[sig] = signal.signal(sig, forward)
        except ValueError:          # not the main thread
            pass
    try:
        while True:
            try:
                return child.wait()
            except KeyboardInterrupt:   # forwarded already; keep waiting for the child
                continue
    finally:
        for sig, handler in previous.items():
            if handler is not None:     # None: a handler set outside Python (Blender) - it cannot be put back
                signal.signal(sig, handler)


def main(argv: list) -> int:
    if argv[:1] == ["status"]:
        print(holder() or "free")
        return 0
    if argv[:1] == ["run"]:
        rest, what, checkout = argv[1:], None, None
        while rest[:1] in (["--what"], ["--checkout"]) and len(rest) >= 2:
            if rest[0] == "--what":
                what = rest[1]
            else:
                checkout = rest[1]
            rest = rest[2:]
        if rest[:1] == ["--"]:
            rest = rest[1:]
        if rest:
            with held(what or os.path.basename(rest[0]), checkout) as lock:
                if lock.skipped:
                    print("heavy lock: %s" % lock.state(), flush=True)
                return run_forwarding(rest)
    print(__doc__.strip())
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
