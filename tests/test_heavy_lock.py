"""One heavy run at a time (scripts/heavy_lock.py, 1.20.3): the lock shared with Cube Escape and Cubegen, and how the
heavy steps (quiet.py run, unity/run_testbed.sh) use it."""
import json
import os
import subprocess
import sys
import time
import unittest
from unittest import mock

from helpers import SCRIPTS, tempdir

import heavy_lock
import quiet

ROOT = os.path.dirname(SCRIPTS)
CE_TOOL = "/Users/ffunk/Claude/Cube Escape/Tools/heavy_lock.py"


class LockCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempdir(self)
        self.lock = os.path.join(self.tmp, "sub", "heavy-run.lock")
        env = mock.patch.dict(os.environ, {"CE_HEAVY_LOCK_FILE": self.lock})
        env.start()
        self.addCleanup(env.stop)
        for k in ("CE_HEAVY_LOCK", "CE_HEAVY_HELD", "ROBOGEN_QUIET_ACTIVE", "ROBOGEN_QUIET_INFO"):
            os.environ.pop(k, None)
        self.lines = []

    def env(self, **extra):
        e = dict(os.environ, **extra)
        return e

    def python(self, code, **kw):
        return subprocess.Popen([sys.executable, "-c", code], env=self.env(CE_HEAVY_HELD=""), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, **kw)


class Contract(LockCase):
    def test_holder_line_and_status(self):
        self.assertIsNone(heavy_lock.holder())
        with heavy_lock.held("robogen full", "/some/checkout", out=self.lines.append) as lock:
            self.assertTrue(lock.held)
            with open(self.lock, encoding="utf-8") as fh:
                info = json.loads(fh.readline())
            self.assertEqual(sorted(info), ["checkout", "pid", "since", "what"])
            self.assertEqual((info["pid"], info["what"], info["checkout"]), (os.getpid(), "robogen full",
                                                                             "/some/checkout"))
            self.assertIn("robogen full (PID %d, /some/checkout" % os.getpid(), heavy_lock.holder())
            self.assertEqual(os.environ.get("CE_HEAVY_HELD"), "1")      # children do not wait again
            self.assertEqual(lock.state(), "held, waited 0 s")
        self.assertIsNone(heavy_lock.holder())
        self.assertNotIn("CE_HEAVY_HELD", os.environ)
        self.assertTrue(os.path.exists(self.lock))                       # never deleted
        self.assertEqual(os.path.getsize(self.lock), 0)                  # no stale holder line

    def test_skipped(self):
        self.assertIsNone(heavy_lock.skipped({}))
        self.assertIn("CE_HEAVY_LOCK=0", heavy_lock.skipped({"CE_HEAVY_LOCK": " 0 "}))
        self.assertIn("CE_HEAVY_HELD=1", heavy_lock.skipped({"CE_HEAVY_HELD": "1"}))
        os.environ["CE_HEAVY_LOCK"] = "0"
        with heavy_lock.held("x") as lock:
            self.assertFalse(lock.held)
            self.assertIsNone(heavy_lock.holder())
            self.assertEqual(lock.state(), "skipped (CE_HEAVY_LOCK=0)")

    def test_a_second_run_waits_and_names_the_holder(self):
        code = ("import sys; sys.path.insert(0, %r); import heavy_lock\n"
                "with heavy_lock.held('second', poll=0.05) as l: print('GOT', l.state(), flush=True)" % SCRIPTS)
        with heavy_lock.held("robogen check_output.sh output", out=self.lines.append):
            waiter = self.python(code)
            first = waiter.stdout.readline()
            self.assertIn("waiting", first)
            self.assertIn("robogen check_output.sh output (PID %d" % os.getpid(), first)
            time.sleep(0.3)
            self.assertIsNone(waiter.poll())                              # still waiting
        out, _ = waiter.communicate(timeout=20)
        self.assertIn("free after", out)
        self.assertIn("GOT held", out)

    def test_the_lock_is_not_inherited(self):
        """A background child of the holder (close_fds=False on purpose) must not keep the lock after the run."""
        lock = heavy_lock.HeavyLock("x", out=self.lines.append).acquire()
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], close_fds=False)
        self.addCleanup(child.wait)
        self.addCleanup(child.kill)
        lock.release()
        self.assertIsNone(heavy_lock.holder())
        again = heavy_lock.HeavyLock("y", out=self.lines.append, poll=0.05)
        start = time.monotonic()
        again.acquire()
        self.assertLess(time.monotonic() - start, 1.0)
        self.assertEqual(self.lines, [])                                   # never had to wait
        again.release()

    def test_cli_run_passes_env_and_exit_code(self):
        proc = subprocess.run([sys.executable, os.path.join(SCRIPTS, "heavy_lock.py"), "run", "--what", "probe", "--",
                               sys.executable, "-c", "import os, sys; print(os.environ.get('CE_HEAVY_HELD')); "
                               "print(open(os.environ['CE_HEAVY_LOCK_FILE']).read().strip()); sys.exit(3)"],
                              env=self.env(), capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 3)
        held, line = proc.stdout.splitlines()[:2]
        self.assertEqual(held, "1")
        self.assertEqual(json.loads(line)["what"], "probe")
        self.assertIsNone(heavy_lock.holder())

    def test_forwarding_survives_handlers_set_outside_python(self):
        """Inside Blender signal.signal() returns None for handlers set in C - restoring them must not fail."""
        real = heavy_lock.signal.signal
        seen = []

        def fake(sig, handler):
            seen.append(sig)
            real(sig, handler)
            return None

        with mock.patch.object(heavy_lock.signal, "signal", side_effect=fake):
            self.assertEqual(heavy_lock.run_forwarding([sys.executable, "-c", "raise SystemExit(4)"]), 4)
        self.assertEqual(len(seen), 3)                   # installed, nothing put back (there is nothing to put back)
        sig = heavy_lock.signal
        for number, handler in ((sig.SIGTERM, sig.SIG_DFL), (sig.SIGHUP, sig.SIG_DFL),
                                (sig.SIGINT, sig.default_int_handler)):
            real(number, handler)

    def test_opt_out_runs_while_another_holds_it(self):
        with heavy_lock.held("other", out=self.lines.append):
            proc = subprocess.run([sys.executable, os.path.join(SCRIPTS, "heavy_lock.py"), "run", "--", "true"],
                                  env=self.env(CE_HEAVY_LOCK="0", CE_HEAVY_HELD=""), capture_output=True, text=True,
                                  timeout=10)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("skipped (CE_HEAVY_LOCK=0)", proc.stdout)

    def test_cube_escape_reads_our_holder_line(self):
        """Same file, same JSON line: Cube Escape's tool (if on this machine) sees our run as the holder."""
        if not os.path.isfile(CE_TOOL):
            self.skipTest("Cube Escape's heavy_lock.py not on this machine")
        with heavy_lock.held("robogen full", "/x", out=self.lines.append):
            out = subprocess.run([sys.executable, CE_TOOL, "status"], env=self.env(), capture_output=True,
                                 text=True, timeout=20).stdout
        self.assertIn("robogen full (PID %d, /x" % os.getpid(), out)


class Quiet(LockCase):
    def test_lock_first_then_only_working_processes_count(self):
        claude = "/Applications/Claude.app/Contents/Resources/app/claude.app/Contents/MacOS/claude"
        blender = "/Applications/Blender.app/Contents/MacOS/Blender -b --python scripts/generate_robot.py"
        t = {1: (0, "launchd", 0.0), 100: (1, claude, 0.0), 300: (100, "python3 quiet.py", 0.0),
             500: (1, "node /opt/homebrew/bin/codex", 0.0), 520: (500, blender, 0.0)}     # waits at the lock
        with mock.patch.dict(os.environ, {"ROBOGEN_QUIET_LOAD": "4"}):
            self.assertTrue(quiet.decide(t, load=1.0, me=300)["busy"])                   # without the lock: busy
            self.assertFalse(quiet.decide(t, load=1.0, me=300, lock_held=True)["busy"])  # it only waits
            t[520] = (500, blender, 350.0)                                              # works: not queued
            self.assertTrue(quiet.decide(t, load=1.0, me=300, lock_held=True)["busy"])

    def test_run_holds_the_lock_and_reports_it(self):
        logged = []
        out = os.path.join(self.tmp, "out.txt")
        code = ("import os; open(%r, 'w').write(os.environ.get('CE_HEAVY_HELD', '') + '|' + "
                "os.environ['ROBOGEN_QUIET_INFO'])" % out)
        with mock.patch.dict(os.environ, {"ROBOGEN_NO_PAUSE": "1"}), \
                mock.patch.object(quiet, "processes", return_value={}):
            rc = quiet.run([sys.executable, "-c", code], log=logged.append, what="check_output.sh output")
        self.assertEqual(rc, 0)
        with open(out, encoding="utf-8") as fh:
            held, info = fh.read().split("|")
        self.assertEqual(held, "1")
        self.assertIn("heavy lock: held", info)
        self.assertIn("heavy lock: held, waited 0 s", logged[-1])
        self.assertIsNone(heavy_lock.holder())                                         # released afterwards
        self.assertNotIn("CE_HEAVY_HELD", os.environ)

    def test_child_sees_lock_and_info(self):
        proc = subprocess.run([sys.executable, os.path.join(SCRIPTS, "quiet.py"), "run", "--what", "probe", "--",
                               sys.executable, "-c", "import os; print(os.environ.get('CE_HEAVY_HELD')); "
                               "print(os.environ['ROBOGEN_QUIET_INFO']); "
                               "print(open(os.environ['CE_HEAVY_LOCK_FILE']).read().strip())"],
                              env=self.env(ROBOGEN_NO_PAUSE="1"), capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        lines = proc.stdout.splitlines()
        self.assertEqual(lines[-3], "1")
        self.assertIn("heavy lock: held", lines[-2])
        self.assertEqual(json.loads(lines[-1])["what"], "robogen probe")

    def test_nested_quiet_run_does_not_queue_again(self):
        with heavy_lock.held("outer", out=self.lines.append):
            os.environ["ROBOGEN_QUIET_ACTIVE"] = "1"
            rc = quiet.run([sys.executable, "-c", "pass"], log=self.lines.append)
        self.assertEqual(rc, 0)
        self.assertEqual(self.lines, [])


class Testbed(LockCase):
    """unity/run_testbed.sh queues on the lock unless a parent holds it (check_output.sh) - Unity itself is missing
    here on purpose (UNITY_EDITOR), so the script stops with exit 2 right after the queue."""

    def start(self, **extra):
        env = self.env(UNITY_EDITOR=os.path.join(self.tmp, "no-unity"), **extra)
        return subprocess.Popen(["bash", os.path.join(ROOT, "unity", "run_testbed.sh"), self.tmp], env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def test_waits_for_the_lock(self):
        with heavy_lock.held("robogen full", out=self.lines.append):
            proc = self.start(CE_HEAVY_HELD="")    # another session's run: not a child of this holder
            self.assertIn("waiting", proc.stdout.readline())
        out, _ = proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("not found", out)

    def test_inside_a_holder_it_does_not_wait(self):
        with heavy_lock.held("robogen check_output.sh", out=self.lines.append):
            proc = self.start()                    # inherits CE_HEAVY_HELD=1 from this holder
            out, _ = proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 2)
        self.assertNotIn("waiting", out)


if __name__ == "__main__":
    unittest.main()
