"""Shared cache of the robot generator (1.20.2): one content-addressed store for every checkout.

Pure Python (no bpy): the generator imports it inside Blender, scripts/testplan.py and the unit tests (tests/) with
the system Python. Entries are named by their content key, so the main checkout, every worktree and nightly runs share
them; only checkout-bound state stays in <out>/cache (outputs.json with paths relative to the output, the working
copies of the preview skins).

    ROBOGEN_CACHE            the folder (default ~/Library/Caches/RobotGen/cache, elsewhere $XDG_CACHE_HOME/RobotGen/cache)
    ROBOGEN_CACHE_LIMIT_GB   size limit for prune() (default 6): least recently used entries go first

Layout: <root>/<kind>/<entry>; an entry is a file or a folder (a set of files that only makes sense together, e.g. the
static textures of one bake). Rules that keep concurrent runs safe:
  * writes are atomic: a temp file (".tmp-<pid>-<random>-<name>") in the target folder, then os.replace / os.rename -
    a reader sees the old entry, the new one or none, never half a file;
  * nothing deletes an entry because a newer one exists (another checkout may need the older one) - only prune() by
    size, and never an entry used within GRACE_SECONDS (a run in another checkout may be reading it right now);
  * a read touches the entry (mtime = last use) - prune() removes the least recently used first.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import json
import os
import shutil
import subprocess
import sys
import time
import uuid

ENV_DIR = "ROBOGEN_CACHE"
ENV_LIMIT = "ROBOGEN_CACHE_LIMIT_GB"
DEFAULT_LIMIT_GB = 6.0
GRACE_SECONDS = 2 * 3600            # a cold full run takes well under that; its entries are never pruned meanwhile
STALE_TMP_SECONDS = 24 * 3600       # temp files of a crashed run
KINDS = ("uv", "ao", "bake", "textures", "skins", "source", "render", "qa", "clips", "outputs")
TMP_PREFIX = ".tmp-"
MIGRATED = "migrated.json"          # legacy <out>/cache folders already adopted (see adopt_legacy)
LEGACY_SOURCES = "legacy_sources.json"  # their SourceTextures folders: old bake keys held that path (legacy_sources)


def default_root() -> str:
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Caches", "RobotGen", "cache")
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "RobotGen", "cache")


def root() -> str:
    return os.path.abspath(os.path.expanduser(os.environ.get(ENV_DIR) or default_root()))


def limit_bytes() -> int:
    try:
        gb = float(os.environ.get(ENV_LIMIT, DEFAULT_LIMIT_GB))
    except ValueError:
        gb = DEFAULT_LIMIT_GB
    return int(gb * 1024 ** 3)


def kind_dir(kind: str, base: str | None = None) -> str:
    path = os.path.join(base or root(), kind)
    os.makedirs(path, exist_ok=True)
    return path


# ------------------------------------------------------------------------------------------------------------------
# Atomic writes, clones, touch
# ------------------------------------------------------------------------------------------------------------------

def tmp_path(path: str) -> str:
    """A unique temp name next to path that keeps its extension (np.savez appends .npz otherwise)."""
    folder, name = os.path.split(path)
    return os.path.join(folder, "%s%d-%s-%s" % (TMP_PREFIX, os.getpid(), uuid.uuid4().hex[:8], name))


def write_atomic(path: str, writer) -> str:
    """writer(temp_path) writes the content; then it replaces path in one step."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = tmp_path(path)
    try:
        writer(tmp)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return path


def write_json(path: str, data) -> str:
    def dump(tmp):
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    return write_atomic(path, dump)


def write_text(path: str, text: str) -> str:
    def dump(tmp):
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
    return write_atomic(path, dump)


def read_json(path: str, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


_CLONEFILE = None


def _clonefile():
    global _CLONEFILE
    if _CLONEFILE is None:
        _CLONEFILE = False
        if sys.platform == "darwin":
            try:
                libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
                fn = libc.clonefile
                fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
                fn.restype = ctypes.c_int
                _CLONEFILE = fn
            except (OSError, AttributeError):
                pass
    return _CLONEFILE


def clone(src: str, dst: str) -> None:
    """Copy src to a not yet existing dst: an APFS clone where possible (no extra space, instant), else a copy."""
    fn = _clonefile()
    if fn and fn(os.fsencode(src), os.fsencode(dst), 0) == 0:
        return
    shutil.copyfile(src, dst)


def copy_atomic(src: str, dst: str) -> str:
    return write_atomic(dst, lambda tmp: clone(src, tmp))


def touch(path: str) -> None:
    try:
        os.utime(path, None)
    except OSError:
        pass


def get_file(entry: str, dst: str) -> bool:
    """Cached file -> dst (atomic). False when the entry is missing (pruned meanwhile counts as missing)."""
    try:
        copy_atomic(entry, dst)
    except FileNotFoundError:
        return False
    touch(entry)
    return True


def put_file(src: str, entry: str) -> str:
    return copy_atomic(src, entry)


def put_dir(entry: str, files: dict) -> bool:
    """Stores {relative name: source file} as one folder entry: built under a temp name, then renamed in one step.
    An entry that already exists is kept (same key = same content). Returns True when this call stored it."""
    if os.path.isdir(entry):
        touch(entry)
        return False
    tmp = tmp_path(entry)
    try:
        for rel, src in files.items():
            dst = os.path.join(tmp, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            clone(src, dst)
        try:
            os.rename(tmp, entry)
        except OSError:             # another run stored it first
            return False
        return True
    finally:
        if os.path.isdir(tmp):
            shutil.rmtree(tmp, ignore_errors=True)


def get_dir(entry: str, dst_dir: str, names) -> bool:
    """Copies the named files of a folder entry into dst_dir (each atomic). False when any is missing."""
    if not all(os.path.isfile(os.path.join(entry, n)) for n in names):
        return False
    try:
        for n in names:
            copy_atomic(os.path.join(entry, n), os.path.join(dst_dir, n))
    except FileNotFoundError:       # pruned while copying
        return False
    touch(entry)
    return True


# ------------------------------------------------------------------------------------------------------------------
# Checkout-bound state: which output files are current
# ------------------------------------------------------------------------------------------------------------------

class OutputCache:
    """<out>/cache/outputs.json: per output a key and the files it wrote with their sizes (+ a small result, e.g. the
    triangle counts for the report). fresh() is true when the key matches and every file is still there unchanged.
    Checkout-bound state (which files of this output are current), so it stays in the output; paths are relative to
    the output (1.20.2 - absolute paths broke a copied output). An absolute path from an older generator counts
    only inside this output.

    shared: the folder of the shared cache's "outputs" entries. A miss here is then filled from an entry
    <name>_<key> that another checkout stored (its files are copied into this output) - a new worktree does not
    export the FBX and viewer data again."""

    MANIFEST = "_manifest.json"

    def __init__(self, folder, base, shared=None):
        self.path = os.path.join(folder, "outputs.json") if folder else None
        self.base = os.path.abspath(base)
        self.shared = shared if self.path else None
        self.entries = read_json(self.path, {}) if self.path else {}
        self.hits = []
        self.shared_hits = []

    def _file(self, name):
        if os.path.isabs(name):
            inside = os.path.commonpath([self.base, os.path.abspath(name)]) == self.base
            return name if inside else None
        return os.path.join(self.base, name)

    def _entry(self, name, key):
        return os.path.join(self.shared, "%s_%s" % (name, key[:20]))

    def fresh(self, name, key):
        if not self.path:
            return None
        e = self.entries.get(name)
        if e and e["key"] == key:
            ok = True
            for f, size in e["files"].items():
                f = self._file(f)
                if not f or not os.path.exists(f) or os.path.getsize(f) != size:
                    ok = False
                    break
            if ok:
                self.hits.append(name)
                return e
        return self._from_shared(name, key)

    def _from_shared(self, name, key):
        if not self.shared:
            return None
        entry = self._entry(name, key)
        man = read_json(os.path.join(entry, self.MANIFEST))
        if not man or man.get("key") != key or not get_dir(entry, self.base, list(man["files"])):
            return None
        for d in man.get("dirs", []):       # folders the export owns (Forms, ViewerData): no stale files left over
            keep = {os.path.join(self.base, f) for f in man["files"]}
            for root_, _, names in os.walk(os.path.join(self.base, d)):
                for n in names:
                    if os.path.join(root_, n) not in keep:
                        _remove(os.path.join(root_, n))
        man = {k: v for k, v in man.items() if k != "dirs"}
        self.entries[name] = man
        write_json(self.path, self.entries)
        self.hits.append(name)
        self.shared_hits.append(name)
        return man

    def store(self, name, key, files, result=None):
        if not self.path:
            return
        found = []
        for f in files:
            if os.path.isdir(f):
                found += [os.path.join(d, n) for d, _, ns in os.walk(f) for n in ns]
            elif os.path.exists(f):
                found.append(f)
        rel = {os.path.relpath(f, self.base): f for f in sorted(found)}
        dirs = [os.path.relpath(f, self.base) for f in files if os.path.isdir(f)]
        self.entries[name] = {"key": key, "files": {r: os.path.getsize(f) for r, f in rel.items()}, "result": result}
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        write_json(self.path, self.entries)
        if self.shared and all(not r.startswith("..") and r != self.MANIFEST for r in rel):
            man = tmp_path(os.path.join(os.path.dirname(self.path), self.MANIFEST))
            try:
                write_json(man, dict(self.entries[name], dirs=dirs))
                put_dir(self._entry(name, key), dict(rel, **{self.MANIFEST: man}))
            finally:
                if os.path.exists(man):
                    os.remove(man)


# ------------------------------------------------------------------------------------------------------------------
# Size, pruning
# ------------------------------------------------------------------------------------------------------------------

def _tree_size(path: str) -> int:
    total = 0
    for d, _, names in os.walk(path):
        for n in names:
            try:
                total += os.lstat(os.path.join(d, n)).st_size
            except OSError:
                pass
    return total


def entries(base: str | None = None):
    """(path, size, last use) of every entry, plus stale temp files as entries with last use 0."""
    base = base or root()
    found = []
    if not os.path.isdir(base):
        return found
    for kind in sorted(os.listdir(base)):
        kdir = os.path.join(base, kind)
        if kind.startswith(".") or not os.path.isdir(kdir):
            continue
        for e in os.scandir(kdir):
            try:
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            size = _tree_size(e.path) if e.is_dir(follow_symlinks=False) else st.st_size
            used = st.st_mtime
            if e.name.startswith(TMP_PREFIX):
                if time.time() - used < STALE_TMP_SECONDS:
                    continue                # a write in progress
                used = 0.0
            found.append((e.path, size, used))
    return found


def usage(base: str | None = None) -> int:
    return sum(size for _, size, _ in entries(base))


class _Lock:
    """Non-blocking (prune: one at a time, skip otherwise) or blocking (migration) lock on a file in base."""

    def __init__(self, base, name, block):
        self.path, self.block, self.fh = os.path.join(base, name), block, None

    def __enter__(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.fh = open(self.path, "a")
        try:
            import fcntl
            fcntl.flock(self.fh, fcntl.LOCK_EX | (0 if self.block else fcntl.LOCK_NB))
        except BlockingIOError:
            self.fh.close()
            self.fh = None
        except ImportError:         # no fcntl (not macOS/Linux): no lock
            pass
        return self.fh is not None

    def __exit__(self, *exc):
        if self.fh:
            self.fh.close()


def _remove(path: str) -> None:
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path, ignore_errors=True)
    else:
        try:
            os.remove(path)
        except OSError:
            pass


def prune(base: str | None = None, limit: int | None = None, now: float | None = None,
          grace: float = GRACE_SECONDS) -> dict:
    """Removes the least recently used entries until the cache fits the limit. Entries used within grace stay even
    when that leaves the cache above the limit (another run may need them right now)."""
    base = base or root()
    limit = limit_bytes() if limit is None else limit
    now = time.time() if now is None else now
    with _Lock(base, ".prune.lock", block=False) as locked:
        if not locked:
            return {"skipped": True, "total": None, "removed": 0, "freed": 0}
        found = sorted(entries(base), key=lambda e: e[2])
        total = sum(size for _, size, _ in found)
        before, removed, freed = total, 0, 0
        for path, size, used in found:
            if total <= limit and used > 0:
                break
            if used > now - grace:
                continue
            _remove(path)
            total -= size
            removed += 1
            freed += size
        return {"skipped": False, "before": before, "total": total, "limit": limit, "removed": removed,
                "freed": freed}


# ------------------------------------------------------------------------------------------------------------------
# Migration of the per-output caches (generator <= 1.20.1: <out>/cache)
# ------------------------------------------------------------------------------------------------------------------

LEGACY_FILES = (("uv_", ".npz", "uv"), ("ao_", ".npz", "ao"), ("bake_", ".npz", "bake"))
LEGACY_DIRS = ("render", "qa", "clips")


def _adopt_file(src: str, dst: str, move: bool) -> bool:
    if os.path.exists(dst):
        if move:
            _remove(src)
        return False
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if move:
        try:
            os.rename(src, dst)
            return True
        except OSError:
            pass
    tmp = tmp_path(dst)
    try:
        clone(src, tmp)
        os.replace(tmp, dst)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    if move:
        _remove(src)
    return True


def _read_text(path: str):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip() or None
    except OSError:
        return None


def _adopt_folder(src_dir: str, entry: str, keep=lambda rel: True) -> int:
    """Clones every file of src_dir (recursively, no dot files) as one folder entry."""
    files = {}
    for d, _, names in os.walk(src_dir):
        for fn in names:
            rel = os.path.relpath(os.path.join(d, fn), src_dir)
            if not fn.startswith(".") and keep(rel):
                files[rel] = os.path.join(d, fn)
    return int(bool(files) and put_dir(entry, files))


def legacy_sources(base: str | None = None) -> list:
    """SourceTextures folders of adopted outputs. Up to 1.20.1 a bake key held the absolute path of each source
    texture; textures.bake_textures rebuilds such an old key to reuse a migrated bake byte for byte (a new bake
    differs slightly - Cycles - and every preview render would miss once)."""
    return read_json(os.path.join(base or root(), LEGACY_SOURCES), []) or []


def adopt_legacy(cache: str, base: str | None = None, move: bool = False) -> int:
    """Takes the entries of an old per-output cache folder into the shared cache: move (own output: the folder is
    ours and no longer used) or clone (another checkout's, which may still run an older generator). Entries already
    there are kept. Returns the number of adopted entries. outputs.json, timings.json (the time band of this output)
    and the skin working copies stay.
    Also clones the output's source textures (entry source/<stamp>) and the static textures of its last bake (entry
    textures/<bake key>): byte-identical inputs are what lets the next run reuse its renders."""
    base = base or root()
    if not os.path.isdir(cache):
        return 0
    n = 0
    out = os.path.dirname(os.path.abspath(cache))
    src = os.path.join(out, "SourceTextures")
    stamp = _read_text(os.path.join(src, ".make_textures.stamp"))
    legacy_bake = any(fn.startswith("bake_") and fn.endswith(".npz") for fn in os.listdir(cache))
    if stamp:
        n += _adopt_folder(src, os.path.join(base, "source", stamp[:20]))
        known = legacy_sources(base)
        if legacy_bake and src not in known:      # only a <= 1.20.1 cache has bake files in <out>/cache
            write_json(os.path.join(base, LEGACY_SOURCES), known + [src])
    tex = os.path.join(out, "Textures")
    key = _read_text(os.path.join(tex, ".bake.stamp"))
    if key and os.path.isfile(os.path.join(cache, "bake_%s.npz" % key[:20])):
        n += _adopt_folder(tex, os.path.join(base, "textures", key[:20]),
                           keep=lambda rel: not rel.startswith(("T_Robot_BaseColor", "T_Robot_Emission")))
    for fn in os.listdir(cache):
        for prefix, ext, kind in LEGACY_FILES:
            if fn.startswith(prefix) and fn.endswith(ext) and ".tmp" not in fn:
                n += _adopt_file(os.path.join(cache, fn), os.path.join(base, kind, fn), move)
    for kind in LEGACY_DIRS:
        folder = os.path.join(cache, kind)
        if not os.path.isdir(folder):
            continue
        for fn in os.listdir(folder):
            if not fn.endswith(".tmp") and not fn.startswith(("dump_", ".")):
                n += _adopt_file(os.path.join(folder, fn), os.path.join(base, kind, fn), move)
        if move:
            shutil.rmtree(folder, ignore_errors=True)
    skins = os.path.join(cache, "skin_textures")
    stamps = read_json(os.path.join(skins, "stamps.json"), {}) or {}
    # the default skin is the one without a preview copy: its maps are the deliverables in Textures/
    default = [k for k in stamps if not os.path.isfile(os.path.join(skins, "T_Robot_BaseColor_%s.png" % k))]
    for skin, stamp in stamps.items():
        for prefix, part in (("T_Robot_BaseColor", "base"), ("T_Robot_Emission", "emit")):
            src = os.path.join(skins, "%s_%s.png" % (prefix, skin))
            if default == [skin]:
                src = os.path.join(tex, "%s.png" % prefix)
            if os.path.isfile(src):                 # never move: working copies or deliverables of this output
                n += _adopt_file(src, os.path.join(base, "skins", "%s_%s.png" % (stamp, part)), False)
    outputs = read_json(os.path.join(cache, "outputs.json"), {}) or {}
    for name, e in outputs.items():                 # exported files (FBX, forms, viewer data) as shared entries
        files = {}
        for f, size in e.get("files", {}).items():
            f = f if os.path.isabs(f) else os.path.join(out, f)
            rel = os.path.relpath(f, out)
            if rel.startswith("..") or not os.path.isfile(f) or os.path.getsize(f) != size:
                files = None
                break
            files[rel] = f
        if files:
            man = tmp_path(os.path.join(base, "outputs", OutputCache.MANIFEST))
            os.makedirs(os.path.dirname(man), exist_ok=True)
            try:
                write_json(man, {"key": e["key"], "files": {r: os.path.getsize(f) for r, f in files.items()},
                                 "result": e.get("result")})
                n += put_dir(os.path.join(base, "outputs", "%s_%s" % (name, e["key"][:20])),
                             dict(files, **{OutputCache.MANIFEST: man}))
            finally:
                if os.path.exists(man):
                    os.remove(man)
    return n


def main_checkout(scripts_dir: str) -> str | None:
    """The main checkout of the repository scripts_dir belongs to (the folder of the shared .git)."""
    try:
        common = subprocess.run(["git", "-C", scripts_dir, "rev-parse", "--path-format=absolute", "--git-common-dir"],
                                capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return os.path.dirname(common) if common.endswith(".git") else None


LEGACY_OUTPUTS = ("output", "output/_quick", "output/_standard")


def prepare(out: str, scripts_dir: str, base: str | None = None, log=None) -> dict:
    """Once per run (main process): moves leftovers of this output's old cache into the shared one and, the first
    time, clones the main checkout's caches (its full, quick and standard outputs) so a new worktree starts warm."""
    base = base or root()
    os.makedirs(base, exist_ok=True)
    adopted = {}
    with _Lock(base, ".migrate.lock", block=True):
        own = os.path.join(out, "cache")
        n = adopt_legacy(own, base, move=True)
        if n:
            adopted[own] = n
        done = read_json(os.path.join(base, MIGRATED), []) or []
        main = main_checkout(scripts_dir)
        todo = [os.path.join(main, o, "cache") for o in LEGACY_OUTPUTS] if main else []
        new = [c for c in todo if os.path.realpath(c) not in done and os.path.realpath(c) != os.path.realpath(own)]
        for c in new:
            n = adopt_legacy(c, base, move=False)
            if n:
                adopted[c] = n
        if new:
            write_json(os.path.join(base, MIGRATED), sorted(set(done) | {os.path.realpath(c) for c in new}))
    if log:
        for c, n in adopted.items():
            log("  shared cache: adopted %d entries from %s" % (n, c))
    return adopted
