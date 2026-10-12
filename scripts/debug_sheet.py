"""Debug helper: renders several actions into one labelled sheet (one row per action).
Usage: Blender -b <blend> --python debug_sheet.py -- <out.png> <view: side|front|iso> Name:f1,f2,... [Name:...]"""
import os, subprocess, sys, bpy
from mathutils import Vector
argv = sys.argv[sys.argv.index("--") + 1:]
out, view, specs = argv[0], argv[1], argv[2:]
scene = bpy.context.scene
arm = bpy.data.objects["Robot_Armature"]
scene.render.engine = "BLENDER_EEVEE"
scene.eevee.taa_render_samples = 8
scene.render.resolution_x = scene.render.resolution_y = 260
cam = scene.camera
if cam is None:
    cam = bpy.data.objects.new("DbgCam", bpy.data.cameras.new("DbgCam"))
    scene.collection.objects.link(cam)
    scene.camera = cam
    sun = bpy.data.objects.new("DbgSun", bpy.data.lights.new("DbgSun", "SUN"))
    sun.data.energy = 4
    sun.rotation_euler = (0.8, 0.2, -0.6)
    scene.collection.objects.link(sun)
    scene.world = scene.world or bpy.data.worlds.new("W")
cam.data.type = "ORTHO"
cam.data.ortho_scale = float(os.environ.get("DBG_SCALE", "1.75"))
cam.location = {"side": (5, -0.2, 0.6), "rside": (-5, -0.2, 0.6), "front": (0, -5, 0.6), "iso": (-4, -4, 3.6)}[view]
cam.rotation_euler = (Vector((0, -0.05, 0.6)) - cam.location).to_track_quat("-Z", "Y").to_euler()
rows = []
for spec in specs:
    name, frames = spec.split(":")
    act = bpy.data.actions[name]
    tool = bpy.data.objects.get("Preview_Tool")
    if tool is not None:
        tool.hide_render = name not in ("ToolAim", "ToolUse", "Scan")
    arm.animation_data.action = act
    arm.animation_data.action_slot = act.slots[0]
    files = []
    for f in [int(x) for x in frames.split(",")]:
        scene.frame_set(f)
        p = "%s.%s.%03d.png" % (out, name, f)
        scene.render.filepath = p
        bpy.ops.render.render(write_still=True)
        files += ["-label", "%s f%d" % (name, f), p]
    row = "%s.%s.row.png" % (out, name)
    subprocess.run(["magick", "montage"] + files + ["-tile", "x1", "-geometry", "+2+2", "-pointsize", "13", row], check=True)
    rows.append(row)
    for p in files[2::3]:
        os.remove(p)
subprocess.run(["magick"] + rows + ["-append", out], check=True)
for r in rows:
    os.remove(r)
