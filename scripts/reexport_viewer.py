"""Re-exports only the viewer data from a saved CubeEscape_Robot.blend (no rebuild, no bake).
Usage: Blender -b output/CubeEscape_Robot.blend --python scripts/reexport_viewer.py -- [--out output]"""
import collections
import json
import logging
import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import export_viewer  # noqa: E402

args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
out = os.path.abspath(args[args.index("--out") + 1]) if "--out" in args else os.path.dirname(bpy.data.filepath)
logging.basicConfig(level=logging.INFO, format="%(message)s")
report = json.load(open(os.path.join(out, "logs", "robot_report.json"), encoding="utf-8"))
config = json.load(open(os.path.join(out, "robot_config.json"), encoding="utf-8"))
config.pop("atlasCells", None)
config.pop("generator", None)
Anim = collections.namedtuple("Anim", "name frames loop fn desc group unity")
anims = [Anim(n, a["frames"], a["loop"], None, a["description"], a["group"], a["unity"])
         for n, a in report["animations"].items()]
arm = bpy.data.objects["Robot_Armature"]
parts = [o for o in bpy.data.collections["Robot_Parts"].objects if o.type == "MESH"]
order = list(report["part_objects"].keys())
parts.sort(key=lambda o: order.index("Body" if o.name == "Robot_Body" else o.name) if
           ("Body" if o.name == "Robot_Body" else o.name) in order else 999)
actions = {a.name: bpy.data.actions[a.name] for a in anims}
export_viewer.export(os.path.join(out, "ViewerData"), parts, arm, anims, actions, os.path.join(out, "Textures"),
                     config, logging.getLogger("reexport"))
