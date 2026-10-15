"""Quiet machine (scripts/quiet.py): who counts as load, when the pause tool is used."""
import os
import unittest
from unittest import mock

from helpers import tempdir

import quiet

CLAUDE = "/Applications/Claude.app/Contents/Resources/app/claude.app/Contents/MacOS/claude --resume"
CODEX = "node /opt/homebrew/bin/codex"
BLENDER = "/Applications/Blender.app/Contents/MacOS/Blender -b --python scripts/generate_robot.py -- --tier full"
UNITY = "/Applications/Unity/Hub/Editor/6000.6.0f1/Unity.app/Contents/MacOS/Unity -batchmode -projectPath x"


def table():
    """pid -> (ppid, command): our agent 100 runs us (300); agent 500 (Codex) runs Blender 520 and Unity 530;
    the user's own Unity editor 700 and Blender 710 run without an agent; our own Blender worker 310."""
    return {
        1: (0, "launchd"),
        100: (1, CLAUDE), 200: (100, "/bin/zsh -c python3 scripts/testplan.py --run"), 300: (200, "python3 quiet.py"),
        310: (300, BLENDER),
        500: (1, CODEX), 510: (500, "/bin/bash -lc run"), 520: (510, BLENDER), 530: (510, UNITY),
        540: (510, "rg -n foo"),
        700: (1, UNITY.replace("-batchmode ", "")), 710: (1, "/Applications/Blender.app/Contents/MacOS/Blender"),
    }


class Decide(unittest.TestCase):
    def test_only_other_sessions_count(self):
        found = dict(quiet.other_sessions_load(table(), me=300))
        self.assertEqual(sorted(found), [520, 530])      # never the user's Unity/Blender, never ours, no light tools

    def test_own_agent_children_are_ours(self):
        t = table()
        t[320] = (200, BLENDER)                         # another command of our own session
        self.assertNotIn(320, dict(quiet.other_sessions_load(t, me=300)))

    def test_quiet_machine(self):
        t = {k: v for k, v in table().items() if k not in (520, 530)}
        with mock.patch.dict(os.environ, {"ROBOGEN_QUIET_LOAD": "4"}):
            d = quiet.decide(t, load=1.0, me=300)
        self.assertFalse(d["busy"])

    def test_high_load_counts_when_other_sessions_use_the_cpu(self):
        t = {k: v for k, v in table().items() if k not in (520, 530)}
        t[540] = (510, "rg -n foo", 180.0)            # another session's light tool, busy
        with mock.patch.dict(os.environ, {"ROBOGEN_QUIET_LOAD": "4"}):
            d = quiet.decide(t, load=6.5, me=300)
        self.assertTrue(d["busy"])
        self.assertIn("load 6.5", d["reasons"][0])

    def test_own_or_user_load_pauses_nobody(self):
        t = {k: v for k, v in table().items() if k not in (520, 530)}
        t[310] = (300, BLENDER, 900.0)                 # our own previous step / workers
        t[700] = (1, UNITY, 400.0)                     # the user's editor
        with mock.patch.dict(os.environ, {"ROBOGEN_QUIET_LOAD": "4"}):
            self.assertFalse(quiet.decide(t, load=14.0, me=300)["busy"])

    def test_other_pause_run_is_found(self):
        t = table()
        t[550] = (510, "python3 /x/pause_sessions.py run -- make", 0.0)
        self.assertEqual(quiet.other_pause(t, me=300), 550)
        t2 = table()
        t2[250] = (200, "python3 /x/pause_sessions.py run -- blender", 0.0)    # our own wrapper (ancestor)
        t2[300] = (250, "python3 quiet.py")
        self.assertIsNone(quiet.other_pause(t2, me=300))


class Plan(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"ROBOGEN_QUIET_LOAD": "4"})
        self.env.start()
        for k in ("ROBOGEN_QUIET_ACTIVE", "ROBOGEN_NO_PAUSE", "ROBOGEN_PAUSE_TOOL"):
            os.environ.pop(k, None)
        self.addCleanup(self.env.stop)
        tmp = tempdir(self)
        for name, value in (("PAUSE_STATE", os.path.join(tmp, "state.json")),
                            ("QUIET_LOCK", os.path.join(tmp, "quiet.lock"))):
            patcher = mock.patch.object(quiet, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.tool = os.path.join(tmp, "pause_sessions.py")
        with open(self.tool, "w") as fh:
            fh.write("# stand-in\n")

    def busy(self):
        with mock.patch.object(quiet, "other_sessions_load", return_value=[(520, BLENDER)]):
            return quiet.plan(table(), load=1.0)

    def test_busy_uses_the_pause_tool(self):
        os.environ["ROBOGEN_PAUSE_TOOL"] = self.tool
        p = self.busy()
        self.assertEqual(p["mode"], "pause")
        argv, env = quiet.command_for(["scripts/check_output.sh", "output"], p)
        self.assertEqual(argv[1:4], [self.tool, "run", "--"])
        self.assertEqual(argv[-2:], ["scripts/check_output.sh", "output"])
        self.assertEqual(env["ROBOGEN_QUIET_ACTIVE"], "1")
        self.assertTrue(env["ROBOGEN_QUIET_INFO"].startswith("paused=1"))
        p["lock"].close()

    def test_never_two_pauses(self):
        os.environ["ROBOGEN_PAUSE_TOOL"] = self.tool
        first = self.busy()
        self.assertEqual(first["mode"], "pause")                 # holds the generator lock
        second = self.busy()
        self.assertEqual(second["mode"], "direct")
        self.assertIn("another session pauses", second["why"])
        first["lock"].close()
        with open(quiet.PAUSE_STATE, "w") as fh:                 # Cube Escape's tool is pausing right now
            fh.write("[]")
        self.assertEqual(self.busy()["mode"], "direct")

    def test_opt_out(self):
        os.environ["ROBOGEN_PAUSE_TOOL"] = self.tool
        os.environ["ROBOGEN_NO_PAUSE"] = "1"
        p = self.busy()
        self.assertEqual(p["mode"], "direct")
        self.assertIn("ROBOGEN_NO_PAUSE", p["why"])
        argv, env = quiet.command_for(["x"], p)
        self.assertEqual(argv, ["x"])
        self.assertTrue(env["ROBOGEN_QUIET_INFO"].startswith("paused=0"))

    def test_missing_tool_runs_normally_with_a_warning(self):
        os.environ["ROBOGEN_PAUSE_TOOL"] = self.tool + ".missing"
        p = self.busy()
        self.assertEqual(p["mode"], "direct")
        self.assertTrue(p.get("warning"))
        self.assertIn("missing", p["why"])

    def test_nested_heavy_steps_do_not_wrap_again(self):
        os.environ["ROBOGEN_QUIET_ACTIVE"] = "1"
        self.assertEqual(quiet.plan(table(), load=50.0)["mode"], "direct")

    def test_the_real_tool_spares_the_users_programs(self):
        """The Cube Escape tool pauses only processes below another agent (read, not run)."""
        if not os.path.isfile(quiet.PAUSE_TOOL):
            self.skipTest("pause tool not on this machine")
        with open(quiet.PAUSE_TOOL, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("for agent in other_agents(table)", src)
        self.assertIn("descendants(agent, table)", src)
        self.assertIn("SIGCONT", src)


if __name__ == "__main__":
    unittest.main()
