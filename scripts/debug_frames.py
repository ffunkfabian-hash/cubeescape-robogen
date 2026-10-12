"""Debug helper: renders selected frames of an action from the saved .blend (side + front views).
Usage: Blender -b <blend> --python debug_frames.py -- <action> <out.png> <frame,frame,...> [view]"""
import sys, os, bpy, subprocess
from mathutils import Vector
argv = sys.argv[sys.argv.index("--") + 1:]
action, out, frames = argv[0], argv[1], [int(f) for f in argv[2].split(",")]
view = argv[3] if len(argv) > 3 else "side"
scene = bpy.context.scene
arm = bpy.data.objects["Robot_Armature"]
act = bpy.data.actions[action]
arm.animation_data.action = act
arm.animation_data.action_slot = act.slots[0]
scene.render.engine = "BLENDER_EEVEE"
scene.eevee.taa_render_samples = 8
scene.render.resolution_x = scene.render.resolution_y = 360
cam = scene.camera
if cam is None:
    cam = bpy.data.objects.new("DbgCam", bpy.data.cameras.new("DbgCam"))
    scene.collection.objects.link(cam)
    scene.camera = cam
    sun = bpy.data.objects.new("DbgSun", bpy.data.lights.new("DbgSun", "SUN"))
    sun.data.energy = 4
    sun.rotation_euler = (0.8, 0.2, -0.6)
    scene.collection.objects.link(sun)
    w = scene.world or bpy.data.worlds.new("W"); scene.world = w
cam.data.type = "ORTHO"; cam.data.ortho_scale = 1.6
loc = {"side": (5, -0.2, 0.6), "front": (0, -5, 0.6), "iso": (-4, -4, 3.6)}[view]
cam.location = loc
cam.rotation_euler = (Vector((0, -0.1, 0.55)) - cam.location).to_track_quat("-Z", "Y").to_euler()
files = []
for f in frames:
    scene.frame_set(f)
    p = out + ".%03d.png" % f
    scene.render.filepath = p
    bpy.ops.render.render(write_still=True)
    files.append(p)
subprocess.run(["magick", "montage"] + files + ["-tile", "%dx1" % len(files), "-geometry", "+2+2", "-label", "%f", out])
for p in files: os.remove(p)
