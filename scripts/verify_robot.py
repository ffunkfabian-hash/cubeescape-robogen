"""Cube Escape robot - independent verification of the generated files.

Run in a fresh Blender process against the saved .blend:
    Blender -b output/CubeEscape_Robot.blend --python-exit-code 1 --python verify_robot.py -- [--out output]

Checks
    .blend : objects, clean transforms, bone hierarchy, rigid weights, material/textures, actions, UVs
    .fbx   : re-import into an empty scene, meshes/bones/material/takes present, scale and axes,
             every take reproduces the original animation (bounding box per sampled frame)
Writes <out>/logs/verify_report.json and exits with code 1 if any check fails.
"""

import json
import os
import sys

import bpy
import numpy as np
from mathutils import Vector

ARGS = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = os.path.abspath(ARGS[ARGS.index("--out") + 1]) if "--out" in ARGS else os.path.dirname(bpy.data.filepath)

REQUIRED = ["Root", "Pelvis", "Spine", "Head", "Shoulder_L", "Shoulder_R", "UpperArm_L", "UpperArm_R", "LowerArm_L",
            "LowerArm_R", "Hand_L", "Hand_R", "UpperLeg_L", "UpperLeg_R", "LowerLeg_L", "LowerLeg_R", "Foot_L",
            "Foot_R"]
HIERARCHY = {"Pelvis": "Root", "Spine": "Pelvis", "Head": "Spine", "Shoulder_L": "Spine", "Shoulder_R": "Spine",
             "UpperArm_L": "Shoulder_L", "UpperArm_R": "Shoulder_R", "LowerArm_L": "UpperArm_L",
             "LowerArm_R": "UpperArm_R", "Hand_L": "LowerArm_L", "Hand_R": "LowerArm_R", "UpperLeg_L": "Pelvis",
             "UpperLeg_R": "Pelvis", "LowerLeg_L": "UpperLeg_L", "LowerLeg_R": "UpperLeg_R", "Foot_L": "LowerLeg_L",
             "Foot_R": "LowerLeg_R"}
ANIMS = ["Idle", "Walk", "Run", "Jump", "Fall", "Land", "Crouch", "Interact", "Push", "Death"]
_report = os.path.join(OUT, "logs", "robot_report.json")
if os.path.exists(_report):
    with open(_report, encoding="utf-8") as _fh:
        ANIMS = list(json.load(_fh).get("animations", {})) or ANIMS
TEXTURES = ["T_Robot_BaseColor.png", "T_Robot_MetallicSmoothness.png", "T_Robot_Normal.png", "T_Robot_Emission.png",
            "T_Robot_Occlusion.png", "T_Robot_ColorMask.png", "T_Robot_LightMask.png", "T_Robot_EyeAtlas.png", "T_Robot_DisplayAtlas.png",
            "T_Robot_DisplayFlipbook.png"]
MASK_TEXTURES = ["T_Robot_BadgeMask.png", "T_Robot_BadgeMask2.png", "T_Robot_BadgeMask3.png", "T_Robot_LineMask.png", "T_Robot_LineMask2.png",
                 "T_Robot_ZoneMask.png", "T_Robot_ZoneMask2.png",       # 1.10: RGBA masks (alpha = a mask channel)
                 "T_Robot_LayerMask.png", "T_Robot_LayerMask2.png", "T_Robot_MaterialMask.png",
                 "T_Robot_MaterialMask2.png"]                            # 1.11
START_SET_1_13 = {"Glass", "Ghost", "Aurora", "Rainbow"}
SPECIALS_1_13 = ["Glass", "Hologram", "Glitch", "Iridescent", "Sketch", "Rainbow", "Galaxy"]
FORMS_1_14 = ["Faceted", "Voxel", "Perforated", "Wired"]
START_SET_1_12 = {"Miner", "Samurai", "Steam", "Signal", "Wood", "Celest"}
START_SET_1_11 = {"Rust", "Moss", "Arctic", "Inferno", "Circuit"}
# 1.12 Bolt-On: attachments that a robot type must carry (so that the QA of all types covers them in every clip)
ATTACHMENTS_1_12 = {"AccHead": {"MinerHelmet", "Kabuto", "Spikes", "Ears", "Dish", "Chimney", "Halo", "Sprout"},
                    "AccEar": {"LensEye", "Gears", "Dial"}, "AccFace": {"LensRims", "SpaceRing"},
                    "AccBack": {"Pouches"}}
START_SET_1_10 = {"Classic", "Shadow", "Hazard", "Medic", "Volt", "Gold", "Candy"}
MAIN_MATERIALS = {"M_Robot", "M_RobotEyes", "M_RobotDisplay"}
import math  # noqa: E402

from mathutils import Matrix  # noqa: E402


def unturn(objs):
    """generate_robot.py turns the armature 180 degrees about Z for the FBX (Unity: the robot looks +Z); turn the
    imported top-level objects back so they compare with the .blend."""
    for o in objs:
        if o.parent is None:
            o.matrix_world = Matrix.Rotation(-math.pi, 4, "Z") @ o.matrix_world
    bpy.context.view_layer.update()

results = []


def check(name, ok, detail=""):
    results.append({"check": name, "ok": bool(ok), "detail": str(detail)})
    print("%s %-55s %s" % ("PASS" if ok else "FAIL", name, detail))


def bbox_of(ob):
    dg = bpy.context.evaluated_depsgraph_get()
    ev = ob.evaluated_get(dg)
    me = ev.to_mesh()
    co = np.empty(len(me.vertices) * 3)
    me.vertices.foreach_get("co", co)
    ev.to_mesh_clear()
    co = co.reshape(-1, 3)
    mw = np.array(ob.matrix_world)
    co = co @ mw[:3, :3].T + mw[:3, 3]
    return co.min(axis=0), co.max(axis=0)


def set_action(arm, act):
    ad = arm.animation_data or arm.animation_data_create()
    ad.action = act
    if act.slots:
        ad.action_slot = act.slots[0]


def verify_blend():
    ob = bpy.data.objects.get("Robot_Mesh")
    arm = bpy.data.objects.get("Robot_Armature")
    check("blend: Robot_Mesh and Robot_Armature exist", ob is not None and arm is not None)
    if ob is None or arm is None:
        return None
    for o in (ob, arm):
        clean = (o.location.length < 1e-6 and Vector(o.rotation_euler).length < 1e-6
                 and (Vector(o.scale) - Vector((1, 1, 1))).length < 1e-6)
        check("blend: %s has identity transform" % o.name, clean, tuple(o.location))
    check("blend: mesh parented to armature", ob.parent == arm)
    mod = next((m for m in ob.modifiers if m.type == "ARMATURE"), None)
    check("blend: armature modifier targets rig", mod is not None and mod.object == arm)
    bones = arm.data.bones
    missing = [b for b in REQUIRED if b not in bones]
    check("blend: required bones present", not missing, missing or len(bones))
    wrong = [b for b, p in HIERARCHY.items() if b in bones and (bones[b].parent is None or bones[b].parent.name != p)]
    check("blend: bone hierarchy", not wrong, wrong)
    me = ob.data
    tris = sum(len(p.vertices) - 2 for p in me.polygons)
    check("blend: triangle budget 5k-12.5k", 5000 <= tris <= 12500, tris)
    check("blend: UV map present", len(me.uv_layers) == 1, [u.name for u in me.uv_layers])
    bad = [v.index for v in me.vertices if len(v.groups) != 1 or abs(v.groups[0].weight - 1.0) > 1e-6]
    check("blend: every vertex rigidly bound to one bone", not bad, len(bad))
    groups = {g.name for g in ob.vertex_groups}
    check("blend: vertex groups match bones", groups <= set(bones.keys()), sorted(groups - set(bones.keys())))
    check("blend: default mesh uses body + eye + display material", {m.name for m in me.materials} == MAIN_MATERIALS,
          [m.name for m in me.materials])
    parts = [o for o in bpy.data.collections["Robot_Parts"].objects if o.type == "MESH"]
    unbound = [o.name for o in parts if o.parent != arm or not any(m.type == "ARMATURE" for m in o.modifiers)]
    check("blend: modular part objects bound to the rig", len(parts) >= 15 and not unbound, (len(parts), unbound))
    bad_parts = [o.name for o in parts if any(len(v.groups) != 1 for v in o.data.vertices)]
    check("blend: modular parts rigidly skinned", not bad_parts, bad_parts)
    imgs = [n.image for n in me.materials[0].node_tree.nodes if n.type == "TEX_IMAGE"]
    missing_files = [i.name for i in imgs if not os.path.exists(bpy.path.abspath(i.filepath))]
    check("blend: material textures exist on disk", imgs and not missing_files, missing_files or len(imgs))
    tex_dir = os.path.join(OUT, "Textures")
    missing_tex = [t for t in TEXTURES if not os.path.exists(os.path.join(tex_dir, t))]
    check("files: exported texture set complete", not missing_tex, missing_tex)
    for t in TEXTURES:
        p = os.path.join(tex_dir, t)
        if os.path.exists(p):
            img = bpy.data.images.load(p, check_existing=True)
            check("files: %s size" % t, tuple(img.size) in ((2048, 2048), (1024, 1024), (2048, 1024), (2048, 2560)),
                  tuple(img.size))
    acts = [a for a in ANIMS if a in bpy.data.actions]
    check("blend: all %d actions present" % len(ANIMS), len(acts) == len(ANIMS), sorted(set(ANIMS) - set(acts)))
    for a in acts:
        act = bpy.data.actions[a]
        fcs = sum(len(cb.fcurves) for layer in act.layers for strip in layer.strips for cb in strip.channelbags)
        check("blend: action %s is a layered action with keys" % a, act.is_action_layered and fcs > 0 and
              len(act.slots) == 1, "%d fcurves, frames %s" % (fcs, tuple(act.frame_range)))
    # rest bounding box: robot stands on the floor, about 1.38 m tall (1.14 Reshape; 1.3 m before)
    arm.animation_data.action = None
    for pb in arm.pose.bones:
        pb.location = (0, 0, 0)
        pb.rotation_quaternion = (1, 0, 0, 0)
        pb.scale = (1, 1, 1)
    bpy.context.view_layer.update()
    lo, hi = bbox_of(ob)
    check("blend: rest pose stands on z=0", abs(lo[2]) < 0.002, round(float(lo[2]), 4))
    check("blend: height 1.2-1.5 m", 1.2 <= hi[2] - lo[2] <= 1.5, round(float(hi[2] - lo[2]), 3))
    visor_y = min(v.co.y for v in ob.data.vertices if v.co.z > 1.05)
    check("blend: faces -Y (visor in front)", visor_y < -0.2, round(visor_y, 3))
    reference = {}
    for a in acts:
        act = bpy.data.actions[a]
        set_action(arm, act)
        s, e = (int(x) for x in act.frame_range)
        frames = sorted({s, (s + e) // 2, e})
        boxes = []
        for f in frames:
            bpy.context.scene.frame_set(f)
            boxes.append(np.concatenate(bbox_of(ob)))
        reference[a] = (frames, boxes)
    return reference


def verify_fbx(reference):
    path = os.path.join(OUT, "CubeEscape_Robot.fbx")
    check("fbx: file exists", os.path.exists(path), path)
    if not os.path.exists(path):
        return
    scene = bpy.context.scene
    before = set(bpy.data.objects)
    before_actions = set(bpy.data.actions)
    bpy.ops.import_scene.fbx(filepath=path, use_anim=True, ignore_leaf_bones=False, automatic_bone_orientation=False)
    new = [o for o in bpy.data.objects if o not in before]
    unturn(new)
    meshes = [o for o in new if o.type == "MESH"]
    arms = [o for o in new if o.type == "ARMATURE"]
    check("fbx: one mesh and one armature imported", len(meshes) == 1 and len(arms) == 1,
          [(o.name, o.type) for o in new])
    if not meshes or not arms:
        return
    ob, arm = meshes[0], arms[0]
    bones = arm.data.bones
    missing = [b for b in REQUIRED if b not in bones]
    check("fbx: required bones present", not missing, missing)
    leaf = [b.name for b in bones if b.name.endswith("_end")]
    check("fbx: no leaf bones", not leaf, leaf)
    wrong = [b for b, p in HIERARCHY.items() if b in bones and (bones[b].parent is None or bones[b].parent.name != p)]
    check("fbx: bone hierarchy", not wrong, wrong)
    check("fbx: body/eye/display materials", {m.name.split(".")[0] for m in ob.data.materials} == MAIN_MATERIALS,
          [m.name for m in ob.data.materials])
    check("fbx: UVs and vertex groups", len(ob.data.uv_layers) >= 1 and len(ob.vertex_groups) >= 18,
          (len(ob.data.uv_layers), len(ob.vertex_groups)))
    imported = [a for a in bpy.data.actions if a not in before_actions]
    names = {a.name.split("|")[-1]: a for a in imported}
    missing_takes = [a for a in ANIMS if a not in names]
    check("fbx: %d animation takes" % len(ANIMS), not missing_takes, len(names))
    arm.animation_data_create()
    h = max((ob.matrix_world @ v.co).z for v in ob.data.vertices) - min((ob.matrix_world @ v.co).z for v in ob.data.vertices)
    check("fbx: imported scale (height 1.2-1.5 m)", 1.2 <= h <= 1.5, round(h, 3))
    worst = 0.0
    for a in ANIMS:
        if a not in names or a not in reference:
            continue
        act = names[a]
        set_action(arm, act)
        frames, boxes = reference[a]
        fs, fe = (int(round(x)) for x in act.frame_range)
        err = 0.0
        for f, ref in zip(frames, boxes):
            scene.frame_set(f - frames[0] + fs)
            got = np.concatenate(bbox_of(ob))
            # the takes key the armature object's export turn (180 degrees about Z): x and y swap sign
            ref = np.array([-ref[3], -ref[4], ref[2], -ref[0], -ref[1], ref[5]])
            err = max(err, float(np.abs(got - ref).max()))
        worst = max(worst, err)
        check("fbx: take %s matches original (bbox)" % a, err < 0.004, "max dev %.4f m" % err)
    check("fbx: worst animation deviation < 4 mm", worst < 0.004, round(worst, 5))


def verify_modular():
    path = os.path.join(OUT, "CubeEscape_Robot_Modular.fbx")
    check("fbx modular: file exists", os.path.exists(path), path)
    if not os.path.exists(path):
        return
    expected = len([o for o in bpy.data.collections["Robot_Parts"].objects if o.type == "MESH"])
    before = set(bpy.data.objects)
    bpy.ops.import_scene.fbx(filepath=path, use_anim=False, ignore_leaf_bones=False)
    new = [o for o in bpy.data.objects if o not in before]
    unturn(new)
    meshes = [o for o in new if o.type == "MESH"]
    arms = [o for o in new if o.type == "ARMATURE"]
    check("fbx modular: all part meshes + rig", len(meshes) == expected and len(arms) == 1, (len(meshes), expected))
    sockets = [b for b in ("Socket_Head", "Socket_Neck", "Socket_Back", "Prop_L", "Prop_R", "Cape", "Antenna")
               if arms and b not in arms[0].data.bones]
    check("fbx modular: socket bones present", not sockets, sockets)


def verify_side_files():
    cfg_path = os.path.join(OUT, "robot_config.json")
    ok = os.path.exists(cfg_path)
    detail = ""
    if ok:
        cfg = json.load(open(cfg_path, encoding="utf-8"))
        ok = (len(cfg["expressions"]) >= 16 and len(cfg["robotTypes"]) >= 5 and len(cfg["skins"]) >= 10 and
              len(cfg["expressions"]) <= cfg["eyeGrid"][0] * cfg["eyeGrid"][1] and
              len(cfg["displays"]) <= cfg["displayGrid"][0] * cfg["displayGrid"][1])
        detail = "%d slots, %d types, %d skins, %d expressions, %d displays" % (
            len(cfg["slots"]), len(cfg["robotTypes"]), len(cfg["skins"]), len(cfg["expressions"]), len(cfg["displays"]))
    check("files: robot_config.json complete", ok, detail)
    vd = os.path.join(OUT, "ViewerData")
    need = ["robot.json", "vertices.bin", "indices.bin", "anim.bin"]
    missing = [n for n in need if not os.path.exists(os.path.join(vd, n))]
    check("files: viewer data complete", not missing, missing)
    if not missing:
        doc = json.load(open(os.path.join(vd, "robot.json"), encoding="utf-8"))
        nb = len(doc["bones"])
        floats = os.path.getsize(os.path.join(vd, "anim.bin")) // 4
        exp = sum((a["frames"] + 1) * nb * doc["floatsPerBoneFrame"] for a in doc["animations"])
        check("files: viewer animation buffer size", floats == exp, (floats, exp))
        nv = os.path.getsize(os.path.join(vd, "vertices.bin")) // (4 * doc["vertexStride"])
        check("files: viewer vertex buffer size", nv == sum(p["vertexCount"] for p in doc["parts"]), nv)


def verify_types():
    """Pre-combined FBX per robot type: one mesh + rig, no animations (they come from the main FBX)."""
    cfg = json.load(open(os.path.join(OUT, "robot_config.json"), encoding="utf-8"))
    names = [t["name"] for t in cfg["robotTypes"]]
    tdir = os.path.join(OUT, "Types")
    missing = [n for n in names if not os.path.exists(os.path.join(tdir, "CubeEscape_Robot_%s.fbx" % n))]
    check("fbx types: one file per robot type", not missing, missing or len(names))
    for n in names:
        path = os.path.join(tdir, "CubeEscape_Robot_%s.fbx" % n)
        if not os.path.exists(path):
            continue
        before = set(bpy.data.objects)
        bpy.ops.import_scene.fbx(filepath=path, use_anim=False, ignore_leaf_bones=False)
        new = [o for o in bpy.data.objects if o not in before]
        unturn(new)
        meshes = [o for o in new if o.type == "MESH"]
        arms = [o for o in new if o.type == "ARMATURE"]
        ok = len(meshes) == 1 and len(arms) == 1
        detail = (len(meshes), len(arms))
        if ok:
            me = meshes[0].data
            tris = sum(len(p.vertices) - 2 for p in me.polygons)
            zs = [(meshes[0].matrix_world @ v.co).z for v in me.vertices]
            mats = {m.name.split(".")[0] for m in me.materials}
            bones_ok = all(b in arms[0].data.bones for b in REQUIRED)
            # accessories (hats, antenna, cape) add up to ~1.7k triangles and ~0.3 m on top of the 12k/1.38 m body (1.14)
            ok = (5000 <= tris <= 15000 and 1.2 <= max(zs) - min(zs) <= 1.75 and bones_ok and
                  mats <= MAIN_MATERIALS | {"M_RobotFabric"} and len(mats) <= 4)
            detail = "%d tris, %.2f m, %d materials" % (tris, max(zs) - min(zs), len(mats))
        check("fbx types: %s" % n, ok, detail)


def verify_clip_data():
    cfg = json.load(open(os.path.join(OUT, "robot_config.json"), encoding="utf-8"))
    clips = cfg.get("clips", {})
    check("clips: data for every clip", sorted(clips) == sorted(ANIMS), len(clips))
    if not clips:
        return
    bad = []
    for n, c in clips.items():
        frames = c["frames"]
        names_ok = all(v == "$base" or v in cfg["expressions"] for _, v in c["expression"]) and \
            all(v == "$base" or v in cfg["displays"] for _, v in c["display"])
        frames_ok = all(0 <= f <= frames for f, _ in c["expression"] + c["display"] + c["emission"] + c["events"] +
                        c["footsteps"])
        if not (names_ok and frames_ok and all(v >= 0 for _, v in c["emission"])):
            bad.append(n)
    check("clips: names valid, keys inside the clip", not bad, bad)
    speeds = {n: clips[n]["naturalSpeed"] for n in ("CrouchWalk", "Walk", "Run", "RunFast", "Sprint") if n in clips}
    check("clips: locomotion speeds increase", list(speeds.values()) == sorted(speeds.values()) and
          all(v > 0.1 for v in speeds.values()), speeds)
    steps = [n for n in ("Walk", "Run", "RunFast", "Sprint", "CrouchWalk") if n in clips and
             len(clips[n]["footsteps"]) != 2]
    check("clips: two footsteps per locomotion cycle", not steps, steps)
    if "ClimbUp" in clips:
        mv = clips["ClimbUp"]["rootMotion"]["move"]
        check("clips: ClimbUp root motion 1.20 up / 0.36 forward", abs(mv[1] - 1.2) < 0.01 and abs(mv[2] - 0.36) < 0.01, mv)
    turns = {n: clips[n]["rootMotion"]["turnRight"] for n in ("TurnLeft", "TurnRight", "DanceSpin") if n in clips}
    check("clips: turn root motion", all(abs(abs(v) - (360 if n == "DanceSpin" else 90)) < 1 for n, v in turns.items()),
          turns)
    if "HangIdle" in clips:
        prop = clips["HangIdle"]["prop"] or {}
        h = clips["HangIdle"]["hands"]
        err = max(abs(h[s][0][1] - prop.get("top", 0) - 0.032) + abs(h[s][0][2] - prop.get("edge", 0) + 0.005)
                  for s in "LR")
        check("clips: HangIdle hands on the ledge edge", prop.get("type") == "ledge" and err < 0.03, round(err, 4))
    if "Crouch" in clips and "CrouchDeep" in clips:
        check("clips: CrouchDeep lower than Crouch", clips["CrouchDeep"]["height"]["max"] < clips["Crouch"]["height"]["min"],
              (clips["CrouchDeep"]["height"], clips["Crouch"]["height"]))
    if "Death" in clips:
        check("clips: Death powers the robot off", clips["Death"]["emission"][-1][1] == 0.0, clips["Death"]["emission"][-1])
    bounds = cfg.get("typeBounds", {})
    check("clips: bounds per robot type", sorted(bounds) == sorted(t["name"] for t in cfg["robotTypes"]), len(bounds))


def verify_looks():
    cfg = json.load(open(os.path.join(OUT, "robot_config.json"), encoding="utf-8"))
    schema_path = os.path.join(OUT, "robot_look.schema.json")
    check("looks: schema exists", os.path.exists(schema_path))
    slots = {s["id"]: s["variants"] for s in cfg["slots"]}
    bad = []
    for t in cfg["robotTypes"]:
        path = os.path.join(OUT, "Looks", "%s.robotlook.json" % t["name"])
        if not os.path.exists(path):
            bad.append(t["name"] + " missing")
            continue
        look = json.load(open(path, encoding="utf-8"))
        displays = cfg["displays"] + [a["name"] for a in cfg.get("displayAnimations", [])]
        ok = (look.get("format") == cfg["lookFormat"]["format"] and look.get("version") == cfg["lookFormat"]["version"]
              and set(look["variants"]) == set(slots) and all(v in slots[k] for k, v in look["variants"].items())
              and look["expression"] in cfg["expressions"] and look["display"] in displays
              and all(0 <= look[k][c] <= 1 for k in ("armor", "mech", "metal", "lights", "screen", "eyes", "visor",
                                                     "fabric", "armor2") for c in "rgb")
              and look["skin"] == t["skin"] and look["finish"] in [f["id"] for f in cfg["finishes"]]
              and all(b["id"] in [x["id"] for x in cfg["badges"]] for b in look["badges"])
              and look["lines"]["pattern"] in ["None"] + [x["id"] for x in cfg["linePatterns"]]
              and set(look["zones2"]) <= {z["id"] for z in cfg["zones"]})
        if not ok:
            bad.append(t["name"])
    check("looks: example look per robot type valid", not bad, bad or len(cfg["robotTypes"]))


def mask_planes(tex_dir, name):
    """(h, w, 4) float array of a mask texture as stored (straight alpha)."""
    img = bpy.data.images.load(os.path.join(tex_dir, name), check_existing=True)
    img.colorspace_settings.name = "Non-Color"
    img.alpha_mode = "STRAIGHT"
    px = np.empty(img.size[0] * img.size[1] * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    return px.reshape(img.size[1], img.size[0], 4)


def verify_paintjob():
    """1.10: skin data, mask textures, composed skins, expressions, animated displays, icons and sheets."""
    cfg = json.load(open(os.path.join(OUT, "robot_config.json"), encoding="utf-8"))
    tex_dir = os.path.join(OUT, "Textures")
    skins = {sk["name"]: sk for sk in cfg["skins"]}
    finishes = {f["id"] for f in cfg["finishes"]}
    badges = {b["id"] for b in cfg["badges"]}
    lines = {x["id"] for x in cfg["linePatterns"]}
    zones = {z["id"] for z in cfg["zones"]}
    bad = [n for n, sk in skins.items() if sk["finish"] not in finishes or len(sk["badges"]) > 2
           or any(b["id"] not in badges for b in sk["badges"]) or (sk["lines"] and sk["lines"]["pattern"] not in lines)
           or not set(sk["zones2"]) <= zones or len(sk["armor2"]) != 3]
    check("skins: data valid (finish, badges, lines, zones, armor2)", not bad, bad or len(skins))
    check("skins: at least 62 (20 from 1.7, 18 new in 1.10, 9 in 1.11, 15 in 1.12)", len(skins) >= 62, len(skins))
    layers = {e["id"] for e in cfg["layers"]}
    materials = {e["id"] for e in cfg["materials"]}
    bad = [n for n, sk in skins.items() if (sk["material"] and sk["material"]["id"] not in materials)
           or any(ly["id"] not in layers or not 0 <= ly["amount"] <= 1 for ly in sk["layers"]) or not 0 <= sk["wear"] <= 1]
    check("skins: materials, layers and wear valid", not bad, bad)
    start11 = {n for n, sk in skins.items() if sk["startSet"] and sk["stage"] == "1.11"}
    check("skins: start set of stage 1.11", start11 == START_SET_1_11, sorted(start11 ^ START_SET_1_11))
    start = {n for n, sk in skins.items() if sk["startSet"] and sk["stage"] == "1.10"}
    check("skins: start set of stage 1.10", start == START_SET_1_10, sorted(start ^ START_SET_1_10))
    # 1.17: the default skin is in Textures, the composed preview skins in cache/skin_textures - only where the cache
    # came along (an output copied without its cache, like the main checkout's, is a complete deliverable)
    skin_dir = os.path.join(OUT, "cache", "skin_textures")
    previews = os.path.isdir(skin_dir)
    missing = [n for n in skins if not os.path.exists(os.path.join(tex_dir, "T_Robot_BaseColor.png") if n == "Classic"
                                                      else os.path.join(skin_dir, "T_Robot_BaseColor_%s.png" % n))
               and (n == "Classic" or previews)]
    check("skins: composed texture per skin (default in Textures, previews in cache%s)" %
          ("" if previews else ": no cache, default only"), not missing, missing or len(skins))
    stale = [f for f in os.listdir(tex_dir) if f.startswith(("T_Robot_BaseColor_", "T_Robot_Emission_"))]
    check("textures: no preview skins among the deliverables", not stale, stale[:5])
    missing = [t for t in MASK_TEXTURES if not os.path.exists(os.path.join(tex_dir, t))]
    check("masks: textures present", not missing, missing)
    if not missing:
        planes = {t: mask_planes(tex_dir, t) for t in MASK_TEXTURES}
        empty = []
        for kind in ("badges", "linePatterns", "zones"):
            for e in cfg[kind]:
                cover = float((planes[e["texture"]][..., e["channel"]] > 0.5).mean())
                if cover < 1e-4:
                    empty.append("%s %s" % (kind, e["id"]))
        check("masks: every badge, line pattern and zone has pixels", not empty, empty)
        zsum = sum(planes[z["texture"]][..., z["channel"]] for z in cfg["zones"])
        check("masks: paint zones do not overlap", float(zsum.max()) < 1.05, round(float(zsum.max()), 3))
        cm = mask_planes(tex_dir, "T_Robot_ColorMask.png")
        painted = cm[..., :3].sum(axis=-1) > 0.5
        shares = {}
        for e in cfg["layers"]:
            if e["id"] in cfg["weathering"]["equalised"]:
                f = planes[e["texture"]][..., e["channel"]][painted]
                shares[e["id"]] = round(float((f > 0.5).mean()), 3)
        check("masks: layers equalised (amount 0.5 covers about half)", all(0.4 < v < 0.6 for v in shares.values()),
              shares)
        for kind in ("layers", "materials"):
            empty = [e["id"] for e in cfg[kind] if float((planes[e["texture"]][..., e["channel"]] > 0.05).mean()) < 1e-3]
            check("masks: every %s channel has pixels" % kind[:-1], not empty, empty)
    styles = cfg.get("expressionStyles", {})
    check("expressions: 1.10 faces, styles and blink", {"Damage", "LowPower", "Check", "Warning", "Blink"} <=
          set(cfg["expressions"]) and set(styles) <= set(cfg["expressions"]) and
          cfg["blink"]["expression"] in cfg["expressions"], len(cfg["expressions"]))
    gx, gy = cfg["flipbook"]["grid"]
    anims = cfg.get("displayAnimations", [])
    check("displays: animated motifs fit the flipbook", anims and all(a["frames"] <= gx and a["row"] < gy for a in anims),
          [a["name"] for a in anims])
    previews = os.path.join(OUT, "Previews")
    if os.path.exists(os.path.join(previews, "skins.png")):          # tiers with renders
        need = [os.path.join(previews, "skin_sheet.png"), os.path.join(previews, "silhouettes.png"),
                os.path.join(previews, "weathering.png")]
        need += [os.path.join(OUT, "Icons", k, n + ".png") for k, names in
                 (("Skins", list(skins)), ("Expressions", cfg["expressions"]), ("Displays", cfg["displays"])) for n in names]
        missing = [os.path.relpath(p, OUT) for p in need if not os.path.exists(p)]
        check("previews: skin sheet, silhouettes and icons", not missing, missing[:5] or len(need))


def verify_bolton():
    """1.12: attachments covered by the types, start set, parts of skins, emitters, spring bones, type emotes."""
    cfg = json.load(open(os.path.join(OUT, "robot_config.json"), encoding="utf-8"))
    slots = {sl["id"]: set(sl["variants"]) for sl in cfg["slots"]}
    missing = ["%s_%s" % (sl, v) for sl, vs in ATTACHMENTS_1_12.items() for v in sorted(vs)
               if v not in slots.get(sl, ())]
    check("attachments: 14 new variants exist", not missing, missing)
    types = {t["name"]: t for t in cfg["robotTypes"]}
    carried = {(sl, v) for t in types.values() for sl, v in t["variants"].items()}
    uncovered = ["%s_%s" % (sl, v) for sl, vs in ATTACHMENTS_1_12.items() for v in sorted(vs) if (sl, v) not in carried]
    check("attachments: each one carried by a robot type (QA coverage)", not uncovered, uncovered)
    skins = {sk["name"]: sk for sk in cfg["skins"]}
    bad = ["%s %s=%s" % (n, sl, v) for n, sk in skins.items() for sl, v in (sk.get("parts") or {}).items()
           if v not in slots.get(sl, ())]
    check("skins: parts name existing slot variants", not bad, bad)
    start12 = {n for n, sk in skins.items() if sk["startSet"] and sk["stage"] == "1.12"}
    check("skins: start set of stage 1.12", start12 == START_SET_1_12, sorted(start12 ^ START_SET_1_12))
    arm = bpy.data.objects.get("Robot_Armature")
    bones = set(arm.data.bones.keys()) if arm else set()
    em = cfg.get("emitters", [])
    bad = [e["id"] for e in em if e["bone"] not in bones or e["variant"] not in slots.get(e["slot"], ())]
    check("emitters: chimney smoke on an existing socket", any(e["id"] == "ChimneySmoke" for e in em) and not bad,
          bad or [e["id"] for e in em])
    springs = cfg.get("springBones", [])
    check("springs: spring bones in the rig", bool(springs) and set(springs) <= bones, springs)
    clips = cfg.get("clips", {})
    emotes = {t["name"]: t["emote"] for t in types.values() if t.get("emote")}
    check("emotes: type emotes are clips (Gentleman HatTip, King FlagWave)",
          emotes.get("Gentleman") == "HatTip" and emotes.get("King") == "FlagWave" and
          all(c in clips for c in emotes.values()), emotes)
    if arm is None or "Run" not in bpy.data.actions or "Cape" not in bones:
        return
    # the cape lifts in the run (wind) and swings; loops close without a jump
    pb = arm.pose.bones["Cape"]
    angles = []
    for name in ("Idle", "Run"):
        set_action(arm, bpy.data.actions[name])
        lo, hi = 1e9, -1e9
        first = None
        act = bpy.data.actions[name]
        for f in range(int(act.frame_range[0]), int(act.frame_range[1]) + 1):
            bpy.context.scene.frame_set(f)
            a = math.degrees(pb.rotation_quaternion.angle)
            first = a if first is None else first
            lo, hi = min(lo, a), max(hi, a)
        angles.append((name, round(lo, 1), round(hi, 1), round(abs(a - first), 3)))
    run = angles[1]
    check("springs: cape lifts in Run (> 10 deg), loops close", run[2] > 10 and all(x[3] < 0.5 for x in angles), angles)


def verify_spectral():
    """1.13: special materials, look format 5, Unity mask packs, detail levels, hit capsules, servo events."""
    cfg = json.load(open(os.path.join(OUT, "robot_config.json"), encoding="utf-8"))
    specials = [sp["id"] for sp in cfg.get("specials", [])]
    check("specials: 7 special materials with description and URP note", specials[:7] == SPECIALS_1_13 and
          all(sp["description"] and sp["urp"] for sp in cfg["specials"]), specials)
    skins = {sk["name"]: sk for sk in cfg["skins"]}
    bad = [n for n, sk in skins.items() if sk.get("special") and sk["special"]["id"] not in specials]
    check("skins: special materials known", not bad, bad)
    used = {sk["special"]["id"] for sk in skins.values() if sk.get("special")}
    check("skins: every special material used by a skin", used == set(specials), sorted(set(specials) - used))
    start13 = {n for n, sk in skins.items() if sk["startSet"] and sk["stage"] == "1.13"}
    check("skins: start set of stage 1.13", start13 == START_SET_1_13, sorted(start13 ^ START_SET_1_13))
    check("skins: at least 71 (9 new in 1.13)", len(skins) >= 71, len(skins))
    looks = [json.load(open(os.path.join(OUT, "Looks", f), encoding="utf-8")) for f in os.listdir(os.path.join(OUT, "Looks"))]
    check("looks: format version 5 or newer with a special field", looks and all(lk["version"] >= 5 and "special" in lk
                                                                                  for lk in looks), len(looks))
    # Unity mask packs: present, at most 1024², zone ID consistent with the zone masks
    tex = os.path.join(OUT, "Textures")
    ut = [u["file"] for u in cfg.get("unityTextures", [])]
    missing = [u for u in ut if not os.path.exists(os.path.join(tex, u))]
    check("unity: 10 packed mask textures present", len(ut) == 10 and not missing, missing)
    if not missing and ut:
        aux = mask_planes(tex, "Unity/T_Robot_U_Aux.png")
        n = aux.shape[0]
        check("unity: packed masks at most 1024²", n <= 1024, n)
        z1, z2 = mask_planes(tex, "T_Robot_ZoneMask.png"), mask_planes(tex, "T_Robot_ZoneMask2.png")
        zones = np.concatenate([z1, z2], axis=-1)
        step = zones.shape[0] // n
        zs = zones[::step, ::step]
        want = np.where(zs.max(axis=-1) > 0.5, zs.argmax(axis=-1) + 1, 0)
        got = np.round(aux[..., 1] * 8).astype(int)
        check("unity: zone ID channel matches the zone masks (> 98 %)", float((want == got).mean()) > 0.98,
              round(float((want == got).mean()), 4))
    # detail levels
    rep = json.load(open(os.path.join(OUT, "logs", "robot_report.json"), encoding="utf-8"))
    lods = rep.get("lods", {})
    bad = [k for k, v in lods.items() if not (v["LOD1"] <= 6300 and v["LOD2"] <= 2650 and v["LOD2"] < v["LOD1"] < v["LOD0"])]
    files = [os.path.join(OUT, "LOD", "CubeEscape_Robot_LOD%d.fbx" % i) for i in (1, 2)]
    check("lod: LOD1/LOD2 for the robot and every type within 6000/2500 triangles",
          len(lods) == 1 + len(cfg["robotTypes"]) and not bad and all(os.path.exists(f) for f in files), bad or len(lods))
    caps = cfg.get("hitCapsules", [])
    cov = [c["coverage"] for c in caps]
    check("capsules: 15 hit capsules covering their bones (mean > 80 %, each > 60 %)",
          len(caps) == 15 and sum(cov) / len(cov) > 0.8 and min(cov) > 0.6, {c["bone"]: c["coverage"] for c in caps})
    clips = cfg["clips"]
    walk = {e[1] for e in clips["Walk"]["servo"]}
    check("servo: events in every clip's data, legs in Walk, none in Idle",
          all("servo" in c for c in clips.values()) and {"Leg_L", "Leg_R"} <= walk and not clips["Idle"]["servo"],
          sorted(walk))


def import_single(path):
    """Imports an FBX; returns (mesh object, armature) or None when it is not one mesh + one rig."""
    before = set(bpy.data.objects)
    bpy.ops.import_scene.fbx(filepath=path, use_anim=False, ignore_leaf_bones=False)
    new = [o for o in bpy.data.objects if o not in before]
    unturn(new)
    meshes = [o for o in new if o.type == "MESH"]
    arms = [o for o in new if o.type == "ARMATURE"]
    return (meshes[0], arms[0]) if len(meshes) == 1 and len(arms) == 1 else None


def verify_reshape():
    """1.14: base proportions (height), body forms (catalog, skins, look version 6, FBX, viewer chunks, QA)."""
    cfg = json.load(open(os.path.join(OUT, "robot_config.json"), encoding="utf-8"))
    forms = {f["id"]: f for f in cfg.get("forms", [])}
    check("forms: Faceted, Voxel, Perforated, Wired with kind, description and URP note",
          list(forms)[:4] == FORMS_1_14 and all(f["kind"] in ("mesh", "shader", "parts") and f["description"] and f["urp"]
                                               for f in forms.values()), list(forms))
    perf = cfg.get("perforation", {})
    check("forms: perforation grid from the texel density (holes per UV unit, radius < half a cell)",
          perf.get("cells", 0) > 10 and 0 < perf.get("radius", 0) < 0.5, perf)
    skins = {sk["name"]: sk for sk in cfg["skins"]}
    bad = [n for n, sk in skins.items() if sk.get("form", "None") not in ["None"] + list(forms)]
    used = {sk["form"] for sk in skins.values() if sk.get("form", "None") != "None"}
    check("skins: forms known, every form used by a skin", not bad and used == set(forms), bad or sorted(set(forms) - used))
    start14 = {n for n, sk in skins.items() if sk["startSet"] and sk["stage"] == "1.14"}
    check("skins: start set of stage 1.14 (Pixel, Origami), at least 76 skins", start14 == {"Pixel", "Origami"} and
          len(skins) >= 76, (sorted(start14), len(skins)))
    looks = [json.load(open(os.path.join(OUT, "Looks", f), encoding="utf-8")) for f in os.listdir(os.path.join(OUT, "Looks"))]
    check("looks: format version 6 with a form field", looks and all(lk["version"] == 6 and lk.get("form") == "None"
                                                                     for lk in looks), len(looks))
    for fid, f in forms.items():
        if f["kind"] == "shader":
            continue
        path = os.path.join(OUT, "Forms", "CubeEscape_Robot_%s.fbx" % fid)
        got = import_single(path) if os.path.exists(path) else None
        ok, detail = got is not None, "missing or not one mesh + rig"
        if got:
            ob, arm = got
            tris = sum(len(p.vertices) - 2 for p in ob.data.polygons)
            zs = [(ob.matrix_world @ v.co).z for v in ob.data.vertices]
            ok = 2000 <= tris <= 25000 and 1.2 <= max(zs) - min(zs) <= 1.5 and all(b in arm.data.bones for b in REQUIRED)
            if f["kind"] == "mesh":
                ok &= os.path.exists(os.path.join(OUT, "Forms", "CubeEscape_Robot_Modular_%s.fbx" % fid))
            detail = "%d tris, %.2f m" % (tris, max(zs) - min(zs))
        check("forms: %s FBX (rig, 1.2-1.5 m, 2k-25k triangles%s)" % (fid, ", modular" if f["kind"] == "mesh" else ""),
              ok, detail)
    doc = json.load(open(os.path.join(OUT, "ViewerData", "robot.json"), encoding="utf-8"))
    chunk_forms = {p.get("form", "") for p in doc["parts"]}
    want = {""} | {fid for fid, f in forms.items() if f["kind"] == "mesh"}
    check("viewer: geometry chunks of the base and of every mesh form", chunk_forms == want, sorted(chunk_forms))
    check("viewer: the cable part of the form Wired is a chunk object",
          any(p["object"] == "Form_Wired" for p in doc["parts"]), "Form_Wired")
    rep_ = json.load(open(os.path.join(OUT, "logs", "robot_report.json"), encoding="utf-8"))
    qa = rep_.get("qa", {})
    if all(t["name"] in qa for t in cfg["robotTypes"]):     # all types checked (not --qa-types): the forms too
        form_qa = {k: v for k, v in qa.items() if k.startswith("Form_")}
        bad = {k: [n for n, e in v["actions"].items() if e["new_overlaps"] or e["detached_joints"] or e["min_z"] < -0.006]
               for k, v in form_qa.items()}
        check("qa: geometry forms checked over all clips without problems",
              len(form_qa) == 3 and not any(bad.values()) and all(len(v["actions"]) >= 69 for v in form_qa.values()),
              {k: len(v) for k, v in bad.items()} or "missing")


def verify_crawlspace():
    """1.16: clip categories (every clip once, display order) and the crawl clips under the 0.65 m crouch capsule."""
    cfg = json.load(open(os.path.join(OUT, "robot_config.json"), encoding="utf-8"))
    cats = cfg.get("clipCategories", [])
    listed = [c for cat in cats for c in cat["clips"]]
    check("categories: every clip in exactly one category", sorted(listed) == sorted(ANIMS) and
          len(listed) == len(set(listed)) and all(cat["label"] for cat in cats), (len(cats), len(listed)))
    clips = cfg.get("clips", {})
    wrong = [n for n, c in clips.items() if not any(n in cat["clips"] and cat["id"] == c.get("category")
                                                     for cat in cats)]
    check("categories: clip data names its category", not wrong, wrong)
    crawl = {n: clips.get(n) for n in ("CrawlIdle", "Crawl")}
    if not all(crawl.values()):                 # a --groups run without the crawl clips
        return
    ok = all(c and c["height"]["max"] < 0.62 for c in crawl.values())
    check("crawl: CrawlIdle and Crawl stay below 0.62 m (crouch capsule 0.65 m)", ok,
          {n: c["height"] if c else None for n, c in crawl.items()})
    sp = (crawl["Crawl"] or {}).get("naturalSpeed", 0)
    check("crawl: Crawl loops with a natural speed from the hands (0.2-0.5 m/s), items hidden",
          crawl["Crawl"]["loop"] and crawl["Crawl"].get("gait") == "hands" and 0.2 <= sp <= 0.5 and
          all(c and c["hideHeldItem"] for c in crawl.values()), sp)


def group_boxes(ob):
    """Bounds per main vertex group of a mesh object (rest shape, world space)."""
    names = {g.index: g.name for g in ob.vertex_groups}
    co = np.array([ob.matrix_world @ v.co for v in ob.data.vertices]) if len(ob.data.vertices) else np.zeros((0, 3))
    grp = [names[max(v.groups, key=lambda g: g.weight).group] if v.groups else None for v in ob.data.vertices]
    out = {}
    for n in set(grp) - {None}:
        pts = co[[i for i, g in enumerate(grp) if g == n]]
        out[n] = (pts.min(axis=0), pts.max(axis=0))
    return out


def verify_slimline():
    """1.17: every bone group of a mesh form stays inside the bounds of the same group of its base part (the voxel
    inset and the faceted pull-back; a lost element index once voxelised both feet as Foot_R, 0.23 m outside)."""
    bad, n = [], 0
    for ob in bpy.data.objects:
        base = bpy.data.objects.get(ob.name.rsplit("__", 1)[0]) if "__" in ob.name else None
        if ob.type != "MESH" or base is None or not ob.name.endswith(("__Voxel", "__Faceted")):
            continue
        n += 1
        src = group_boxes(base)
        for g, (lo, hi) in group_boxes(ob).items():
            if g not in src:
                bad.append((ob.name, g, "group not in base"))
                continue
            out = float(max((src[g][0] - lo).max(), (hi - src[g][1]).max()))
            if out > 0.03:     # one voxel cell: block corners on curved thin parts (cape) reach past the bounds
                bad.append((ob.name, g, round(out, 3)))
    check("forms: every bone group of a mesh form inside its base part (+ one voxel cell)", n >= 40 and not bad,
          bad[:6] or n)


# 1.19 Longstride: clips at the game's own tempo (Cube Escape 0.58), carried crate, valve wheel, knockback, roll
LONGSTRIDE_SPEEDS = {"Jog": 5.0, "SprintFull": 8.0, "StrafeLeft": 5.0, "StrafeRight": 5.0, "RunBack": 5.0,
                     "CarryWalk": 5.0, "Wade": 4.0, "CrawlFast": 2.4}
# direction in which a planted contact moves (Blender world: the robot looks along -Y, its left is +X)
LONGSTRIDE_GROUND = {"Jog": (0, 1), "SprintFull": (0, 1), "StrafeLeft": (-1, 0), "StrafeRight": (1, 0),
                     "RunBack": (0, -1), "CarryWalk": (0, 1), "Wade": (0, 1), "CrawlFast": (0, 1)}
PLANT_TOLERANCE = 0.03       # every frame step of a planted contact within 3 % of naturalSpeed


def evaluated(ob):
    dg = bpy.context.evaluated_depsgraph_get()
    ev = ob.evaluated_get(dg)
    me = ev.to_mesh()
    co = np.empty(len(me.vertices) * 3)
    me.vertices.foreach_get("co", co)
    ev.to_mesh_clear()
    mw = np.array(ob.matrix_world)
    return co.reshape(-1, 3) @ mw[:3, :3].T + mw[:3, 3]


def group_index(ob):
    names = {g.index: g.name for g in ob.vertex_groups}
    grp = np.array([names[max(v.groups, key=lambda g: g.weight).group] if v.groups else "" for v in ob.data.vertices])
    return grp


def verify_longstride():
    cfg = json.load(open(os.path.join(OUT, "robot_config.json"), encoding="utf-8"))
    clips = cfg.get("clips", {})
    new = ["Jog", "SprintFull", "StrafeLeft", "StrafeRight", "RunBack", "CarryIdle", "CarryWalk", "Wade", "Crank",
           "Knockback", "LandRoll", "CrawlFast"]
    if not all(n in clips for n in new):          # a --groups run without the 1.19 clips
        return
    speeds = {n: clips[n]["naturalSpeed"] for n in LONGSTRIDE_SPEEDS}
    check("longstride: natural speeds 5 / 8 / 5 / 4 / 2.4 m/s (+-1 %)",
          all(abs(speeds[n] - v) <= 0.01 * v for n, v in LONGSTRIDE_SPEEDS.items()), speeds)
    steps = {n: len(clips[n]["footsteps"]) for n in LONGSTRIDE_SPEEDS}
    check("longstride: two steps per stride (SprintFull: two strides per loop)",
          all(v == (4 if n == "SprintFull" else 2) for n, v in steps.items()), steps)
    loops = {n: clips[n]["loop"] for n in new}
    check("longstride: loops (Knockback and LandRoll play once)",
          all(v == (n not in ("Knockback", "LandRoll")) for n, v in loops.items()), loops)
    still = [n for n in new if any(abs(v) > 1e-4 for v in clips[n]["rootMotion"]["move"]) or
             abs(clips[n]["rootMotion"]["turnRight"]) > 0.01]
    check("longstride: all in place (root motion 0)", not still, still)
    cf = clips["CrawlFast"]
    check("longstride: CrawlFast below 0.60 m, steps on the hands, items hidden",
          cf["height"]["max"] <= 0.60 and cf["gait"] == "hands" and cf["hideHeldItem"] and
          all(s.startswith("Hand_") for _, s in cf["footsteps"]), cf["height"])
    ev = {n: clips[n]["events"] for n in ("LandRoll", "Knockback", "Crank")}
    check("longstride: events (LandRoll Impact at touchdown, Knockback Impact, Crank Grab)",
          [0, "Impact"] in ev["LandRoll"] and any(e == "Impact" for _, e in ev["Knockback"]) and
          any(e == "Grab" for _, e in ev["Crank"]), ev)
    props = {n: (clips[n]["prop"] or {}).get("type") for n in ("CarryIdle", "CarryWalk", "Crank")}
    check("longstride: props crate (carry) and wheel (crank)",
          props == {"CarryIdle": "crate", "CarryWalk": "crate", "Crank": "wheel"}, props)
    cats = {c["id"]: c["clips"] for c in cfg["clipCategories"]}
    want = {"Move": ["Jog", "SprintFull", "StrafeLeft", "StrafeRight", "RunBack", "Wade"], "Air": ["LandRoll"],
            "Crouch": ["CrawlFast"], "Interact": ["CarryIdle", "CarryWalk", "Crank"], "Damage": ["Knockback"]}
    check("longstride: categories, appended at the end", all(cats[k][-len(v):] == v for k, v in want.items()),
          {k: cats[k][-len(v):] for k, v in want.items()})
    ob, arm = bpy.data.objects.get("Robot_Mesh"), bpy.data.objects.get("Robot_Armature")
    if ob is None or arm is None:
        return
    grp = group_index(ob)
    # planted contacts: every frame step on the ground moves at naturalSpeed against the running direction
    worst, wrong = {}, []
    for n, (dx, dy) in LONGSTRIDE_GROUND.items():
        hands = clips[n]["gait"] == "hands"
        sides = {s: np.isin(grp, ["Hand_" + s, "Fingers_" + s] if hands else ["Foot_" + s]) for s in "LR"}
        set_action(arm, bpy.data.actions[n])
        prev, dev = None, 0.0
        for f in range(clips[n]["frames"] + 1):
            bpy.context.scene.frame_set(f)
            co = evaluated(ob)
            cur = {}
            for s, m in sides.items():
                pts = co[m]
                cur[s] = (pts[:, 2].min() < (0.035 if hands else 0.006), pts.mean(axis=0))
                if prev and cur[s][0] and prev[s][0]:
                    d = (cur[s][1] - prev[s][1])[:2] * 30.0
                    along = d[0] * dx + d[1] * dy
                    dev = max(dev, abs(np.linalg.norm(d) - speeds[n]) / speeds[n])
                    if along < 0.97 * np.linalg.norm(d):
                        wrong.append("%s@%d" % (n, f))
            prev = cur
        worst[n] = round(float(dev), 4)
    check("longstride: planted contacts move at naturalSpeed (+-3 %) against the run, no slip",
          all(v <= PLANT_TOLERANCE for v in worst.values()) and not wrong, (worst, wrong[:4]))
    # standing clips: a foot on the ground stays where it is (< 2 mm per frame)
    feet = {s: grp == "Foot_" + s for s in "LR"}
    slide = {}
    for n in ("CarryIdle", "Crank", "Knockback", "LandRoll"):
        set_action(arm, bpy.data.actions[n])
        prev, most = None, 0.0
        for f in range(clips[n]["frames"] + 1):
            bpy.context.scene.frame_set(f)
            co = evaluated(ob)
            cur = {s: (co[m][:, 2].min() < 0.006, co[m].mean(axis=0)) for s, m in feet.items()}
            for s in "LR":
                if prev and cur[s][0] and prev[s][0]:
                    most = max(most, float(np.linalg.norm((cur[s][1] - prev[s][1])[:2])))
            prev = cur
        slide[n] = round(most, 4)
    check("longstride: feet on the ground stay put in CarryIdle, Crank, Knockback, LandRoll (< 2 mm/frame)",
          all(v < 0.002 for v in slide.values()), slide)
    # the carried crate: no body part inside it, both hands on its side faces
    prop = clips["CarryIdle"]["prop"]
    size, front, bottom = prop["size"], prop["forward"], prop["bottom"]
    held = np.array([g.startswith(("Hand_", "Fingers_", "Thumb_", "Prop_")) for g in grp])
    inside, gap = [], 0.0
    for n in ("CarryIdle", "CarryWalk"):
        set_action(arm, bpy.data.actions[n])
        for f in range(0, clips[n]["frames"] + 1, 2):
            bpy.context.scene.frame_set(f)
            co = evaluated(ob)
            body = co[~held]
            m = ((np.abs(body[:, 0]) < size / 2 - 0.005) & (-body[:, 1] > front + 0.005) &
                 (-body[:, 1] < front + size) & (body[:, 2] > bottom + 0.005) & (body[:, 2] < bottom + size))
            if m.any():
                inside.append("%s@%d" % (n, f))
            for s in "LR":
                hand = co[np.isin(grp, ["Hand_" + s, "Fingers_" + s])]
                gap = max(gap, float(np.abs(np.abs(hand[:, 0]) - size / 2).min()))
    check("longstride: crate clear of the body, hands on its sides (< 3 cm)", not inside and gap < 0.03,
          (inside[:4], round(gap, 4)))


def main():
    print("== verify %s" % bpy.data.filepath)
    ref = verify_blend()
    if ref is not None:
        verify_fbx(ref)
        verify_modular()
    verify_side_files()
    verify_types()
    verify_clip_data()
    verify_looks()
    verify_paintjob()
    verify_bolton()
    verify_spectral()
    verify_reshape()
    verify_crawlspace()
    verify_slimline()
    verify_longstride()
    ok = all(r["ok"] for r in results)
    os.makedirs(os.path.join(OUT, "logs"), exist_ok=True)
    with open(os.path.join(OUT, "logs", "verify_report.json"), "w", encoding="utf-8") as fh:
        json.dump({"ok": ok, "passed": sum(r["ok"] for r in results), "total": len(results), "checks": results},
                  fh, indent=2)
    print("== %d/%d checks passed" % (sum(r["ok"] for r in results), len(results)))
    sys.exit(0 if ok else 1)


main()
