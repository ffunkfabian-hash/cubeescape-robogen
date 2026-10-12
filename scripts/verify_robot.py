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
MASK_TEXTURES = ["T_Robot_BadgeMask.png", "T_Robot_BadgeMask2.png", "T_Robot_LineMask.png", "T_Robot_LineMask2.png",
                 "T_Robot_ZoneMask.png", "T_Robot_ZoneMask2.png"]       # 1.10: RGBA masks (alpha = a mask channel)
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
    # rest bounding box: robot stands on the floor, about 1.3 m tall
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
    sockets = [b for b in ("Socket_Head", "Socket_Neck", "Socket_Back", "Prop_L", "Prop_R")
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
            # accessories (hats, antenna, cape) add up to ~1.7k triangles and ~0.25 m on top of the 12k/1.3 m body
            ok = (5000 <= tris <= 15000 and 1.2 <= max(zs) - min(zs) <= 1.6 and bones_ok and
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
    check("skins: at least 38 (20 from 1.7 + 18 new in 1.10)", len(skins) >= 38, len(skins))
    start = {n for n, sk in skins.items() if sk["startSet"] and sk["stage"] == "1.10"}
    check("skins: start set of stage 1.10", start == START_SET_1_10, sorted(start ^ START_SET_1_10))
    missing = [n for n in skins if not os.path.exists(os.path.join(tex_dir, "T_Robot_BaseColor%s.png" %
                                                                    ("" if n == "Classic" else "_" + n)))]
    check("skins: composed texture per skin", not missing, missing or len(skins))
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
        need = [os.path.join(previews, "skin_sheet.png"), os.path.join(previews, "silhouettes.png")]
        need += [os.path.join(OUT, "Icons", k, n + ".png") for k, names in
                 (("Skins", list(skins)), ("Expressions", cfg["expressions"]), ("Displays", cfg["displays"])) for n in names]
        missing = [os.path.relpath(p, OUT) for p in need if not os.path.exists(p)]
        check("previews: skin sheet, silhouettes and icons", not missing, missing[:5] or len(need))


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
    ok = all(r["ok"] for r in results)
    os.makedirs(os.path.join(OUT, "logs"), exist_ok=True)
    with open(os.path.join(OUT, "logs", "verify_report.json"), "w", encoding="utf-8") as fh:
        json.dump({"ok": ok, "passed": sum(r["ok"] for r in results), "total": len(results), "checks": results},
                  fh, indent=2)
    print("== %d/%d checks passed" % (sum(r["ok"] for r in results), len(results)))
    sys.exit(0 if ok else 1)


main()
