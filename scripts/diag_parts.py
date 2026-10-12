"""Debug helper: lists intersecting part pairs (different bones) for frames of an action.
Usage: Blender -b <blend> --python diag_parts.py -- <action> <frame,frame,...>"""
import sys, bpy, numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree
argv = sys.argv[sys.argv.index("--") + 1:]
arm = bpy.data.objects["Robot_Armature"]; ob = bpy.data.objects["Robot_Mesh"]
act = bpy.data.actions[argv[0]]
arm.animation_data.action = act; arm.animation_data.action_slot = act.slots[0]
me = ob.data
names = list(me["part_names"])
pid = np.zeros(len(me.polygons), dtype=np.int32)
me.attributes["part_id"].data.foreach_get("value", pid)
bone_of = {}
for p in me.polygons:
    bone_of[pid[p.index]] = ob.vertex_groups[me.vertices[p.vertices[0]].groups[0].group].name
parent = {b.name: (b.parent.name if b.parent else None) for b in arm.data.bones}
for f in [int(x) for x in argv[1].split(",")]:
    bpy.context.scene.frame_set(f)
    ev = ob.evaluated_get(bpy.context.evaluated_depsgraph_get()); m = ev.to_mesh()
    co = [v.co.copy() for v in m.vertices]
    polys = {}
    for p in m.polygons:
        polys.setdefault(pid[p.index], []).append(list(p.vertices))
    trees = {}
    for k, pl in polys.items():
        idx = sorted({i for q in pl for i in q}); rm = {o: n for n, o in enumerate(idx)}
        trees[k] = BVHTree.FromPolygons([co[i] for i in idx], [[rm[i] for i in q] for q in pl])
    hits = []
    ks = sorted(trees)
    for i, a in enumerate(ks):
        for b in ks[i + 1:]:
            ba, bb = bone_of[a], bone_of[b]
            if ba == bb or parent.get(ba) == bb or parent.get(bb) == ba:
                continue
            if trees[a].overlap(trees[b]):
                hits.append("%s(%s) x %s(%s)" % (names[a], ba, names[b], bb))
    ev.to_mesh_clear()
    print("FRAME", f, hits)
