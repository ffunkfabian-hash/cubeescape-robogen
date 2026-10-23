"""Verification plan from the diff (1.20.2): which tiers and checks a change needs, with a time estimate.

    python3 scripts/testplan.py                      # diff against main (merge base) + uncommitted + untracked files
    python3 scripts/testplan.py --base v1.20.1       # against another ref
    python3 scripts/testplan.py --files data/skins.json scripts/robogen/clips.py
    python3 scripts/testplan.py --path data          # force a path (data, full) when the diff cannot tell, e.g. only
                                                     # values in a code table like FINISH_PARAMS changed
    python3 scripts/testplan.py --run                # run the worktree part of the plan (stops at the first failure)
    python3 scripts/testplan.py --base v1.20.1 --after-merge --run   # in the main checkout: the part after the merge

The rules follow AGENTS.md › Prüfablauf: docs only -> nothing heavy; data only -> fast path (quick once, standard once,
after the merge one warm full + check_output.sh in the main checkout); model/rig/clips/UV/bake/export/cache code ->
full path (quick twice with cache-hit checks, standard, full, check_output.sh); viewer -> viewer build + tests;
export/clips/textures/masks/looks/shader -> Unity testbed. The mapping is the table RULES (first match wins) and AREAS;
tests: tests/test_testplan.py. Pure Python, no Blender.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------------------------------------------------------
# The mapping: file pattern -> area (first match wins; fnmatch, "*" also matches "/")
# ---------------------------------------------------------------------------------------------------------------
RULES = [
    ("output/*", "ignored"),
    ("unity/RobotTestbed/Library/*", "ignored"),
    ("unity/RobotTestbed/Temp/*", "ignored"),
    ("unity/RobotTestbed/Logs/*", "ignored"),
    ("viewer/.build/*", "ignored"),
    ("*.md", "docs"),
    ("docs/*", "docs"),
    (".agents/*", "docs"),
    (".github/*", "docs"),
    (".claude/agents/*", "docs"),
    (".claude/hookify.*", "docs"),
    (".claude/agent-guard.json", "docs"),
    (".claude/settings.json", "docs"),
    (".claude/skills", "docs"),                    # link to .agents/skills
    (".gitignore", "docs"),
    (".claude/hooks/*", "tooling"),
    ("tests/*", "tooling"),
    ("scripts/testplan.py", "tooling"),
    ("scripts/quiet.py", "tooling"),
    ("scripts/heavy_lock.py", "tooling"),
    ("ce", "tooling"),                               # loader of the shared tools (./ce tools pin)
    (".ce-repo.json", "tooling"),
    ("check", "tooling"),
    ("ce", "tooling"),
    (".mcp.json", "tooling"),
    ("scripts/nightly.sh", "tooling"),
    ("release", "tooling"),
    ("scripts/release.py", "tooling"),
    ("scripts/check_output.sh", "tooling"),
    ("scripts/debug_*.py", "debug"),
    ("scripts/diag_parts.py", "debug"),
    ("scripts/profile_run.py", "debug"),
    ("scripts/concept_palettes.py", "debug"),
    ("data/*", "data"),
    ("scripts/robocache.py", "cache"),
    ("scripts/robogen/pipeline.py", "cache"),
    ("scripts/make_textures.py", "textures"),
    ("scripts/robogen/textures.py", "textures"),
    ("scripts/robogen/materials.py", "textures"),
    ("scripts/robogen/export.py", "export"),
    ("scripts/export_viewer.py", "export"),
    ("scripts/reexport_viewer.py", "export"),
    ("scripts/robogen/config.py", "export"),       # robot_config.json, looks: read by the viewer and Unity
    ("scripts/robogen/clips.py", "clips"),
    ("scripts/robogen/motion.py", "clips"),
    ("scripts/robogen/preview.py", "preview"),
    ("scripts/robogen/*", "model"),
    ("scripts/generate_robot.py", "model"),
    ("scripts/verify_robot.py", "verify"),
    ("viewer/*", "viewer"),
    ("unity/*", "unity"),
    ("*", "unknown"),
]

# Paths, from light to heavy. A plan takes the heaviest path of its files plus every extra check they need.
PATHS = ("none", "tooling", "data", "full")
PATH_TEXT = {
    "none": "nothing heavy (documentation only)",
    "tooling": "light path: no generator change - only the checks below (unit tests, viewer, Unity, verify)",
    "data": "fast path for data-only changes (AGENTS.md › Schneller Weg für reine Datenänderungen)",
    "full": "full path (AGENTS.md › Prüfablauf vor dem Commit)",
}
AREAS = {
    #            path        extra checks                 why
    "ignored": ("none", (), "not versioned"),
    "docs": ("none", (), "documentation, rules, agent files"),
    "debug": ("none", (), "debugging helpers (not part of a run)"),
    "tooling": ("tooling", ("unit",), "verification tooling"),
    "data": ("data", (), "data tables (skins, looks, colours)"),
    "verify": ("tooling", ("verify",), "the independent checks (verify_robot.py on an existing output)"),
    "viewer": ("tooling", ("viewer",), "viewer code"),
    "unity": ("tooling", ("unity",), "Unity testbed, Unity templates"),
    "model": ("full", (), "model, rig, UV, QA or run code"),
    "clips": ("full", ("unity",), "clips and clip data (game contract)"),
    "textures": ("full", ("unity",), "materials, bake, masks, textures"),
    "export": ("full", ("viewer", "unity"), "export, config, looks"),
    "preview": ("full", (), "preview renders"),
    "cache": ("full", ("cachehits", "unit"), "caches and work packages"),
    "unknown": ("full", (), "unknown file - the safe side"),
}

BLENDER_TIER = "{blender} -b --factory-startup --python-exit-code 1 --python scripts/generate_robot.py -- --tier {tier}"
# Steps in their order. est: seconds on a quiet machine with warm caches (README › Teststufen; check_output.sh is
# the slowest of its three parallel checks). where: worktree = before the commit, main = after the merge.
STEPS = {
    "unit": {"cmd": "python3 -m unittest discover -s tests", "est": 5, "where": "worktree",
             "text": "unit tests (shared cache, testplan, quiet, heavy lock, release)"},
    "quick1": {"cmd": BLENDER_TIER.replace("{tier}", "quick"), "est": 12, "where": "worktree", "tier": "quick",
               "compare": True, "text": "quick once; qa/clips/type_bounds against the saved report"},
    "quick2": {"cmd": BLENDER_TIER.replace("{tier}", "quick"), "est": 12, "where": "worktree", "tier": "quick",
               "hits": True, "text": "quick again: UV, bake, QA and output caches must hit"},
    "standard": {"cmd": BLENDER_TIER.replace("{tier}", "standard"), "est": 70, "where": "worktree", "tier": "standard",
                 "compare": True, "text": "standard once; qa/clips/type_bounds against the saved report"},
    "full": {"cmd": BLENDER_TIER.replace("{tier}", "full"), "est": 100, "where": "worktree", "tier": "full",
             "compare": True, "heavy": True, "text": "full (heavy lock, pauses other sessions on a busy machine)"},
    "check": {"cmd": "scripts/check_output.sh output", "est": 65, "where": "worktree", "heavy": True,
              "text": "check_output.sh: verify_robot.py, viewer build + tests, Unity testbed in parallel"},
    "verify": {"cmd": "{blender} -b output/CubeEscape_Robot.blend --python-exit-code 1 --python scripts/verify_robot.py "
                      "-- --out output", "est": 35, "where": "worktree", "needs": "full",
               "text": "verify_robot.py on the full output"},
    "viewer": {"cmd": "viewer/build.sh output/_quick && viewer/test_viewer.sh output/_quick", "est": 25,
               "where": "worktree", "needs": "quick", "text": "viewer build + tests"},
    "unity": {"cmd": "unity/run_testbed.sh output/_standard", "est": 65, "where": "worktree", "needs": "standard",
              "text": "Unity testbed (own project, never the game)"},
    "main_full": {"cmd": BLENDER_TIER.replace("{tier}", "full"), "est": 100, "where": "main", "tier": "full",
                  "compare": True, "heavy": True,
                  "text": "full once in the main checkout (shared cache: warm) = the deliverable in output/"},
    "main_check": {"cmd": "scripts/check_output.sh output", "est": 65, "where": "main", "heavy": True,
                   "text": "check_output.sh once on it"},
}
PATH_STEPS = {
    "none": [],
    "tooling": [],
    "data": ["quick1", "standard", "main_full", "main_check"],
    "full": ["quick1", "quick2", "standard", "full", "check", "main_full"],
}
# check_output.sh already contains these
COVERED_BY_CHECK = {"verify", "viewer", "unity"}
# log lines of the second quick run (AGENTS.md › Prüfablauf 4)
CACHE_HITS = ("UV cache hit", "bake cache hit", "clips from the cache, 0 measured", "unchanged outputs kept")
COMPARE_KEYS = ("qa", "clips", "type_bounds")


def area_of(path: str) -> str:
    for pattern, area in RULES:
        if fnmatch.fnmatch(path, pattern):
            return area
    return "unknown"


def make_plan(files, force_path: str | None = None) -> dict:
    """{"files": [(file, area)], "path": ..., "steps": [step ids in order], "estimate": {where: seconds}}."""
    rows = [(f, area_of(f)) for f in sorted(set(files))]
    path, extras = "none", set()
    for _, area in rows:
        p, ex, _ = AREAS[area]
        path = max(path, p, key=PATHS.index)
        extras.update(ex)
    if force_path:
        path = force_path
    steps = list(PATH_STEPS[path])
    extras.discard("cachehits")                 # quick2 (cache hits) is part of the full path
    if "check" in steps or "main_check" in steps:
        extras -= COVERED_BY_CHECK
    for ex in sorted(extras):
        needs = STEPS[ex].get("needs")
        if needs == "quick" and not any(STEPS[s].get("tier") for s in steps):
            steps.append("quick1")
        if needs == "standard" and "standard" not in steps:
            steps.append("standard")
        if needs == "full" and "full" not in steps:
            steps.append("full")
        steps.append(ex)
    order = list(STEPS)
    steps = sorted(set(steps), key=order.index)
    est = {"worktree": 0, "main": 0}
    for s in steps:
        est[STEPS[s]["where"]] += STEPS[s]["est"]
    return {"files": rows, "path": path, "steps": steps, "estimate": est}


def git(*args) -> str:
    return subprocess.run(["git", "-C", ROOT] + list(args), capture_output=True, text=True).stdout.strip()


def changed_files(base: str) -> tuple:
    """(files, description): committed since the merge base with base, uncommitted and untracked files."""
    mb = git("merge-base", "HEAD", base) or base
    files = set(filter(None, git("diff", "--name-only", mb, "HEAD").splitlines()))
    files |= set(filter(None, git("diff", "--name-only", "HEAD").splitlines()))
    files |= set(filter(None, git("ls-files", "--others", "--exclude-standard").splitlines()))
    return sorted(files), "against %s (merge base %s) + uncommitted + untracked" % (base, mb[:9])


def fmt_time(seconds: float) -> str:
    return "%d s" % seconds if seconds < 90 else "%d:%02d min" % (seconds // 60, seconds % 60)


def print_plan(plan: dict, source: str) -> None:
    print("Verification plan (%d files %s)" % (len(plan["files"]), source))
    for f, area in plan["files"]:
        print("  %-8s %s" % (area, f))
    print("Path: %s" % PATH_TEXT[plan["path"]])
    for where, title in (("worktree", "Before the commit (worktree)"), ("main", "After the merge (main checkout)")):
        steps = [s for s in plan["steps"] if STEPS[s]["where"] == where]
        if not steps:
            continue
        print("%s - about %s with warm caches:" % (title, fmt_time(plan["estimate"][where])))
        for i, s in enumerate(steps, 1):
            print("  %d. %-10s %s" % (i, s, STEPS[s]["text"]))
            print("     %s" % STEPS[s]["cmd"].format(blender=blender_path()))
    if plan["path"] == "none" and not plan["steps"]:
        print("Nothing to run: no Blender tier, no checks.")
    print("Heavy steps (full, check_output.sh, run_testbed.sh) queue on the heavy-run lock shared with Cube Escape "
          "and Cubegen (scripts/heavy_lock.py, CE_HEAVY_LOCK=0 = skip), then pause other sessions on a still busy "
          "machine (scripts/quiet.py, ROBOGEN_NO_PAUSE=1 = never).")


# ---------------------------------------------------------------------------------------------------------------
# --run
# ---------------------------------------------------------------------------------------------------------------

def blender_path() -> str:
    if os.environ.get("BLENDER"):
        return os.environ["BLENDER"]
    mac = "/Applications/Blender.app/Contents/MacOS/Blender"
    return mac if os.path.exists(mac) else (shutil.which("blender") or "blender")


def out_dir(tier: str) -> str:
    return os.path.join(ROOT, "output") if tier == "full" else os.path.join(ROOT, "output", "_" + tier)


def generator_version() -> str:
    with open(os.path.join(ROOT, "scripts", "robogen", "core.py"), encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("VERSION = "):
                return line.split('"')[1]
    return "?"


def main_checkout() -> str | None:
    common = git("rev-parse", "--path-format=absolute", "--git-common-dir")
    return os.path.dirname(common) if common.endswith(".git") else None


def reference_report(tier: str) -> tuple:
    """(path, note) of the report to compare a tier with: this output's last report, else the main checkout's of
    the same tier when it comes from the same generator minor version (an older one would differ anyway)."""
    own = os.path.join(out_dir(tier), "logs", "robot_report.json")
    if os.path.exists(own):
        return own, "this output's last report"
    main = main_checkout()
    if main and os.path.realpath(main) != os.path.realpath(ROOT):
        rel = os.path.relpath(own, ROOT)
        other = os.path.join(main, rel)
        try:
            with open(other, encoding="utf-8") as fh:
                ver = json.load(fh).get("generator", "?")
        except (OSError, ValueError):
            return None, "no report to compare with"
        if ver.split(".")[:2] == generator_version().split(".")[:2]:
            return other, "the main checkout's report (generator %s)" % ver
        return None, "the main checkout's report is from generator %s - not compared" % ver
    return None, "no report to compare with"


def compare_reports(old: dict, new: dict, keys=COMPARE_KEYS) -> list:
    """Keys whose content differs (a missing key on one side counts as different)."""
    return [k for k in keys if old.get(k) != new.get(k)]


def missing_hits(log_text: str, needed=CACHE_HITS) -> list:
    return [h for h in needed if h not in log_text]


def run_step(step: str, allow_change: bool) -> bool:
    spec = STEPS[step]
    cmd = spec["cmd"].format(blender='"%s"' % blender_path())
    report = before = None
    ref_note = ""
    if spec.get("tier"):
        report = os.path.join(out_dir(spec["tier"]), "logs", "robot_report.json")
        if spec.get("compare"):
            ref, ref_note = reference_report(spec["tier"])
            if ref:
                before = report.replace(".json", ".before.json")
                os.makedirs(os.path.dirname(before), exist_ok=True)
                shutil.copyfile(ref, before)
    print("\n>> %s: %s" % (step, cmd), flush=True)
    t0 = time.time()
    code = subprocess.call(cmd, shell=True, cwd=ROOT)
    took = time.time() - t0
    ok = code == 0
    note = []
    if ok and report and os.path.exists(report):
        with open(report, encoding="utf-8") as fh:
            new = json.load(fh)
        if before:
            with open(before, encoding="utf-8") as fh:
                diff = compare_reports(json.load(fh), new)
            if diff:
                note.append("report differs in %s (vs %s, saved as %s)" % (", ".join(diff), ref_note, before))
                ok = ok and allow_change
            else:
                note.append("qa/clips/type_bounds identical to %s" % ref_note)
        elif spec.get("compare"):
            note.append(ref_note)
        if spec.get("hits"):
            with open(os.path.join(out_dir(spec["tier"]), "logs", "generate_robot.log"), encoding="utf-8") as fh:
                miss = missing_hits(fh.read())
            note.append("cache hits: %s" % ("all" if not miss else "MISSING " + ", ".join(miss)))
            ok = ok and not miss
        if new.get("quiet"):
            note.append("quiet: %s" % new["quiet"])
    print("<< %s: %s in %s%s" % (step, "OK" if ok else "FAILED (exit %d)" % code if code else "FAILED", fmt_time(took),
                                 "; " + "; ".join(note) if note else ""), flush=True)
    return ok


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", default="main", help="ref to diff against (merge base), default main")
    ap.add_argument("--files", nargs="+", help="these files instead of the diff")
    ap.add_argument("--path", choices=PATHS, help="force a path (e.g. data when only table values changed)")
    ap.add_argument("--run", action="store_true", help="run the plan (worktree part, or with --after-merge the rest)")
    ap.add_argument("--after-merge", action="store_true", help="with --run: the steps in the main checkout")
    ap.add_argument("--allow-change", action="store_true", help="a differing report is wanted (named in the report)")
    ap.add_argument("--json", action="store_true", help="print the plan as JSON")
    args = ap.parse_args(argv)
    if args.files:
        files, source = args.files, "given"
    else:
        files, source = changed_files(args.base)
    plan = make_plan(files, args.path)
    if args.json:
        print(json.dumps(plan, indent=1))
    else:
        print_plan(plan, source)
    if not args.run:
        return 0
    where = "main" if args.after_merge else "worktree"
    todo = [s for s in plan["steps"] if STEPS[s]["where"] == where]
    t0 = time.time()
    for s in todo:
        if not run_step(s, args.allow_change):
            print("\nstopped after %s (%s)" % (s, fmt_time(time.time() - t0)))
            return 1
    print("\nplan done: %d steps in %s" % (len(todo), fmt_time(time.time() - t0)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
