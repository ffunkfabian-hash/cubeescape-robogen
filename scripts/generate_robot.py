"""Cube Escape - robot character generator for Blender 5.2 LTS.

Builds the stylized low-poly robot from the Cube Escape key art completely from code:
geometry, procedural materials, UVs, baked textures, rig, ten animations, QA checks,
preview renders, .blend and a Unity-ready .fbx.

Usage (Blender CLI):
    Blender -b --factory-startup --python-exit-code 1 --python generate_robot.py -- \
        [--tier quick|standard|full] [--out /path/to/output] [--stage model|full] [--tex-size 2048] [--no-render]

Test tiers (see TIERS): quick = small textures, QA of the standard robot only, no renders (output/_quick);
standard = medium textures, full QA, still renders (output/_standard); full = deliverables (output). Every run
reports its step times in logs/robot_report.json and warns when the tier's time budget is exceeded.

Conventions (shared with the Cube Escape Blender pipeline):
    * 1 Blender unit = 1 m, robot stands on z = 0, origin between the feet.
    * The robot looks along -Y (Blender "Front" view). The FBX is exported with
      forward -Z / up Y, so in Unity it looks along +Z.
    * The robot's left side is +X. Bones are suffixed _L / _R.
"""

import argparse
import collections
import concurrent.futures
import hashlib
import inspect
import shutil
import json
import logging
import math
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import make_textures  # noqa: E402 - ImageMagick source textures
import export_viewer  # noqa: E402 - data for the RobotViewer app

LOG = logging.getLogger("robot")
VERSION = "1.8.0"

# ----------------------------------------------------------------------------------------------
# Arguments and logging
# ----------------------------------------------------------------------------------------------


# Test tiers: what a run checks and produces, and how long it may take (warning only).
TIERS = {
    "quick": {"tex_size": 512, "preview_samples": 0.25, "ao_samples": 16, "qa_types": "Classic", "no_render": True, "no_anim_sheets": True,
              "budget": 30},
    "standard": {"tex_size": 1024, "preview_samples": 0.25, "ao_samples": 32, "qa_types": "", "no_render": False, "no_anim_sheets": True,
                 "budget": 120},
    "full": {"tex_size": 2048, "preview_samples": 1.0, "ao_samples": 64, "qa_types": "", "no_render": False, "no_anim_sheets": False,
             "budget": 300},
}


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    here = os.path.dirname(os.path.abspath(__file__))
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
    parser.add_argument("--qa-workers", type=int, default=max(1, min(8, (os.cpu_count() or 4) - 2)),
                        help="parallel Blender processes for the type QA (default: cores - 2, at most 8)")
    parser.add_argument("--qa-worker-out", default="", help=argparse.SUPPRESS)   # internal: QA worker mode
    parser.add_argument("--src-dir", default="", help=argparse.SUPPRESS)         # internal: shared source textures
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


# ----------------------------------------------------------------------------------------------
# Materials (procedural authoring materials, later baked into one atlas material)
# ----------------------------------------------------------------------------------------------

MAT_ORDER = ["White", "Dark", "Grey", "Visor", "Orange", "Fabric", "EyeScreen", "Display"]
BODY_MATS = ["White", "Dark", "Grey", "Visor", "Orange", "Fabric"]
MAT_NAMES = {k: "M_Robot_" + k for k in MAT_ORDER}
CHANNELS = {}  # material key -> {"base", "rough", "metal", "emit"} output sockets

# Eye layout (also used by the eye shaders)
EYE_X, EYE_Z = 0.088, 1.117
EYE_W, EYE_H = 0.086, 0.080
GLOW_W, GLOW_H = 0.150, 0.136


class NodeKit:
    """Tiny helper to write shader node graphs compactly."""

    def __init__(self, mat):
        self.nt = mat.node_tree
        self.n = self.nt.nodes
        self.l = self.nt.links
        self.x = -1400

    def node(self, kind, **props):
        nd = self.n.new(kind)
        nd.location = (self.x, 0)
        self.x += 40
        for k, v in props.items():
            setattr(nd, k, v)
        return nd

    def link(self, a, b):
        self.l.new(a, b)

    def value(self, v):
        nd = self.node("ShaderNodeValue")
        nd.outputs[0].default_value = v
        return nd.outputs[0]

    def rgb(self, c):
        nd = self.node("ShaderNodeRGB")
        nd.outputs[0].default_value = (c[0], c[1], c[2], 1.0)
        return nd.outputs[0]

    def math(self, op, a, b=None, clamp=False):
        nd = self.node("ShaderNodeMath", operation=op, use_clamp=clamp)
        for i, v in enumerate((a, b)):
            if v is None:
                continue
            if isinstance(v, (int, float)):
                nd.inputs[i].default_value = v
            else:
                self.link(v, nd.inputs[i])
        return nd.outputs[0]

    def mix(self, fac, a, b):
        """Color mix; a/b are sockets or rgb tuples, fac socket or float."""
        nd = self.node("ShaderNodeMix", data_type="RGBA", clamp_factor=True)
        if isinstance(fac, (int, float)):
            nd.inputs[0].default_value = fac
        else:
            self.link(fac, nd.inputs[0])
        for idx, v in ((6, a), (7, b)):
            if isinstance(v, tuple):
                nd.inputs[idx].default_value = (v[0], v[1], v[2], 1.0)
            else:
                self.link(v, nd.inputs[idx])
        return nd.outputs[2]

    def scale_color(self, col, fac):
        nd = self.node("ShaderNodeMix", data_type="RGBA", blend_type="MULTIPLY", clamp_factor=True)
        nd.inputs[0].default_value = 1.0
        self.link(col, nd.inputs[6])
        if isinstance(fac, (int, float)):
            nd.inputs[7].default_value = (fac, fac, fac, 1.0)
        else:
            self.link(fac, nd.inputs[7])
        return nd.outputs[2]

    def coords(self):
        tc = self.node("ShaderNodeTexCoord")
        sep = self.node("ShaderNodeSeparateXYZ")
        self.link(tc.outputs["Object"], sep.inputs[0])
        return tc.outputs["Object"], sep.outputs[0], sep.outputs[1], sep.outputs[2]

    def noise(self, vec, scale, detail=3.0, rough=0.55):
        nd = self.node("ShaderNodeTexNoise")
        self.link(vec, nd.inputs["Vector"])
        nd.inputs["Scale"].default_value = scale
        nd.inputs["Detail"].default_value = detail
        nd.inputs["Roughness"].default_value = rough
        return nd.outputs["Fac"]

    def map_range(self, v, a, b, c=0.0, d=1.0, smooth=False):
        nd = self.node("ShaderNodeMapRange", interpolation_type="SMOOTHSTEP" if smooth else "LINEAR")
        self.link(v, nd.inputs[0])
        nd.inputs[1].default_value = a
        nd.inputs[2].default_value = b
        nd.inputs[3].default_value = c
        nd.inputs[4].default_value = d
        return nd.outputs[0]

    def stretch(self, vec, sx, sy, sz):
        nd = self.node("ShaderNodeVectorMath", operation="MULTIPLY")
        self.link(vec, nd.inputs[0])
        nd.inputs[1].default_value = (sx, sy, sz)
        return nd.outputs[0]

    def ellipse_dist(self, x, z, cx, cz, rx, rz, mirror_x=True):
        ax = self.math("ABSOLUTE", x) if mirror_x else x
        dx = self.math("DIVIDE", self.math("SUBTRACT", ax, cx), rx)
        dz = self.math("DIVIDE", self.math("SUBTRACT", z, cz), rz)
        return self.math("SQRT", self.math("ADD", self.math("MULTIPLY", dx, dx), self.math("MULTIPLY", dz, dz)))


def new_material(key):
    mat = bpy.data.materials.new(MAT_NAMES[key])
    if mat.node_tree is None:
        mat.use_nodes = True
    mat.node_tree.nodes.clear()
    kit = NodeKit(mat)
    bsdf = kit.n.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (300, 0)
    out = kit.n.new("ShaderNodeOutputMaterial")
    out.location = (600, 0)
    out.name = "Output"
    bsdf.name = "BSDF"
    kit.link(bsdf.outputs[0], out.inputs["Surface"])
    return mat, kit, bsdf


def finish_material(key, mat, kit, bsdf, base, rough, metal, emit, emit_strength=0.0, viewport=(0.8, 0.8, 0.8)):
    kit.link(base, bsdf.inputs["Base Color"])
    kit.link(rough, bsdf.inputs["Roughness"])
    kit.link(metal, bsdf.inputs["Metallic"])
    kit.link(emit, bsdf.inputs["Emission Color"])
    bsdf.inputs["Emission Strength"].default_value = emit_strength
    mat.diffuse_color = (viewport[0], viewport[1], viewport[2], 1.0)
    mat.use_fake_user = True
    CHANNELS[key] = {"base": base, "rough": rough, "metal": metal, "emit": emit}


# Skins: every colour of the robot comes from one palette. The generator bakes a BaseColor/Emission pair per
# skin and a colour mask (R = armour, G = mechanics, B = eyes) for free tinting at runtime.
SKINS = {
    "Classic": {
        "armor": (0.66, 0.685, 0.72), "mech": (0.075, 0.080, 0.092), "metal": (0.38, 0.395, 0.42),
        "eye": (0.02, 0.62, 0.95), "screen": (0.30, 1.00, 0.62), "accent": (1.00, 0.38, 0.05),
    },
    "Sunrise": {
        "armor": (0.86, 0.50, 0.12), "mech": (0.085, 0.080, 0.078), "metal": (0.42, 0.40, 0.38),
        "eye": (0.02, 0.62, 0.95), "screen": (0.30, 1.00, 0.62), "accent": (0.95, 0.95, 0.90),
    },
    "Midnight": {
        "armor": (0.13, 0.14, 0.165), "mech": (0.030, 0.032, 0.038), "metal": (0.22, 0.23, 0.26),
        "eye": (0.35, 1.00, 0.10), "screen": (0.35, 1.00, 0.10), "accent": (1.00, 0.20, 0.35),
    },
    "Rescue": {
        "armor": (0.78, 0.12, 0.10), "mech": (0.080, 0.080, 0.085), "metal": (0.80, 0.80, 0.82),
        "eye": (0.02, 0.62, 0.95), "screen": (0.30, 1.00, 0.62), "accent": (1.00, 0.90, 0.20),
    },
    "Arctic": {
        "armor": (0.62, 0.80, 0.98), "mech": (0.10, 0.22, 0.42), "metal": (0.70, 0.80, 0.90),
        "eye": (0.35, 0.85, 1.00), "screen": (0.50, 0.90, 1.00), "accent": (0.40, 0.80, 1.00),
    },
    "Jungle": {
        "armor": (0.30, 0.36, 0.18), "mech": (0.100, 0.090, 0.060), "metal": (0.40, 0.36, 0.26),
        "eye": (1.00, 0.85, 0.10), "screen": (0.90, 0.85, 0.20), "accent": (1.00, 0.55, 0.10),
    },
    "Neon": {
        "armor": (0.06, 0.06, 0.075), "mech": (0.020, 0.020, 0.025), "metal": (0.16, 0.16, 0.20),
        "eye": (1.00, 0.10, 0.75), "screen": (0.10, 0.95, 1.00), "accent": (0.20, 1.00, 0.90),
    },
    "Gold": {
        "armor": (0.95, 0.60, 0.10), "mech": (0.100, 0.080, 0.060), "metal": (0.80, 0.55, 0.18),
        "eye": (1.00, 0.95, 0.75), "screen": (1.00, 0.80, 0.40), "accent": (1.00, 0.45, 0.10),
    },
    "Mint": {
        "armor": (0.55, 0.85, 0.72), "mech": (0.20, 0.22, 0.28), "metal": (0.80, 0.80, 0.85),
        "eye": (1.00, 0.35, 0.60), "screen": (1.00, 0.50, 0.75), "accent": (1.00, 0.75, 0.20),
    },
    "Copper": {
        "armor": (0.72, 0.38, 0.20), "mech": (0.070, 0.060, 0.055), "metal": (0.55, 0.45, 0.35),
        "eye": (0.30, 1.00, 0.35), "screen": (0.30, 1.00, 0.35), "accent": (1.00, 0.60, 0.15),
    },
    "Royal": {
        "armor": (0.10, 0.18, 0.48), "mech": (0.050, 0.050, 0.080), "metal": (0.80, 0.65, 0.30),
        "eye": (0.50, 0.80, 1.00), "screen": (0.90, 0.75, 0.30), "accent": (1.00, 0.80, 0.25),
    },
    "Lava": {
        "armor": (0.20, 0.07, 0.05), "mech": (0.030, 0.020, 0.020), "metal": (0.30, 0.20, 0.15),
        "eye": (1.00, 0.35, 0.02), "screen": (1.00, 0.45, 0.05), "accent": (1.00, 0.25, 0.02), "fabric": (0.90, 0.30, 0.05),
    },
    "Ocean": {
        "armor": (0.05, 0.35, 0.55), "mech": (0.020, 0.080, 0.140), "metal": (0.45, 0.60, 0.70),
        "eye": (0.30, 1.00, 0.85), "screen": (0.20, 0.90, 1.00), "accent": (0.10, 1.00, 0.70), "fabric": (0.95, 0.85, 0.40),
    },
    "Bubblegum": {
        "armor": (0.95, 0.45, 0.70), "mech": (0.35, 0.12, 0.40), "metal": (0.85, 0.75, 0.90),
        "eye": (0.35, 0.95, 1.00), "screen": (1.00, 0.60, 0.90), "accent": (0.60, 1.00, 0.95), "fabric": (0.45, 0.20, 0.85),
    },
    "Toxic": {
        "armor": (0.05, 0.06, 0.05), "mech": (0.020, 0.030, 0.020), "metal": (0.20, 0.25, 0.18),
        "eye": (0.55, 1.00, 0.05), "screen": (0.55, 1.00, 0.05), "accent": (0.70, 1.00, 0.00), "fabric": (0.40, 0.90, 0.05),
    },
    "Sand": {
        "armor": (0.75, 0.62, 0.42), "mech": (0.20, 0.15, 0.10), "metal": (0.55, 0.48, 0.38),
        "eye": (0.20, 0.75, 1.00), "screen": (1.00, 0.75, 0.35), "accent": (1.00, 0.45, 0.15), "fabric": (0.60, 0.25, 0.12),
    },
    "Cherry": {
        "armor": (0.85, 0.05, 0.12), "mech": (0.92, 0.92, 0.90), "metal": (0.75, 0.75, 0.78),
        "eye": (1.00, 0.95, 0.90), "screen": (1.00, 0.40, 0.50), "accent": (1.00, 0.85, 0.90), "fabric": (0.95, 0.95, 0.95),
    },
    "Vapor": {
        "armor": (0.35, 0.15, 0.65), "mech": (0.050, 0.030, 0.120), "metal": (0.55, 0.45, 0.75),
        "eye": (0.10, 1.00, 1.00), "screen": (1.00, 0.30, 0.85), "accent": (1.00, 0.30, 0.85), "fabric": (0.10, 0.90, 1.00),
    },
    "Noir": {
        "armor": (0.03, 0.03, 0.035), "mech": (0.85, 0.85, 0.85), "metal": (0.85, 0.70, 0.35),
        "eye": (1.00, 1.00, 1.00), "screen": (0.90, 0.90, 0.90), "accent": (1.00, 0.80, 0.30), "fabric": (0.70, 0.05, 0.08),
    },
    "Forest": {
        "armor": (0.12, 0.30, 0.12), "mech": (0.080, 0.060, 0.040), "metal": (0.45, 0.35, 0.22),
        "eye": (1.00, 0.85, 0.30), "screen": (0.60, 1.00, 0.30), "accent": (1.00, 0.60, 0.10), "fabric": (0.75, 0.15, 0.10),
    },
}
DEFAULT_SKIN = "Classic"
# colour masks: ColorMask RGB = armour, mechanics, metal; LightMask = lights; visor = 1 - sum (body faces)
MASKS = {"White": (1, 0, 0, 0), "Dark": (0, 1, 0, 0), "Grey": (0, 0, 1, 0), "Visor": (0, 0, 0, 0),
         "Orange": (0, 0, 0, 1), "Fabric": (0, 0, 0, 0)}
PALETTE_DEFAULTS = {"visor": (0.006, 0.007, 0.010), "fabric": (0.78, 0.10, 0.12)}
FABRIC_COLORS = {"Sunrise": (0.10, 0.16, 0.40), "Midnight": (0.10, 0.85, 0.95), "Rescue": (0.95, 0.95, 0.92),
                 "Arctic": (0.10, 0.25, 0.60), "Jungle": (0.55, 0.30, 0.10), "Neon": (1.00, 0.10, 0.75),
                 "Gold": (0.45, 0.05, 0.10), "Mint": (1.00, 0.45, 0.65), "Copper": (0.10, 0.35, 0.30),
                 "Royal": (0.85, 0.65, 0.20)}
for _name, _pal in SKINS.items():
    _pal.setdefault("visor", PALETTE_DEFAULTS["visor"])
    _pal.setdefault("fabric", FABRIC_COLORS.get(_name, PALETTE_DEFAULTS["fabric"]))

PALETTE_NODES = []  # (node, palette key)


def pal(kit, key, factor=1.0):
    """Palette colour socket (optionally scaled). Nodes are re-coloured per skin before baking."""
    nd = kit.node("ShaderNodeRGB")
    nd.label = "PAL_" + key
    nd.outputs[0].default_value = (*SKINS[DEFAULT_SKIN][key], 1.0)
    PALETTE_NODES.append((nd, key))
    return nd.outputs[0] if factor == 1.0 else kit.scale_color(nd.outputs[0], factor)


def apply_skin(name):
    for nd, key in PALETTE_NODES:
        nd.outputs[0].default_value = (*SKINS[name][key], 1.0)


SRC = {}  # ImageMagick source images (bpy.data.images), filled by load_source_textures


def load_source_textures(paths):
    for key, path in paths.items():
        img = bpy.data.images.load(path, check_existing=True)
        img.colorspace_settings.name = "Non-Color"
        SRC[key] = img


def box_tex(k, key, vec, scale):
    """Tileable ImageMagick texture, box-projected in object space (1 tile = 1/scale m)."""
    nd = k.node("ShaderNodeTexImage", projection="BOX", projection_blend=0.3, interpolation="Cubic")
    nd.image = SRC[key]
    k.link(k.stretch(vec, scale, scale, scale), nd.inputs["Vector"])
    return nd.outputs["Color"]


def decal(k, us, vs, rect, px, extra_mask=None):
    """Projects a region of the ImageMagick decal atlas onto the plane (us, vs) inside rect (u0, u1, v0, v1)."""
    u0, u1, v0, v1 = rect
    x, y, w, h = px
    size = float(make_textures.SIZE)
    u = k.math("DIVIDE", k.math("SUBTRACT", us, u0), u1 - u0)
    v = k.math("DIVIDE", k.math("SUBTRACT", vs, v0), v1 - v0)
    inside = k.math("MULTIPLY", k.math("MULTIPLY", k.math("GREATER_THAN", u, 0.0), k.math("LESS_THAN", u, 1.0)),
                    k.math("MULTIPLY", k.math("GREATER_THAN", v, 0.0), k.math("LESS_THAN", v, 1.0)))
    au = k.math("ADD", k.math("MULTIPLY", u, w / size), x / size)
    av = k.math("ADD", k.math("MULTIPLY", v, h / size), 1.0 - (y + h) / size)
    comb = k.node("ShaderNodeCombineXYZ")
    k.link(au, comb.inputs[0])
    k.link(av, comb.inputs[1])
    tex = k.node("ShaderNodeTexImage", extension="CLIP", interpolation="Cubic")
    tex.image = SRC["decals"]
    k.link(comb.outputs[0], tex.inputs["Vector"])
    mask = k.math("MULTIPLY", tex.outputs["Alpha"], inside)
    if extra_mask is not None:
        mask = k.math("MULTIPLY", mask, extra_mask)
    return tex.outputs["Color"], mask


def bump(k, bsdf, height, strength, distance=0.002):
    nd = k.node("ShaderNodeBump")
    nd.inputs["Strength"].default_value = strength
    nd.inputs["Distance"].default_value = distance
    k.link(height, nd.inputs["Height"])
    k.link(nd.outputs["Normal"], bsdf.inputs["Normal"])


def surface(k, scale):
    """Shared wear layers from the ImageMagick textures."""
    vec, x, y, z = k.coords()
    g = k.map_range(box_tex(k, "grunge", vec, scale), 0.15, 0.85)
    br = k.map_range(box_tex(k, "brushed", vec, scale * 1.6), 0.45, 1.0)
    sc = k.map_range(box_tex(k, "scratches", vec, scale * 1.3), 0.2, 0.75)
    return vec, x, y, z, g, br, sc


def build_materials():
    mats = {}
    atlas = make_textures.ATLAS

    # Armour: painted metal - grime, brushed streaks, scratches showing bare metal, painted decals.
    mat, k, b = new_material("White")
    vec, x, y, z, g, br, sc = surface(k, 1.6)
    base = k.mix(g, pal(k, "armor", 0.84), pal(k, "armor"))
    base = k.mix(k.math("MULTIPLY", br, 0.22), base, pal(k, "armor", 1.14))
    wear = k.math("MULTIPLY", sc, 0.55)
    base = k.mix(wear, base, (0.80, 0.81, 0.83))
    ax = k.math("ABSOLUTE", x)
    _, m_emblem = decal(k, y, z, (-0.066, 0.066, 0.780, 0.912), atlas["emblem"], k.math("GREATER_THAN", ax, 0.34))
    _, m_code = decal(k, k.math("MULTIPLY", x, -1.0), z, (-0.080, 0.080, 1.012, 1.052), atlas["code"],
                      k.math("GREATER_THAN", y, 0.15))
    sx, sy, sw, sh = atlas["stripes"]
    _, m_stripes = decal(k, k.math("MULTIPLY", x, -1.0), z, (-0.112, 0.112, 0.673, 0.701), (sx, sy, sw, sh // 2),
                         k.math("GREATER_THAN", y, 0.20))
    paint = k.math("MAXIMUM", k.math("MAXIMUM", m_emblem, m_code), m_stripes)
    paint = k.math("MULTIPLY", paint, k.math("SUBTRACT", 1.0, k.math("MULTIPLY", wear, 0.8)))
    base = k.mix(paint, base, pal(k, "mech", 1.4))
    rough = k.math("ADD", k.math("MULTIPLY", g, 0.14), 0.27)
    rough = k.math("SUBTRACT", rough, k.math("ADD", k.math("MULTIPLY", br, 0.05), k.math("MULTIPLY", wear, 0.10)))
    metal = k.math("ADD", 0.42, k.math("MULTIPLY", wear, 0.45))
    bump(k, b, k.math("SUBTRACT", k.math("MULTIPLY", g, 0.25), k.math("MULTIPLY", sc, 0.9)), 0.35)
    finish_material("White", mat, k, b, base, rough, metal, k.rgb((0, 0, 0)), viewport=(0.66, 0.68, 0.72))
    mats["White"] = mat

    # Anthracite mechanics.
    mat, k, b = new_material("Dark")
    vec, x, y, z, g, br, sc = surface(k, 2.2)
    base = k.mix(g, pal(k, "mech", 0.6), pal(k, "mech", 1.1))
    base = k.mix(k.math("MULTIPLY", sc, 0.35), base, pal(k, "metal"))
    rough = k.math("ADD", k.math("MULTIPLY", g, 0.16), 0.36)
    bump(k, b, k.math("SUBTRACT", k.math("MULTIPLY", g, 0.3), k.math("MULTIPLY", sc, 0.5)), 0.3)
    finish_material("Dark", mat, k, b, base, rough, k.value(0.70), k.rgb((0, 0, 0)), viewport=(0.06, 0.065, 0.075))
    mats["Dark"] = mat

    # Mid grey metal for hands, ear caps and small mechanics.
    mat, k, b = new_material("Grey")
    vec, x, y, z, g, br, sc = surface(k, 2.6)
    base = k.mix(g, pal(k, "metal", 0.78), pal(k, "metal"))
    base = k.mix(k.math("MULTIPLY", br, 0.25), base, pal(k, "metal", 1.2))
    rough = k.math("ADD", k.math("MULTIPLY", g, 0.10), 0.30)
    bump(k, b, k.math("SUBTRACT", k.math("MULTIPLY", g, 0.2), k.math("MULTIPLY", sc, 0.6)), 0.3)
    finish_material("Grey", mat, k, b, base, rough, k.value(0.60), k.rgb((0, 0, 0)), viewport=(0.34, 0.35, 0.38))
    mats["Grey"] = mat

    # Glossy black face visor.
    mat, k, b = new_material("Visor")
    finish_material("Visor", mat, k, b, k.rgb((0.006, 0.007, 0.010)), k.value(0.07), k.value(0.15),
                    k.rgb((0, 0, 0)), viewport=(0.01, 0.01, 0.012))
    mats["Visor"] = mat

    # Status lights.
    mat, k, b = new_material("Orange")
    accent = pal(k, "accent")
    finish_material("Orange", mat, k, b, accent, k.value(0.25), k.value(0.0), accent, emit_strength=5.0,
                    viewport=(1.0, 0.45, 0.1))
    mats["Orange"] = mat

    # Fabric (accessories): baked light grey with weave grime, tinted per look by the fabric colour.
    mat, k, b = new_material("Fabric")
    vec, x, y, z, g, br, sc = surface(k, 3.0)
    base = k.mix(g, (0.62, 0.62, 0.62), (0.80, 0.80, 0.80))
    finish_material("Fabric", mat, k, b, base, k.value(0.75), k.value(0.0), k.rgb((0, 0, 0)), viewport=(0.8, 0.1, 0.1))
    mats["Fabric"] = mat

    mats["EyeScreen"] = build_eye_material()
    mats["Display"] = build_display_material()

    for key in BODY_MATS:
        kit = NodeKit(mats[key])
        m = MASKS[key]
        CHANNELS[key]["mask"] = kit.rgb(m[:3])
        CHANNELS[key]["mask_a"] = kit.value(m[3])
    return mats


EXPRESSION_INDEX = {n: i for i, n in enumerate(make_textures.EXPRESSIONS)}
DISPLAY_INDEX = {n: i for i, n in enumerate(make_textures.DISPLAYS)}


def atlas_cell(kind, index):
    """(scale_u, scale_v, offset_u, offset_v) of an atlas cell in Blender/Unity UV space (origin bottom-left)."""
    if kind == "eyes":
        cols, rows = make_textures.EYE_GRID
        cw, ch, H = 1.0 / cols, 1.0 / rows, 1.0
        col, row = index % cols, index // cols
        return cw, ch, col * cw, 1.0 - (row + 1) * ch
    cols, rows = make_textures.DISPLAY_GRID
    cw, ch = 1.0 / cols, make_textures.DISPLAY_CELL[1] / float(make_textures.DISPLAY_ATLAS_H)
    col, row = index % cols, index // cols
    return cw, ch, col * cw, 1.0 - (row + 1) * ch


def _atlas_uv(nt, kind, index):
    su, sv, ou, ov = atlas_cell(kind, index)
    uv = nt.nodes.new("ShaderNodeUVMap")
    uv.uv_map = "UVMap"
    mp = nt.nodes.new("ShaderNodeMapping")
    mp.name = "AtlasCell"
    mp.inputs["Scale"].default_value = (su, sv, 1.0)
    mp.inputs["Location"].default_value = (ou, ov, 0.0)
    nt.links.new(uv.outputs[0], mp.inputs["Vector"])
    return mp


def set_atlas_cell(mat, kind, index):
    su, sv, ou, ov = atlas_cell(kind, index)
    mp = mat.node_tree.nodes["AtlasCell"]
    mp.inputs["Scale"].default_value = (su, sv, 1.0)
    mp.inputs["Location"].default_value = (ou, ov, 0.0)


def build_eye_material(name="M_RobotEyes", color=(0.02, 0.62, 0.95), expression="Normal"):
    """Additive emissive eye screen: atlas luminance x eye colour, bright cores turn white."""
    mat = bpy.data.materials.new(name)
    if mat.node_tree is None:
        mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    mp = _atlas_uv(nt, "eyes", EXPRESSION_INDEX[expression])
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = SRC["eyes"]
    tex.extension = "CLIP"
    nt.links.new(mp.outputs[0], tex.inputs["Vector"])
    col = nt.nodes.new("ShaderNodeRGB")
    col.name = "EyeColor"
    col.outputs[0].default_value = (*color, 1.0)
    mul = nt.nodes.new("ShaderNodeMix")
    mul.data_type, mul.blend_type = "RGBA", "MULTIPLY"
    mul.inputs[0].default_value = 1.0
    nt.links.new(tex.outputs["Color"], mul.inputs[6])
    nt.links.new(col.outputs[0], mul.inputs[7])
    core = nt.nodes.new("ShaderNodeMath")
    core.operation = "POWER"
    core.inputs[1].default_value = 6.0
    nt.links.new(tex.outputs["Color"], core.inputs[0])
    add = nt.nodes.new("ShaderNodeMix")
    add.data_type, add.blend_type = "RGBA", "ADD"
    nt.links.new(core.outputs[0], add.inputs[0])
    nt.links.new(mul.outputs[2], add.inputs[6])
    add.inputs[7].default_value = (0.8, 0.8, 0.8, 1.0)
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Strength"].default_value = 4.0
    nt.links.new(add.outputs[2], em.inputs["Color"])
    tr = nt.nodes.new("ShaderNodeBsdfTransparent")
    addsh = nt.nodes.new("ShaderNodeAddShader")
    nt.links.new(tr.outputs[0], addsh.inputs[0])
    nt.links.new(em.outputs[0], addsh.inputs[1])
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.name = "Output"
    nt.links.new(addsh.outputs[0], out.inputs["Surface"])
    try:
        mat.surface_render_method = "BLENDED"
    except (AttributeError, TypeError):
        pass
    mat.use_fake_user = True
    mat.diffuse_color = (*color, 1.0)
    return mat


def build_display_material(name="M_RobotDisplay", color=(0.30, 1.00, 0.62), motif="Status"):
    mat = bpy.data.materials.new(name)
    if mat.node_tree is None:
        mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    mp = _atlas_uv(nt, "displays", DISPLAY_INDEX[motif])
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = SRC["displays"]
    tex.extension = "CLIP"
    nt.links.new(mp.outputs[0], tex.inputs["Vector"])
    col = nt.nodes.new("ShaderNodeRGB")
    col.name = "DisplayColor"
    col.outputs[0].default_value = (*color, 1.0)
    mix = nt.nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    nt.links.new(tex.outputs["Color"], mix.inputs[0])
    mix.inputs[6].default_value = (0.01, 0.025, 0.02, 1.0)
    nt.links.new(col.outputs[0], mix.inputs[7])
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.name = "BSDF"
    bsdf.inputs["Base Color"].default_value = (0.01, 0.02, 0.015, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.15
    nt.links.new(mix.outputs[2], bsdf.inputs["Emission Color"])
    bsdf.inputs["Emission Strength"].default_value = 3.0
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.name = "Output"
    nt.links.new(bsdf.outputs[0], out.inputs["Surface"])
    mat.use_fake_user = True
    return mat


# ----------------------------------------------------------------------------------------------
# Geometry kit (bmesh, world coordinates, every part rigidly bound to one bone)
# ----------------------------------------------------------------------------------------------

PARTS = []  # dicts: name, bone, mat, bm


def add_part(name, bm, mat, bone):
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    slot, var = VARIANT
    PARTS.append({"name": name, "bone": bone, "mat": mat, "bm": bm, "slot": slot, "variant": var,
                  "group": group_key(slot, var)})
    return bm


def to_matrix(rot):
    if rot is None:
        return Matrix.Identity(3)
    if isinstance(rot, Matrix):
        return rot.to_3x3()
    if isinstance(rot, Quaternion):
        return rot.to_matrix()
    return Euler([math.radians(a) for a in rot], "XYZ").to_matrix()


def place(bm, center, rot=None):
    m = Matrix.Translation(Vector(center)) @ to_matrix(rot).to_4x4()
    bmesh.ops.transform(bm, matrix=m, verts=bm.verts[:])


def bevel(bm, edges, r, segs):
    if r <= 0 or not edges:
        return
    verts = list({v for e in edges for v in e.verts})
    bmesh.ops.bevel(bm, geom=verts + list(edges), offset=r, offset_type="OFFSET", segments=segs,
                    profile=0.5, affect="EDGES", clamp_overlap=True)


def frame_from_dir(d, side=Vector((0, 1, 0))):
    """3x3 frame whose Z axis points along d (Y stays close to 'side')."""
    z = Vector(d).normalized()
    x = side.cross(z)
    if x.length < 1e-6:
        x = Vector((1, 0, 0)).cross(z)
    x.normalize()
    y = z.cross(x)
    return Matrix((x, y, z)).transposed()


def rbox(name, size, center, mat, bone, r=0.02, segs=2, taper_top=(1, 1), taper_bot=(1, 1), rot=None,
         deform=None):
    """Rounded (optionally tapered) box. Taper scales the +Z / -Z face in local X/Y."""
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    for v in bm.verts:
        tx, ty = taper_top if v.co.z > 0 else taper_bot
        v.co = Vector((v.co.x * size[0] * tx, v.co.y * size[1] * ty, v.co.z * size[2]))
    r = min(r, 0.49 * min(size[0] * min(taper_top[0], taper_bot[0]), size[1] * min(taper_top[1], taper_bot[1]), size[2]))
    bevel(bm, bm.edges[:], r, segs)
    if deform:
        for v in bm.verts:
            v.co = deform(v.co.copy())
    place(bm, center, rot)
    return add_part(name, bm, mat, bone)


def rrect(w, h, r, seg=4):
    """Rounded rectangle outline (counter-clockwise)."""
    r = min(r, w * 0.5 - 1e-4, h * 0.5 - 1e-4)
    pts = []
    corners = [(w / 2 - r, h / 2 - r, 0), (-w / 2 + r, h / 2 - r, 90), (-w / 2 + r, -h / 2 + r, 180),
               (w / 2 - r, -h / 2 + r, 270)]
    for cx, cy, a0 in corners:
        for i in range(seg + 1):
            a = math.radians(a0 + 90.0 * i / seg)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def ellipse(w, h, n=20):
    return [(w / 2 * math.cos(2 * math.pi * i / n), h / 2 * math.sin(2 * math.pi * i / n)) for i in range(n)]


def prism(name, pts, depth, axis, center, mat, bone, r=0.0, segs=2, back_scale=1.0, rot=None, deform=None,
          cuts_x=(), world_deform=None):
    """Extrudes a 2D outline. axis 'Y': outline in XZ (front view), 'Z': outline in XY, 'X': outline in YZ."""

    def P(u, v, w):
        if axis == "Y":
            return Vector((u, w, v))
        if axis == "X":
            return Vector((w, u, v))
        return Vector((u, v, w))

    bm = bmesh.new()
    front = [bm.verts.new(P(u, v, -depth / 2)) for u, v in pts]
    back = [bm.verts.new(P(u * back_scale, v * back_scale, depth / 2)) for u, v in pts]
    bm.faces.new(front)
    bm.faces.new(back)
    n = len(pts)
    for i in range(n):
        j = (i + 1) % n
        bm.faces.new((front[i], front[j], back[j], back[i]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    fset = set(front)
    ring = [e for e in bm.edges if (e.verts[0] in fset) == (e.verts[1] in fset)]
    bevel(bm, ring, min(r, depth * 0.49), segs)
    for cx in cuts_x:
        geom = bm.verts[:] + bm.edges[:] + bm.faces[:]
        bmesh.ops.bisect_plane(bm, geom=geom, plane_co=(cx, 0, 0), plane_no=(1, 0, 0))
    if deform:
        for v in bm.verts:
            v.co = deform(v.co.copy())
    place(bm, center, rot)
    if world_deform:
        for v in bm.verts:
            v.co = world_deform(v.co.copy())
    return add_part(name, bm, mat, bone)


VISOR_BEND = 0.55


def visor_bend(co):
    """Wraps face parts around the rounded head (y grows with x^2)."""
    co.y += VISOR_BEND * co.x * co.x
    return co


def cylinder(name, r, length, center, mat, bone, axis=(0, 0, 1), segs=12, r2=None, bev=0.0, bsegs=2,
             scale=(1, 1, 1)):
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=segs, radius1=r,
                          radius2=r if r2 is None else r2, depth=length)
    ring = [e for e in bm.edges if abs(e.verts[0].co.z - e.verts[1].co.z) < 1e-6]
    bevel(bm, ring, min(bev, length * 0.45), bsegs)
    for v in bm.verts:
        v.co = Vector((v.co.x * scale[0], v.co.y * scale[1], v.co.z * scale[2]))
    q = Vector((0, 0, 1)).rotation_difference(Vector(axis).normalized())
    place(bm, center, q)
    return add_part(name, bm, mat, bone)


def cyl_between(name, a, b, r, mat, bone, segs=12, r2=None, bev=0.0):
    a, b = Vector(a), Vector(b)
    return cylinder(name, r, (b - a).length, (a + b) / 2, mat, bone, axis=b - a, segs=segs, r2=r2, bev=bev)


def torus(name, R, r, center, mat, bone, axis=(0, 0, 1), segs=24, rsegs=8, arc=360.0):
    """Ring (optionally an open arc in the local XY plane starting at +X), local Z mapped to axis."""
    bm = bmesh.new()
    closed = arc >= 359.9
    n = segs if closed else segs + 1
    rings = []
    for i in range(n):
        a = math.radians(arc * i / segs)
        ring = []
        for j in range(rsegs):
            b = 2 * math.pi * j / rsegs
            rr = R + r * math.cos(b)
            ring.append(bm.verts.new((rr * math.cos(a), rr * math.sin(a), r * math.sin(b))))
        rings.append(ring)
    for i in range(segs):
        r0, r1 = rings[i], rings[(i + 1) % n]
        for j in range(rsegs):
            bm.faces.new((r0[j], r0[(j + 1) % rsegs], r1[(j + 1) % rsegs], r1[j]))
    if not closed:
        bm.faces.new(rings[0])
        bm.faces.new(rings[-1])
    q = Vector((0, 0, 1)).rotation_difference(Vector(axis).normalized())
    place(bm, center, q)
    return add_part(name, bm, mat, bone)


def sphere(name, r, center, mat, bone, segs=14, rings=8, scale=(1, 1, 1)):
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=segs, v_segments=rings, radius=r)
    for v in bm.verts:
        v.co = Vector((v.co.x * scale[0], v.co.y * scale[1], v.co.z * scale[2]))
    place(bm, center)
    return add_part(name, bm, mat, bone)


# ----------------------------------------------------------------------------------------------
# Robot layout (meters). All joint positions live here so geometry and rig agree.
# ----------------------------------------------------------------------------------------------

J = {}  # joint name -> Vector
LEG_X = 0.120
SHOULDER_X = 0.282


def define_joints():
    J["root"] = Vector((0, 0, 0))
    J["pelvis"] = Vector((0, 0, 0.470))
    J["spine"] = Vector((0, 0, 0.550))
    J["neck"] = Vector((0, 0, 0.900))
    J["head_top"] = Vector((0, 0, 1.300))
    J["eyes"] = Vector((0, -0.215, EYE_Z))
    for s, side in ((1, "L"), (-1, "R")):
        J["hip_" + side] = Vector((s * LEG_X, 0, 0.405))
        J["knee_" + side] = Vector((s * LEG_X, 0, 0.245))
        J["ankle_" + side] = Vector((s * LEG_X, 0, 0.095))
        J["toe_" + side] = Vector((s * LEG_X, -0.165, 0.030))
        J["clav_" + side] = Vector((s * 0.070, 0, 0.830))
        sh = Vector((s * SHOULDER_X, 0, 0.830))
        d1 = Vector((s * math.sin(math.radians(8)), 0, -math.cos(math.radians(8)))).normalized()
        el = sh + d1 * 0.165
        d2 = Vector((s * math.sin(math.radians(5)), -math.sin(math.radians(10)), -1.0)).normalized()
        wr = el + d2 * 0.165
        J["shoulder_" + side] = sh
        J["elbow_" + side] = el
        J["wrist_" + side] = wr
        J["dir_upper_" + side] = d1
        J["dir_fore_" + side] = d2
        J["knuckle_" + side] = wr + d2 * 0.090


def foot_deform(co):
    """Toe slope: lowers the top surface towards the toe tip (local foot coordinates)."""
    if co.z > 0.0:
        t = max(0.0, min(1.0, (-0.060 - co.y) / 0.13))
        t = t * t * (3 - 2 * t)
        co.z -= (co.z + 0.04) * 0.32 * t
    return co


# ------------------------------- modular parts: slots, variants, accessories -------------------------------

VARIANT = [None, None]          # (slot, variant) the parts being built belong to; None = core body
SLOTS = collections.OrderedDict()   # slot -> [variants]; the first variant is the default
SLOT_LABELS = {"Head": "Kopf", "Hands": "Hände", "Feet": "Füße", "Pads": "Schulterpanzer", "Backpack": "Rucksack",
               "AccHead": "Kopf-Accessoire", "AccFace": "Gesicht", "AccNeck": "Hals", "AccBack": "Rücken",
               "AccHand": "In der Hand"}
ACCESSORY_SLOTS = ("AccHead", "AccFace", "AccNeck", "AccBack", "AccHand")
SPIN_PARTS = set()              # part names that spin in the viewer (propeller)
for _slot in ACCESSORY_SLOTS:
    SLOTS[_slot] = ["None"]

# Robot types: complete looks = variants + skin + eyes + display + accessories
ROBOT_TYPES = collections.OrderedDict([
    ("Classic", {"variants": {}, "skin": "Classic", "expression": "Normal", "display": "Status"}),
    ("Scout", {"variants": {"Head": "Dome", "Feet": "Round", "AccHead": "Antenna"}, "skin": "Arctic",
               "expression": "Focused", "display": "Wave"}),
    ("Worker", {"variants": {"Head": "Boxy", "Hands": "Claw", "Feet": "Mech", "Pads": "Square",
                             "Backpack": "Battery"}, "skin": "Sunrise", "expression": "Normal", "display": "Battery"}),
    ("Retro", {"variants": {"Head": "TV", "Pads": "Square"}, "skin": "Copper", "expression": "Pixel",
               "display": "Hello"}),
    ("Heavy", {"variants": {"Head": "Boxy", "Hands": "Claw", "Feet": "Mech", "Pads": "Square", "Backpack": "Battery",
                            "AccBack": "Jetpack"}, "skin": "Midnight", "expression": "Angry", "display": "Status"}),
    ("Party", {"variants": {"Feet": "Round", "AccHead": "PropellerCap", "AccNeck": "BowTie"}, "skin": "Mint",
               "expression": "Happy", "display": "Music"}),
    ("Agent", {"variants": {"Head": "Dome", "AccFace": "Shades", "AccNeck": "Scarf"}, "skin": "Neon",
               "expression": "Focused", "display": "Cube"}),
    ("Gentleman", {"variants": {"AccHead": "TopHat", "AccFace": "Monocle", "AccNeck": "BowTie"}, "skin": "Noir",
                   "expression": "Skeptical", "display": "Clock"}),
    ("Punk", {"variants": {"Pads": "Square", "AccHead": "Mohawk", "AccFace": "Shades", "AccNeck": "Bell"},
              "skin": "Toxic", "expression": "Determined", "display": "Lightning"}),
    ("King", {"variants": {"Head": "Dome", "AccHead": "Crown", "AccNeck": "Medal", "AccBack": "Cape",
                           "AccHand": "Flag"}, "skin": "Gold", "expression": "Happy", "display": "Star"}),
    ("DJ", {"variants": {"Head": "Boxy", "Feet": "Round", "AccHead": "Headphones", "AccBack": "Wings"},
            "skin": "Vapor", "expression": "Laugh", "display": "Music"}),
    ("Gardener", {"variants": {"AccFace": "Mustache", "AccHand": "Flower"}, "skin": "Forest", "expression": "Shy",
                  "display": "Heart"}),
    ("Mechanic", {"variants": {"Backpack": "Battery", "AccFace": "Goggles", "AccHand": "Wrench"}, "skin": "Sand",
                  "expression": "Focused", "display": "Warning"}),
    # narrowest silhouette: fits the game's character capsule (radius 0.3 m)
    ("Runner", {"variants": {"Head": "Dome", "Pads": "Slim", "Feet": "Round"}, "skin": "Ocean",
                "expression": "Determined", "display": "Lightning"}),
])


class variant:
    """with variant("Head", "Dome"): parts built inside belong to that slot variant."""

    def __init__(self, slot, name):
        self.slot, self.name = slot, name

    def __enter__(self):
        SLOTS.setdefault(self.slot, [])
        if self.name not in SLOTS[self.slot]:
            SLOTS[self.slot].append(self.name)
        VARIANT[:] = [self.slot, self.name]
        return self

    def __exit__(self, *exc):
        VARIANT[:] = [None, None]
        return False


def group_key(slot, var):
    return "Body" if slot is None else "%s_%s" % (slot, var)


def default_config():
    return {slot: vs[0] for slot, vs in SLOTS.items()}


def config_groups(cfg):
    """Object groups visible for a configuration (slot -> variant)."""
    groups = ["Body"]
    for slot, var in cfg.items():
        key = group_key(slot, var)
        if any(p["group"] == key for p in PARTS):
            groups.append(key)
    return groups


def eye_screen(name, bone):
    """Flat, slightly curved emissive plane in front of the visor that shows the eye expressions."""
    bm = bmesh.new()
    nx, nz = 16, 2
    verts = []
    for iz in range(nz + 1):
        row = []
        for ix in range(nx + 1):
            x = -EYE_SCREEN_W / 2 + EYE_SCREEN_W * ix / nx
            z = EYE_Z - EYE_SCREEN_H / 2 + EYE_SCREEN_H * iz / nz
            row.append(bm.verts.new((x, EYE_SCREEN_Y, z)))
        verts.append(row)
    for iz in range(nz):
        for ix in range(nx):
            bm.faces.new((verts[iz][ix], verts[iz][ix + 1], verts[iz + 1][ix + 1], verts[iz + 1][ix]))
    for v in bm.verts:
        v.co = visor_bend(v.co.copy())
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    for f in bm.faces:              # the screen must face forward (-Y)
        if f.normal.y > 0:
            f.normal_flip()
    PARTS.append({"name": name, "bone": bone, "mat": "EyeScreen", "bm": bm, "slot": None, "variant": None,
                  "group": "Body"})


EYE_SCREEN_W, EYE_SCREEN_H, EYE_SCREEN_Y = 0.33, 0.165, -0.2235
DISPLAY_RECT = (0.014, 0.096, 0.767, 0.833)     # x0, x1, z0, z1 of the chest screen


def build_robot():
    W, D, G, V, O, F = "White", "Dark", "Grey", "Visor", "Orange", "Fabric"
    xs = [i * 0.02 for i in range(-11, 12)]

    # ================= core head (shared by every head variant) =================
    prism("Head_Visor", rrect(0.400, 0.190, 0.076, 5), 0.060, "Y", (0, -0.190, 1.112), V, "Head", r=0.012, segs=3,
          cuts_x=xs, world_deform=visor_bend)
    eye_screen("Eye_Screen", "Eyes")
    cylinder("Neck", 0.068, 0.120, (0, 0.010, 0.905), D, "Head", segs=12, bev=0.006)
    cylinder("Neck_Ring", 0.082, 0.022, (0, 0.010, 0.928), G, "Head", segs=16, bev=0.006)

    # ---------------- Head: Classic (key art) ----------------
    with variant("Head", "Classic"):
        rbox("Head_Shell", (0.52, 0.43, 0.37), (0, 0.0, 1.118), W, "Head", r=0.14, segs=5, taper_bot=(0.90, 0.92))
        for s, side in ((1, "L"), (-1, "R")):
            cylinder("Ear_Base_" + side, 0.090, 0.060, (s * 0.250, 0.010, 1.100), D, "Head", axis=(1, 0, 0),
                     segs=18, bev=0.010)
            cylinder("Ear_Ring_" + side, 0.068, 0.026, (s * 0.288, 0.010, 1.100), W, "Head", axis=(1, 0, 0),
                     segs=18, bev=0.009)
            cylinder("Ear_Cap_" + side, 0.034, 0.014, (s * 0.304, 0.010, 1.100), G, "Head", axis=(1, 0, 0),
                     segs=12, bev=0.004)
        n = Vector((0, -1, 1)).normalized()
        for i, xx in enumerate((0.050, 0.088)):
            cylinder("Head_Sensor_%d" % i, 0.011, 0.020, (xx, -0.170, 1.259), D, "Head", axis=n, segs=10, bev=0.003)
        prism("Head_BackVent", rrect(0.200, 0.100, 0.03, 3), 0.030, "Y", (0, 0.203, 1.118), D, "Head", r=0.006)
        for i, zz in enumerate((1.093, 1.118, 1.143)):
            rbox("Head_VentSlat_%d" % i, (0.165, 0.012, 0.011), (0, 0.2205, zz), G, "Head", r=0.004, segs=1)

    # ---------------- Head: Dome (round helmet with fins) ----------------
    with variant("Head", "Dome"):
        rbox("Dome_Shell", (0.50, 0.44, 0.40), (0, 0.0, 1.130), W, "Head", r=0.19, segs=6, taper_bot=(0.90, 0.92))
        rbox("Dome_Brow", (0.42, 0.06, 0.03), (0, -0.168, 1.232), D, "Head", r=0.012, segs=2)
        for s, side in ((1, "L"), (-1, "R")):
            cylinder("Dome_EarDisc_" + side, 0.070, 0.040, (s * 0.238, 0.0, 1.110), D, "Head", axis=(1, 0, 0),
                     segs=16, bev=0.008)
            fin = [(0.06, -0.02), (-0.03, -0.035), (-0.13, 0.10), (-0.10, 0.12), (0.04, 0.03)]
            prism("Dome_Fin_" + side, fin, 0.030, "X", (s * 0.262, 0.02, 1.115), W, "Head", r=0.008, segs=2)
        cylinder("Dome_TopLight", 0.035, 0.030, (0, 0.0, 1.322), O, "Head", segs=14, bev=0.008)

    # ---------------- Head: Boxy (worker box with antenna stubs) ----------------
    with variant("Head", "Boxy"):
        rbox("Boxy_Shell", (0.50, 0.42, 0.36), (0, 0.0, 1.125), W, "Head", r=0.06, segs=2, taper_bot=(0.82, 1.0))
        for i, xx in enumerate((-0.10, 0.0, 0.10)):
            rbox("Boxy_Grill_%d" % i, (0.06, 0.24, 0.016), (xx, 0.02, 1.305), D, "Head", r=0.006, segs=1)
        for s, side in ((1, "L"), (-1, "R")):
            cylinder("Boxy_Stub_" + side, 0.030, 0.070, (s * 0.262, 0.0, 1.160), D, "Head", axis=(1, 0, 0),
                     segs=12, bev=0.006)
            sphere("Boxy_StubLight_" + side, 0.020, (s * 0.300, 0.0, 1.160), O, "Head", segs=10, rings=6)
            rbox("Boxy_Bolt_" + side, (0.030, 0.050, 0.050), (s * 0.222, 0.06, 1.040), G, "Head", r=0.008, segs=1)

    # ---------------- Head: TV (retro CRT with rabbit ears) ----------------
    with variant("Head", "TV"):
        rbox("TV_Shell", (0.52, 0.36, 0.34), (0, -0.02, 1.136), W, "Head", r=0.07, segs=3, taper_bot=(0.82, 1.0))
        rbox("TV_Back", (0.40, 0.26, 0.14), (0, 0.19, 1.128), G, "Head", r=0.06, segs=3, taper_top=(0.8, 0.8),
             rot=(-90, 0, 0))
        for nm, size, c in (("Top", (0.40, 0.03, 0.030), (0, -0.198, 1.232)),):
            rbox("TV_Bezel_" + nm, size, c, D, "Head", r=0.008, segs=1)
        for i, zz in enumerate((1.17, 1.07)):
            cylinder("TV_Knob_%d" % i, 0.026, 0.030, (-0.252, -0.05, zz + 0.04), G, "Head", axis=(1, 0, 0), segs=14,
                     bev=0.006)
        rbox("TV_Speaker", (0.020, 0.16, 0.12), (0.250, -0.02, 1.17), D, "Head", r=0.006, segs=1)
        for s in (1, -1):
            d = Vector((s * math.sin(math.radians(28)), 0.0, math.cos(math.radians(28))))
            base = Vector((s * 0.05, 0.02, 1.29))
            cyl_between("TV_Antenna_%s" % ("L" if s > 0 else "R"), base, base + d * 0.20, 0.007, G, "Head", segs=8)
            sphere("TV_AntennaTip_%s" % ("L" if s > 0 else "R"), 0.018, base + d * 0.205, O, "Head", segs=10,
                   rings=6)
        cylinder("TV_AntennaBase", 0.045, 0.030, (0, 0.02, 1.296), D, "Head", segs=14, bev=0.006)

    # ================= core torso, pelvis =================
    rbox("Torso_Core", (0.36, 0.27, 0.34), (0, 0.010, 0.730), D, "Spine", r=0.05, segs=2)
    rbox("Chest_Plate", (0.50, 0.33, 0.23), (0, 0.0, 0.790), W, "Spine", r=0.075, segs=3, taper_bot=(0.84, 0.92))
    left = [(-0.182, 0.669), (-0.007, 0.669), (-0.007, 0.582), (-0.165, 0.613)]
    prism("Abdomen_Plate_R", left, 0.285, "Y", (0, 0.0, 0), W, "Spine", r=0.018, segs=2)
    prism("Abdomen_Plate_L", [(-x, z) for x, z in reversed(left)], 0.285, "Y", (0, 0.0, 0), W, "Spine", r=0.018,
          segs=2)
    prism("Chest_Display", rrect(0.110, 0.094, 0.018, 3), 0.030, "Y", (0.055, -0.160, 0.800), D, "Spine", r=0.006)
    prism("Chest_Screen", rrect(0.082, 0.066, 0.010, 3), 0.012, "Y", (0.055, -0.175, 0.800), "Display", "Spine",
          r=0.003)
    for i, xx in enumerate((-0.135, -0.108, -0.081)):
        cylinder("Chest_LED_%d" % i, 0.0098, 0.016, (xx, -0.166, 0.816), O, "Spine", axis=(0, 1, 0), segs=10,
                 bev=0.003)
    rbox("Chest_Badge", (0.062, 0.014, 0.016), (-0.108, -0.166, 0.781), G, "Spine", r=0.004, segs=1)
    cylinder("Collar", 0.112, 0.030, (0, 0.010, 0.902), D, "Spine", segs=18, bev=0.008)

    with variant("Backpack", "Classic"):
        rbox("Backpack", (0.29, 0.08, 0.20), (0, 0.180, 0.770), W, "Spine", r=0.03, segs=2)
        for s in (1, -1):
            rbox("Backpack_Vent_%s" % ("L" if s > 0 else "R"), (0.070, 0.012, 0.120), (s * 0.070, 0.2205, 0.760),
                 D, "Spine", r=0.005, segs=1)
        prism("Backpack_Light", rrect(0.070, 0.018, 0.008, 2), 0.010, "Y", (0, 0.2205, 0.845), O, "Spine", r=0.003)
    with variant("Backpack", "Battery"):
        rbox("Battery_Mount", (0.20, 0.06, 0.16), (0, 0.165, 0.770), D, "Spine", r=0.015, segs=2)
        for s in (1, -1):
            sd = "L" if s > 0 else "R"
            cylinder("Battery_Cell_" + sd, 0.055, 0.200, (s * 0.068, 0.205, 0.770), W, "Spine", segs=16, bev=0.012)
            cylinder("Battery_CapTop_" + sd, 0.040, 0.026, (s * 0.068, 0.205, 0.878), D, "Spine", segs=14, bev=0.006)
            cylinder("Battery_Ring_" + sd, 0.057, 0.012, (s * 0.068, 0.205, 0.800), O, "Spine", segs=16, bev=0.003)

    cylinder("Waist", 0.140, 0.090, (0, 0.010, 0.560), D, "Pelvis", segs=16, bev=0.012, scale=(1, 0.80, 1))
    cylinder("Waist_Ring", 0.152, 0.022, (0, 0.010, 0.537), G, "Pelvis", segs=18, bev=0.007, scale=(1, 0.80, 1))
    rbox("Hips", (0.37, 0.27, 0.13), (0, 0.005, 0.470), W, "Pelvis", r=0.055, segs=3, taper_bot=(0.85, 0.90))
    rbox("Crotch", (0.110, 0.180, 0.085), (0, 0.0, 0.405), D, "Pelvis", r=0.025, segs=2)

    # ================= legs and feet =================
    for s, side in ((1, "L"), (-1, "R")):
        x0 = s * LEG_X
        sphere("Hip_Ball_" + side, 0.068, J["hip_" + side], D, "UpperLeg_" + side, segs=12, rings=7)
        rbox("Thigh_" + side, (0.145, 0.150, 0.135), (x0, 0.0, 0.330), W, "UpperLeg_" + side, r=0.045, segs=3,
             taper_bot=(0.92, 0.92))
        cylinder("Knee_" + side, 0.058, 0.135, J["knee_" + side], D, "LowerLeg_" + side, axis=(1, 0, 0), segs=14,
                 bev=0.010)
        rbox("KneeCap_" + side, (0.100, 0.040, 0.072), (x0, -0.068, 0.250), W, "LowerLeg_" + side, r=0.016, segs=2)
        rbox("Shin_" + side, (0.155, 0.165, 0.130), (x0, 0.005, 0.165), W, "LowerLeg_" + side, r=0.045, segs=3,
             taper_top=(0.86, 0.86))
        cylinder("Ankle_" + side, 0.044, 0.115, J["ankle_" + side], D, "Foot_" + side, axis=(1, 0, 0), segs=12,
                 bev=0.006)
        foot = "Foot_" + side
        with variant("Feet", "Boots"):
            prism("Foot_Shell_" + side, rrect(0.185, 0.300, 0.070, 4), 0.080, "Z", (x0, -0.050, 0.065), W, foot,
                  r=0.032, segs=3, deform=foot_deform)
            prism("Foot_Sole_" + side, rrect(0.195, 0.310, 0.075, 4), 0.030, "Z", (x0, -0.050, 0.015), D, foot,
                  r=0.008, segs=2)
        with variant("Feet", "Round"):
            prism("Sneaker_Shell_" + side, rrect(0.185, 0.295, 0.090, 5), 0.085, "Z", (x0, -0.048, 0.072), W, foot,
                  r=0.040, segs=4, deform=foot_deform)
            prism("Sneaker_Sole_" + side, rrect(0.195, 0.305, 0.092, 5), 0.034, "Z", (x0, -0.048, 0.017), O, foot,
                  r=0.010, segs=2)
            rbox("Sneaker_Strap_" + side, (0.150, 0.050, 0.020), (x0, -0.030, 0.108), D, foot, r=0.008, segs=1)
        with variant("Feet", "Mech"):
            rbox("Mech_Heel_" + side, (0.150, 0.115, 0.068), (x0, 0.030, 0.046), G, foot, r=0.020, segs=2)
            for i, dx in enumerate((-0.048, 0.048)):
                prism("Mech_Toe_%d_%s" % (i, side), rrect(0.088, 0.150, 0.030, 3), 0.060, "Z",
                      (x0 + dx, -0.120, 0.035), W, foot, r=0.018, segs=2, deform=foot_deform)
            rbox("Mech_Sole_" + side, (0.198, 0.300, 0.022), (x0, -0.052, 0.011), D, foot, r=0.008, segs=1)
            rbox("Mech_AnklePlate_" + side, (0.110, 0.035, 0.050), (x0, -0.075, 0.080), W, foot, r=0.012, segs=2)

    # ================= arms and hands =================
    for s, side in ((1, "L"), (-1, "R")):
        sh, el, wr = J["shoulder_" + side], J["elbow_" + side], J["wrist_" + side]
        d1, d2 = J["dir_upper_" + side], J["dir_fore_" + side]
        cylinder("Shoulder_Socket_" + side, 0.062, 0.075, (s * 0.232, 0, 0.830), D, "Shoulder_" + side,
                 axis=(1, 0, 0), segs=16, bev=0.008)
        sphere("Shoulder_Ball_" + side, 0.062, sh, D, "UpperArm_" + side, segs=12, rings=7)
        with variant("Pads", "Round"):
            rbox("Shoulder_Pad_" + side, (0.165, 0.190, 0.135), sh + Vector((s * 0.014, 0, 0.016)), W,
                 "UpperArm_" + side, r=0.064, segs=3)
        with variant("Pads", "Slim"):     # low profile: keeps the silhouette narrow (character capsule)
            rbox("Slim_Pad_" + side, (0.112, 0.170, 0.070), sh + Vector((s * -0.004, 0, 0.040)), W,
                 "UpperArm_" + side, r=0.030, segs=2, taper_top=(0.86, 0.90))
            rbox("Slim_PadStripe_" + side, (0.020, 0.150, 0.012), sh + Vector((s * 0.020, 0, 0.076)), O,
                 "UpperArm_" + side, r=0.004, segs=1)
        with variant("Pads", "Square"):
            rbox("Square_Pad_" + side, (0.170, 0.200, 0.120), sh + Vector((s * 0.018, 0, 0.012)), W,
                 "UpperArm_" + side, r=0.022, segs=2)
            rbox("Square_PadStripe_" + side, (0.030, 0.205, 0.020), sh + Vector((s * 0.070, 0, 0.068)), O,
                 "UpperArm_" + side, r=0.006, segs=1)
        cyl_between("Upper_Arm_" + side, sh + d1 * 0.05, el, 0.049, W, "UpperArm_" + side, segs=12, bev=0.008)
        sphere("Elbow_" + side, 0.056, el, D, "LowerArm_" + side, segs=12, rings=7)
        cyl_between("Elbow_Band_" + side, el + d2 * 0.012, el + d2 * 0.052, 0.052, D, "LowerArm_" + side, segs=14,
                    bev=0.006)
        fr = frame_from_dir(d2)
        rbox("Forearm_" + side, (0.110, 0.120, 0.120), el + d2 * 0.106, W, "LowerArm_" + side, r=0.04, segs=3,
             taper_bot=(0.80, 0.82), rot=fr)
        cyl_between("Wrist_" + side, wr - d2 * 0.010, wr + d2 * 0.028, 0.033, D, "Hand_" + side, segs=12)
        kn = J["knuckle_" + side]
        fdir = (Matrix.Rotation(math.radians(s * 12), 3, "Y") @ d2).normalized()
        ff = frame_from_dir(fdir)
        tdir = (Vector((-s * 0.25, -0.75, -0.62))).normalized()
        tb = wr + d2 * 0.044 + Vector((-s * 0.014, -0.046, 0))
        J["thumb_" + side] = tb
        J["thumb_dir_" + side] = tdir
        J["finger_dir_" + side] = fdir
        with variant("Hands", "Mitt"):
            rbox("Palm_" + side, (0.064, 0.098, 0.080), wr + d2 * 0.054, G, "Hand_" + side, r=0.022, segs=2, rot=fr)
            for i, off in enumerate((-0.031, 0.0, 0.031)):
                base = kn + Vector((0, off, 0))
                rbox("Finger_%d_%s" % (i, side), (0.030, 0.027, 0.058), base + fdir * 0.024, D, "Fingers_" + side,
                     r=0.011, segs=1, rot=ff)
            rbox("Thumb_" + side, (0.028, 0.028, 0.050), tb + tdir * 0.024, D, "Thumb_" + side, r=0.010, segs=1,
                 rot=frame_from_dir(tdir))
        with variant("Hands", "Claw"):
            cyl_between("Claw_Cuff_" + side, wr + d2 * 0.010, wr + d2 * 0.085, 0.040, G, "Hand_" + side, segs=14,
                        bev=0.008)
            inward = Vector((-s, 0, 0))
            for i, off in enumerate((-0.024, 0.024)):
                base = kn + Vector((0, off, 0))
                rbox("Claw_Prong_%d_%s" % (i, side), (0.022, 0.024, 0.050), base + fdir * 0.020, D, "Fingers_" + side,
                     r=0.008, segs=1, rot=ff)
                tip_dir = (fdir + inward * 0.9).normalized()
                rbox("Claw_Tip_%d_%s" % (i, side), (0.020, 0.022, 0.032), base + fdir * 0.042 + tip_dir * 0.012, W,
                     "Fingers_" + side, r=0.007, segs=1, rot=frame_from_dir(tip_dir))
            rbox("Claw_Thumb_" + side, (0.024, 0.026, 0.060), tb + tdir * 0.022, D, "Thumb_" + side, r=0.008,
                 segs=1, rot=frame_from_dir(tdir))

    # ================= accessories (on socket bones) =================
    with variant("AccHead", "Antenna"):
        cylinder("Antenna_Base", 0.032, 0.050, (0, 0.0, 1.300), D, "Socket_Head", segs=14, bev=0.008)
        cylinder("Antenna_Rod", 0.007, 0.160, (0, 0.0, 1.400), G, "Socket_Head", segs=8)
        sphere("Antenna_Tip", 0.022, (0, 0.0, 1.482), O, "Socket_Head", segs=12, rings=7)
    with variant("AccHead", "PropellerCap"):
        sphere("Cap_Dome", 0.20, (0, 0.02, 1.270), F, "Socket_Head", segs=20, rings=10, scale=(1.0, 1.0, 0.55))
        prism("Cap_Bill", ellipse(0.22, 0.14, 20), 0.014, "Z", (0, -0.17, 1.272), F, "Socket_Head", r=0.005, segs=1)
        cylinder("Cap_Stem", 0.010, 0.050, (0, 0.02, 1.395), G, "Socket_Head", segs=8)
        for i, ang in enumerate((0, 90)):
            rbox("Cap_Blade_%d" % i, (0.24, 0.032, 0.008), (0, 0.02, 1.420), O if i else W, "Socket_Head", r=0.003,
                 segs=1, rot=(0, 0, ang))
            SPIN_PARTS.add("Cap_Blade_%d" % i)
        sphere("Cap_Hub", 0.018, (0, 0.02, 1.424), D, "Socket_Head", segs=10, rings=6)
        SPIN_PARTS.add("Cap_Hub")
    with variant("AccFace", "Shades"):
        for s in (1, -1):
            prism("Shades_Lens_%s" % ("L" if s > 0 else "R"), rrect(0.172, 0.080, 0.030, 4), 0.016, "Y",
                  (s * 0.090, -0.242, 1.120), V, "Head", r=0.004, segs=1,
                  cuts_x=[s * 0.088 + d for d in (-0.05, -0.025, 0, 0.025, 0.05)], world_deform=visor_bend)
            rbox("Shades_Arm_%s" % ("L" if s > 0 else "R"), (0.012, 0.20, 0.016), (s * 0.172, -0.135, 1.135), D,
                 "Head", r=0.004, segs=1)
        rbox("Shades_Bridge", (0.050, 0.014, 0.016), (0, -0.243, 1.130), D, "Head", r=0.004, segs=1)
    with variant("AccNeck", "BowTie"):
        for s in (1, -1):
            wing = [(0.0, 0.0), (s * 0.075, 0.035), (s * 0.075, -0.035)]
            prism("Bow_Wing_%s" % ("L" if s > 0 else "R"), wing if s < 0 else list(reversed(wing)), 0.020, "Y",
                  (0, -0.172, 0.872), F, "Socket_Neck", r=0.006, segs=1)
        rbox("Bow_Knot", (0.034, 0.026, 0.032), (0, -0.176, 0.872), F, "Socket_Neck", r=0.008, segs=1)
    with variant("AccNeck", "Scarf"):
        cylinder("Scarf_Ring", 0.124, 0.032, (0, 0.010, 0.908), F, "Socket_Neck", segs=20, bev=0.012)
        rbox("Scarf_Tail", (0.060, 0.022, 0.150), (0.075, 0.150, 0.835), F, "Socket_Neck", r=0.008, segs=1,
             rot=(-14, 0, -8))
    with variant("AccBack", "Jetpack"):
        rbox("Jet_Frame", (0.22, 0.060, 0.070), (0, 0.250, 0.790), D, "Socket_Back", r=0.012, segs=1)
        for s in (1, -1):
            sd = "L" if s > 0 else "R"
            cylinder("Jet_Tank_" + sd, 0.050, 0.230, (s * 0.078, 0.292, 0.745), W, "Socket_Back", segs=16, bev=0.012)
            sphere("Jet_Cap_" + sd, 0.046, (s * 0.078, 0.292, 0.860), D, "Socket_Back", segs=14, rings=7,
                   scale=(1, 1, 0.6))
            cylinder("Jet_Nozzle_" + sd, 0.034, 0.050, (s * 0.078, 0.292, 0.611), D, "Socket_Back", segs=14,
                     r2=0.044)
            cylinder("Jet_Flame_" + sd, 0.030, 0.006, (s * 0.078, 0.292, 0.586), O, "Socket_Back", segs=14)

    # ---------------- more head accessories ----------------
    with variant("AccHead", "Headphones"):
        torus("Phones_Band", 0.248, 0.013, (0, 0.010, 1.100), D, "Socket_Head", axis=(0, -1, 0), segs=20, rsegs=8,
              arc=180.0)
        rbox("Phones_Pad", (0.15, 0.050, 0.022), (0, 0.010, 1.352), F, "Socket_Head", r=0.008, segs=1)
        for s in (1, -1):
            sd = "L" if s > 0 else "R"
            cylinder("Phones_Cup_" + sd, 0.070, 0.040, (s * 0.288, 0.010, 1.100), G, "Socket_Head", axis=(1, 0, 0),
                     segs=20, bev=0.010)
            cylinder("Phones_Cushion_" + sd, 0.064, 0.020, (s * 0.262, 0.010, 1.100), F, "Socket_Head",
                     axis=(1, 0, 0), segs=20, bev=0.006)
            torus("Phones_Light_" + sd, 0.045, 0.005, (s * 0.309, 0.010, 1.100), O, "Socket_Head", axis=(1, 0, 0),
                  segs=20, rsegs=6)
    with variant("AccHead", "TopHat"):
        # glossy black like the visor (skin-independent); the band takes the fabric colour
        cylinder("Hat_Brim", 0.200, 0.014, (0, 0.0, 1.302), V, "Socket_Head", segs=28, bev=0.005)
        cylinder("Hat_Crown", 0.125, 0.220, (0, 0.0, 1.418), V, "Socket_Head", segs=24, r2=0.132, bev=0.010)
        cylinder("Hat_Band", 0.129, 0.040, (0, 0.0, 1.336), F, "Socket_Head", segs=24, r2=0.130)
    with variant("AccHead", "Crown"):
        cylinder("Crown_Ring", 0.110, 0.070, (0, 0.0, 1.330), G, "Socket_Head", segs=24, bev=0.008)
        cylinder("Crown_Velvet", 0.098, 0.030, (0, 0.0, 1.355), F, "Socket_Head", segs=20, bev=0.010)
        for i in range(6):
            a = 2 * math.pi * i / 6
            c = Vector((0.100 * math.cos(a), 0.100 * math.sin(a), 1.392))
            cylinder("Crown_Spike_%d" % i, 0.026, 0.070, c, G, "Socket_Head", segs=8, r2=0.004)
            sphere("Crown_Gem_%d" % i, 0.016, (0.113 * math.cos(a), 0.113 * math.sin(a), 1.330), O, "Socket_Head",
                   segs=10, rings=6)
    with variant("AccHead", "Mohawk"):
        for i, (yy, h) in enumerate(((-0.140, 0.085), (-0.070, 0.115), (0.0, 0.130), (0.070, 0.115), (0.140, 0.085))):
            fin = [(-0.040, 0.0), (0.040, 0.0), (0.026, h), (0.008, h)]
            prism("Mohawk_Fin_%d" % i, fin, 0.024, "X", (0, yy, 1.272), F, "Socket_Head", r=0.006, segs=1)

    # ---------------- more face accessories ----------------
    with variant("AccFace", "Monocle"):
        torus("Monocle_Ring", 0.056, 0.008, (-EYE_X, -0.236, EYE_Z), G, "Head", axis=(0, -1, 0), segs=24, rsegs=6)
        a = math.radians(-135)
        start = Vector((-EYE_X + 0.056 * math.cos(a) * -1, -0.236, EYE_Z + 0.056 * math.sin(a)))
        cyl_between("Monocle_Chain", start, (-0.255, -0.06, 1.05), 0.0045, G, "Head", segs=6)
    with variant("AccFace", "Mustache"):
        for s in (1, -1):
            sd = "L" if s > 0 else "R"
            prism("Mustache_" + sd, ellipse(0.105, 0.036, 18), 0.018, "Y", (s * 0.052, -0.229, 1.040), D, "Head",
                  r=0.006, segs=1, rot=(0, -s * 14, 0), cuts_x=[s * 0.052 + d for d in (-0.04, 0.0, 0.04)],
                  world_deform=visor_bend)
            prism("Mustache_Curl_" + sd, ellipse(0.034, 0.034, 14), 0.018, "Y", (s * 0.108, -0.226, 1.060), D,
                  "Head", r=0.006, segs=1, world_deform=visor_bend)
    with variant("AccFace", "Goggles"):
        n = Vector((0, -0.6, 0.8)).normalized()
        for s in (1, -1):
            sd = "L" if s > 0 else "R"
            c = Vector((s * 0.075, -0.166, 1.247))
            cylinder("Goggle_Rim_" + sd, 0.054, 0.036, c, G, "Head", axis=n, segs=18, bev=0.008)
            cylinder("Goggle_Glass_" + sd, 0.042, 0.040, c + n * 0.002, V, "Head", axis=n, segs=18)
        rbox("Goggle_Strap", (0.46, 0.030, 0.030), (0, -0.140, 1.262), D, "Head", r=0.008, segs=1, rot=(-37, 0, 0))

    # ---------------- more neck accessories ----------------
    with variant("AccNeck", "Medal"):
        for s, top in ((1, -0.030), (-1, -0.100)):
            a, b = Vector((top, -0.168, 0.888)), Vector((-0.065, -0.176, 0.745))
            d = (b - a)
            rbox("Medal_Ribbon_%d" % (0 if s > 0 else 1), (0.020, 0.006, d.length), (a + b) / 2, F, "Socket_Neck",
                 r=0.002, segs=1, rot=frame_from_dir(d))
        cylinder("Medal_Disc", 0.032, 0.012, (-0.065, -0.172, 0.722), G, "Socket_Neck", axis=(0, 1, 0), segs=20,
                 bev=0.004)
        prism("Medal_Star", [(0.016 * math.cos(math.radians(90 + k * 36)) * (1 if k % 2 == 0 else 0.45),
                              0.016 * math.sin(math.radians(90 + k * 36)) * (1 if k % 2 == 0 else 0.45))
                             for k in range(10)], 0.006, "Y", (-0.065, -0.180, 0.722), O, "Socket_Neck")
    with variant("AccNeck", "Bell"):
        torus("Bell_Collar", 0.122, 0.012, (0, 0.010, 0.905), F, "Socket_Neck", segs=28, rsegs=6)
        sphere("Bell_Body", 0.026, (0, -0.124, 0.870), G, "Socket_Neck", segs=14, rings=8)
        rbox("Bell_Slit", (0.030, 0.010, 0.006), (0, -0.148, 0.862), D, "Socket_Neck", r=0.002, segs=1)

    # ---------------- more back accessories ----------------
    with variant("AccBack", "Cape"):
        rows = [(0.830, 0.235, 0.170), (0.800, 0.268, 0.190), (0.760, 0.290, 0.205), (0.700, 0.302, 0.218),
                (0.620, 0.310, 0.228), (0.520, 0.320, 0.240)]
        bm = bmesh.new()
        grid = []
        cols = 8
        for z, y, hw in rows:
            grid.append([bm.verts.new((-hw + 2 * hw * c / cols, y + 0.012 * math.cos(math.pi * c / cols * 2), z))
                         for c in range(cols + 1)])
        for r_ in range(len(rows) - 1):
            for c in range(cols):
                bm.faces.new((grid[r_][c], grid[r_][c + 1], grid[r_ + 1][c + 1], grid[r_ + 1][c]))
        bmesh.ops.solidify(bm, geom=bm.faces[:], thickness=0.012)
        add_part("Cape_Cloth", bm, F, "Socket_Back")
        cylinder("Cape_Clasp", 0.022, 0.085, (0, 0.200, 0.828), O, "Socket_Back", axis=(0, 1, 0), segs=14)
    with variant("AccBack", "Wings"):
        wing = [(0.0, 0.0), (0.10, 0.08), (0.27, 0.19), (0.30, 0.13), (0.21, 0.05), (0.25, -0.02), (0.12, -0.03)]
        for s in (1, -1):
            sd = "L" if s > 0 else "R"
            pts = [(s * u, v) for u, v in wing]
            prism("Wing_" + sd, pts if s > 0 else list(reversed(pts)), 0.022, "Y", (s * 0.065, 0.250, 0.790), W,
                  "Socket_Back", r=0.006, segs=1)
            prism("Wing_Edge_" + sd, [(s * 0.10, 0.075), (s * 0.27, 0.185), (s * 0.255, 0.165), (s * 0.095, 0.060)]
                  if s > 0 else [(s * 0.095, 0.060), (s * 0.255, 0.165), (s * 0.27, 0.185), (s * 0.10, 0.075)],
                  0.026, "Y", (s * 0.065, 0.250, 0.790), O, "Socket_Back", r=0.004, segs=1)
            cylinder("Wing_Joint_" + sd, 0.028, 0.050, (s * 0.068, 0.240, 0.790), D, "Socket_Back", axis=(0, 1, 0),
                     segs=12, bev=0.006)

    # ---------------- items in the right hand (Prop_R) ----------------
    grip = J["knuckle_R"] + Vector((0.030, -0.005, 0.0))
    d = Vector((-0.65, -0.70, 0.30)).normalized()       # outwards-forwards: away from the head when the arm is raised
    fr = frame_from_dir(d)
    out = Vector((-1.0, 0.0, 0.0))
    with variant("AccHand", "Wrench"):
        rbox("Wrench_Handle", (0.022, 0.012, 0.120), grip + d * 0.035, G, "Prop_R", r=0.005, segs=1, rot=fr)
        for k, off in enumerate((-0.019, 0.019)):
            rbox("Wrench_Jaw_%d" % k, (0.014, 0.014, 0.036), grip + d * 0.112 + fr.col[0] * off, G, "Prop_R",
                 r=0.004, segs=1, rot=fr)
        rbox("Wrench_Head", (0.052, 0.014, 0.018), grip + d * 0.096, G, "Prop_R", r=0.004, segs=1, rot=fr)
    with variant("AccHand", "Flower"):
        cyl_between("Flower_Stem", grip - d * 0.020, grip + d * 0.130, 0.006, D, "Prop_R", segs=8)
        head = grip + d * 0.140
        sphere("Flower_Center", 0.018, head, O, "Prop_R", segs=12, rings=7)
        for k in range(6):
            a = 2 * math.pi * k / 6
            off = fr.col[0] * math.cos(a) + fr.col[1] * math.sin(a)
            sphere("Flower_Petal_%d" % k, 0.022, head + off * 0.026, F, "Prop_R", segs=10, rings=6,
                   scale=(1.0, 1.0, 0.45))
        prism("Flower_Leaf", ellipse(0.044, 0.018, 12), 0.006, "Y", grip + d * 0.065 + out * 0.016, D, "Prop_R")
    with variant("AccHand", "Flag"):
        cyl_between("Flag_Pole", grip - d * 0.030, grip + d * 0.230, 0.007, G, "Prop_R", segs=8)
        sphere("Flag_Tip", 0.013, grip + d * 0.236, O, "Prop_R", segs=10, rings=6)
        rbox("Flag_Cloth", (0.110, 0.004, 0.075), grip + d * 0.180 - fr.col[0] * 0.058, F, "Prop_R", r=0.002, segs=1,
             rot=fr)


# ----------------------------------------------------------------------------------------------
# Mesh assembly
# ----------------------------------------------------------------------------------------------

BONE_ORDER = ["Root", "Pelvis", "Spine", "Head", "Eyes",
              "Shoulder_L", "UpperArm_L", "LowerArm_L", "Hand_L", "Fingers_L", "Thumb_L",
              "Shoulder_R", "UpperArm_R", "LowerArm_R", "Hand_R", "Fingers_R", "Thumb_R",
              "UpperLeg_L", "LowerLeg_L", "Foot_L", "UpperLeg_R", "LowerLeg_R", "Foot_R",
              "Socket_Head", "Socket_Neck", "Socket_Back", "Prop_L", "Prop_R"]
REQUIRED_BONES = ["Root", "Pelvis", "Spine", "Head", "Shoulder_L", "Shoulder_R", "UpperArm_L", "UpperArm_R",
                  "LowerArm_L", "LowerArm_R", "Hand_L", "Hand_R", "UpperLeg_L", "UpperLeg_R", "LowerLeg_L",
                  "LowerLeg_R", "Foot_L", "Foot_R"]


def check_part_connectivity(report):
    """Every part must touch another part of a valid configuration (core + defaults + its own variant)."""
    trees = []
    for p in PARTS:
        p["bm"].verts.ensure_lookup_table()
        trees.append(BVHTree.FromBMesh(p["bm"]))
    defaults = {group_key(sl, vs[0]) for sl, vs in SLOTS.items()}
    floating = []
    for i, p in enumerate(PARTS):
        ok_groups = {"Body", p["group"]} | (defaults - {group_key(p["slot"], v) for v in SLOTS.get(p["slot"], [])})
        others = [j for j, q in enumerate(PARTS) if j != i and q["group"] in ok_groups]
        if any(trees[i].overlap(trees[j]) for j in others):
            continue
        best = 1e9
        for v in p["bm"].verts:
            for j in others:
                hit = trees[j].find_nearest(v.co)
                if hit[0] is not None:
                    best = min(best, hit[3])
            if best < 0.002:
                break
        limit = 0.006 if p["mat"] == "EyeScreen" else 0.002     # the eye screen is a decal 1.5 mm off the visor
        if best >= limit:
            floating.append((p["name"], round(best, 4)))
    report["parts"] = len(PARTS)
    report["floating_parts"] = floating
    if floating:
        LOG.warning("Floating parts: %s", floating)
    else:
        LOG.info("  all %d parts are connected", len(PARTS))
    return floating


def assemble_groups(mats, coll):
    """One mesh object per group: Robot_Body (core) and <Slot>_<Variant> for every variant/accessory."""
    objs = collections.OrderedDict()
    order = ["Body"] + [group_key(sl, v) for sl, vs in SLOTS.items() for v in vs]
    for key in order:
        parts = [p for p in PARTS if p["group"] == key]
        if not parts:
            continue
        name = "Robot_Body" if key == "Body" else key
        me = bpy.data.meshes.new(name)
        ob = bpy.data.objects.new(name, me)
        coll.objects.link(ob)
        for mk in MAT_ORDER:
            me.materials.append(mats[mk])
        final = bmesh.new()
        deform = final.verts.layers.deform.verify()
        part_layer = final.faces.layers.int.new("part_id")
        groups = {b: ob.vertex_groups.new(name=b).index for b in BONE_ORDER if b != "Root"}
        tmp = bpy.data.meshes.new("tmp")
        names = []
        for p in parts:
            n_v, n_f = len(final.verts), len(final.faces)
            p["bm"].to_mesh(tmp)
            final.from_mesh(tmp)
            final.verts.ensure_lookup_table()
            final.faces.ensure_lookup_table()
            gi = groups[p["bone"]]
            for v in final.verts[n_v:]:
                v[deform][gi] = 1.0
            mi = MAT_ORDER.index(p["mat"])
            for f in final.faces[n_f:]:
                f.material_index = mi
                f[part_layer] = len(names)
                f.smooth = True
            names.append(p["name"])
        bpy.data.meshes.remove(tmp)
        for e in final.edges:
            if len(e.link_faces) == 2 and e.calc_face_angle(0.0) > math.radians(38):
                e.smooth = False
        final.to_mesh(me)
        final.free()
        me["part_names"] = names
        me["spin_parts"] = [n for n in names if n in SPIN_PARTS]
        slot = parts[0]["slot"]
        ob["slot"] = slot or ""
        ob["variant"] = parts[0]["variant"] or ""
        objs[key] = ob
    for p in PARTS:
        p["bm"].free()
    return objs


def apply_weighted_normals(ob):
    for o in bpy.context.scene.objects:
        o.select_set(o == ob)
    bpy.context.view_layer.objects.active = ob
    mod = ob.modifiers.new("WeightedNormal", "WEIGHTED_NORMAL")
    mod.mode = "FACE_AREA"
    mod.weight = 50
    mod.keep_sharp = True
    with bpy.context.temp_override(object=ob, active_object=ob, selected_objects=[ob]):
        bpy.ops.object.modifier_apply(modifier=mod.name)


UV_SCHEMA = "1.8-a"     # bump when unwrap() changes


def mesh_arrays(me):
    n = len(me.vertices)
    co = np.empty(n * 3, dtype=np.float32)
    me.vertices.foreach_get("co", co)
    lv = np.empty(len(me.loops), dtype=np.int32)
    me.loops.foreach_get("vertex_index", lv)
    ls = np.empty(len(me.polygons), dtype=np.int32)
    me.polygons.foreach_get("loop_start", ls)
    lt = np.empty(len(me.polygons), dtype=np.int32)
    me.polygons.foreach_get("loop_total", lt)
    mi = np.empty(len(me.polygons), dtype=np.int32)
    me.polygons.foreach_get("material_index", mi)
    return co.reshape(-1, 3), lv, ls, lt, mi


def canonical_order(me):
    """Blender's face order of the generated parts varies between runs (the vertices do not). Polygons sorted by
    their rounded corner positions, each rotated to start at its smallest corner, give an order that does not:
    returns (loop permutation, polygon permutation) into the mesh's own order."""
    co, lv, ls, lt, _ = mesh_arrays(me)
    co = np.round(co.astype(np.float64), 5)
    keys, rot = [], []
    for p in range(len(ls)):
        pts = [tuple(c) for c in co[lv[ls[p]:ls[p] + lt[p]]].tolist()]
        r = min(range(len(pts)), key=pts.__getitem__)
        keys.append(pts[r:] + pts[:r])
        rot.append(r)
    order = sorted(range(len(ls)), key=keys.__getitem__)
    perm = np.empty(len(lv), dtype=np.int64)
    k = 0
    for p in order:
        start, total, r = ls[p], lt[p], rot[p]
        for j in range(total):
            perm[k] = start + (r + j) % total
            k += 1
    return perm, np.array(order, dtype=np.int64)


def geometry_key(objs):
    """Content hash of the part meshes before unwrapping (names, corners, materials), independent of face order."""
    h = hashlib.sha256(repr((UV_SCHEMA, bpy.app.version_string)).encode())
    for o in sorted(objs, key=lambda o: o.name):
        co, lv, ls, lt, mi = mesh_arrays(o.data)
        perm, order = canonical_order(o.data)
        h.update(o.name.encode())
        h.update(co[lv[perm]].tobytes())
        h.update(lt[order].tobytes())
        h.update(mi[order].tobytes())
    return h.hexdigest()


def unwrap(objs, cache_dir=None):
    """One shared atlas for every part object (body materials only); eye screen and display keep fixed UVs.
    Blender's island packing is not deterministic, so with cache_dir the UVs of unchanged geometry are reused:
    stable textures between runs, and the bake cache can hit."""
    key = geometry_key(objs)
    cache_path = os.path.join(cache_dir, "uv_%s.npz" % key[:20]) if cache_dir else None
    if cache_path and os.path.exists(cache_path):
        with np.load(cache_path) as data:
            if all(o.name in data.files and len(data[o.name]) == len(o.data.loops) * 2 for o in objs):
                for o in objs:
                    if not o.data.uv_layers:
                        o.data.uv_layers.new(name="UVMap")
                    o.data.uv_layers[0].name = "UVMap"
                    perm, _ = canonical_order(o.data)
                    uv = np.empty((len(o.data.loops), 2), dtype=np.float32)
                    uv[perm] = data[o.name].reshape(-1, 2)
                    o.data.uv_layers[0].data.foreach_set("uv", uv.ravel())
                LOG.info("  UV cache hit (%s)", key[:12])
                return
    unwrap_fresh(objs)
    if cache_path:
        os.makedirs(cache_dir, exist_ok=True)
        for fn in os.listdir(cache_dir):
            if fn.startswith("uv_") and fn.endswith(".npz"):
                os.remove(os.path.join(cache_dir, fn))
        arrays = {}
        for o in objs:
            uv = np.empty(len(o.data.loops) * 2, dtype=np.float32)
            o.data.uv_layers[0].data.foreach_get("uv", uv)
            perm, _ = canonical_order(o.data)
            arrays[o.name] = uv.reshape(-1, 2)[perm].ravel()
        tmp = cache_path + ".tmp.npz"
        np.savez_compressed(tmp, **arrays)
        os.replace(tmp, cache_path)


def unwrap_fresh(objs):
    view_layer = bpy.context.view_layer
    for o in bpy.context.scene.objects:
        o.select_set(o in objs)
    view_layer.objects.active = objs[0]
    body_idx = {MAT_ORDER.index(k) for k in BODY_MATS}
    for o in objs:
        sel = [p.material_index in body_idx for p in o.data.polygons]
        o.data.polygons.foreach_set("select", sel)
        if not o.data.uv_layers:
            o.data.uv_layers.new(name="UVMap")
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.uv.smart_project(angle_limit=math.radians(60), island_margin=0.003, area_weight=0.0,
                             correct_aspect=True, scale_to_bounds=False)
    bpy.ops.object.mode_set(mode="OBJECT")
    eye_i, disp_i = MAT_ORDER.index("EyeScreen"), MAT_ORDER.index("Display")
    x0, x1, z0, z1 = DISPLAY_RECT
    for o in objs:
        me = o.data
        me.uv_layers[0].name = "UVMap"
        uv = me.uv_layers[0].data
        for p in me.polygons:
            if p.material_index not in (eye_i, disp_i):
                continue
            for li in p.loop_indices:
                co = me.vertices[me.loops[li].vertex_index].co
                if p.material_index == eye_i:
                    uv[li].uv = ((co.x + EYE_SCREEN_W / 2) / EYE_SCREEN_W, (co.z - (EYE_Z - EYE_SCREEN_H / 2)) / EYE_SCREEN_H)
                else:
                    uv[li].uv = ((co.x - x0) / (x1 - x0), (co.z - z0) / (z1 - z0))


def tri_count(me):
    return sum(len(p.vertices) - 2 for p in me.polygons)


# ----------------------------------------------------------------------------------------------
# Texture baking
# ----------------------------------------------------------------------------------------------


def write_png(path, pixels, width, height, channels):
    """Writes an 8-bit PNG. pixels: float array (h, w, c) in 0..1, row 0 = bottom (Blender order)."""
    arr = np.clip(np.round(pixels[::-1] * 255.0), 0, 255).astype(np.uint8)
    color_type = {1: 0, 3: 2, 4: 6}[channels]
    raw = b"".join(b"\x00" + arr[y].tobytes() for y in range(height))

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    with open(path, "wb") as fh:
        fh.write(png)


def enable_gpu(device="gpu"):
    if device == "cpu":
        bpy.context.scene.cycles.device = "CPU"
        LOG.info("  Cycles device: CPU")
        return
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
        for dev_type in ("METAL", "OPTIX", "CUDA", "HIP", "ONEAPI"):
            try:
                prefs.compute_device_type = dev_type
            except TypeError:
                continue
            prefs.get_devices()
            gpus = [d for d in prefs.devices if d.type != "CPU"]
            if gpus:
                for d in prefs.devices:
                    d.use = d.type != "CPU"     # GPU only: deterministic, and the CPU is busy with the QA
                bpy.context.scene.cycles.device = "GPU"
                LOG.info("  Cycles device: %s (%s)", dev_type, ", ".join(d.name for d in gpus))
                return
    except Exception as exc:  # noqa: BLE001 - GPU is optional
        LOG.warning("  GPU setup failed, using CPU: %s", exc)
    bpy.context.scene.cycles.device = "CPU"
    LOG.info("  Cycles device: CPU")


def srgb_to_linear(c):
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(c):
    c = np.clip(c, 0, 1)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1 / 2.4) - 0.055)


BAKE_SCHEMA = "1.8-c"   # bump when the baking code changes in a way the cache key cannot see
STATIC_TEXTURES = ("T_Robot_MetallicSmoothness.png", "T_Robot_Occlusion.png", "T_Robot_ColorMask.png",
                   "T_Robot_LightMask.png", "T_Robot_Normal.png")
LAYER_SCALE = 16383.75  # cached float maps as uint16 over 0..4: steps of 6e-5, far below one 8-bit step
COMPOSE_TOLERANCE = 2e-3


def bake_textures(objs, mats, size, ao_samples, tex_dir, cache_dir=None, check_compose=False, device="auto"):
    """Atlas textures. Skins are not baked one by one: every colour of the body is linear in the palette (palette
    colours only pass through mixes and multiplies with scalar factors), so base = C + sum(w_k * palette_k) with
    grey weight maps w_k. One bake with the palette black gives C, one bake per three palette colours (one per RGB
    channel) gives the weights - six bakes for any number of skins, each skin is then composed with numpy.
    check_compose bakes the default skin directly and compares. With cache_dir, the layers and the static textures
    of an unchanged model (geometry, UVs, materials, source textures, settings) are reused without baking."""
    os.makedirs(tex_dir, exist_ok=True)
    geo_key = bake_geometry_key(objs, size, ao_samples)
    key = bake_key(geo_key, mats)
    ao_cache = os.path.join(cache_dir, "ao_%s.npz" % geo_key[:20]) if cache_dir else None
    stamp_path = os.path.join(tex_dir, ".bake.stamp")
    cache_path = os.path.join(cache_dir, "bake_%s.npz" % key[:20]) if cache_dir else None
    layers = None
    if cache_path and os.path.exists(cache_path) and read_text(stamp_path) == key and \
            all(os.path.exists(os.path.join(tex_dir, n)) for n in STATIC_TEXTURES):
        layers = load_layers(cache_path)
        LOG.info("  bake cache hit (%s): static textures and skin layers reused", key[:12])
    if layers is None:
        if os.path.exists(stamp_path):
            os.remove(stamp_path)
        layers = bake_layers(objs, mats, size, ao_samples, tex_dir, check_compose, device, ao_cache)
        with open(stamp_path, "w", encoding="utf-8") as fh:
            fh.write(key)
        if cache_path:
            os.makedirs(cache_dir, exist_ok=True)
            for fn in os.listdir(cache_dir):
                if fn.startswith("bake_") and fn.endswith(".npz"):
                    os.remove(os.path.join(cache_dir, fn))
            save_layers(cache_path, layers)
    paths = {n: os.path.join(tex_dir, n) for n in STATIC_TEXTURES}

    def write_skin(skin):
        base, emit = compose_skin(layers, SKINS[skin])
        suffix = "" if skin == DEFAULT_SKIN else "_" + skin
        out = {}
        for name, arr in (("T_Robot_BaseColor%s.png" % suffix, base * layers["ao_soft"][..., None]),
                          ("T_Robot_Emission%s.png" % suffix, emit)):
            out[name] = os.path.join(tex_dir, name)
            write_png(out[name], linear_to_srgb(arr), size, size, 3)
        return out

    t0 = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for out in pool.map(write_skin, list(SKINS)):
            paths.update(out)
    LOG.info("  composed %d skins in %.1fs", len(SKINS), time.time() - t0)
    for key_ in ("eyes", "displays"):       # runtime atlases ship next to the baked set
        dst = os.path.join(tex_dir, os.path.basename(SRC[key_].filepath))
        shutil.copyfile(bpy.path.abspath(SRC[key_].filepath), dst)
        paths[os.path.basename(dst)] = dst
    return paths


def bake_layers(objs, mats, size, ao_samples, tex_dir, check_compose, device, ao_cache=None):
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    enable_gpu("cpu" if device == "cpu" else "gpu")
    scene.cycles.use_denoising = False
    bake = scene.render.bake
    bake.margin = 12
    bake.margin_type = "EXTEND"
    bake.use_clear = True
    bake.target = "IMAGE_TEXTURES"
    # Cycles bakes every selected object in its own session; on Metal each costs a full device sync. One joined
    # copy of all parts turns 35 sessions per pass into one (the parts share the atlas and the materials).
    for o in objs:
        o.hide_render = True
    joined = joined_copy(objs, "Bake_All")
    for o in scene.objects:
        o.select_set(o == joined)
    bpy.context.view_layer.objects.active = joined
    dummy = bpy.data.images.new("bake_dummy", 32, 32, alpha=False, float_buffer=True)

    results = {}

    def bake_pass(label, channel, colorspace, samples=4, bake_type="EMIT"):
        img = bpy.data.images.new("bake_" + label, size, size, alpha=False, float_buffer=True)
        img.colorspace_settings.name = "Linear Rec.709" if colorspace == "linear" else "Non-Color"
        img.generated_color = (0, 0, 0, 1)
        restore = []
        for key in MAT_ORDER:
            mat = mats[key]
            nt = mat.node_tree
            tex = nt.nodes.new("ShaderNodeTexImage")
            tex.image = img if key in BODY_MATS else dummy   # screens keep their own atlases
            tex.name = "BakeTarget"
            nt.nodes.active = tex
            if channel is not None and key in BODY_MATS:
                out = nt.nodes["Output"]
                em = nt.nodes.new("ShaderNodeEmission")
                em.name = "BakeEmission"
                em.inputs["Strength"].default_value = 1.0
                if isinstance(channel, tuple):     # three scalar channels packed into R, G, B
                    comb = nt.nodes.new("ShaderNodeCombineColor")
                    comb.name = "BakeCombine"
                    for i, ch in enumerate(channel):
                        nt.links.new(CHANNELS[key][ch], comb.inputs[i])
                    src = comb.outputs[0]
                else:
                    src = CHANNELS[key][channel]
                if src.type == "VALUE":
                    comb = nt.nodes.new("ShaderNodeCombineColor")
                    comb.name = "BakeCombine"
                    for i in range(3):
                        nt.links.new(src, comb.inputs[i])
                    nt.links.new(comb.outputs[0], em.inputs["Color"])
                else:
                    nt.links.new(src, em.inputs["Color"])
                nt.links.new(em.outputs[0], out.inputs["Surface"])
            restore.append(mat)
        scene.cycles.samples = samples
        t0 = time.time()
        bpy.ops.object.bake(type=bake_type, margin=bake.margin, use_clear=True)
        LOG.info("  baked %-9s (%s, %d spp) in %.1fs", label, bake_type, samples, time.time() - t0)
        for key, mat in zip(MAT_ORDER, restore):
            nt = mat.node_tree
            for nm in ("BakeTarget", "BakeEmission", "BakeCombine"):
                nd = nt.nodes.get(nm)
                if nd:
                    nt.nodes.remove(nd)
            if key in BODY_MATS:
                nt.links.new(nt.nodes["BSDF"].outputs[0], nt.nodes["Output"].inputs["Surface"])
        px = np.empty(size * size * 4, dtype=np.float32)
        img.pixels.foreach_get(px)
        bpy.data.images.remove(img)
        return px.reshape(size, size, 4)

    keys = sorted({k for _, k in PALETTE_NODES})
    set_palette({})
    c_base, c_emit = bake_pass("SkinConst", "base", "linear"), bake_pass("SkinConstEmit", "emit", "linear")
    layers = {"c_base": c_base[..., :3], "c_emit": c_emit[..., :3], "w_base": {}, "w_emit": {}}
    for i in range(0, len(keys), 3):
        pack = keys[i:i + 3]
        set_palette({k: tuple(1.0 if c == j else 0.0 for c in range(3)) for j, k in enumerate(pack)})
        b = bake_pass("Skin_" + "+".join(pack), "base", "linear")
        e = bake_pass("SkinEmit_" + "+".join(pack), "emit", "linear")
        for j, k in enumerate(pack):
            layers["w_base"][k] = b[..., j] - c_base[..., j]
            layers["w_emit"][k] = e[..., j] - c_emit[..., j]
    apply_skin(DEFAULT_SKIN)
    if check_compose:
        direct = (bake_pass("Base_" + DEFAULT_SKIN, "base", "linear")[..., :3],
                  bake_pass("Emit_" + DEFAULT_SKIN, "emit", "linear")[..., :3])
        composed = compose_skin(layers, SKINS[DEFAULT_SKIN])
        err = max(float(np.abs(d - c).max()) for d, c in zip(direct, composed))
        LOG.info("  skin composition check (%s): max difference %.2e (linear)", DEFAULT_SKIN, err)
        if err > COMPOSE_TOLERANCE:
            raise RuntimeError("composed skin differs from the direct bake by %.2e - a palette colour no longer "
                               "enters linearly (bake skins one by one or fix the material)" % err)
    results = {}
    rma = bake_pass("Rough+Metal+LightMask", ("rough", "metal", "mask_a"), "data")
    results["rough"], results["metal"], results["mask_a"] = rma[..., 0:1], rma[..., 1:2], rma[..., 2:3]
    results["mask"] = bake_pass("ColorMask", "mask", "data", samples=1)
    bake.normal_space = "TANGENT"
    results["normal"] = bake_pass("Normal", None, "data", samples=4, bake_type="NORMAL")
    ao_cached = load_ao(ao_cache)
    if ao_cached is not None:
        ao = ao_cached
        LOG.info("  AO cache hit: geometry, UVs and AO settings unchanged")
    else:
        world = scene.world or bpy.data.worlds.new("BakeWorld")
        scene.world = world
        world.light_settings.distance = 0.10
        ao = np.clip(bake_ao_configs(objs, mats, size, ao_samples, dummy)[..., 0], 0, 1)
        save_ao(ao_cache, ao)

    layers["ao_soft"] = 1.0 - (1.0 - ao) * 0.55
    metal = np.clip(results["metal"][..., 0], 0, 1)
    smooth = 1.0 - np.clip(results["rough"][..., 0], 0, 1)
    files = {
        "T_Robot_MetallicSmoothness.png": (np.stack([metal, metal, metal, smooth], axis=-1), 4),
        "T_Robot_Occlusion.png": (np.stack([ao, ao, ao], axis=-1), 3),
        "T_Robot_ColorMask.png": (np.clip(results["mask"][..., :3], 0, 1), 3),
        "T_Robot_LightMask.png": (np.clip(results["mask_a"][..., :1], 0, 1), 1),
        "T_Robot_Normal.png": (np.clip(results["normal"][..., :3], 0, 1), 3),
    }
    for name, (arr, ch) in files.items():
        path = os.path.join(tex_dir, name)
        write_png(path, arr, size, size, ch)
        LOG.info("  wrote %s", path)
    bpy.data.images.remove(dummy)
    remove_object(joined)
    for o in objs:
        o.hide_render = False
        o.select_set(True)
    return layers


def joined_copy(objs, name):
    """A temporary single object with copies of the given parts (same materials, UVs, normals, transforms)."""
    view_layer = bpy.context.view_layer
    copies = []
    for o in objs:
        c = o.copy()
        c.data = o.data.copy()
        c.hide_render = False
        bpy.context.scene.collection.objects.link(c)
        c.hide_set(False)
        copies.append(c)
    for o in bpy.context.scene.objects:
        o.select_set(o in copies)
    view_layer.objects.active = copies[0]
    if len(copies) > 1:
        bpy.ops.object.join()
    ob = view_layer.objects.active
    ob.name = name
    return ob


def remove_object(ob):
    me = ob.data
    bpy.data.objects.remove(ob, do_unlink=True)
    if me is not None and me.users == 0:
        bpy.data.meshes.remove(me)


def set_palette(colors):
    """Sets every palette node; keys missing in colors become black."""
    for nd, key in PALETTE_NODES:
        nd.outputs[0].default_value = (*colors.get(key, (0.0, 0.0, 0.0)), 1.0)


def compose_skin(layers, palette):
    """Linear base colour and emission of a skin from the baked layers (float32, no AO)."""
    base = layers["c_base"].astype(np.float32, copy=True)
    emit = layers["c_emit"].astype(np.float32, copy=True)
    for k, w in layers["w_base"].items():
        base += w[..., None] * np.asarray(palette[k], dtype=np.float32)
    for k, w in layers["w_emit"].items():
        emit += w[..., None] * np.asarray(palette[k], dtype=np.float32)
    return base, emit


def save_layers(path, layers):
    flat = {"c_base": layers["c_base"], "c_emit": layers["c_emit"], "ao_soft": layers["ao_soft"]}
    for kind in ("w_base", "w_emit"):
        for k, w in layers[kind].items():
            flat["%s__%s" % (kind, k)] = w
    q = {n: np.round(np.clip(a, 0.0, 4.0) * LAYER_SCALE).astype(np.uint16) for n, a in flat.items()}
    tmp = path + ".tmp.npz"
    np.savez_compressed(tmp, **q)
    os.replace(tmp, path)


def load_layers(path):
    layers = {"w_base": {}, "w_emit": {}}
    with np.load(path) as data:
        for n in data.files:
            a = data[n].astype(np.float32) / LAYER_SCALE
            if "__" in n:
                kind, k = n.split("__", 1)
                layers[kind][k] = a
            else:
                layers[n] = a
    return layers


def read_text(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return None


NODE_ATTRS = ("operation", "blend_type", "data_type", "interpolation_type", "use_clamp", "clamp_factor",
              "clamp_result", "projection", "projection_blend", "interpolation", "extension", "uv_map", "space",
              "noise_dimensions", "normalize", "vector_type", "convert_from", "convert_to", "mode", "target")


def _sig_value(v):
    if isinstance(v, float):
        return round(v, 7)
    try:
        return tuple(round(float(x), 7) for x in v)
    except TypeError:
        return v


def node_tree_signature(nt):
    """Everything in a material that changes a bake - except the palette colours (skins are composed)."""
    rows = []
    for nd in sorted(nt.nodes, key=lambda n: n.name):
        row = [nd.name, nd.bl_idname, nd.label]
        for a in NODE_ATTRS:
            if hasattr(nd, a):
                row.append((a, str(getattr(nd, a))))
        img = getattr(nd, "image", None)
        if img is not None:
            row.append(("image", img.name, img.filepath, img.colorspace_settings.name))
        for sock in nd.inputs:
            if not sock.is_linked and hasattr(sock, "default_value"):
                row.append((sock.identifier, _sig_value(sock.default_value)))
        if not nd.label.startswith("PAL_"):
            for sock in nd.outputs:
                if hasattr(sock, "default_value"):
                    row.append(("out", sock.identifier, _sig_value(sock.default_value)))
        rows.append(repr(row))
    rows += sorted(repr((l.from_node.name, l.from_socket.identifier, l.to_node.name, l.to_socket.identifier))
                   for l in nt.links)
    return "\n".join(rows)


def load_ao(path):
    if not path or not os.path.exists(path):
        return None
    with np.load(path) as data:
        return data["ao"].astype(np.float32) / LAYER_SCALE


def save_ao(path, ao):
    if not path:
        return
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    for fn in os.listdir(folder):
        if fn.startswith("ao_") and fn.endswith(".npz"):
            os.remove(os.path.join(folder, fn))
    tmp = path + ".tmp.npz"
    np.savez_compressed(tmp, ao=np.round(np.clip(ao, 0.0, 4.0) * LAYER_SCALE).astype(np.uint16))
    os.replace(tmp, path)


def bake_key(geo_key, mats):
    """Content hash of everything the bakes depend on: geometry key + materials + source textures."""
    h = hashlib.sha256(geo_key.encode())
    for key in MAT_ORDER:
        sig = node_tree_signature(mats[key].node_tree)
        h.update(key.encode())
        h.update(sig.encode())
    for k in sorted(SRC):
        with open(bpy.path.abspath(SRC[k].filepath), "rb") as fh:
            h.update(fh.read())
    return h.hexdigest()


def bake_geometry_key(objs, size, ao_samples):
    """Content hash of what the AO depends on: meshes, UVs, material slots, size and samples (in canonical corner
    order, see canonical_order) and the code that makes normals and bakes. The normals themselves are left out:
    their float sums depend on the face order, they follow from the geometry and apply_weighted_normals."""
    h = hashlib.sha256()
    h.update(repr((BAKE_SCHEMA, bpy.app.version_string, size, ao_samples, MAT_ORDER, BODY_MATS)).encode())
    for fn in (apply_weighted_normals, bake_layers, bake_ao_configs, joined_copy):
        h.update(inspect.getsource(fn).encode())
    for o in sorted(objs, key=lambda o: o.name):
        me = o.data
        h.update(repr((o.name, sorted((k, str(o[k])) for k in o.keys()),
                       [sl.material.name if sl.material else None for sl in o.material_slots])).encode())
        h.update(np.array(o.matrix_world, dtype=np.float64).tobytes())
        co, lv, ls, lt, mi = mesh_arrays(me)
        perm, order = canonical_order(me)
        h.update(co[lv[perm]].tobytes())
        h.update(lt[order].tobytes())
        h.update(mi[order].tobytes())
        for uvl in me.uv_layers:
            uv = np.empty(len(me.loops) * 2, dtype=np.float32)
            uvl.data.foreach_get("uv", uv)
            h.update(uvl.name.encode())
            h.update(uv.reshape(-1, 2)[perm].tobytes())
    return h.hexdigest()


def bake_ao_configs(objs, mats, size, samples, dummy):
    """AO must only see one valid look at a time: all head/feet/... variants overlap in space and would darken
    each other. Bakes several configurations into one image; the default look is baked last so the core body gets
    the AO of the standard robot."""
    scene = bpy.context.scene
    core = [o for o in objs if not o.get("slot")]
    by_slot = collections.OrderedDict()
    for o in objs:
        if o.get("slot"):
            by_slot.setdefault(o["slot"], []).append(o)
    passes = []
    n = max(len(v) for v in by_slot.values())
    for i in range(n):
        passes.append(core + [vs[i % len(vs)] for vs in by_slot.values()])
    defaults = [o for o in objs if o.get("slot") and SLOTS[o["slot"]][0] == o["variant"]]
    passes.append(core + defaults)
    img = bpy.data.images.new("bake_AO", size, size, alpha=False, float_buffer=True)
    img.colorspace_settings.name = "Non-Color"
    for key in MAT_ORDER:
        nt = mats[key].node_tree
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = img if key in BODY_MATS else dummy
        tex.name = "BakeTarget"
        nt.nodes.active = tex
    scene.cycles.samples = samples
    t0 = time.time()
    for o in scene.objects:
        o.hide_render = True
    for i, visible in enumerate(passes):
        conf = joined_copy(visible, "Bake_AO_%d" % i)     # one bake session per configuration (see bake_layers)
        for o in scene.objects:
            o.select_set(o == conf)
        bpy.context.view_layer.objects.active = conf
        # small margin: neighbouring islands of other variants must not be overwritten
        bpy.ops.object.bake(type="AO", margin=2 if i < len(passes) - 1 else 4, use_clear=(i == 0))
        remove_object(conf)
    for key in MAT_ORDER:
        nt = mats[key].node_tree
        nt.nodes.remove(nt.nodes["BakeTarget"])
    LOG.info("  baked AO        (%d configurations, %d spp) in %.1fs", len(passes), samples, time.time() - t0)
    px = np.empty(size * size * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    bpy.data.images.remove(img)
    return px.reshape(size, size, 4)


def build_baked_material(tex_paths, name="M_Robot", tint=None):
    mat = bpy.data.materials.new(name)
    if mat.node_tree is None:
        mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (300, 0)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (600, 0)
    nt.links.new(bsdf.outputs[0], out.inputs["Surface"])

    def tex(name, colorspace, y):
        img = bpy.data.images.load(tex_paths[name], check_existing=True)
        img.colorspace_settings.name = colorspace
        nd = nt.nodes.new("ShaderNodeTexImage")
        nd.image = img
        nd.location = (-400, y)
        return nd

    base = tex("T_Robot_BaseColor.png", "sRGB", 300)
    if tint is None:
        nt.links.new(base.outputs["Color"], bsdf.inputs["Base Color"])
    else:
        mul = nt.nodes.new("ShaderNodeMix")
        mul.data_type, mul.blend_type = "RGBA", "MULTIPLY"
        mul.inputs[0].default_value = 1.0
        nt.links.new(base.outputs["Color"], mul.inputs[6])
        rgb = nt.nodes.new("ShaderNodeRGB")
        rgb.name = "TintColor"
        rgb.outputs[0].default_value = (*tint, 1.0)
        nt.links.new(rgb.outputs[0], mul.inputs[7])
        nt.links.new(mul.outputs[2], bsdf.inputs["Base Color"])
    ms = tex("T_Robot_MetallicSmoothness.png", "Non-Color", 0)
    sep = nt.nodes.new("ShaderNodeSeparateColor")
    sep.location = (-150, 0)
    nt.links.new(ms.outputs["Color"], sep.inputs[0])
    nt.links.new(sep.outputs[0], bsdf.inputs["Metallic"])
    inv = nt.nodes.new("ShaderNodeMath")
    inv.operation = "SUBTRACT"
    inv.inputs[0].default_value = 1.0
    inv.location = (-150, -150)
    nt.links.new(ms.outputs["Alpha"], inv.inputs[1])
    nt.links.new(inv.outputs[0], bsdf.inputs["Roughness"])
    nrm = tex("T_Robot_Normal.png", "Non-Color", -650)
    nmap = nt.nodes.new("ShaderNodeNormalMap")
    nmap.location = (-150, -650)
    nmap.uv_map = "UVMap"
    nt.links.new(nrm.outputs["Color"], nmap.inputs["Color"])
    nt.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
    em = tex("T_Robot_Emission.png", "sRGB", -350)
    nt.links.new(em.outputs["Color"], bsdf.inputs["Emission Color"])
    bsdf.inputs["Emission Strength"].default_value = 5.0
    mat.diffuse_color = (0.78, 0.8, 0.83, 1.0)
    return mat


FINAL_SLOTS = {"White": 0, "Dark": 0, "Grey": 0, "Visor": 0, "Orange": 0, "Fabric": 1, "EyeScreen": 2, "Display": 3}


def remap_materials(ob, final_mats):
    """Authoring slots -> M_Robot / M_RobotFabric / M_RobotEyes / M_RobotDisplay (only the used ones)."""
    me = ob.data
    idx = [FINAL_SLOTS[MAT_ORDER[p.material_index]] for p in me.polygons]
    used = sorted(set(idx))
    me.materials.clear()
    for u in used:
        me.materials.append(final_mats[u])
    lookup = {u: i for i, u in enumerate(used)}
    me.polygons.foreach_set("material_index", [lookup[i] for i in idx])


def combine_objects(objs, name, coll):
    """Joined copy of several part objects (vertex groups and custom normals are kept)."""
    copies = []
    for o in objs:
        c = o.copy()
        c.data = o.data.copy()
        c.modifiers.clear()
        c.parent = None
        coll.objects.link(c)
        copies.append(c)
    for o in bpy.context.scene.objects:
        o.select_set(o in copies)
    bpy.context.view_layer.objects.active = copies[0]
    bpy.ops.object.join()
    res = bpy.context.view_layer.objects.active
    res.name = name
    res.data.name = name
    return res


# ----------------------------------------------------------------------------------------------
# Rig
# ----------------------------------------------------------------------------------------------


def build_armature(coll):
    arm_data = bpy.data.armatures.new("Robot_Rig")
    arm_data.display_type = "OCTAHEDRAL"
    arm = bpy.data.objects.new("Robot_Armature", arm_data)
    coll.objects.link(arm)
    arm.show_in_front = True
    bpy.context.view_layer.objects.active = arm
    for o in bpy.context.scene.objects:
        o.select_set(o == arm)
    bpy.ops.object.mode_set(mode="EDIT")
    eb = arm_data.edit_bones

    def bone(name, head, tail, parent=None, connect=False, roll_up=None):
        b = eb.new(name)
        b.head = Vector(head)
        b.tail = Vector(tail)
        if roll_up is not None:
            b.align_roll(Vector(roll_up))
        if parent:
            b.parent = eb[parent]
            b.use_connect = connect
        b.use_deform = True
        return b

    bone("Root", J["root"], J["root"] + Vector((0, 0.18, 0)), roll_up=(0, 0, 1))
    bone("Pelvis", J["pelvis"], J["spine"], "Root", roll_up=(0, -1, 0))
    bone("Spine", J["spine"], J["neck"], "Pelvis", connect=True, roll_up=(0, -1, 0))
    bone("Head", J["neck"], J["head_top"], "Spine", connect=True, roll_up=(0, -1, 0))
    bone("Eyes", J["eyes"], J["eyes"] + Vector((0, -0.08, 0)), "Head", roll_up=(0, 0, 1))
    # accessory sockets (deforming, so accessories can be skinned rigidly like every other part)
    bone("Socket_Head", (0, 0, 1.30), (0, 0, 1.40), "Head", roll_up=(0, -1, 0))
    bone("Socket_Neck", (0, -0.17, 0.872), (0, -0.25, 0.872), "Spine", roll_up=(0, 0, 1))
    bone("Socket_Back", (0, 0.22, 0.77), (0, 0.30, 0.77), "Spine", roll_up=(0, 0, 1))
    for side in ("L", "R"):
        bone("Shoulder_" + side, J["clav_" + side], J["shoulder_" + side], "Spine", roll_up=(0, 0, 1))
        bone("UpperArm_" + side, J["shoulder_" + side], J["elbow_" + side], "Shoulder_" + side, True, (0, -1, 0))
        bone("LowerArm_" + side, J["elbow_" + side], J["wrist_" + side], "UpperArm_" + side, True, (0, -1, 0))
        bone("Hand_" + side, J["wrist_" + side], J["knuckle_" + side], "LowerArm_" + side, True, (0, -1, 0))
        bone("Fingers_" + side, J["knuckle_" + side], J["knuckle_" + side] + J["finger_dir_" + side] * 0.05,
             "Hand_" + side, True, (0, -1, 0))
        bone("Thumb_" + side, J["thumb_" + side], J["thumb_" + side] + J["thumb_dir_" + side] * 0.045,
             "Hand_" + side, False, (0, 0, 1))
        # tool socket: grip point inside the curled fingers, pointing along the hand (no weights, exported for props)
        s = 1 if side == "L" else -1
        grip = J["knuckle_" + side] + Vector((-s * 0.030, -0.005, 0.0))
        bone("Prop_" + side, grip, grip + J["dir_fore_" + side] * 0.10, "Hand_" + side, False, (0, -1, 0))
        bone("UpperLeg_" + side, J["hip_" + side], J["knee_" + side], "Pelvis", roll_up=(0, -1, 0))
        bone("LowerLeg_" + side, J["knee_" + side], J["ankle_" + side], "UpperLeg_" + side, True, (0, -1, 0))
        bone("Foot_" + side, J["ankle_" + side], J["toe_" + side], "LowerLeg_" + side, True, (0, 0, 1))
    bpy.ops.object.mode_set(mode="OBJECT")
    for pb in arm.pose.bones:
        pb.rotation_mode = "QUATERNION"
    return arm


def bind_mesh(ob, arm):
    ob.parent = arm
    ob.matrix_parent_inverse = Matrix.Identity(4)
    mod = ob.modifiers.new("Armature", "ARMATURE")
    mod.object = arm
    mod.use_vertex_groups = True
    mod.use_deform_preserve_volume = False


# ----------------------------------------------------------------------------------------------
# Animation system: poses in armature-aligned delta rotations, analytic two-bone leg IK
# ----------------------------------------------------------------------------------------------

THIGH = 0.160
SHIN = 0.150
ANKLE_H = 0.095
TOE_OFF = Vector((0, -0.200, -ANKLE_H))    # toe contact relative to ankle
HEEL_OFF = Vector((0, 0.102, -ANKLE_H))    # heel contact relative to ankle

ARM_BONES = ["Shoulder", "UpperArm", "LowerArm", "Hand", "Fingers", "Thumb"]
CHANNEL_DEFAULTS = {
    "pelvis": (0.0, 0.0, -0.012),
    "pelvis_rot": (0.0, 0.0, 0.0),
    "spine": (0.0, 0.0, 0.0),
    "head": (0.0, 0.0, 0.0),
    "eyes": (1.0, 0.0, 0.0),        # scale_z, offset_x, offset_z
    "root": (0.0, 0.0, 0.0),        # x, y (m, world), yaw (deg) - root motion for turns and spins
    "root_z": (0.0,),               # height (m) - root motion for climbing
}
for _s in ("L", "R"):
    CHANNEL_DEFAULTS["shoulder_" + _s] = (0.0, 0.0, 0.0)
    CHANNEL_DEFAULTS["uarm_" + _s] = (0.0, 0.0, 0.0)
    CHANNEL_DEFAULTS["larm_" + _s] = (0.0, 0.0, 0.0)
    CHANNEL_DEFAULTS["hand_" + _s] = (0.0, 0.0, 0.0)
    CHANNEL_DEFAULTS["fing_" + _s] = (15.0,)
    CHANNEL_DEFAULTS["thumb_" + _s] = (10.0,)
    CHANNEL_DEFAULTS["foot_" + _s] = (0.0, 0.0, 0.0, 0.0)   # outward, forward(+y back), lift, pitch
    CHANNEL_DEFAULTS["footyaw_" + _s] = (0.0,)               # world yaw of the foot (deg), about its own ankle
    # arm IK: knuckle target x (outward), y (+ = back), z in world (left convention, mirrored), elbow pole, weight
    CHANNEL_DEFAULTS["handik_" + _s] = (0.30, -0.20, 1.0, 1.0, 0.0, -0.3, 0.0)


def P(**kw):
    """Pose dict with defaults. Left/right channels use left-side conventions and are mirrored."""
    pose = dict(CHANNEL_DEFAULTS)
    for k, v in kw.items():
        if k.endswith("_LR"):
            pose[k[:-3] + "_L"] = v
            pose[k[:-3] + "_R"] = v
        else:
            pose[k] = v
    return {k: tuple(v) for k, v in pose.items()}


def arm(rx=0.0, abd=0.0, twist=0.0):
    """Arm rotation in left-side convention: rx<0 swings forward, abd>0 moves the arm outwards."""
    return (rx, -abd, twist)


def curl(deg):
    return (deg,)


def ease(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def lerp(a, b, t):
    return a + (b - a) * t


def lerp_pose(a, b, t):
    return {k: tuple(lerp(x, y, t) for x, y in zip(a[k], b[k])) for k in a}


def keyed(frame, keys):
    """keys: list of (frame, pose[, 'linear'|'smooth'|'out'|'in']). Smooth interpolation between keys."""
    if frame <= keys[0][0]:
        return keys[0][1]
    for i in range(len(keys) - 1):
        f0, p0 = keys[i][0], keys[i][1]
        f1, p1 = keys[i + 1][0], keys[i + 1][1]
        if f0 <= frame <= f1:
            t = (frame - f0) / max(1e-6, f1 - f0)
            mode = keys[i + 1][2] if len(keys[i + 1]) > 2 else "smooth"
            if mode == "smooth":
                t = ease(t)
            elif mode == "out":
                t = 1 - (1 - t) ** 2
            elif mode == "in":
                t = t * t
            return lerp_pose(p0, p1, t)
    return keys[-1][1]


def q_euler(rx, ry, rz):
    return Euler((math.radians(rx), math.radians(ry), math.radians(rz)), "XYZ").to_quaternion()


def rx_q(deg):
    return Quaternion((1, 0, 0), math.radians(deg))


class Solver:
    def __init__(self, arm_ob):
        self.arm = arm_ob
        bones = arm_ob.data.bones
        self.rest = {b.name: b.matrix_local.to_quaternion() for b in bones}
        self.rest_inv = {k: q.inverted() for k, q in self.rest.items()}
        self.head = {b.name: b.head_local.copy() for b in bones}
        self.parent = {b.name: (b.parent.name if b.parent else None) for b in bones}
        # local axis of the Eyes bone that matches armature Z (for blink scaling)
        m = bones["Eyes"].matrix_local.to_3x3()
        self.eye_axis = max(range(3), key=lambda i: abs(m.col[i].z))
        self.ik_error = 0.0

    def local(self, bone, q_delta):
        return self.rest_inv[bone] @ q_delta @ self.rest[bone]

    def leg_ik(self, side, w_pelvis, pelvis_off, foot, foot_yaw=0.0, root=(0.0, 0.0, 0.0), pelvis_yaw=0.0,
               root_z=0.0):
        s = 1 if side == "L" else -1
        outward, fy, lift, pitch = foot
        q_fyaw = Quaternion((0, 0, 1), math.radians(foot_yaw))
        q_root = Quaternion((0, 0, 1), math.radians(root[2]))
        q_root_inv = q_root.inverted()
        hip_rest = self.head["UpperLeg_" + side]
        pel = self.head["Pelvis"]
        hip = pel + pelvis_off + w_pelvis @ (hip_rest - pel)
        ankle_rest = self.head["Foot_" + side]
        base = Vector((ankle_rest.x + s * outward, fy, ankle_rest.z + lift))
        contact = TOE_OFF if pitch > 0 else HEEL_OFF
        target = base + q_fyaw @ (contact + rx_q(pitch) @ (-contact))
        # targets are world positions; the rig below Root lives in the (possibly turning) root frame
        # x/y of the feet are world positions (turns), the height is relative to the (climbing) root
        target = q_root_inv @ (target - Vector((root[0], root[1], 0.0)))
        v = w_pelvis.inverted() @ (target - hip)
        d_raw = v.length
        d = max(abs(THIGH - SHIN) + 1e-3, min(THIGH + SHIN - 1e-4, d_raw))
        self.ik_error = max(self.ik_error, abs(d_raw - d))
        t = v.normalized()
        # knee pole: perpendicular to the hip->ankle line inside the sagittal plane, pointing forward/up
        rel = Quaternion((0, 0, 1), math.radians(foot_yaw - root[2] - pelvis_yaw))
        tl = rel.inverted() @ t
        pole = Vector((0.0, tl.z, -tl.y))
        if pole.length < 1e-6:
            pole = Vector((0.0, -1.0, 0.0))
        pole = (rel @ pole).normalized()
        delta = math.acos(max(-1, min(1, (THIGH ** 2 + d ** 2 - SHIN ** 2) / (2 * THIGH * d))))
        gamma = math.acos(max(-1, min(1, (THIGH ** 2 + SHIN ** 2 - d ** 2) / (2 * THIGH * SHIN))))
        knee = t * (THIGH * math.cos(delta)) + pole * (THIGH * math.sin(delta))
        u1 = knee.normalized()
        hinge = pole.cross(u1).normalized()
        up = -u1
        q_upper = Matrix((hinge, up.cross(hinge), up)).transposed().to_quaternion()
        q_lower = Quaternion((1, 0, 0), math.pi - gamma)
        w_lower = w_pelvis @ q_upper @ q_lower
        q_foot = w_lower.inverted() @ q_root_inv @ q_fyaw @ rx_q(pitch)
        return q_upper, q_lower, q_foot

    def arm_ik(self, side, w_shoulder, shoulder_pos, q_hand, target, pole):
        """Upper/lower arm rotations (armature-aligned deltas) that put the knuckle (Fingers head) on target.
        Works in the root frame; w_shoulder = world delta rotation of the Shoulder bone; pole = elbow direction."""
        h = self.head
        v1 = h["LowerArm_" + side] - h["UpperArm_" + side]
        v2 = (h["Hand_" + side] - h["LowerArm_" + side]) + q_hand @ (h["Fingers_" + side] - h["Hand_" + side])
        d = target - shoulder_pos
        dist = d.length

        def reach(phi):
            return (v1 + rx_q(-phi) @ v2).length

        lo, hi = 0.0, 150.0          # elbow flexion (deg); reach shrinks monotonically
        if dist >= reach(lo):
            phi = lo
        elif dist <= reach(hi):
            phi = hi
        else:
            for _ in range(40):
                mid = (lo + hi) / 2
                if reach(mid) > dist:
                    lo = mid
                else:
                    hi = mid
            phi = (lo + hi) / 2
        self.arm_error = abs(reach(phi) - dist)
        w_loc = v1 + rx_q(-phi) @ v2
        xl = w_loc.normalized()
        back = Vector((0, 1, 0))      # the elbow sticks out backwards when the forearm flexes forwards
        yl = (back - xl * back.dot(xl)).normalized()
        xw = d.normalized()
        yw = pole - xw * pole.dot(xw)
        if yw.length < 1e-6:
            yw = Vector((0, 0, -1)) - xw * (-xw.z)
        yw.normalize()
        fl = Matrix((xl, yl, xl.cross(yl))).transposed()
        fw = Matrix((xw, yw, xw.cross(yw))).transposed()
        r_world = (fw @ fl.transposed()).to_quaternion()
        q_upper = w_shoulder.inverted() @ r_world
        return q_upper, rx_q(-phi)

    def solve(self, pose):
        out = {}
        q_pel = q_euler(*pose["pelvis_rot"])
        pel_off = Vector(pose["pelvis"])
        out["Pelvis"] = (self.rest_inv["Pelvis"] @ pel_off, self.local("Pelvis", q_pel), None)
        out["Spine"] = (None, self.local("Spine", q_euler(*pose["spine"])), None)
        out["Head"] = (None, self.local("Head", q_euler(*pose["head"])), None)
        sz, ox, oz = pose["eyes"]
        scale = [1.0, 1.0, 1.0]
        scale[self.eye_axis] = sz
        out["Eyes"] = (self.rest_inv["Eyes"] @ Vector((ox, 0, oz)), Quaternion(), Vector(scale))
        for side, s in (("L", 1), ("R", -1)):
            def mirrored(v):
                return (v[0], s * v[1], s * v[2])
            out["Shoulder_" + side] = (None, self.local("Shoulder_" + side, q_euler(*mirrored(pose["shoulder_" + side]))), None)
            out["UpperArm_" + side] = (None, self.local("UpperArm_" + side, q_euler(*mirrored(pose["uarm_" + side]))), None)
            out["LowerArm_" + side] = (None, self.local("LowerArm_" + side, q_euler(*mirrored(pose["larm_" + side]))), None)
            q_hand = q_euler(*mirrored(pose["hand_" + side]))
            out["Hand_" + side] = (None, self.local("Hand_" + side, q_hand), None)
            ik = pose["handik_" + side]
            if ik[6] > 1e-4:
                # chain positions in the root frame (targets are world positions, like the feet)
                rx_, ry_, ryaw = pose["root"]
                q_root_inv = Quaternion((0, 0, 1), math.radians(ryaw)).inverted()
                tgt = q_root_inv @ (Vector((s * ik[0], ik[1], ik[2])) - Vector((rx_, ry_, pose["root_z"][0])))
                pole = q_root_inv @ Vector((s * ik[3], ik[4], ik[5]))
                h = self.head
                w_sp = q_pel @ q_euler(*pose["spine"])
                w_sh = w_sp @ q_euler(*mirrored(pose["shoulder_" + side]))
                spine_pos = h["Pelvis"] + pel_off + q_pel @ (h["Spine"] - h["Pelvis"])
                sh_pos = spine_pos + w_sp @ (h["Shoulder_" + side] - h["Spine"])
                arm_pos = sh_pos + w_sh @ (h["UpperArm_" + side] - h["Shoulder_" + side])
                qu, ql = self.arm_ik(side, w_sh, arm_pos, q_hand, tgt, pole)
                fk_u = q_euler(*mirrored(pose["uarm_" + side]))
                fk_l = q_euler(*mirrored(pose["larm_" + side]))
                wgt = min(1.0, ik[6])
                self.ik_error = max(self.ik_error, self.arm_error * wgt)
                out["UpperArm_" + side] = (None, self.local("UpperArm_" + side, fk_u.slerp(qu, wgt)), None)
                out["LowerArm_" + side] = (None, self.local("LowerArm_" + side, fk_l.slerp(ql, wgt)), None)
            out["Fingers_" + side] = (None, self.local("Fingers_" + side, q_euler(0, s * pose["fing_" + side][0], 0)), None)
            out["Thumb_" + side] = (None, self.local("Thumb_" + side, q_euler(0, s * pose["thumb_" + side][0], 0)), None)
            qu, ql, qf = self.leg_ik(side, q_pel, pel_off, pose["foot_" + side], pose["footyaw_" + side][0],
                                     pose["root"], pose["pelvis_rot"][2], pose["root_z"][0])
            out["UpperLeg_" + side] = (None, self.local("UpperLeg_" + side, qu), None)
            out["LowerLeg_" + side] = (None, self.local("LowerLeg_" + side, ql), None)
            out["Foot_" + side] = (None, self.local("Foot_" + side, qf), None)
        rx_, ry_, ryaw = pose["root"]
        out["Root"] = (self.rest_inv["Root"] @ Vector((rx_, ry_, pose["root_z"][0])),
                       self.local("Root", Quaternion((0, 0, 1), math.radians(ryaw))), None)
        return out


def write_action(arm_ob, solver, name, n_frames, pose_fn, loop):
    act = bpy.data.actions.new(name)
    act.use_fake_user = True
    slot = act.slots.new(id_type="OBJECT", name=arm_ob.name)
    cb = anim_utils.action_ensure_channelbag_for_slot(act, slot)
    solver.ik_error = 0.0
    samples = []
    worst = (0.0, 0)
    for f in range(n_frames + 1):
        before = solver.ik_error
        solver.ik_error = 0.0
        samples.append(solver.solve(pose_fn(f)))
        if solver.ik_error > worst[0]:
            worst = (solver.ik_error, f)
        solver.ik_error = max(before, solver.ik_error)
    if loop:
        samples[-1] = samples[0]
    curves = {}
    prev_q = {}
    for f, sample in enumerate(samples):
        for bone, (loc, q, scale) in sample.items():
            q = q.normalized()
            if bone in prev_q and prev_q[bone].dot(q) < 0:
                q = -q
            prev_q[bone] = q
            chans = [("rotation_quaternion", list(q))]
            if loc is not None:
                chans.append(("location", list(loc)))
            if scale is not None:
                chans.append(("scale", list(scale)))
            for prop, vals in chans:
                for i, v in enumerate(vals):
                    curves.setdefault((bone, prop, i), []).extend((float(f), float(v)))
    for (bone, prop, i), co in curves.items():
        fc = cb.fcurves.new('pose.bones["%s"].%s' % (bone, prop), index=i, group_name=bone)
        fc.keyframe_points.add(len(co) // 2)
        fc.keyframe_points.foreach_set("co", co)
        for kp in fc.keyframe_points:
            kp.interpolation = "LINEAR"
        fc.update()
    act.use_frame_range = True
    act.frame_start = 0
    act.frame_end = n_frames
    act.use_cyclic = loop
    act["cube_escape_loop"] = int(loop)
    if solver.ik_error > 0.003:
        LOG.warning("  %s: IK clamped by %.1f mm (frame %d)", name, solver.ik_error * 1000, worst[1])
    return act, slot


def assign_action(arm_ob, act):
    ad = arm_ob.animation_data or arm_ob.animation_data_create()
    ad.action = act
    ad.action_slot = act.slots[0]


# ---------------------------------- gait helpers ----------------------------------


def gait_foot(u, stride, stance, lift, p_strike, p_off, kick=0.0, center=0.0, outward=0.0):
    """Foot channel for an in-place locomotion cycle. u = 0 at heel strike."""
    u %= 1.0
    if u < stance:
        s = u / stance
        y = center - stride / 2 + stride * s
        p = 0.0
        if s < 0.18:
            p = p_strike * (1 - ease(s / 0.18))
        elif s > 0.62:
            p = p_off * ((s - 0.62) / 0.38) ** 1.6
        return (outward, y, 0.0, p)
    w = (u - stance) / (1 - stance)
    e = ease(w)
    y = lerp(center + stride / 2, center - stride / 2, e) + kick * math.sin(math.pi * w) * (1 - w) * 1.6
    z = lift * math.sin(math.pi * w) ** 0.9
    p = lerp(p_off, p_strike, ease(min(1.0, w * 2.2)))
    return (outward, y, z, p)


def blink(f, center, width=2.5):
    d = abs(f - center) / width
    return 1.0 - 0.92 * max(0.0, 1.0 - d)


# ---------------------------------- the ten animations ----------------------------------

STAND = P()
Anim = collections.namedtuple("Anim", "name frames loop fn desc group unity")
ANIMS = []  # Anim records in registration order
GROUPS = ["Basis", "Phase1", "Phase2", "Phase3", "Phase4", "Phase5", "Phase6", "Phase7"]
GROUP_LABELS = {"Basis": "Basis", "Phase1": "Spielsysteme", "Phase2": "Werkzeuge & Interaktion", "Phase3": "Charakter",
                "Phase4": "Tänze", "Phase5": "Emotes", "Phase6": "Klettern & Hängen", "Phase7": "Tempo & Ducken"}


def anim(name, frames, loop, desc, group="Basis", unity=""):
    """Registers a clip. unity: import hint (e.g. 'Additive', 'Oberkörper-Maske', 'Root Motion')."""
    def deco(fn):
        ANIMS.append(Anim(name, frames, loop, fn, desc, group, unity))
        return fn
    return deco


@anim("Idle", 120, True, "Atmen, neugierige Kopfbewegungen, Blinzeln")
def idle(f):
    t = f / 120.0
    br = math.sin(2 * math.pi * 2 * t)
    look = keyed(f, [
        (0, P(head=(0, 0, 0))),
        (14, P(head=(-4, 8, 22))),
        (42, P(head=(-3, 9, 24))),
        (58, P(head=(0, 0, 0))),
        (72, P(head=(6, -7, -16))),
        (98, P(head=(5, -8, -18))),
        (113, P(head=(0, 0, 0))),
        (120, P(head=(0, 0, 0))),
    ])["head"]
    yaw = look[2]
    eyes = (min(blink(f, 52), blink(f, 107)), 0.010 * max(-1, min(1, yaw / 20.0)), 0.0)
    return P(
        pelvis=(0.003 * math.sin(2 * math.pi * t), 0, -0.014 - 0.004 * (0.5 + 0.5 * br)),
        spine=(1.2 * br, 0, 1.5 * math.sin(2 * math.pi * t)),
        head=(look[0] - 0.8 * br, look[1], look[2]),
        shoulder_LR=(0, -1.5 * (0.5 + 0.5 * br), 0),
        uarm_L=arm(1.5 * br, 2 + 1.0 * br),
        uarm_R=arm(1.5 * br, 2 + 1.0 * br),
        larm_LR=arm(-6),
        fing_LR=curl(18 + 4 * math.sin(2 * math.pi * t)),
        eyes=eyes,
    )


WALK_FRAMES = 20
WALK_STRIDE = 0.30
WALK_STANCE = 0.60


@anim("Walk", WALK_FRAMES, True, "freundlicher mechanischer Gang")
def walk(f):
    t = f / WALK_FRAMES
    c = math.cos(2 * math.pi * t)
    feet = {}
    for side, off in (("L", 0.0), ("R", 0.5)):
        feet[side] = gait_foot(t + off, WALK_STRIDE, WALK_STANCE, 0.075, -12, 24, outward=0.004)
    return P(
        pelvis=(0.012 * math.sin(2 * math.pi * t), 0, -0.046 - 0.012 * math.cos(4 * math.pi * t)),
        pelvis_rot=(2, 0, -6 * c),
        spine=(4 + 1.5 * math.cos(4 * math.pi * t), -1.5 * math.sin(2 * math.pi * t), 5 * c),
        head=(-4 - 1.5 * math.cos(4 * math.pi * t), 1.5 * math.sin(2 * math.pi * t), -1.5 * c),
        uarm_L=arm(20 * c, 5),
        uarm_R=arm(-20 * c, 5),
        larm_L=arm(-14 - 12 * max(0.0, -c)),
        larm_R=arm(-14 - 12 * max(0.0, c)),
        shoulder_L=(0, -2 * max(0.0, -c), 0),
        shoulder_R=(0, -2 * max(0.0, c), 0),
        fing_LR=curl(28),
        foot_L=feet["L"],
        foot_R=feet["R"],
    )


RUN_FRAMES = 16
RUN_STRIDE = 0.30
RUN_STANCE = 0.36


@anim("Run", RUN_FRAMES, True, "dynamischer Lauf mit Flugphase")
def run(f):
    t = f / RUN_FRAMES
    c = math.cos(2 * math.pi * t)
    feet = {}
    for side, off in (("L", 0.0), ("R", 0.5)):
        feet[side] = gait_foot(t + off, RUN_STRIDE, RUN_STANCE, 0.068, -6, 28, kick=0.02, outward=0.004)
    bob = math.cos(4 * math.pi * (t - RUN_STANCE / 2))
    return P(
        pelvis=(0.008 * math.sin(2 * math.pi * t), 0, -0.058 - 0.018 * bob),
        pelvis_rot=(2, 0, -9 * c),
        spine=(14 + 2 * bob, 0, 9 * c),
        head=(-12 - 2 * bob, 0, -3 * c),
        uarm_L=arm(38 * c, 9),
        uarm_R=arm(-38 * c, 9),
        larm_L=arm(-78 + 12 * c),
        larm_R=arm(-78 - 12 * c),
        hand_LR=arm(-8),
        shoulder_L=(0, -3 * max(0.0, -c), 0),
        shoulder_R=(0, -3 * max(0.0, c), 0),
        fing_LR=curl(72),
        thumb_LR=curl(45),
        foot_L=feet["L"],
        foot_R=feet["R"],
    )


JUMP_KEYS = [
    (0, STAND),
    (7, P(pelvis=(0, 0, -0.105), spine=(14, 0, 0), head=(-8, 0, 0), uarm_LR=arm(38, 10), larm_LR=arm(-22),
          fing_LR=curl(40))),
    (11, P(pelvis=(0, 0, 0.0), spine=(-2, 0, 0), head=(-10, 0, 0), uarm_LR=arm(-95, 34), larm_LR=arm(-20),
           foot_LR=(0, 0.02, 0.0, 30), fing_LR=curl(10), eyes=(1.08, 0, 0.002)), "out"),
    (14, P(pelvis=(0, 0, 0.0), spine=(-4, 0, 0), head=(-10, 0, 0), uarm_LR=arm(-100, 40), larm_LR=arm(-15),
           foot_LR=(0, 0.03, 0.05, 25), fing_LR=curl(5), eyes=(1.08, 0, 0.002))),
    (21, P(pelvis=(0, 0, 0.0), spine=(4, 0, 0), head=(-6, 0, 0), uarm_LR=arm(-95, 40), larm_LR=arm(-35),
           foot_LR=(0.01, -0.03, 0.15, -8), fing_LR=curl(20))),
    (28, P(pelvis=(0, 0, 0.0), spine=(2, 0, 0), head=(4, 0, 0), uarm_LR=arm(-45, 50), larm_LR=arm(-25),
           foot_LR=(0.01, -0.02, 0.05, -10), fing_LR=curl(20))),
    (31, P(pelvis=(0, 0, -0.02), spine=(4, 0, 0), head=(4, 0, 0), uarm_LR=arm(-38, 45), larm_LR=arm(-25),
           foot_LR=(0.01, 0.0, 0.0, 0), fing_LR=curl(20)), "in"),
    (34, P(pelvis=(0, 0, -0.115), spine=(16, 0, 0), head=(8, 0, 0), uarm_LR=arm(-28, 18), larm_LR=arm(-30),
           foot_LR=(0.01, 0.0, 0.0, 0), fing_LR=curl(35), eyes=(0.75, 0, 0)), "out"),
    (40, STAND),
]


@anim("Jump", 40, False, "Absprung (0-11), Flug (11-30), Landung (31-40)")
def jump(f):
    return keyed(f, JUMP_KEYS)


@anim("Fall", 24, True, "kontrolliertes Fallen, Arme ausbalancierend")
def fall(f):
    t = f / 24.0
    s1 = math.sin(2 * math.pi * t)
    s2 = math.sin(2 * math.pi * t + 1.3)
    return P(
        pelvis=(0, 0, -0.005),
        pelvis_rot=(-4, 0, 2 * s1),
        spine=(-5 + 1.5 * s2, 0, -2 * s1),
        head=(10 + 2 * s2, 2 * s1, 0),
        uarm_L=arm(-35 + 8 * s1, 62 + 6 * s2),
        uarm_R=arm(-35 - 8 * s1, 62 - 6 * s2),
        larm_L=arm(-30 + 6 * s2),
        larm_R=arm(-30 - 6 * s2),
        fing_LR=curl(8),
        thumb_LR=curl(0),
        foot_L=(0.015, -0.03 + 0.03 * s1, 0.07 + 0.015 * s2, -8),
        foot_R=(0.015, -0.03 - 0.03 * s1, 0.07 - 0.015 * s2, -8),
        eyes=(1.08, 0, 0.002),
    )


LAND_KEYS = [
    (0, P(pelvis=(0, 0, -0.01), spine=(-3, 0, 0), head=(10, 0, 0), uarm_LR=arm(-35, 55), larm_LR=arm(-30),
          fing_LR=curl(8))),
    (3, P(pelvis=(0, 0, -0.125), spine=(18, 0, 0), head=(10, 0, 0), uarm_LR=arm(-30, 25), larm_LR=arm(-38),
          fing_LR=curl(35), eyes=(0.7, 0, 0)), "out"),
    (8, P(pelvis=(0, 0, -0.035), spine=(4, 0, 0), head=(-3, 0, 0), uarm_LR=arm(-6, 8), larm_LR=arm(-14),
          fing_LR=curl(20))),
    (12, P(pelvis=(0, 0, -0.022), spine=(1, 0, 0), head=(1, 0, 0), uarm_LR=arm(2, 3), larm_LR=arm(-6))),
    (18, STAND),
]


@anim("Land", 18, False, "Abfedern nach dem Sprung")
def land(f):
    return keyed(f, LAND_KEYS)


@anim("Crouch", 40, True, "mechanisches Ducken (gehaltene Pose, leichtes Hydraulik-Wippen)")
def crouch(f):
    t = f / 40.0
    w = math.sin(2 * math.pi * t)
    return P(
        pelvis=(0, 0.060, -0.165 - 0.005 * w),
        pelvis_rot=(6, 0, 0),
        spine=(36 + 1.0 * w, 0, 0),
        head=(-36 - 1.0 * w, 0, 0),
        shoulder_LR=(0, 2, 0),
        uarm_LR=arm(-28, 14),
        larm_LR=arm(-48),
        hand_LR=arm(-10),
        fing_LR=curl(35),
        foot_LR=(0.075, 0.0, 0.0, 0.0),
        eyes=(1.0, 0, 0.003),
    )


INTERACT_KEYS = [
    (0, STAND),
    (9, P(head=(10, 0, -8), spine=(3, 0, -4), uarm_R=arm(-55, 4), larm_R=arm(-40), hand_R=arm(-10),
          fing_R=curl(8), thumb_R=curl(30), eyes=(1.0, -0.006, -0.004))),
    (16, P(pelvis=(0, 0, -0.016), head=(12, 0, -6), spine=(6, 0, -5), uarm_R=arm(-74, -4), larm_R=arm(-18),
           hand_R=arm(-18), fing_R=curl(4), thumb_R=curl(35), uarm_L=arm(6, 6), eyes=(1.0, -0.006, -0.004))),
    (19, P(pelvis=(0, 0, -0.018), head=(12, 0, -6), spine=(7, 0, -5), uarm_R=arm(-80, -4), larm_R=arm(-10),
           hand_R=arm(-22), fing_R=curl(4), thumb_R=curl(35), uarm_L=arm(6, 6), eyes=(1.0, -0.006, -0.004)), "out"),
    (25, P(pelvis=(0, 0, -0.018), head=(12, 0, -6), spine=(7, 0, -5), uarm_R=arm(-80, -4), larm_R=arm(-10),
           hand_R=arm(-22), fing_R=curl(4), thumb_R=curl(35), uarm_L=arm(6, 6), eyes=(0.55, -0.006, -0.004))),
    (30, P(pelvis=(0, 0, -0.014), head=(4, 6, -4), spine=(4, 0, -3), uarm_R=arm(-55, 4), larm_R=arm(-40),
           hand_R=arm(-10), fing_R=curl(10), thumb_R=curl(25), eyes=(1.0, 0, 0))),
    (37, P(head=(14, 4, 0), uarm_R=arm(-10, 3), larm_R=arm(-14))),
    (48, STAND),
]


@anim("Interact", 48, False, "Schalter/Konsole bedienen (Druck bei Frame 19-25)")
def interact(f):
    return keyed(f, INTERACT_KEYS)


PUSH_FRAMES = 32


@anim("Push", PUSH_FRAMES, True, "schweres Objekt schieben")
def push(f):
    t = f / PUSH_FRAMES
    c = math.cos(2 * math.pi * t)
    feet = {}
    for side, off in (("L", 0.0), ("R", 0.5)):
        feet[side] = gait_foot(t + off, 0.20, 0.70, 0.045, -6, 18, center=0.07, outward=0.012)
    return P(
        pelvis=(0.010 * math.sin(2 * math.pi * t), 0.02, -0.062 - 0.008 * math.cos(4 * math.pi * t)),
        pelvis_rot=(10, 0, -4 * c),
        spine=(16 + 1.0 * math.cos(4 * math.pi * t), 0, 3 * c),
        head=(-20, 0, -1.5 * c),
        uarm_L=arm(-78 + 3 * c, -6),
        uarm_R=arm(-78 - 3 * c, -6),
        larm_L=arm(-28 - 4 * c),
        larm_R=arm(-28 + 4 * c),
        hand_LR=arm(-12),
        fing_LR=curl(78),
        thumb_LR=curl(50),
        foot_L=feet["L"],
        foot_R=feet["R"],
        eyes=(0.85, 0, 0.003),
    )


DEATH_KEYS = [
    (0, STAND),
    (3, P(head=(-6, 12, 14), spine=(-5, 0, 6), uarm_L=arm(-10, 18), uarm_R=arm(5, 12), larm_LR=arm(-25),
          fing_LR=curl(5), eyes=(0.25, 0, 0)), "out"),
    (5, P(head=(-4, 10, 12), spine=(-4, 0, 5), uarm_L=arm(-8, 16), uarm_R=arm(4, 10), larm_LR=arm(-22),
          fing_LR=curl(5), eyes=(1.0, 0, 0))),
    (8, P(head=(2, -12, -10), spine=(-2, 0, -6), uarm_L=arm(4, 10), uarm_R=arm(-10, 18), larm_LR=arm(-20),
          fing_LR=curl(10), eyes=(0.15, 0, 0)), "out"),
    (10, P(head=(2, -10, -9), spine=(-2, 0, -5), uarm_LR=arm(0, 12), larm_LR=arm(-18), eyes=(0.9, 0, 0))),
    (18, P(pelvis=(0, 0.01, -0.05), head=(26, 4, 0), spine=(10, 0, 0), shoulder_LR=(0, 4, 0), uarm_LR=arm(4, 6),
           larm_LR=arm(-6), fing_LR=curl(28), thumb_LR=curl(15), eyes=(0.5, 0, -0.004))),
    (29, P(pelvis=(0, 0.10, -0.125), pelvis_rot=(0, 0, 0), head=(34, 4, 0), spine=(16, 0, 0),
           shoulder_LR=(0, 6, 0), uarm_LR=arm(-12, 12), larm_LR=arm(-14), fing_LR=curl(30),
           foot_LR=(0.10, -0.08, 0.0, 0.0), eyes=(0.3, 0, -0.006)), "in"),
    (33, P(pelvis=(0, 0.11, -0.235), pelvis_rot=(-6, 0, 0), head=(32, 3, 0), spine=(26, 0, 0),
           shoulder_LR=(0, 6, 0), uarm_LR=arm(-10, 14), larm_LR=arm(-14), fing_LR=curl(30),
           foot_LR=(0.12, -0.33, 0.05, -45), eyes=(0.25, 0, -0.006)), "smooth"),
    (37, P(pelvis=(0, 0.06, -0.335), pelvis_rot=(-32, 0, 0), head=(30, 2, 0), spine=(30, 0, 0),
           shoulder_LR=(0, 6, 0), uarm_LR=arm(-8, 16), larm_LR=arm(-14), fing_LR=curl(30),
           foot_LR=(0.13, -0.40, 0.0, -68), eyes=(0.22, 0, -0.006)), "in"),
    (42, P(pelvis=(0, 0.06, -0.318), pelvis_rot=(-30, 0, 0), head=(38, 2, 0), spine=(36, 0, 0),
           shoulder_LR=(0, 6, 0), uarm_LR=arm(-6, 18), larm_LR=arm(-16), fing_LR=curl(32),
           foot_LR=(0.13, -0.40, 0.0, -68), eyes=(0.3, 0, -0.006)), "out"),
    (51, P(pelvis=(0, 0.06, -0.332), pelvis_rot=(-32, 0, 0), head=(46, 4, 4), spine=(40, 0, 0),
           shoulder_LR=(0, 8, 0), uarm_LR=arm(-2, 16), larm_LR=arm(-12), fing_LR=curl(35),
           foot_LR=(0.13, -0.40, 0.0, -68), eyes=(0.1, 0, -0.008))),
    (64, P(pelvis=(0, 0.06, -0.334), pelvis_rot=(-32, 0, 0), head=(52, 5, 6), spine=(42, 0, 0),
           shoulder_LR=(0, 8, 0), uarm_LR=arm(0, 16), larm_LR=arm(-10), fing_LR=curl(38),
           foot_LR=(0.13, -0.40, 0.0, -68), eyes=(0.02, 0, -0.008))),
]


@anim("Death", 64, False, "Energieverlust, Zusammensacken, Augen erlöschen")
def death(f):
    return keyed(f, DEATH_KEYS)


# ================================ Phase 1: systems the game already has ================================


def wave(t, k, phase=0.0):
    """Loop-safe oscillation: integer k cycles per clip."""
    return math.sin(2 * math.pi * (k * t + phase))


def pulse(f, center, width):
    """Smooth 0..1..0 bump around a frame."""
    d = abs(f - center) / width
    return max(0.0, 1.0 - d) ** 2 * (3 - 2 * max(0.0, 1.0 - d)) if d < 1 else 0.0


@anim("DoubleJump", 24, False, "zweiter Sprung in der Luft: Beine anziehen, Arme schlagen, endet in Fall-Pose", "Phase1")
def double_jump(f):
    return keyed(f, [
        (0, P(spine=(4, 0, 0), head=(-6, 0, 0), uarm_LR=arm(-60, 45), larm_LR=arm(-30),
              foot_LR=(0.01, -0.02, 0.10, -8), fing_LR=curl(20))),
        (4, P(pelvis_rot=(8, 0, 0), spine=(14, 0, 0), head=(-10, 0, 0), uarm_LR=arm(35, 22), larm_LR=arm(-18),
              foot_LR=(0.01, -0.04, 0.15, -6), fing_LR=curl(60), thumb_LR=curl(40), eyes=(1.15, 0, 0.003)), "out"),
        (9, P(spine=(-6, 0, 0), head=(-10, 0, 0), uarm_LR=arm(-95, 45), larm_LR=arm(-15),
              foot_LR=(0.02, 0.04, 0.06, 18), fing_LR=curl(5), eyes=(1.12, 0, 0.003)), "out"),
        (16, P(spine=(2, 0, 0), head=(4, 0, 0), uarm_LR=arm(-50, 55), larm_LR=arm(-28),
               foot_LR=(0.015, -0.02, 0.08, -8), fing_LR=curl(10), eyes=(1.08, 0, 0.002))),
        (24, fall(0)),
    ])


HIT_KEYS = [
    (0, STAND),
    (2, P(pelvis=(0, 0.012, -0.022), spine=(-9, 4, 6), head=(-14, -6, 8), uarm_L=arm(8, 16), uarm_R=arm(6, 12),
          larm_LR=arm(-20), fing_LR=curl(0), eyes=(0.3, 0, 0)), "out"),
    (5, P(pelvis=(0, 0.004, -0.016), spine=(4, -1, -2), head=(5, 2, -2), uarm_LR=arm(-4, 4), larm_LR=arm(-10),
          eyes=(1.0, 0, 0))),
    (12, STAND),
]


@anim("Hit", 12, False, "kurzer Treffer (Hitze/Strom), startet und endet in der Grundpose", "Phase1",
      "Additive (Referenz Frame 0)")
def hit(f):
    return keyed(f, HIT_KEYS)


@anim("HitHeavy", 26, False, "schwerer Treffer: Rückwärtsschritt, Taumeln, Fangen", "Phase1")
def hit_heavy(f):
    return keyed(f, [
        (0, STAND),
        (3, P(pelvis=(0, 0.03, -0.04), spine=(-16, -6, 10), head=(-20, -8, 10), uarm_LR=arm(20, 30),
              larm_LR=arm(-30), fing_LR=curl(0), eyes=(0.2, 0, 0), foot_R=(0.02, 0.09, 0.05, 0)), "out"),
        (8, P(pelvis=(0, 0.05, -0.06), spine=(10, 0, 4), head=(12, 0, 0), uarm_LR=arm(-15, 15), larm_LR=arm(-24),
              eyes=(0.6, 0, -0.003), foot_R=(0.03, 0.13, 0.0, 0))),
        (14, P(pelvis=(0, 0.03, -0.035), spine=(3, 0, 2), head=(4, 0, 0), uarm_LR=arm(-5, 8), larm_LR=arm(-14),
               foot_R=(0.015, 0.07, 0.04, 0))),
        (20, P(pelvis=(0, 0.008, -0.022), spine=(1, 0, 0), foot_R=(0.0, 0.0, 0.0, 0))),
        (26, STAND),
    ])


@anim("Electrocuted", 18, True, "Stromschlag: Zittern, steife Arme, flackernde Augen", "Phase1")
def electrocuted(f):
    t = f / 18.0
    flick = 0.65 + 0.5 * wave(t, 13, 0.1) * wave(t, 5)
    return P(
        pelvis=(0.004 * wave(t, 7, 0.1), 0.004 * wave(t, 9, 0.3), -0.035 + 0.006 * wave(t, 11, 0.2)),
        spine=(-6 + 4 * wave(t, 7), 3 * wave(t, 9, 0.5), 4 * wave(t, 5, 0.2)),
        head=(-8 + 6 * wave(t, 11, 0.1), 8 * wave(t, 7, 0.4), 6 * wave(t, 9)),
        uarm_L=arm(-25 + 10 * wave(t, 9), 40 + 8 * wave(t, 7)),
        uarm_R=arm(-25 + 10 * wave(t, 7, 0.3), 40 + 8 * wave(t, 11, 0.6)),
        larm_L=arm(-15 + 12 * wave(t, 11)),
        larm_R=arm(-15 + 12 * wave(t, 9, 0.2)),
        hand_LR=arm(10 * wave(t, 7, 0.4)),
        fing_LR=curl(-6 + 10 * wave(t, 13)),
        thumb_LR=curl(-10),
        foot_LR=(0.03, 0.0, 0.0, 0.0),
        eyes=(max(0.12, min(1.25, flick)), 0, 0),
    )


@anim("GasCough", 48, True, "Gas: gebeugt, zwei Hustenstöße, wedelt vor dem Visier", "Phase1")
def gas_cough(f):
    t = f / 48.0
    c = pulse(f, 7, 4) + pulse(f, 31, 4)
    return P(
        pelvis=(0.004 * wave(t, 1), 0.02, -0.035 - 0.012 * c),
        pelvis_rot=(4, 0, 0),
        spine=(16 + 10 * c, 0, 3 * wave(t, 1)),
        head=(10 + 12 * c, 3 * wave(t, 1, 0.2), -4),
        uarm_L=arm(-78 + 4 * wave(t, 4), -16),
        larm_L=arm(-62, -10),
        hand_L=arm(0, 0, 22 * wave(t, 4)),
        fing_L=curl(2),
        uarm_R=arm(-28, 6),
        larm_R=arm(-62),
        fing_R=curl(40),
        foot_LR=(0.02, 0.0, 0.0, 0.0),
        eyes=(0.5 - 0.35 * c, 0, -0.003),
    )


@anim("SlopeSlide", 32, True, "auf steilem Hang rutschen, seitlich wie beim Surfen", "Phase1")
def slope_slide(f):
    t = f / 32.0
    return P(
        pelvis=(0.004 * wave(t, 1), 0.02, -0.07 + 0.006 * wave(t, 2)),
        pelvis_rot=(0, 0, 25),
        spine=(-6 + 2 * wave(t, 1), 2 * wave(t, 1, 0.3), -12 + 3 * wave(t, 1)),
        head=(8, 0, -14),
        uarm_L=arm(-20 + 10 * wave(t, 1), 60 + 8 * wave(t, 1, 0.25)),
        uarm_R=arm(10 - 10 * wave(t, 1), 55 - 8 * wave(t, 1, 0.6)),
        larm_LR=arm(-25),
        fing_LR=curl(5),
        foot_L=(0.02, -0.10, 0.0, 0.0),
        foot_R=(0.04, 0.10, 0.0, 0.0),
        footyaw_LR=(25.0,),
        eyes=(1.12, 0, 0.002),
    )


@anim("PushStart", 16, False, "an die Kiste lehnen: Grundpose -> Push (Frame 0)", "Phase1")
def push_start(f):
    end = push(0)
    mid = lerp_pose(STAND, end, 0.5)
    mid["foot_R"] = (end["foot_R"][0], end["foot_R"][1] * 0.5, 0.04, 0.0)
    mid["foot_L"] = (end["foot_L"][0], end["foot_L"][1] * 0.5, 0.0, 0.0)
    return keyed(f, [(0, STAND), (8, mid), (16, end)])


@anim("PushStep", 24, False, "Kiste rutscht einen Schritt: Ausholen, Stoß, zurück in Push (Frame 0)", "Phase1")
def push_step(f):
    base = push(0)

    def mod(**kw):
        p = dict(base)
        p.update({k: tuple(v) for k, v in kw.items()})
        return p
    return keyed(f, [
        (0, base),
        (6, mod(pelvis=(0, 0.035, -0.075), spine=(22, 0, 0), larm_L=arm(-42), larm_R=arm(-42), eyes=(0.7, 0, 0.003))),
        (11, mod(pelvis=(0, 0.0, -0.058), spine=(12, 0, 0), uarm_L=arm(-86, -6), uarm_R=arm(-86, -6),
                 larm_L=arm(-6), larm_R=arm(-6), eyes=(0.55, 0, 0.003)), "out"),
        (16, mod(pelvis=(0, 0.012, -0.062))),
        (24, base),
    ])


MUD_FRAMES = 32


@anim("MudWalk", MUD_FRAMES, True, "schwerer Gang durch Schlamm, Füße ziehen sich heraus", "Phase1")
def mud_walk(f):
    t = f / MUD_FRAMES
    c = math.cos(2 * math.pi * t)
    feet = {}
    for side, off in (("L", 0.0), ("R", 0.5)):
        feet[side] = gait_foot(t + off, 0.18, 0.66, 0.10, -4, 30, outward=0.02)
    return P(
        pelvis=(0.016 * math.sin(2 * math.pi * t), 0, -0.050 - 0.010 * math.cos(4 * math.pi * t)),
        pelvis_rot=(4, 0, -5 * c),
        spine=(10, 3 * math.sin(2 * math.pi * t), 6 * c),
        head=(-8, -2 * math.sin(2 * math.pi * t), -3 * c),
        uarm_L=arm(16 * c, 22),
        uarm_R=arm(-16 * c, 22),
        larm_LR=arm(-30),
        fing_LR=curl(30),
        foot_L=feet["L"],
        foot_R=feet["R"],
        eyes=(0.8, 0, 0.002),
    )


RESPAWN_DOWN = P(pelvis=(0, 0.02, -0.11), pelvis_rot=(6, 0, 0), spine=(20, 0, 0), head=(38, 0, 0),
                 shoulder_LR=(0, 6, 0), uarm_LR=arm(-5, 6), larm_LR=arm(-8), fing_LR=curl(30),
                 foot_LR=(0.04, 0.0, 0.0, 0.0), eyes=(0.02, 0, -0.008))


def respawn_eyes(p, value):
    p = dict(p)
    p["eyes"] = (value, p["eyes"][1], p["eyes"][2])
    return p


@anim("Respawn", 48, False, "Hochfahren am Checkpoint: Augen flackern an, Systemcheck, Grundpose", "Phase1")
def respawn(f):
    return keyed(f, [
        (0, RESPAWN_DOWN),
        (8, respawn_eyes(RESPAWN_DOWN, 0.35), "linear"),
        (10, respawn_eyes(RESPAWN_DOWN, 0.05), "linear"),
        (12, respawn_eyes(RESPAWN_DOWN, 0.8), "linear"),
        (14, respawn_eyes(RESPAWN_DOWN, 0.2), "linear"),
        (17, respawn_eyes(RESPAWN_DOWN, 1.0), "linear"),
        (24, P(pelvis=(0, 0, -0.03), spine=(2, 0, 0), head=(-8, 0, 0), uarm_LR=arm(-10, 10), larm_LR=arm(-25),
               fing_LR=curl(0), eyes=(1.1, 0, 0.002))),
        (30, P(pelvis=(0, 0, -0.02), head=(0, 6, 25), uarm_LR=arm(-8, 8), larm_LR=arm(-22), fing_LR=curl(55),
               eyes=(1.0, 0.01, 0))),
        (36, P(pelvis=(0, 0, -0.02), head=(0, -6, -25), uarm_LR=arm(-4, 6), larm_LR=arm(-14), fing_LR=curl(15),
               eyes=(1.0, -0.01, 0))),
        (42, P(pelvis=(0, 0, -0.035), head=(3, 0, 0), eyes=(1.0, 0, 0))),
        (48, STAND),
    ])


# ================================ Phase 2: tools and interaction ================================

TOOL_CLIPS = {"ToolAim", "ToolUse", "Scan"}


def aim_pose(t=0.0):
    br = wave(t, 2)
    return P(
        pelvis=(0, 0, -0.016 - 0.003 * br),
        spine=(2 + 0.8 * br, 0, 8),
        head=(-1, 0, -8),
        uarm_R=arm(-84 + 1.0 * br, -6),
        larm_R=arm(-8),
        hand_R=arm(-4),
        fing_R=curl(75),
        thumb_R=curl(45),
        uarm_L=arm(4 + 1.0 * br, 6),
        larm_L=arm(-10),
        eyes=(0.85, -0.004, 0),
    )


@anim("ToolAim", 40, True, "Werkzeug (Lampe/Scanner/Portal-Emitter) nach vorn halten, rechte Hand an Prop_R",
      "Phase2", "Oberkörper-Maske")
def tool_aim(f):
    return aim_pose(f / 40.0)


@anim("ToolUse", 20, False, "Werkzeug auslösen: Rückstoß und zurück in ToolAim (Frame 0)", "Phase2",
      "Oberkörper-Maske")
def tool_use(f):
    base = aim_pose(0)

    def mod(**kw):
        p = dict(base)
        p.update({k: tuple(v) for k, v in kw.items()})
        return p
    return keyed(f, [
        (0, base),
        (3, mod(uarm_R=arm(-98, -6), larm_R=arm(-22), spine=(-3, 0, 10), head=(-5, 0, -8), eyes=(1.25, -0.004, 0)),
         "out"),
        (8, mod(uarm_R=arm(-81, -6), larm_R=arm(-6), spine=(3, 0, 8), eyes=(0.9, -0.004, 0))),
        (20, base),
    ])


@anim("Scan", 64, True, "Umgebung scannen: Körper schwenkt, Augen pulsieren", "Phase2", "Oberkörper-Maske")
def scan(f):
    t = f / 64.0
    sw = wave(t, 1)
    return P(
        pelvis=(0, 0, -0.018),
        spine=(4, 0, 6 + 20 * sw),
        head=(6, 0, -6 + 14 * wave(t, 1, 0.04)),
        uarm_R=arm(-62, -4),
        larm_R=arm(-26),
        hand_R=arm(-6),
        fing_R=curl(75),
        thumb_R=curl(45),
        uarm_L=arm(3, 6),
        larm_L=arm(-12),
        eyes=(1.0 + 0.14 * wave(t, 4), 0.008 * sw, 0),
    )


@anim("PickUp", 44, False, "Gegenstand aufheben, kurz betrachten, einstecken", "Phase2")
def pick_up(f):
    return keyed(f, [
        (0, STAND),
        (10, P(pelvis=(0, 0.07, -0.155), pelvis_rot=(6, 0, 0), spine=(50, 0, 6), head=(-2, 0, -4),
               uarm_R=arm(-66, 4), larm_R=arm(-4), hand_R=arm(-6), fing_R=curl(4), thumb_R=curl(0),
               uarm_L=arm(-24, 14), larm_L=arm(-24), foot_LR=(0.08, 0.0, 0.0, 0.0))),
        (14, P(pelvis=(0, 0.07, -0.155), pelvis_rot=(6, 0, 0), spine=(50, 0, 6), head=(-2, 0, -4),
               uarm_R=arm(-66, 4), larm_R=arm(-4), hand_R=arm(-6), fing_R=curl(78), thumb_R=curl(45),
               uarm_L=arm(-24, 14), larm_L=arm(-24), foot_LR=(0.08, 0.0, 0.0, 0.0)), "linear"),
        (23, P(pelvis=(0, 0, -0.02), spine=(4, 0, 0), head=(14, 0, -10), uarm_R=arm(-58, -10), larm_R=arm(-72),
               hand_R=arm(-10), fing_R=curl(78), thumb_R=curl(45), eyes=(1.12, -0.006, -0.002))),
        (32, P(pelvis=(0, 0, -0.02), spine=(4, 0, 0), head=(14, 10, -10), uarm_R=arm(-58, -10), larm_R=arm(-72),
               hand_R=arm(-10), fing_R=curl(78), thumb_R=curl(45), eyes=(1.0, -0.006, -0.002))),
        (38, P(pelvis=(0, 0, -0.016), uarm_R=arm(4, 10), larm_R=arm(-30), fing_R=curl(60), eyes=(0.4, 0, 0))),
        (44, STAND),
    ])


@anim("TerminalTyping", 32, True, "am Terminal tippen, Kopf liest mit", "Phase2")
def terminal_typing(f):
    t = f / 32.0
    return P(
        pelvis=(0, 0.01, -0.02),
        spine=(7, 0, 0),
        head=(16, 0, 7 * wave(t, 1)),
        uarm_LR=arm(-46, -3),
        larm_L=arm(-50 + 6 * wave(t, 4)),
        larm_R=arm(-50 + 6 * wave(t, 4, 0.5)),
        hand_LR=arm(16),
        fing_L=curl(10 + 22 * max(0.0, wave(t, 8))),
        fing_R=curl(10 + 22 * max(0.0, wave(t, 8, 0.5))),
        thumb_LR=curl(20),
        eyes=(0.95, 0.009 * wave(t, 1), -0.003),
    )


@anim("PortalEnter", 20, False, "durch ein Portal: Zusammenzucken, Strecken, Augen-Blitz", "Phase2")
def portal_enter(f):
    return keyed(f, [
        (0, STAND),
        (4, P(pelvis=(0, 0, -0.06), spine=(10, 0, 0), head=(6, 0, 0), uarm_LR=arm(-22, 6), larm_LR=arm(-60),
              fing_LR=curl(60), eyes=(1.3, 0, 0.004)), "out"),
        (8, P(pelvis=(0, 0, 0.0), spine=(-6, 0, 0), head=(-8, 0, 0), uarm_LR=arm(25, 30), larm_LR=arm(-10),
              fing_LR=curl(0), foot_LR=(0.0, 0.0, 0.0, 20), eyes=(0.2, 0, 0))),
        (13, P(pelvis=(0, 0, -0.03), head=(0, 10, 0), uarm_LR=arm(0, 8), larm_LR=arm(-12), eyes=(1.1, 0, 0))),
        (16, P(pelvis=(0, 0, -0.02), head=(0, -6, 0), eyes=(0.9, 0, 0))),
        (20, STAND),
    ])


# ================================ Phase 3: character and life ================================


@anim("IdleScratch", 90, False, "Idle-Variante: kratzt sich am Ohr", "Phase3")
def idle_scratch(f):
    up = P(head=(2, 8, -6), uarm_R=arm(-148, -12), larm_R=arm(-64), hand_R=arm(-10), fing_R=curl(35),
           thumb_R=curl(20), uarm_L=arm(2, 4), eyes=(0.8, -0.006, 0))
    keys = [(0, STAND), (14, up)]
    for i, fr in enumerate(range(18, 50, 5)):
        p = dict(up)
        p["fing_R"] = (15.0 if i % 2 else 50.0,)
        p["hand_R"] = arm(-20 + (6 if i % 2 else -6))
        keys.append((fr, p))
    keys += [(60, P(head=(2, -3, -4), uarm_R=arm(-20, 8), larm_R=arm(-20), eyes=(0.3, 0, 0))),
             (66, P(eyes=(1.0, 0, 0))), (90, STAND)]
    return keyed(f, keys)


@anim("IdleFootTap", 72, False, "Idle-Variante: Gewicht verlagern, ungeduldig mit dem Fuß tippen", "Phase3")
def idle_foot_tap(f):
    lean = P(pelvis=(0.015, 0, -0.015), pelvis_rot=(0, 2, 0), head=(0, 6, 4), uarm_L=arm(2, 6),
             foot_R=(0.02, -0.04, 0.0, 0.0), eyes=(1.0, 0.004, 0))
    keys = [(0, STAND), (10, lean)]
    for i, fr in enumerate(range(14, 58, 6)):
        p = dict(lean)
        p["foot_R"] = (0.02, -0.04, 0.0, -18.0 if i % 2 == 0 else 0.0)
        p["head"] = (2 if i % 2 == 0 else 0, 6, 4)
        keys.append((fr, p, "linear"))
    keys += [(64, lean), (72, STAND)]
    return keyed(f, keys)


@anim("IdleYawn", 90, False, "Idle-Variante: Strecken und „Gähnen“ (Augen zu Schlitzen)", "Phase3")
def idle_yawn(f):
    stretch = P(pelvis=(0, 0, -0.005), spine=(-10, 0, 0), head=(-20, 0, 0), uarm_LR=arm(-40, 115),
                larm_LR=arm(-20), fing_LR=curl(0), thumb_LR=curl(0), foot_LR=(0.0, 0.0, 0.0, 10), eyes=(0.12, 0, 0))
    hold = dict(stretch)
    hold["uarm_L"] = arm(-38, 118)
    hold["uarm_R"] = arm(-42, 112)
    return keyed(f, [
        (0, STAND),
        (18, stretch),
        (36, hold),
        (50, P(pelvis=(0, 0, -0.02), spine=(4, 0, 0), head=(6, 0, 0), eyes=(0.3, 0, 0))),
        (58, P(head=(0, 8, 6), eyes=(0.15, 0, 0))),
        (64, P(head=(0, -8, -6), eyes=(1.0, 0, 0))),
        (72, P(head=(0, 4, 3))),
        (90, STAND),
    ])


def stepping_feet(t, total_yaw, steps, sign, end=1.0, widen=0.02):
    """Feet that turn with the body in alternating steps (lead foot first). Returns pose kwargs."""
    lead, trail = ("L", "R") if sign > 0 else ("R", "L")
    per = total_yaw / steps
    slot = end / (2 * steps)
    kw = {}
    for side, offset in ((lead, 0), (trail, 1)):
        fyaw, lift = 0.0, 0.0
        for i in range(steps):
            a = (2 * i + offset) * slot + 0.02 * end
            b = a + 1.25 * slot
            w = ease((t - a) / (b - a))
            fyaw += sign * per * w
            if 0 < w < 1:
                lift = 0.045 * math.sin(math.pi * w)
        x0 = LEG_X if side == "L" else -LEG_X
        r = x0 + (widen if x0 > 0 else -widen)       # radial stance width, rotates with the foot
        ang = math.radians(fyaw)
        s = 1 if side == "L" else -1
        kw["foot_" + side] = ((r * math.cos(ang) - x0) * s, r * math.sin(ang), lift, 0.0)
        kw["footyaw_" + side] = (fyaw,)
    return kw


def turn_pose(f, frames, sign):
    """Turn in place by 90 deg (root motion) in four 45-deg foot steps, lead foot first."""
    t = f / frames
    yaw = sign * 90.0 * ease(t)
    bob = math.sin(math.pi * t)
    return P(root=(0.0, 0.0, yaw), pelvis=(0, 0, -0.012 - 0.018 * bob), pelvis_rot=(0, 0, 0),
             spine=(3 * bob, 0, sign * 6 * math.sin(math.pi * min(1, t * 1.6))),
             head=(0, 0, sign * 10 * math.sin(math.pi * min(1, t * 1.4))),
             uarm_L=arm(-8 * bob * sign, 8 * bob), uarm_R=arm(8 * bob * sign, 8 * bob), larm_LR=arm(-10 * bob),
             eyes=(1.0, sign * 0.008 * bob, 0), **stepping_feet(t, 90.0, 2, sign))


@anim("TurnLeft", 24, False, "90° nach links auf der Stelle (Root Motion)", "Phase3", "Root Motion (Rotation)")
def turn_left(f):
    return turn_pose(f, 24, 1)


@anim("TurnRight", 24, False, "90° nach rechts auf der Stelle (Root Motion)", "Phase3", "Root Motion (Rotation)")
def turn_right(f):
    return turn_pose(f, 24, -1)


@anim("WalkStart", 12, False, "Anlaufen: Grundpose -> Walk (Frame 0)", "Phase3")
def walk_start(f):
    end = walk(0)
    mid = lerp_pose(STAND, end, 0.5)
    mid["foot_L"] = (end["foot_L"][0], end["foot_L"][1] * 0.5, 0.05, -4.0)
    mid["pelvis"] = (0.0, 0.0, -0.05)
    mid["spine"] = (7.0, 0.0, 0.0)
    return keyed(f, [(0, STAND), (6, mid), (12, end)])


@anim("WalkStop", 16, False, "Abbremsen: Walk (Frame 10) -> Grundpose mit Nachwippen", "Phase3")
def walk_stop(f):
    start = walk(WALK_FRAMES // 2)
    mid = lerp_pose(start, STAND, 0.5)
    mid["foot_L"] = (start["foot_L"][0] * 0.5, start["foot_L"][1] * 0.4, 0.05, 0.0)
    settle = P(pelvis=(0, 0, -0.038), spine=(5, 0, 0), head=(-3, 0, 0), uarm_LR=arm(-6, 4), larm_LR=arm(-14))
    return keyed(f, [(0, start), (6, mid), (10, settle), (16, STAND)])


@anim("Talk", 72, True, "im Gespräch (z. B. mit M-4): Gesten, Nicken", "Phase3")
def talk(f):
    t = f / 72.0
    return P(
        pelvis=(0.004 * wave(t, 1), 0, -0.015),
        spine=(2 * wave(t, 2), 0, 4 * wave(t, 1)),
        head=(5 * wave(t, 3), 4 * wave(t, 1), 6 * wave(t, 1, 0.2)),
        uarm_R=arm(-30 + 10 * wave(t, 2), 10),
        larm_R=arm(-60 + 12 * wave(t, 3, 0.1)),
        hand_R=arm(-10, 0, 15 * wave(t, 2)),
        fing_R=curl(5),
        uarm_L=arm(-10 + 6 * wave(t, 2, 0.5), 8),
        larm_L=arm(-30 + 8 * wave(t, 3, 0.4)),
        fing_L=curl(12),
        eyes=(min(1.0, blink(f, 40)), 0.004 * wave(t, 1), 0),
    )


@anim("Listen", 72, True, "zuhören: Kopf geneigt, langsames Nicken", "Phase3")
def listen(f):
    t = f / 72.0
    nod = pulse(f, 20, 6) + pulse(f, 50, 6)
    return P(
        pelvis=(0.008 * wave(t, 1), 0, -0.016),
        pelvis_rot=(0, 1.5 * wave(t, 1), 0),
        spine=(1, 0, 2 * wave(t, 1)),
        head=(3 + 8 * nod, 10, 6),
        uarm_LR=arm(1, 4),
        larm_LR=arm(-8),
        fing_LR=curl(22),
        eyes=(min(blink(f, 30), blink(f, 62)), -0.004, 0.002),
    )


@anim("Celebrate", 48, False, "Freude: Hüpfer, Fäuste hoch, „^^“-Augen", "Phase3")
def celebrate(f):
    happy = (0.35, 0, 0.006)
    up = P(pelvis=(0, 0, 0.10), spine=(-6, 0, 0), head=(-12, 0, 0), uarm_LR=arm(-30, 125), larm_LR=arm(-30),
           fing_LR=curl(80), thumb_LR=curl(45), foot_LR=(0.0, 0.0, 0.10, 10), eyes=happy)
    apex = dict(up)
    apex["pelvis"] = (0, 0, 0.12)
    apex["foot_L"] = apex["foot_R"] = (0.0, 0.0, 0.12, 6)
    return keyed(f, [
        (0, STAND),
        (6, P(pelvis=(0, 0, -0.08), spine=(10, 0, 0), uarm_LR=arm(25, 10), larm_LR=arm(-20), fing_LR=curl(60),
              eyes=(0.8, 0, 0))),
        (10, up, "out"),
        (15, apex),
        (20, P(pelvis=(0, 0, -0.07), spine=(8, 0, 0), head=(-6, 0, 0), uarm_LR=arm(-40, 105), larm_LR=arm(-40),
               fing_LR=curl(80), eyes=happy), "in"),
        (26, P(pelvis=(0, 0, -0.02), head=(-10, 6, 0), uarm_LR=arm(-40, 112), larm_LR=arm(-25), fing_LR=curl(80),
               eyes=happy)),
        (32, P(pelvis=(0, 0, -0.03), head=(-10, -6, 0), uarm_LR=arm(-40, 128), larm_LR=arm(-45), fing_LR=curl(80),
               eyes=happy)),
        (38, P(pelvis=(0, 0, -0.02), uarm_LR=arm(-10, 20), larm_LR=arm(-20), eyes=happy)),
        (48, STAND),
    ])


@anim("EdgeBalance", 48, True, "an der Kante: vor und zurück wanken, Arme rudern", "Phase3")
def edge_balance(f):
    t = f / 48.0
    sway = wave(t, 1)
    return P(
        pelvis=(0, -0.012 * sway, -0.02),
        pelvis_rot=(5 * sway, 0, 0),
        spine=(8 * sway, 0, 3 * wave(t, 1, 0.3)),
        head=(14 - 4 * sway, 0, 4 * wave(t, 2)),
        uarm_L=arm(-60 + 38 * wave(t, 2), 78),
        uarm_R=arm(-60 + 38 * wave(t, 2, 0.5), 78),
        larm_LR=arm(-20),
        fing_LR=curl(5),
        thumb_LR=curl(0),
        foot_LR=(0.0, 0.0, 0.0, 0.0),
        eyes=(1.15, 0.006 * wave(t, 3), 0.002),
    )


# ================================ Phase 4: dances (120 BPM, 1 beat = 15 frames) ================================

BEAT = 15


def beat_bounce(f, depth=0.022):
    """Down on every beat."""
    return -depth * abs(math.sin(math.pi * f / BEAT))


@anim("DanceBob", 60, True, "Groove: Wippen auf jeden Schlag, Seitschritt, Kopfnicken", "Phase4")
def dance_bob(f):
    t = f / 60.0
    side = wave(t, 1)
    return P(
        pelvis=(0.03 * side, 0, -0.02 + beat_bounce(f)),
        pelvis_rot=(0, 3 * side, -6 * side),
        spine=(3, -4 * side, 6 * side),
        head=(6 * abs(math.sin(math.pi * f / BEAT)) - 2, 6 * side, -4 * side),
        uarm_L=arm(-35 + 25 * wave(t, 2), 18),
        uarm_R=arm(-35 + 25 * wave(t, 2, 0.5), 18),
        larm_LR=arm(-70),
        fing_LR=curl(65),
        thumb_LR=curl(40),
        foot_L=(0.03 + 0.03 * max(0.0, side), 0.0, 0.035 * max(0.0, wave(t, 2, 0.25)), 0.0),
        foot_R=(0.03 + 0.03 * max(0.0, -side), 0.0, 0.035 * max(0.0, wave(t, 2, 0.75)), 0.0),
        eyes=(0.75 + 0.25 * abs(math.cos(math.pi * f / BEAT)), 0.006 * side, 0.002),
    )


ROBOT_POSES = [
    P(uarm_L=arm(-90, 0), larm_L=arm(-90), uarm_R=arm(0, 4), larm_R=arm(0), head=(0, 0, 30), eyes=(0.8, 0.01, 0)),
    P(uarm_L=arm(0, 85), larm_L=arm(0, 0, 0), uarm_R=arm(0, 85), larm_R=arm(0), head=(0, 0, 0), spine=(0, 0, 0),
      pelvis=(0, 0, -0.04), eyes=(1.0, 0, 0)),
    P(uarm_R=arm(-90, 0), larm_R=arm(-90), uarm_L=arm(0, 4), larm_L=arm(0), head=(0, 0, -30), eyes=(0.8, -0.01, 0)),
    P(uarm_LR=arm(-90, 0), larm_LR=arm(0), spine=(0, 0, 20), head=(0, 0, -20), pelvis=(0, 0, -0.03)),
    P(uarm_LR=arm(-90, 0), larm_LR=arm(0), spine=(0, 0, -20), head=(0, 0, 20), pelvis=(0, 0, -0.03)),
    P(uarm_L=arm(-40, 30), larm_L=arm(-90), uarm_R=arm(-40, 30), larm_R=arm(-90), head=(10, 0, 0),
      pelvis=(0, 0, -0.06), eyes=(0.5, 0, 0)),
    P(uarm_L=arm(0, 85), larm_L=arm(-90, 0, 0), uarm_R=arm(0, 4), larm_R=arm(0), head=(-8, 0, 25)),
    P(uarm_R=arm(0, 85), larm_R=arm(-90, 0, 0), uarm_L=arm(0, 4), larm_L=arm(0), head=(-8, 0, -25)),
]


@anim("DanceRobot", 120, True, "der Roboter-Tanz: abgehackte Posen im Takt", "Phase4")
def dance_robot(f):
    keys = []
    for i, pose in enumerate(ROBOT_POSES + [ROBOT_POSES[0]]):
        fr = i * BEAT
        if i:
            keys.append((fr - BEAT + 4, keys[-1][1], "linear"))   # hold, then snap in 4 frames
        p = dict(pose)
        p["fing_L"] = p["fing_R"] = (5.0,)
        keys.append((fr, p, "out"))
    return keyed(f, keys)


@anim("DanceWave", 60, True, "Armwelle von der linken zur rechten Hand und zurück", "Phase4")
def dance_wave(f):
    t = f / 60.0
    chain = []   # L hand, L forearm, L upper arm, shoulders, R upper arm, R forearm, R hand
    for i in range(7):
        chain.append(math.sin(2 * math.pi * (t - i * 0.07)))
    return P(
        pelvis=(0, 0, -0.02 + beat_bounce(f, 0.012)),
        spine=(0, 0, 3 * wave(t, 1)),
        head=(0, 6 * wave(t, 1, 0.25), 0),
        uarm_L=arm(0, 82 + 12 * chain[2]),
        larm_L=(0.0, -22 * chain[1], 0.0),
        hand_L=(0.0, -25 * chain[0], 0.0),
        shoulder_L=(0, -6 * chain[3], 0),
        shoulder_R=(0, -6 * chain[3], 0),
        uarm_R=arm(0, 82 + 12 * chain[4]),
        larm_R=(0.0, -22 * chain[5], 0.0),
        hand_R=(0.0, -25 * chain[6], 0.0),
        fing_LR=curl(0),
        thumb_LR=curl(0),
        eyes=(0.8, 0.01 * wave(t, 1), 0),
    )


@anim("DanceShuffle", 30, True, "schneller Shuffle (Running Man) auf der Stelle", "Phase4")
def dance_shuffle(f):
    t = f / 30.0
    feet = {}
    for side, off in (("L", 0.0), ("R", 0.5)):
        u = (t + off) % 1.0
        if u < 0.5:      # planted foot slides back
            y = -0.10 + 0.20 * (u / 0.5)
            feet[side] = (0.02, y, 0.0, 0.0)
        else:            # knee lift and reach forward
            w = (u - 0.5) / 0.5
            feet[side] = (0.02, lerp(0.10, -0.10, ease(w)), 0.085 * math.sin(math.pi * w), -6 * math.sin(math.pi * w))
    c = wave(t, 1)
    return P(
        pelvis=(0, 0, -0.04 - 0.012 * abs(wave(t, 2))),
        pelvis_rot=(4, 0, -6 * c),
        spine=(6, 0, 6 * c),
        head=(-6 + 3 * abs(wave(t, 2)), 0, -3 * c),
        uarm_L=arm(-30 * c - 20, 12),
        uarm_R=arm(30 * c - 20, 12),
        larm_LR=arm(-95),
        fing_LR=curl(75),
        thumb_LR=curl(45),
        foot_L=feet["L"],
        foot_R=feet["R"],
        eyes=(0.6, 0, 0.003),
    )


@anim("DanceDisco", 60, True, "Disco-Zeigepose: rechte Hand oben/unten im Takt, Hüftschwung", "Phase4")
def dance_disco(f):
    up = P(pelvis=(0.03, 0, -0.025), pelvis_rot=(0, 4, -10), spine=(-4, -6, 10), head=(-12, -6, -10),
           uarm_R=arm(-150, -20), larm_R=arm(-6), fing_R=curl(80), thumb_R=curl(50), uarm_L=arm(10, 14),
           larm_L=arm(-70), fing_L=curl(70), foot_R=(0.05, -0.06, 0.0, 12), eyes=(0.7, -0.01, 0.003))
    down = P(pelvis=(-0.03, 0, -0.03), pelvis_rot=(0, -4, 8), spine=(6, 6, -8), head=(10, 4, 8),
             uarm_R=arm(-58, -14), larm_R=arm(-6), fing_R=curl(80), thumb_R=curl(50), uarm_L=arm(10, 14),
             larm_L=arm(-70), fing_L=curl(70), foot_R=(0.05, -0.06, 0.0, 12), eyes=(0.9, 0.01, 0))
    return keyed(f, [(0, up), (BEAT, down, "out"), (2 * BEAT, up, "out"), (3 * BEAT, down, "out"), (4 * BEAT, up, "out")])


@anim("DanceSpin", 60, False, "360°-Drehung in kleinen Schritten, Schlusspose (Root Motion)", "Phase4",
      "Root Motion (Rotation)")
def dance_spin(f):
    t = min(1.0, f / 44.0)
    spin = math.sin(math.pi * t)
    pose = P(root=(0.0, 0.0, 360.0 * ease(t)), pelvis=(0, 0, -0.02 - 0.015 * spin), uarm_LR=arm(-10, 70 * spin),
             larm_LR=arm(-15), fing_LR=curl(5), head=(-6 * spin, 0, 0), eyes=(0.6 + 0.4 * (1 - spin), 0, 0),
             **stepping_feet(ease(t), 360.0, 8, 1, widen=0.045))
    if f <= 44:
        return pose
    finale = P(root=(0.0, 0.0, 360.0), pelvis=(0, 0, -0.01), spine=(-4, 0, 0), head=(-10, 6, 0),
               uarm_R=arm(-150, -25), larm_R=arm(-4), fing_R=curl(80), thumb_R=curl(50), uarm_L=arm(0, 30),
               larm_L=arm(-10), footyaw_LR=(360.0,), eyes=(0.35, 0, 0.006))
    return keyed(f, [(44, pose), (52, finale, "out"), (60, finale)])


# ================================ Phase 5: emotes ================================

SIT = P(pelvis=(0, 0.06, -0.334), pelvis_rot=(-32, 0, 0), spine=(28, 0, 0), head=(-2, 0, 0), uarm_LR=arm(-36, 12),
        larm_LR=arm(-26), fing_LR=curl(30), foot_LR=(0.13, -0.40, 0.0, -68))


def with_(pose, **kw):
    p = dict(pose)
    for k, v in kw.items():
        if k.endswith("_LR"):
            p[k[:-3] + "_L"] = p[k[:-3] + "_R"] = tuple(v)
        else:
            p[k] = tuple(v)
    return p


@anim("Wave", 48, False, "freundlich winken (rechte Hand)", "Phase5")
def wave_hello(f):
    up = P(head=(0, -6, -6), spine=(0, -3, 0), uarm_R=arm(-12, 95), larm_R=arm(0, 85), hand_R=arm(0), fing_R=curl(0),
           thumb_R=curl(0), eyes=(0.9, -0.006, 0))
    keys = [(0, STAND), (9, up)]
    for i, fr in enumerate((13, 17, 21, 25, 29, 33)):
        keys.append((fr, with_(up, larm_R=arm(0, 66 if i % 2 == 0 else 100))))
    keys += [(38, up), (48, STAND)]
    return keyed(f, keys)


@anim("Bow", 48, False, "höfliche Verbeugung", "Phase5")
def bow(f):
    down = P(pelvis=(0, 0.04, -0.025), pelvis_rot=(8, 0, 0), spine=(30, 0, 0), head=(16, 0, 0), uarm_LR=arm(8, 6),
             larm_LR=arm(-8), fing_LR=curl(10), eyes=(0.3, 0, -0.004))
    return keyed(f, [(0, STAND), (14, down), (30, down), (48, STAND)])


@anim("Clap", 30, True, "Klatschen", "Phase5")
def clap(f):
    t = f / 30.0
    c = 0.5 + 0.5 * math.cos(4 * math.pi * t)          # 1 = hands apart, 0 = together (two claps per loop)
    return P(spine=(3, 0, 0), head=(-4, 0, 0), uarm_LR=arm(-56, -10 + 10 * c), larm_LR=arm(-48 + 6 * c),
             hand_LR=arm(0, 0, 0), fing_LR=curl(4), thumb_LR=curl(5), pelvis=(0, 0, -0.016 - 0.006 * (1 - c)),
             eyes=(0.85, 0, 0.002))


@anim("Think", 72, True, "Nachdenken, Hand am Kinn", "Phase5")
def think(f):
    t = f / 72.0
    return P(pelvis=(0.008 * wave(t, 1), 0, -0.016), spine=(4, 0, -4), head=(-6 + 2 * wave(t, 2), -10, 8 + 4 * wave(t, 1)),
             uarm_R=arm(-66, -16), larm_R=arm(-96), hand_R=arm(-30), fing_R=curl(65), thumb_R=curl(40),
             uarm_L=arm(2, 6), eyes=(0.8, 0.008 * wave(t, 1), 0.004))


@anim("Point", 40, False, "nach vorn zeigen", "Phase5")
def point(f):
    p = P(spine=(2, 0, -8), head=(0, 0, -6), uarm_R=arm(-86, -4), larm_R=arm(-2), hand_R=arm(0), fing_R=curl(0),
          thumb_R=curl(40), uarm_L=arm(4, 6), eyes=(0.85, -0.004, 0))
    return keyed(f, [(0, STAND), (10, p, "out"), (30, p), (40, STAND)])


@anim("Shrug", 36, False, "Achselzucken", "Phase5")
def shrug(f):
    p = P(shoulder_LR=(0, -6, 0), uarm_LR=arm(-10, 26), larm_LR=arm(-72), hand_LR=arm(0, 0, -30), fing_LR=curl(0),
          thumb_LR=curl(0), head=(0, 10, 0), eyes=(0.9, 0, 0.003))
    return keyed(f, [(0, STAND), (10, p, "out"), (22, p), (36, STAND)])


@anim("NodYes", 30, False, "Ja-Nicken", "Phase5")
def nod_yes(f):
    return keyed(f, [(0, STAND), (6, P(head=(14, 0, 0))), (12, P(head=(-3, 0, 0))), (18, P(head=(12, 0, 0))),
                     (24, P(head=(-2, 0, 0))), (30, STAND)])


@anim("ShakeNo", 30, False, "Nein-Kopfschütteln", "Phase5")
def shake_no(f):
    return keyed(f, [(0, STAND), (6, P(head=(2, 0, 18))), (12, P(head=(2, 0, -18))), (18, P(head=(2, 0, 16))),
                     (24, P(head=(2, 0, -12))), (30, STAND)])


@anim("SitDown", 40, False, "hinsetzen: Grundpose -> SitIdle (Frame 0)", "Phase5")
def sit_down(f):
    return keyed(f, [
        (0, STAND),
        (12, P(pelvis=(0, 0.10, -0.125), spine=(16, 0, 0), head=(-6, 0, 0), uarm_LR=arm(-20, 12), larm_LR=arm(-20),
               foot_LR=(0.10, -0.08, 0.0, 0.0)), "in"),
        (18, P(pelvis=(0, 0.11, -0.235), pelvis_rot=(-6, 0, 0), spine=(24, 0, 0), head=(-4, 0, 0),
               uarm_LR=arm(-28, 12), larm_LR=arm(-22), foot_LR=(0.12, -0.33, 0.05, -45))),
        (26, with_(SIT, pelvis=(0, 0.06, -0.326)), "out"),
        (40, SIT),
    ])


@anim("SitIdle", 90, True, "sitzen: atmen, umschauen, mit den Füßen wippen", "Phase5")
def sit_idle(f):
    t = f / 90.0
    br = wave(t, 3)
    tap = max(0.0, wave(t, 6))
    return with_(SIT, spine=(28 + br, 0, 2 * wave(t, 1)), head=(-2 + 2 * br, 4 * wave(t, 1, 0.3), 14 * wave(t, 1)),
                 foot_L=(0.13, -0.40, 0.0, -68 + 10 * tap), foot_R=(0.13, -0.40, 0.0, -68 + 10 * max(0.0, wave(t, 6, 0.5))),
                 eyes=(min(blink(f, 30), blink(f, 75)), 0.008 * wave(t, 1), 0))


@anim("Sleep", 120, True, "eingeschlafen im Sitzen, ruhiges Atmen", "Phase5")
def sleep(f):
    t = f / 120.0
    br = wave(t, 3)
    return P(pelvis=(0, 0.06, -0.334), pelvis_rot=(-32, 0, 0), spine=(42 + 2 * br, 0, 0), head=(50 + 3 * br, 5, 6),
             shoulder_LR=(0, 8 - 2 * br, 0), uarm_LR=arm(0, 16), larm_LR=arm(-10), fing_LR=curl(38),
             foot_LR=(0.13, -0.40, 0.0, -68), eyes=(0.12, 0, -0.008))


@anim("DanceMoonwalk", 40, True, "Moonwalk: Füße gleiten nach hinten", "Phase5")
def moonwalk(f):
    t = f / 40.0

    def foot(u):
        u %= 1.0
        if u < 0.5:
            return (0.02, -0.12 + 0.24 * (u / 0.5), 0.0, 0.0)
        w = (u - 0.5) / 0.5
        return (0.02, lerp(0.12, -0.12, ease(w)), 0.010 * math.sin(math.pi * w), 32 * math.sin(math.pi * w) ** 0.4)
    c = wave(t, 1)
    return P(pelvis=(0.008 * c, 0.02, -0.030 - 0.010 * abs(wave(t, 2))), spine=(-4, 0, 3 * c), head=(-4, 0, -6),
             uarm_L=arm(10 * c, 12), uarm_R=arm(-10 * c, 12), larm_LR=arm(-30), fing_LR=curl(50),
             foot_L=foot(t), foot_R=foot(t + 0.5), eyes=(0.75, 0, 0.003))


@anim("DanceSway", 64, True, "langsames Schunkeln mit erhobenen Armen", "Phase5")
def dance_sway(f):
    t = f / 64.0
    sw = wave(t, 1)
    return P(pelvis=(0.03 * sw, 0, -0.025 + 0.008 * abs(sw)), pelvis_rot=(0, 4 * sw, 0), spine=(0, -6 * sw, 0),
             head=(-10, 8 * sw, 0), uarm_L=arm(-25, 122 + 8 * sw), uarm_R=arm(-25, 122 - 8 * sw), larm_LR=arm(-15),
             fing_LR=curl(10), foot_LR=(0.03, 0.0, 0.0, 0.0), eyes=(0.5, 0.008 * sw, 0.004))


# ================================ Phase 6: climbing and hanging ================================

# The ledge in root space: top 1.20 m above the root, front edge 0.22 m in front of it. The hands grip the edge
# by IK (knuckles 0.24 m forward, 1.215 m up), so a game can align the character to any ledge from these numbers.
LEDGE_TOP, LEDGE_EDGE = 1.20, 0.22
GRIP_X, GRIP_Y, GRIP_Z = 0.35, -0.215, 1.232
HANG_POLE = (1.0, 0.3, -0.2)                      # elbows out


def grip(dx=0.0, w=1.0, pole=HANG_POLE):
    return (GRIP_X + dx, GRIP_Y, GRIP_Z) + tuple(pole) + (w,)


HANG = P(pelvis=(0, 0.06, 0.135), spine=(-3, 0, 0), head=(-14, 0, 0), uarm_LR=arm(-30, 140), larm_LR=arm(0, 14),
         fing_LR=curl(20), thumb_LR=curl(65), foot_LR=(0.0, 0.08, 0.185, 18), eyes=(1.0, 0, 0.004),
         handik_LR=grip())


@anim("LedgeGrab", 20, False, "aus dem Fallen eine Kante greifen -> HangIdle (Frame 0)", "Phase6",
      "Kante: Oberseite 1,20 m über Root, Vorderkante 0,22 m vor Root")
def ledge_grab(f):
    return keyed(f, [
        (0, fall(0)),
        (5, P(pelvis=(0, 0.03, 0.06), spine=(-4, 0, 0), head=(-12, 0, 0), uarm_LR=arm(-30, 132), larm_LR=arm(0, 10),
              fing_LR=curl(5), foot_LR=(0.01, 0.0, 0.12, -8), eyes=(1.12, 0, 0.004), handik_LR=grip(w=0.0)), "out"),
        (9, with_(HANG, pelvis=(0, 0.06, 0.115), foot_LR=(0.0, -0.02, 0.20, 10)), "in"),
        (14, with_(HANG, foot_LR=(0.0, 0.11, 0.17, 20))),
        (20, HANG),
    ])


@anim("HangIdle", 60, True, "an einer Kante hängen (Hände greifen die Vorderkante per IK)", "Phase6",
      "Kante: Oberseite 1,20 m über Root, Vorderkante 0,22 m vor Root, Griffe ±0,35 m")
def hang_idle(f):
    t = f / 60.0
    return with_(HANG, pelvis=(0.004 * wave(t, 1), 0.06 + 0.012 * wave(t, 1, 0.2), 0.135),
                 head=(-14, 2 * wave(t, 1), 4 * wave(t, 1, 0.3)),
                 foot_L=(0.0, 0.08 + 0.03 * wave(t, 1), 0.185, 18), foot_R=(0.0, 0.08 + 0.03 * wave(t, 1, 0.15), 0.185, 18),
                 eyes=(min(1.0, blink(f, 40)), 0.006 * wave(t, 1, 0.3), 0.004))


def shimmy(f, sign):
    t = f / 36.0
    a = wave(t, 1)
    return with_(HANG, pelvis=(sign * 0.03 * a, 0.06, 0.135 - 0.01 * abs(a)), spine=(-3, sign * 3 * a, 0),
                 handik_L=grip(sign * 0.05 * a), handik_R=grip(-sign * 0.05 * a),
                 foot_L=(0.0 + 0.02 * a, 0.08, 0.185, 18), foot_R=(0.0 - 0.02 * a, 0.08, 0.185, 18),
                 head=(-14, 0, sign * 5))


@anim("ShimmyLeft", 36, True, "hängend nach links hangeln", "Phase6", "Controller bewegt seitlich")
def shimmy_left(f):
    return shimmy(f, 1)


@anim("ShimmyRight", 36, True, "hängend nach rechts hangeln", "Phase6", "Controller bewegt seitlich")
def shimmy_right(f):
    return shimmy(f, -1)


@anim("HangSwing", 48, True, "an einer Stange vor dem Gesicht hängen und schwingen", "Phase6",
      "Stange wie Kantengriff: 1,20 m über Root, 0,22 m vor Root")
def hang_swing(f):
    # the arms cannot reach above the big head, so the bar is in front of the face (same grip as the ledge)
    t = f / 48.0
    sw = wave(t, 1)
    return with_(HANG, pelvis=(0, 0.06 + 0.035 * sw, 0.135 - 0.008 * abs(sw)), spine=(-3 + 6 * sw, 0, 0),
                 head=(-14 - 4 * sw, 0, 0),
                 foot_LR=(0.0, 0.08 - 0.07 * sw, 0.185 + 0.02 * abs(sw), 18 - 10 * sw))


@anim("ClimbUp", 48, False, "aus dem Hängen auf die Kante ziehen (Root Motion 1,20 m hoch, 0,36 m vor)", "Phase6",
      "Root Motion (Position)")
def climb_up(f):
    push = (0.6, 1.0, 0.2)                         # elbows back while pressing down on the ledge
    k22 = P(root=(0, -0.10, 0), root_z=(0.85,), pelvis=(0, 0, -0.05), spine=(26, 0, 0), head=(-12, 0, 0),
            uarm_LR=arm(-30, 22), larm_LR=arm(-60), fing_LR=curl(20), foot_L=(0.02, -0.02, 0.06, 0),
            foot_R=(0.02, 0.02, 0.04, 15), handik_LR=grip(pole=push))
    k32 = P(root=(0, -0.22, 0), root_z=(1.18,), pelvis=(0, 0.02, -0.04), spine=(20, 0, 0), head=(-6, 0, 0),
            uarm_LR=arm(-20, 14), larm_LR=arm(-30), foot_L=(0.03, -0.32, 0.04, 0), foot_R=(0.03, 0.02, 0.0, 0),
            handik_LR=grip(w=0.0, pole=push))
    k42 = P(root=(0, -0.36, 0), root_z=(1.20,), pelvis=(0, 0, -0.04), spine=(8, 0, 0), uarm_LR=arm(-6, 8),
            larm_LR=arm(-14), foot_LR=(0.0, -0.36, 0.0, 0), handik_LR=grip(w=0.0, pole=push))
    # feet: lift first (sole above the ledge top), then step forward onto it - never through the ledge
    k28 = with_(lerp_pose(k22, k32, 0.6), root_z=(1.10,), foot_L=(0.03, 0.03, 0.11, 0), foot_R=(0.02, 0.02, 0.02, 10),
                handik_LR=grip(w=0.0, pole=push))
    k36 = with_(lerp_pose(k32, k42, 0.4), foot_R=(0.03, 0.0, 0.06, 0), foot_L=(0.02, -0.34, 0.015, 0))
    p = keyed(f, [
        (0, HANG),
        (12, P(root_z=(0.45,), pelvis=(0, 0.04, 0.0), spine=(10, 0, 0), head=(-8, 0, 0), uarm_LR=arm(-10, 70),
               larm_LR=arm(0, 70), fing_LR=curl(20), thumb_LR=curl(30), foot_LR=(0.0, 0.07, 0.10, 15),
               handik_LR=grip(pole=push))),
        (22, k22), (28, k28), (32, k32), (36, k36), (42, k42),
        (48, P(root=(0, -0.36, 0), root_z=(1.20,), foot_LR=(0.0, -0.36, 0.0, 0), handik_LR=grip(w=0.0, pole=push))),
    ])
    if 22 < f < 28:                                  # hands leave the ledge while the knee comes up
        ik = p["handik_L"]
        p = with_(p, handik_LR=ik[:6] + (1.0 - ease((f - 22) / 6.0),))
    press = ease(f / 10.0) if f <= 22 else 1.0 - ease((f - 22) / 8.0)   # palms flat on the ledge while pressing
    p = with_(p, hand_LR=tuple(x + HAND_PRESS * press * y for x, y in zip(p["hand_L"], arm(1.0))))
    return p


HAND_PRESS = -45       # wrist angle (deg) for a flat palm on the ledge

LADDER_Y = -0.27        # rung plane 0.27 m in front of the root
LADDER_RAIL = 0.33      # the hands hold the rails (beside the big head)


def ladder(f, frames=32):
    t = f / frames

    def foot(u):
        u %= 1.0
        if u < 0.5:
            return (0.03, -0.10, 0.15 * (1 - u / 0.5), 0.0)
        w = (u - 0.5) / 0.5
        return (0.03, -0.10 - 0.02 * math.sin(math.pi * w), 0.15 * ease(w) + 0.03 * math.sin(math.pi * w), 0.0)

    def hand(u):
        """Knuckle on the rung while gripping (moves down with the in-place climb), then reach up."""
        u %= 1.0
        if u < 0.5:
            y, z = LADDER_Y, 1.08 - 0.15 * (u / 0.5)
        else:
            w = (u - 0.5) / 0.5
            y, z = LADDER_Y + 0.07 * math.sin(math.pi * w), 0.93 + 0.15 * ease(w)
        return (LADDER_RAIL, y, z, 1.0, 0.2, -0.6, 1.0)

    def arm_reach(u):
        u %= 1.0
        k = u / 0.5 if u < 0.5 else 1 - ease((u - 0.5) / 0.5)
        rx = -150 + 90 * k                       # FK fallback only (IK weight 1)
        return arm(rx, 14 * math.cos(math.radians(rx))), arm(-20 - 70 * k)

    uL, lL = arm_reach(t + 0.5)
    uR, lR = arm_reach(t)
    return P(pelvis=(0.01 * wave(t, 1), 0.02, -0.01), spine=(6, 0, 3 * wave(t, 1)), head=(-12, 0, 0),
             uarm_L=uL, larm_L=lL, uarm_R=uR, larm_R=lR, fing_LR=curl(70), thumb_LR=curl(45),
             handik_L=hand(t + 0.5), handik_R=hand(t),
             foot_L=foot(t), foot_R=foot(t + 0.5), eyes=(0.95, 0, 0.004))


@anim("LadderUp", 32, True, "Leiter hochklettern (Controller steigt ca. 0,28 m/s)", "Phase6", "in-place, vertikal")
def ladder_up(f):
    return ladder(f)


@anim("LadderDown", 32, True, "Leiter hinunterklettern", "Phase6", "in-place, vertikal")
def ladder_down(f):
    return ladder(32 - f)


@anim("Vault", 32, False, "über ein niedriges Hindernis flanken", "Phase6")
def vault(f):
    return keyed(f, [
        (0, STAND),
        (6, P(pelvis=(0, 0, -0.07), spine=(30, 0, 0), head=(-10, 0, 0), uarm_LR=arm(-70, 8), larm_LR=arm(-10),
              fing_LR=curl(10))),
        (12, P(pelvis=(0.05, 0, 0.20), spine=(24, 0, -10), head=(-6, 0, 0), uarm_LR=arm(-52, 6), larm_LR=arm(-4),
               foot_LR=(0.04, 0.0, 0.27, -10), eyes=(1.1, 0, 0.002)), "out"),
        (20, P(pelvis=(-0.02, 0, 0.12), spine=(8, 0, 6), uarm_LR=arm(10, 40), larm_LR=arm(-20),
               foot_LR=(0.02, -0.04, 0.10, -6))),
        (25, P(pelvis=(0, 0, -0.10), spine=(14, 0, 0), uarm_LR=arm(-20, 25), larm_LR=arm(-30)), "in"),
        (32, STAND),
    ])

# ================================ Phase 7: game tempo and crouching ================================


def fast_gait(f, frames, stride, stance, lift, kick, lean, pelvis_z, swing, bob_amp, tilt=4):
    t = f / frames
    c = math.cos(2 * math.pi * t)
    feet = {}
    for side, off in (("L", 0.0), ("R", 0.5)):
        feet[side] = gait_foot(t + off, stride, stance, lift, -4, 10, kick=kick, outward=0.006)
    bob = math.cos(4 * math.pi * (t - stance / 2))
    return P(
        pelvis=(0.008 * math.sin(2 * math.pi * t), 0.010, pelvis_z - bob_amp * bob),
        pelvis_rot=(tilt, 0, -10 * c),
        spine=(lean + 4 - tilt + 2 * bob, 0, 10 * c),
        head=(-lean + 2 - 2 * bob, 0, -4 * c),
        uarm_L=arm(swing * c, 10),
        uarm_R=arm(-swing * c, 10),
        larm_L=arm(-84 + 14 * c),
        larm_R=arm(-84 - 14 * c),
        hand_LR=arm(-10),
        shoulder_L=(0, -4 * max(0.0, -c), 0),
        shoulder_R=(0, -4 * max(0.0, c), 0),
        fing_LR=curl(78),
        thumb_LR=curl(48),
        foot_L=feet["L"],
        foot_R=feet["R"],
        eyes=(0.92, 0, 0.003),
    )


@anim("RunFast", 12, True, "schneller Lauf mit langer Flugphase (Grundtempo ca. 3 m/s)", "Phase7",
      "in-place; Animator-Tempo = Geschwindigkeit / naturalSpeed")
def run_fast(f):
    return fast_gait(f, 12, 0.38, 0.30, 0.058, 0.0, 20, -0.084, 48, 0.016)


@anim("Sprint", 10, True, "Cartoon-Sprint, stark vorgebeugt, Arme pumpen (Grundtempo ca. 4,2 m/s)", "Phase7",
      "in-place; Animator-Tempo = Geschwindigkeit / naturalSpeed")
def sprint(f):
    return fast_gait(f, 10, 0.40, 0.28, 0.050, 0.0, 28, -0.090, 58, 0.014, tilt=-8)


CROUCH_DEEP = P(pelvis=(0, 0.100, -0.232), pelvis_rot=(-14, 0, 0), spine=(46, 0, 0), head=(10, 0, 0),
                shoulder_LR=(0, 4, 0), uarm_LR=arm(-30, 22), larm_LR=arm(-50), hand_LR=arm(-10), fing_LR=curl(40),
                foot_LR=(0.150, 0.0, 0.0, 0.0), footyaw_L=(32,), footyaw_R=(-32,), eyes=(0.9, 0, 0.004))


@anim("CrouchDeep", 40, True, "tiefes Ducken: kompakt zusammengefaltet für niedrige Durchgänge", "Phase7",
      "Kapsel: siehe clips.CrouchDeep.height")
def crouch_deep(f):
    w = wave(f / 40.0, 1)
    return with_(CROUCH_DEEP, pelvis=(0, 0.100, -0.232 - 0.003 * w), spine=(46 + w, 0, 0), head=(10 - w, 0, 0))


@anim("CrouchWalk", 28, True, "geduckt schleichen (Grundtempo ca. 0,3 m/s)", "Phase7",
      "in-place; Animator-Tempo = Geschwindigkeit / naturalSpeed")
def crouch_walk(f):
    t = f / 28.0
    c = math.cos(2 * math.pi * t)
    feet = {}
    for side, off in (("L", 0.0), ("R", 0.5)):
        feet[side] = gait_foot(t + off, 0.20, 0.66, 0.028, -4, 6, outward=0.075)
    return P(
        pelvis=(0.016 * math.sin(2 * math.pi * t), 0.050, -0.135 - 0.006 * math.cos(4 * math.pi * t)),
        pelvis_rot=(-6, 0, -5 * c),
        spine=(44 + math.cos(4 * math.pi * t), 0, 5 * c),
        head=(-34, 0, -3 * c),
        shoulder_LR=(0, 2, 0),
        uarm_L=arm(-26 + 12 * c, 14),
        uarm_R=arm(-26 - 12 * c, 14),
        larm_LR=arm(-50),
        hand_LR=arm(-10),
        fing_LR=curl(38),
        foot_L=feet["L"],
        foot_R=feet["R"],
        eyes=(0.85, 0.006 * c, 0.003),
    )


# ---------------------------------- per-clip game data ----------------------------------
# Everything the game needs besides the bone curves. Exported to robot_config.json ("clips") and the viewer.
# Expression/display keys: [frame, name]; "$base" = the look's own choice. Emission keys: [frame, factor], linear.

def _flicker(f0, f1, a, b, step=2):
    return [[k, a if (k - f0) // step % 2 == 0 else b] for k in range(f0, f1, step)]


CLIP_EXPRESSIONS = {
    "Death": _flicker(0, 27, "Glitch", "$base") + [[27, "Off"]],
    "Hit": [[0, "Angry"], [10, "$base"]], "HitHeavy": [[0, "Angry"], [10, "$base"]],
    "Respawn": [[0, "Off"], [17, "Loading"], [24, "$base"]],
    "SitDown": [[0, "$base"], [31, "Cute"]], "PickUp": [[0, "$base"], [21, "Love"]],
}
for _names, _expr in (
        (("Celebrate", "DanceBob", "DanceShuffle", "DanceDisco", "DanceSway", "Wave", "Talk", "NodYes"), "Happy"),
        (("DanceRobot",), "Pixel"), (("DanceWave", "DanceSpin", "DanceMoonwalk", "DoubleJump", "Vault"), "Excited"),
        (("Electrocuted",), "Glitch"), (("GasCough",), "Crying"), (("IdleYawn",), "Sleepy"), (("Sleep",), "Sleeping"),
        (("Interact", "TerminalTyping", "CrouchWalk", "CrouchDeep"), "Focused"),
        (("Scan", "ToolAim", "ToolUse", "Point"), "Target"),
        (("Fall", "SlopeSlide", "EdgeBalance", "PortalEnter", "LedgeGrab"), "Surprised"),
        (("Push", "PushStart", "PushStep", "MudWalk", "ClimbUp", "LadderUp", "LadderDown", "HangIdle", "ShimmyLeft",
          "ShimmyRight", "HangSwing", "RunFast", "Sprint"), "Determined"),
        (("Listen", "SitIdle"), "Cute"), (("Clap",), "Laugh"), (("Bow",), "Shy"), (("Think", "IdleScratch"), "Question"),
        (("Shrug",), "Skeptical"), (("ShakeNo",), "Sad"), (("IdleFootTap",), "LookLeft")):
    for _n in _names:
        CLIP_EXPRESSIONS[_n] = [[0, _expr]]

CLIP_DISPLAYS = {
    "Death": [[0, "Lightning"], [21, "Warning"]],
    "Respawn": [[0, "Loading"], [24, "$base"]],
}
for _names, _disp in (
        (("Electrocuted", "Hit", "HitHeavy", "Sprint"), "Lightning"), (("GasCough", "MudWalk"), "Warning"),
        (("Celebrate", "DanceSpin"), "Star"), (("Sleep",), "Clock"), (("Scan",), "Signal"),
        (("Run", "RunFast", "LadderUp", "ClimbUp"), "Heart"), (("Talk", "Wave"), "Hello"), (("Think",), "Loading")):
    for _n in _names:
        CLIP_DISPLAYS[_n] = [[0, _disp]]

# Emission factor for eyes, chest display and lights ("power"): Death switches the robot off, Respawn on.
CLIP_EMISSION = {
    "Death": [[0, 1.0], [18, 1.0], [20, 0.4], [21, 1.0], [23, 0.25], [24, 0.8], [27, 0.15], [36, 0.03], [64, 0.0]],
    "Respawn": [[0, 0.0], [8, 0.35], [10, 0.05], [12, 0.8], [14, 0.2], [17, 1.0], [24, 1.15], [30, 1.0]],
    "Electrocuted": [[0, 1.0], [2, 1.8], [4, 0.3], [6, 1.6], [9, 0.5], [12, 1.9], [15, 0.4], [18, 1.0]],
    "Hit": [[0, 1.0], [1, 1.7], [5, 1.0]],
    "HitHeavy": [[0, 1.0], [1, 2.0], [4, 0.4], [7, 1.3], [10, 1.0]],
    "Sleep": [[0, 0.35], [60, 0.28], [120, 0.35]],
    "IdleYawn": [[0, 1.0], [30, 0.7], [60, 0.7], [90, 1.0]],
    "Celebrate": [[0, 1.0], [20, 1.4], [48, 1.0]],
}

# Semantic events (besides the measured footsteps) for Unity animation events / sounds.
CLIP_EVENTS = {
    "Jump": [[10, "TakeOff"]], "Land": [[0, "Impact"]], "Interact": [[19, "Press"]], "Death": [[37, "Thud"]],
    "DoubleJump": [[2, "Boost"]], "HitHeavy": [[1, "Impact"]], "Clap": [[8, "Clap"], [23, "Clap"]],
    "ClimbUp": [[42, "Arrive"]], "LedgeGrab": [[9, "Grab"]], "Vault": [[12, "Hop"], [25, "Land"]],
}

# Helper geometry the viewer shows to check contacts (the game provides the real geometry).
CLIP_PROPS = {n: "ledge" for n in ("LedgeGrab", "HangIdle", "ShimmyLeft", "ShimmyRight", "ClimbUp")}
CLIP_PROPS.update({"HangSwing": "bar", "LadderUp": "ladder", "LadderDown": "ladder", "Push": "box",
                   "PushStart": "box", "PushStep": "box"})


def clip_meta(a):
    """Static per-clip data (measured data is added by measure_clips)."""
    def check(keys, valid):
        for _, v in keys:
            assert v == "$base" or v in valid, "unknown name %s in clip %s" % (v, a.name)
        return keys
    return {
        "name": a.name, "group": a.group, "frames": a.frames, "fps": FPS, "loop": a.loop,
        "hideHeldItem": a.name in HIDE_HELD_ITEM,
        "expression": check(CLIP_EXPRESSIONS.get(a.name, []), make_textures.EXPRESSIONS),
        "display": check(CLIP_DISPLAYS.get(a.name, []), make_textures.DISPLAYS),
        "emission": CLIP_EMISSION.get(a.name, []),
        "events": CLIP_EVENTS.get(a.name, []),
        "prop": None,
    }


def prop_info(name, hands):
    """Helper geometry for the viewer, derived from the clip's constants and measured hand positions."""
    kind = CLIP_PROPS.get(name)
    if kind == "ledge":
        return {"type": "ledge", "top": LEDGE_TOP, "edge": LEDGE_EDGE}
    if kind == "ladder":
        return {"type": "ladder", "forward": -LADDER_Y, "rail": LADDER_RAIL, "spacing": 0.30}
    if kind in ("bar", "box") and hands:
        up = (hands["L"][0][1] + hands["R"][0][1]) / 2
        fwd = (hands["L"][0][2] + hands["R"][0][2]) / 2
        if kind == "bar":                   # bar centre just below the knuckles
            return {"type": "bar", "up": round(up - 0.03, 3), "forward": round(fwd + 0.005, 3)}
        return {"type": "box", "forward": round(fwd + 0.01, 3), "top": 0.9}
    return None


def add_preview_tool(arm_ob, coll):
    """Flashlight proxy parented to Prop_R - only for preview renders, never exported."""
    bpy.context.view_layer.update()
    pb = arm_ob.pose.bones["Prop_R"]
    m = arm_ob.matrix_world @ pb.matrix
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=14, radius1=0.020, radius2=0.026, depth=0.13)
    bmesh.ops.translate(bm, vec=(0, 0, 0.045), verts=bm.verts[:])
    me = bpy.data.meshes.new("Preview_Tool")
    bm.to_mesh(me)
    bm.free()
    mat = bpy.data.materials.new("Preview_Tool")
    if mat.node_tree is None:
        mat.use_nodes = True
    b = mat.node_tree.nodes.get("Principled BSDF")
    b.inputs["Base Color"].default_value = (0.25, 0.27, 0.3, 1)
    b.inputs["Metallic"].default_value = 0.8
    b.inputs["Emission Color"].default_value = (0.2, 0.9, 1.0, 1)
    b.inputs["Emission Strength"].default_value = 0.6
    me.materials.append(mat)
    tool = bpy.data.objects.new("Preview_Tool", me)
    coll.objects.link(tool)
    # cone axis (+Z) along the bone (+Y)
    tool.matrix_world = m @ Matrix.Rotation(math.radians(-90), 4, "X")
    tool.parent = arm_ob
    tool.parent_type = "BONE"
    tool.parent_bone = "Prop_R"
    bone_tail = arm_ob.matrix_world @ pb.matrix @ Matrix.Translation((0, pb.bone.length, 0))
    tool.matrix_parent_inverse = bone_tail.inverted()
    tool.hide_render = True
    return tool


def write_animation_table(out, data=None):
    """ANIMATIONS.md next to the outputs: always in sync with the generated clips (and their measured data)."""
    data = data or {}
    lines = ["# Cube Escape Roboter – Animationsliste (Generator %s)" % VERSION, "",
             "Automatisch erzeugt von `generate_robot.py`. 30 fps, alle Clips in-place (außer *Root Motion*).",
             "Tempo = Grundtempo des In-place-Clips (Animator-Tempo = Spielgeschwindigkeit / Tempo), "
             "Höhe = kleinste/größte Körperhöhe im Clip, Schritte = Frames mit Fußaufsatz. "
             "Alle Werte maschinenlesbar in `robot_config.json` › `clips`.", ""]
    for g in GROUPS:
        clips = [a for a in ANIMS if a.group == g]
        if not clips:
            continue
        lines += ["## %s – %s" % (g, GROUP_LABELS[g]), "",
                  "| Clip | Frames | Loop | Tempo | Höhe | Schritte | Unity | Beschreibung |",
                  "|---|---|---|---|---|---|---|---|"]
        for a in clips:
            d = data.get(a.name, {})
            speed = "%.2f m/s" % d["naturalSpeed"] if d.get("naturalSpeed") else "–"
            height = "%.2f–%.2f m" % (d["height"]["min"], d["height"]["max"]) if "height" in d else "–"
            steps = ", ".join(str(f) for f, _ in d.get("footsteps", [])) or "–"
            lines.append("| %s | 0–%d | %s | %s | %s | %s | %s | %s |" % (
                a.name, a.frames, "ja" if a.loop else "nein", speed, height, steps, a.unity or "–", a.desc))
        lines.append("")
    with open(os.path.join(out, "ANIMATIONS.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def build_animations(arm_ob):
    solver = Solver(arm_ob)
    actions = {}
    for a in ANIMS:
        act, slot = write_action(arm_ob, solver, a.name, a.frames, a.fn, a.loop)
        actions[a.name] = act
        LOG.info("  action %-15s %3d frames  loop=%-5s %s  (%s)", a.name, a.frames, a.loop, a.group, a.desc)
    # Unity-friendly default: rest pose action assigned last is Idle
    assign_action(arm_ob, actions["Idle"])
    # NLA tracks keep every action visible and editable in the .blend
    ad = arm_ob.animation_data
    for name in reversed([a.name for a in ANIMS]):
        track = ad.nla_tracks.new()
        track.name = name
        strip = track.strips.new(name, 0, actions[name])
        strip.action_slot = actions[name].slots[0]
        track.mute = True
    return actions


# ----------------------------------------------------------------------------------------------
# QA
# ----------------------------------------------------------------------------------------------


def group_faces(ob):
    me = ob.data
    vg_of_vertex = np.full(len(me.vertices), -1, dtype=np.int32)
    for v in me.vertices:
        if v.groups:
            vg_of_vertex[v.index] = v.groups[0].group
    names = {g.index: g.name for g in ob.vertex_groups}
    groups = {}
    for p in me.polygons:
        g = vg_of_vertex[p.vertices[0]]
        groups.setdefault(names[g], []).append(list(p.vertices))
    return groups


def evaluated_coords(ob):
    dg = bpy.context.evaluated_depsgraph_get()
    ev = ob.evaluated_get(dg)
    me = ev.to_mesh()
    co = np.empty(len(me.vertices) * 3, dtype=np.float64)
    me.vertices.foreach_get("co", co)
    ev.to_mesh_clear()
    mw = np.array(ob.matrix_world)
    co = co.reshape(-1, 3) @ mw[:3, :3].T + mw[:3, 3]
    return co


def group_topology(groups):
    """Per vertex group: its vertex indices and its polygons in local indices - computed once per mesh."""
    topo = {}
    for g, polys in groups.items():
        idx = sorted({i for p in polys for i in p})
        remap = {o: n for n, o in enumerate(idx)}
        topo[g] = (np.array(idx, dtype=np.int64), [[remap[i] for i in p] for p in polys])
    return topo


class GroupTrees:
    """BVH trees of the vertex groups of one mesh state, built only when a pair needs them. Groups whose
    bounding boxes are apart cannot overlap, so the box test answers most pairs without a tree (same result
    as testing every pair; the margin covers float32 rounding inside the trees)."""

    MARGIN = 1e-5

    def __init__(self, co, topo):
        self.co, self.topo, self.trees = co, topo, {}
        self.lo, self.hi = {}, {}
        for g, (idx, _) in topo.items():
            pts = co[idx]
            self.lo[g] = pts.min(axis=0) - self.MARGIN
            self.hi[g] = pts.max(axis=0) + self.MARGIN

    def tree(self, g):
        t = self.trees.get(g)
        if t is None:
            idx, polys = self.topo[g]
            t = self.trees[g] = BVHTree.FromPolygons(self.co[idx].tolist(), polys)
        return t

    def overlap(self, a, b):
        if (self.lo[a] > self.hi[b]).any() or (self.lo[b] > self.hi[a]).any():
            return False
        return bool(self.tree(a).overlap(self.tree(b)))


# Contacts that are intended: fabric accessories may be squeezed by the drooping head.
ACCEPTED_OVERLAPS = {("*", "Head", "Socket_Neck"), ("*", "Eyes", "Socket_Neck"),
                     # held items are gripped by the fingers
                     ("*", "Fingers_R", "Prop_R"), ("*", "Prop_R", "Thumb_R"),
                     ("*", "Fingers_L", "Prop_L"), ("*", "Prop_L", "Thumb_L")}


# Clips in which a held item (Prop_R) is hidden by the game: climbing, hanging, thinking with the hand at the chin.
HIDE_HELD_ITEM = {"Think", "LedgeGrab", "HangIdle", "ShimmyLeft", "ShimmyRight", "HangSwing", "ClimbUp", "LadderUp",
                  "LadderDown", "Vault", "PickUp", "TerminalTyping", "Clap", "Wave", "EdgeBalance"}


def run_qa(ob, arm_ob, actions, report, label="Classic", quiet=False):
    scene = bpy.context.scene
    groups = group_faces(ob)
    bones = arm_ob.data.bones
    parent = {b.name: (b.parent.name if b.parent else None) for b in bones}
    names = sorted(groups)
    allowed = set()
    for a in names:
        for b in names:
            if parent.get(a) == b or parent.get(b) == a:
                allowed.add((a, b))
    allowed.add(("Fingers_L", "Thumb_L"))
    allowed.add(("Fingers_R", "Thumb_R"))
    # baseline: pairs already touching in rest pose are joints by construction
    ad = arm_ob.animation_data
    saved = ad.action
    ad.action = None
    for pb in arm_ob.pose.bones:
        pb.location = (0, 0, 0)
        pb.rotation_quaternion = (1, 0, 0, 0)
        pb.scale = (1, 1, 1)
    bpy.context.view_layer.update()
    rest_co = evaluated_coords(ob)
    topo = group_topology(groups)
    rest_trees = GroupTrees(rest_co, topo)
    baseline = set()
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if rest_trees.overlap(a, b):
                baseline.add((a, b))
    # the eye screen is a decal (it shrinks when blinking), not a joint
    joints = [(a, b) for a in names for b in names if parent.get(a) == b and a != "Eyes"]
    qa = {"rest_min_z": float(rest_co[:, 2].min()), "rest_height": float(rest_co[:, 2].max() - rest_co[:, 2].min()),
          "actions": {}}
    LOG.info("  [%s] rest pose: height %.3f m, min z %.4f m, baseline joint overlaps %d", label, qa["rest_height"],
             qa["rest_min_z"], len(baseline))
    vgroup = np.empty(len(ob.data.vertices), dtype=object)
    for g, polys in groups.items():
        for poly in polys:
            vgroup[poly] = g
    foot_groups = {"L": "Foot_L", "R": "Foot_R"}
    foot_idx = {s: sorted({i for p in groups[foot_groups[s]] for i in p}) for s in foot_groups}
    for name, frames, loop, fn, desc, *_ in ANIMS:
        act = actions[name]
        assign_action(arm_ob, act)
        min_z = 1e9
        min_z_where = None
        new_overlaps = {}
        detached = {}
        flight_frames = 0
        stance_speed = []
        prev_foot = None
        first = last = None
        pairs = [(a, b) for i, a in enumerate(names) for b in names[i + 1:]
                 if not ((a, b) in allowed or (a, b) in baseline or (name, a, b) in ACCEPTED_OVERLAPS or
                         ("*", a, b) in ACCEPTED_OVERLAPS or (name in HIDE_HELD_ITEM and "Prop_R" in (a, b)))]
        joint_pairs = [(child, par) for child, par in joints
                       if (min(child, par), max(child, par)) in baseline or (child, par) in baseline or
                       (par, child) in baseline]
        for f in range(0, frames + 1):
            scene.frame_set(f)
            co = evaluated_coords(ob)
            if f == 0:
                first = co.copy()
            if f == frames:
                last = co
            iz = int(co[:, 2].argmin())
            if co[iz, 2] < min_z:
                min_z = float(co[iz, 2])
                min_z_where = "%s@%d" % (vgroup[iz], f)
            trees = GroupTrees(co, topo)
            for a, b in pairs:
                if trees.overlap(a, b):
                    new_overlaps.setdefault("%s/%s" % (a, b), []).append(f)
            for child, par in joint_pairs:
                if not trees.overlap(child, par):
                    detached.setdefault("%s/%s" % (child, par), []).append(f)
            feet_z = {s: float(co[foot_idx[s], 2].min()) for s in foot_idx}
            if all(z > 0.004 for z in feet_z.values()):
                flight_frames += 1
            foot_pos = {s: co[foot_idx[s]].mean(axis=0) for s in foot_idx}
            if prev_foot is not None:
                for s in foot_idx:
                    if feet_z[s] < 0.003 and prev_feet_z[s] < 0.003:
                        stance_speed.append(abs(foot_pos[s][1] - prev_foot[s][1]) * FPS)
            prev_foot, prev_feet_z = foot_pos, feet_z
        loop_err = float(np.abs(first - last).max()) if loop else None
        entry = {
            "frames": frames,
            "loop": loop,
            "min_z": round(min_z, 4),
            "min_z_at": min_z_where,
            "new_overlaps": {k: [v[0], v[-1], len(v)] for k, v in new_overlaps.items()},
            "detached_joints": {k: [v[0], v[-1], len(v)] for k, v in detached.items()},
            "flight_frames": flight_frames,
            "loop_error": loop_err,
            "stance_speed_mps": round(float(np.median(stance_speed)), 3) if stance_speed else None,
        }
        qa["actions"][name] = entry
        lvl = logging.DEBUG if quiet else logging.INFO
        if min_z < -0.006 or new_overlaps or detached or (loop and loop_err > 1e-4):
            lvl = logging.WARNING
        LOG.log(lvl, "  QA[%s] %-9s min_z=%+.4f (%s) overlaps=%s detached=%s flight=%d loop_err=%s speed=%s", label, name, min_z, min_z_where,
                entry["new_overlaps"] or "-", entry["detached_joints"] or "-", flight_frames,
                "-" if loop_err is None else "%.1e" % loop_err, entry["stance_speed_mps"])
    ad.action = saved
    report.setdefault("qa", {})[label] = qa
    return qa


def char_space(v):
    """Blender world (front -Y, Z up) -> character space [right, up, forward] (= Unity local space)."""
    return [round(-v[0], 4) + 0.0, round(v[2], 4) + 0.0, round(-v[1], 4) + 0.0]    # + 0.0: no "-0.0"


def contact_ranges(flags, loop):
    """Frames with ground contact as [[first, last], ...]; footsteps = first frames (cyclic for loops)."""
    ranges, start = [], None
    for f, on in enumerate(flags):
        if on and start is None:
            start = f
        if not on and start is not None:
            ranges.append([start, f - 1])
            start = None
    if start is not None:
        ranges.append([start, len(flags) - 1])
    steps = []
    for a, b in ranges:
        if a == 0 and not loop:
            continue                # standing at the start is not a step
        prev = flags[a - 1] if a > 0 else (flags[-2] if len(flags) > 1 else False)
        if not prev and not (loop and a == len(flags) - 1):
            steps.append(a)
    return ranges, steps


def measure_clips(ob, arm_ob, actions):
    """Measured game data per clip: root motion, foot contacts, footsteps, natural speed, height, hand grips."""
    scene = bpy.context.scene
    groups = group_faces(ob)
    foot_idx = {s: sorted({i for p in groups["Foot_" + s] for i in p}) for s in ("L", "R")}
    root_pb = arm_ob.pose.bones["Root"]
    grip_pb = {s: arm_ob.pose.bones["Fingers_" + s] for s in ("L", "R")}
    mw = arm_ob.matrix_world
    root_rest = arm_ob.data.bones["Root"].matrix_local.to_3x3().inverted()
    out = {}
    for a in ANIMS:
        assign_action(arm_ob, actions[a.name])
        heights, contact, speeds, hands, roots = [], {"L": [], "R": []}, [], {"L": [], "R": []}, []
        prev = None
        for f in range(a.frames + 1):
            scene.frame_set(f)
            co = evaluated_coords(ob)
            heights.append(float(co[:, 2].max() - max(0.0, co[:, 2].min())))
            cur = {}
            for s in ("L", "R"):
                pts = co[foot_idx[s]]
                on = float(pts[:, 2].min()) < 0.006
                contact[s].append(on)
                cur[s] = (on, pts.mean(axis=0))
                if prev and on and prev[s][0]:
                    speeds.append(float(np.linalg.norm((cur[s][1] - prev[s][1])[:2])) * FPS)
                hands[s].append(char_space(mw @ grip_pb[s].head))
            prev = cur
            m = mw @ root_pb.matrix
            r = m.to_3x3() @ root_rest
            roots.append((m.translation.copy(), math.atan2(r[1][0], r[0][0])))
        d = roots[-1][0] - roots[0][0]
        yaw = 0.0                   # unwrapped (DanceSpin turns a full circle)
        for (_, y0), (_, y1) in zip(roots, roots[1:]):
            yaw += math.degrees((y1 - y0 + math.pi) % (2 * math.pi) - math.pi)
        entry = {
            "rootMotion": {"move": char_space(d), "turnRight": round(-yaw, 2) + 0.0},
            "height": {"min": round(min(heights), 3), "max": round(max(heights), 3)},
            "naturalSpeed": round(float(np.median(speeds)), 3) if len(speeds) > 3 and a.loop else 0.0,
            "feet": {}, "footsteps": [],
        }
        for s in ("L", "R"):
            ranges, steps = contact_ranges(contact[s], a.loop)
            entry["feet"][s] = ranges
            entry["footsteps"] += [[f, "Foot_" + s] for f in steps]
        entry["footsteps"].sort()
        if a.name in CLIP_PROPS or a.group == "Phase6":
            entry["hands"] = hands
        out[a.name] = entry
    return out


def type_bounds(part_objs, cfg):
    """Rest-pose size of a configuration. coreWidth leaves out forearms, hands and held items (they swing
    freely); it is the number to compare with the character capsule diameter."""
    free = ("LowerArm_", "Hand_", "Fingers_", "Thumb_", "Prop_")
    lo, hi = [1e9] * 3, [-1e9] * 3
    core = [1e9, -1e9]
    for g in config_groups(cfg):
        ob = part_objs[g]
        names = {vg.index: vg.name for vg in ob.vertex_groups}
        mw = ob.matrix_world
        for v in ob.data.vertices:
            w = mw @ v.co
            for i in range(3):
                lo[i], hi[i] = min(lo[i], w[i]), max(hi[i], w[i])
            if not v.groups or not names[v.groups[0].group].startswith(free):
                core = [min(core[0], w.x), max(core[1], w.x)]
    return {"width": round(hi[0] - lo[0], 3), "coreWidth": round(core[1] - core[0], 3),
            "depth": round(hi[1] - lo[1], 3), "height": round(hi[2] - lo[2], 3)}


# ----------------------------------------------------------------------------------------------
# Preview rendering
# ----------------------------------------------------------------------------------------------


PREVIEW_SAMPLE_SCALE = 1.0      # set per tier: previews in the standard tier only need to be readable


def preview_samples(n):
    return max(4, int(round(n * PREVIEW_SAMPLE_SCALE)))


def look_at(cam, target):
    d = Vector(target) - cam.location
    cam.rotation_euler = d.to_track_quat("-Z", "Y").to_euler()


def setup_render_scene(coll):
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.eevee.taa_render_samples = preview_samples(64)
    try:
        scene.eevee.use_shadows = True
        scene.eevee.use_raytracing = True
    except AttributeError:
        pass
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.look = "AgX - Medium High Contrast"
    scene.render.film_transparent = False
    world = scene.world or bpy.data.worlds.new("World")
    scene.world = world
    if world.node_tree is None:
        world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg is None:
        bg = world.node_tree.nodes.new("ShaderNodeBackground")
        out = world.node_tree.nodes.get("World Output") or world.node_tree.nodes.new("ShaderNodeOutputWorld")
        world.node_tree.links.new(bg.outputs[0], out.inputs[0])
    bg.inputs[0].default_value = (0.020, 0.032, 0.048, 1.0)
    bg.inputs[1].default_value = 1.0

    # floor with subtle tile grid
    fm = bpy.data.materials.new("Preview_Floor")
    if fm.node_tree is None:
        fm.use_nodes = True
    k = NodeKit(fm)
    b = k.n.get("Principled BSDF")
    vec, x, y, z = k.coords()
    gx = k.math("ABSOLUTE", k.math("SUBTRACT", k.math("FRACT", k.math("DIVIDE", x, 1.0)), 0.5))
    gy = k.math("ABSOLUTE", k.math("SUBTRACT", k.math("FRACT", k.math("DIVIDE", y, 1.0)), 0.5))
    line = k.map_range(k.math("MAXIMUM", gx, gy), 0.488, 0.495, 0.0, 1.0)
    col = k.mix(line, (0.030, 0.036, 0.046), (0.09, 0.11, 0.13))
    k.link(col, b.inputs["Base Color"])
    b.inputs["Roughness"].default_value = 0.55
    b.inputs["Metallic"].default_value = 0.3
    bpy.ops.mesh.primitive_plane_add(size=30, location=(0, 0, 0))
    floor = bpy.context.active_object
    floor.name = "Preview_Floor"
    floor.data.materials.append(fm)
    for c in floor.users_collection:
        c.objects.unlink(floor)
    coll.objects.link(floor)

    def light(name, kind, loc, energy, color, size=1.0, target=(0, 0, 0.7)):
        ld = bpy.data.lights.new(name, kind)
        ld.energy = energy
        ld.color = color
        if kind == "AREA":
            ld.size = size
        lo = bpy.data.objects.new(name, ld)
        lo.location = loc
        coll.objects.link(lo)
        look_at(lo, target)
        return lo

    light("Key", "AREA", (-2.2, -2.6, 2.8), 420, (1.0, 0.93, 0.85), 2.0)
    light("Fill", "AREA", (2.6, -1.8, 1.4), 130, (0.75, 0.88, 1.0), 2.5)
    light("Rim", "AREA", (1.2, 2.8, 2.2), 700, (0.35, 0.85, 1.0), 1.5)
    light("RimL", "AREA", (-2.0, 2.4, 1.6), 380, (0.45, 0.8, 1.0), 1.5)
    cam_data = bpy.data.cameras.new("Preview_Camera")
    cam = bpy.data.objects.new("Preview_Camera", cam_data)
    coll.objects.link(cam)
    scene.camera = cam
    return cam


def render_views(cam, out_dir, prefix="", res=1100):
    scene = bpy.context.scene
    scene.render.resolution_x = res
    scene.render.resolution_y = res
    scene.render.image_settings.file_format = "PNG"
    views = {
        "front": ("ORTHO", (0, -6, 0.66), (0, 0, 0.66), 1.55),
        "side": ("ORTHO", (6, 0, 0.66), (0, 0, 0.66), 1.55),
        "back": ("ORTHO", (0, 6, 0.66), (0, 0, 0.66), 1.55),
        "iso": ("ORTHO", (-4.5, -4.5, 4.1), (0, 0, 0.62), 1.75),
        "head": ("PERSP", (-0.55, -1.15, 1.25), (0, -0.05, 1.10), 50.0),
        "hero": ("PERSP", (0.75, -2.35, 0.42), (0, 0, 0.66), 42.0),
    }
    paths = {}
    for name, (kind, loc, target, val) in views.items():
        cam.data.type = kind
        if kind == "ORTHO":
            cam.data.ortho_scale = val
        else:
            cam.data.lens = val
        cam.location = loc
        look_at(cam, target)
        path = os.path.join(out_dir, "%s%s.png" % (prefix, name))
        scene.render.filepath = path
        bpy.ops.render.render(write_still=True)
        paths[name] = path
        LOG.info("  rendered %s", path)
    # small iso shot to judge readability at game distance
    scene.render.resolution_x = scene.render.resolution_y = 360
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = 4.5
    cam.location = (-4.5, -4.5, 4.1)
    look_at(cam, (0, 0, 0.62))
    path = os.path.join(out_dir, "%sdistance.png" % prefix)
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)
    paths["distance"] = path
    return paths


def render_anim_sheets(cam, arm_ob, actions, out_dir):
    scene = bpy.context.scene
    scene.eevee.taa_render_samples = preview_samples(24)
    scene.render.resolution_x = scene.render.resolution_y = 300
    sheet_dir = os.path.join(out_dir, "anim_frames")
    os.makedirs(sheet_dir, exist_ok=True)
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = 1.75
    rows = []
    for name, frames, loop, fn, desc, *_ in ANIMS:
        assign_action(arm_ob, actions[name])
        tool = bpy.data.objects.get("Preview_Tool")
        if tool is not None:
            tool.hide_render = name not in TOOL_CLIPS
        picks = [round(i * frames / (6 if loop else 5)) for i in range(6)]
        files = []
        for view, loc in (("side", (5.0, -1.2, 0.9)), ("iso", (-4.0, -4.0, 3.6))):
            cam.location = loc
            look_at(cam, (0, -0.05, 0.6))
            for f in picks:
                scene.frame_set(f)
                p = os.path.join(sheet_dir, "%s_%s_%03d.png" % (name, view, f))
                scene.render.filepath = p
                bpy.ops.render.render(write_still=True)
                files.append(p)
        rows.append((name, files, picks))
        LOG.info("  anim frames %s", name)
    return rows


def magick(*args):
    exe = make_textures.find_magick()
    subprocess.run([exe] + [str(a) for a in args], check=True, capture_output=True)


def build_contact_sheets(rows, out_dir):
    """ImageMagick: one labelled row per animation, side and iso view."""
    group_of = {a.name: a.group for a in ANIMS}
    for group, view in [(g, v) for g in GROUPS for v in ("side", "iso")]:
        row_files = []
        for name, files, picks in rows:
            if group_of[name] != group:
                continue
            sel = [f for f in files if "_%s_" % view in os.path.basename(f)]
            row = os.path.join(out_dir, "anim_frames", "row_%s_%s.png" % (view, name))
            args = ["montage"]
            for f, fr in zip(sel, picks):
                args += ["-label", "%s  f%d" % (name, fr), f]
            args += ["-tile", "%dx1" % len(sel), "-geometry", "+3+3", "-background", "#10161d", "-fill", "#d8e6f0",
                     "-pointsize", "15", row]
            magick(*args)
            row_files.append(row)
        if not row_files:
            continue
        sheet = os.path.join(out_dir, "animations_%s_%s.png" % (group, view))
        magick(*(row_files + ["-background", "#10161d", "-append", sheet]))
        for r in row_files:
            os.remove(r)
        LOG.info("  contact sheet %s", sheet)


def set_look(ob, tex_dir, skin=DEFAULT_SKIN, expression="Normal", display="Status"):
    """Preview look on the combined mesh: baked skin textures + eye/display atlas cell + colours."""
    pal_ = SKINS[skin]
    for mat in ob.data.materials:
        nt = mat.node_tree
        if mat.name.startswith("M_RobotEyes"):
            nt.nodes["EyeColor"].outputs[0].default_value = (*pal_["eye"], 1.0)
            set_atlas_cell(mat, "eyes", EXPRESSION_INDEX[expression])
        elif mat.name.startswith("M_RobotDisplay"):
            nt.nodes["DisplayColor"].outputs[0].default_value = (*pal_["screen"], 1.0)
            set_atlas_cell(mat, "displays", DISPLAY_INDEX[display])
        elif mat.name.startswith("M_Robot"):
            if "TintColor" in nt.nodes:
                nt.nodes["TintColor"].outputs[0].default_value = (*pal_["fabric"], 1.0)
            suffix = "" if skin == DEFAULT_SKIN else "_" + skin
            for nd in nt.nodes:
                if nd.type == "TEX_IMAGE" and nd.image.name.startswith("T_Robot_BaseColor"):
                    nd.image = bpy.data.images.load(os.path.join(tex_dir, "T_Robot_BaseColor%s.png" % suffix),
                                                    check_existing=True)
                elif nd.type == "TEX_IMAGE" and nd.image.name.startswith("T_Robot_Emission"):
                    nd.image = bpy.data.images.load(os.path.join(tex_dir, "T_Robot_Emission%s.png" % suffix),
                                                    check_existing=True)


def render_expressions(cam, ob, out_dir, tex_dir):
    scene = bpy.context.scene
    scene.eevee.taa_render_samples = preview_samples(32)
    scene.render.resolution_x = scene.render.resolution_y = 300
    cam.data.type = "PERSP"
    cam.data.lens = 70
    cam.location = (0.0, -1.6, 1.12)
    look_at(cam, (0, 0, 1.11))
    tiles = []
    for name in make_textures.EXPRESSIONS:
        set_look(ob, tex_dir, expression=name)
        p = os.path.join(out_dir, "expr_%s.png" % name)
        scene.render.filepath = p
        bpy.ops.render.render(write_still=True)
        tiles += ["-label", name, p]
    set_look(ob, tex_dir)
    sheet = os.path.join(out_dir, "expressions.png")
    magick("montage", *tiles, "-tile", "4x", "-geometry", "+3+3", "-background", "#10161d", "-fill", "#d8e6f0",
           "-pointsize", "18", sheet)
    for i in range(2, len(tiles), 3):
        os.remove(tiles[i])
    LOG.info("  expressions sheet %s", sheet)


def render_types(cam, part_objs, robot_mesh, coll, out_dir, tex_dir):
    """Every robot type (variants + skin + eyes + display + accessories), front and iso."""
    scene = bpy.context.scene
    scene.eevee.taa_render_samples = preview_samples(48)
    scene.render.resolution_x = scene.render.resolution_y = 520
    robot_mesh.hide_render = True
    tiles = {"front": [], "iso": []}
    for tname, spec in ROBOT_TYPES.items():
        cfg = default_config()
        cfg.update(spec["variants"])
        objs = [part_objs[g] for g in config_groups(cfg)]
        for o in objs:
            o.hide_render = False
        for o in objs:
            set_look(o, tex_dir, spec["skin"], spec["expression"], spec["display"])
        for view, loc, val in (("front", (0, -6, 0.70), 1.62), ("iso", (-4.5, -4.5, 4.2), 1.85)):
            cam.data.type = "ORTHO"
            cam.data.ortho_scale = val
            cam.location = loc
            look_at(cam, (0, 0, 0.70))
            p = os.path.join(out_dir, "type_%s_%s.png" % (tname, view))
            scene.render.filepath = p
            bpy.ops.render.render(write_still=True)
            tiles[view] += ["-label", tname, p]
        for o in objs:
            o.hide_render = True
            set_look(o, tex_dir)
    robot_mesh.hide_render = False
    for view, files in tiles.items():
        sheet = os.path.join(out_dir, "types.png" if view == "front" else "types_iso.png")
        magick("montage", *files, "-tile", "4x", "-geometry", "+3+3", "-background", "#10161d", "-fill", "#d8e6f0",
               "-pointsize", "22", sheet)
        for i in range(2, len(files), 3):
            os.remove(files[i])
        LOG.info("  robot types sheet %s", sheet)


def render_skins(cam, ob, out_dir, tex_dir):
    """Front + iso render per skin (texture swap on M_Robot), combined with ImageMagick."""
    scene = bpy.context.scene
    scene.eevee.taa_render_samples = preview_samples(48)
    scene.render.resolution_x = scene.render.resolution_y = 520
    nodes = ob.data.materials[0].node_tree.nodes
    base = next(n for n in nodes if n.type == "TEX_IMAGE" and n.image.name.startswith("T_Robot_BaseColor"))
    emit = next(n for n in nodes if n.type == "TEX_IMAGE" and n.image.name.startswith("T_Robot_Emission"))
    orig = (base.image, emit.image)
    tiles = {"front": [], "iso": []}
    for skin in SKINS:
        set_look(ob, tex_dir, skin)
        for view, kind, loc, val in (("front", "ORTHO", (0, -6, 0.66), 1.5), ("iso", "ORTHO", (-4.5, -4.5, 4.1), 1.7)):
            cam.data.type = kind
            cam.data.ortho_scale = val
            cam.location = loc
            look_at(cam, (0, 0, 0.64))
            p = os.path.join(out_dir, "skin_%s_%s.png" % (skin, view))
            scene.render.filepath = p
            bpy.ops.render.render(write_still=True)
            tiles[view] += ["-label", skin, p]
    set_look(ob, tex_dir)
    for view, files in tiles.items():
        sheet = os.path.join(out_dir, "skins.png" if view == "front" else "skins_iso.png")
        magick("montage", *files, "-tile", "4x", "-geometry", "+3+3", "-background", "#10161d", "-fill", "#d8e6f0",
               "-pointsize", "22", sheet)
        for i in range(2, len(files), 3):
            os.remove(files[i])
        LOG.info("  skins sheet %s", sheet)


def render_gifs(cam, arm_ob, actions, out_dir):
    scene = bpy.context.scene
    scene.eevee.taa_render_samples = preview_samples(16)
    scene.render.resolution_x = scene.render.resolution_y = 320
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = 1.75
    cam.location = (-4.0, -4.0, 3.6)
    look_at(cam, (0, -0.05, 0.6))
    tmp = os.path.join(out_dir, "anim_frames", "gif")
    os.makedirs(tmp, exist_ok=True)
    for name, step in (("Idle", 2), ("Walk", 1), ("Run", 1), ("DanceRobot", 2), ("Celebrate", 1)):
        act = actions[name]
        assign_action(arm_ob, act)
        n = int(act.frame_range[1])
        files = []
        for f in range(0, n, step):
            scene.frame_set(f)
            p = os.path.join(tmp, "%s_%03d.png" % (name, f))
            scene.render.filepath = p
            bpy.ops.render.render(write_still=True)
            files.append(p)
        gif = os.path.join(out_dir, "anim_%s.gif" % name)
        magick("-delay", "%dx%d" % (step, FPS), "-loop", "0", *files, "-layers", "Optimize", gif)
        for p in files:
            os.remove(p)
        LOG.info("  gif %s", gif)
    os.rmdir(tmp)


# ----------------------------------------------------------------------------------------------
# Export
# ----------------------------------------------------------------------------------------------


def export_fbx(path, objs, arm_ob, animations=True):
    hidden = [o for o in objs if o.hide_get()]
    for o in hidden:
        o.hide_set(False)              # hidden objects cannot be selected for use_selection
    for o in bpy.context.scene.objects:
        o.select_set(o in objs or o == arm_ob)
    bpy.context.view_layer.objects.active = arm_ob
    ad = arm_ob.animation_data
    ad.action = None
    # every NLA track holds one action; exported as its own take named after the strip ("Walk", not "Rig|Walk")
    for track in ad.nla_tracks:
        track.mute = False
    for pb in arm_ob.pose.bones:
        pb.location = (0, 0, 0)
        pb.rotation_quaternion = (1, 0, 0, 0)
        pb.scale = (1, 1, 1)
    bpy.ops.export_scene.fbx(
        filepath=path, use_selection=True, object_types={"ARMATURE", "MESH"},
        apply_unit_scale=True, apply_scale_options="FBX_SCALE_ALL", global_scale=1.0,
        axis_forward="-Z", axis_up="Y", use_space_transform=True, bake_space_transform=False,
        mesh_smooth_type="OFF", use_mesh_modifiers=True, use_triangles=True, use_tspace=True,
        add_leaf_bones=False, primary_bone_axis="Y", secondary_bone_axis="X",
        use_armature_deform_only=True, armature_nodetype="NULL",
        bake_anim=animations, bake_anim_use_all_bones=True, bake_anim_use_nla_strips=True,
        bake_anim_use_all_actions=False, bake_anim_force_startend_keying=True, bake_anim_step=1.0,
        bake_anim_simplify_factor=0.0, path_mode="STRIP", embed_textures=False, use_custom_props=False,
    )
    for track in ad.nla_tracks:
        track.mute = True
    for o in hidden:
        o.hide_set(True)
    assign_action(arm_ob, bpy.data.actions["Idle"])
    LOG.info("  wrote %s (%.1f KB)", path, os.path.getsize(path) / 1024)


# ----------------------------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------------------------


def qa_robot_type(tname, part_objs, coll, arm_ob, actions, report):
    cfg = default_config()
    cfg.update(ROBOT_TYPES[tname]["variants"])
    tmp = combine_objects([part_objs[g] for g in config_groups(cfg)], "QA_" + tname, coll)
    bind_mesh(tmp, arm_ob)
    run_qa(tmp, arm_ob, actions, report, tname, quiet=True)
    bpy.data.objects.remove(tmp, do_unlink=True)


def start_qa_workers(type_names, args, out):
    """The type QA in parallel: each worker is a Blender process that rebuilds the model (no bake, no render -
    the QA only looks at geometry and animation) and checks a share of the robot types."""
    n = min(args.qa_workers, len(type_names))
    workers = []
    for i in range(n):
        chunk = type_names[i::n]
        wdir = os.path.join(out, "logs", "qa_worker_%d" % i)
        os.makedirs(wdir, exist_ok=True)
        res = os.path.join(wdir, "qa.json")
        if os.path.exists(res):
            os.remove(res)
        cmd = [bpy.app.binary_path, "-b", "--factory-startup", "-noaudio", "--python-exit-code", "1",
               "--python", os.path.abspath(__file__), "--", "--out", wdir, "--src-dir", out, "--skip-bake",
               "--no-render", "--qa-types", ",".join(chunk), "--qa-worker-out", res, "--qa-workers", "1"]
        if args.groups:
            cmd += ["--groups", args.groups]
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        workers.append((chunk, wdir, res, proc))
    LOG.info("  %d QA workers for %d robot types", n, len(type_names))
    return workers


def collect_qa_workers(workers, report):
    for chunk, wdir, res, proc in workers:
        code = proc.wait()
        if code != 0 or not os.path.exists(res):
            raise RuntimeError("QA worker for %s failed (exit %s), log: %s" % (
                ", ".join(chunk), code, os.path.join(wdir, "logs", "generate_robot.log")))
        with open(res, encoding="utf-8") as fh:
            report.setdefault("qa", {}).update(json.load(fh))
        shutil.rmtree(wdir, ignore_errors=True)


def main():
    args = parse_args()
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)
    setup_logging(out)
    t_start = time.time()
    global PREVIEW_SAMPLE_SCALE
    PREVIEW_SAMPLE_SCALE = args.preview_samples
    worker = bool(args.qa_worker_out)
    LOG.info("Cube Escape robot generator %s | Blender %s | stage=%s | tier=%s%s", VERSION, bpy.app.version_string,
             args.stage, args.tier, " (QA worker)" if worker else "")
    report = {"generator": VERSION, "blender": bpy.app.version_string, "stage": args.stage, "tier": args.tier}
    preview_dir = os.path.join(out, "Previews")
    tex_dir = os.path.join(out, "Textures")
    os.makedirs(preview_dir, exist_ok=True)

    with Step("scene"):
        reset_scene()
        coll = get_collection("Robot")
        preview_coll = get_collection("Preview")
    with Step("ImageMagick source textures"):
        src_paths = make_textures.make_all(os.path.join(args.src_dir or out, "SourceTextures"), log=LOG.info,
                                           cache=not args.no_cache)
        load_source_textures(src_paths)
        report["source_textures"] = sorted(os.path.basename(p) for p in src_paths.values())
    with Step("materials"):
        mats = build_materials()
    with Step("geometry"):
        define_joints()
        build_robot()
        floating = check_part_connectivity(report)
        parts_coll = get_collection("Robot_Parts")
        part_objs = assemble_groups(mats, parts_coll)
        for o in part_objs.values():
            apply_weighted_normals(o)
        report["slots"] = {sl: list(vs) for sl, vs in SLOTS.items()}
        report["part_objects"] = {k: tri_count(o.data) for k, o in part_objs.items()}
        LOG.info("  %d parts in %d objects; slots: %s", report["parts"], len(part_objs),
                 ", ".join("%s=%s" % (k, "/".join(v)) for k, v in SLOTS.items()))
    with Step("uv"):
        unwrap(list(part_objs.values()), None if args.no_cache else os.path.join(out, "cache"))

    if args.skip_bake:
        LOG.warning("skip-bake: procedural materials only (debug run)")
    with Step("bake textures") if not args.skip_bake else _Skip():
        if args.skip_bake:
            raise _SkipBlock()
        tex_paths = bake_textures(list(part_objs.values()), mats, args.tex_size, args.ao_samples, tex_dir,
                                  None if args.no_cache else os.path.join(out, "cache"), args.check_compose,
                                  args.bake_device)
        final = [build_baked_material(tex_paths), build_baked_material(tex_paths, "M_RobotFabric",
                                                                        SKINS[DEFAULT_SKIN]["fabric"]),
                 mats["EyeScreen"], mats["Display"]]
        for o in part_objs.values():
            remap_materials(o, final)
        report["textures"] = sorted(os.path.basename(p) for p in tex_paths.values())
    with Step("default mesh"):
        robot_mesh = combine_objects([part_objs[g] for g in config_groups(default_config())], "Robot_Mesh", coll)
        report["triangles"] = tri_count(robot_mesh.data)
        report["vertices"] = len(robot_mesh.data.vertices)
        report["materials"] = [m.name for m in robot_mesh.data.materials]
        LOG.info("  Robot_Mesh (default look): %d triangles, %d vertices, materials %s", report["triangles"],
                 report["vertices"], report["materials"])
        for o in part_objs.values():
            o.hide_render = True
            o.hide_set(True)
    if args.stage == "model":
        if not args.no_render:
            with Step("preview renders (procedural materials)"):
                cam = setup_render_scene(preview_coll)
                render_views(cam, preview_dir, prefix="model_")
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(out, "CubeEscape_Robot_model.blend"))
        write_report(out, report)
        return 1 if floating else 0
    with Step("rig"):
        arm_ob = build_armature(coll)
        for o in [robot_mesh] + list(part_objs.values()):
            bind_mesh(o, arm_ob)
        add_preview_tool(arm_ob, preview_coll)
        report["bones"] = [b.name for b in arm_ob.data.bones]
    with Step("animations"):
        if args.groups:
            keep = set(args.groups.split(",")) | {"Basis"}
            ANIMS[:] = [a for a in ANIMS if a.group in keep]
            LOG.warning("animation filter: %s (%d clips)", sorted(keep), len(ANIMS))
        actions = build_animations(arm_ob)
        write_animation_table(out)
        report["animations"] = {a.name: {"frames": a.frames, "loop": a.loop, "description": a.desc, "group": a.group,
                                         "unity": a.unity} for a in ANIMS}
    with Step("qa"):
        only = set(filter(None, args.qa_types.split(",")))
        type_names = [t for t in ROBOT_TYPES if t != "Classic" and (not only or t in only)]
        if worker:
            for tname in type_names:
                qa_robot_type(tname, part_objs, coll, arm_ob, actions, report)
            with open(args.qa_worker_out, "w", encoding="utf-8") as fh:
                json.dump({t: report["qa"][t] for t in type_names}, fh)
            return 0
        workers = start_qa_workers(type_names, args, out) if args.qa_workers > 1 and len(type_names) > 1 else []
        run_qa(robot_mesh, arm_ob, actions, report, "Classic")
        if workers:
            collect_qa_workers(workers, report)
        else:
            for tname in type_names:
                qa_robot_type(tname, part_objs, coll, arm_ob, actions, report)
        report["qa"] = {t: report["qa"][t] for t in ["Classic"] + type_names}
        for tname in type_names:
            qa_t = report["qa"][tname]
            bad = [n for n, e in qa_t["actions"].items() if e["min_z"] < -0.006 or e["new_overlaps"] or
                   e["detached_joints"]]
            LOG.log(logging.WARNING if bad else logging.INFO, "  QA[%s] %d clips, problems: %s", tname,
                    len(qa_t["actions"]), bad or "-")
    with Step("clip data"):
        clips = measure_clips(robot_mesh, arm_ob, actions)
        for a in ANIMS:
            clips[a.name] = dict(clip_meta(a), **clips[a.name])
            clips[a.name]["prop"] = prop_info(a.name, clips[a.name].get("hands"))
        report["clips"] = clips
        write_animation_table(out, clips)
        bounds = {}
        for tname, spec in ROBOT_TYPES.items():
            cfg = default_config()
            cfg.update(spec["variants"])
            bounds[tname] = type_bounds(part_objs, cfg)
        report["type_bounds"] = bounds
        for n in ("Walk", "Run", "RunFast", "Sprint", "CrouchWalk", "Push", "MudWalk"):
            if n in clips:
                LOG.info("  %-10s natural speed %.2f m/s, footsteps %s", n, clips[n]["naturalSpeed"],
                         [f for f, _ in clips[n]["footsteps"]])
        for n in ("Idle", "Crouch", "CrouchDeep", "CrouchWalk", "SitIdle"):
            if n in clips:
                LOG.info("  %-10s height %.3f..%.3f m", n, clips[n]["height"]["min"], clips[n]["height"]["max"])
        LOG.info("  width/core width: %s", ", ".join("%s %.2f/%.2f" % (k, v["width"], v["coreWidth"])
                                                     for k, v in bounds.items()))
    with Step("export fbx"):
        export_fbx(os.path.join(out, "CubeEscape_Robot.fbx"), [robot_mesh], arm_ob)
        export_fbx(os.path.join(out, "CubeEscape_Robot_Modular.fbx"), list(part_objs.values()), arm_ob,
                   animations=False)
        type_dir = os.path.join(out, "Types")
        os.makedirs(type_dir, exist_ok=True)
        report["type_fbx"] = {}
        for tname, spec in ROBOT_TYPES.items():
            cfg = default_config()
            cfg.update(spec["variants"])
            tmp = combine_objects([part_objs[g] for g in config_groups(cfg)], "Robot_Mesh_" + tname, coll)
            bind_mesh(tmp, arm_ob)
            export_fbx(os.path.join(type_dir, "CubeEscape_Robot_%s.fbx" % tname), [tmp], arm_ob, animations=False)
            report["type_fbx"][tname] = {"triangles": tri_count(tmp.data), "materials": len(tmp.data.materials)}
            bpy.data.objects.remove(tmp, do_unlink=True)
        write_config(out, clips, bounds)
        write_looks(out)
    with Step("viewer data"):
        vcfg = viewer_config()
        vcfg["clips"] = clips
        vcfg["typeBounds"] = bounds
        export_viewer.export(os.path.join(out, "ViewerData"), list(part_objs.values()), arm_ob, ANIMS, actions,
                             tex_dir, vcfg, LOG)
    if not args.no_render:
        with Step("preview renders"):
            assign_action(arm_ob, actions["Idle"])
            arm_ob.animation_data.action = None
            for pb in arm_ob.pose.bones:
                pb.rotation_quaternion = (1, 0, 0, 0)
                pb.location = (0, 0, 0)
                pb.scale = (1, 1, 1)
            cam = setup_render_scene(preview_coll)
            with Step("render views"):
                report["previews"] = render_views(cam, preview_dir)
            with Step("render skins"):
                render_skins(cam, robot_mesh, preview_dir, tex_dir)
            with Step("render expressions"):
                render_expressions(cam, robot_mesh, preview_dir, tex_dir)
            with Step("render types"):
                render_types(cam, part_objs, robot_mesh, coll, preview_dir, tex_dir)
            if not args.no_anim_sheets:
                rows = render_anim_sheets(cam, arm_ob, actions, preview_dir)
                build_contact_sheets(rows, preview_dir)
                render_gifs(cam, arm_ob, actions, preview_dir)
                frames_dir = os.path.join(preview_dir, "anim_frames")
                for fn in os.listdir(frames_dir):
                    os.remove(os.path.join(frames_dir, fn))
                os.rmdir(frames_dir)
            assign_action(arm_ob, actions["Idle"])
            bpy.context.scene.frame_set(0)
    with Step("save blend"):
        preview_coll.hide_render = False
        bpy.context.scene.frame_end = 120
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(out, "CubeEscape_Robot.blend"))
    total = time.time() - t_start
    report["timings"] = {"steps": dict(STEP_TIMES), "total": round(total, 1), "budget": args.budget}
    write_report(out, report)
    slow = sorted(STEP_TIMES.items(), key=lambda kv: -kv[1])[:4]
    LOG.log(logging.WARNING if total > args.budget else logging.INFO, "time: %.0f s (tier %s, budget %d s)%s - "
            "slowest: %s", total, args.tier, args.budget, " OVER BUDGET" if total > args.budget else "",
            ", ".join("%s %.0f s" % kv for kv in slow))
    problems = bool(floating)
    for label, qa in report.get("qa", {}).items():
        for name, entry in qa["actions"].items():
            if entry["min_z"] < -0.006 or entry["new_overlaps"] or entry["detached_joints"]:
                problems = True
    LOG.info("finished %s", "WITH QA WARNINGS" if problems else "OK")
    return 0


def viewer_config():
    return {
        "slots": [{"id": sl, "label": SLOT_LABELS.get(sl, sl), "variants": list(vs), "accessory": sl in ACCESSORY_SLOTS}
                  for sl, vs in SLOTS.items()],
        "robotTypes": [dict(name=n, **spec) for n, spec in ROBOT_TYPES.items()],
        "skins": [dict(name=n, **{k: list(v) for k, v in pal_.items()}) for n, pal_ in SKINS.items()],
        "expressions": list(make_textures.EXPRESSIONS),
        "displays": list(make_textures.DISPLAYS),
        "eyeGrid": list(make_textures.EYE_GRID),
        "displayGrid": list(make_textures.DISPLAY_GRID),
        "displayCellHeight": make_textures.DISPLAY_CELL[1] / float(make_textures.DISPLAY_ATLAS_H),
        "spinParts": sorted(SPIN_PARTS),
        "lookVersion": LOOK_VERSION,
        "groupLabels": GROUP_LABELS,
    }


def write_config(out, clips, bounds):
    """robot_config.json: slots, variants, robot types, skins, atlas layout, clip data - for Unity and the viewer."""
    cfg = viewer_config()
    cfg["generator"] = VERSION
    cfg["coordinates"] = "character space [right, up, forward] in m (= Unity local space), angles in degrees"
    cfg["atlasCells"] = {
        "eyes": {n: atlas_cell("eyes", i) for i, n in enumerate(make_textures.EXPRESSIONS)},
        "displays": {n: atlas_cell("displays", i) for i, n in enumerate(make_textures.DISPLAYS)},
    }
    cfg["typeBounds"] = bounds
    cfg["lookFormat"] = {"format": LOOK_FORMAT, "version": LOOK_VERSION, "schema": "robot_look.schema.json"}
    cfg["clips"] = clips
    with open(os.path.join(out, "robot_config.json"), "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)


# ---------------------------------- player looks ----------------------------------
# A look is what the player saves. Names of skins, variants, expressions and displays are IDs: never rename them.
LOOK_FORMAT = "CubeEscapeRobotLook"
LOOK_VERSION = 2            # 1 = viewer 1.6 files without format/version fields (migrated on load)
FINISHES = ["Standard", "Matt", "Satin", "Glanz", "Candy", "Chrom"]
LOOK_COLORS = {"armor": "armor", "mech": "mech", "metal": "metal", "lights": "accent", "screen": "screen",
               "eyes": "eye", "visor": "visor", "fabric": "fabric"}     # look key -> skin key


def type_look(tname):
    spec = ROBOT_TYPES[tname]
    variants = default_config()
    variants.update(spec["variants"])
    skin = SKINS[spec["skin"]]
    look = {"format": LOOK_FORMAT, "version": LOOK_VERSION, "name": tname, "variants": variants}
    for k, sk in LOOK_COLORS.items():
        r, g, b = skin[sk]
        look[k] = {"r": round(r, 4), "g": round(g, 4), "b": round(b, 4)}
    look.update({"finish": "Standard", "glow": 1.0, "expression": spec["expression"], "display": spec["display"],
                 "autoExpression": True, "autoDisplay": True})
    return look


def look_schema():
    rgb = {"type": "object", "required": ["r", "g", "b"], "additionalProperties": False,
           "properties": {c: {"type": "number", "minimum": 0, "maximum": 1} for c in "rgb"}}
    variant_props = {sl: {"enum": list(vs)} for sl, vs in SLOTS.items()}
    props = {
        "format": {"const": LOOK_FORMAT},
        "version": {"type": "integer", "minimum": 1, "maximum": LOOK_VERSION},
        "name": {"type": "string", "maxLength": 40},
        "variants": {"type": "object", "properties": variant_props, "additionalProperties": False},
        "finish": {"enum": FINISHES},
        "glow": {"type": "number", "minimum": 0.2, "maximum": 2.5},
        "expression": {"enum": list(make_textures.EXPRESSIONS)},
        "display": {"enum": list(make_textures.DISPLAYS)},
        "autoExpression": {"type": "boolean"},
        "autoDisplay": {"type": "boolean"},
    }
    props.update({k: rgb for k in LOOK_COLORS})
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": "robot_look.schema.json",
            "title": "Cube Escape robot look (version %d)" % LOOK_VERSION,
            "description": "Player customisation. Names are permanent IDs; unknown values fall back to the default "
                           "with a warning. Version 1 files (no format/version) are migrated on load.",
            "type": "object", "required": ["format", "version", "variants", "expression", "display"],
            "properties": props, "additionalProperties": False}


def write_looks(out):
    look_dir = os.path.join(out, "Looks")
    os.makedirs(look_dir, exist_ok=True)
    for tname in ROBOT_TYPES:
        with open(os.path.join(look_dir, "%s.robotlook.json" % tname), "w", encoding="utf-8") as fh:
            json.dump(type_look(tname), fh, indent=2, sort_keys=True)
    with open(os.path.join(out, "robot_look.schema.json"), "w", encoding="utf-8") as fh:
        json.dump(look_schema(), fh, indent=2)


def write_report(out, report):
    path = os.path.join(out, "logs", "robot_report.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, default=str)
    LOG.info("report: %s", path)


if __name__ == "__main__":
    try:
        code = main()
    except Exception:  # noqa: BLE001 - log everything for the CLI user
        LOG.critical("Unhandled exception:\n%s", traceback.format_exc())
        sys.exit(1)
    sys.exit(code)
