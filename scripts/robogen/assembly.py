"""Joining parts into groups, connectivity check, normals and UV unwrap (with cache)."""

from robogen.body import *  # noqa: F401,F403 - one shared namespace, see robogen/__init__.py


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


__all__ = [_n for _n in dir() if not _n.startswith("__")]
