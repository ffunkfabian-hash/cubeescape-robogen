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

QA and preview renders are work packages run by worker processes (--workers, --render-workers) and then by the
main process itself; see make_jobs() and JobQueue.

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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import make_textures  # noqa: E402 - ImageMagick source textures
import export_viewer  # noqa: E402 - data for the RobotViewer app

LOG = logging.getLogger("robot")
VERSION = "1.16.0"

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

    def math(self, op, a, b=None, clamp=False, c=None):
        nd = self.node("ShaderNodeMath", operation=op, use_clamp=clamp)
        for i, v in enumerate((a, b, c)):
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


# Skins: every colour of the robot comes from one palette (data/skins.json). The generator composes a BaseColor/Emission
# pair per skin from shared baked layers and bakes masks (colour, badges, glow lines) for free tinting at runtime.
SKIN_FILE = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "skins.json"))
SKIN_FORMAT, SKIN_FORMAT_VERSION = "CubeEscapeRobotSkins", 1
PALETTE_KEYS = ("armor", "mech", "metal", "eye", "screen", "accent", "visor", "fabric", "armor2")
PALETTE_DEFAULTS = {"visor": (0.006, 0.007, 0.010), "fabric": (0.78, 0.10, 0.12)}
# Finishes (IDs are permanent, saved in looks): roughness factor and metalness offset on the armour.
# 1.10: Candy and Chrom with less metal - in the dark game scenes a near-mirror armour read as black.
FINISH_PARAMS = collections.OrderedDict([("Standard", (1.0, 0.0)), ("Matt", (1.9, -0.25)), ("Satin", (1.35, 0.05)),
                                         ("Glanz", (0.45, 0.0)), ("Candy", (0.35, 0.08)), ("Chrom", (0.30, 0.32))])
FINISHES = list(FINISH_PARAMS)
# Badges: painted onto the armour at fixed anchors (decals of make_textures.BADGE_ART or procedural patterns). Each badge
# is one channel of a mask texture: index i -> BADGE_TEXTURES[i // 4], channel "RGBA"[i % 4]. Append only (IDs).
BADGES = ["HazardStripes", "Cross", "Cloud", "Number", "Biohazard", "Glyph", "RacingStripes", "Target", "Star", "Gear",
          "Leaf"]
BADGE_TEXTURES = ["T_Robot_BadgeMask.png", "T_Robot_BadgeMask2.png", "T_Robot_BadgeMask3.png"]
# Glow line patterns: emissive lines (on the armour, "Joints" on ring parts), one channel each of LINE_TEXTURES.
LINE_PATTERNS = ["Circuit", "Rings", "Seams", "Bolt", "Joints"]
LINE_TEXTURES = ["T_Robot_LineMask.png", "T_Robot_LineMask2.png"]
MAX_BADGES = 2
# Paint zones for the second armour colour (armor2): parts by name (without _L/_R). One channel each of ZONE_TEXTURES.
PAINT_ZONES = collections.OrderedDict([
    ("Head", ("Head_Shell", "Dome_Shell", "Boxy_Shell", "TV_Shell")),
    ("Ears", ("Ear_Ring", "Ear_Cap", "Dome_EarDisc", "Boxy_Bolt")),
    ("Chest", ("Chest_Plate", "Abdomen_Plate", "Backpack", "Battery_Cell")),
    ("Pads", ("Shoulder_Pad", "Slim_Pad", "Square_Pad")),
    ("Arms", ("Upper_Arm", "Forearm")),
    ("Hips", ("Hips",)),
    ("Legs", ("Thigh", "KneeCap", "Shin")),
    ("Feet", ("Foot_Shell", "Sneaker_Shell", "Mech_AnklePlate", "Mech_Heel")),
])
ZONE_TEXTURES = ["T_Robot_ZoneMask.png", "T_Robot_ZoneMask2.png"]
# 1.11 Weathering. Surface layers: equalised fields (amount 0..1 = covered share of the painted surface), one channel
# each; Wear (scratches) and Grime are raw fields for the wear amount. Materials: pattern of the armour (1 = armour
# colour, 0 = material colour). Append only (IDs, channel = position).
LAYERS = ["Rust", "Moss", "Frost", "Embers", "Camo", "Wear", "Grime"]
EQUALISED_LAYERS = ["Rust", "Moss", "Frost", "Embers", "Camo"]
LAYER_TEXTURES = ["T_Robot_LayerMask.png", "T_Robot_LayerMask2.png"]
MATERIALS = ["Wood", "Stone", "Sand", "Paper", "Concrete", "Brass", "Circuit"]
MATERIAL_TEXTURES = ["T_Robot_MaterialMask.png", "T_Robot_MaterialMask2.png"]
# defaults per layer/material: colour (sRGB) and surface (roughness factor, metal offset) like the finishes
LAYER_DEFAULTS = collections.OrderedDict([
    ("Rust", {"color": "#8a3a12", "roughness": 1.8, "metalOffset": -0.35}),
    ("Moss", {"color": "#4e6b1d", "roughness": 2.0, "metalOffset": -0.45}),
    ("Frost", {"color": "#cfeaff", "roughness": 0.7, "metalOffset": -0.2}),
    ("Embers", {"color": "#ff5c0a", "roughness": 1.6, "metalOffset": -0.3}),
    ("Camo", {"color": "#56602e", "roughness": 1.5, "metalOffset": -0.25}),
])
# "base" = a fitting armour colour (the comparison sheet and the viewer's material picker use it)
MATERIAL_DEFAULTS = collections.OrderedDict([
    ("Wood", {"color": "#5b3a1f", "base": "#c08a52", "roughness": 1.6, "metalOffset": -0.4}),
    ("Stone", {"color": "#4a4741", "base": "#a49c8c", "roughness": 1.9, "metalOffset": -0.45}),
    ("Sand", {"color": "#8f7449", "base": "#d2b07a", "roughness": 2.0, "metalOffset": -0.45}),
    ("Paper", {"color": "#a8987c", "base": "#e6dcc8", "roughness": 2.0, "metalOffset": -0.45}),
    ("Concrete", {"color": "#5d5b57", "base": "#9a958d", "roughness": 1.9, "metalOffset": -0.45}),
    ("Brass", {"color": "#7a5420", "base": "#c8963a", "roughness": 0.55, "metalOffset": 0.45}),
    ("Circuit", {"color": "#d8a63a", "base": "#1f7a3e", "roughness": 0.9, "metalOffset": -0.2}),
])
WEAR_DEFAULT = 0.5          # the baked look; 0 = clean, 1 = battered
# 1.13 Spectral: special materials are runtime effects on top of the composed skin (nothing extra is baked; without
# the effect the skin shows its palette). Formulas: docs/TECHNIK.md › 11; identical in preview_special_nodes() and the
# viewer shader. color = main colour, color2 = second colour, strength 0..1, speed = animation tempo (1 = default).
# IDs are permanent (append only). "urp" describes the later Unity material (game version Spektrum).
SPECIALS = collections.OrderedDict([
    ("Glass", {"color": "#9ed6f8", "color2": "#e8f8ff", "strength": 1.0, "speed": 0.0, "label": "Glas/Kristall",
               "description": "durchscheinend, Kanten heller (Fresnel), glatt",
               "urp": "Transparent (Alpha 0,35 innen → 0,95 am Rand per Fresnel), Smoothness 0,95, Metallic 0, "
                      "Rim-Emission in Farbe 2; Tiefe zuerst schreiben (Depth Prepass), damit sich Teile nicht "
                      "durchmischen"}),
    ("Hologram", {"color": "#7fa8ff", "color2": "#d8b0ff", "strength": 1.0, "speed": 1.0, "label": "Hologramm",
                  "description": "leuchtend, halbdurchsichtig, wandernde Scanlinien",
                  "urp": "Transparent additiv/Unlit: Emission Farbe × (0,35 + 1,6·Fresnel) × Scanlinien "
                         "(40 Linien je Meter Höhe, laufen mit 0,5·Tempo m/s nach oben), Alpha 0,25 + 0,6·Fresnel"}),
    ("Glitch", {"color": "#ff3df0", "color2": "#30f0ff", "strength": 1.0, "speed": 1.0, "label": "Glitch",
                "description": "zufällige Bänder springen in zwei Signalfarben",
                "urp": "Opak: Höhe in 12 Bänder je Meter, Zufallswert je Band und Takt (6·Tempo Hz); Bänder über "
                       "0,78 nehmen Farbe 1, über 0,9 Farbe 2, beide leuchten"}),
    ("Iridescent", {"color": "#ffffff", "color2": "#ffffff", "strength": 0.85, "speed": 0.0, "label": "Schillern",
                    "description": "Panzerung schillert je nach Blickwinkel in Regenbogenfarben",
                    "urp": "Opak: Farbton = frac(1,2·Fresnel + 0,35·Höhe), Sättigung 0,55, nur auf der Panzerung, "
                           "Helligkeit aus der Grundfarbe; alternativ Thin-Film-Lit"}),
    ("Sketch", {"color": "#efe6dc", "color2": "#2a2622", "strength": 1.0, "speed": 0.0, "label": "Skizze",
                "description": "Papierweiß mit Tuschekontur und Schraffur in dunklen Bereichen",
                "urp": "Unlit: Papierfarbe, Kontur aus Fresnel (0,25–0,5), Schraffur diagonal 45 Linien je Meter "
                       "(Strichanteil 30 %) in Bereichen dunkler als 62 %; optional Outline-Pass"}),
    ("Rainbow", {"color": "#ffffff", "color2": "#ffffff", "strength": 0.9, "speed": 1.0, "label": "Regenbogen",
                 "description": "Regenbogen wandert über die Panzerung (animiert)",
                 "urp": "Opak: Farbton = frac(0,8·Höhe + 0,3·Seite + 0,15·Tempo·Zeit), Sättigung 0,75, nur auf der "
                        "Panzerung, leichte Emission"}),
    ("Galaxy", {"color": "#2e1d6c", "color2": "#f077f8", "strength": 1.0, "speed": 1.0, "label": "Galaxie",
                "description": "Nebel und funkelnde Sterne auf der Panzerung (langsam ziehend)",
                "urp": "Opak: Nebel = 3D-Rauschen (3 Oktaven, Maßstab 3) zwischen Farbe 1 und 2, driftet mit "
                       "0,02·Tempo; Sterne = Zellraster 120/m mit Schwelle 0,985, leuchtend"}),
])
# 1.14 Reshape: body forms. "mesh" forms are geometry of their own (every part object rebuilt: same rig, same UVs on
# the texture atlas, so every skin, mask and badge still fits), "shader" forms a runtime cut-out (formula in
# docs/TECHNIK.md › 15, identical in preview and viewer), "parts" forms extra parts (group Form_<id>). A skin names its
# form; IDs are permanent (append only).
FORMS = collections.OrderedDict([
    ("Faceted", {"kind": "mesh", "label": "Facettiert",
                 "description": "flache, scharfkantige Facetten wie gefaltetes Papier (Origami, Geo)",
                 "urp": "eigene Meshes (Forms/…_Faceted.fbx), flach schattiert; dasselbe Material und dieselben Clips"}),
    ("Voxel", {"kind": "mesh", "label": "Voxel",
               "description": "aus Würfeln von 3 cm gebaut, jede Würfelseite einfarbig (Pixel)",
               "urp": "eigene Meshes (Forms/…_Voxel.fbx); Gelenke, Finger, Kleinteile, Visier und Bildschirme bleiben glatt"}),
    ("Perforated", {"kind": "shader", "label": "Durchbrochen",
                    "description": "runde Löcher in der Panzerung, innen sichtbar (Bone)",
                    "urp": "Alpha-Clip im Body-Shader: Lochraster im UV-Raum (perforation.cells je UV-Einheit, jede "
                           "zweite Reihe um eine halbe Zelle versetzt), Loch bei Abstand zur Zellmitte < "
                           "perforation.radius × Zelle, nur wo ColorMask R > 0,5; Cull Off; derselbe Clip auch im "
                           "ShadowCaster-, DepthOnly- und DepthNormals-Pass (sonst Schatten ohne Löcher und "
                           "Tiefe, wo die Farbe fehlt)"}),
    ("Wired", {"kind": "parts", "label": "Offene Kabel",
               "description": "Kabelstränge an Armen, Beinen, Hals und Rumpf (Wire)",
               "urp": "zusätzliches Teil Form_Wired im modularen FBX bzw. Forms/…_Wired.fbx, starr an den Knochen"}),
])
MESH_FORMS = [f for f, d in FORMS.items() if d["kind"] == "mesh"]
QA_FORMS = ["Form_" + f for f, d in FORMS.items() if d["kind"] in ("mesh", "parts")]   # checked like robot types
PERFORATION = {"spacing": 0.045, "radius": 0.33}    # metres between hole centres, hole radius as share of a cell
GLOW_PARTS = ("Ear_Ring", "Neck_Ring", "Waist_Ring", "Elbow_Band", "Battery_Ring")    # line pattern "Joints"


def part_base(name):
    return name[:-2] if name.endswith(("_L", "_R")) else name


def paint_zone(name):
    """1-based zone index of a part (0 = none)."""
    base = part_base(name)
    for i, parts in enumerate(PAINT_ZONES.values()):
        if base in parts:
            return i + 1
    return 0


def srgb_channel_to_linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def parse_color(v):
    """Linear RGB tuple from a linear [r, g, b] list or an sRGB '#rrggbb' string."""
    if isinstance(v, str):
        if len(v) != 7 or v[0] != "#":
            raise ValueError("colour %r: expected '#rrggbb'" % v)
        return tuple(round(srgb_channel_to_linear(int(v[i:i + 2], 16) / 255.0), 5) for i in (1, 3, 5))
    if len(v) != 3 or not all(0.0 <= float(x) <= 1.0 for x in v):
        raise ValueError("colour %r: expected three values 0..1" % (v,))
    return tuple(float(x) for x in v)


def load_skins(path=SKIN_FILE):
    """data/skins.json -> ordered {id: record} with parsed (linear) colours. Errors name the skin."""
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    if doc.get("format") != SKIN_FORMAT or doc.get("version") != SKIN_FORMAT_VERSION:
        raise ValueError("%s: expected %s version %d" % (path, SKIN_FORMAT, SKIN_FORMAT_VERSION))
    skins = collections.OrderedDict()
    for rec in doc["skins"]:
        sid = rec["id"]
        try:
            if sid in skins:
                raise ValueError("duplicate id")
            pal_ = {k: parse_color(rec["palette"][k]) for k in PALETTE_KEYS if k in rec["palette"]}
            for k in ("armor", "mech", "metal", "eye", "screen", "accent"):
                if k not in pal_:
                    raise ValueError("palette lacks %s" % k)
            for k, v in PALETTE_DEFAULTS.items():
                pal_.setdefault(k, v)
            if rec.get("finish", "Standard") not in FINISH_PARAMS:
                raise ValueError("unknown finish %r" % rec["finish"])
            badges = [{"id": b["id"], "color": parse_color(b["color"])} for b in rec.get("badges", [])]
            if len(badges) > MAX_BADGES or any(b["id"] not in BADGES for b in badges):
                raise ValueError("badges %s (at most %d of %s)" % ([b["id"] for b in badges], MAX_BADGES, BADGES))
            if "armor2" in rec["palette"]:
                pal_["armor2"] = parse_color(rec["palette"]["armor2"])
            zones2 = list(rec.get("zones2", []))
            if any(z not in PAINT_ZONES for z in zones2) or (zones2 and "armor2" not in pal_):
                raise ValueError("zones2 %s need palette.armor2 and zones of %s" % (zones2, list(PAINT_ZONES)))
            material = None
            if rec.get("material"):
                m = rec["material"]
                if m["id"] not in MATERIALS:
                    raise ValueError("material %r" % m)
                material = {"id": m["id"], "color": parse_color(m.get("color", MATERIAL_DEFAULTS[m["id"]]["color"]))}
            layers = []
            for ly in rec.get("layers", []):
                if ly["id"] not in LAYER_DEFAULTS or not 0.0 <= float(ly["amount"]) <= 1.0:
                    raise ValueError("layer %r" % ly)
                layers.append({"id": ly["id"], "amount": float(ly["amount"]),
                               "color": parse_color(ly.get("color", LAYER_DEFAULTS[ly["id"]]["color"]))})
            special = None
            if rec.get("special"):
                sp = rec["special"]
                if sp["id"] not in SPECIALS:
                    raise ValueError("special %r" % sp)
                d = SPECIALS[sp["id"]]
                special = {"id": sp["id"], "color": parse_color(sp.get("color", d["color"])),
                           "color2": parse_color(sp.get("color2", d["color2"])),
                           "strength": float(sp.get("strength", d["strength"])),
                           "speed": float(sp.get("speed", d["speed"]))}
                if not 0.0 <= special["strength"] <= 1.0 or not 0.0 <= special["speed"] <= 4.0:
                    raise ValueError("special %r: strength 0..1, speed 0..4" % sp)
            if rec.get("form", "None") not in ["None"] + list(FORMS):
                raise ValueError("form %r (one of %s)" % (rec["form"], list(FORMS)))
            wear = float(rec.get("wear", WEAR_DEFAULT))
            if not 0.0 <= wear <= 1.0:
                raise ValueError("wear %r" % wear)
            lines = None
            if rec.get("lines"):
                ln = rec["lines"]
                if ln["pattern"] not in LINE_PATTERNS or not 0.0 < float(ln.get("strength", 1.0)) <= 2.0:
                    raise ValueError("lines %r" % ln)
                lines = {"pattern": ln["pattern"], "color": parse_color(ln["color"]),
                         "strength": float(ln.get("strength", 1.0))}
        except (KeyError, ValueError, TypeError) as exc:
            raise ValueError("%s: skin %s: %s" % (path, sid, exc)) from exc
        pal_.setdefault("armor2", pal_["armor"])
        skins[sid] = {"id": sid, "palette": pal_, "zones2": zones2, "finish": rec.get("finish", "Standard"),
                      "material": material, "layers": layers, "wear": wear, "badges": badges, "special": special,
                      "form": None if rec.get("form", "None") == "None" else rec["form"],
                      "lines": lines, "parts": dict(rec.get("parts", {})), "expression": rec.get("expression"),
                      "display": rec.get("display"), "theme": rec.get("theme", ""), "since": rec.get("since", ""),
                      "stage": rec.get("stage", ""), "startSet": bool(rec.get("startSet")),
                      "concept": rec.get("concept", "")}
    return skins


SKIN_DATA = load_skins()
SKINS = collections.OrderedDict((sid, rec["palette"]) for sid, rec in SKIN_DATA.items())   # id -> palette
DEFAULT_SKIN = "Classic"
# colour masks: ColorMask RGB = armour, mechanics, metal; LightMask = lights; visor = 1 - sum (body faces)
MASKS = {"White": (1, 0, 0, 0), "Dark": (0, 1, 0, 0), "Grey": (0, 0, 1, 0), "Visor": (0, 0, 0, 0),
         "Orange": (0, 0, 0, 1), "Fabric": (0, 0, 0, 0)}

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


def decal(k, us, vs, rect, px, extra_mask=None, image="decals"):
    """Projects a region of an ImageMagick atlas (decals or badges) onto the plane (us, vs) inside rect (u0, u1, v0, v1)."""
    u0, u1, v0, v1 = rect
    x, y, w, h = px
    size = float(SRC[image].size[0])
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
    tex.image = SRC[image]
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


VISOR_ROUGHNESS, VISOR_METALLIC = 0.07, 0.15

# Badge anchors: (plane, rect (u0, u1, v0, v1) in m, side mask). Planes: "side" = (y * sign(x), z) on both outer sides
# (reads the same from either side), "front" = (x, z), "back" = (-x, z), each seen from outside.
def badge_anchors():
    return {
        "shoulder": [("side", (-0.07, 0.07, 0.77, 0.91), ("absx>", 0.29))],
        "thigh": [("front", (s * LEG_X - 0.062, s * LEG_X + 0.062, 0.268, 0.392), ("y<", -0.02)) for s in (1, -1)],
        "head_back": [("back", (-0.10, 0.10, 1.06, 1.26), ("y>", 0.12))],
        "back": [("back", (-0.11, 0.11, 0.67, 0.89), ("y>", 0.15))],
        "ear": [("side", (-0.07, 0.09, 1.02, 1.18), ("absx>", 0.24))],        # ear disc (Classic: ring + cap)
        "chest": [("front", (-0.046, 0.046, 0.664, 0.756), ("y<", -0.10))],   # below the LEDs and the display
    }



BADGE_PLACES = {"Cross": ("ear", "chest", "shoulder", "thigh", "back"),
                "Cloud": ("ear", "shoulder", "thigh", "head_back"),
                "Number": ("chest", "shoulder", "thigh", "back"), "Biohazard": ("ear", "chest", "shoulder", "back"),
                "Glyph": ("ear", "shoulder", "back", "head_back"), "Target": ("ear", "shoulder", "thigh", "head_back"),
                "Star": ("ear", "chest", "shoulder", "back"), "Gear": ("ear", "shoulder", "thigh", "back"),
                "Leaf": ("ear", "shoulder", "thigh", "head_back")}


def layer_fields(k, vec, g, sc):
    """Raw fields of the surface layers (baked, then equalised so that amount = covered share). Shared by armour,
    mechanics and metal, so rust and moss also reach joints. Wear = scratches, Grime = grime (raw, for the wear amount)."""
    geo = k.node("ShaderNodeNewGeometry")
    sep = k.node("ShaderNodeSeparateXYZ")
    k.link(geo.outputs["Normal"], sep.inputs[0])
    up = k.math("MAXIMUM", sep.outputs[2], 0.0)
    nmax = k.math("MAXIMUM", k.math("MAXIMUM", k.math("ABSOLUTE", sep.outputs[0]), k.math("ABSOLUTE", sep.outputs[1])),
                  k.math("ABSOLUTE", sep.outputs[2]))
    edge = k.map_range(nmax, 0.95, 0.6)                      # rounded edges and corners
    ao = k.node("ShaderNodeAmbientOcclusion", samples=8)
    ao.inputs["Distance"].default_value = 0.06
    crevice = k.math("SUBTRACT", 1.0, ao.outputs["AO"])
    blotch = k.noise(vec, 4.0, 4.0, 0.6)
    fine = k.noise(vec, 18.0, 3.0, 0.55)

    def blend(*terms):
        acc = None
        for w, t in terms:
            v = k.math("MULTIPLY", t, w)
            acc = v if acc is None else k.math("ADD", acc, v)
        return acc

    return {
        "layer_Rust": blend((0.4, blotch), (0.35, crevice), (0.15, edge), (0.1, g)),
        "layer_Moss": blend((0.45, up), (0.3, crevice), (0.25, blotch)),
        "layer_Frost": blend((0.5, up), (0.3, edge), (0.2, fine)),
        "layer_Embers": box_tex(k, "cracks", vec, 1.3),
        "layer_Camo": box_tex(k, "camo", vec, 0.9),
        "layer_Wear": sc,
        "layer_Grime": g,
    }


def material_fields(k, vec, x, y, z):
    """Armour patterns of the materials: 1 = armour colour, 0 = material colour (object space, baked)."""
    out = {}
    grain = k.node("ShaderNodeTexWave", wave_type="RINGS", rings_direction="SPHERICAL", wave_profile="SIN")
    k.link(k.stretch(vec, 1.0, 1.0, 0.12), grain.inputs["Vector"])
    grain.inputs["Scale"].default_value = 7.0
    grain.inputs["Distortion"].default_value = 7.0
    grain.inputs["Detail"].default_value = 3.0
    out["mat_Wood"] = k.map_range(k.math("ADD", k.math("MULTIPLY", grain.outputs["Fac"], 0.8),
                                         k.math("MULTIPLY", k.noise(k.stretch(vec, 1.0, 1.0, 0.2), 60.0, 2.0), 0.3)),
                                  0.25, 1.0)
    cells = k.node("ShaderNodeTexVoronoi", feature="DISTANCE_TO_EDGE")
    k.link(vec, cells.inputs["Vector"])
    cells.inputs["Scale"].default_value = 8.0
    mortar = k.map_range(cells.outputs["Distance"], 0.0, 0.06)
    carve = k.map_range(k.math("ABSOLUTE", k.math("SUBTRACT", k.math("FRACT", k.math("MULTIPLY", z, 6.0)), 0.5)),
                        0.44, 0.47)                          # engraved bands (Temple)
    speck = k.map_range(k.noise(vec, 45.0, 3.0), 0.3, 0.7, 0.75, 1.0)
    out["mat_Stone"] = k.math("MULTIPLY", k.math("MULTIPLY", mortar, k.math("SUBTRACT", 1.0, k.math("MULTIPLY", carve, 0.7))),
                              speck)
    ripple = k.node("ShaderNodeTexWave", wave_type="BANDS", bands_direction="Z", wave_profile="SIN")
    k.link(vec, ripple.inputs["Vector"])
    ripple.inputs["Scale"].default_value = 9.0
    ripple.inputs["Distortion"].default_value = 3.0
    out["mat_Sand"] = k.math("ADD", k.math("MULTIPLY", k.map_range(k.noise(vec, 160.0, 2.0), 0.3, 0.7), 0.7),
                             k.math("MULTIPLY", ripple.outputs["Fac"], 0.3))
    fibres = k.noise(k.stretch(vec, 1.0, 1.0, 0.08), 70.0, 4.0, 0.7)
    fold = k.node("ShaderNodeTexWave", wave_type="BANDS", bands_direction="DIAGONAL", wave_profile="SAW")
    k.link(vec, fold.inputs["Vector"])
    fold.inputs["Scale"].default_value = 2.5
    out["mat_Paper"] = k.map_range(k.math("SUBTRACT", k.math("ADD", k.math("MULTIPLY", fibres, 0.35), 0.75),
                                          k.math("MULTIPLY", k.map_range(fold.outputs["Fac"], 0.9, 1.0), 0.35)),
                                   0.4, 1.0)
    pores = k.node("ShaderNodeTexVoronoi", feature="F1")
    k.link(vec, pores.inputs["Vector"])
    pores.inputs["Scale"].default_value = 70.0
    out["mat_Concrete"] = k.math("MULTIPLY", k.map_range(pores.outputs["Distance"], 0.05, 0.25),
                                 k.map_range(k.noise(vec, 12.0, 4.0), 0.25, 0.75, 0.55, 1.0))
    out["mat_Brass"] = k.map_range(k.math("ADD", k.math("MULTIPLY", box_tex(k, "brushed", vec, 2.5), 0.5),
                                          k.math("MULTIPLY", k.noise(vec, 6.0, 3.0), 0.5)), 0.3, 0.8, 0.45, 1.0)
    out["mat_Circuit"] = k.math("SUBTRACT", 1.0, k.map_range(box_tex(k, "circuit", vec, 2.2), 0.25, 0.75))
    return out


def pattern_masks(k, vec, x, y, z):
    """Badge and glow line masks of the armour (0..1 scalars, keys "badge_<id>" / "line_<id>"); baked once into mask
    textures, the colours come from the skin (composed per skin, tinted at runtime)."""
    out = {}
    anchors = badge_anchors()

    def inrange(v, lo, hi):
        return k.math("MULTIPLY", k.math("GREATER_THAN", v, lo), k.math("LESS_THAN", v, hi))

    def side(cond):
        kind, val = cond
        if kind == "absx>":
            return k.math("GREATER_THAN", k.math("ABSOLUTE", x), val)
        return k.math("LESS_THAN", y, val) if kind == "y<" else k.math("GREATER_THAN", y, val)

    # each anchor plane is projected once (local 0..1 coordinates and its mask); the badges only offset into the atlas
    # (every node costs about 1 ms to create, so shared projections keep the material build short)
    planes = {}
    for anchor, entries in anchors.items():
        planes[anchor] = []
        for plane, (u0, u1, v0, v1), cond in entries:
            us = {"side": lambda: k.math("MULTIPLY", y, k.math("SIGN", x)), "front": lambda: x,
                  "back": lambda: k.math("MULTIPLY", x, -1.0)}[plane]()
            u = k.math("DIVIDE", k.math("SUBTRACT", us, u0), u1 - u0)
            v = k.math("DIVIDE", k.math("SUBTRACT", z, v0), v1 - v0)
            inside = k.math("MULTIPLY", k.math("MULTIPLY", inrange(u, 0.0, 1.0), inrange(v, 0.0, 1.0)), side(cond))
            planes[anchor].append((u, v, inside))
    c, size = make_textures.BADGE_CELL, float(SRC["badges"].size[0])
    for i, name in enumerate(make_textures.BADGE_ART):
        mask = None
        for anchor in BADGE_PLACES[name]:
            for u, v, inside in planes[anchor]:
                comb = k.node("ShaderNodeCombineXYZ")
                k.link(k.math("MULTIPLY_ADD", u, c / size, c=(i % 4) * c / size), comb.inputs[0])
                k.link(k.math("MULTIPLY_ADD", v, c / size, c=1.0 - ((i // 4) + 1) * c / size), comb.inputs[1])
                tex = k.node("ShaderNodeTexImage", extension="CLIP", interpolation="Cubic")
                tex.image = SRC["badges"]
                k.link(comb.outputs[0], tex.inputs["Vector"])
                m = k.math("MULTIPLY", tex.outputs["Alpha"], inside)
                mask = m if mask is None else k.math("MAXIMUM", mask, m)
        out["badge_" + name] = mask
    ax = k.math("ABSOLUTE", x)
    legx = k.math("ABSOLUTE", k.math("SUBTRACT", ax, LEG_X))
    # hazard stripes: diagonal bands (6 cm period) around the shins and the hips
    stripes = k.math("LESS_THAN", k.math("FRACT", k.math("DIVIDE", k.math("ADD", k.math("ADD", x, y), z), 0.06)), 0.5)
    region = k.math("MAXIMUM", k.math("MULTIPLY", k.math("LESS_THAN", legx, 0.10), inrange(z, 0.09, 0.235)),
                    k.math("MULTIPLY", k.math("LESS_THAN", ax, 0.21), inrange(z, 0.40, 0.545)))
    out["badge_HazardStripes"] = k.math("MULTIPLY", stripes, region)
    # racing stripes: a pair over head, chest and hips, one stripe down each leg
    upper = k.math("MULTIPLY", inrange(ax, 0.022, 0.052), k.math("GREATER_THAN", z, 0.40))
    legs = k.math("MULTIPLY", k.math("LESS_THAN", legx, 0.013), k.math("LESS_THAN", z, 0.40))
    out["badge_RacingStripes"] = k.math("MAXIMUM", upper, legs)
    # glow lines
    out["line_Circuit"] = k.map_range(box_tex(k, "circuit", vec, 1.6), 0.25, 0.75)
    out["line_Bolt"] = k.map_range(box_tex(k, "bolt", vec, 1.4), 0.25, 0.75)
    band = k.math("ABSOLUTE", k.math("SUBTRACT", k.math("FRACT", k.math("ADD", k.math("DIVIDE", z, 0.085), 0.5)), 0.5))
    out["line_Rings"] = k.map_range(k.math("MULTIPLY", band, 0.085), 0.0045, 0.0030)    # 7 mm rings every 8.5 cm
    geo = k.node("ShaderNodeNewGeometry")
    sep = k.node("ShaderNodeSeparateXYZ")
    k.link(geo.outputs["Normal"], sep.inputs[0])
    nmax = k.math("MAXIMUM", k.math("MAXIMUM", k.math("ABSOLUTE", sep.outputs[0]), k.math("ABSOLUTE", sep.outputs[1])),
                  k.math("ABSOLUTE", sep.outputs[2]))
    out["line_Seams"] = k.map_range(nmax, 0.80, 0.72)          # the middle of every rounded edge
    assert set(out) == {"badge_" + b for b in BADGES} | {"line_" + n for n in LINE_PATTERNS if n != "Joints"}
    return out


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
    CHANNELS["White"].update(pattern_masks(k, vec, x, y, z))
    CHANNELS["White"].update(layer_fields(k, vec, g, sc))
    CHANNELS["White"].update(material_fields(k, vec, x, y, z))
    mats["White"] = mat

    # Anthracite mechanics.
    mat, k, b = new_material("Dark")
    vec, x, y, z, g, br, sc = surface(k, 2.2)
    base = k.mix(g, pal(k, "mech", 0.6), pal(k, "mech", 1.1))
    base = k.mix(k.math("MULTIPLY", sc, 0.35), base, pal(k, "metal"))
    rough = k.math("ADD", k.math("MULTIPLY", g, 0.16), 0.36)
    bump(k, b, k.math("SUBTRACT", k.math("MULTIPLY", g, 0.3), k.math("MULTIPLY", sc, 0.5)), 0.3)
    finish_material("Dark", mat, k, b, base, rough, k.value(0.70), k.rgb((0, 0, 0)), viewport=(0.06, 0.065, 0.075))
    CHANNELS["Dark"].update(layer_fields(k, vec, g, sc))
    mats["Dark"] = mat

    # Mid grey metal for hands, ear caps and small mechanics.
    mat, k, b = new_material("Grey")
    vec, x, y, z, g, br, sc = surface(k, 2.6)
    base = k.mix(g, pal(k, "metal", 0.78), pal(k, "metal"))
    base = k.mix(k.math("MULTIPLY", br, 0.25), base, pal(k, "metal", 1.2))
    rough = k.math("ADD", k.math("MULTIPLY", g, 0.10), 0.30)
    bump(k, b, k.math("SUBTRACT", k.math("MULTIPLY", g, 0.2), k.math("MULTIPLY", sc, 0.6)), 0.3)
    finish_material("Grey", mat, k, b, base, rough, k.value(0.60), k.rgb((0, 0, 0)), viewport=(0.34, 0.35, 0.38))
    CHANNELS["Grey"].update(layer_fields(k, vec, g, sc))
    mats["Grey"] = mat

    # Glossy black face visor.
    mat, k, b = new_material("Visor")
    finish_material("Visor", mat, k, b, k.rgb((0.006, 0.007, 0.010)), k.value(VISOR_ROUGHNESS), k.value(VISOR_METALLIC),
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
        CHANNELS[key]["zero"] = kit.value(0.0)
        zone = kit.node("ShaderNodeAttribute", attribute_type="GEOMETRY", attribute_name="paint_zone").outputs["Fac"]
        for i, z in enumerate(PAINT_ZONES):
            CHANNELS[key]["zone_" + z] = kit.math("LESS_THAN", kit.math("ABSOLUTE", kit.math("SUBTRACT", zone, i + 1)),
                                                  0.5)
        glow = kit.node("ShaderNodeAttribute", attribute_type="GEOMETRY", attribute_name="glow_part").outputs["Fac"]
        CHANNELS[key]["line_Joints"] = kit.math("GREATER_THAN", glow, 0.5)
        for ch in MASK_CHANNELS:            # badges and the other glow lines only on the armour
            CHANNELS[key].setdefault(ch, CHANNELS[key]["zero"])
    return mats


MASK_CHANNELS = (["badge_" + b for b in BADGES] + ["line_" + n for n in LINE_PATTERNS] +
                 ["layer_" + n for n in LAYERS] + ["mat_" + n for n in MATERIALS])


EXPRESSION_INDEX = {n: i for i, n in enumerate(make_textures.EXPRESSIONS)}
DISPLAY_STILLS = {"Heartbeat": "Heart", "Spinner": "Loading", "Talk": "Wave"}   # animated motif -> still for renders
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


def flipbook_cell(row, frame):
    """(scale_u, scale_v, offset_u, offset_v) of a flipbook frame; frame f of a motif = offset_u + f * scale_u."""
    (gx, _), (cw, ch), (w, h) = make_textures.FLIPBOOK_GRID, make_textures.FLIPBOOK_CELL, make_textures.FLIPBOOK_SIZE
    su, sv = cw / float(w), ch / float(h)
    return su, sv, frame * su, 1.0 - (row + 1) * sv


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


def tube(name, pts, r, mat, bone, segs=6):
    """Closed tube of radius r along a polyline (cables)."""
    pts = [Vector(p) for p in pts]
    bm = bmesh.new()
    rings = []
    for i, p in enumerate(pts):
        fr = frame_from_dir(pts[min(i + 1, len(pts) - 1)] - pts[max(i - 1, 0)])
        rings.append([bm.verts.new(p + fr @ Vector((r * math.cos(2 * math.pi * j / segs),
                                                    r * math.sin(2 * math.pi * j / segs), 0.0)))
                      for j in range(segs)])
    for r0, r1 in zip(rings, rings[1:]):
        for j in range(segs):
            bm.faces.new((r0[j], r0[(j + 1) % segs], r1[(j + 1) % segs], r1[j]))
    bm.faces.new(list(reversed(rings[0])))
    bm.faces.new(rings[-1])
    return add_part(name, bm, mat, bone)


def bowed(a, b, out, n=6):
    """Points from a to b that bulge by the vector out in the middle (a loose cable)."""
    a, b, out = Vector(a), Vector(b), Vector(out)
    return [a.lerp(b, i / (n - 1)) + out * math.sin(math.pi * i / (n - 1)) for i in range(n)]


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


def deform_part(bm, fn):
    """Applies a world-space deform (e.g. visor_bend) to a part that is already placed."""
    for v in bm.verts:
        v.co = fn(v.co.copy())
    return bm


def ellipse_ring(name, a, b, r, mat, bone, segs=48, rsegs=8):
    """Tube of radius r along an ellipse (half axes a in X, b in Z) around the origin, facing -Y; place it with
    conform_front."""
    bm = bmesh.new()
    rings = []
    for i in range(segs):
        t = 2 * math.pi * i / segs
        c = Vector((a * math.cos(t), 0.0, b * math.sin(t)))
        n = Vector((math.cos(t) / a, 0.0, math.sin(t) / b)).normalized()     # in-plane outward normal
        rings.append([bm.verts.new(c + n * (r * math.cos(2 * math.pi * j / rsegs)) +
                                   Vector((0, r * math.sin(2 * math.pi * j / rsegs), 0))) for j in range(rsegs)])
    for i in range(segs):
        r0, r1 = rings[i], rings[(i + 1) % segs]
        for j in range(rsegs):
            bm.faces.new((r0[j], r0[(j + 1) % rsegs], r1[(j + 1) % rsegs], r1[j]))
    return add_part(name, bm, mat, bone)


def conform_front(bm, target, center_xz, gap=0.010):
    """Moves a part built around y = 0 onto the front surface of another part (ray cast along +Y), so a rim follows
    a rounded shell; gap = how far the part's y = 0 plane stays in front of the surface."""
    src = next(p for p in PARTS if p["name"] == target)
    tree = BVHTree.FromBMesh(src["bm"])
    cx, cz = center_xz
    fallback = min(v.co.y for v in src["bm"].verts)
    for v in bm.verts:
        x, z = v.co.x + cx, v.co.z + cz
        hit = tree.ray_cast(Vector((x, -1.0, z)), Vector((0, 1, 0)))
        y0 = hit[0].y if hit[0] is not None else fallback
        v.co = Vector((x, y0 - gap + v.co.y, z))
    return bm


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


# ------------------------------- 1.14 Reshape: base proportions -------------------------------
# The parts are modelled at the 1.13 measures and then reshaped per bone towards the concept art: stocky, a bigger head,
# chunky plates. Limb joints stay where they are, so every clip, IK target and ground contact keeps working; what hangs
# on the head and the torso (eyes, sockets, spring bones, emitters) moves with the reshape.
#   axes: per-axis scale about a pivot | radial: thicker around the bone line
# Pivots name a joint ("ankle" + side) or are points; "axes" with a joint pivot keeps the given z (feet stay on y = 0).
PROPORTIONS = {
    "Head": ("axes", (1.14, 1.06, 1.24), (0.0, 0.0, 0.935)),        # mostly taller: raised arms and pads pass its sides
    "Spine": ("axes", (1.05, 1.0, 1.0), (0.0, 0.010, 0.730)),       # deeper chests meet the forearms (Jump, EdgeBalance)
    "Pelvis": ("axes", (1.05, 1.0, 1.0), (0.0, 0.005, 0.470)),      # deeper hips meet the shins when crouching
    "UpperArm": ("radial", 1.16, ("shoulder", "dir_upper")),
    "LowerArm": ("radial", 1.20, ("elbow", "dir_fore")),
    "Hand": ("radial", 1.24, ("wrist", "dir_fore")),
    "Thumb": ("radial", 1.10, ("wrist", "dir_fore")),             # sticks out of the fist: meets pads (ClimbUp)
    "UpperLeg": ("axes", (1.18, 1.0, 1.0), ("hip", 0.0)),           # wider only: heels reach the thighs in Run
    "LowerLeg": ("axes", (1.16, 1.0, 1.0), ("knee", 0.0)),
    "Foot": ("axes", (1.05, 1.0, 1.0), ("ankle", 0.0)),           # wide Mech feet meet in turns
}
# variants with their own shape on a reshaped bone (group -> spec, None = keep the 1.13 shape): the pads are chunky
# already and rise beside the head and the thumbs (ClimbUp); round feet would meet the thighs in CrouchWalk
SHAPE_VARIANTS = {"Pads_Round": None, "Pads_Slim": None, "Pads_Square": None, "Feet_Round": None,
                  # the wide box head keeps its width (Sleep, Death: it rests on the square pads), grows in height
                  "Head_Boxy": ("axes", (1.04, 1.06, 1.24), (0.0, 0.0, 0.935))}
# bones that take the shape of another bone (sockets, spring bones, fingers); None = keep the 1.13 shape
SHAPE_OF = {"Eyes": "Head", "Socket_Head": "Head", "Antenna": "Head", "Socket_Neck": "Spine", "Socket_Back": "Spine",
            "Cape": "Spine", "Fingers": "Hand"}
_SHAPES = {}


def shape_matrix(bone, group=None):
    """4x4 reshape matrix of a bone (identity for bones and variants that keep their 1.13 shape)."""
    key = (bone, group if group in SHAPE_VARIANTS else None)
    if key in _SHAPES:
        return _SHAPES[key]
    base, side = bone, ""
    if bone[-2:] in ("_L", "_R"):
        base, side = bone[:-2], bone[-1]
    spec = SHAPE_VARIANTS[group] if key[1] else PROPORTIONS.get(SHAPE_OF.get(base, base))
    m = Matrix.Identity(4)
    if spec:
        kind, k, piv = spec
        if kind == "radial":
            a = J["%s_%s" % (piv[0], side)]
            d = (J["%s_%s" % (piv[1], side)] if piv[1] else Vector((0, 0, 1))).normalized()
            dd = Matrix([[d[i] * d[j] for j in range(3)] for i in range(3)])
            s3 = Matrix.Identity(3) * k + dd * (1 - k)
        else:
            if isinstance(piv[0], str):
                j = J["%s_%s" % (piv[0], side)]
                a = Vector((j.x, j.y, piv[1]))
            else:
                a = Vector(piv)
            s3 = Matrix.Diagonal(k)
        m = Matrix.Translation(a) @ s3.to_4x4() @ Matrix.Translation(-a)
    _SHAPES[key] = m
    return m


def shaped(bone, co):
    return (shape_matrix(bone) @ Vector(co).to_4d()).to_3d()


def apply_proportions():
    """Reshapes every part with the matrix of its bone and moves what rides on the head and torso along."""
    _SHAPES.clear()
    ident = Matrix.Identity(4)
    for p in PARTS:
        m = shape_matrix(p["bone"], p["group"])
        if m != ident:
            bmesh.ops.transform(p["bm"], matrix=m, verts=p["bm"].verts[:])
    for e in EMITTERS:
        e["position"] = tuple(shaped(e["bone"], e["position"]))
    J["head_top"] = shaped("Head", J["head_top"])
    J["eyes"] = shaped("Head", J["eyes"])


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
               "AccHand": "In der Hand", "AccEar": "Ohren"}
ACCESSORY_SLOTS = ("AccHead", "AccFace", "AccNeck", "AccBack", "AccHand", "AccEar")
SPIN_PARTS = set()              # part names that spin in the viewer (propeller)
FLOATING_PARTS = {"Halo_Ring"}  # parts that float on purpose (rigid on their socket); skipped by the connectivity check
EMITTERS = []                   # particle emitters of accessories (smoke ...), exported for the viewer and the game
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
                   "expression": "Skeptical", "display": "Clock", "emote": "HatTip"}),
    ("Punk", {"variants": {"Pads": "Square", "AccHead": "Mohawk", "AccFace": "Shades", "AccNeck": "Bell"},
              "skin": "Toxic", "expression": "Determined", "display": "Lightning"}),
    ("King", {"variants": {"Head": "Dome", "AccHead": "Crown", "AccNeck": "Medal", "AccBack": "Cape",
                           "AccHand": "Flag"}, "skin": "Gold", "expression": "Happy", "display": "Star",
              "emote": "FlagWave"}),
    ("DJ", {"variants": {"Head": "Boxy", "Feet": "Round", "AccHead": "Headphones", "AccBack": "Wings"},
            "skin": "Vapor", "expression": "Laugh", "display": "Music"}),
    ("Gardener", {"variants": {"AccFace": "Mustache", "AccHand": "Flower"}, "skin": "Forest", "expression": "Shy",
                  "display": "Heart"}),
    ("Mechanic", {"variants": {"Backpack": "Battery", "AccFace": "Goggles", "AccHand": "Wrench"}, "skin": "Sand",
                  "expression": "Focused", "display": "Warning"}),
    # narrowest silhouette: fits the game's character capsule (radius 0.3 m)
    ("Runner", {"variants": {"Head": "Dome", "Pads": "Slim", "Feet": "Round"}, "skin": "Ocean",
                "expression": "Determined", "display": "Lightning"}),
    # 1.12 Bolt-On: together these carry every attachment, so the QA of all types covers each one in every clip
    ("Miner", {"variants": {"Hands": "Claw", "Feet": "Mech", "AccHead": "MinerHelmet"}, "skin": "Miner",
               "expression": "Focused", "display": "Warning"}),
    ("Samurai", {"variants": {"Pads": "Square", "AccHead": "Kabuto"}, "skin": "Samurai", "expression": "Determined",
                 "display": "Lightning"}),
    ("Steam", {"variants": {"AccHead": "Chimney", "AccFace": "Goggles", "AccEar": "Dial"}, "skin": "Steam",
               "expression": "Normal", "display": "Clock"}),
    ("Signal", {"variants": {"AccHead": "Dish", "AccEar": "LensEye"}, "skin": "Signal", "expression": "Focused",
                "display": "Wave"}),
    ("Celest", {"variants": {"AccHead": "Halo", "AccBack": "Wings"}, "skin": "Celest", "expression": "Happy",
                "display": "Star"}),
    ("Sapling", {"variants": {"Feet": "Round", "AccHead": "Sprout"}, "skin": "Wood", "expression": "Happy",
                 "display": "Heart"}),
    ("Astronaut", {"variants": {"Feet": "Mech", "AccHead": "Antenna", "AccFace": "SpaceRing", "AccBack": "Pouches"},
                   "skin": "Astronaut", "expression": "Normal", "display": "Status"}),
    ("Clockwork", {"variants": {"AccFace": "LensRims", "AccEar": "Gears"}, "skin": "Clockwork",
                   "expression": "Skeptical", "display": "Clock"}),
    ("Vertex", {"variants": {"Pads": "Slim", "AccHead": "Ears"}, "skin": "Vertex", "expression": "Happy",
                "display": "Cube"}),
    ("Spike", {"variants": {"Pads": "Square", "Hands": "Claw", "AccHead": "Spikes"}, "skin": "Spike",
               "expression": "Angry", "display": "Warning"}),
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


class form_parts:
    """with form_parts("Wired"): parts of a "parts" form (group Form_<id>; not a slot, the look's form shows them)."""

    def __init__(self, name):
        self.name = name

    def __enter__(self):
        VARIANT[:] = ["Form", self.name]
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

    # ================= 1.14 seams and bolts of the concept art, on the core plates (every type) =================
    # built around y = 0 and laid onto the plate's front (conform_front), half sunk in so they stay connected
    def front_detail(name, bm, target, cx, cz, mat, bone):
        conform_front(bm, target, (cx, cz), gap=0.0)
        add_part(name, bm, mat, bone)

    def strip(w, h, d=0.006):
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=(w, d, h), verts=bm.verts[:])
        for i in range(1, 8):           # cuts, so the strip bends with the rounded plate
            geom = bm.verts[:] + bm.edges[:] + bm.faces[:]
            bmesh.ops.bisect_plane(bm, geom=geom, plane_co=(w * (i / 8 - 0.5), 0, 0), plane_no=(1, 0, 0))
        return bm

    def bolt(r=0.011, d=0.008):
        bm = bmesh.new()
        bmesh.ops.create_cone(bm, cap_ends=True, segments=8, radius1=r, radius2=r * 0.8, depth=d)
        bmesh.ops.rotate(bm, verts=bm.verts[:], cent=(0, 0, 0), matrix=Matrix.Rotation(math.radians(90), 3, "X"))
        return bm

    for s, side in ((1, "L"), (-1, "R")):
        x0 = s * LEG_X
        front_detail("Shin_Seam_" + side, strip(0.120, 0.008), "Shin_" + side, x0, 0.180, D, "LowerLeg_" + side)
        front_detail("Thigh_Seam_" + side, strip(0.110, 0.008), "Thigh_" + side, x0, 0.315, D, "UpperLeg_" + side)
        for i, dx in enumerate((-0.030, 0.030)):
            front_detail("Shin_Bolt_%d_%s" % (i, side), bolt(), "Shin_" + side, x0 + dx, 0.142, G, "LowerLeg_" + side)
        front_detail("Hips_Bolt_" + side, bolt(), "Hips", s * 0.120, 0.470, G, "Pelvis")
        front_detail("Chest_Bolt_" + side, bolt(), "Chest_Plate", s * 0.165, 0.770, G, "Spine")

    # ================= 1.14 form "Wired": open cable strands (rigid per bone, they never cross a joint) =================
    with form_parts("Wired"):
        for s, side in ((1, "L"), (-1, "R")):
            sh, el, wr = J["shoulder_" + side], J["elbow_" + side], J["wrist_" + side]
            d1, d2 = J["dir_upper_" + side], J["dir_fore_" + side]
            out, back = Vector((s, 0, 0)), Vector((0, 1, 0))       # outside and back of the arm
            for i, (off, mat) in enumerate(((out * 0.054, O), ((out + back).normalized() * 0.054, D))):
                tube("Wire_UpperArm_%d_%s" % (i, side), bowed(sh + d1 * 0.075 + off, el - d1 * 0.03 + off,
                                                             off.normalized() * 0.012), 0.008, mat, "UpperArm_" + side)
                tube("Wire_Forearm_%d_%s" % (i, side), bowed(el + d2 * 0.05 + off * 1.15, wr - d2 * 0.03 + off,
                                                            off.normalized() * 0.012), 0.008, mat, "LowerArm_" + side)
            x0 = s * LEG_X
            for i, (dx, dy, mat) in enumerate(((0.068, -0.010, O), (0.052, -0.052, D))):
                tube("Wire_Thigh_%d_%s" % (i, side), bowed((x0 + s * dx, dy, 0.375), (x0 + s * dx, dy, 0.285),
                                                          (s * 0.012, 0, 0)), 0.008, mat, "UpperLeg_" + side)
                tube("Wire_Shin_%d_%s" % (i, side), bowed((x0 + s * dx, dy, 0.205), (x0 + s * dx, dy, 0.125),
                                                         (s * 0.012, 0, 0)), 0.008, mat, "LowerLeg_" + side)
            tube("Wire_Neck_" + side, bowed((s * 0.072, 0.035, 0.955), (s * 0.090, 0.035, 0.900), (s * 0.010, 0.0, 0)),
                 0.009, O if s > 0 else D, "Head")
            tube("Wire_Belly_" + side, bowed((s * 0.120, -0.140, 0.690), (s * 0.105, -0.115, 0.585), (0, -0.016, 0)),
                 0.009, D if s > 0 else O, "Spine")

    # ================= accessories (on socket bones) =================
    with variant("AccHead", "Antenna"):
        cylinder("Antenna_Base", 0.032, 0.050, (0, 0.0, 1.300), D, "Socket_Head", segs=14, bev=0.008)
        cylinder("Antenna_Rod", 0.007, 0.160, (0, 0.0, 1.400), G, "Antenna", segs=8)
        sphere("Antenna_Tip", 0.022, (0, 0.0, 1.482), O, "Antenna", segs=12, rings=7)
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
        add_part("Cape_Cloth", bm, F, "Cape")
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

    # ================= 1.12 Bolt-On: attachments =================
    # head tops (Socket_Head; the Classic shell is flat up to |x| 0.12 at z 1.303, rounded with r 0.14 beyond)
    with variant("AccHead", "MinerHelmet"):
        # the shell stays above the eye screen (z <= 1.22 at its front), which moves up when the robot looks up
        sphere("Miner_Shell", 0.290, (0, 0.010, 1.275), W, "Socket_Head", segs=24, rings=12, scale=(1.0, 0.88, 0.36))
        cylinder("Miner_Brim", 0.300, 0.016, (0, 0.010, 1.278), D, "Socket_Head", segs=28, bev=0.005,
                 scale=(1.0, 0.90, 1.0))
        rbox("Miner_Ridge", (0.050, 0.30, 0.030), (0, 0.035, 1.372), D, "Socket_Head", r=0.012, segs=1,
             rot=(-6, 0, 0))
        lamp_dir = Vector((0, -1.0, 0.30)).normalized()
        lamp = Vector((0, -0.224, 1.318))
        rbox("Miner_LampMount", (0.070, 0.030, 0.050), lamp - lamp_dir * 0.004, D, "Socket_Head", r=0.010, segs=1,
             rot=frame_from_dir(lamp_dir))
        cylinder("Miner_LampHousing", 0.040, 0.050, lamp + lamp_dir * 0.028, G, "Socket_Head", axis=lamp_dir,
                 segs=18, bev=0.008)
        cylinder("Miner_LampGlass", 0.031, 0.010, lamp + lamp_dir * 0.054, O, "Socket_Head", axis=lamp_dir, segs=18)
    with variant("AccHead", "Kabuto"):
        sphere("Kabuto_Bowl", 0.285, (0, 0.010, 1.240), W, "Socket_Head", segs=24, rings=12, scale=(1.0, 0.90, 0.44))
        torus("Kabuto_Rim", 0.272, 0.014, (0, 0.010, 1.240), D, "Socket_Head", segs=28, rsegs=6)
        for s in (1, -1):
            sd = "L" if s > 0 else "R"
            rbox("Kabuto_Guard_" + sd, (0.022, 0.300, 0.110), (s * 0.282, 0.050, 1.190), D, "Socket_Head",
                 r=0.008, segs=1, rot=(0, s * 24, 0))
            rbox("Kabuto_Wing_" + sd, (0.020, 0.075, 0.095), (s * 0.238, -0.140, 1.228), W, "Socket_Head",
                 r=0.008, segs=1, rot=(0, s * 30, s * 28))
            horn = [(0.0, 0.0), (0.028, 0.0), (0.150, 0.215), (0.122, 0.222)]
            prism("Kabuto_Horn_" + sd, [(s * u, v) for u, v in (horn if s > 0 else list(reversed(horn)))], 0.012,
                  "Y", (s * 0.030, -0.232, 1.300), O, "Socket_Head", r=0.003, segs=1)
        rbox("Kabuto_Neckguard", (0.420, 0.024, 0.115), (0, 0.235, 1.185), D, "Socket_Head", r=0.008, segs=1,
             rot=(-24, 0, 0))
        cylinder("Kabuto_Crest", 0.036, 0.016, (0, -0.236, 1.306), O, "Socket_Head", axis=(0, -1, 0), segs=16,
                 bev=0.004)
    with variant("AccHead", "Spikes"):
        spikes = (((0.0, 0.0, 1.296), (0.0, 0.0, 1.0)),
                  ((0.155, 0.0, 1.282), (0.55, 0.0, 0.84)), ((-0.155, 0.0, 1.282), (-0.55, 0.0, 0.84)),
                  ((0.0, 0.130, 1.282), (0.0, 0.55, 0.84)), ((0.0, -0.115, 1.290), (0.0, -0.35, 0.94)),
                  ((0.110, 0.110, 1.280), (0.42, 0.42, 0.80)), ((-0.110, 0.110, 1.280), (-0.42, 0.42, 0.80)))
        for i, (base, d) in enumerate(spikes):
            d = Vector(d).normalized()
            b = Vector(base)
            cylinder("Spike_Base_%d" % i, 0.040, 0.020, b, D, "Socket_Head", axis=d, segs=12, bev=0.005)
            cylinder("Spike_%d" % i, 0.032, 0.105 if i else 0.125, b + d * (0.062 if i else 0.072), G, "Socket_Head",
                     axis=d, segs=10, r2=0.002)
    with variant("AccHead", "Ears"):
        for s in (1, -1):
            sd = "L" if s > 0 else "R"
            outer = [(0.0, 0.0), (0.120, 0.0), (0.085, 0.165)]
            inner = [(0.026, 0.020), (0.094, 0.020), (0.074, 0.112)]
            for nm, pts, depth, y, mat in (("Ear_Fin_", outer, 0.050, 0.020, W),
                                           ("Ear_Inner_", inner, 0.012, -0.010, O)):
                pts = [(s * (u - 0.060), v) for u, v in pts]
                prism(nm + sd, pts if s > 0 else list(reversed(pts)), depth, "Y", (s * 0.130, y, 1.268), mat,
                      "Socket_Head", r=0.006 if mat == W else 0.002, segs=1, rot=(0, s * 14, 0))
    with variant("AccHead", "Dish"):
        cylinder("Dish_Mast", 0.012, 0.130, (0.120, 0.060, 1.355), D, "Socket_Head", segs=10)
        cylinder("Dish_Foot", 0.034, 0.020, (0.120, 0.060, 1.298), G, "Socket_Head", segs=14, bev=0.005)
        dd = Vector((0.35, -0.45, 0.82)).normalized()
        dc = Vector((0.120, 0.060, 1.440))
        sphere("Dish_Joint", 0.020, dc - dd * 0.012, G, "Socket_Head", segs=12, rings=7)
        cylinder("Dish_Bowl", 0.040, 0.050, dc + dd * 0.020, W, "Socket_Head", axis=dd, segs=24, r2=0.120,
                 bev=0.006)
        cylinder("Dish_Face", 0.106, 0.006, dc + dd * 0.046, D, "Socket_Head", axis=dd, segs=24)
        cyl_between("Dish_Feed", dc + dd * 0.046, dc + dd * 0.125, 0.006, G, "Socket_Head", segs=8)
        sphere("Dish_Tip", 0.014, dc + dd * 0.130, O, "Socket_Head", segs=10, rings=6)
    with variant("AccHead", "Chimney"):
        cx, cy = 0.115, 0.055
        cylinder("Chimney_Foot", 0.058, 0.026, (cx, cy, 1.296), D, "Socket_Head", segs=16, bev=0.006)
        cylinder("Chimney_Stack", 0.038, 0.170, (cx, cy, 1.390), G, "Socket_Head", segs=16, r2=0.034)
        cylinder("Chimney_Band", 0.041, 0.018, (cx, cy, 1.360), D, "Socket_Head", segs=16, bev=0.004)
        cylinder("Chimney_Top", 0.034, 0.030, (cx, cy, 1.482), G, "Socket_Head", segs=16, r2=0.052, bev=0.004)
        cylinder("Chimney_Glow", 0.030, 0.006, (cx, cy, 1.496), O, "Socket_Head", segs=16)
        EMITTERS.append({"id": "ChimneySmoke", "kind": "smoke", "slot": "AccHead", "variant": "Chimney",
                         "bone": "Socket_Head", "position": (cx, cy, 1.505), "direction": (0.0, 0.0, 1.0)})
    with variant("AccHead", "Halo"):
        # floats above the head (FLOATING_PARTS): rigid on the socket, so it follows every clip
        torus("Halo_Ring", 0.150, 0.013, (0, 0.020, 1.440), O, "Socket_Head", axis=(0, 0.26, 1.0), segs=32, rsegs=8)
    with variant("AccHead", "Sprout"):
        stem_a, stem_b = Vector((0.0, 0.020, 1.296)), Vector((0.012, 0.030, 1.415))
        cylinder("Sprout_Pot", 0.034, 0.020, stem_a, D, "Socket_Head", segs=14, bev=0.005)
        cyl_between("Sprout_Stem", stem_a, stem_b, 0.009, D, "Socket_Head", segs=8)
        for s in (1, -1):
            sd = "L" if s > 0 else "R"
            prism("Sprout_Leaf_" + sd, ellipse(0.120, 0.050, 16), 0.010, "Y",
                  stem_b + Vector((s * 0.058, 0.0, 0.020)), F, "Socket_Head", r=0.003, segs=1, rot=(0, -s * 28, 0))
        sphere("Sprout_Bud", 0.016, stem_b + Vector((0, 0, 0.010)), O, "Socket_Head", segs=10, rings=6)

    # ear pieces (Head; Classic ear cap ends at |x| 0.311). A stub from inside the shell keeps them mounted on every
    # head variant. Only on the left ear: the right hand passes the right ear within 1 cm (Wave, Think).
    with variant("AccEar", "LensEye"):
        cyl_between("Lens_Stub", (0.200, 0.010, 1.100), (0.310, 0.010, 1.100), 0.040, D, "Head", segs=14)
        cylinder("Lens_Housing", 0.078, 0.070, (0.345, 0.010, 1.100), D, "Head", axis=(1, 0, 0), segs=24, bev=0.010)
        cylinder("Lens_Ring", 0.070, 0.016, (0.384, 0.010, 1.100), G, "Head", axis=(1, 0, 0), segs=24, bev=0.004)
        cylinder("Lens_Glass", 0.056, 0.012, (0.388, 0.010, 1.100), V, "Head", axis=(1, 0, 0), segs=24)
        cylinder("Lens_Iris", 0.022, 0.006, (0.395, 0.010, 1.100), O, "Head", axis=(1, 0, 0), segs=16)
    with variant("AccEar", "Gears"):
        for s in (1,):
            sd = "L" if s > 0 else "R"
            cyl_between("Gear_Stub_" + sd, (s * 0.200, 0.010, 1.100), (s * 0.312, 0.010, 1.100), 0.030, D, "Head",
                        segs=12)
            for k, (c, rad, n, th) in enumerate((((s * 0.322, 0.010, 1.100), 0.064, 10, 0.020),
                                                 ((s * 0.312, 0.085, 1.175), 0.036, 7, 0.016))):
                c = Vector(c)
                cylinder("Gear_Disc_%d_%s" % (k, sd), rad, th, c, G, "Head", axis=(1, 0, 0), segs=20, bev=0.003)
                cylinder("Gear_Hub_%d_%s" % (k, sd), rad * 0.32, th + 0.010, c, D, "Head", axis=(1, 0, 0), segs=12)
                for t in range(n):
                    a = 2 * math.pi * (t + 0.5 * k) / n
                    off = Vector((0.0, math.cos(a), math.sin(a)))
                    rbox("Gear_Tooth_%d_%d_%s" % (k, t, sd), (th, 0.020, 0.024), c + off * (rad + 0.008), G, "Head",
                         r=0.003, segs=1, rot=(math.degrees(a) - 90, 0, 0))
    with variant("AccEar", "Dial"):
        for s in (1,):
            sd = "L" if s > 0 else "R"
            cyl_between("Dial_Stub_" + sd, (s * 0.200, 0.010, 1.100), (s * 0.312, 0.010, 1.100), 0.030, D, "Head",
                        segs=12)
            c = Vector((s * 0.322, 0.010, 1.100))
            cylinder("Dial_Rim_" + sd, 0.080, 0.024, c, G, "Head", axis=(1, 0, 0), segs=28, bev=0.005)
            cylinder("Dial_Face_" + sd, 0.068, 0.006, c + Vector((s * 0.012, 0, 0)), W, "Head", axis=(1, 0, 0),
                     segs=28)
            for t in range(4):
                a = math.pi / 2 * t
                off = Vector((0.0, math.cos(a), math.sin(a)))
                rbox("Dial_Tick_%d_%s" % (t, sd), (0.006, 0.010, 0.014), c + Vector((s * 0.016, 0, 0)) + off * 0.054,
                     D, "Head", r=0.002, segs=1, rot=(math.degrees(a) - 90, 0, 0))
            for k, (ln, ang) in enumerate(((0.048, 20), (0.034, 120))):
                a = math.radians(ang)
                off = Vector((0.0, math.cos(a), math.sin(a)))
                rbox("Dial_Hand_%d_%s" % (k, sd), (0.006, 0.010, ln), c + Vector((s * 0.018, 0, 0)) + off * ln / 2,
                     D, "Head", r=0.002, segs=1, rot=(ang - 90, 0, 0))
            cylinder("Dial_Pin_" + sd, 0.008, 0.012, c + Vector((s * 0.020, 0, 0)), O, "Head", axis=(1, 0, 0),
                     segs=10)

    # face rims (Head)
    with variant("AccFace", "LensRims"):
        for s in (1, -1):
            sd = "L" if s > 0 else "R"
            deform_part(torus("LensRim_" + sd, 0.064, 0.013, (s * EYE_X, -0.226, EYE_Z), G, "Head", axis=(0, -1, 0),
                              segs=28, rsegs=6), visor_bend)
        rbox("LensRim_Bridge", (0.040, 0.014, 0.018), (0, -0.228, EYE_Z + 0.012), G, "Head", r=0.005, segs=1)
    with variant("AccFace", "SpaceRing"):
        conform_front(ellipse_ring("SpaceRing_Bezel", 0.236, 0.126, 0.012, D, "Head"), "Head_Shell", (0, 1.112),
                      gap=0.005)
        for s in (1, -1):
            cylinder("SpaceRing_Light_" + ("L" if s > 0 else "R"), 0.016, 0.040, (s * 0.262, -0.120, 1.205), O, "Head",
                     axis=(s * 0.5, -0.6, 0.5), segs=12, bev=0.004)

    # back (Socket_Back, behind the Classic backpack that ends at y 0.22)
    with variant("AccBack", "Pouches"):
        # low enough that the head never reaches it (crouching tilts the head back over the shoulders)
        rbox("Pack_Body", (0.250, 0.100, 0.200), (0, 0.262, 0.735), F, "Socket_Back", r=0.030, segs=2)
        rbox("Pack_Flap", (0.256, 0.070, 0.050), (0, 0.268, 0.822), D, "Socket_Back", r=0.016, segs=1)
        for s in (1, -1):
            sd = "L" if s > 0 else "R"
            rbox("Pack_Pouch_" + sd, (0.090, 0.050, 0.090), (s * 0.062, 0.330, 0.700), F, "Socket_Back", r=0.016,
                 segs=1)
            rbox("Pack_PouchFlap_" + sd, (0.094, 0.030, 0.034), (s * 0.062, 0.340, 0.745), D, "Socket_Back",
                 r=0.008, segs=1)
            rbox("Pack_Strap_" + sd, (0.030, 0.012, 0.190), (s * 0.090, 0.315, 0.735), D, "Socket_Back", r=0.004,
                 segs=1)
        cylinder("Pack_Roll", 0.040, 0.270, (0, 0.262, 0.602), W, "Socket_Back", axis=(1, 0, 0), segs=16, bev=0.010)

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
              "Socket_Head", "Socket_Neck", "Socket_Back", "Prop_L", "Prop_R", "Cape", "Antenna"]
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
        if p["name"] in FLOATING_PARTS:
            continue
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
    order = ["Body"] + [group_key(sl, v) for sl, vs in SLOTS.items() for v in vs] + \
        [group_key("Form", f) for f, d in FORMS.items() if d["kind"] == "parts"]
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
        zone_layer = final.faces.layers.int.new("paint_zone")
        glow_layer = final.faces.layers.int.new("glow_part")
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
            zone, glow = paint_zone(p["name"]), int(part_base(p["name"]) in GLOW_PARTS)
            for f in final.faces[n_f:]:
                f.material_index = mi
                f[part_layer] = len(names)
                f[zone_layer] = zone
                f[glow_layer] = glow
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


UV_SCHEMA = "1.15-a"    # bump when unwrap() changes
UV_MARGIN = 0.002       # gap between packed islands, share of the atlas (4 px at 2048, 2 px in the 1024 Unity masks)


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


def unwrap(objs, cache_dir=None, read=True, require=False):
    """One shared atlas for every part object (body materials only); eye screen and display keep fixed UVs.
    Blender's island packing is not deterministic, so with cache_dir the UVs of unchanged geometry are reused:
    stable textures between runs, and the bake cache can hit. The cache is always written (read=False only skips
    reading it): render workers take the main process's UVs from there (require: never unwrap). Returns True on a hit."""
    key = geometry_key(objs)
    cache_path = os.path.join(cache_dir, "uv_%s.npz" % key[:20]) if cache_dir else None
    if cache_path and (read or require) and os.path.exists(cache_path):
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
                return True
    if require:
        return False
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
    return False


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
    bpy.ops.uv.smart_project(angle_limit=math.radians(60), island_margin=UV_MARGIN, area_weight=0.0,
                             correct_aspect=True, scale_to_bounds=False)
    # 1.15: smart project leaves wide gaps between its thousands of small islands (19 % of the atlas used); the
    # concave packer with free rotation fills the atlas far more densely - more texels per centimetre in every map
    t0 = time.time()
    bpy.ops.uv.pack_islands(rotate=True, rotate_method="ANY", scale=True, margin_method="FRACTION",
                            margin=UV_MARGIN, shape_method="CONCAVE")
    LOG.info("  packed UV islands in %.1f s", time.time() - t0)
    bpy.ops.object.mode_set(mode="OBJECT")
    eye_i, disp_i = MAT_ORDER.index("EyeScreen"), MAT_ORDER.index("Display")
    x0, x1, z0, z1 = DISPLAY_RECT
    eye_unshape, disp_unshape = shape_matrix("Eyes").inverted(), shape_matrix("Spine").inverted()
    for o in objs:
        me = o.data
        me.uv_layers[0].name = "UVMap"
        uv = me.uv_layers[0].data
        for p in me.polygons:
            if p.material_index not in (eye_i, disp_i):
                continue
            unshape = eye_unshape if p.material_index == eye_i else disp_unshape     # UVs in the 1.13 frame
            for li in p.loop_indices:
                co = unshape @ me.vertices[me.loops[li].vertex_index].co
                if p.material_index == eye_i:
                    uv[li].uv = ((co.x + EYE_SCREEN_W / 2) / EYE_SCREEN_W, (co.z - (EYE_Z - EYE_SCREEN_H / 2)) / EYE_SCREEN_H)
                else:
                    uv[li].uv = ((co.x - x0) / (x1 - x0), (co.z - z0) / (z1 - z0))


def tri_count(me):
    return sum(len(p.vertices) - 2 for p in me.polygons)


# ----------------------------------------------------------------------------------------------
# Texture baking
# ----------------------------------------------------------------------------------------------


def halve(a):
    """2x2 box filter (h, w, c)."""
    h, w = a.shape[:2]
    return a.reshape(h // 2, 2, w // 2, 2, -1).mean(axis=(1, 3))


def unity_textures(results, layers, metal, smooth):
    """The packed Unity masks (UNITY_TEXTURES) as {name: (n, n, 4) float array}, n <= UNITY_TEXTURE_MAX."""
    def shrink(a):
        a = a if a.ndim == 3 else a[..., None]
        while a.shape[0] > UNITY_TEXTURE_MAX:
            a = halve(a)
        return a

    def rgba(*planes):
        n = planes[0].shape[0]
        out = np.zeros((n, n, 4), np.float32)
        for i, pl in enumerate(planes):
            if pl is not None:
                out[..., i] = pl[..., 0] if pl.ndim == 3 else pl
        return out

    def chans(kind, start):
        a = shrink(layers[kind])
        return [a[..., i] if i < a.shape[-1] else None for i in range(start, start + 4)]

    cm = shrink(np.clip(results["mask"][..., :3], 0, 1))
    zones = shrink(layers["zone_masks"])
    zone_id = np.where(zones.max(axis=-1) > 0.5, (zones.argmax(axis=-1) + 1) / 8.0, 0.0)
    lay = shrink(layers["layer_masks"])
    lines = shrink(layers["line_masks"])
    out = {
        "T_Robot_U_ColorLight.png": rgba(cm[..., 0], cm[..., 1], cm[..., 2], shrink(np.clip(results["mask_a"][..., :1], 0, 1))),
        "T_Robot_U_MetalWear.png": rgba(shrink(metal), lay[..., LAYERS.index("Wear")], lay[..., LAYERS.index("Grime")],
                                        shrink(smooth)),
        "T_Robot_U_Aux.png": rgba(lines[..., LINE_PATTERNS.index("Joints")], zone_id, lay[..., LAYERS.index("Camo")],
                                  None),
        "T_Robot_U_Layers.png": rgba(*[lay[..., LAYERS.index(n)] for n in ("Rust", "Moss", "Frost", "Embers")]),
        "T_Robot_U_Lines.png": rgba(*chans("line_masks", 0)),
        "T_Robot_U_Material.png": rgba(*chans("material_masks", 0)),
        "T_Robot_U_Material2.png": rgba(*chans("material_masks", 4)),
        "T_Robot_U_Badges.png": rgba(*chans("badge_masks", 0)),
        "T_Robot_U_Badges2.png": rgba(*chans("badge_masks", 4)),
        "T_Robot_U_Badges3.png": rgba(*chans("badge_masks", 8)),
    }
    assert list(out) == list(UNITY_TEXTURES)
    return out, cm.shape[0]


def write_png(path, pixels, width, height, channels, level=9):
    """Writes an 8-bit PNG. pixels: float array (h, w, c) in 0..1, row 0 = bottom (Blender order)."""
    arr = np.clip(np.round(pixels[::-1] * 255.0), 0, 255).astype(np.uint8)
    color_type = {1: 0, 3: 2, 4: 6}[channels]
    raw = b"".join(b"\x00" + arr[y].tobytes() for y in range(height))

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, level)) + chunk(b"IEND", b"")
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


BAKE_SCHEMA = "1.13-a"  # bump when the baking code changes in a way the cache key cannot see
# 1.13: the masks packed for Unity (Textures/Unity, at most 1024²) like the viewer packs them on load: zones as one ID
# channel, wear/grime next to metal/smoothness, joints and camo in an aux texture. Channels: robot_config.json >
# unityTextures. 10 textures instead of 14, a quarter of the pixels each.
UNITY_TEXTURES = collections.OrderedDict([
    ("T_Robot_U_ColorLight.png", "R armour, G mechanics, B metal (colour mask), A lights"),
    ("T_Robot_U_MetalWear.png", "R metallic, G wear (scratches), B grime, A smoothness"),
    ("T_Robot_U_Aux.png", "R glow pattern Joints, G paint zone ID (zone index + 1) / 8 - sample with point filter, "
                          "B camo field, A 0"),
    ("T_Robot_U_Layers.png", "R rust, G moss, B frost, A embers (equalised: amount a covers the share a)"),
    ("T_Robot_U_Lines.png", "glow line patterns 1-4 (linePatterns order)"),
    ("T_Robot_U_Material.png", "material patterns 1-4 (materials order)"),
    ("T_Robot_U_Material2.png", "material patterns 5-8"),
    ("T_Robot_U_Badges.png", "badges 1-4 (badges order)"),
    ("T_Robot_U_Badges2.png", "badges 5-8"),
    ("T_Robot_U_Badges3.png", "badges 9-12"),
])
UNITY_TEXTURE_MAX = 1024
STATIC_TEXTURES = ("T_Robot_MetallicSmoothness.png", "T_Robot_Occlusion.png", "T_Robot_ColorMask.png",
                   "T_Robot_LightMask.png", "T_Robot_Normal.png") + tuple(BADGE_TEXTURES + LINE_TEXTURES + ZONE_TEXTURES +
                                                                         LAYER_TEXTURES + MATERIAL_TEXTURES) + \
    tuple("Unity/" + n for n in UNITY_TEXTURES)
LAYER_SCALE = 16383.75  # cached float maps as uint16 over 0..4: steps of 6e-5, far below one 8-bit step
COMPOSE_TOLERANCE = 2e-3


def compare_skins():
    """The weathering comparison: Classic with every layer at three amounts, every material, three wear steps."""
    base = SKIN_DATA[DEFAULT_SKIN]
    out = []
    for lid, d in LAYER_DEFAULTS.items():
        for a in (0.3, 0.6, 1.0):
            out.append(("%s %d%%" % (lid, a * 100), dict(base, layers=[{"id": lid, "amount": a,
                                                                        "color": parse_color(d["color"])}])))
    for mid, d in MATERIAL_DEFAULTS.items():
        pal_ = dict(base["palette"], armor=parse_color(d["base"]))
        out.append((mid, dict(base, palette=pal_, material={"id": mid, "color": parse_color(d["color"])})))
    for w in (0.0, 0.5, 1.0):
        out.append(("Abnutzung %d%%" % (w * 100), dict(base, wear=w)))
    return out


def bake_textures(objs, mats, size, ao_samples, tex_dir, cache_dir=None, check_compose=False, device="auto",
                  compare_dir=None):
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
        base, emit = decorate_skin(layers, SKIN_DATA[skin], *compose_skin(layers, SKINS[skin]))
        suffix = "" if skin == DEFAULT_SKIN else "_" + skin
        out = {}
        for name, arr in (("T_Robot_BaseColor%s.png" % suffix, base * layers["ao_soft"][..., None]),
                          ("T_Robot_Emission%s.png" % suffix, emit)):
            out[name] = os.path.join(tex_dir, name)
            write_png(out[name], linear_to_srgb(arr), size, size, 3, level=6)   # 38 skins: level 9 cost seconds
        return out

    t0 = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for out in pool.map(write_skin, list(SKINS)):
            paths.update(out)
    LOG.info("  composed %d skins in %.1fs", len(SKINS), time.time() - t0)
    if compare_dir:            # half-size textures for the weathering comparison renders
        os.makedirs(compare_dir, exist_ok=True)
        half = {k: (v[::2, ::2] if isinstance(v, np.ndarray) else {n: w[::2, ::2] for n, w in v.items()})
                for k, v in layers.items()}

        def write_compare(item):
            i, (_, rec) = item
            b, e = decorate_skin(half, rec, *compose_skin(half, rec["palette"]))
            write_png(os.path.join(compare_dir, "base_%02d.png" % i), linear_to_srgb(b * half["ao_soft"][..., None]),
                      size // 2, size // 2, 3, level=3)
            write_png(os.path.join(compare_dir, "emit_%02d.png" % i), linear_to_srgb(e), size // 2, size // 2, 3, level=3)

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(write_compare, enumerate(compare_skins())))
    for key_ in ("eyes", "displays", "flipbook"):       # runtime atlases ship next to the baked set
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
    cmask = np.clip(results["mask"][..., :3], 0, 1)
    layers["cmask"] = cmask
    armor, painted = cmask[..., 0], np.clip(cmask[..., 0] + cmask[..., 2], 0, 1)     # armour; armour + metal
    for kind, chans in (("badge_masks", ["badge_" + b for b in BADGES]),
                        ("line_masks", ["line_" + n for n in LINE_PATTERNS]),
                        ("zone_masks", ["zone_" + z for z in PAINT_ZONES]),
                        ("layer_masks", ["layer_" + n for n in LAYERS]),
                        ("material_masks", ["mat_" + n for n in MATERIALS])):
        planes = []
        for i in range(0, len(chans), 3):
            pack = chans[i:i + 3]
            px = bake_pass("Masks_" + "+".join(c.split("_", 1)[1] for c in pack),
                           tuple(pack + ["zero"] * (3 - len(pack))), "data")
            for j, ch in enumerate(pack):
                # badges and zones paint armour and metal (an ear cap), glow lines the armour, joints their parts
                limit = {"badge": painted, "zone": painted, "material": armor,
                         "layer": np.clip(cmask.sum(axis=-1), 0, 1)}.get(kind[:-6], armor if ch != "line_Joints" else 1.0)
                plane = np.clip(px[..., j], 0, 1)
                if ch[len("layer_"):] in EQUALISED_LAYERS and ch.startswith("layer_"):
                    plane = equalise(plane, limit > 0.5)
                planes.append(plane * limit)
        layers[kind] = np.stack(planes, axis=-1)
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
    for kind, names in (("badge_masks", BADGE_TEXTURES), ("line_masks", LINE_TEXTURES), ("zone_masks", ZONE_TEXTURES),
                        ("layer_masks", LAYER_TEXTURES), ("material_masks", MATERIAL_TEXTURES)):
        for t, name in enumerate(names):
            planes = layers[kind][..., 4 * t:4 * t + 4]
            if planes.shape[-1] < 4:            # unused channels stay black (room for later patterns)
                planes = np.concatenate([planes, np.zeros(planes.shape[:2] + (4 - planes.shape[-1],), np.float32)], -1)
            files[name] = (planes, 4)
    unity, usize = unity_textures(results, layers, metal, smooth)
    for name, (arr, ch) in files.items():
        path = os.path.join(tex_dir, name)
        write_png(path, arr, size, size, ch)
        LOG.info("  wrote %s", path)
    os.makedirs(os.path.join(tex_dir, "Unity"), exist_ok=True)
    for name, arr in unity.items():
        write_png(os.path.join(tex_dir, "Unity", name), arr, usize, usize, 4)
    LOG.info("  wrote %d Unity textures (%d²)", len(unity), usize)
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


def equalise(field, inside):
    """Rank transform inside the painted surface (via a 65536-bin histogram, no full sort): afterwards the share of
    pixels above 1 - a is a, so a layer amount reads as the covered share. Pixels outside stay as they are."""
    out = field.copy()
    vals = field[inside]
    if vals.size:
        bins = np.minimum((np.clip(vals, 0, 1) * 65535.0).astype(np.int64), 65535)
        counts = np.bincount(bins, minlength=65536)
        cdf = (np.cumsum(counts) - counts * 0.5) / vals.size       # mid-rank of each bin
        out[inside] = cdf[bins].astype(np.float32)
    return out


LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
LINE_PAINT = 0.85       # glow lines also tint the albedo (visible in daylight): share of the line colour x 0.6


COVER_SOFT = 0.1           # layer edge: cover = clamp((field - (1 - amount)) / COVER_SOFT, 0, 1)
BARE_METAL = np.array([0.62, 0.63, 0.65], dtype=np.float32)


def cover(field, amount, soft=COVER_SOFT):
    return np.clip((field - (1.0 - amount)) / soft, 0.0, 1.0)


def decorate_skin(layers, skin, base, emit):
    """Everything of a skin on top of the composed palette colours, in this order (same formulas as the viewer and the
    Unity shader): second colour in its zones -> wear (0 clean, 0.5 as baked, 1 battered) -> material pattern of the
    armour -> badges -> surface layers (rust, moss, frost, camo, embers) -> glow lines. Colour replacements keep the
    shading (luminance ratio to the region's palette colour)."""
    if not (skin["badges"] or skin["lines"] or skin["zones2"] or skin["material"] or skin["layers"] or
            abs(skin["wear"] - WEAR_DEFAULT) > 1e-6):
        return base, emit
    pal_ = skin["palette"]
    cm = layers["cmask"]
    lum = {k: float(np.asarray(pal_[k], np.float32) @ LUMA) for k in ("armor", "mech", "metal")}
    blum = base @ LUMA
    ratio = np.clip(blum / np.maximum(cm[..., 0] * lum["armor"] + cm[..., 2] * lum["metal"], 1e-3), 0.2, 1.6)[..., None]
    ratio_all = np.clip(blum / np.maximum(cm[..., 0] * lum["armor"] + cm[..., 1] * lum["mech"] + cm[..., 2] * lum["metal"],
                                          1e-3), 0.2, 1.6)[..., None]
    region = (cm[..., 0:1] * np.asarray(pal_["armor"], np.float32) + cm[..., 1:2] * np.asarray(pal_["mech"], np.float32) +
              cm[..., 2:3] * np.asarray(pal_["metal"], np.float32))
    if skin["zones2"]:
        m = np.clip(sum(layers["zone_masks"][..., list(PAINT_ZONES).index(z)] for z in skin["zones2"]), 0, 1)[..., None]
        base = base * (1.0 - m) + m * np.asarray(pal_["armor2"], dtype=np.float32) * ratio
        region = region * (1.0 - m) + m * np.asarray(pal_["armor2"], dtype=np.float32)
    w = skin["wear"]          # before material and layers: cleaning must not erase their patterns
    if w < WEAR_DEFAULT:                         # cleaner: scratches and grime back to the paint
        clean = (WEAR_DEFAULT - w) * 2.0
        sc = np.clip(layers["layer_masks"][..., LAYERS.index("Wear")][..., None] * 1.6, 0, 1) * clean
        base = base * (1.0 - sc) + sc * region
        gr = layers["layer_masks"][..., LAYERS.index("Grime")][..., None] * clean * 0.4
        base = base * (1.0 - gr) + gr * region
    elif w > WEAR_DEFAULT:                       # battered: more bare metal, dirt in the dents
        more = (w - WEAR_DEFAULT) * 2.0
        sc = np.clip(layers["layer_masks"][..., LAYERS.index("Wear")][..., None] * 4.0 * more, 0, 1) * 0.85
        base = base * (1.0 - sc) + sc * BARE_METAL
        base = base * (1.0 - 0.6 * more * layers["layer_masks"][..., LAYERS.index("Grime")][..., None])
    if skin["material"]:
        mat = skin["material"]
        p = layers["material_masks"][..., MATERIALS.index(mat["id"])][..., None]
        armor = cm[..., 0:1]
        base = base * (1.0 - armor) + armor * (base * p + np.asarray(mat["color"], np.float32) * ratio * (1.0 - p))
    for b in skin["badges"]:
        m = layers["badge_masks"][..., BADGES.index(b["id"])][..., None]
        base = base * (1.0 - m) + m * np.asarray(b["color"], dtype=np.float32) * ratio
    fields = {n: layers["layer_masks"][..., i][..., None] for i, n in enumerate(LAYERS)}
    by_id = {ly["id"]: ly for ly in skin["layers"]}
    for lid in ("Rust", "Moss", "Frost"):
        if lid in by_id:
            c = cover(fields[lid], by_id[lid]["amount"])
            col = np.asarray(by_id[lid]["color"], np.float32) * (0.6 + 0.6 * fields["Grime"])   # patchy, not flat
            base = base * (1.0 - c) + c * col * ratio_all
    if "Camo" in by_id:
        a, col = by_id["Camo"]["amount"], np.asarray(by_id["Camo"]["color"], np.float32)
        c1, c2 = cover(fields["Camo"], a), cover(fields["Camo"], a * 0.45)
        base = base * (1.0 - c1) + c1 * col * ratio_all
        base = base * (1.0 - c2) + c2 * col * 0.45 * ratio_all
    if "Embers" in by_id:
        a, col = by_id["Embers"]["amount"], np.asarray(by_id["Embers"]["color"], np.float32)
        char = cover(fields["Embers"], min(1.0, a * 2.5))
        glow = cover(fields["Embers"], a * 0.3, 0.04)                  # glowing veins in the charred area
        base = base * (1.0 - 0.88 * char)
        emit = emit + glow * col * 1.5
    if skin["lines"]:
        ln = skin["lines"]
        m = layers["line_masks"][..., LINE_PATTERNS.index(ln["pattern"])][..., None]
        col = np.asarray(ln["color"], dtype=np.float32)
        base = base * (1.0 - m * LINE_PAINT) + m * LINE_PAINT * 0.6 * col
        emit = emit + m * col * ln["strength"]
    return base, emit
    pal_ = skin["palette"]
    cm = layers["cmask"]
    ref = cm[..., 0] * float(np.asarray(pal_["armor"], np.float32) @ LUMA) + \
        cm[..., 2] * float(np.asarray(pal_["metal"], np.float32) @ LUMA)
    ratio = np.clip((base @ LUMA) / np.maximum(ref, 1e-3), 0.2, 1.6)[..., None]   # shading of the painted surface
    if skin["zones2"]:
        m = np.clip(sum(layers["zone_masks"][..., list(PAINT_ZONES).index(z)] for z in skin["zones2"]), 0, 1)[..., None]
        base = base * (1.0 - m) + m * np.asarray(pal_["armor2"], dtype=np.float32) * ratio
    for b in skin["badges"]:
        m = layers["badge_masks"][..., BADGES.index(b["id"])][..., None]
        base = base * (1.0 - m) + m * np.asarray(b["color"], dtype=np.float32) * ratio
    if skin["lines"]:
        ln = skin["lines"]
        m = layers["line_masks"][..., LINE_PATTERNS.index(ln["pattern"])][..., None]
        col = np.asarray(ln["color"], dtype=np.float32)
        base = base * (1.0 - m * LINE_PAINT) + m * LINE_PAINT * 0.6 * col
        emit = emit + m * col * ln["strength"]
    return base, emit


def save_layers(path, layers):
    flat = {"c_base": layers["c_base"], "c_emit": layers["c_emit"], "ao_soft": layers["ao_soft"],
            "badge_masks": layers["badge_masks"], "line_masks": layers["line_masks"],
            "zone_masks": layers["zone_masks"], "cmask": layers["cmask"], "layer_masks": layers["layer_masks"],
            "material_masks": layers["material_masks"]}
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
    defaults = [o for o in objs if o.get("slot") and SLOTS.get(o["slot"], [None])[0] == o["variant"]]
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
        # margin at most half the gap between packed islands: neighbouring islands of other variants (baked in other
        # passes) must not be overwritten
        bpy.ops.object.bake(type="AO", margin=max(1, int(UV_MARGIN * size / 2)), use_clear=(i == 0))
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
    inv = nt.nodes.new("ShaderNodeMath")
    inv.operation = "SUBTRACT"
    inv.inputs[0].default_value = 1.0
    inv.location = (-150, -150)
    nt.links.new(ms.outputs["Alpha"], inv.inputs[1])
    # finish of the skin on the armour (ColorMask R), like the runtime shader: roughness x factor, metalness + offset
    cm = nt.nodes.new("ShaderNodeSeparateColor")
    nt.links.new(tex("T_Robot_ColorMask.png", "Non-Color", -200).outputs["Color"], cm.inputs[0])

    def finished(value, op, name, default):
        par = nt.nodes.new("ShaderNodeValue")
        par.name = name
        par.outputs[0].default_value = default
        f = nt.nodes.new("ShaderNodeMath")
        f.operation, f.use_clamp = op, True
        nt.links.new(value, f.inputs[0])
        nt.links.new(par.outputs[0], f.inputs[1])
        mix = nt.nodes.new("ShaderNodeMix")
        mix.data_type = "FLOAT"
        nt.links.new(cm.outputs[0], mix.inputs[0])
        nt.links.new(value, mix.inputs[2])
        nt.links.new(f.outputs[0], mix.inputs[3])
        return mix.outputs[0]

    nt.links.new(finished(sep.outputs[0], "ADD", "FinishMetal", 0.0), bsdf.inputs["Metallic"])
    nt.links.new(finished(inv.outputs[0], "MULTIPLY", "FinishRough", 1.0), bsdf.inputs["Roughness"])
    nrm = tex("T_Robot_Normal.png", "Non-Color", -650)
    nmap = nt.nodes.new("ShaderNodeNormalMap")
    nmap.location = (-150, -650)
    nmap.uv_map = "UVMap"
    nt.links.new(nrm.outputs["Color"], nmap.inputs["Color"])
    nt.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
    em = tex("T_Robot_Emission.png", "sRGB", -350)
    nt.links.new(em.outputs["Color"], bsdf.inputs["Emission Color"])
    bsdf.inputs["Emission Strength"].default_value = 5.0
    preview_special_nodes(mat, bsdf, cm.outputs[0])
    mat.diffuse_color = (0.78, 0.8, 0.83, 1.0)
    return mat


def preview_special_nodes(mat, bsdf, armor):
    """1.13 special materials for the preview renders (static, time = 0): every effect computes colour, emission and
    alpha; value nodes SpecialW_<id> (0/1, set by set_look) pick one. Same formulas as the viewer shader."""
    k = NodeKit(mat)
    links = {name: bsdf.inputs[name].links[0].from_socket for name in ("Base Color", "Emission Color", "Roughness",
                                                                         "Metallic")}
    c, emit = links["Base Color"], links["Emission Color"]

    def rgb_node(name):
        nd = k.node("ShaderNodeRGB")
        nd.name = name
        nd.outputs[0].default_value = (1, 1, 1, 1)
        return nd.outputs[0]

    def value_node(name, v):
        nd = k.node("ShaderNodeValue")
        nd.name = name
        nd.outputs[0].default_value = v
        return nd.outputs[0]

    def hsv(hue, sat):
        nd = k.node("ShaderNodeCombineColor", mode="HSV")
        k.link(hue, nd.inputs[0])
        nd.inputs[1].default_value = sat
        nd.inputs[2].default_value = 1.0
        return nd.outputs[0]

    def white(vec=None, w=None):
        nd = k.node("ShaderNodeTexWhiteNoise", noise_dimensions="1D" if w is not None else "3D")
        k.link(w if w is not None else vec, nd.inputs["W" if w is not None else "Vector"])
        return nd.outputs["Value"]

    a, b, st = rgb_node("SpecialA"), rgb_node("SpecialB"), value_node("SpecialStrength", 1.0)
    vec, x, _, h = k.coords()                      # object space = character space (Z up), metres
    lw = k.node("ShaderNodeLayerWeight")
    lw.inputs["Blend"].default_value = 0.5
    facing = lw.outputs["Facing"]
    f = k.math("POWER", facing, 3.0)
    bw = k.node("ShaderNodeRGBToBW")
    k.link(c, bw.inputs[0])
    lum = bw.outputs[0]
    arm_s = k.math("MULTIPLY", armor, st)
    effects = []        # (id, colour, emission (strength 1) or None, alpha or None, roughness or None, metal or None)
    # Glass: tint, see-through inside, bright rim
    effects.append(("Glass", k.mix(k.math("MULTIPLY", st, 0.6), c, a), k.scale_color(b, k.math("MULTIPLY", f, 0.25)),
                    k.math("MULTIPLY_ADD", f, 0.6, c=0.35), 0.05, 0.0))
    # Hologram: scan lines 40 per metre of height
    scan = k.math("MULTIPLY_ADD", k.math("GREATER_THAN", k.math("FRACT", k.math("MULTIPLY", h, 40.0)), 0.5), 0.35,
                  c=0.65)
    holo_e = k.math("MULTIPLY", k.math("MULTIPLY", k.math("MULTIPLY_ADD", f, 1.6, c=0.35), scan), st)
    effects.append(("Hologram", k.scale_color(a, 0.15), k.scale_color(a, holo_e),
                    k.math("MULTIPLY", k.math("MULTIPLY_ADD", f, 0.6, c=0.25), scan), 0.2, 0.0))
    # Glitch: 12 bands per metre, a random value per band
    r = white(w=k.math("FLOOR", k.math("MULTIPLY", h, 12.0)))
    g1, g2 = k.math("GREATER_THAN", r, 0.78), k.math("GREATER_THAN", r, 0.9)
    gcol = k.mix(g2, a, b)
    effects.append(("Glitch", k.mix(k.math("MULTIPLY", g1, st), c, gcol),
                    k.scale_color(gcol, k.math("MULTIPLY", k.math("MULTIPLY", g1, st), 1.2)), None, None, None))
    # Iridescent: hue from the viewing angle and the height, on the armour
    iri = hsv(k.math("FRACT", k.math("MULTIPLY_ADD", f, 1.2, c=k.math("MULTIPLY", h, 0.35))), 0.55)
    effects.append(("Iridescent", k.mix(arm_s, c, k.scale_color(iri, k.math("DIVIDE", lum, 0.5))), None, None, None,
                    None))
    # Sketch: paper, ink outline from the rim, diagonal hatching where the surface is dark
    outline = k.map_range(facing, 0.25, 0.5, smooth=True)
    dark = k.math("SUBTRACT", 1.0, k.math("MULTIPLY", lum, 1.6), clamp=True)
    hatch = k.math("GREATER_THAN", k.math("FRACT", k.math("MULTIPLY", k.math("ADD", x, h), 45.0)), 0.7)
    ink = k.math("MAXIMUM", outline, k.math("MULTIPLY", hatch, dark))
    effects.append(("Sketch", k.mix(k.math("MULTIPLY", ink, st), k.mix(st, c, a), b), None, None, 1.0, 0.0))
    # Rainbow (animated at runtime): hue along the height and the side, on the armour
    rb = hsv(k.math("FRACT", k.math("MULTIPLY_ADD", h, 0.8, c=k.math("MULTIPLY", x, 0.3))), 0.75)
    effects.append(("Rainbow", k.mix(arm_s, c, k.scale_color(rb, k.math("DIVIDE", lum, 0.45))),
                    k.scale_color(rb, k.math("MULTIPLY", arm_s, 0.15)), None, None, None))
    # Galaxy (animated at runtime): nebula noise between the two colours, stars on a 120/m cell grid
    n = k.noise(k.stretch(vec, 3.0, 3.0, 3.0), 1.0, detail=3.0)
    neb = k.scale_color(k.mix(n, a, b), k.math("MULTIPLY_ADD", n, 0.8, c=0.4))
    cell = k.node("ShaderNodeVectorMath", operation="FLOOR")
    k.link(k.stretch(vec, 120.0, 120.0, 120.0), cell.inputs[0])
    stars = k.math("GREATER_THAN", white(vec=cell.outputs[0]), 0.985)
    gal_e = k.mix(stars, k.scale_color(neb, 0.5), (2.0, 2.0, 2.0))
    effects.append(("Galaxy", k.mix(arm_s, c, neb), k.scale_color(gal_e, arm_s), None, None, None))
    assert [e[0] for e in effects] == list(SPECIALS)
    col, em_add, alpha = c, None, 1.0
    rough, metal = links["Roughness"], links["Metallic"]
    # visor and lights (nothing in the colour mask) keep their look, like in the viewer; fabric takes the effect
    sep = armor.node
    painted = (1.0 if mat.name.startswith("M_RobotFabric") else
               k.math("ADD", k.math("ADD", sep.outputs[0], sep.outputs[1]), sep.outputs[2], clamp=True))
    for sid, ecol, eem, ealpha, erough, emetal in effects:
        w = k.math("MULTIPLY", value_node("SpecialW_" + sid, 0.0), painted)
        col = k.mix(w, col, ecol)
        if eem is not None:
            term = k.scale_color(eem, k.math("MULTIPLY", w, 0.2))        # the BSDF multiplies emission by 5
            em_add = term if em_add is None else _add_rgb(k, em_add, term)
        if ealpha is not None:
            alpha = k.math("ADD", alpha, k.math("MULTIPLY", w, k.math("SUBTRACT", ealpha, 1.0)))
        if erough is not None:
            rough = k.math("ADD", rough, k.math("MULTIPLY", w, k.math("SUBTRACT", erough, rough)))
            metal = k.math("ADD", metal, k.math("MULTIPLY", w, k.math("SUBTRACT", emetal, metal)))
    k.link(col, bsdf.inputs["Base Color"])
    k.link(_add_rgb(k, emit, em_add), bsdf.inputs["Emission Color"])
    # 1.14 form "Perforated": round holes on a grid in UV space (every second row offset by half a cell), armour only
    uvn = k.node("ShaderNodeUVMap")
    uvn.uv_map = "UVMap"
    uvs = k.node("ShaderNodeSeparateXYZ")
    k.link(uvn.outputs[0], uvs.inputs[0])
    cells = value_node("PerfCells", 50.0)
    u, v = k.math("MULTIPLY", uvs.outputs[0], cells), k.math("MULTIPLY", uvs.outputs[1], cells)
    shift = k.math("MULTIPLY", k.math("FLOORED_MODULO", k.math("FLOOR", v), 2.0), 0.5)
    du = k.math("SUBTRACT", k.math("FRACT", k.math("ADD", u, shift)), 0.5)
    dv = k.math("SUBTRACT", k.math("FRACT", v), 0.5)
    dist = k.math("SQRT", k.math("ADD", k.math("MULTIPLY", du, du), k.math("MULTIPLY", dv, dv)))
    hole = k.math("MULTIPLY", k.math("LESS_THAN", dist, PERFORATION["radius"]),
                  k.math("GREATER_THAN", sep.outputs[0], 0.5))
    alpha = k.math("MULTIPLY", alpha, k.math("SUBTRACT", 1.0, k.math("MULTIPLY", hole, value_node("PerfW", 0.0))))
    keep = k.node("NodeReroute")         # alpha is linked only while a see-through special or the form needs it
    keep.name = "SpecialAlpha"
    k.link(alpha, keep.inputs[0])
    k.link(rough, bsdf.inputs["Roughness"])
    k.link(metal, bsdf.inputs["Metallic"])


def _add_rgb(k, a, b):
    nd = k.node("ShaderNodeMix", data_type="RGBA", blend_type="ADD")
    nd.inputs[0].default_value = 1.0
    k.link(a, nd.inputs[6])
    k.link(b, nd.inputs[7])
    return nd.outputs[2]


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


# ------------------------------- 1.14 body forms: Faceted and Voxel geometry -------------------------------
# Every part object gets a form copy with the same vertex groups, materials and UV atlas: bound to the same rig, it
# plays every clip, and every skin texture fits. Screens and the visor stay smooth (they carry the eye and display
# atlases); in the voxel form they move in front of the blocks.
FACET_RATIO = 0.16          # share of the triangles the collapse keeps (sharp, paper-like facets)
VOXEL_CELL = 0.03           # metres; world grid in the rest pose, so the blocks of neighbouring parts line up
VOXEL_INSET = 0.5           # a cell counts if its centre lies half a cell inside the part: every block stays inside
                            # the base form, so its collision-free clips carry over (0.25 collided in 67 of 69 clips)
FORM_KEEP = ("Head_Visor", "Eye_Screen", "Chest_Display", "Chest_Screen")
# voxel form: joints stay round (a block cluster is not rotation-invariant like a ball: it would cut into its
# neighbour in every clip) and fingers stay as they are (smaller than a cell); matched on the part name without side
VOXEL_KEEP = ("Shoulder_Socket", "Shoulder_Ball", "Elbow", "Elbow_Band", "Wrist", "Hip_Ball", "Knee", "Ankle", "Neck",
              "Neck_Ring", "Collar", "Waist", "Waist_Ring", "Crotch", "Palm", "Finger", "Thumb", "Claw_Cuff",
              "Claw_Prong", "Claw_Tip", "Claw_Thumb")
FORM_OBJS = {}              # form -> {group key: object}
FACE_INT_LAYERS = ("part_id", "paint_zone", "glow_part")


def form_keep(ob, bm, extra=()):
    """Predicate: faces that stay smooth (screens by material, visor and screens by part name, extra: base names
    without side and index, e.g. "Finger" for Finger_0_L)."""
    names = list(ob.data.get("part_names", []))
    ids = {i for i, n in enumerate(names) if n in FORM_KEEP or
           any(n == e or n.startswith(e + "_") for e in extra)}
    mats = {i for i, m in enumerate(ob.data.materials)
            if m and m.name.startswith(("M_RobotEyes", "M_RobotDisplay", "EyeScreen", "Display"))}
    layer = bm.faces.layers.int.get("part_id")
    return lambda f: f.material_index in mats or (layer is not None and f[layer] in ids)


def uv_layer_of(bm):
    """The UV layer of a new form mesh, named like the part objects' (materials and the FBX look it up by name)."""
    return bm.loops.layers.uv.get("UVMap") or bm.loops.layers.uv.new("UVMap")


class FaceCopier:
    """Copies bmesh faces with weights, UVs, materials and the integer face layers into another bmesh."""

    def __init__(self, src, dst):
        self.dst = dst
        self.src_def, self.dst_def = src.verts.layers.deform.verify(), dst.verts.layers.deform.verify()
        self.src_uv = src.loops.layers.uv.active
        self.dst_uv = uv_layer_of(dst) if self.src_uv is not None else None
        self.ints = []
        for name in FACE_INT_LAYERS:
            sl = src.faces.layers.int.get(name)
            if sl is not None:
                self.ints.append((sl, dst.faces.layers.int.get(name) or dst.faces.layers.int.new(name)))

    def copy(self, faces):
        for f in faces:
            vs = []
            for v in f.verts:
                nv = self.dst.verts.new(v.co)
                for k, w in v[self.src_def].items():
                    nv[self.dst_def][k] = w
                vs.append(nv)
            nf = self.dst.faces.new(vs)
            nf.material_index, nf.smooth = f.material_index, f.smooth
            if self.src_uv is not None:
                for l, ol in zip(f.loops, nf.loops):
                    ol[self.dst_uv].uv = l[self.src_uv].uv
            for sl, dl in self.ints:
                nf[dl] = f[sl]


def _form_object(src, bm, form, coll):
    me = bpy.data.meshes.new("%s__%s" % (src.name, form))
    for m in src.data.materials:
        me.materials.append(m)
    bm.to_mesh(me)
    for k in ("part_names", "spin_parts"):
        if k in src.data:
            me[k] = list(src.data[k])
    ob = bpy.data.objects.new(me.name, me)
    coll.objects.link(ob)
    for g in src.vertex_groups:                 # same order: the deform layer indexes them
        ob.vertex_groups.new(name=g.name)
    for p in me.polygons:
        p.use_smooth = False
    ob["form"], ob["base"] = form, src.name
    ob["slot"], ob["variant"] = src.get("slot", ""), src.get("variant", "")
    return ob


def faceted_copy(src, coll):
    """Collapse-decimated copy with flat facets; screens and visor are carried over unchanged."""
    bm = bmesh.new()
    bm.from_mesh(src.data)
    keeps = form_keep(src, bm)
    keep = [f for f in bm.faces if keeps(f)]
    rest = bmesh.new()
    rest.from_mesh(src.data)
    rest_keep = form_keep(src, rest)
    bmesh.ops.delete(rest, geom=[f for f in rest.faces if rest_keep(f)], context="FACES")
    tmp_me = bpy.data.meshes.new("facet_tmp")
    rest.to_mesh(tmp_me)
    rest.free()
    tmp = bpy.data.objects.new("facet_tmp", tmp_me)
    coll.objects.link(tmp)
    for g in src.vertex_groups:
        tmp.vertex_groups.new(name=g.name)
    mod = tmp.modifiers.new("Decimate", "DECIMATE")
    mod.decimate_type, mod.ratio, mod.use_collapse_triangulate = "COLLAPSE", FACET_RATIO, True
    for o in bpy.context.scene.objects:
        o.select_set(o == tmp)
    bpy.context.view_layer.objects.active = tmp
    with bpy.context.temp_override(object=tmp, active_object=tmp, selected_objects=[tmp]):
        if tmp.data.has_custom_normals:
            bpy.ops.mesh.customdata_custom_splitnormals_clear()
        bpy.ops.object.modifier_apply(modifier=mod.name)
    out = bmesh.new()
    out.from_mesh(tmp.data)
    remove_object(tmp)
    if keep:
        FaceCopier(bm, out).copy(keep)
    bm.free()
    ob = _form_object(src, out, "Faceted", coll)
    out.free()
    return ob


def _islands(bm):
    seen, islands = set(), []
    for f in bm.faces:
        if f.index in seen:
            continue
        stack, isl = [f], []
        seen.add(f.index)
        while stack:
            g = stack.pop()
            isl.append(g)
            for e in g.edges:
                for h in e.link_faces:
                    if h.index not in seen:
                        seen.add(h.index)
                        stack.append(h)
        islands.append(isl)
    return islands


_VOX_DIRS = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))


def _voxelise_island(isl, tris, out, cell, uv_layer, int_layers, group):
    """Cells whose centre lies inside the closed island (ray parity) and VOXEL_INSET deep; one quad per open cube
    side, UV, material and part of the nearest point of the surface. Returns False for thin parts without such a
    cell (bolts, seams, rings): they stay smooth, a whole block would stick out far."""
    loops = [lt for f in isl for lt in tris[f.index]]
    verts = [l.vert.co for lt in loops for l in lt]
    tree = BVHTree.FromPolygons(verts, [(3 * i, 3 * i + 1, 3 * i + 2) for i in range(len(loops))])
    lo = Vector([min(v[i] for v in verts) for i in range(3)])
    hi = Vector([max(v[i] for v in verts) for i in range(3)])
    occ = set()
    ray = Vector((1, 0, 0))
    for ix in range(math.floor(lo.x / cell), math.ceil(hi.x / cell)):
        for iy in range(math.floor(lo.y / cell), math.ceil(hi.y / cell)):
            for iz in range(math.floor(lo.z / cell), math.ceil(hi.z / cell)):
                o0 = o = Vector(((ix + 0.5) * cell, (iy + 0.5) * cell, (iz + 0.5) * cell))
                n = 0
                while True:
                    hit = tree.ray_cast(o, ray)
                    if hit[0] is None:
                        break
                    n += 1
                    o = hit[0] + ray * 1e-5
                if n % 2 and tree.find_nearest(o0)[3] >= VOXEL_INSET * cell:
                    occ.add((ix, iy, iz))
    if not occ:
        return False
    dl = out.verts.layers.deform.verify()
    ul = uv_layer_of(out) if uv_layer is not None else None
    for (ix, iy, iz) in sorted(occ):
        for d in _VOX_DIRS:
            if (ix + d[0], iy + d[1], iz + d[2]) in occ:
                continue
            fc = Vector(((ix + 0.5 + d[0] / 2) * cell, (iy + 0.5 + d[1] / 2) * cell, (iz + 0.5 + d[2] / 2) * cell))
            a, b = [k for k in range(3) if d[k] == 0]
            corners = []
            for s1, s2 in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                p = fc.copy()
                p[a] += s1 * cell / 2
                p[b] += s2 * cell / 2
                corners.append(p)
            if Vector(d).dot((corners[1] - corners[0]).cross(corners[2] - corners[1])) < 0:
                corners.reverse()
            vs = [out.verts.new(c) for c in corners]
            for v in vs:
                v[dl][group] = 1.0
            f = out.faces.new(vs)
            _, near, ti, _ = tree.find_nearest(fc)
            lt = loops[ti]
            src_face = lt[0].face
            f.material_index, f.smooth = src_face.material_index, False
            if ul is not None:
                tri = [l.vert.co for l in lt]
                uv = mathutils.geometry.barycentric_transform(near, *tri, *[l[uv_layer].uv.to_3d() for l in lt])
                if not all(math.isfinite(c) for c in uv):         # degenerate triangle: its nearest corner
                    uv = min(lt, key=lambda l: (l.vert.co - near).length)[uv_layer].uv.to_3d()
                for l in f.loops:
                    l[ul].uv = uv.to_2d()
            for sl, dl_ in int_layers:
                f[dl_] = src_face[sl]
    return True


def voxel_copies(part_objs, keys, coll):
    """Voxel copies of the part objects. Joints, fingers, thin parts, the visor and the screens stay smooth; the
    blocks lie inside the base form (VOXEL_INSET), so nothing covers the visor or the screens."""
    objs = collections.OrderedDict()
    for key in keys:
        src = part_objs[key]
        bm = bmesh.new()
        bm.from_mesh(src.data)
        bm.faces.ensure_lookup_table()
        tris = collections.defaultdict(list)
        for lt in bm.calc_loop_triangles():
            tris[lt[0].face.index].append(lt)
        keeps = form_keep(src, bm, VOXEL_KEEP)
        deform = bm.verts.layers.deform.verify()
        uv_layer = bm.loops.layers.uv.active
        out = bmesh.new()
        copier = FaceCopier(bm, out)
        smooth = []
        for isl in _islands(bm):
            if keeps(isl[0]):
                smooth.extend(isl)
                continue
            group = max(isl[0].verts[0][deform].items(), key=lambda kv: kv[1])[0]
            if not _voxelise_island(isl, tris, out, VOXEL_CELL, uv_layer, copier.ints, group):
                smooth.extend(isl)
        copier.copy(smooth)
        objs[key] = _form_object(src, out, "Voxel", coll)
        bm.free()
        out.free()
    return objs


def perforation_cells(ob):
    """Holes per UV unit for PERFORATION["spacing"]: metres per UV unit from the surface and UV areas of the core
    body's atlas faces (smart UV project keeps the texel density about even; eyes and display have fixed UVs that
    span a whole atlas cell and are left out)."""
    me = ob.data
    uv = me.uv_layers[0].data
    body = {i for i, k in enumerate(MAT_ORDER) if k in BODY_MATS}     # authoring slots (before remap_materials)
    area3 = area2 = 0.0
    for p in me.polygons:
        if p.material_index not in body:
            continue
        pts = [uv[i].uv for i in p.loop_indices]
        area2 += abs(sum(pts[i].x * pts[i - 1].y - pts[i - 1].x * pts[i].y for i in range(len(pts)))) / 2
        area3 += p.area
    return round(math.sqrt(area3 / max(area2, 1e-9)) / PERFORATION["spacing"], 1)


def make_forms(part_objs, coll, arm_ob):
    """FORM_OBJS: the mesh-form copies of every part object, bound to the rig and hidden."""
    FORM_OBJS.clear()
    keys = [k for k in part_objs if not k.startswith("Form_")]
    for form in MESH_FORMS:
        t0 = time.time()
        if form == "Faceted":
            objs = collections.OrderedDict((k, faceted_copy(part_objs[k], coll)) for k in keys)
        else:
            objs = voxel_copies(part_objs, keys, coll)
        for ob in objs.values():
            bind_mesh(ob, arm_ob)
            ob.hide_render = True
            ob.hide_set(True)
        FORM_OBJS[form] = objs
        LOG.debug("  form %s: %d objects, %d triangles (default look) in %.1f s", form, len(objs),
                  sum(tri_count(objs[g].data) for g in config_groups(default_config())), time.time() - t0)


def form_objects(part_objs, robot_mesh, variants, form):
    """Objects that show a look: the variants in its form (mesh form copies, or the parts of a parts form)."""
    kind = FORMS[form]["kind"] if form else None
    if not variants and kind not in ("mesh", "parts"):
        return [robot_mesh]
    cfg = default_config()
    cfg.update(variants or {})
    groups = config_groups(cfg)
    if kind == "mesh":
        return [FORM_OBJS[form][g] for g in groups]
    objs = [part_objs[g] for g in groups]
    if kind == "parts":
        objs.append(part_objs[group_key("Form", form)])
    return objs


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
    # sockets and spring bones sit where the reshaped head and torso put their parts (PROPORTIONS)
    bone("Socket_Head", shaped("Head", (0, 0, 1.30)), shaped("Head", (0, 0, 1.40)), "Head", roll_up=(0, -1, 0))
    bone("Socket_Neck", shaped("Spine", (0, -0.17, 0.872)), shaped("Spine", (0, -0.25, 0.872)), "Spine",
         roll_up=(0, 0, 1))
    bone("Socket_Back", shaped("Spine", (0, 0.22, 0.77)), shaped("Spine", (0, 0.30, 0.77)), "Spine", roll_up=(0, 0, 1))
    # 1.12 spring bones (SPRINGS): baked secondary motion of the cape and the antenna rod
    bone("Cape", shaped("Spine", (0, 0.235, 0.830)), shaped("Spine", (0, 0.235, 0.600)), "Socket_Back",
         roll_up=(0, 1, 0))
    bone("Antenna", shaped("Head", (0, 0.0, 1.325)), shaped("Head", (0, 0.0, 1.480)), "Socket_Head",
         roll_up=(0, -1, 0))
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
    "hat": (0.0, 0.0, 0.0, 0.0),    # Socket_Head: pitch, roll (deg) about its base, shift x, lift z (m) - emotes
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
        mats = {b.name: b.matrix_local.copy() for b in bones}
        self.length = {b.name: b.length for b in bones}
        self.rest_rel = {n: (mats[p].inverted() @ m if p else m) for n, (m, p) in
                         ((n, (mats[n], self.parent[n])) for n in mats)}
        # local axis of the Eyes bone that matches armature Z (for blink scaling)
        m = bones["Eyes"].matrix_local.to_3x3()
        self.eye_axis = max(range(3), key=lambda i: abs(m.col[i].z))
        self.ik_error = 0.0

    def local(self, bone, q_delta):
        return self.rest_inv[bone] @ q_delta @ self.rest[bone]

    def pose_matrix(self, sample, bone, cache):
        """Armature-space matrix of a bone for one solved sample (Blender's pose formula)."""
        m = cache.get(bone)
        if m is None:
            loc, q, scale = sample.get(bone, (None, None, None))
            basis = (Matrix.Translation(loc) if loc is not None else Matrix.Identity(4)) @ \
                (q.to_matrix().to_4x4() if q is not None else Matrix.Identity(4))
            if scale is not None:
                basis = basis @ Matrix.Diagonal(Vector(scale)).to_4x4()
            par = self.parent[bone]
            m = (self.pose_matrix(sample, par, cache) if par else Matrix.Identity(4)) @ self.rest_rel[bone] @ basis
            cache[bone] = m
        return m

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
        hp, hr, hx, hz = pose["hat"]
        out["Socket_Head"] = (self.rest_inv["Socket_Head"] @ Vector((hx, 0, hz)),
                              self.local("Socket_Head", q_euler(hp, hr, 0)), None)
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


# Spring bones: secondary motion baked into every clip. A point mass at the bone tip follows the posed rest direction
# (blended towards gravity) as a damped spring; the result is limited so the part never swings into the body.
# out = the bone-local axis that points away from the body; swing = (min, max) degrees towards out, side = +-degrees.
SPRINGS = collections.OrderedDict([
    # side: small, the cape turns in its own plane and its upper corners rise towards the backpack
    ("Cape", {"freq": 1.5, "damping": 0.32, "gravity": 0.5, "wind": 38.0, "out": 2, "swing": (0.0, 70.0),
              "side": 7.0}),
    ("Antenna", {"freq": 2.8, "damping": 0.16, "gravity": 0.0, "wind": 16.0, "out": None, "swing": (-24.0, 24.0),
                 "side": 24.0}),
])
# Relative wind of the in-place locomotion clips (0..1): lifts the cape and bends the antenna backwards.
CLIP_WIND = {"Walk": 0.3, "Run": 0.75, "RunFast": 0.9, "Sprint": 1.0, "CrouchWalk": 0.2, "SlopeSlide": 0.6,
             "Fall": 0.35, "DoubleJump": 0.3}


# Type emotes: a socket rides rigidly on a hand between two frames (the hat in "HatTip").
HAT_GRAB, HAT_RELEASE = 12, 46      # HatTip key frames where the hand holds the hat (same pose at both)
ATTACHMENTS = {"HatTip": ("Socket_Head", "Hand_R", HAT_GRAB, HAT_RELEASE)}


def bake_attachment(solver, samples, name):
    if name not in ATTACHMENTS:
        return
    bone, hand, f0, f1 = ATTACHMENTS[name]
    par = solver.parent[bone]
    c0 = {}
    grip = solver.pose_matrix(samples[f0], hand, c0).inverted() @ solver.pose_matrix(samples[f0], bone, c0)
    for f in range(f0 + 1, f1 + 1):
        cache = {}
        want = solver.pose_matrix(samples[f], hand, cache) @ grip
        basis = (solver.pose_matrix(samples[f], par, cache) @ solver.rest_rel[bone]).inverted() @ want
        loc, rot, _ = basis.decompose()
        samples[f][bone] = (loc, rot, None)


def bake_springs(solver, samples, loop, name):
    """Adds the spring bones to the solved samples (in place)."""
    n = len(samples)
    if not all(b in solver.rest_rel for b in SPRINGS):
        return
    statics = []
    for sample in samples:
        cache = {}
        statics.append({b: solver.pose_matrix(sample, solver.parent[b], cache) @ solver.rest_rel[b] for b in SPRINGS})
    dt, sub = 1.0 / FPS, 4
    wind = CLIP_WIND.get(name, 0.0)
    for b, cfg in SPRINGS.items():
        length = solver.length[b]
        w = 2 * math.pi * cfg["freq"]

        def target(f):
            m = statics[f][b]
            d = m.col[1].to_3d().normalized()
            if cfg["gravity"]:
                d = d.lerp(Vector((0, 0, -1)), cfg["gravity"]).normalized()
            if wind:
                back = (m.col[cfg["out"]].to_3d() if cfg["out"] is not None else -m.col[2].to_3d()).normalized()
                a = math.radians(cfg["wind"] * wind)
                d = (d * math.cos(a) + back * math.sin(a)).normalized()
            return m.translation + d * length

        # loops: four cycles to settle, the last one is kept; one extra step gives the start of the next cycle
        frames = list(range(n - 1)) * 4 + [0] if loop else list(range(n))
        x, v = target(frames[0]), Vector((0, 0, 0))
        tips = {}
        for k, f in enumerate(frames):
            t1 = target(f)
            h = statics[f][b].translation
            for _ in range(sub):
                a = (t1 - x) * (w * w) - v * (2 * cfg["damping"] * w)
                v = v + a * (dt / sub)
                x = h + (x + v * (dt / sub) - h).normalized() * length
            if not loop or len(frames) - n <= k < len(frames) - 1:
                tips[f] = x.copy()
        if loop:
            seam = x - tips[0]                  # next cycle start minus this cycle start
            tips = {f: tips[f] - seam * (f / (n - 1)) for f in tips}       # closes the cycle without a jump
            tips[n - 1] = tips[0]
        for f in range(n):
            m = statics[f][b]
            d = m.to_3x3().inverted() @ (tips[f] - m.translation)
            d.normalize()
            out = cfg["out"]
            # pitch towards the out axis (or the bone's local X/Z for a free antenna), roll sideways
            ax_p, ax_r = (out, 0) if out is not None else (2, 0)
            pitch = math.degrees(math.atan2(d[ax_p], d[1]))
            roll = math.degrees(math.atan2(d[ax_r], d[1]))
            pitch = max(cfg["swing"][0], min(cfg["swing"][1], pitch))
            roll = max(-cfg["side"], min(cfg["side"], roll))
            dl = [0.0, 1.0, 0.0]
            dl[ax_p] = math.tan(math.radians(pitch))
            dl[ax_r] = math.tan(math.radians(roll))
            samples[f][b] = (None, Vector((0, 1, 0)).rotation_difference(Vector(dl).normalized()), None)


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
    bake_attachment(solver, samples, name)
    bake_springs(solver, samples, loop, name)
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
GROUPS = ["Basis", "Phase1", "Phase2", "Phase3", "Phase4", "Phase5", "Phase6", "Phase7", "Phase8"]
GROUP_LABELS = {"Basis": "Basis", "Phase1": "Spielsysteme", "Phase2": "Werkzeuge & Interaktion", "Phase3": "Charakter",
                "Phase4": "Tänze", "Phase5": "Emotes", "Phase6": "Klettern & Hängen", "Phase7": "Tempo & Ducken",
                "Phase8": "Kriechen"}


# 1.16: categories by content for the viewer, ANIMATIONS.md and the game (the groups above are build phases and stay
# for contact sheets, QA packages and --groups). Every clip in exactly one category, in display order; IDs permanent.
CLIP_CATEGORIES = collections.OrderedDict([
    ("Move", ("Gehen & Laufen", ["Idle", "Walk", "WalkStart", "WalkStop", "Run", "RunFast", "Sprint", "TurnLeft",
                                 "TurnRight", "MudWalk", "SlopeSlide"])),
    ("Air", ("Springen & Fallen", ["Jump", "DoubleJump", "Fall", "Land"])),
    ("Crouch", ("Ducken & Kriechen", ["Crouch", "CrouchWalk", "CrouchDeep", "CrawlIdle", "Crawl"])),
    ("Climb", ("Klettern & Hängen", ["LedgeGrab", "HangIdle", "ShimmyLeft", "ShimmyRight", "HangSwing", "ClimbUp",
                                     "LadderUp", "LadderDown", "Vault"])),
    ("Interact", ("Interaktion & Werkzeuge", ["Interact", "PickUp", "Push", "PushStart", "PushStep", "ToolAim",
                                               "ToolUse", "Scan", "TerminalTyping", "PortalEnter"])),
    ("Damage", ("Treffer & Gefahren", ["Hit", "HitHeavy", "Electrocuted", "GasCough", "Death", "Respawn"])),
    ("Idle", ("Warten & Charakter", ["IdleScratch", "IdleFootTap", "IdleYawn", "Talk", "Listen", "EdgeBalance",
                                     "Celebrate"])),
    ("Emote", ("Gesten", ["Wave", "Bow", "Clap", "Think", "Point", "Shrug", "NodYes", "ShakeNo", "HatTip",
                          "FlagWave"])),
    ("Rest", ("Sitzen & Schlafen", ["SitDown", "SitIdle", "Sleep"])),
    ("Dance", ("Tänze", ["DanceBob", "DanceRobot", "DanceWave", "DanceShuffle", "DanceDisco", "DanceSpin",
                         "DanceMoonwalk", "DanceSway"])),
])


def clip_category(name):
    return next(c for c, (_, clips) in CLIP_CATEGORIES.items() if name in clips)


def check_categories():
    """Every registered clip in exactly one category, no category entry without a clip."""
    names = [a.name for a in ANIMS]
    listed = [c for _, clips in CLIP_CATEGORIES.values() for c in clips]
    missing = [n for n in names if n not in listed]
    extra = [c for c in listed if c not in names]
    twice = sorted({c for c in listed if listed.count(c) > 1})
    if missing or extra or twice:
        raise ValueError("CLIP_CATEGORIES: missing %s, unknown %s, twice %s" % (missing, extra, twice))


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
    (11, P(pelvis=(0, 0, 0.0), spine=(-2, 0, 0), head=(-10, 0, 0), uarm_LR=arm(-95, 34, 10), larm_LR=arm(-20),
           foot_LR=(0, 0.02, 0.0, 30), fing_LR=curl(10), eyes=(1.08, 0, 0.002)), "out"),
    (14, P(pelvis=(0, 0, 0.0), spine=(-4, 0, 0), head=(-10, 0, 0), uarm_LR=arm(-100, 40, 12), larm_LR=arm(-15),
           foot_LR=(0, 0.03, 0.05, 25), fing_LR=curl(5), eyes=(1.08, 0, 0.002))),
    (21, P(pelvis=(0, 0, 0.0), spine=(4, 0, 0), head=(-6, 0, 0), uarm_LR=arm(-95, 50, 10), larm_LR=arm(-24),
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
        head=(-32 - 1.0 * w, 0, 0),         # 1.14: the taller head would touch backpacks and wings at -36
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
    keys += [(53, with_(up, uarm_R=arm(-130, -30), larm_R=arm(-48))),
             (60, P(head=(2, -3, -4), uarm_R=arm(-20, 8), larm_R=arm(-20), eyes=(0.3, 0, 0))),
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
        uarm_L=arm(-56 + 30 * wave(t, 2), 78),
        uarm_R=arm(-56 + 30 * wave(t, 2, 0.5), 78),
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
    P(uarm_L=arm(-90, -12), larm_L=arm(-90), uarm_R=arm(0, 4), larm_R=arm(0), head=(0, 0, 30), eyes=(0.8, 0.01, 0)),
    P(uarm_L=arm(0, 85), larm_L=arm(0, 0, 0), uarm_R=arm(0, 85), larm_R=arm(0), head=(0, 0, 0), spine=(0, 0, 0),
      pelvis=(0, 0, -0.04), eyes=(1.0, 0, 0)),
    P(uarm_R=arm(-90, -12), larm_R=arm(-90), uarm_L=arm(0, 4), larm_L=arm(0), head=(0, 0, -30), eyes=(0.8, -0.01, 0)),
    P(uarm_LR=arm(-90, 0), larm_LR=arm(0), spine=(0, 0, 20), head=(0, 0, -20), pelvis=(0, 0, -0.03)),
    P(uarm_LR=arm(-90, 0), larm_LR=arm(0), spine=(0, 0, -20), head=(0, 0, 20), pelvis=(0, 0, -0.03)),
    P(uarm_L=arm(-40, 30), larm_L=arm(-90), uarm_R=arm(-40, 30), larm_R=arm(-90), head=(10, 0, 0),
      pelvis=(0, 0, -0.06), eyes=(0.5, 0, 0)),
    P(uarm_L=arm(0, 80), larm_L=arm(-90, 0, 0), uarm_R=arm(0, 4), larm_R=arm(0), head=(-8, 0, 25)),
    P(uarm_R=arm(0, 80), larm_R=arm(-90, 0, 0), uarm_L=arm(0, 4), larm_L=arm(0), head=(-8, 0, -25)),
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
    up = P(head=(0, -6, -6), spine=(0, -3, 0), uarm_R=arm(-12, 78), larm_R=arm(0, 85), hand_R=arm(0), fing_R=curl(0),
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
    return P(pelvis=(0.008 * wave(t, 1), 0, -0.016), spine=(4, 0, -4), head=(-15 + 2 * wave(t, 2), -4, 8 + 4 * wave(t, 1)),
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
    return P(pelvis=(0, 0.06, -0.334), pelvis_rot=(-32, 0, 0), spine=(42 + 2 * br, 0, 0), head=(50 + 3 * br, 0, 4),
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
        head=(-30, 0, -3 * c),              # 1.14: the taller head would touch backpacks and wings at -34
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


# ================================ Phase 8 (1.16): crawling under the 0.65 m crouch capsule ================================
# Belly crawl: the robot lies face down, forearms pull, toes push. The big head decides the height: chest close to the
# ground, head tilted a little forward. Game: crouch capsule 0.65 m (decision 13.10.2026), clips stay below 0.62 m.
CRAWL_BASE = dict(pelvis=(0, -0.12, -0.30), pelvis_rot=(84, 0, 0), spine=(4, 0, 0), head=(-26, 0, 0),
                  shoulder_LR=(0, 6, 0), hand_LR=arm(-10), fing_LR=curl(55), thumb_LR=curl(35))
CRAWL_HAND = (0.43, -0.72, 0.075)    # resting knuckle target beside the head (x out, y forward = minus, z)
CRAWL_POLE = (1.0, 0.0, 1.0)         # elbows out and up
CRAWL_FOOT = (0.03, 0.47, 0.004, 85)  # on the toes, legs stretched back
CRAWL_FRAMES = 40
CRAWL_REACH = 0.22                   # metres a hand pulls per stroke
CRAWL_STANCE = 0.55                  # share of the cycle a hand lies on the ground


def crawl_pose(**kw):
    p = dict(CRAWL_BASE)
    p.update(kw)
    return P(**p)


@anim("CrawlIdle", 60, True, "bäuchlings liegen und atmen (Kriechhaltung)", "Phase8")
def crawl_idle(f):
    t = f / 60.0
    br = math.sin(2 * math.pi * t)
    x, y, z = CRAWL_HAND
    return crawl_pose(pelvis=(0, -0.12, -0.30 + 0.004 * br), handik_LR=(x, y, z, *CRAWL_POLE, 1.0),
                      foot_LR=CRAWL_FOOT, eyes=(1.0, 0, 0.004))


def crawl_hand(u):
    """Knuckle target of one hand over the stroke (u = 0..1): pulls back on the ground, then reaches forward."""
    x, y0, z0 = CRAWL_HAND
    front = y0 - CRAWL_REACH / 2
    if u < CRAWL_STANCE:
        return (x, front + CRAWL_REACH * u / CRAWL_STANCE, z0)
    s = (u - CRAWL_STANCE) / (1 - CRAWL_STANCE)
    return (x, front + CRAWL_REACH * (1 - ease(s)), z0 + 0.07 * math.sin(math.pi * s))


@anim("Crawl", CRAWL_FRAMES, True, "robben: Unterarme ziehen im Wechsel, Fußspitzen schieben (Grundtempo ca. 0,3 m/s)",
      "Phase8", "in-place; Animator-Tempo = Geschwindigkeit / naturalSpeed (gemessen an den Händen)")
def crawl(f):
    t = f / CRAWL_FRAMES
    c = math.cos(2 * math.pi * t)
    hands = {s: crawl_hand((t + off) % 1.0) for s, off in (("L", 0.0), ("R", 0.5))}
    ox, oy, lift, pitch = CRAWL_FOOT
    # each foot pushes with the opposite hand (the left foot while the right hand pulls)
    feet = {s: (ox, oy + 0.04 * math.cos(2 * math.pi * (t + off)), lift + 0.012 * max(0.0, math.sin(2 * math.pi * (t + off))),
                pitch) for s, off in (("L", 0.5), ("R", 0.0))}
    return crawl_pose(pelvis=(0.010 * c, -0.12, -0.30 + 0.004 * math.cos(4 * math.pi * t)),
                      pelvis_rot=(84, 0, -4 * c), spine=(4, 0, 5 * c), head=(-26, 0, -4 * c),
                      handik_L=(*hands["L"], *CRAWL_POLE, 1.0), handik_R=(*hands["R"], *CRAWL_POLE, 1.0),
                      foot_L=feet["L"], foot_R=feet["R"], eyes=(1.0, 0.005 * c, 0.004))


# ---------------------------------- 1.12 type emotes ----------------------------------

@anim("HatTip", 56, False, "Hut lüften mit Verbeugung (Typ Gentleman; der Kopf-Sockel liegt in der Hand)", "Phase5")
def hat_tip(f):
    # the arms cannot reach the crown of the head: the hat lifts and slides towards the hand that comes up beside the
    # head, then rides on the hand (ATTACHMENTS) through the bow and back
    lifted = (0, -24, -0.210, -0.053)    # the right brim edge rests on the fingers (1.14: head wider and taller)
    reach = P(head=(-4, 6, 4), spine=(-2, 0, -3), hat=lifted, fing_R=curl(55), thumb_R=curl(45),
              handik_R=(0.385, -0.040, 1.180, 1.0, 0.3, -0.2, 1.0), uarm_L=arm(4, 6))
    sweep = P(pelvis=(0, 0.03, -0.022), pelvis_rot=(6, 0, 0), spine=(20, 0, 0), head=(12, 0, 0), hat=lifted,
              fing_R=curl(55), thumb_R=curl(45), handik_R=(0.450, -0.200, 0.900, 1.0, 0.3, -0.2, 1.0),
              uarm_L=arm(10, 4), larm_L=arm(-12), eyes=(0.35, 0, -0.004))
    return keyed(f, [(0, STAND), (HAT_GRAB, reach), (22, sweep), (34, sweep), (44, reach), (HAT_RELEASE, reach),
                     (56, STAND)])


@anim("FlagWave", 60, False, "Fahne schwenken (Typ King; Gegenstand in der rechten Hand bleibt sichtbar)", "Phase5")
def flag_wave(f):
    t = f / 60.0
    env = ease(min(1.0, f / 10.0)) * ease(min(1.0, (60 - f) / 12.0))
    sw = math.sin(2 * math.pi * 3 * t) * env
    return P(spine=(-4 * env, 0, -6 * env), head=(-10 * env, -4 * env, -12 * env),
             uarm_R=arm(-100 * env, 22 * env + 10 * sw), larm_R=arm(-8 * env, 0, 32 * sw), hand_R=arm(0, 0, 0),
             fing_R=curl(15 + 50 * env), thumb_R=curl(10 + 40 * env), uarm_L=arm(4 * env, 10 * env),
             eyes=(1.0 - 0.1 * env, -0.004 * env, 0.004 * env))


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
        (("Celebrate", "DanceBob", "DanceShuffle", "DanceDisco", "DanceSway", "Wave", "Talk", "NodYes", "HatTip",
          "FlagWave"), "Happy"),
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
        "name": a.name, "group": a.group, "category": clip_category(a.name), "frames": a.frames, "fps": FPS,
        "loop": a.loop,
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
    by_name = {a.name: a for a in ANIMS}
    for cat, (label, names) in CLIP_CATEGORIES.items():
        clips = [by_name[n] for n in names if n in by_name]
        if not clips:
            continue
        lines += ["## %s (`%s`)" % (label, cat), "",
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


def build_animations(arm_ob, quiet=False):
    solver = Solver(arm_ob)
    actions = {}
    for a in ANIMS:
        act, slot = write_action(arm_ob, solver, a.name, a.frames, a.fn, a.loop)
        actions[a.name] = act
        LOG.log(logging.DEBUG if quiet else logging.INFO, "  action %-15s %3d frames  loop=%-5s %s  (%s)", a.name, a.frames, a.loop, a.group, a.desc)
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
    co = np.empty(len(me.vertices) * 3, dtype=np.float32)     # the native type: a plain copy, no conversion
    me.vertices.foreach_get("co", co)
    ev.to_mesh_clear()
    mw = np.array(ob.matrix_world)
    co = co.astype(np.float64).reshape(-1, 3) @ mw[:3, :3].T + mw[:3, 3]
    return co


class Topology:
    """Per vertex group: its vertex indices and its polygons in local indices - computed once per mesh. order/starts
    list all groups' vertices back to back, so the bounding boxes of all groups are two numpy calls per frame."""

    def __init__(self, groups):
        self.groups = {}
        for g, polys in groups.items():
            idx = sorted({i for p in polys for i in p})
            remap = {o: n for n, o in enumerate(idx)}
            self.groups[g] = (np.array(idx, dtype=np.int64), [[remap[i] for i in p] for p in polys])
        self.index = {g: i for i, g in enumerate(self.groups)}
        self.order = np.concatenate([idx for idx, _ in self.groups.values()])
        self.starts = np.cumsum([0] + [len(idx) for idx, _ in self.groups.values()])[:-1]

    def pair_index(self, pairs):
        return (np.array([self.index[a] for a, _ in pairs], dtype=np.int64),
                np.array([self.index[b] for _, b in pairs], dtype=np.int64))


class GroupTrees:
    """BVH trees of the vertex groups of one mesh state, built only when a pair needs them. Groups whose
    bounding boxes are apart cannot overlap, so the box test answers most pairs without a tree (same result
    as testing every pair; the margin covers float32 rounding inside the trees)."""

    MARGIN = 1e-5

    def __init__(self, co, topo):
        self.co, self.topo, self.trees = co, topo, {}
        pts = co[topo.order]
        self.lo = np.minimum.reduceat(pts, topo.starts, axis=0) - self.MARGIN
        self.hi = np.maximum.reduceat(pts, topo.starts, axis=0) + self.MARGIN

    def tree(self, g):
        t = self.trees.get(g)
        if t is None:
            idx, polys = self.topo.groups[g]
            t = self.trees[g] = BVHTree.FromPolygons(self.co[idx].tolist(), polys)
        return t

    def boxes_touch(self, ia, ib):
        """Box test for many pairs at once (index arrays): False where the pair cannot overlap."""
        return ~((self.lo[ia] > self.hi[ib]).any(axis=1) | (self.lo[ib] > self.hi[ia]).any(axis=1))

    def overlap(self, a, b, boxes_checked=False):
        if not boxes_checked:
            ia, ib = self.topo.index[a], self.topo.index[b]
            if (self.lo[ia] > self.hi[ib]).any() or (self.lo[ib] > self.hi[ia]).any():
                return False
        return bool(self.tree(a).overlap(self.tree(b)))


# Contacts that are intended: fabric accessories may be squeezed by the drooping head.
ACCEPTED_OVERLAPS = {("*", "Head", "Socket_Neck"), ("*", "Eyes", "Socket_Neck"),
                     # held items are gripped by the fingers
                     ("*", "Fingers_R", "Prop_R"), ("*", "Prop_R", "Thumb_R"),
                     ("*", "Fingers_L", "Prop_L"), ("*", "Prop_L", "Thumb_L"),
                     # the hat rides on the right hand
                     ("HatTip", "Fingers_R", "Socket_Head"), ("HatTip", "Hand_R", "Socket_Head"),
                     ("HatTip", "Socket_Head", "Thumb_R")}


# Clips in which a held item (Prop_R) is hidden by the game: climbing, hanging, thinking with the hand at the chin.
HIDE_HELD_ITEM = {"Think", "LedgeGrab", "HangIdle", "ShimmyLeft", "ShimmyRight", "HangSwing", "ClimbUp", "LadderUp",
                  "LadderDown", "Vault", "PickUp", "TerminalTyping", "Clap", "Wave", "EdgeBalance", "HatTip", "CrawlIdle",
                  "Crawl"}
# Joints that open on purpose: the hat leaves the head in "HatTip".
ACCEPTED_DETACH = {("HatTip", "Socket_Head", "Head")}


def type_emotes():
    """Clips that belong to one robot type (ROBOT_TYPES[...]["emote"]): checked and played only with that type."""
    names = {a.name for a in ANIMS}         # a --groups filter may have dropped them
    return {spec["emote"]: name for name, spec in ROBOT_TYPES.items() if spec.get("emote") in names}


def run_qa(ob, arm_ob, actions, label="Classic", quiet=False, clips=None):
    """QA of one mesh over the given clips (default: all, in ANIMS order): rest-pose baseline once, then every
    frame of every clip. Returns {"rest_min_z", "rest_height", "actions": {clip: entry}}."""
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
    topo = Topology(groups)
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
    LOG.log(logging.DEBUG if quiet else logging.INFO, "  [%s] rest pose: height %.3f m, min z %.4f m, baseline "
            "joint overlaps %d", label, qa["rest_height"], qa["rest_min_z"], len(baseline))
    vgroup = np.empty(len(ob.data.vertices), dtype=object)
    for g, polys in groups.items():
        for poly in polys:
            vgroup[poly] = g
    foot_groups = {"L": "Foot_L", "R": "Foot_R"}
    foot_idx = {s: sorted({i for p in groups[foot_groups[s]] for i in p}) for s in foot_groups}
    wanted = None if clips is None else set(clips)
    for name, frames, loop, fn, desc, *_ in ANIMS:
        if wanted is not None and name not in wanted:
            continue
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
                       if ((min(child, par), max(child, par)) in baseline or (child, par) in baseline or
                           (par, child) in baseline) and (name, child, par) not in ACCEPTED_DETACH]
        pa, pb = topo.pair_index(pairs)
        ja, jb = topo.pair_index(joint_pairs)
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
            for k in np.flatnonzero(trees.boxes_touch(pa, pb)):
                a, b = pairs[k]
                if trees.overlap(a, b, boxes_checked=True):
                    new_overlaps.setdefault("%s/%s" % (a, b), []).append(f)
            touch = trees.boxes_touch(ja, jb)
            for k, (child, par) in enumerate(joint_pairs):
                if not (touch[k] and trees.overlap(child, par, boxes_checked=True)):
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
        log_qa_entry(label, name, entry, quiet)
    ad.action = saved
    return qa


def qa_problem(entry):
    return entry["min_z"] < -0.006 or bool(entry["new_overlaps"]) or bool(entry["detached_joints"])


def log_qa_entry(label, name, entry, quiet=False):
    loop_err = entry["loop_error"]
    lvl = logging.DEBUG if quiet else logging.INFO
    if qa_problem(entry) or (entry["loop"] and loop_err > 1e-4):
        lvl = logging.WARNING
    LOG.log(lvl, "  QA[%s] %-9s min_z=%+.4f (%s) overlaps=%s detached=%s flight=%d loop_err=%s speed=%s", label, name,
            entry["min_z"], entry["min_z_at"], entry["new_overlaps"] or "-", entry["detached_joints"] or "-",
            entry["flight_frames"], "-" if loop_err is None else "%.1e" % loop_err, entry["stance_speed_mps"])


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


# Servo sound events (1.13): a joint group starts a movement when its summed angular speed rises above SERVO_ON and
# to SERVO_RISE times the lowest speed since the last movement; the movement ends when the speed falls below
# SERVO_FALL of its peak (so every step or wave gets its own event). Intensity = peak / SERVO_FULL (0..1).
SERVO_GROUPS = collections.OrderedDict([
    ("Head", ("Head",)), ("Torso", ("Pelvis", "Spine")),
    ("Arm_L", ("Shoulder_L", "UpperArm_L", "LowerArm_L", "Hand_L")),
    ("Arm_R", ("Shoulder_R", "UpperArm_R", "LowerArm_R", "Hand_R")),
    ("Leg_L", ("UpperLeg_L", "LowerLeg_L", "Foot_L")), ("Leg_R", ("UpperLeg_R", "LowerLeg_R", "Foot_R")),
])
SERVO_ON, SERVO_RISE, SERVO_FALL, SERVO_FULL = 90.0, 1.8, 0.6, 900.0     # degrees per second, factors


def servo_events(rotations, loop):
    """rotations: per frame {bone: local quaternion}. Returns [[frame, group, intensity, frames], ...] by frame."""
    events = []
    for group, bones in SERVO_GROUPS.items():
        speed = [0.0]
        for q0, q1 in zip(rotations, rotations[1:]):
            speed.append(sum(math.degrees(q0[b].rotation_difference(q1[b]).angle) for b in bones) * FPS)
        if loop and len(speed) > 1:
            speed[0] = speed[-1]            # frame 0 continues the last frame of the cycle
        start, peak, low = None, 0.0, speed[0]
        for f, v in enumerate(speed):
            if start is None:
                low = min(low, v)
                if v > SERVO_ON and v > SERVO_RISE * low:
                    start, peak = f, v
            else:
                peak = max(peak, v)
                if v < SERVO_FALL * peak:
                    events.append([start, group, round(min(1.0, peak / SERVO_FULL), 2), f - start])
                    start, low = None, v
        if start is not None:
            events.append([start, group, round(min(1.0, peak / SERVO_FULL), 2), len(speed) - start])
    return sorted(events)


HAND_GAITS = {"Crawl"}      # in-place clips whose natural speed comes from the hands on the ground
HAND_CONTACT = 0.02         # metres: a hand counts as lying on the ground (knuckles and palm are rounded)


def measure_clips(ob, arm_ob, actions):
    """Measured game data per clip: root motion, foot contacts, footsteps, natural speed, height, hand grips."""
    scene = bpy.context.scene
    groups = group_faces(ob)
    foot_idx = {s: sorted({i for p in groups["Foot_" + s] for i in p}) for s in ("L", "R")}
    hand_idx = {s: sorted({i for g in ("Hand_" + s, "Fingers_" + s) for p in groups[g] for i in p}) for s in ("L", "R")}
    root_pb = arm_ob.pose.bones["Root"]
    grip_pb = {s: arm_ob.pose.bones["Fingers_" + s] for s in ("L", "R")}
    mw = arm_ob.matrix_world
    root_rest = arm_ob.data.bones["Root"].matrix_local.to_3x3().inverted()
    out = {}
    for a in ANIMS:
        assign_action(arm_ob, actions[a.name])
        heights, contact, speeds, hands, roots = [], {"L": [], "R": []}, [], {"L": [], "R": []}, []
        rotations = []
        prev = None
        for f in range(a.frames + 1):
            scene.frame_set(f)
            rotations.append({b: arm_ob.pose.bones[b].rotation_quaternion.copy()
                              for bones in SERVO_GROUPS.values() for b in bones})
            co = evaluated_coords(ob)
            heights.append(float(co[:, 2].max() - max(0.0, co[:, 2].min())))
            cur = {}
            for s in ("L", "R"):
                pts = co[foot_idx[s]]
                on = float(pts[:, 2].min()) < 0.006
                contact[s].append(on)
                cur[s] = (on, pts.mean(axis=0))
                if prev and on and prev[s][0] and a.name not in HAND_GAITS:
                    speeds.append(float(np.linalg.norm((cur[s][1] - prev[s][1])[:2])) * FPS)
                if a.name in HAND_GAITS:        # crawling: the hands lying on the ground carry the body
                    hp = co[hand_idx[s]]
                    cur["h" + s] = (float(hp[:, 2].min()) < HAND_CONTACT, hp.mean(axis=0))
                    if prev and cur["h" + s][0] and prev["h" + s][0]:
                        speeds.append(float(np.linalg.norm((cur["h" + s][1] - prev["h" + s][1])[:2])) * FPS)
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
        entry["servo"] = servo_events(rotations, a.loop)
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


def preview_quality(exact):
    """exact: the look of the deliverables (additive eyes, screen-space ray tracing). Otherwise the fast preview
    look for the many small renders (skins, types, animation sheets, GIFs): opaque eyes, no ray tracing."""
    set_fast_eyes(not exact)
    bpy.context.scene.eevee.use_raytracing = exact


FAST_EYE_NODES = ("FastEyes", "FastEyesBSDF", "FastVisor", "FastEyesMask", "FastEyesTransparent")


def set_fast_eyes(on):
    """The additive eye screen (Transparent + Emission, blended) is the only forward-rendered surface. EEVEE then
    runs a second shadow update per sample and waits for a GPU read-back each time (ShadowModule::
    shadow_update_finished) - measured: most of a preview render. Fast eyes are an opaque surface with the visor's
    shading plus the same emission, cut out (dithered, binary) where the atlas is black: deferred only, about half
    the render time and the same picture at preview size. The nodes are removed again before anything is saved."""
    for mat in bpy.data.materials:
        if not mat.name.startswith("M_RobotEyes") or mat.node_tree is None:
            continue
        nt = mat.node_tree
        out = nt.nodes["Output"]
        fast = nt.nodes.get("FastEyes")
        if on and fast is None:
            em = next(n for n in nt.nodes if n.type == "EMISSION")
            tex = next(n for n in nt.nodes if n.type == "TEX_IMAGE")
            visor = nt.nodes.new("ShaderNodeRGB")
            visor.name = "FastVisor"
            visor.outputs[0].default_value = (*PALETTE_DEFAULTS["visor"], 1.0)
            bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
            bsdf.name = "FastEyesBSDF"
            nt.links.new(visor.outputs[0], bsdf.inputs["Base Color"])
            bsdf.inputs["Roughness"].default_value = VISOR_ROUGHNESS
            bsdf.inputs["Metallic"].default_value = VISOR_METALLIC
            nt.links.new(em.inputs["Color"].links[0].from_socket, bsdf.inputs["Emission Color"])
            bsdf.inputs["Emission Strength"].default_value = em.inputs["Strength"].default_value
            mask = nt.nodes.new("ShaderNodeMath")
            mask.name = "FastEyesMask"
            mask.operation = "GREATER_THAN"
            mask.inputs[1].default_value = 0.002
            nt.links.new(tex.outputs["Color"], mask.inputs[0])
            tr = nt.nodes.new("ShaderNodeBsdfTransparent")
            tr.name = "FastEyesTransparent"
            fast = nt.nodes.new("ShaderNodeMixShader")
            fast.name = "FastEyes"
            nt.links.new(mask.outputs[0], fast.inputs[0])
            nt.links.new(tr.outputs[0], fast.inputs[1])
            nt.links.new(bsdf.outputs[0], fast.inputs[2])
            nt.links.new(fast.outputs[0], out.inputs["Surface"])
            mat.surface_render_method = "DITHERED"
        elif not on and fast is not None:
            add = next(n for n in nt.nodes if n.type == "ADD_SHADER")
            nt.links.new(add.outputs[0], out.inputs["Surface"])
            for name in FAST_EYE_NODES:
                nt.nodes.remove(nt.nodes[name])
            mat.surface_render_method = "BLENDED"


def show_tool(visible):
    tool = bpy.data.objects.get("Preview_Tool")
    if tool is not None:
        tool.hide_render = not visible


def rest_pose(arm_ob):
    arm_ob.animation_data.action = None
    for pb in arm_ob.pose.bones:
        pb.rotation_quaternion = (1, 0, 0, 0)
        pb.location = (0, 0, 0)
        pb.scale = (1, 1, 1)


# Render cache (1.13.3): every preview image goes through render_still(). Its key is a fingerprint of what the render
# sees - visible objects (mesh, transform, pose), their materials as node graphs with all values, image files by
# content, camera, lights, world, compositor and render settings. Unchanged scene = the stored image, whatever code
# produced the scene; a changed skin colour re-renders only its own images. --no-cache renders everything.
RENDER_CACHE = {"dir": None, "hits": 0, "renders": 0}
_FILE_DIGESTS = {}
_MESH_DIGESTS = {}
_SKIP_PROPS = {"rna_type", "filepath", "frame_path", "gi_cache_info", "name_full", "session_uid", "is_evaluated",
               "original", "users", "use_fake_user", "is_dirty", "is_missing", "is_embedded_data", "tag",
               "is_runtime_data", "is_editmode", "is_library_indirect", "library_weak_reference", "id_type",
               # performance only - the main process renders with all threads, the workers with two
               "threads", "threads_mode", "use_persistent_data", "use_lock_interface", "preview_pixel_size",
               # per-process identifiers and UI state of modifiers/nodes
               "persistent_uid", "is_active", "show_expanded", "is_override_data", "use_pin_to_last", "select"}


def _rna_values(obj, out):
    """Plain properties (numbers, flags, enums, strings) of an RNA struct, in a stable order."""
    for p in obj.bl_rna.properties:
        ident = p.identifier
        if ident in _SKIP_PROPS or p.type not in {"BOOLEAN", "INT", "FLOAT", "STRING", "ENUM"}:
            continue
        try:
            v = getattr(obj, ident)
        except AttributeError:
            continue
        if p.type == "FLOAT":
            v = tuple(round(float(x), 5) for x in v) if getattr(p, "array_length", 0) else round(float(v), 5)
        elif p.type in {"BOOLEAN", "INT"} and getattr(p, "array_length", 0):
            v = tuple(v)
        elif p.type == "ENUM" and p.is_enum_flag:
            v = tuple(sorted(v))
        out.append((ident, v))
    return out


def _file_digest(path):
    path = bpy.path.abspath(path)
    try:
        st = os.stat(path)
    except OSError:
        return "missing:" + path
    key = (path, st.st_mtime_ns, st.st_size)
    if key not in _FILE_DIGESTS:
        with open(path, "rb") as fh:
            _FILE_DIGESTS[key] = hashlib.sha1(fh.read()).hexdigest()
    return _FILE_DIGESTS[key]


def _tree_digest(tree, h, seen):
    # by identity: every material's embedded tree is called "Shader Nodetree" - a name would skip all but the first
    if tree is None or tree.as_pointer() in seen:
        return
    seen.add(tree.as_pointer())
    for nd in sorted(tree.nodes, key=lambda n: n.name):
        h.update(repr((nd.bl_idname, nd.name, _rna_values(nd, []))).encode())
        for sock in list(nd.inputs) + list(nd.outputs):
            if not sock.is_linked and hasattr(sock, "default_value"):
                v = sock.default_value
                if isinstance(v, float):
                    v = round(v, 5)
                elif hasattr(v, "__len__") and not isinstance(v, str):      # vectors, colours (menus are strings)
                    v = tuple(round(float(x), 5) for x in v)
                h.update(repr((sock.identifier, v)).encode())
        img = getattr(nd, "image", None)
        if img is not None:
            if img.filepath:
                content = _file_digest(img.filepath)
            else:                               # generated in memory: its pixels are the content
                px = np.empty(img.size[0] * img.size[1] * img.channels, dtype=np.float32)
                img.pixels.foreach_get(px)
                content = hashlib.sha1(px.tobytes()).hexdigest()
            h.update(repr((img.colorspace_settings.name, img.alpha_mode, tuple(img.size), content)).encode())
        _tree_digest(getattr(nd, "node_tree", None), h, seen)
    for ln in tree.links:
        h.update(repr((ln.from_node.name, ln.from_socket.identifier, ln.to_node.name, ln.to_socket.identifier,
                       ln.is_muted)).encode())


def _mesh_digest(me):
    """Coordinates every time (cheap, catches in-place edits); the canonical face list memoised per datablock."""
    key = (me.as_pointer(), len(me.vertices), len(me.polygons))
    if key not in _MESH_DIGESTS:
        _MESH_DIGESTS[key] = hashlib.sha1(repr(sorted(tuple(sorted(p.vertices)) for p in me.polygons)).encode())
    co = np.empty(len(me.vertices) * 3, dtype=np.float32)
    me.vertices.foreach_get("co", co)
    h = _MESH_DIGESTS[key].copy()
    h.update(co.tobytes())
    return h.hexdigest()


class _DumpHash:
    lines = []

    def update(self, b):
        _DumpHash.lines.append(b.decode(errors="replace"))


_ACTION_DIGESTS = {}


def _pose_key(arm, scene):
    """The pose by its inputs: action keys + frame (evaluated matrices differ by ~1e-7 between processes and flip
    roundings); without an action the pose values themselves (rest pose: exact)."""
    ad = arm.animation_data
    if ad and ad.action:
        act = ad.action
        if act.name not in _ACTION_DIGESTS:
            _ACTION_DIGESTS[act.name] = action_digest(act)
        manual = [(pb.name, tuple(pb.location), tuple(pb.rotation_quaternion), tuple(pb.scale))
                  for pb in arm.pose.bones if not _bone_animated(act, pb.name)]
        return repr((act.name, _ACTION_DIGESTS[act.name], scene.frame_current, round(scene.frame_subframe, 3),
                     hashlib.sha1(repr(manual).encode()).hexdigest()))
    return repr([(pb.name, tuple(round(x, 5) for x in (*pb.location, *pb.rotation_quaternion, *pb.scale)))
                 for pb in arm.pose.bones])


_ANIMATED_BONES = {}


def _bone_animated(act, bone):
    if act.name not in _ANIMATED_BONES:
        names = set()
        for slot in act.slots:
            cb = anim_utils.action_get_channelbag_for_slot(act, slot)
            for fc in (cb.fcurves if cb else []):
                if fc.data_path.startswith('pose.bones["'):
                    names.add(fc.data_path.split('"')[1])
        _ANIMATED_BONES[act.name] = names
    return bone in _ANIMATED_BONES[act.name]


def scene_digest(scene, h=None):
    h = h or hashlib.sha1()
    bpy.context.view_layer.update()        # matrix_world of moved lights/cameras is stale until the depsgraph runs
    for part in (scene.render, scene.render.image_settings, scene.eevee, scene.view_settings, scene.display_settings):
        h.update(repr(_rna_values(part, [])).encode())
    # no frame number: what a frame shows is the pose (below); stills keep whatever frame the last package left
    h.update(repr(scene.camera.name if scene.camera else None).encode())
    seen = set()
    _tree_digest(scene.world.node_tree if scene.world else None, h, seen)
    if scene.render.use_compositing:
        _tree_digest(getattr(scene, "compositing_node_group", None), h, seen)
    poses = {}
    for ob in sorted(scene.objects, key=lambda o: o.name):
        hidden = ob.hide_render or any(c.hide_render for c in ob.users_collection)
        h.update(repr((ob.name, hidden)).encode())
        if hidden:
            continue
        h.update(repr((ob.type, tuple(round(x, 5) for row in ob.matrix_world for x in row))).encode())
        if ob.type == "MESH":
            h.update(_mesh_digest(ob.data).encode())
            h.update(repr([(m.type, getattr(m, "object", None) and m.object.name, _rna_values(m, []))
                           for m in ob.modifiers]).encode())
            arm = next((m.object for m in ob.modifiers if m.type == "ARMATURE" and m.object), None)
            if arm is not None:
                if arm.name not in poses:
                    poses[arm.name] = _pose_key(arm, scene)
                h.update(poses[arm.name].encode())
            for slot in ob.material_slots:
                if slot.material:
                    _tree_digest(slot.material.node_tree, h, seen)
                    h.update(repr(_rna_values(slot.material, [])).encode())
        elif ob.type == "LIGHT":
            h.update(repr(_rna_values(ob.data, [])).encode())
        elif ob.type == "CAMERA" and ob == scene.camera:    # only what the camera type uses (ortho keeps a stale lens)
            c = ob.data
            optics = (round(c.ortho_scale, 5),) if c.type == "ORTHO" else \
                (round(c.lens, 5), round(c.sensor_width, 5), round(c.sensor_height, 5), c.sensor_fit)
            h.update(repr((c.type, optics, round(c.shift_x, 5), round(c.shift_y, 5), round(c.clip_start, 5),
                           round(c.clip_end, 5), c.dof.use_dof,
                           (round(c.dof.focus_distance, 5), round(c.dof.aperture_fstop, 5),
                            c.dof.focus_object.name if c.dof.focus_object else None) if c.dof.use_dof else None)).encode())
    return h.hexdigest() if hasattr(h, "hexdigest") else None


def render_still(path):
    scene = bpy.context.scene
    scene.render.filepath = path
    folder = RENDER_CACHE["dir"]
    if folder:
        key = scene_digest(scene) + os.path.splitext(path)[1]
        cached = os.path.join(folder, key)
        hit = os.path.exists(cached)
        dump = os.environ.get("ROBOT_RENDER_DIGEST_DUMP")      # debugging: write the fingerprint parts of one image
        if dump and dump in os.path.basename(path):
            _DumpHash.lines = []
            scene_digest(scene, _DumpHash())
            with open(os.path.join(folder, "dump_%s_%d.txt" % (os.path.basename(path), os.getpid())), "w") as fh:
                fh.write("\n".join(_DumpHash.lines))
        LOG.debug("render cache %s %s %s", "hit" if hit else "miss", key[:12], os.path.basename(path))
        if hit:
            shutil.copyfile(cached, path)
            os.utime(cached)
            RENDER_CACHE["hits"] += 1
            return path
    bpy.ops.render.render(write_still=True)
    RENDER_CACHE["renders"] += 1
    if folder:
        os.makedirs(folder, exist_ok=True)
        tmp = "%s.%d.tmp" % (cached, os.getpid())
        shutil.copyfile(path, tmp)
        os.replace(tmp, cached)
    return path


def render_views(cam, out_dir, prefix="", res=1100):
    scene = bpy.context.scene
    scene.eevee.taa_render_samples = preview_samples(64)
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
        paths[name] = render_still(os.path.join(out_dir, "%s%s.png" % (prefix, name)))
        LOG.info("  rendered %s", paths[name])
    # small iso shot to judge readability at game distance
    scene.render.resolution_x = scene.render.resolution_y = 360
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = 4.5
    cam.location = (-4.5, -4.5, 4.1)
    look_at(cam, (0, 0, 0.62))
    paths["distance"] = render_still(os.path.join(out_dir, "%sdistance.png" % prefix))
    return paths


SHEET_VIEWS = (("side", (5.0, -1.2, 0.9)), ("iso", (-4.0, -4.0, 3.6)))


def sheet_picks(frames, loop):
    return [round(i * frames / (6 if loop else 5)) for i in range(6)]


def render_anim_rows(cam, arm_ob, actions, out_dir, name):
    """Six frames of one clip from the side and from iso, each view as one labelled contact-sheet row (ImageMagick).
    Returns {view: row file}; the frames themselves are deleted."""
    scene = bpy.context.scene
    scene.eevee.taa_render_samples = preview_samples(12)     # 828 small frames: 12 samples look the same as 24
    scene.render.resolution_x = scene.render.resolution_y = 300
    frame_dir = os.path.join(out_dir, "anim_frames")
    os.makedirs(frame_dir, exist_ok=True)
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = 1.75
    a = next(a for a in ANIMS if a.name == name)
    assign_action(arm_ob, actions[name])
    picks = sheet_picks(a.frames, a.loop)
    rows = {}
    for view, loc in SHEET_VIEWS:
        cam.location = loc
        look_at(cam, (0, -0.05, 0.6))
        files = []
        for f in picks:
            scene.frame_set(f)
            files.append(render_still(os.path.join(frame_dir, "%s_%s_%03d.png" % (name, view, f))))
        rows[view] = os.path.join(frame_dir, "row_%s_%s.png" % (view, name))
        args = ["montage"]
        for p, fr in zip(files, picks):
            args += ["-label", "%s  f%d" % (name, fr), p]
        magick(*(args + ["-tile", "%dx1" % len(files), "-geometry", "+3+3", "-background", "#10161d", "-fill",
                         "#d8e6f0", "-pointsize", "15", rows[view]]))
        for p in files:
            os.remove(p)
    LOG.info("  anim frames %s", name)
    return rows


def magick(*args):
    make_textures.run(make_textures.find_magick(), args)


def build_contact_sheets(rows, out_dir):
    """ImageMagick: per group and view, the clip rows stacked (rows: clip -> {view: row file}), in parallel."""
    tasks = []
    for group, view in [(g, v) for g in GROUPS for v in ("side", "iso")]:
        files = [rows[a.name][view] for a in ANIMS if a.group == group and a.name in rows]
        if files:
            tasks.append((files, os.path.join(out_dir, "animations_%s_%s.png" % (group, view))))

    def append(task):
        files, sheet = task
        magick(*(files + ["-background", "#10161d", "-append", sheet]))
        return sheet

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(8, os.cpu_count() or 4))) as pool:
        for sheet in pool.map(append, tasks):
            LOG.info("  contact sheet %s", sheet)
    for views in rows.values():
        for p in views.values():
            os.remove(p)


def montage_sheet(tiles, sheet, pointsize):
    """tiles: [(label, file)] in sheet order; the tile files are deleted afterwards."""
    args = ["montage"]
    for label, p in tiles:
        args += ["-label", label, p]
    magick(*(args + ["-tile", "4x", "-geometry", "+3+3", "-background", "#10161d", "-fill", "#d8e6f0",
                     "-pointsize", str(pointsize), sheet]))
    for _, p in tiles:
        os.remove(p)
    LOG.info("  sheet %s", sheet)


SEE_THROUGH_SPECIALS = ("Glass", "Hologram")


def set_special(nt, sp, form=None):
    """Preview: switch the special-material nodes (preview_special_nodes) to a skin's special (None = off) and the
    holes of the form "Perforated" on or off."""
    nt.nodes["PerfW"].outputs[0].default_value = float(form == "Perforated")
    nt.nodes["PerfCells"].outputs[0].default_value = PERFORATION.get("cells", 50.0)
    for sid in SPECIALS:
        nt.nodes["SpecialW_" + sid].outputs[0].default_value = float(bool(sp) and sp["id"] == sid)
    bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
    for link in list(bsdf.inputs["Alpha"].links):
        nt.links.remove(link)
    bsdf.inputs["Alpha"].default_value = 1.0
    if sp and sp["id"] in SEE_THROUGH_SPECIALS or form == "Perforated":     # else EEVEE's opaque path
        nt.links.new(nt.nodes["SpecialAlpha"].outputs[0], bsdf.inputs["Alpha"])
    if sp:
        nt.nodes["SpecialA"].outputs[0].default_value = (*sp["color"], 1.0)
        nt.nodes["SpecialB"].outputs[0].default_value = (*sp["color2"], 1.0)
        nt.nodes["SpecialStrength"].outputs[0].default_value = sp["strength"]


def set_look(ob, tex_dir, skin=DEFAULT_SKIN, expression="Normal", display="Status"):
    """Preview look on the combined mesh: baked skin textures + eye/display atlas cell + colours."""
    pal_ = SKINS[skin]
    for mat in ob.data.materials:
        nt = mat.node_tree
        if mat.name.startswith("M_RobotEyes"):
            style = make_textures.EXPRESSION_STYLES.get(expression, {})
            eye = [c * style.get("glow", 1.0) for c in style.get("tint", pal_["eye"])]
            nt.nodes["EyeColor"].outputs[0].default_value = (*eye, 1.0)
            if "FastVisor" in nt.nodes:
                nt.nodes["FastVisor"].outputs[0].default_value = (*pal_["visor"], 1.0)
            set_atlas_cell(mat, "eyes", EXPRESSION_INDEX[expression])
        elif mat.name.startswith("M_RobotDisplay"):
            nt.nodes["DisplayColor"].outputs[0].default_value = (*pal_["screen"], 1.0)
            set_atlas_cell(mat, "displays", DISPLAY_INDEX[DISPLAY_STILLS.get(display, display)])
        elif mat.name.startswith("M_Robot"):
            if "TintColor" in nt.nodes:
                nt.nodes["TintColor"].outputs[0].default_value = (*pal_["fabric"], 1.0)
            if "FinishRough" in nt.nodes:
                rec = SKIN_DATA[skin]
                rough, metal = FINISH_PARAMS[rec["finish"]]
                if rec["material"] and rec["finish"] == "Standard":     # the material brings its own surface
                    d = MATERIAL_DEFAULTS[rec["material"]["id"]]
                    rough, metal = d["roughness"], d["metalOffset"]
                nt.nodes["FinishRough"].outputs[0].default_value = rough
                nt.nodes["FinishMetal"].outputs[0].default_value = metal
            if "SpecialA" in nt.nodes:
                set_special(nt, SKIN_DATA[skin]["special"], SKIN_DATA[skin]["form"])
            suffix = "" if skin == DEFAULT_SKIN else "_" + skin
            # "base_"/"emit_": half-size textures the comparison job left behind in this worker
            for nd in nt.nodes:
                if nd.type == "TEX_IMAGE" and nd.image.name.startswith(("T_Robot_BaseColor", "base_")):
                    nd.image = bpy.data.images.load(os.path.join(tex_dir, "T_Robot_BaseColor%s.png" % suffix),
                                                    check_existing=True)
                elif nd.type == "TEX_IMAGE" and nd.image.name.startswith(("T_Robot_Emission", "emit_")):
                    nd.image = bpy.data.images.load(os.path.join(tex_dir, "T_Robot_Emission%s.png" % suffix),
                                                    check_existing=True)


def render_expressions(cam, ob, out_dir, tex_dir, names):
    """Close-up of the face per expression; returns [(name, file)]."""
    scene = bpy.context.scene
    scene.eevee.taa_render_samples = preview_samples(32)
    scene.render.resolution_x = scene.render.resolution_y = 300
    cam.data.type = "PERSP"
    cam.data.lens = 70
    cam.location = (0.0, -1.6, 1.12)
    look_at(cam, (0, 0, 1.11))
    tiles = []
    for name in names:
        set_look(ob, tex_dir, expression=name)
        tiles.append((name, render_still(os.path.join(out_dir, "expr_%s.png" % name))))
    set_look(ob, tex_dir)
    return tiles


SHEET_CAMERAS = {"front": ((0, -6, 0.66), 1.5), "iso": ((-4.5, -4.5, 4.1), 1.7)}
TYPE_CAMERAS = {"front": ((0, -6, 0.70), 1.62), "iso": ((-4.5, -4.5, 4.2), 1.85)}


def render_types(cam, part_objs, robot_mesh, out_dir, tex_dir, names):
    """Every robot type (variants + skin + eyes + display + accessories), front and iso; returns {view: [(name, file)]}."""
    scene = bpy.context.scene
    scene.eevee.taa_render_samples = preview_samples(48)
    scene.render.resolution_x = scene.render.resolution_y = 520
    robot_mesh.hide_render = True
    tiles = {"front": [], "iso": []}
    for tname in names:
        spec = ROBOT_TYPES[tname]
        cfg = default_config()
        cfg.update(spec["variants"])
        objs = [part_objs[g] for g in config_groups(cfg)]
        for o in objs:
            o.hide_render = False
        for o in objs:
            set_look(o, tex_dir, spec["skin"], spec["expression"], spec["display"])
        for view, (loc, val) in TYPE_CAMERAS.items():
            cam.data.type = "ORTHO"
            cam.data.ortho_scale = val
            cam.location = loc
            look_at(cam, (0, 0, 0.70))
            tiles[view].append((tname, render_still(os.path.join(out_dir, "type_%s_%s.png" % (tname, view)))))
        for o in objs:
            o.hide_render = True
            set_look(o, tex_dir)
    robot_mesh.hide_render = False
    return tiles


def render_skins(cam, ob, part_objs, out_dir, tex_dir, names):
    """Front + iso render per skin (texture swap on M_Robot); skins with parts (Retro, Tank ...) are rendered with
    them, plus their own expression and display. Returns {view: [(skin, file)]}."""
    scene = bpy.context.scene
    scene.eevee.taa_render_samples = preview_samples(48)
    scene.render.resolution_x = scene.render.resolution_y = 520
    tiles = {"front": [], "iso": []}
    for skin in names:
        rec = SKIN_DATA[skin]
        objs = form_objects(part_objs, ob, rec["parts"], rec["form"])
        own = objs != [ob]
        if own:
            ob.hide_render = True
            for o in objs:
                o.hide_render = False
        for o in objs:
            set_look(o, tex_dir, skin, rec["expression"] or "Normal", rec["display"] or "Status")
        for view, (loc, val) in SHEET_CAMERAS.items():
            cam.data.type = "ORTHO"
            cam.data.ortho_scale = val
            cam.location = loc
            look_at(cam, (0, 0, 0.64))
            tiles[view].append((skin, render_still(os.path.join(out_dir, "skin_%s_%s.png" % (skin, view)))))
        if own:
            for o in objs:
                o.hide_render = True
                set_look(o, tex_dir)
            ob.hide_render = False
    set_look(ob, tex_dir)
    return tiles


def card_objects(part_objs, robot_mesh, kind, name):
    """Objects and look of a card: a skin (with its parts) or a robot type."""
    if kind == "type":
        spec = ROBOT_TYPES[name]
        variants, skin, expr, disp = spec["variants"], spec["skin"], spec["expression"], spec["display"]
    else:
        rec = SKIN_DATA[name]
        variants, skin = rec["parts"], name
        expr, disp = rec["expression"] or "Normal", rec["display"] or "Status"
    return form_objects(part_objs, robot_mesh, variants, None if kind == "type" else rec["form"]), skin, expr, disp


def render_compare(cam, ob, out_dir, compare_dir):
    """Classic with the comparison textures (compare_skins), front three-quarter view; returns [(label, file)]."""
    scene = bpy.context.scene
    scene.eevee.taa_render_samples = preview_samples(32)
    scene.render.resolution_x, scene.render.resolution_y = 300, 330
    cam.data.type = "PERSP"
    cam.data.lens = 58
    cam.location = (-1.03, -2.20, 1.00)
    look_at(cam, (0.02, 0, 0.66))
    tiles = []
    for i, (label, _) in enumerate(compare_skins()):
        for mat in ob.data.materials:
            if not mat.name.startswith("M_Robot") or mat.name.startswith(("M_RobotEyes", "M_RobotDisplay")):
                continue
            for nd in mat.node_tree.nodes:
                if nd.type == "TEX_IMAGE" and nd.image.name.startswith(("T_Robot_BaseColor", "base_")):
                    nd.image = bpy.data.images.load(os.path.join(compare_dir, "base_%02d.png" % i), check_existing=True)
                elif nd.type == "TEX_IMAGE" and nd.image.name.startswith(("T_Robot_Emission", "emit_")):
                    nd.image = bpy.data.images.load(os.path.join(compare_dir, "emit_%02d.png" % i), check_existing=True)
        tiles.append((label, render_still(os.path.join(out_dir, "compare_%02d.png" % i))))
    return tiles


def card_compositor(on):
    """Bloom for the skin cards (glow lines, eyes and lights read as light, like on the concept sheets)."""
    scene = bpy.context.scene
    if not on:
        scene.compositing_node_group = None
        return
    ng = bpy.data.node_groups.get("Card_Bloom")
    if ng is None:
        ng = bpy.data.node_groups.new("Card_Bloom", "CompositorNodeTree")
        ng.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
        rl = ng.nodes.new("CompositorNodeRLayers")
        glare = ng.nodes.new("CompositorNodeGlare")
        glare.inputs["Type"].default_value = "Bloom"
        glare.inputs["Highlights Threshold"].default_value = 2.2
        glare.inputs["Strength"].default_value = 0.3
        glare.inputs["Size"].default_value = 0.3
        out = ng.nodes.new("NodeGroupOutput")
        ng.links.new(rl.outputs["Image"], glare.inputs["Image"])
        ng.links.new(glare.outputs[0], out.inputs[0])
    scene.compositing_node_group = ng
    scene.render.use_compositing = True


CARD_BG = (0.0060, 0.0090, 0.0140)          # linear; the card background (#0f1620 after the view transform)


def render_cards(cam, part_objs, robot_mesh, out_dir, tex_dir, items):
    """Skin cards like the concept sheets: perspective three-quarter view, rim light in the skin's glow colour, bloom,
    background = card colour (kind "skin"). Kind "type" and skins with parts also get a transparent front render for
    the silhouettes ("sil:<name>"). Returns {"kind:name": file}."""
    scene = bpy.context.scene
    scene.eevee.taa_render_samples = preview_samples(48)
    floor = bpy.data.objects.get("Preview_Floor")
    rim = bpy.data.objects.get("Rim")
    bg = scene.world.node_tree.nodes.get("Background")
    old_bg, old_rim = tuple(bg.inputs[0].default_value), tuple(rim.data.color) if rim else None
    if floor:
        floor.hide_render = True
    robot_mesh.hide_render = True
    out = {}
    try:
        _render_card_items(cam, part_objs, robot_mesh, out_dir, tex_dir, items, out, rim, bg, old_bg, old_rim)
    finally:            # back to the preview scene even if a render fails (later packages, the saved .blend)
        card_compositor(False)
        scene.render.film_transparent = False
        if rim:
            rim.data.color = old_rim
        bg.inputs[0].default_value = old_bg
        robot_mesh.hide_render = False
        if floor:
            floor.hide_render = False
    return out


def _render_card_items(cam, part_objs, robot_mesh, out_dir, tex_dir, items, out, rim, bg, old_bg, old_rim):
    scene = bpy.context.scene
    for kind, name in items:
        objs, skin, expr, disp = card_objects(part_objs, robot_mesh, kind, name)
        for o in objs:
            o.hide_render = False
            set_look(o, tex_dir, skin, expr, disp)
        if kind == "skin":
            rec = SKIN_DATA[skin]
            glow = rec["lines"]["color"] if rec["lines"] else rec["palette"]["eye"]
            if rim:
                rim.data.color = tuple(0.55 + 0.45 * c for c in glow)
            bg.inputs[0].default_value = (*CARD_BG, 1.0)
            card_compositor(True)
            scene.render.film_transparent = False
            scene.render.resolution_x, scene.render.resolution_y = 420, 460
            cam.data.type = "PERSP"
            cam.data.lens = 58
            cam.location = (-1.03, -2.20, 1.00)      # 2.45 m away: the robot fills the card like on the concepts
            look_at(cam, (0.02, 0, 0.66))
            out["skin:" + name] = render_still(os.path.join(out_dir, "card_skin_%s.png" % name))
            card_compositor(False)
            if rim:
                rim.data.color = old_rim
            bg.inputs[0].default_value = old_bg
        if kind == "type" or kind == "skin" and (SKIN_DATA[name]["parts"] or SKIN_DATA[name]["form"]):
            scene.render.film_transparent = True
            scene.render.resolution_x, scene.render.resolution_y = 400, 440
            cam.data.type = "ORTHO"
            cam.data.ortho_scale = 1.62
            cam.location = (0, -6, 0.70)
            look_at(cam, (0, 0, 0.70))
            out["sil:%s:%s" % (kind, name)] = render_still(os.path.join(out_dir, "sil_%s_%s.png" % (kind, name)))
            scene.render.film_transparent = False
        for o in objs:
            o.hide_render = True
            set_look(o, tex_dir)


def render_accessory_icons(cam, part_objs, out_dir, tex_dir):
    """Every accessory alone, transparent, framed by its bounds (UI icons). Returns {"acc:<group>": file}."""
    scene = bpy.context.scene
    scene.eevee.taa_render_samples = preview_samples(32)
    scene.render.resolution_x = scene.render.resolution_y = 320
    scene.render.film_transparent = True
    floor = bpy.data.objects.get("Preview_Floor")
    if floor:
        floor.hide_render = True
    hidden = {o: o.hide_render for o in scene.objects if o.type == "MESH"}
    for o in hidden:
        o.hide_render = True
    out = {}
    view = Vector((-0.55, -1.0, 0.45)).normalized()
    for key, ob in part_objs.items():
        if not key.startswith("Acc"):
            continue
        ob.hide_render = False
        set_look(ob, tex_dir)
        pts = [ob.matrix_world @ Vector(c) for c in ob.bound_box]
        center = sum(pts, Vector()) / 8.0
        size = max((max(p[i] for p in pts) - min(p[i] for p in pts)) for i in range(3))
        cam.data.type = "ORTHO"
        cam.data.ortho_scale = size * 1.35
        cam.location = center + view * 3.0
        look_at(cam, center)
        out["acc:" + key] = render_still(os.path.join(out_dir, "icon_%s.png" % key))
        ob.hide_render = True
    for o, h in hidden.items():
        o.hide_render = h
    if floor:
        floor.hide_render = False
    scene.render.film_transparent = False
    return out


ICON_SIZE = 256


def icon_check(path, transparent):
    """Readability without a saliency model: the object must fill 30-96 % of the longer side and sit near the centre.
    Returns a problem text or None (only a hint in the report)."""
    res = subprocess.run([make_textures.find_magick(), path] + (["-alpha", "extract"] if transparent else
                         ["-fuzz", "12%"]) + ["-trim", "-format", "%w %h %X %Y %W %H", "info:"],
                         capture_output=True, text=True, env=make_textures.MAGICK_ENV)
    try:
        w, h, x, y, cw, ch = (int(v.lstrip("+")) for v in res.stdout.split())
    except ValueError:
        return "leer"
    fill = max(w / float(cw), h / float(ch))
    off = max(abs(x + w / 2.0 - cw / 2.0) / cw, abs(y + h / 2.0 - ch / 2.0) / ch)
    if fill < 0.30:
        return "zu klein (%.0f %%)" % (100 * fill)
    if fill > 0.96 and transparent:
        return "angeschnitten"
    if off > 0.16:
        return "nicht mittig (%.0f %%)" % (100 * off)
    return None


def build_icons(cards, acc_renders, out, tex_dir):
    """UI icons (ImageMagick) for a customisation menu: Icons/<Kind>/<ID>.png, 256 px. Skins from the card renders,
    expressions/displays/badges from the atlases, accessories from their own renders. Returns {kind: count} and
    readability hints."""
    root = os.path.join(out, "Icons")
    shutil.rmtree(root, ignore_errors=True)
    mg = make_textures.find_magick()
    jobs = []
    bg = ["-size", "%dx%d" % (ICON_SIZE, ICON_SIZE), "xc:none", "-fill", "#0f1620", "-draw",
          "roundrectangle 0,0 %d,%d 36,36" % (ICON_SIZE - 1, ICON_SIZE - 1)]
    for sid in SKINS:
        f = cards.get("skin:" + sid)
        if f:      # head and chest of the card render
            jobs.append(("Skins", sid, False, bg + ["(", f, "-gravity", "north", "-crop", "70%x58%+0+0", "+repage",
                                                    "-resize", "%dx%d" % (ICON_SIZE - 16, ICON_SIZE - 16), ")",
                                                    "-gravity", "center", "-compose", "over", "-composite"]))
    eye = bpy.path.abspath(SRC["eyes"].filepath)
    cols, rows = make_textures.EYE_GRID
    cw, ch = make_textures.EYE_CELL
    for i, name in enumerate(make_textures.EXPRESSIONS):
        style = make_textures.EXPRESSION_STYLES.get(name, {})
        tint = srgb_hex([c * style.get("glow", 1.0) for c in style.get("tint", SKINS[DEFAULT_SKIN]["eye"])])
        jobs.append(("Expressions", name, False, bg + [
            "(", eye, "-crop", "%dx%d+%d+%d" % (cw, ch, (i % cols) * cw, (i // cols) * ch), "+repage",
            "-resize", "%dx" % (ICON_SIZE - 24), "(", "+clone", "-fill", tint, "-colorize", "100", ")", "+swap",
            "-compose", "multiply", "-composite", ")", "-gravity", "center", "-compose", "screen", "-composite"]))
    disp = os.path.join(tex_dir, "T_Robot_DisplayAtlas.png")
    dcols, _ = make_textures.DISPLAY_GRID
    dw, dh = make_textures.DISPLAY_CELL
    screen = srgb_hex(SKINS[DEFAULT_SKIN]["screen"])
    for i, name in enumerate(make_textures.DISPLAYS):
        jobs.append(("Displays", name, False, bg + [
            "(", disp, "-crop", "%dx%d+%d+%d" % (dw, dh, (i % dcols) * dw, (i // dcols) * dh), "+repage",
            "-resize", "%dx" % (ICON_SIZE - 24), "(", "+clone", "-fill", screen, "-colorize", "100", ")", "+swap",
            "-compose", "multiply", "-composite", ")", "-gravity", "center", "-compose", "screen", "-composite"]))
    badges = bpy.path.abspath(SRC["badges"].filepath)
    c = make_textures.BADGE_CELL
    for i, name in enumerate(make_textures.BADGE_ART):
        jobs.append(("Badges", name, False, bg + [
            "(", badges, "-crop", "%dx%d+%d+%d" % (c, c, (i % 4) * c, (i // 4) * c), "+repage",
            "-resize", "%dx" % (ICON_SIZE - 48), ")", "-gravity", "center", "-compose", "over", "-composite"]))
    for key, f in acc_renders.items():
        name = key.split(":", 1)[1]
        jobs.append(("Accessories", name, True, [f, "-trim", "+repage", "-resize", "%dx%d" % ((ICON_SIZE - 24,) * 2),
                                                  "-background", "none", "-gravity", "center",
                                                  "-extent", "%dx%d" % (ICON_SIZE, ICON_SIZE)]))

    def make(job):
        kind, name, transparent, args = job
        path = os.path.join(root, kind, name + ".png")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        make_textures.run(mg, args + ["-depth", "8", path])
        return kind, name, icon_check(path, transparent)

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(make, jobs))
    for f in acc_renders.values():
        if os.path.exists(f):
            os.remove(f)
    counts = collections.Counter(k for k, _, _ in results)
    hints = {"%s/%s" % (k, n): p for k, n, p in results if p}
    LOG.info("  icons: %s%s", ", ".join("%s %d" % kv for kv in sorted(counts.items())),
             (" - check: " + ", ".join("%s %s" % kv for kv in sorted(hints.items()))) if hints else " - all readable")
    return dict(counts), hints


def srgb_hex(c):
    return "#%02x%02x%02x" % tuple(int(round(255 * float(linear_to_srgb(np.float32(v))))) for v in c)


BADGE_LABELS = {"HazardStripes": "Warnstreifen", "Cross": "Kreuz", "Cloud": "Wolke", "Number": "Nummer",
                "Biohazard": "Biohazard", "Glyph": "Schriftzeichen", "RacingStripes": "Rennstreifen",
                "Target": "Zielmarke", "Star": "Stern", "Gear": "Zahnrad", "Leaf": "Blatt"}
LINE_LABELS = {"Circuit": "Leiterbahnen", "Rings": "Ringe", "Seams": "Kanten", "Bolt": "Blitze", "Joints": "Gelenke"}
MATERIAL_LABELS = {"Wood": "Holz", "Stone": "Stein", "Sand": "Sand", "Paper": "Papier", "Concrete": "Beton",
                   "Brass": "Messing", "Circuit": "Platine"}
LAYER_LABELS = {"Rust": "Rost", "Moss": "Moos", "Frost": "Frost", "Embers": "Glutrisse", "Camo": "Tarnung"}
ZONE_LABELS = {"Head": "Kopf", "Ears": "Ohren", "Chest": "Brust", "Pads": "Schultern", "Arms": "Arme", "Hips": "Hüfte",
               "Legs": "Beine", "Feet": "Füße"}
CARD_FONT, CARD_FONT_BOLD = "Arial", "Arial-Bold"


def skin_card(i, sid, render, path):
    """One card in the style of the concept sheets: render, four colour dots, systems, "NN // NAME", theme."""
    rec = SKIN_DATA[sid]
    pal_ = rec["palette"]
    dots = []
    for j, key in enumerate(("armor", "armor2" if rec["zones2"] else "mech", "accent", "eye")):
        cx = 336 + (j % 2) * 40 + (j // 2) * 90
        dots += ["-fill", srgb_hex(pal_[key]), "-stroke", "#5b6b7c", "-strokewidth", 2,
                 "-draw", "circle %d,46 %d,46" % (cx, cx + 16)]
    info = ["Spezial: " + SPECIALS[rec["special"]["id"]]["label"]] if rec["special"] else []
    if rec["form"]:
        info.insert(0, "Form: " + FORMS[rec["form"]]["label"])
    if rec["finish"] != "Standard":
        info.append("Oberfläche " + rec["finish"])
    if rec["zones2"]:
        info.append("2. Farbe: " + ", ".join(ZONE_LABELS[z] for z in rec["zones2"][:2]) +
                    (" +%d" % (len(rec["zones2"]) - 2) if len(rec["zones2"]) > 2 else ""))
    if rec["material"]:
        info.append("Werkstoff: " + MATERIAL_LABELS[rec["material"]["id"]])
    info += ["%s %d %%" % (LAYER_LABELS[ly["id"]], round(ly["amount"] * 100)) for ly in rec["layers"]]
    if abs(rec["wear"] - WEAR_DEFAULT) > 1e-6:
        info.append("Abnutzung %d %%" % round(rec["wear"] * 100))
    info += [BADGE_LABELS[b["id"]] for b in rec["badges"]]
    if rec["lines"]:
        info.append("Linien: " + LINE_LABELS[rec["lines"]["pattern"]])
    if rec["parts"]:
        info.append("eigene Teile")
    text = []
    for j, line in enumerate(info[:6]):
        text += ["-annotate", "+318+%d" % (104 + 26 * j), line]
    magick("-size", "520x420", "xc:#0f1620", "-fill", "none", "-stroke", "#26394c", "-strokewidth", 2,
           "-draw", "roundrectangle 1,1 518,418 12,12", "-stroke", "none",
           "(", render, "-resize", "306x335", ")", "-gravity", "northwest", "-geometry", "+4+4", "-composite",
           *dots, "-stroke", "none", "-fill", "#9fb3c8", "-font", CARD_FONT, "-pointsize", 17, *text,
           "-fill", "#0b1118", "-draw", "rectangle 2,340 517,417",
           "-fill", "#3fd0ff", "-font", CARD_FONT_BOLD, "-pointsize", 28, "-annotate", "+16+378", "%02d //" % (i + 1),
           "-fill", "#eef6ff", "-annotate", "+86+378", sid.upper(),
           "-fill", "#6fa8c8", "-font", CARD_FONT, "-pointsize", 16, "-annotate", "+18+404", rec["theme"].upper(),
           *(["-fill", "#ffd23a", "-font", CARD_FONT_BOLD, "-pointsize", 24, "-annotate", "+484+380", "*"]
             if rec["startSet"] else []),
           "-depth", "8", path)
    return path


def build_skin_sheet(cards, preview_dir):
    """Previews/skin_sheet.png (all skins as cards, start set marked *) and Previews/silhouettes.png (types and
    skins with their own parts, from the alpha of the card renders)."""
    tmp = os.path.join(preview_dir, "cards_tmp")
    os.makedirs(tmp, exist_ok=True)
    names = [n for n in SKINS if "skin:" + n in cards]
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        tiles = list(pool.map(lambda a: skin_card(a[0], a[1], cards["skin:" + a[1]],
                                                  os.path.join(tmp, "card_%02d.png" % a[0])), enumerate(names)))
    grid = os.path.join(tmp, "grid.png")
    magick("montage", *tiles, "-tile", "6x", "-geometry", "+8+8", "-background", "#0a0f15", "-depth", "8", grid)
    magick(grid, "-gravity", "north", "-background", "#0a0f15", "-splice", "0x96", "-gravity", "northwest",
           "-fill", "#3fd0ff", "-font", CARD_FONT_BOLD, "-pointsize", 46, "-annotate", "+24+20", "CUBE ESCAPE",
           "-fill", "#9fd8f0", "-font", CARD_FONT, "-pointsize", 22, "-annotate", "+26+70",
           "SPIELER-ROBOTER  ·  SKINS  ·  GENERATOR %s  ·  * = STARTSET" % VERSION, "-depth", "8",
           os.path.join(preview_dir, "skin_sheet.png"))
    sil = [cards["sil:type:" + t] for t in ROBOT_TYPES if "sil:type:" + t in cards]
    sil += [cards["sil:skin:" + n] for n in names if "sil:skin:" + n in cards]
    if sil:
        shapes = []
        for j, f in enumerate(sil):
            out = os.path.join(tmp, "sil_%02d.png" % j)
            magick(f, "-alpha", "extract", "-threshold", "50%", "-negate", "-fill", "#e9e2d6", "-opaque", "white",
                   "-fill", "#1c2026", "-opaque", "black", "-resize", "50%", out)
            shapes.append(out)
        magick("montage", *shapes, "-tile", "%dx" % min(len(shapes), 10), "-geometry", "+4+4", "-background", "#e9e2d6",
               "-depth", "8", os.path.join(preview_dir, "silhouettes.png"))
    shutil.rmtree(tmp, ignore_errors=True)
    for f in cards.values():
        if os.path.exists(f):
            os.remove(f)
    LOG.info("  sheet %s (%d skins), silhouettes (%d)", os.path.join(preview_dir, "skin_sheet.png"), len(names),
             len(sil))


GIF_CLIPS = (("Idle", 2), ("Walk", 1), ("Run", 1), ("DanceRobot", 2), ("Celebrate", 1))


def render_gif(cam, arm_ob, actions, out_dir, name, step):
    scene = bpy.context.scene
    scene.eevee.taa_render_samples = preview_samples(12)
    scene.render.resolution_x = scene.render.resolution_y = 320
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = 1.75
    cam.location = (-4.0, -4.0, 3.6)
    look_at(cam, (0, -0.05, 0.6))
    tmp = os.path.join(out_dir, "anim_frames", "gif_" + name)
    os.makedirs(tmp, exist_ok=True)
    act = actions[name]
    assign_action(arm_ob, act)
    files = []
    for f in range(0, int(act.frame_range[1]), step):
        scene.frame_set(f)
        files.append(render_still(os.path.join(tmp, "%s_%03d.png" % (name, f))))
    gif = os.path.join(out_dir, "anim_%s.gif" % name)
    magick("-delay", "%dx%d" % (step, FPS), "-loop", "0", *files, "-layers", "Optimize", gif)
    shutil.rmtree(tmp)
    LOG.info("  gif %s", gif)
    return gif


# ----------------------------------------------------------------------------------------------
# Export
# ----------------------------------------------------------------------------------------------


# Unity converts the declared FBX axes consistently: Blender's -Y (the robot's front) always ends up as Unity -Z, whatever
# axis_forward says (measured in the Unity testbed, 1.10.1). For the README promise "the robot looks +Z" the armature
# object is turned 180 degrees about Z for the export only; bones, animation and clip data stay as they are.
FBX_EXPORT_TURN = math.pi


# Detail levels (1.13): decimated copies for distance and crowds, exported next to each FBX (same rig, no takes).
LOD_TRIANGLES = (6000, 2500)


def make_lod(ob, target, name, coll, arm_ob):
    """Decimated copy of a bound mesh (collapse keeps UVs, materials and the rigid weights of every part)."""
    lod = ob.copy()
    lod.data = ob.data.copy()
    lod.modifiers.clear()
    lod.name = lod.data.name = name
    coll.objects.link(lod)
    ratio = min(1.0, target / max(1, tri_count(lod.data)))
    mod = lod.modifiers.new("Decimate", "DECIMATE")
    mod.decimate_type, mod.ratio, mod.use_collapse_triangulate = "COLLAPSE", ratio, True
    for o in bpy.context.scene.objects:
        o.select_set(o == lod)
    bpy.context.view_layer.objects.active = lod
    with bpy.context.temp_override(object=lod, active_object=lod, selected_objects=[lod]):
        if lod.data.has_custom_normals:          # the weighted normals of LOD0 would not fit the collapsed faces
            bpy.ops.mesh.customdata_custom_splitnormals_clear()
        bpy.ops.object.modifier_apply(modifier=mod.name)
        bpy.ops.object.shade_smooth_by_angle(angle=math.radians(38))      # the custom normals are gone
    bind_mesh(lod, arm_ob)
    return lod


def export_lods(path, ob, coll, arm_ob):
    """<path>_LOD1.fbx / _LOD2.fbx in a LOD folder next to path; returns the triangle counts."""
    folder = os.path.join(os.path.dirname(path), "LOD")
    os.makedirs(folder, exist_ok=True)
    base = os.path.splitext(os.path.basename(path))[0]
    tris = {"LOD0": tri_count(ob.data)}
    for i, target in enumerate(LOD_TRIANGLES, 1):
        lod = make_lod(ob, target, "%s_LOD%d" % (ob.name, i), coll, arm_ob)
        tris["LOD%d" % i] = tri_count(lod.data)
        export_fbx(os.path.join(folder, "%s_LOD%d.fbx" % (base, i)), [lod], arm_ob, animations=False)
        bpy.data.objects.remove(lod, do_unlink=True)
    return tris


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
    saved_rotation = (arm_ob.rotation_mode, tuple(arm_ob.rotation_euler))
    arm_ob.rotation_mode = "XYZ"
    arm_ob.rotation_euler = (0.0, 0.0, FBX_EXPORT_TURN)      # the parented meshes turn with it
    bpy.context.view_layer.update()
    try:
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
    finally:          # a failed export must not leave the robot turned (renders, .blend)
        arm_ob.rotation_mode, arm_ob.rotation_euler = saved_rotation[0], saved_rotation[1]
        bpy.context.view_layer.update()
    for track in ad.nla_tracks:
        track.mute = True
    for o in hidden:
        o.hide_set(True)
    assign_action(arm_ob, bpy.data.actions["Idle"])
    LOG.info("  wrote %s (%.1f KB)", path, os.path.getsize(path) / 1024)


# ----------------------------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------------------------


# ---------------------------------- work packages and worker processes ----------------------------------
# After the model is built, the QA and all preview renders are work packages in a queue. Worker processes
# (Blender, each rebuilding the model from the same code) and, once its own steps are done, the main process take
# them first come, first served. QA workers start before the bake (they only need geometry and animation), render
# workers right after it (they take the UVs from the cache and load the baked textures). Results are merged in a
# fixed order, so the report does not depend on who ran what.

STILL_JOBS = ("views", "skins", "expressions", "types", "cards", "icons", "compare")
EXACT_JOBS = ("views", "expressions")       # the deliverable look; everything else uses the fast preview look


def chunks(items, n):
    return [items[i:i + n] for i in range(0, len(items), n)]


def make_jobs(type_names, actions_available, args, n_proc):
    """QA: one package per robot type and clip group (big groups split so that every process gets work even if only
    the Classic robot is checked). Renders: views, skins, expressions and types in chunks, one package per clip for
    the animation sheets and per GIF. Each lane is sorted by estimated cost, biggest first."""
    jobs = []
    all_types = ["Classic"] + type_names
    total = sum(a.frames + 1 for a in ANIMS) * len(all_types)
    limit = max(120, -(-total // (2 * n_proc)))
    owner = type_emotes()
    # a type emote is always checked with its owner type, also when that type is not in this run (quick: Classic)
    for clip, t in owner.items():
        a = next(x for x in ANIMS if x.name == clip)
        if t not in all_types:
            jobs.append({"id": "qa:%s:emote:%s" % (t, clip), "lane": "qa", "kind": "qa", "type": t,
                         "clips": [clip], "cost": a.frames + 1})
    for t in all_types:
        for g in GROUPS:
            part, size, k = [], 0, 0
            for a in [a for a in ANIMS if a.group == g and owner.get(a.name, t) == t] + [None]:
                if part and (a is None or size + a.frames + 1 > limit):
                    jobs.append({"id": "qa:%s:%s:%d" % (t, g, k), "lane": "qa", "kind": "qa", "type": t,
                                 "clips": [c.name for c in part], "cost": size})
                    part, size, k = [], 0, k + 1
                if a is not None:
                    part.append(a)
                    size += a.frames + 1
    if not args.no_render:
        def render(kind, key, cost, **kw):
            jobs.append(dict(id="%s:%s" % (kind, key), lane="render", kind=kind, cost=cost, **kw))
        render("views", "all", 7 * 64 * 3)
        for i, names in enumerate(chunks(list(SKINS), 5)):
            render("skins", i, len(names) * 2 * 48, names=names)
        for i, names in enumerate(chunks(list(make_textures.EXPRESSIONS), 14)):
            render("expressions", i, len(names) * 32 * 3, names=names)
        for i, names in enumerate(chunks(list(ROBOT_TYPES), 4)):
            render("types", i, len(names) * 2 * 48, names=names)
        card_items = [["skin", n] for n in SKINS] + [["type", t] for t in ROBOT_TYPES]
        for i, items in enumerate(chunks(card_items, 9)):
            render("cards", i, len(items) * 48, items=items)
        render("icons", "accessories", 20 * 32)
        render("compare", "weathering", len(compare_skins()) * 32)
        if not args.no_anim_sheets:
            for a in ANIMS:
                render("sheet", a.name, 2 * 6 * 12, clip=a.name)
            for name, step in GIF_CLIPS:
                if name in actions_available:
                    frames = next(a.frames for a in ANIMS if a.name == name)
                    render("gif", name, -(-frames // step) * 12, clip=name, step=step)
    # type FBX and their detail levels: packages of the render lane (those workers have the baked materials)
    for i, names in enumerate(chunks(list(ROBOT_TYPES), 3)):
        jobs.append({"id": "fbx:%d" % i, "lane": "render", "kind": "fbx", "types": names, "lods": args.lods,
                     "cost": len(names) * (300 if args.lods else 100)})
    jobs.sort(key=lambda j: -j["cost"])
    return jobs


class JobQueue:
    """A folder: jobs.json lists the packages; a process claims package n by creating claim_<n> exclusively and
    writes res_<n>.json when it is done."""

    def __init__(self, path):
        self.path = path
        with open(os.path.join(path, "jobs.json"), encoding="utf-8") as fh:
            self.jobs = json.load(fh)

    @staticmethod
    def create(path, jobs):
        shutil.rmtree(path, ignore_errors=True)
        os.makedirs(path)
        with open(os.path.join(path, "jobs.json"), "w", encoding="utf-8") as fh:
            json.dump(jobs, fh)
        return JobQueue(path)

    def claim(self, i):
        try:
            fd = os.open(os.path.join(self.path, "claim_%d" % i), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True

    def work(self, ctx, lanes, who):
        """Runs unclaimed packages of the given lanes (in lane order) until none is left; returns (count, seconds)."""
        count, busy = 0, 0.0
        for lane in lanes:
            for i, job in enumerate(self.jobs):
                if job["lane"] != lane or not self.claim(i):
                    continue
                t0 = time.time()
                result = run_job(ctx, job)
                dt = time.time() - t0
                count, busy = count + 1, busy + dt
                LOG.debug("  [%s] %s %.1fs", who, job["id"], dt)
                tmp = os.path.join(self.path, "res_%d.tmp" % i)
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump({"result": result, "seconds": round(dt, 2), "by": who}, fh)
                os.replace(tmp, os.path.join(self.path, "res_%d.json" % i))
        return count, busy

    def results(self):
        out = []
        for i, job in enumerate(self.jobs):
            path = os.path.join(self.path, "res_%d.json" % i)
            if not os.path.exists(path):
                raise RuntimeError("work package %s has no result" % job["id"])
            with open(path, encoding="utf-8") as fh:
                out.append((job, json.load(fh)))
        return out


# QA cache (1.13.1): a clip's QA result only depends on the robot type's mesh, the rig, the clip's keys and the QA code.
# Unchanged inputs = reused result (cache/qa, one small JSON per type and clip). The key holds the source code of the
# QA functions, so a changed check invalidates the cache by itself; --no-cache ignores it.
QA_CODE_PARTS = ("run_qa", "qa_problem", "group_faces", "evaluated_coords", "Topology", "GroupTrees")


def qa_code_digest():
    h = hashlib.sha1()
    for name in QA_CODE_PARTS:
        h.update(inspect.getsource(globals()[name]).encode())
    for rule in (ACCEPTED_OVERLAPS, ACCEPTED_DETACH, HIDE_HELD_ITEM):
        h.update(repr(sorted(rule)).encode())
    return h.hexdigest()


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


QA_CACHE_DAYS = 7


def prune_qa_cache(folder):
    """Removes cached QA results nobody used for QA_CACHE_DAYS (every model change starts a new generation)."""
    if not os.path.isdir(folder):
        return 0
    limit = time.time() - QA_CACHE_DAYS * 86400
    old = [f for f in os.scandir(folder) if f.is_file() and f.stat().st_mtime < limit]
    for f in old:
        os.remove(f.path)
    return len(old)


def write_json_atomic(path, data):
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)


class JobContext:
    """What a package needs from the built model; QA meshes and the preview scene are made on first use."""

    def __init__(self, part_objs, coll, preview_coll, arm_ob, actions, robot_mesh, preview_dir, tex_dir,
                 qa_cache=None, export_dir=None):
        self.part_objs, self.coll, self.preview_coll = part_objs, coll, preview_coll
        self.arm_ob, self.actions, self.robot_mesh = arm_ob, actions, robot_mesh
        self.preview_dir, self.tex_dir = preview_dir, tex_dir
        self.qa_meshes = {}
        self.qa_cache = qa_cache            # folder or None
        self.qa_keys = {}                   # type -> digest of QA code, rig and type mesh
        self.action_keys = {}               # clip -> digest of its keys
        self.qa_hits = 0
        self.export_dir = export_dir        # the main output folder (type FBX packages)

    def qa_key(self, tname):
        if tname not in self.qa_keys:
            if "code" not in self.qa_keys:
                self.qa_keys["code"] = qa_code_digest() + rig_digest(self.arm_ob)
            ob = self.qa_mesh(tname)
            self.qa_keys[tname] = hashlib.sha1((self.qa_keys["code"] + mesh_digest(ob)).encode()).hexdigest()[:20]
        return self.qa_keys[tname]

    def cached_qa(self, tname, clips):
        """QA of clips with the cache: only the clips without a stored result are run."""
        if not self.qa_cache:
            return run_qa(self.qa_mesh(tname), self.arm_ob, self.actions, tname, quiet=True, clips=clips)
        os.makedirs(self.qa_cache, exist_ok=True)
        key = self.qa_key(tname)
        rest_path = os.path.join(self.qa_cache, "%s_rest.json" % key)
        paths, found = {}, {}
        for c in clips:
            if c not in self.action_keys:
                self.action_keys[c] = action_digest(self.actions[c])[:16]
            paths[c] = os.path.join(self.qa_cache, "%s_%s_%s.json" % (key, c, self.action_keys[c]))
            if os.path.exists(paths[c]):
                with open(paths[c], encoding="utf-8") as fh:
                    found[c] = json.load(fh)
                os.utime(paths[c])          # used: survives prune_qa_cache
        missing = [c for c in clips if c not in found]
        if not missing and os.path.exists(rest_path):
            with open(rest_path, encoding="utf-8") as fh:
                res = json.load(fh)
        else:
            res = run_qa(self.qa_mesh(tname), self.arm_ob, self.actions, tname, quiet=True, clips=missing)
            write_json_atomic(rest_path, {"rest_min_z": res["rest_min_z"], "rest_height": res["rest_height"]})
            for c in missing:
                write_json_atomic(paths[c], res["actions"][c])
        self.qa_hits += len(found)
        actions = dict(found)
        actions.update(res.get("actions", {}))
        return {"rest_min_z": res["rest_min_z"], "rest_height": res["rest_height"], "actions": actions}

    def camera(self):
        cam = bpy.data.objects.get("Preview_Camera") or setup_render_scene(self.preview_coll)
        bpy.context.scene.camera = cam
        return cam

    def qa_mesh(self, tname):
        """One QA mesh at a time: every mesh in the view layer is evaluated on each frame change."""
        ob = self.qa_meshes.get(tname)
        if ob is None:
            for other in self.qa_meshes.values():
                bpy.data.objects.remove(other, do_unlink=True)
            self.qa_meshes.clear()
            if tname.startswith("Form_"):         # a body form in the default look (QA_FORMS)
                objs = form_objects(self.part_objs, None, {}, tname[len("Form_"):])
            else:
                cfg = default_config()
                cfg.update(ROBOT_TYPES[tname]["variants"])
                objs = [self.part_objs[g] for g in config_groups(cfg)]
            ob = combine_objects(objs, "QA_" + tname, self.coll)
            bind_mesh(ob, self.arm_ob)
            self.qa_meshes[tname] = ob
        return ob

    def cleanup(self):
        """Back to the state the .blend is saved in."""
        for ob in self.qa_meshes.values():
            bpy.data.objects.remove(ob, do_unlink=True)
        self.qa_meshes.clear()
        set_fast_eyes(False)
        bpy.context.scene.eevee.use_raytracing = True
        show_tool(False)


def export_forms(folder, part_objs, robot_mesh, coll, arm_ob, tex_dir):
    """Forms/CubeEscape_Robot_<Form>.fbx (default look in that form, same rig, no takes - the clips of the main FBX
    play on it) and, for mesh forms, Forms/CubeEscape_Robot_Modular_<Form>.fbx. Returns triangle counts."""
    os.makedirs(folder, exist_ok=True)
    out = {}
    for form, d in FORMS.items():
        if d["kind"] == "shader":
            continue
        tmp = combine_objects(form_objects(part_objs, robot_mesh, {}, form), "Robot_Mesh_" + form, coll)
        bind_mesh(tmp, arm_ob)
        set_look(tmp, tex_dir)
        export_fbx(os.path.join(folder, "CubeEscape_Robot_%s.fbx" % form), [tmp], arm_ob, animations=False)
        out[form] = {"triangles": tri_count(tmp.data)}
        bpy.data.objects.remove(tmp, do_unlink=True)
        if d["kind"] == "mesh":
            export_fbx(os.path.join(folder, "CubeEscape_Robot_Modular_%s.fbx" % form), list(FORM_OBJS[form].values()),
                       arm_ob, animations=False)
    LOG.info("  forms: %s", ", ".join("%s %d triangles" % (f, v["triangles"]) for f, v in out.items()))
    return out


def export_type_fbx(ctx, names, lods):
    """Types/CubeEscape_Robot_<Type>.fbx (+ LOD) for some robot types; returns their triangle and material counts."""
    type_dir = os.path.join(ctx.export_dir, "Types")
    os.makedirs(type_dir, exist_ok=True)
    out = {}
    for tname in names:
        cfg = default_config()
        cfg.update(ROBOT_TYPES[tname]["variants"])
        tmp = combine_objects([ctx.part_objs[g] for g in config_groups(cfg)], "Robot_Mesh_" + tname, ctx.coll)
        bind_mesh(tmp, ctx.arm_ob)
        set_look(tmp, ctx.tex_dir)          # render packages may have left another skin on the shared materials
        path = os.path.join(type_dir, "CubeEscape_Robot_%s.fbx" % tname)
        export_fbx(path, [tmp], ctx.arm_ob, animations=False)
        out[tname] = {"triangles": tri_count(tmp.data), "materials": len(tmp.data.materials),
                      "lods": export_lods(path, tmp, ctx.coll, ctx.arm_ob) if lods else None}
        bpy.data.objects.remove(tmp, do_unlink=True)
    return out


def run_job(ctx, job):
    kind = job["kind"]
    if kind == "qa":
        return ctx.cached_qa(job["type"], job["clips"])
    if kind == "fbx":
        return export_type_fbx(ctx, job["types"], job["lods"])
    cam = ctx.camera()
    preview_quality(kind in EXACT_JOBS)
    show_tool(kind == "sheet" and job["clip"] in TOOL_CLIPS)
    if kind in STILL_JOBS:
        rest_pose(ctx.arm_ob)
    if kind == "views":
        return render_views(cam, ctx.preview_dir)
    if kind == "skins":
        return render_skins(cam, ctx.robot_mesh, ctx.part_objs, ctx.preview_dir, ctx.tex_dir, job["names"])
    if kind == "expressions":
        return render_expressions(cam, ctx.robot_mesh, ctx.preview_dir, ctx.tex_dir, job["names"])
    if kind == "types":
        return render_types(cam, ctx.part_objs, ctx.robot_mesh, ctx.preview_dir, ctx.tex_dir, job["names"])
    if kind == "cards":
        return render_cards(cam, ctx.part_objs, ctx.robot_mesh, ctx.preview_dir, ctx.tex_dir, job["items"])
    if kind == "icons":
        rest_pose(ctx.arm_ob)      # the antenna rides on a spring bone: no leftover clip pose of an earlier package
        return render_accessory_icons(cam, ctx.part_objs, ctx.preview_dir, ctx.tex_dir)
    if kind == "compare":
        tiles = render_compare(cam, ctx.robot_mesh, ctx.preview_dir, os.path.join(ctx.preview_dir, ".compare"))
        set_look(ctx.robot_mesh, ctx.tex_dir)
        return tiles
    if kind == "sheet":
        return render_anim_rows(cam, ctx.arm_ob, ctx.actions, ctx.preview_dir, job["clip"])
    if kind == "gif":
        return render_gif(cam, ctx.arm_ob, ctx.actions, ctx.preview_dir, job["clip"], job["step"])
    raise ValueError("unknown work package %s" % job["id"])


def start_workers(first, n, lanes, args, out, queue, textures=None):
    """n Blender processes that rebuild the model (no bake: QA workers skip textures, render workers load the baked
    ones and the cached UVs) and then work on the queue."""
    procs = []
    for i in range(first, first + n):
        wdir = os.path.join(out, "logs", "worker_%d" % i)
        shutil.rmtree(wdir, ignore_errors=True)
        os.makedirs(wdir)
        threads = args.worker_threads if lanes[0] == "qa" else args.render_threads
        cmd = [bpy.app.binary_path, "-b", "--factory-startup", "-noaudio", "-t", str(threads), "--python-exit-code", "1",
               "--python", os.path.abspath(__file__), "--", "--tier", args.tier, "--out", wdir, "--src-dir", out,
               "--worker-queue", queue.path, "--worker-lanes", ",".join(lanes), "--worker-id", str(i)]
        cmd += ["--use-textures", textures, "--uv-cache", os.path.join(out, "cache")] if textures else ["--skip-bake"]
        if args.no_cache:
            cmd += ["--no-cache"]           # the workers would otherwise reuse cache/qa and cache/render
        if args.groups:
            cmd += ["--groups", args.groups]
        # niced: the main process's own steps (clip data, export) are the critical path while the workers run
        procs.append((i, wdir, subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                                preexec_fn=(lambda: os.nice(10)) if hasattr(os, "nice") else None)))
    if n:
        LOG.info("  started %d %s worker(s)", n, "/".join(lanes))
    return procs


def wait_workers(procs):
    stats = {}
    for i, wdir, proc in procs:
        code = proc.wait()
        log = os.path.join(wdir, "logs", "generate_robot.log")
        if code != 0:
            raise RuntimeError("worker %d failed (exit %s), log: %s" % (i, code, log))
        with open(os.path.join(wdir, "worker.json"), encoding="utf-8") as fh:
            stats["worker_%d" % i] = json.load(fh)
        if not os.environ.get("ROBOT_KEEP_WORKER_LOGS"):        # debugging: keep logs/worker_<n>
            shutil.rmtree(wdir, ignore_errors=True)
    return stats


def merge_qa(results, type_names):
    """QA package results -> {type: {rest_min_z, rest_height, actions in clip order}} for Classic + type_names."""
    parts = collections.defaultdict(list)
    for job, res in results:
        if job["kind"] == "qa":
            parts[job["type"]].append(res["result"])
    qa = {}
    full = ["Classic"] + type_names
    owner = type_emotes()
    for t in full + sorted(set(parts) - set(full)):     # extra types: only their emotes were checked
        rest = {(r["rest_min_z"], r["rest_height"]) for r in parts[t]}
        if len(rest) != 1:
            raise RuntimeError("QA packages of %s disagree on the rest pose: %s" % (t, rest))
        actions = {}
        for r in parts[t]:
            actions.update(r["actions"])
        wanted = [a.name for a in ANIMS if owner.get(a.name, t) == t] if t in full else \
            [c for c, o in owner.items() if o == t]
        missing = [c for c in wanted if c not in actions]
        if missing:
            raise RuntimeError("QA of %s is missing clips %s" % (t, missing))
        min_z, height = rest.pop()
        qa[t] = {"rest_min_z": min_z, "rest_height": height,
                 "actions": {a.name: actions[a.name] for a in ANIMS if a.name in actions}}
    return qa


def finish_previews(results, preview_dir, report, tex_dir):
    """Sheets from the render packages, always in the same order (skins, expressions, types, contact sheets)."""
    by_kind = collections.defaultdict(list)
    for job, res in results:
        by_kind[job["kind"]].append((job, res["result"]))
    if by_kind["views"]:
        report["previews"] = by_kind["views"][0][1]
    tiles = collections.defaultdict(dict)
    for kind in ("skins", "types"):
        for _, res in by_kind[kind]:
            for view, items in res.items():
                tiles[(kind, view)].update(dict(items))
    for _, res in by_kind["expressions"]:
        tiles[("expressions", "front")].update(dict(res))
    sheets = []
    for (kind, view), names in ((("skins", "front"), list(SKINS)), (("skins", "iso"), list(SKINS)),
                                (("expressions", "front"), list(make_textures.EXPRESSIONS)),
                                (("types", "front"), list(ROBOT_TYPES)), (("types", "iso"), list(ROBOT_TYPES))):
        if tiles[(kind, view)]:
            name = {"skins": "skins", "types": "types", "expressions": "expressions"}[kind]
            sheet = os.path.join(preview_dir, "%s%s.png" % (name, "" if view == "front" else "_iso"))
            sheets.append(([(n, tiles[(kind, view)][n]) for n in names], sheet, 18 if kind == "expressions" else 22))
    # every sheet is independent ImageMagick work (MAGICK_THREAD_LIMIT=1): one pool runs them side by side
    tasks = [(montage_sheet, t) for t in sheets]
    icons = None
    if by_kind["cards"]:
        cards = {}
        for _, res in by_kind["cards"]:
            cards.update(res)
        acc = {}
        for _, res in by_kind["icons"]:
            acc.update(res)
        def icons_then_sheet():         # the skin sheet deletes the card renders the icons are cut from
            res = build_icons(cards, acc, os.path.dirname(preview_dir), tex_dir)
            build_skin_sheet(cards, preview_dir)
            return res
        icons = (icons_then_sheet, ())
        tasks.append(icons)
    for _, res in by_kind["compare"]:          # the weathering comparison: 5 layers x 3 amounts, materials, wear
        tasks.append((montage_sheet, ([tuple(t) for t in res], os.path.join(preview_dir, "weathering.png"), 18)))
    if by_kind["sheet"]:
        tasks.append((build_contact_sheets, ({job["clip"]: rows for job, rows in by_kind["sheet"]}, preview_dir)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(8, len(tasks)))) as pool:
        futures = [pool.submit(fn, *a) for fn, a in tasks]
        done = [f.result() for f in futures]            # re-raises the first error
    if icons is not None:
        report["icons"], report["icon_hints"] = done[tasks.index(icons)]
    shutil.rmtree(os.path.join(preview_dir, ".compare"), ignore_errors=True)
    frames_dir = os.path.join(preview_dir, "anim_frames")
    if os.path.isdir(frames_dir):
        shutil.rmtree(frames_dir)


def textures_in(tex_dir):
    return {fn: os.path.join(tex_dir, fn) for fn in os.listdir(tex_dir) if fn.endswith(".png")}


def main():
    args = parse_args()
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)
    setup_logging(out)
    t_start = time.time()
    global PREVIEW_SAMPLE_SCALE
    PREVIEW_SAMPLE_SCALE = args.preview_samples
    worker = bool(args.worker_queue)
    LOG.info("Cube Escape robot generator %s | Blender %s | stage=%s | tier=%s%s", VERSION, bpy.app.version_string,
             args.stage, args.tier, " (worker %s: %s)" % (args.worker_id, args.worker_lanes) if worker else "")
    report = {"generator": VERSION, "blender": bpy.app.version_string, "stage": args.stage, "tier": args.tier}
    load_start = os.getloadavg()[0]     # times are only comparable on an otherwise idle machine
    preview_dir = os.path.join(out, "Previews") if not worker else os.path.join(args.src_dir, "Previews")
    tex_dir = args.use_textures or os.path.join(out, "Textures")
    cache_dir = os.path.join(out, "cache")
    os.makedirs(preview_dir, exist_ok=True)

    with Step("scene"):
        reset_scene()
        coll = get_collection("Robot")
        preview_coll = get_collection("Preview")
    with Step("ImageMagick source textures"):
        src_paths = make_textures.make_all(os.path.join(args.src_dir or out, "SourceTextures"), log=LOG.info,
                                           cache=not args.no_cache or worker)
        load_source_textures(src_paths)
        report["source_textures"] = sorted(os.path.basename(p) for p in src_paths.values())
    with Step("materials"):
        mats = build_materials()
    with Step("geometry"):
        define_joints()
        build_robot()
        apply_proportions()
        floating = check_part_connectivity(report)
        parts_coll = get_collection("Robot_Parts")
        part_objs = assemble_groups(mats, parts_coll)
        for o in part_objs.values():
            apply_weighted_normals(o)
        validate_skins()            # needs the slots and variants the geometry registered
        report["slots"] = {sl: list(vs) for sl, vs in SLOTS.items()}
        report["part_objects"] = {k: tri_count(o.data) for k, o in part_objs.items()}
        LOG.info("  %d parts in %d objects; slots: %s", report["parts"], len(part_objs),
                 ", ".join("%s=%s" % (k, "/".join(v)) for k, v in SLOTS.items()))
    check_categories()              # on the full clip list, before a --groups filter
    if args.groups:
        keep = set(args.groups.split(",")) | {"Basis"}
        ANIMS[:] = [a for a in ANIMS if a.group in keep]
        LOG.warning("animation filter: %s (%d clips)", sorted(keep), len(ANIMS))
    with Step("uv") if not (worker and args.skip_bake) else _Skip():
        if worker and args.skip_bake:
            raise _SkipBlock()          # the QA does not look at UVs
        if worker:
            if not unwrap(list(part_objs.values()), args.uv_cache, require=True):
                raise RuntimeError("render worker: no cached UVs in %s" % args.uv_cache)
        else:
            unwrap(list(part_objs.values()), cache_dir, read=not args.no_cache)

    if part_objs["Body"].data.uv_layers:
        PERFORATION["cells"] = perforation_cells(part_objs["Body"])
    queue, procs = None, []
    if not args.no_cache:
        RENDER_CACHE["dir"] = os.path.join(args.src_dir or out, "cache", "render")
    if not worker and args.stage == "full" and not args.no_cache:
        for kind in ("qa", "render"):
            pruned = prune_qa_cache(os.path.join(out, "cache", kind))
            if pruned:
                LOG.info("  %s cache: removed %d entries unused for %d days", kind, pruned, QA_CACHE_DAYS)
    if not worker and args.stage == "full":
        only = set(filter(None, args.qa_types.split(",")))
        type_names = [t for t in list(ROBOT_TYPES) + QA_FORMS if t != "Classic" and (not only or t in only)]
        jobs = make_jobs(type_names, {a.name for a in ANIMS}, args, args.workers + 1)
        queue = JobQueue.create(os.path.join(out, "logs", "jobs"), jobs)
        n_qa_jobs = sum(j["lane"] == "qa" for j in jobs)
        # type FBX alone do not justify render workers (quick: the main process exports them, they are small)
        has_render = any(j["lane"] == "render" and j["kind"] != "fbx" for j in jobs)
        n_render = min(args.render_workers, args.workers if args.workers < 2 else args.workers - 1) if has_render else 0
        # about 600 QA frames (2-3 s) per worker at least: a worker costs 2 s to start and slows the main process
        qa_frames = sum(j["cost"] for j in jobs if j["lane"] == "qa")
        n_qa = min(args.workers - n_render, n_qa_jobs, max(1, qa_frames // 600))
        report["workers"] = {"qa": n_qa, "render": n_render, "packages": len(jobs)}
        with Step("start QA workers"):
            procs += start_workers(0, n_qa, ["qa"], args, out, queue)

    if args.skip_bake and not args.use_textures and not worker:
        LOG.warning("skip-bake: procedural materials only (debug run)")
    with Step("bake textures") if not args.skip_bake else _Skip():
        if args.skip_bake:
            raise _SkipBlock()
        if args.use_textures:
            tex_paths = textures_in(args.use_textures)
        else:
            tex_paths = bake_textures(list(part_objs.values()), mats, args.tex_size, args.ao_samples, tex_dir,
                                      None if args.no_cache else cache_dir, args.check_compose, args.bake_device,
                                      None if args.no_render else os.path.join(preview_dir, ".compare"))
        final = [build_baked_material(tex_paths), build_baked_material(tex_paths, "M_RobotFabric",
                                                                        SKINS[DEFAULT_SKIN]["fabric"]),
                 mats["EyeScreen"], mats["Display"]]
        for o in part_objs.values():
            remap_materials(o, final)
        report["textures"] = sorted(os.path.basename(p) for p in tex_paths.values())
    if queue is not None and report["workers"]["render"]:
        with Step("start render workers"):
            procs += start_workers(len(procs), report["workers"]["render"], ["render", "qa"], args, out, queue,
                                   textures=tex_dir)
    with Step("default mesh"):
        robot_mesh = combine_objects([part_objs[g] for g in config_groups(default_config())], "Robot_Mesh", coll)
        report["triangles"] = tri_count(robot_mesh.data)
        report["vertices"] = len(robot_mesh.data.vertices)
        report["materials"] = [m.name for m in robot_mesh.data.materials]
        LOG.log(logging.DEBUG if worker else logging.INFO, "  Robot_Mesh (default look): %d triangles, %d vertices, "
                "materials %s", report["triangles"], report["vertices"], report["materials"])
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
    with Step("forms"):
        make_forms(part_objs, get_collection("Robot_Forms"), arm_ob)
        add_preview_tool(arm_ob, preview_coll)
        report["bones"] = [b.name for b in arm_ob.data.bones]
    with Step("animations"):
        actions = build_animations(arm_ob, quiet=worker)
        report["animations"] = {a.name: {"frames": a.frames, "loop": a.loop, "description": a.desc, "group": a.group,
                                         "unity": a.unity} for a in ANIMS}
    ctx = JobContext(part_objs, coll, preview_coll, arm_ob, actions, robot_mesh, preview_dir, tex_dir,
                     None if args.no_cache else os.path.join(args.src_dir or out, "cache", "qa"), args.src_dir or out)
    if worker:
        ready = time.time() - t_start
        LOG.info("worker ready after %.1f s", ready)
        count, busy = JobQueue(args.worker_queue).work(ctx, args.worker_lanes.split(","), "w%s" % args.worker_id)
        with open(os.path.join(out, "worker.json"), "w", encoding="utf-8") as fh:
            json.dump({"lanes": args.worker_lanes, "ready": round(ready, 1), "packages": count, "busy": round(busy, 1),
                       "total": round(time.time() - t_start, 1), "qa_cached": ctx.qa_hits,
                       "render_cached": RENDER_CACHE["hits"], "rendered": RENDER_CACHE["renders"]}, fh)
        LOG.info("worker done: %d packages in %.1f s", count, busy)
        return 0
    write_animation_table(out)
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
        for n in ("Walk", "Run", "RunFast", "Sprint", "CrouchWalk", "Push", "MudWalk", "Crawl"):
            if n in clips:
                LOG.info("  %-10s natural speed %.2f m/s, footsteps %s", n, clips[n]["naturalSpeed"],
                         [f for f, _ in clips[n]["footsteps"]])
        for n in ("Idle", "Crouch", "CrouchDeep", "CrouchWalk", "SitIdle", "CrawlIdle", "Crawl"):
            if n in clips:
                LOG.info("  %-10s height %.3f..%.3f m", n, clips[n]["height"]["min"], clips[n]["height"]["max"])
        LOG.info("  width/core width: %s", ", ".join("%s %.2f/%.2f" % (k, v["width"], v["coreWidth"])
                                                     for k, v in bounds.items()))
    with Step("export fbx"):
        export_fbx(os.path.join(out, "CubeEscape_Robot.fbx"), [robot_mesh], arm_ob)
        report["lods"] = {}
        if args.lods:
            report["lods"]["Robot_Mesh"] = export_lods(os.path.join(out, "CubeEscape_Robot.fbx"), robot_mesh, coll,
                                                       arm_ob)
        export_fbx(os.path.join(out, "CubeEscape_Robot_Modular.fbx"), list(part_objs.values()), arm_ob,
                   animations=False)
        report["forms"] = export_forms(os.path.join(out, "Forms"), part_objs, robot_mesh, coll, arm_ob, tex_dir)
        # the robot types are exported by "fbx" work packages in parallel (export_type_fbx)
        capsules = hit_capsules(robot_mesh, arm_ob)
        report["hit_capsules"] = {c["bone"]: c["coverage"] for c in capsules}
        LOG.info("  hit capsules: %s", ", ".join("%s %.0f%%" % (c["bone"], c["coverage"] * 100) for c in capsules))
        write_config(out, clips, bounds, capsules)
        write_looks(out)
    with Step("viewer data"):
        vcfg = viewer_config()
        vcfg["clips"] = clips
        vcfg["typeBounds"] = bounds
        form_objs = [o for objs in FORM_OBJS.values() for o in objs.values()]
        export_viewer.export(os.path.join(out, "ViewerData"), list(part_objs.values()) + form_objs, arm_ob, ANIMS,
                             actions, tex_dir, vcfg, LOG)
    with Step("work packages (main process)"):
        ctx.camera()                # the .blend keeps its preview scene
        count, busy = queue.work(ctx, ["render", "qa"], "main")
        LOG.info("  main process: %d packages in %.1f s", count, busy)
    with Step("wait for workers"):
        worker_stats = wait_workers(procs)
        worker_stats["main"] = {"packages": count, "busy": round(busy, 1), "qa_cached": ctx.qa_hits,
                                "render_cached": RENDER_CACHE["hits"], "rendered": RENDER_CACHE["renders"]}
        results = queue.results()
    exported = {}
    for job, res in results:
        if job["kind"] == "fbx":
            exported.update(res["result"])
    missing = [t for t in ROBOT_TYPES if t not in exported]
    if missing:
        raise RuntimeError("type FBX missing: %s" % missing)
    report["type_fbx"] = {}
    for tname in ROBOT_TYPES:               # report in type order, whichever process exported it
        info = dict(exported[tname])
        lods = info.pop("lods", None)
        if lods:
            report["lods"][tname] = lods
        report["type_fbx"][tname] = info
    with Step("qa results"):
        report["qa"] = merge_qa(results, type_names)
        for name, entry in report["qa"]["Classic"]["actions"].items():
            log_qa_entry("Classic", name, entry)
        for tname in [t for t in report["qa"] if t != "Classic"]:      # quick: also the owners of type emotes
            qa_t = report["qa"][tname]
            bad = [n for n, e in qa_t["actions"].items() if qa_problem(e)]
            LOG.log(logging.WARNING if bad else logging.INFO, "  QA[%s] %d clips, problems: %s", tname,
                    len(qa_t["actions"]), bad or "-")
    if not args.no_render:
        with Step("preview sheets"):
            finish_previews(results, preview_dir, report, tex_dir)
    ctx.cleanup()
    shutil.rmtree(queue.path, ignore_errors=True)
    with Step("save blend"):
        assign_action(arm_ob, actions["Idle"])
        bpy.context.scene.frame_set(0)
        preview_coll.hide_render = False
        bpy.context.scene.frame_end = 120
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(out, "CubeEscape_Robot.blend"))
    total = time.time() - t_start
    kinds = collections.defaultdict(float)
    for job, res in results:
        kinds[job["kind"]] += res["seconds"]
    load = (round(load_start, 1), round(os.getloadavg()[0], 1))
    report["timings"] = {"steps": dict(STEP_TIMES), "total": round(total, 1), "budget": args.budget,
                         "load": {"start": load[0], "end": load[1], "cores": os.cpu_count()},
                         "packages": {k: round(v, 1) for k, v in kinds.items()}, "workers": worker_stats,
                         "qa_cached": sum(w.get("qa_cached", 0) for w in worker_stats.values()),
                         "render_cached": sum(w.get("render_cached", 0) for w in worker_stats.values()),
                         "rendered": sum(w.get("rendered", 0) for w in worker_stats.values())}
    write_report(out, report)
    slow = sorted(STEP_TIMES.items(), key=lambda kv: -kv[1])[:4]
    LOG.log(logging.WARNING if total > args.budget else logging.INFO, "time: %.0f s (tier %s, budget %d s)%s - "
            "slowest: %s", total, args.tier, args.budget, " OVER BUDGET" if total > args.budget else "",
            ", ".join("%s %.0f s" % kv for kv in slow))
    busy = load[0] > os.cpu_count() / 2       # at the start: other processes (the end includes our own workers)
    LOG.log(logging.WARNING if busy else logging.INFO, "  system load %.1f at the start (%.1f at the end) on %d cores%s",
            load[0], load[1], os.cpu_count(), " - other processes were running, times are not comparable" if busy else "")
    LOG.info("  work packages (process seconds): %s", ", ".join("%s %.0f" % kv for kv in sorted(kinds.items())))
    if report["timings"]["render_cached"]:
        LOG.info("  render cache: %d images reused, %d rendered (cache/render)", report["timings"]["render_cached"],
                 report["timings"]["rendered"])
    if report["timings"]["qa_cached"]:
        LOG.info("  QA cache: %d clip results reused (cache/qa; --no-cache checks everything again)",
                 report["timings"]["qa_cached"])
    problems = bool(floating)
    for label, qa in report.get("qa", {}).items():
        for name, entry in qa["actions"].items():
            if qa_problem(entry):
                problems = True
    LOG.info("finished %s", "WITH QA WARNINGS" if problems else "OK")
    return 0


def viewer_config():
    return {
        "slots": [{"id": sl, "label": SLOT_LABELS.get(sl, sl), "variants": list(vs), "accessory": sl in ACCESSORY_SLOTS}
                  for sl, vs in SLOTS.items()],
        "robotTypes": [dict(name=n, **spec) for n, spec in ROBOT_TYPES.items()],
        "skins": [skin_config(n) for n in SKINS],
        "finishes": [{"id": n, "roughness": r, "metalOffset": m} for n, (r, m) in FINISH_PARAMS.items()],
        "badges": [{"id": b, "texture": BADGE_TEXTURES[i // 4], "channel": i % 4} for i, b in enumerate(BADGES)],
        "linePatterns": [{"id": n, "texture": LINE_TEXTURES[i // 4], "channel": i % 4}
                         for i, n in enumerate(LINE_PATTERNS)],
        "zones": [{"id": z, "texture": ZONE_TEXTURES[i // 4], "channel": i % 4, "parts": list(parts)}
                  for i, (z, parts) in enumerate(PAINT_ZONES.items())],
        "glowParts": list(GLOW_PARTS),
        # particle emitters of accessories; position/direction in character space (Unity local: right, up, forward)
        "emitters": [dict(e, position=char_space(Vector(e["position"])), direction=char_space(Vector(e["direction"])))
                     for e in EMITTERS],
        "springBones": list(SPRINGS),
        "unityTextures": [{"file": "Unity/" + n, "channels": d} for n, d in UNITY_TEXTURES.items()],
        "specials": [dict(id=n, **{k: (list(parse_color(v)) if k.startswith("color") else v) for k, v in d.items()})
                     for n, d in SPECIALS.items()],
        "forms": [dict(id=n, **d) for n, d in FORMS.items()],
        "perforation": dict(PERFORATION),
        "linePaint": LINE_PAINT,
        "layers": [catalog_entry(n, LAYER_TEXTURES, i, LAYER_DEFAULTS.get(n)) for i, n in enumerate(LAYERS)],
        "materials": [catalog_entry(n, MATERIAL_TEXTURES, i, MATERIAL_DEFAULTS[n]) for i, n in enumerate(MATERIALS)],
        "weathering": {"coverSoft": COVER_SOFT, "wearDefault": WEAR_DEFAULT, "bareMetal": [float(v) for v in BARE_METAL],
                       "equalised": EQUALISED_LAYERS},
        "expressions": list(make_textures.EXPRESSIONS),
        "expressionStyles": make_textures.EXPRESSION_STYLES,
        "blink": make_textures.BLINK,
        "displays": list(make_textures.DISPLAYS),
        "eyeGrid": list(make_textures.EYE_GRID),
        "displayGrid": list(make_textures.DISPLAY_GRID),
        "displayCellHeight": make_textures.DISPLAY_CELL[1] / float(make_textures.DISPLAY_ATLAS_H),
        "displayAnimations": [{"name": n, "row": i, "frames": f, "fps": fps}
                              for i, (n, f, fps) in enumerate(make_textures.DISPLAY_ANIMS)],
        "flipbook": {"texture": "T_Robot_DisplayFlipbook.png", "grid": list(make_textures.FLIPBOOK_GRID),
                     "cell": [make_textures.FLIPBOOK_CELL[0] / float(make_textures.FLIPBOOK_SIZE[0]),
                              make_textures.FLIPBOOK_CELL[1] / float(make_textures.FLIPBOOK_SIZE[1])]},
        "spinParts": sorted(SPIN_PARTS),
        "lookVersion": LOOK_VERSION,
        "groupLabels": GROUP_LABELS,
        "clipCategories": [{"id": c, "label": label, "clips": [n for n in names if any(a.name == n for a in ANIMS)]}
                           for c, (label, names) in CLIP_CATEGORIES.items()],
    }


def catalog_entry(name, textures, i, defaults):
    """A layer or material for robot_config.json: mask channel plus default colour (linear) and surface."""
    e = {"id": name, "texture": textures[i // 4], "channel": i % 4}
    if defaults:
        e.update(color=list(parse_color(defaults["color"])), roughness=defaults["roughness"],
                 metalOffset=defaults["metalOffset"])
        if "base" in defaults:
            e["base"] = list(parse_color(defaults["base"]))
    return e


def skin_config(sid):
    """A skin for robot_config.json: palette as linear lists (flat, as before 1.10) plus the 1.10 fields."""
    rec = SKIN_DATA[sid]
    cfg = dict(name=sid, **{k: list(v) for k, v in rec["palette"].items()})
    cfg.update({"zones2": rec["zones2"], "finish": rec["finish"], "wear": rec["wear"],
                "material": dict(rec["material"], color=list(rec["material"]["color"])) if rec["material"] else None,
                "layers": [dict(ly, color=list(ly["color"])) for ly in rec["layers"]],
                "badges": [{"id": b["id"], "color": list(b["color"])} for b in rec["badges"]],
                "lines": dict(rec["lines"], color=list(rec["lines"]["color"])) if rec["lines"] else None,
                "special": (dict(rec["special"], color=list(rec["special"]["color"]),
                                 color2=list(rec["special"]["color2"])) if rec["special"] else None),
                "form": rec["form"] or "None",
                "parts": rec["parts"], "theme": rec["theme"], "since": rec["since"], "stage": rec["stage"],
                "startSet": rec["startSet"], "concept": rec["concept"]})
    for k in ("expression", "display"):
        if rec[k]:
            cfg[k] = rec[k]
    return cfg


def validate_skins():
    """Checks of the skin data that need the model (slots, expressions, displays); run once at the start."""
    displays = set(make_textures.DISPLAYS) | {n for n, _, _ in make_textures.DISPLAY_ANIMS}
    for sid, rec in SKIN_DATA.items():
        for slot, var in rec["parts"].items():
            if slot not in SLOTS or var not in SLOTS[slot]:
                raise ValueError("skin %s: unknown part %s=%s" % (sid, slot, var))
        if rec["expression"] and rec["expression"] not in make_textures.EXPRESSIONS:
            raise ValueError("skin %s: unknown expression %s" % (sid, rec["expression"]))
        if rec["display"] and rec["display"] not in displays:
            raise ValueError("skin %s: unknown display %s" % (sid, rec["display"]))
    for tname, spec in ROBOT_TYPES.items():
        if spec["skin"] not in SKIN_DATA:
            raise ValueError("robot type %s: unknown skin %s" % (tname, spec["skin"]))


# Hit capsules (1.13): one capsule per body bone, measured on the default mesh in the rest pose. Game: convert the
# points into bone space once (bind pose) and let the bone carry them; also the shape for a later ragdoll.
CAPSULE_BONES = ("Pelvis", "Spine", "Head", "UpperArm_L", "LowerArm_L", "Hand_L", "UpperArm_R", "LowerArm_R", "Hand_R",
                 "UpperLeg_L", "LowerLeg_L", "Foot_L", "UpperLeg_R", "LowerLeg_R", "Foot_R")


def hit_capsules(ob, arm_ob):
    """[{bone, a, b, radius, coverage}] in character space; radius = 93rd percentile of the distance to the bone
    axis, ends 0.3 r inside the 2nd/98th percentile along it; coverage = share of the bone's vertices inside (+5 mm)."""
    me = ob.data
    names = {vg.index: vg.name for vg in ob.vertex_groups}
    co = np.empty(len(me.vertices) * 3, dtype=np.float64)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    owner = np.array([names[v.groups[0].group] if v.groups else "" for v in me.vertices])
    out = []
    for bname in CAPSULE_BONES:
        pts = co[owner == bname]
        if len(pts) < 8:
            continue
        b = arm_ob.data.bones[bname]
        head, axis = np.array(b.head_local), np.array((b.tail_local - b.head_local).normalized())
        t = (pts - head) @ axis
        radial = np.linalg.norm((pts - head) - np.outer(t, axis), axis=1)
        r = float(np.percentile(radial, 93))
        t0, t1 = float(np.percentile(t, 2)), float(np.percentile(t, 98))
        mid = (t0 + t1) / 2
        t0, t1 = min(mid, t0 + 0.3 * r), max(mid, t1 - 0.3 * r)      # boxy parts: corners sit at the ends
        a, bb = head + axis * t0, head + axis * t1
        seg = bb - a
        u = np.clip(((pts - a) @ seg) / max(float(seg @ seg), 1e-9), 0, 1)
        dist = np.linalg.norm(pts - (a + np.outer(u, seg)), axis=1)
        out.append({"bone": bname, "a": char_space(Vector(a)), "b": char_space(Vector(bb)), "radius": round(r, 4),
                    "coverage": round(float((dist <= r + 0.005).mean()), 3)})
    return out


def write_config(out, clips, bounds, capsules=None):
    """robot_config.json: slots, variants, robot types, skins, atlas layout, clip data - for Unity and the viewer."""
    cfg = viewer_config()
    cfg["generator"] = VERSION
    cfg["coordinates"] = "character space [right, up, forward] in m (= Unity local space), angles in degrees"
    cfg["atlasCells"] = {
        "eyes": {n: atlas_cell("eyes", i) for i, n in enumerate(make_textures.EXPRESSIONS)},
        "displays": {n: atlas_cell("displays", i) for i, n in enumerate(make_textures.DISPLAYS)},
        "displayAnimations": {n: flipbook_cell(i, 0) for i, (n, _, _) in enumerate(make_textures.DISPLAY_ANIMS)},
    }
    cfg["typeBounds"] = bounds
    cfg["lookFormat"] = {"format": LOOK_FORMAT, "version": LOOK_VERSION, "schema": "robot_look.schema.json"}
    cfg["clips"] = clips
    cfg["hitCapsules"] = capsules or []
    with open(os.path.join(out, "robot_config.json"), "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)


# ---------------------------------- player looks ----------------------------------
# A look is what the player saves. Names of skins, variants, expressions and displays are IDs: never rename them.
LOOK_FORMAT = "CubeEscapeRobotLook"
LOOK_VERSION = 6            # 1 = viewer 1.6 (no format/version), 2 = 1.7-1.9, 3 = 1.10, 4 = 1.11, 5 = 1.13 (migrated)
LOOK_COLORS = {"armor": "armor", "mech": "mech", "metal": "metal", "lights": "accent", "screen": "screen",
               "eyes": "eye", "visor": "visor", "fabric": "fabric", "armor2": "armor2"}     # look key -> skin key


def type_look(tname):
    spec = ROBOT_TYPES[tname]
    variants = default_config()
    variants.update(spec["variants"])
    skin = SKINS[spec["skin"]]
    look = {"format": LOOK_FORMAT, "version": LOOK_VERSION, "name": tname, "variants": variants}
    for k, sk in LOOK_COLORS.items():
        r, g, b = skin[sk]
        look[k] = {"r": round(r, 4), "g": round(g, 4), "b": round(b, 4)}
    rec = SKIN_DATA[spec["skin"]]
    rgb = lambda c: {"r": round(c[0], 4), "g": round(c[1], 4), "b": round(c[2], 4)}   # noqa: E731
    lines = rec["lines"] or {"pattern": "None", "color": skin["accent"], "strength": 1.0}
    look.update({"skin": spec["skin"], "finish": rec["finish"], "glow": 1.0, "expression": spec["expression"],
                 "display": spec["display"], "autoExpression": True, "autoDisplay": True,
                 "zones2": list(rec["zones2"]),
                 "badges": [{"id": b["id"], "color": rgb(b["color"])} for b in rec["badges"]],
                 "lines": {"pattern": lines["pattern"], "color": rgb(lines["color"]), "strength": lines["strength"]},
                 "material": ({"id": rec["material"]["id"], "color": rgb(rec["material"]["color"])} if rec["material"]
                              else {"id": "None", "color": rgb(skin["armor"])}),
                 "layers": [{"id": ly["id"], "amount": ly["amount"], "color": rgb(ly["color"])} for ly in rec["layers"]],
                 "wear": rec["wear"], "special": special_look(rec["special"]), "form": rec["form"] or "None"})
    return look


def special_look(sp):
    """Look field "special" (version 5); {"id": "None"} = no special material."""
    if not sp:
        return {"id": "None"}
    rgb = lambda c: {"r": round(c[0], 4), "g": round(c[1], 4), "b": round(c[2], 4)}   # noqa: E731
    return {"id": sp["id"], "color": rgb(sp["color"]), "color2": rgb(sp["color2"]),
            "strength": round(sp["strength"], 3), "speed": round(sp["speed"], 3)}


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
        "display": {"enum": list(make_textures.DISPLAYS) + [n for n, _, _ in make_textures.DISPLAY_ANIMS]},
        "autoExpression": {"type": "boolean"},
        "autoDisplay": {"type": "boolean"},
        "skin": {"enum": list(SKINS) + ["Custom"], "description": "skin the look started from (version 3)"},
        "badges": {"type": "array", "maxItems": MAX_BADGES, "items": {
            "type": "object", "required": ["id", "color"], "additionalProperties": False,
            "properties": {"id": {"enum": BADGES}, "color": rgb}}},
        "zones2": {"type": "array", "uniqueItems": True, "items": {"enum": list(PAINT_ZONES)},
                   "description": "parts painted in armor2 (version 3)"},
        "material": {"type": "object", "required": ["id"], "additionalProperties": False,
                     "description": "armour material (version 4)",
                     "properties": {"id": {"enum": ["None"] + MATERIALS}, "color": rgb}},
        "layers": {"type": "array", "description": "surface layers (version 4)", "items": {
            "type": "object", "required": ["id", "amount"], "additionalProperties": False,
            "properties": {"id": {"enum": list(LAYER_DEFAULTS)}, "amount": {"type": "number", "minimum": 0, "maximum": 1},
                           "color": rgb}}},
        "wear": {"type": "number", "minimum": 0, "maximum": 1,
                 "description": "0 clean, 0.5 as baked, 1 battered (version 4)"},
        "special": {"type": "object", "required": ["id"], "additionalProperties": False,
                    "description": "special material (version 5): runtime effect on top of the skin",
                    "properties": {"id": {"enum": ["None"] + list(SPECIALS)}, "color": rgb, "color2": rgb,
                                   "strength": {"type": "number", "minimum": 0, "maximum": 1},
                                   "speed": {"type": "number", "minimum": 0, "maximum": 4}}},
        "form": {"enum": ["None"] + list(FORMS), "description": "body form (version 6): own geometry, holes or "
                                                                "extra parts; follows the skin"},
        "lines": {"type": "object", "required": ["pattern"], "additionalProperties": False, "properties": {
            "pattern": {"enum": ["None"] + LINE_PATTERNS}, "color": rgb,
            "strength": {"type": "number", "minimum": 0, "maximum": 2}}},
    }
    props.update({k: rgb for k in LOOK_COLORS})
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": "robot_look.schema.json",
            "title": "Cube Escape robot look (version %d)" % LOOK_VERSION,
            "description": "Player customisation. Names are permanent IDs; unknown values fall back to the default "
                           "with a warning. Versions 1-5 are migrated on load (missing fields take their defaults).",
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
