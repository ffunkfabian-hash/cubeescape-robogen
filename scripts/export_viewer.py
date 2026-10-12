"""Cube Escape robot - data export for the RobotViewer app (SceneKit).

Writes into <out_dir>:
    robot.json      bones (rest matrices), parts (geometry chunks), animations, customisation config
    vertices.bin    float32 [px py pz nx ny nz u v] per vertex, positions in bone space
    indices.bin     uint32 triangle indices (per chunk, relative to the chunk's first vertex)
    anim.bin        float32 [tx ty tz qx qy qz qw sx sy sz] per bone per frame (bone-local, parent space)
    textures/       Classic texture set + eye/display atlases

Coordinates: Blender (Z up, robot looks along -Y) -> SceneKit (Y up, robot looks along +Z).
Rigid skinning makes the export simple: every triangle belongs to exactly one bone, so the viewer
parents geometry chunks under bone nodes and only animates node transforms.
"""

import json
import os
import shutil

import bpy
import numpy as np
from mathutils import Matrix

C4 = Matrix(((1, 0, 0, 0), (0, 0, 1, 0), (0, -1, 0, 0), (0, 0, 0, 1)))
C4_INV = C4.inverted()
VIEWER_TEXTURES = ["T_Robot_BaseColor.png", "T_Robot_MetallicSmoothness.png", "T_Robot_Normal.png",
                   "T_Robot_Emission.png", "T_Robot_Occlusion.png", "T_Robot_ColorMask.png", "T_Robot_LightMask.png",
                   "T_Robot_EyeAtlas.png", "T_Robot_DisplayAtlas.png", "T_Robot_DisplayFlipbook.png",
                   "T_Robot_BadgeMask.png", "T_Robot_BadgeMask2.png", "T_Robot_LineMask.png",
                   "T_Robot_LineMask2.png", "T_Robot_ZoneMask.png", "T_Robot_ZoneMask2.png",
                   "T_Robot_LayerMask.png", "T_Robot_LayerMask2.png", "T_Robot_MaterialMask.png",
                   "T_Robot_MaterialMask2.png"]
KIND = {"M_Robot": "body", "M_RobotFabric": "fabric", "M_RobotEyes": "eyes", "M_RobotDisplay": "display"}


def conv(m):
    return C4 @ m @ C4_INV


def flat(m):
    return [float(m[r][c]) for r in range(4) for c in range(4)]   # row-major


def export(out_dir, part_objs, arm, anims, actions, tex_dir, config, log):
    os.makedirs(os.path.join(out_dir, "textures"), exist_ok=True)
    bones = list(arm.data.bones)
    names = [b.name for b in bones]
    bone_info = []
    for b in bones:
        m = b.matrix_local if b.parent is None else b.parent.matrix_local.inverted() @ b.matrix_local
        bone_info.append({"name": b.name, "parent": b.parent.name if b.parent else None, "rest": flat(conv(m))})

    spin = set(config.get("spinParts", []))
    vchunks, ichunks, parts = [], [], []
    vbase = ibase = 0
    for ob in part_objs:
        me = ob.data
        me.calc_loop_triangles()
        nt = len(me.loop_triangles)
        tri_loops = np.empty(nt * 3, dtype=np.int64)
        me.loop_triangles.foreach_get("loops", tri_loops)
        tri_poly = np.empty(nt, dtype=np.int64)
        me.loop_triangles.foreach_get("polygon_index", tri_poly)
        loop_vert = np.empty(len(me.loops), dtype=np.int64)
        me.loops.foreach_get("vertex_index", loop_vert)
        co = np.empty(len(me.vertices) * 3)
        me.vertices.foreach_get("co", co)
        co = co.reshape(-1, 3)
        nrm = np.empty(len(me.loops) * 3)
        me.corner_normals.foreach_get("vector", nrm)
        nrm = nrm.reshape(-1, 3)
        uv = np.empty(len(me.loops) * 2)
        me.uv_layers[0].data.foreach_get("uv", uv)
        uv = uv.reshape(-1, 2).copy()
        uv[:, 1] = 1.0 - uv[:, 1]          # SceneKit samples images top-down
        poly_mat = np.empty(len(me.polygons), dtype=np.int64)
        me.polygons.foreach_get("material_index", poly_mat)
        pid = np.zeros(len(me.polygons), dtype=np.int64)
        if "part_id" in me.attributes:
            me.attributes["part_id"].data.foreach_get("value", pid)
        part_names = list(me.get("part_names", []))
        vgroup = np.array([v.groups[0].group for v in me.vertices], dtype=np.int64)
        group_names = {g.index: g.name for g in ob.vertex_groups}

        tri_bone = vgroup[loop_vert[tri_loops[0::3]]]
        tri_mat = poly_mat[tri_poly]
        tri_part = pid[tri_poly]
        keys = {}
        for t in range(nt):
            pname = part_names[tri_part[t]] if part_names else ""
            key = (int(tri_bone[t]), int(tri_mat[t]), pname if pname in spin else "")
            keys.setdefault(key, []).append(t)
        for (gi, mi, spin_name), tris in sorted(keys.items()):
            bone = group_names[gi]
            bm = arm.data.bones[bone].matrix_local
            to_bone = C4 @ bm.inverted()          # Blender world -> SceneKit bone space
            rot = to_bone.to_3x3()
            loops = tri_loops.reshape(-1, 3)[tris].reshape(-1)
            p = co[loop_vert[loops]]
            p4 = np.c_[p, np.ones(len(p))] @ np.array(to_bone).T
            n3 = nrm[loops] @ np.array(rot).T
            n3 /= np.linalg.norm(n3, axis=1, keepdims=True) + 1e-12
            data = np.c_[p4[:, :3], n3, uv[loops]].astype(np.float32)
            idx = np.arange(len(data), dtype=np.uint32)
            mat_name = me.materials[mi].name.split(".")[0]
            entry = {"object": ob.name, "slot": ob.get("slot", ""), "variant": ob.get("variant", ""),
                     "bone": bone, "material": KIND.get(mat_name, "body"), "spin": bool(spin_name),
                     "part": spin_name, "vertexOffset": vbase, "vertexCount": int(len(data)),
                     "indexOffset": ibase, "indexCount": int(len(idx))}
            if spin_name:
                entry["pivot"] = [float(x) for x in p4[:, :3].mean(axis=0)]
            parts.append(entry)
            vchunks.append(data)
            ichunks.append(idx)
            vbase += len(data)
            ibase += len(idx)

    # animations: bone-local transforms per frame (armature modifiers off for speed)
    mods = [(o, m) for o in bpy.context.scene.objects if o.type == "MESH" for m in o.modifiers if m.type == "ARMATURE"]
    for o, m in mods:
        m.show_viewport = False
    ad = arm.animation_data
    saved = ad.action
    anim_info, anim_data = [], []
    offset = 0
    pbs = [arm.pose.bones[n] for n in names]
    parents = [arm.pose.bones[b.parent.name] if b.parent else None for b in bones]
    for a in anims:
        act = actions[a.name]
        ad.action = act
        ad.action_slot = act.slots[0]
        frames = []
        for f in range(a.frames + 1):
            bpy.context.scene.frame_set(f)
            row = []
            for pb, par in zip(pbs, parents):
                m = pb.matrix if par is None else par.matrix.inverted() @ pb.matrix
                loc, q, sc = conv(m).decompose()
                row += [loc.x, loc.y, loc.z, q.x, q.y, q.z, q.w, sc.x, sc.y, sc.z]
            frames.append(row)
        arr = np.array(frames, dtype=np.float32)
        anim_info.append({"name": a.name, "group": a.group, "frames": a.frames, "loop": a.loop,
                          "description": a.desc, "unity": a.unity, "offset": offset})
        anim_data.append(arr.reshape(-1))
        offset += arr.size
    ad.action = saved
    for o, m in mods:
        m.show_viewport = True

    np.concatenate(vchunks).tofile(os.path.join(out_dir, "vertices.bin"))
    np.concatenate(ichunks).tofile(os.path.join(out_dir, "indices.bin"))
    np.concatenate(anim_data).astype(np.float32).tofile(os.path.join(out_dir, "anim.bin"))
    for t in VIEWER_TEXTURES:
        src = os.path.join(tex_dir, t)
        if os.path.exists(src):
            shutil.copyfile(src, os.path.join(out_dir, "textures", t))
    doc = {"format": 1, "fps": bpy.context.scene.render.fps, "floatsPerBoneFrame": 10, "vertexStride": 8,
           "bones": bone_info, "parts": parts, "animations": anim_info, "config": config,
           "textures": VIEWER_TEXTURES}
    with open(os.path.join(out_dir, "robot.json"), "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
    log.info("  viewer data: %d bones, %d chunks, %d vertices, %d clips -> %s", len(bone_info), len(parts), vbase,
             len(anim_info), out_dir)
