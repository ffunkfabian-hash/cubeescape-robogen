"""Shared base: imports, test tiers, arguments, logging, steps, scene helpers and cache digests."""

import argparse
import collections
import concurrent.futures
import hashlib
import inspect
import shutil
import json
import logging
import math
import mathutils
import os
import struct
import subprocess
import sys
import time
import traceback
import zlib

import bmesh
import bpy
import numpy as np
from bpy_extras import anim_utils
from mathutils import Euler, Matrix, Quaternion, Vector
from mathutils.bvhtree import BVHTree

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # scripts/ (this package lives in it)
GENERATOR = os.path.join(SCRIPTS_DIR, "generate_robot.py")
sys.path.insert(0, SCRIPTS_DIR)
import make_textures  # noqa: E402 - ImageMagick source textures
import export_viewer  # noqa: E402 - data for the RobotViewer app

LOG = logging.getLogger("robot")
VERSION = "1.18.2"

# ----------------------------------------------------------------------------------------------
# Arguments and logging
# ----------------------------------------------------------------------------------------------


# Test tiers: what a run checks and produces, and how long it may take (warning only).
TIERS = {
    "quick": {"tex_size": 512, "preview_samples": 0.25, "ao_samples": 16, "qa_types": "Classic", "no_render": True, "no_anim_sheets": True,
              "lods": False, "budget": 20},
    "standard": {"tex_size": 1024, "preview_samples": 0.25, "ao_samples": 32, "qa_types": "", "no_render": False, "no_anim_sheets": True,
                 "lods": True, "budget": 90},
    "full": {"tex_size": 2048, "preview_samples": 1.0, "ao_samples": 64, "qa_types": "", "no_render": False, "no_anim_sheets": False,
             "lods": True, "budget": 300},
}


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    here = SCRIPTS_DIR
    parser = argparse.ArgumentParser(description="Cube Escape robot generator")
    parser.add_argument("--tier", choices=list(TIERS), default="full",
                        help="test tier: quick (seconds), standard (about 2 min), full (deliverables)")
    parser.add_argument("--out", default=None, help="output folder (default: output, output/_quick, output/_standard)")
    parser.add_argument("--stage", choices=["model", "full"], default="full")
    parser.add_argument("--tex-size", type=int, default=None)
    parser.add_argument("--ao-samples", type=int, default=None)
    parser.add_argument("--no-render", action="store_true")
    parser.add_argument("--skip-bake", action="store_true", help="debug: keep procedural materials, no textures")
    parser.add_argument("--groups", default="", help="debug: only these animation groups (comma list), Basis is kept")
    parser.add_argument("--qa-types", default=None, help="QA only these robot types (comma list, Classic always)")
    parser.add_argument("--no-anim-sheets", action="store_true")
    parser.add_argument("--no-cache", action="store_true", help="bake and build everything again")
    parser.add_argument("--check-compose", action="store_true",
                        help="also bake the default skin directly and compare it with the composed one")
    parser.add_argument("--bake-device", choices=["auto", "cpu", "gpu"], default="auto",
                        help="Cycles device for baking (auto = GPU if there is one)")
    parser.add_argument("--workers", "--qa-workers", type=int, default=max(0, min(12, (os.cpu_count() or 4) - 1)),
                        help="worker processes for QA and preview renders besides the main process (default: cores - 1, "
                             "at most 12; 0 = everything in the main process)")
    parser.add_argument("--render-workers", type=int, default=3,
                        help="how many of the workers render (EEVEE on the one GPU; more than 3-4 hardly helps)")
    parser.add_argument("--worker-threads", type=int, default=int(os.environ.get("ROBOT_QA_THREADS", "1")),
                        help=argparse.SUPPRESS)
    parser.add_argument("--render-threads", type=int, default=int(os.environ.get("ROBOT_RENDER_THREADS", "2")),
                        help=argparse.SUPPRESS)
    # internal: worker mode
    parser.add_argument("--worker-queue", default="", help=argparse.SUPPRESS)
    parser.add_argument("--worker-lanes", default="qa", help=argparse.SUPPRESS)
    parser.add_argument("--worker-id", default="", help=argparse.SUPPRESS)
    parser.add_argument("--use-textures", default="", help=argparse.SUPPRESS)  # baked textures of the main process
    parser.add_argument("--uv-cache", default="", help=argparse.SUPPRESS)      # its UV cache
    parser.add_argument("--src-dir", default="", help=argparse.SUPPRESS)       # its source textures and Previews
    args = parser.parse_args(argv)
    tier = TIERS[args.tier]
    if args.out is None:
        args.out = os.path.normpath(os.path.join(here, "..", "output" if args.tier == "full" else "output/_" + args.tier))
    if args.tex_size is None:
        args.tex_size = tier["tex_size"]
    if args.ao_samples is None:
        args.ao_samples = tier["ao_samples"]
    if args.qa_types is None:
        args.qa_types = tier["qa_types"]
    args.no_render = args.no_render or tier["no_render"]
    args.no_anim_sheets = args.no_anim_sheets or tier["no_anim_sheets"]
    args.check_compose = args.check_compose or args.tier == "full"
    args.budget = tier["budget"]
    args.lods = tier["lods"]
    args.preview_samples = tier["preview_samples"]
    return args


def setup_logging(out_dir):
    log_dir = os.path.join(out_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    LOG.setLevel(logging.DEBUG)
    LOG.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S")
    fh = logging.FileHandler(os.path.join(log_dir, "generate_robot.log"), mode="w", encoding="utf-8")
    fh.setFormatter(fmt)
    fh.setLevel(logging.DEBUG)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    sh.setLevel(logging.INFO)
    LOG.addHandler(fh)
    LOG.addHandler(sh)


class _SkipBlock(Exception):
    pass


class _Skip:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return exc_type is _SkipBlock


STEP_TIMES = collections.OrderedDict()     # step name -> seconds, written into the report


class Step:
    """Context manager that logs duration and re-raises with a readable step name."""

    def __init__(self, name):
        self.name = name

    def __enter__(self):
        self.t0 = time.time()
        LOG.info("> %s", self.name)
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            dt = time.time() - self.t0
            STEP_TIMES[self.name] = round(dt, 2)
            LOG.info("  %s done (%.1fs)", self.name, dt)
            return False
        if exc_type is _SkipBlock:
            return True
        LOG.error("  %s FAILED: %s", self.name, exc)
        LOG.debug("".join(traceback.format_exception(exc_type, exc, tb)))
        return False


# ----------------------------------------------------------------------------------------------
# Scene
# ----------------------------------------------------------------------------------------------

FPS = 30


def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.preferences.filepaths.save_version = 0  # no .blend1 backups next to the deliverables
    scene = bpy.context.scene
    scene.name = "CubeEscape_Robot"
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 1.0
    scene.render.fps = FPS
    scene.render.fps_base = 1.0
    scene.frame_start = 0
    scene.frame_end = 120
    return scene


def get_collection(name, parent=None):
    coll = bpy.data.collections.get(name)
    if coll is None:
        coll = bpy.data.collections.new(name)
        (parent or bpy.context.scene.collection).children.link(coll)
    return coll


def code_source(name):
    """Source of a generator function or class by name (the code part of cache keys), in whichever robogen module
    defines it."""
    for key in sorted(sys.modules):
        mod = sys.modules[key]
        ob = vars(mod).get(name) if key.startswith("robogen.") else None
        if ob is not None and getattr(ob, "__module__", None) == key:
            return inspect.getsource(ob)
    raise KeyError(name)


def code_digest(names, *values):
    h = hashlib.sha1()
    for name in names:
        h.update(code_source(name).encode())
    h.update(repr(values).encode())
    return h.hexdigest()


# 1.17: run times judged against a band of earlier quiet runs of the same tier and cache state (like Cube Escape's
# perfdiff): quiet machine = within 30 % of the median, light load (other processes) = within 2.6 x, heavy load = not
# judged. Only quiet runs become the reference. The tier budget stays as the target in the report.
RUN_STATE = {"bake_hit": False, "clips_measured": None}


def mesh_digest(ob):
    me = ob.data
    h = hashlib.sha1()
    co = np.empty(len(me.vertices) * 3, dtype=np.float32)
    me.vertices.foreach_get("co", co)
    h.update(co.tobytes())
    # faces as sorted vertex sets: the start of a face's loop differs between Blender processes (same surface)
    faces = sorted(tuple(sorted(p.vertices)) for p in me.polygons)
    h.update(repr(faces).encode())
    h.update(repr([g.name for g in ob.vertex_groups]).encode())
    h.update(np.array([v.groups[0].group if v.groups else -1 for v in me.vertices], dtype=np.int32).tobytes())
    return h.hexdigest()


def rig_digest(arm_ob):
    h = hashlib.sha1()
    for b in arm_ob.data.bones:
        h.update(repr((b.name, b.parent.name if b.parent else None, tuple(b.head_local), tuple(b.tail_local),
                       tuple(map(tuple, b.matrix_local)))).encode())
    return h.hexdigest()


def action_digest(act):
    h = hashlib.sha1(repr((tuple(act.frame_range), act.use_cyclic)).encode())
    for slot in act.slots:
        cb = anim_utils.action_get_channelbag_for_slot(act, slot)
        for fc in (cb.fcurves if cb else []):
            co = np.empty(len(fc.keyframe_points) * 2, dtype=np.float32)
            fc.keyframe_points.foreach_get("co", co)
            h.update(("%s[%d]" % (fc.data_path, fc.array_index)).encode())
            h.update(co.tobytes())
    return h.hexdigest()


def write_json_atomic(path, data):
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)


__all__ = [_n for _n in dir() if not _n.startswith("__")]
