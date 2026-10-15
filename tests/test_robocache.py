"""Shared cache (scripts/robocache.py): reuse across checkouts, concurrent writers, pruning, outputs.json, migration.

    python3 -m unittest discover -s tests
"""
import hashlib
import json
import multiprocessing
import os
import shutil
import subprocess
import time
import unittest

from helpers import tempdir

import robocache


def write_bytes(path, data):
    with open(path, "wb") as fh:
        fh.write(data)


def fake_run(out, base):
    """A miniature generator run with the cache patterns of the real one: source textures (folder entry), UVs
    (atomic file), static bake textures (folder entry), skins and renders (files copied out), QA results (JSON) and
    outputs.json. Returns {step: "hit" | "miss"}."""
    res = {}
    src_dir = os.path.join(out, "SourceTextures")
    entry = os.path.join(robocache.kind_dir("source", base), "stamp123")
    if robocache.get_dir(entry, src_dir, ["a.png", "b.png"]):
        res["source"] = "hit"
    else:
        os.makedirs(src_dir, exist_ok=True)
        for n in ("a.png", "b.png"):
            with open(os.path.join(src_dir, n), "wb") as fh:
                fh.write(n.encode() * 100)
        robocache.put_dir(entry, {n: os.path.join(src_dir, n) for n in ("a.png", "b.png")})
        res["source"] = "miss"
    uv = os.path.join(robocache.kind_dir("uv", base), "uv_key.npz")
    if os.path.exists(uv):
        robocache.touch(uv)
        res["uv"] = "hit"
    else:
        robocache.write_atomic(uv, lambda tmp: write_bytes(tmp, b"uv" * 1000))
        res["uv"] = "miss"
    tex = os.path.join(out, "Textures")
    tentry = os.path.join(robocache.kind_dir("textures", base), "bakekey")
    names = ["T_A.png", "Unity/T_U.png"]
    if robocache.get_dir(tentry, tex, names):
        res["bake"] = "hit"
    else:
        for n in names:
            os.makedirs(os.path.dirname(os.path.join(tex, n)), exist_ok=True)
            with open(os.path.join(tex, n), "wb") as fh:
                fh.write(b"t" * 5000)
        robocache.put_dir(tentry, {n: os.path.join(tex, n) for n in names})
        res["bake"] = "miss"
    for kind, name in (("skins", "s1_base.png"), ("render", "digest.png")):
        cached = os.path.join(robocache.kind_dir(kind, base), name)
        dst = os.path.join(out, "Previews", name)
        if robocache.get_file(cached, dst):
            res[kind] = "hit"
        else:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "wb") as fh:
                fh.write(name.encode() * 50)
            robocache.put_file(dst, cached)
            res[kind] = "miss"
    qa = os.path.join(robocache.kind_dir("qa", base), "k_Walk_x.json")
    if robocache.read_json(qa) is not None:
        res["qa"] = "hit"
    else:
        robocache.write_json(qa, {"ok": True})
        res["qa"] = "miss"
    outputs = robocache.OutputCache(os.path.join(out, "cache"), out, robocache.kind_dir("outputs", base))
    fbx = os.path.join(out, "Robot.fbx")
    if outputs.fresh("main", "fbxkey"):
        res["outputs"] = "hit"
    else:
        with open(fbx, "wb") as fh:
            fh.write(b"fbx" * 10)
        outputs.store("main", "fbxkey", [fbx])
        res["outputs"] = "miss"
    return res


class ReuseAcrossCheckouts(unittest.TestCase):
    def test_second_checkout_is_fully_warm(self):
        tmp = tempdir(self)
        base = os.path.join(tmp, "shared")
        first = fake_run(os.path.join(tmp, "main", "output"), base)
        self.assertEqual(set(first.values()), {"miss"})
        second = fake_run(os.path.join(tmp, "worktree", "output"), base)
        self.assertEqual(second, {k: "hit" for k in first})       # a new checkout is warm from its first run
        with open(os.path.join(tmp, "worktree", "output", "Robot.fbx"), "rb") as fh:
            self.assertEqual(fh.read(), b"fbx" * 10)
        outputs = robocache.read_json(os.path.join(tmp, "worktree", "output", "cache", "outputs.json"))
        self.assertEqual(list(outputs["main"]["files"]), ["Robot.fbx"])
        self.assertEqual(set(fake_run(os.path.join(tmp, "worktree", "output"), base).values()), {"hit"})
        with open(os.path.join(tmp, "worktree", "output", "Textures", "Unity", "T_U.png"), "rb") as fh:
            self.assertEqual(fh.read(), b"t" * 5000)

    def test_default_root_and_env(self):
        old = os.environ.pop(robocache.ENV_DIR, None)
        try:
            self.assertTrue(robocache.root().endswith(os.path.join("RobotGen", "cache")))
            os.environ[robocache.ENV_DIR] = "~/somewhere"
            self.assertEqual(robocache.root(), os.path.join(os.path.expanduser("~"), "somewhere"))
        finally:
            os.environ.pop(robocache.ENV_DIR, None)
            if old is not None:
                os.environ[robocache.ENV_DIR] = old

    def test_limit_env(self):
        old = os.environ.get(robocache.ENV_LIMIT)
        try:
            os.environ.pop(robocache.ENV_LIMIT, None)
            self.assertEqual(robocache.limit_bytes(), int(6 * 1024 ** 3))
            os.environ[robocache.ENV_LIMIT] = "0.5"
            self.assertEqual(robocache.limit_bytes(), 512 * 1024 ** 2)
        finally:
            os.environ.pop(robocache.ENV_LIMIT, None)
            if old is not None:
                os.environ[robocache.ENV_LIMIT] = old


def _writer(path, marker, rounds):
    blob = bytes([marker]) * (2 * 1024 * 1024)
    for _ in range(rounds):
        robocache.write_atomic(path, lambda tmp: write_bytes(tmp, blob))


def _dir_writer(entry, src_dir):
    robocache.put_dir(entry, {n: os.path.join(src_dir, n) for n in sorted(os.listdir(src_dir))})


class ConcurrentWriters(unittest.TestCase):
    def test_readers_never_see_half_a_file(self):
        tmp = tempdir(self)
        path = os.path.join(tmp, "render", "same_key.png")
        os.makedirs(os.path.dirname(path))
        ctx = multiprocessing.get_context("spawn")
        procs = [ctx.Process(target=_writer, args=(path, m, 40)) for m in (1, 2, 3)]
        for p in procs:
            p.start()
        seen = set()
        while any(p.is_alive() for p in procs):
            try:
                with open(path, "rb") as fh:
                    data = fh.read()
            except FileNotFoundError:
                continue
            self.assertEqual(len(data), 2 * 1024 * 1024)
            self.assertEqual(len(set(data)), 1)         # one writer's complete content, never a mix
            seen.add(data[0])
        for p in procs:
            p.join()
            self.assertEqual(p.exitcode, 0)
        self.assertTrue(seen)                           # the reader really read while they wrote
        self.assertEqual([f for f in os.listdir(os.path.dirname(path)) if f != "same_key.png"], [])   # no temp files

    def test_folder_entry_stored_once_and_complete(self):
        tmp = tempdir(self)
        src = os.path.join(tmp, "src")
        os.makedirs(src)
        for i in range(20):
            with open(os.path.join(src, "f%02d.png" % i), "wb") as fh:
                fh.write(bytes([i]) * 10000)
        entry = os.path.join(tmp, "shared", "textures", "key")
        os.makedirs(os.path.dirname(entry))
        ctx = multiprocessing.get_context("spawn")
        procs = [ctx.Process(target=_dir_writer, args=(entry, src)) for _ in range(4)]
        for p in procs:
            p.start()
        for p in procs:
            p.join()
            self.assertEqual(p.exitcode, 0)
        self.assertEqual(sorted(os.listdir(entry)), sorted(os.listdir(src)))
        self.assertEqual(os.listdir(os.path.dirname(entry)), ["key"])
        dst = os.path.join(tmp, "out")
        self.assertTrue(robocache.get_dir(entry, dst, sorted(os.listdir(src))))
        with open(os.path.join(dst, "f07.png"), "rb") as fh:
            self.assertEqual(fh.read(), bytes([7]) * 10000)

    def test_unique_temp_names(self):
        self.assertNotEqual(robocache.tmp_path("/x/uv_a.npz"), robocache.tmp_path("/x/uv_a.npz"))
        self.assertTrue(robocache.tmp_path("/x/uv_a.npz").endswith("uv_a.npz"))     # np.savez keeps the name


class Pruning(unittest.TestCase):
    def make(self, base, kind, name, size, age, folder=False):
        path = os.path.join(base, kind, name)
        if folder:
            os.makedirs(path)
            for i in range(2):
                with open(os.path.join(path, "f%d" % i), "wb") as fh:
                    fh.write(b"x" * (size // 2))
        else:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as fh:
                fh.write(b"x" * size)
        t = time.time() - age
        os.utime(path, (t, t))
        return path

    def test_least_recently_used_first(self):
        base = tempdir(self)
        day = 86400
        a = self.make(base, "render", "a.png", 1000, 5 * day)
        b = self.make(base, "qa", "b.json", 1000, 4 * day)
        c = self.make(base, "textures", "c", 1000, 3 * day, folder=True)
        d = self.make(base, "uv", "d.npz", 1000, 2 * day)
        res = robocache.prune(base, limit=2500)
        self.assertEqual(res["removed"], 2)
        self.assertFalse(os.path.exists(a))
        self.assertFalse(os.path.exists(b))
        self.assertTrue(os.path.isdir(c) and os.path.exists(d))
        self.assertEqual(res["total"], 2000)

    def test_read_touches(self):
        base = tempdir(self)
        old = self.make(base, "render", "old.png", 1000, 9 * 86400)
        newer = self.make(base, "render", "newer.png", 1000, 8 * 86400)
        self.assertTrue(robocache.get_file(old, os.path.join(base, "..", "copy.png")))
        os.remove(os.path.join(base, "..", "copy.png"))
        robocache.prune(base, limit=1500, grace=3600)
        self.assertTrue(os.path.exists(old))            # just used
        self.assertFalse(os.path.exists(newer))

    def test_grace_keeps_entries_of_running_jobs(self):
        base = tempdir(self)
        recent = self.make(base, "render", "recent.png", 5000, 60)
        res = robocache.prune(base, limit=100)
        self.assertTrue(os.path.exists(recent))
        self.assertEqual(res["removed"], 0)

    def test_stale_temp_files_go_fresh_ones_stay(self):
        base = tempdir(self)
        stale = self.make(base, "render", robocache.TMP_PREFIX + "1-ab-x.png", 10, 3 * 86400)
        fresh = self.make(base, "render", robocache.TMP_PREFIX + "2-cd-y.png", 10, 10)
        robocache.prune(base, limit=10 ** 9)
        self.assertFalse(os.path.exists(stale))
        self.assertTrue(os.path.exists(fresh))

    def test_root_files_are_not_entries(self):
        base = tempdir(self)
        robocache.write_json(os.path.join(base, "timings.json"), {"full/warm": [1]})
        self.make(base, "qa", "q.json", 100, 9 * 86400)
        robocache.prune(base, limit=0)
        self.assertTrue(os.path.exists(os.path.join(base, "timings.json")))

    def test_second_pruner_skips(self):
        base = tempdir(self)
        with robocache._Lock(base, ".prune.lock", block=False) as got:
            self.assertTrue(got)
            self.assertTrue(robocache.prune(base, limit=0)["skipped"])


class OutputsJson(unittest.TestCase):
    def test_relative_paths_survive_a_moved_output(self):
        tmp = tempdir(self)
        out = os.path.join(tmp, "a", "output")
        os.makedirs(os.path.join(out, "Forms"))
        for rel in ("Robot.fbx", "Forms/f1.fbx"):
            with open(os.path.join(out, rel), "w") as fh:
                fh.write(rel)
        oc = robocache.OutputCache(os.path.join(out, "cache"), out)
        oc.store("main", "k", [os.path.join(out, "Robot.fbx"), os.path.join(out, "Forms")])
        with open(os.path.join(out, "cache", "outputs.json")) as fh:
            files = json.load(fh)["main"]["files"]
        self.assertEqual(sorted(files), ["Forms/f1.fbx", "Robot.fbx"])
        moved = os.path.join(tmp, "b", "output")
        shutil.copytree(out, moved)
        self.assertIsNotNone(robocache.OutputCache(os.path.join(moved, "cache"), moved).fresh("main", "k"))
        self.assertIsNone(robocache.OutputCache(os.path.join(moved, "cache"), moved).fresh("main", "other"))

    def test_shared_entry_needs_the_same_key(self):
        tmp = tempdir(self)
        shared = os.path.join(tmp, "shared", "outputs")
        a, b = os.path.join(tmp, "a"), os.path.join(tmp, "b")
        os.makedirs(os.path.join(a, "ViewerData"))
        with open(os.path.join(a, "ViewerData", "robot.json"), "w") as fh:
            fh.write("{}")
        robocache.OutputCache(os.path.join(a, "cache"), a, shared).store("viewer", "k1",
                                                                         [os.path.join(a, "ViewerData")], {"n": 1})
        oc = robocache.OutputCache(os.path.join(b, "cache"), b, shared)
        self.assertIsNone(oc.fresh("viewer", "k2"))
        hit = oc.fresh("viewer", "k1")
        self.assertEqual(hit["result"], {"n": 1})
        self.assertEqual(oc.shared_hits, ["viewer"])
        self.assertTrue(os.path.isfile(os.path.join(b, "ViewerData", "robot.json")))
        with open(os.path.join(b, "ViewerData", "stale.bin"), "w") as fh:    # left by an older export
            fh.write("old")
        oc2 = robocache.OutputCache(os.path.join(b, "cache2"), b, shared)
        self.assertIsNotNone(oc2.fresh("viewer", "k1"))
        self.assertFalse(os.path.exists(os.path.join(b, "ViewerData", "stale.bin")))
        self.assertIsNone(robocache.OutputCache(os.path.join(b, "cache"), b).fresh("viewer", "k2"))   # no shared

    def test_legacy_absolute_paths_only_inside_this_output(self):
        tmp = tempdir(self)
        out = os.path.join(tmp, "output")
        os.makedirs(os.path.join(out, "cache"))
        fbx = os.path.join(out, "Robot.fbx")
        with open(fbx, "w") as fh:
            fh.write("x")
        robocache.write_json(os.path.join(out, "cache", "outputs.json"), {"main": {"key": "k", "files": {fbx: 1}}})
        self.assertIsNotNone(robocache.OutputCache(os.path.join(out, "cache"), out).fresh("main", "k"))
        other = os.path.join(tmp, "other")
        shutil.copytree(out, other)
        self.assertIsNone(robocache.OutputCache(os.path.join(other, "cache"), other).fresh("main", "k"))


def legacy_cache(cache):
    """An output cache as generator 1.20.1 left it."""
    os.makedirs(os.path.join(cache, "render"))
    os.makedirs(os.path.join(cache, "qa"))
    os.makedirs(os.path.join(cache, "clips"))
    os.makedirs(os.path.join(cache, "skin_textures"))
    files = {"uv_aaa.npz": b"uv", "ao_bbb.npz": b"ao", "bake_%s.npz" % ("c" * 20): b"bake", "render/d1.png": b"r1",
             "render/d2.png": b"r2", "qa/k_Walk_1.json": b"{}", "clips/k_Run_2.json": b"{}",
             "skin_textures/T_Robot_BaseColor_Hazard.png": b"hb", "skin_textures/T_Robot_Emission_Hazard.png": b"he",
             "outputs.json": b"{}"}
    for rel, data in files.items():
        with open(os.path.join(cache, rel), "wb") as fh:
            fh.write(data)
    robocache.write_json(os.path.join(cache, "skin_textures", "stamps.json"), {"Hazard": "s" * 40, "Classic": "e" * 40})
    robocache.write_json(os.path.join(cache, "timings.json"), {"full/warm": [100.0, 98.0]})
    out = os.path.dirname(cache)
    for rel, data in ((".make_textures.stamp", "d" * 64), ("T_Src_A.png", "src")):
        os.makedirs(os.path.join(out, "SourceTextures"), exist_ok=True)
        with open(os.path.join(out, "SourceTextures", rel), "w") as fh:
            fh.write(data)
    for rel, data in ((".bake.stamp", "c" * 64), ("T_Robot_Normal.png", "n"), ("T_Robot_BaseColor.png", "skin"),
                      ("T_Robot_Emission.png", "glow"), ("Unity/T_Robot_U_Aux.png", "u"), ("../Robot.fbx", "fbx")):
        os.makedirs(os.path.dirname(os.path.join(out, "Textures", rel)), exist_ok=True)
        with open(os.path.join(out, "Textures", rel), "w") as fh:
            fh.write(data)
    with open(os.path.join(cache, "outputs.json"), "w") as fh:     # 1.20.1: absolute paths
        json.dump({"main": {"key": "f" * 40, "files": {os.path.join(out, "Robot.fbx"): 3}, "result": {"n": 1}}}, fh)
    return files


class Migration(unittest.TestCase):
    EXPECTED = ["ao/ao_bbb.npz", "bake/bake_%s.npz" % ("c" * 20), "clips/k_Run_2.json", "qa/k_Walk_1.json",
                "outputs/main_%s/Robot.fbx" % ("f" * 20), "outputs/main_%s/_manifest.json" % ("f" * 20),
                "render/d1.png", "render/d2.png", "skins/%s_base.png" % ("e" * 40), "skins/%s_emit.png" % ("e" * 40),
                "skins/%s_base.png" % ("s" * 40), "skins/%s_emit.png" % ("s" * 40),
                "source/%s/T_Src_A.png" % ("d" * 20), "textures/%s/T_Robot_Normal.png" % ("c" * 20),
                "textures/%s/Unity/T_Robot_U_Aux.png" % ("c" * 20), "uv/uv_aaa.npz"]

    def listing(self, base):
        return sorted(os.path.relpath(os.path.join(d, n), base) for d, _, ns in os.walk(base) for n in ns
                      if not n.startswith(".") and d != base)

    def test_clone_leaves_the_other_checkout_alone(self):
        tmp = tempdir(self)
        cache, base = os.path.join(tmp, "main", "output", "cache"), os.path.join(tmp, "shared")
        files = legacy_cache(cache)
        self.assertEqual(robocache.adopt_legacy(cache, base, move=False), 14)
        with open(os.path.join(base, "skins", "e" * 40 + "_emit.png")) as fh:
            self.assertEqual(fh.read(), "glow")                     # the default skin from Textures/
        oc = robocache.OutputCache(os.path.join(tmp, "wt", "cache"), os.path.join(tmp, "wt"),
                                   os.path.join(base, "outputs"))
        self.assertEqual(oc.fresh("main", "f" * 40)["result"], {"n": 1})
        self.assertEqual(self.listing(base), sorted(self.EXPECTED))
        self.assertEqual(robocache.legacy_sources(base), [os.path.join(tmp, "main", "output", "SourceTextures")])
        new_out = os.path.join(tmp, "wt", "output")                 # a 1.20.2 output: no bake files in its cache
        os.makedirs(os.path.join(new_out, "cache"))
        os.makedirs(os.path.join(new_out, "SourceTextures"))
        with open(os.path.join(new_out, "SourceTextures", ".make_textures.stamp"), "w") as fh:
            fh.write("d" * 64)
        robocache.adopt_legacy(os.path.join(new_out, "cache"), base, move=True)
        self.assertEqual(len(robocache.legacy_sources(base)), 1)
        for rel in files:
            self.assertTrue(os.path.exists(os.path.join(cache, rel)), rel)
        with open(os.path.join(base, "render", "d2.png"), "rb") as fh:
            self.assertEqual(fh.read(), b"r2")
        self.assertFalse(os.path.exists(os.path.join(base, "timings.json")))      # the time band stays per output
        self.assertEqual(robocache.adopt_legacy(cache, base, move=False), 0)        # idempotent

    def test_move_empties_the_own_cache_but_keeps_checkout_state(self):
        tmp = tempdir(self)
        cache, base = os.path.join(tmp, "output", "cache"), os.path.join(tmp, "shared")
        legacy_cache(cache)
        robocache.adopt_legacy(cache, base, move=True)
        self.assertEqual(self.listing(base), sorted(self.EXPECTED))
        left = sorted(os.path.relpath(os.path.join(d, n), cache) for d, _, ns in os.walk(cache) for n in ns)
        self.assertEqual(left, ["outputs.json", "skin_textures/T_Robot_BaseColor_Hazard.png",
                                "skin_textures/T_Robot_Emission_Hazard.png", "skin_textures/stamps.json",
                                "timings.json"])

    def test_prepare_adopts_the_main_checkout_once(self):
        tmp = tempdir(self)
        main = os.path.join(tmp, "repo")
        os.makedirs(os.path.join(main, "scripts"))
        def git(*a):
            subprocess.run(["git", "-C", main] + list(a), check=True, capture_output=True)
        git("init", "-q", "-b", "main")
        git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "x")
        wt = os.path.join(tmp, "wt")
        git("worktree", "add", "-q", "--detach", wt)
        os.makedirs(os.path.join(wt, "scripts"))
        legacy_cache(os.path.join(main, "output", "cache"))
        legacy_cache(os.path.join(main, "output", "_quick", "cache"))
        with open(os.path.join(main, "output", "_quick", "cache", "uv_quick.npz"), "wb") as fh:
            fh.write(b"q")
        base = os.path.join(tmp, "shared")
        adopted = robocache.prepare(os.path.join(wt, "output"), os.path.join(wt, "scripts"), base)
        self.assertEqual(adopted, {os.path.join(main, "output", "cache"): 14,
                                   os.path.join(main, "output", "_quick", "cache"): 1})
        self.assertTrue(os.path.exists(os.path.join(base, "uv", "uv_quick.npz")))
        self.assertTrue(os.path.exists(os.path.join(main, "output", "cache", "uv_aaa.npz")))    # cloned, not moved
        self.assertEqual(robocache.prepare(os.path.join(wt, "output"), os.path.join(wt, "scripts"), base), {})
        # the main checkout's own first run moves its leftovers (already in the shared cache: just removed)
        robocache.prepare(os.path.join(main, "output"), os.path.join(main, "scripts"), base)
        self.assertFalse(os.path.exists(os.path.join(main, "output", "cache", "uv_aaa.npz")))
        self.assertFalse(os.path.exists(os.path.join(main, "output", "cache", "render")))
        self.assertTrue(os.path.exists(os.path.join(main, "output", "cache", "outputs.json")))
        with open(os.path.join(base, "render", "d1.png"), "rb") as fh:
            self.assertEqual(hashlib.sha1(fh.read()).hexdigest(), hashlib.sha1(b"r1").hexdigest())


if __name__ == "__main__":
    unittest.main()
