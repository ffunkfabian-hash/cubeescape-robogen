"""Verification plan (scripts/testplan.py): example diffs and the steps AGENTS.md › Prüfablauf asks for."""
import unittest

import helpers  # noqa: F401 - scripts on sys.path

import testplan


def plan(*files, path=None):
    return testplan.make_plan(files, path)


class Areas(unittest.TestCase):
    def test_every_rule_names_a_known_area(self):
        for pattern, area in testplan.RULES:
            self.assertIn(area, testplan.AREAS, pattern)
        for area, (path, extras, _) in testplan.AREAS.items():
            self.assertIn(path, testplan.PATHS, area)
            for ex in extras:
                self.assertTrue(ex in testplan.STEPS or ex == "cachehits", (area, ex))

    def test_examples(self):
        cases = {
            "AGENTS.md": "docs", "docs/TECHNIK.md": "docs", ".agents/skills/rg-unity-testbed/SKILL.md": "docs",
            "data/skins.json": "data", "scripts/robogen/clips.py": "clips", "scripts/robogen/body.py": "model",
            "scripts/robogen/textures.py": "textures", "scripts/robogen/export.py": "export",
            "scripts/robogen/pipeline.py": "cache", "scripts/robocache.py": "cache", "scripts/testplan.py": "tooling",
            "viewer/Sources/Look.swift": "viewer", "viewer/test_viewer.sh": "viewer",
            "unity/RobotTestbed/Assets/RobotTestbed/Shaders/RobotBody.shader": "unity",
            "unity/RobotTestbed/Library/foo": "ignored", "scripts/debug_frames.py": "debug",
            "scripts/verify_robot.py": "verify", "something/new.bin": "unknown",
            "scripts/heavy_lock.py": "tooling", "scripts/release.py": "tooling", "unity/run_testbed.sh": "unity",
        }
        for f, area in cases.items():
            self.assertEqual(testplan.area_of(f), area, f)

    def test_every_tracked_file_has_a_rule(self):
        """New files get their own rule (AGENTS.md › Prüfablauf 0) - "unknown" is only the safe side for strays."""
        files = testplan.git("ls-files").splitlines()
        if not files:
            self.skipTest("not in a git checkout")
        self.assertEqual([f for f in files if testplan.area_of(f) == "unknown"], [])


class Plans(unittest.TestCase):
    def test_docs_only_runs_nothing(self):
        p = plan("AGENTS.md", "docs/TECHNIK.md", "CHANGELOG.md")
        self.assertEqual((p["path"], p["steps"]), ("none", []))

    def test_data_only_is_the_fast_path(self):
        p = plan("data/skins.json", "README.md")
        self.assertEqual(p["path"], "data")
        self.assertEqual(p["steps"], ["quick1", "standard", "main_full", "main_check"])
        self.assertNotIn("quick2", p["steps"])
        self.assertNotIn("full", p["steps"])            # no full run in the worktree

    def test_model_code_is_the_full_path(self):
        p = plan("scripts/robogen/body.py")
        self.assertEqual(p["path"], "full")
        self.assertEqual(p["steps"], ["quick1", "quick2", "standard", "full", "check", "main_full"])

    def test_cache_code_adds_unit_tests(self):
        self.assertIn("unit", plan("scripts/robocache.py")["steps"])
        self.assertIn("quick2", plan("scripts/robocache.py")["steps"])

    def test_viewer_only(self):
        p = plan("viewer/Sources/Look.swift")
        self.assertEqual(p["steps"], ["quick1", "viewer"])

    def test_unity_only(self):
        p = plan("unity/RobotTestbed/Assets/RobotTestbed/Shaders/RobotBody.shader")
        self.assertEqual(p["steps"], ["standard", "unity"])

    def test_check_output_covers_viewer_and_unity(self):
        p = plan("scripts/robogen/export.py", "viewer/Sources/Look.swift")
        self.assertIn("check", p["steps"])
        self.assertNotIn("viewer", p["steps"])
        self.assertNotIn("unity", p["steps"])

    def test_data_plus_viewer_keeps_the_fast_path(self):
        p = plan("data/skins.json", "viewer/Sources/Look.swift")
        self.assertEqual(p["path"], "data")
        self.assertIn("main_check", p["steps"])         # viewer tests run in check_output.sh after the merge

    def test_forced_data_path(self):
        p = plan("scripts/robogen/materials.py", path="data")
        self.assertEqual(p["path"], "data")
        self.assertNotIn("quick2", p["steps"])

    def test_verify_change_needs_a_full_output(self):
        p = plan("scripts/verify_robot.py")
        self.assertEqual(p["steps"], ["full", "verify"])

    def test_unknown_file_is_the_safe_side(self):
        self.assertEqual(plan("weird/file.txt")["path"], "full")

    def test_estimates(self):
        p = plan("data/skins.json")
        self.assertEqual(p["estimate"]["worktree"], testplan.STEPS["quick1"]["est"] + testplan.STEPS["standard"]["est"])
        self.assertGreater(p["estimate"]["main"], 0)


class RunHelpers(unittest.TestCase):
    def test_compare_reports(self):
        old = {"qa": {"a": 1}, "clips": {"Walk": 2}, "type_bounds": {}, "timings": 1}
        self.assertEqual(testplan.compare_reports(old, dict(old, timings=2)), [])
        self.assertEqual(testplan.compare_reports(old, dict(old, clips={"Walk": 3})), ["clips"])

    def test_missing_hits(self):
        log = "UV cache hit (abc)\nbake cache hit (x)\nclip data: 85 clips from the cache, 0 measured\n"
        self.assertEqual(testplan.missing_hits(log), ["unchanged outputs kept"])
        log = log.replace("85 clips from the cache, 0 measured", "0 clips from the cache, 85 measured")
        self.assertIn("clips from the cache, 0 measured", testplan.missing_hits(log))


if __name__ == "__main__":
    unittest.main()
