"""Body forms: faceted, voxel, perforated (1.14), deterministic in every process."""

from robogen.rig import *  # noqa: F401,F403 - one shared namespace, see robogen/__init__.py


# ------------------------------- 1.14 body forms: Faceted and Voxel geometry -------------------------------
# Every part object gets a form copy with the same vertex groups, materials and UV atlas: bound to the same rig, it
# plays every clip, and every skin texture fits. Screens and the visor stay smooth (they carry the eye and display
# atlases); in the voxel form they move in front of the blocks.
FACET_RATIO = 0.16          # share of the triangles the collapse keeps (sharp, paper-like facets)
VOXEL_CELL = 0.03           # metres; world grid in the rest pose, so the blocks of neighbouring parts line up
VOXEL_INSET = 0.5           # a cell counts if its centre lies half a cell inside the part: every block stays inside
                            # the base form, so its collision-free clips carry over (0.25 collided in 67 of 69 clips)
VOXEL_RAYS = [Vector(d).normalized() for d in ((1, 0.0137, 0.0071), (-0.0113, 1, 0.0193), (0.0089, -0.0161, 1))]
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


def canonical_bm(src):
    """Copy of a bmesh in a fixed element order: vertices by position (then weights), faces by their vertex ids, each
    face starting at its smallest vertex. Blender's decimate depends on element order, and the loop order of the same
    mesh differs between processes - without this the faceted form came out a little different in every run."""
    src.verts.ensure_lookup_table()
    sd = src.verts.layers.deform.verify()
    suv = src.loops.layers.uv.active
    key = lambda v: (tuple(round(c, 6) for c in v.co), tuple(sorted(v[sd].items())))     # noqa: E731
    verts = sorted(src.verts, key=key)
    rank = {v.index: i for i, v in enumerate(verts)}
    dst = bmesh.new()
    dd = dst.verts.layers.deform.verify()
    duv = uv_layer_of(dst) if suv is not None else None
    ints = [(sl, dst.faces.layers.int.get(n) or dst.faces.layers.int.new(n))
            for n in FACE_INT_LAYERS for sl in [src.faces.layers.int.get(n)] if sl is not None]
    new = []
    for v in verts:
        nv = dst.verts.new(v.co)
        for k, w in v[sd].items():
            nv[dd][k] = w
        new.append(nv)

    def face_key(f):
        ids = [rank[v.index] for v in f.verts]
        k = ids.index(min(ids))
        return ids[k:] + ids[:k]

    for f in sorted(src.faces, key=face_key):
        loops = list(f.loops)
        k = [rank[l.vert.index] for l in loops].index(min(rank[l.vert.index] for l in loops))
        loops = loops[k:] + loops[:k]
        try:
            nf = dst.faces.new([new[rank[l.vert.index]] for l in loops])
        except ValueError:          # a duplicate face (coincident parts): keep the first
            continue
        nf.material_index, nf.smooth = f.material_index, f.smooth
        if suv is not None:
            for l, ol in zip(loops, nf.loops):
                ol[duv].uv = l[suv].uv
        for sl, dl in ints:
            nf[dl] = f[sl]
    # new elements carry no valid index: without this every face had the same one, and _islands / the triangle
    # lookup by face.index merged whole objects into one island (both feet voxelised as Foot_R)
    dst.verts.index_update()
    dst.faces.index_update()
    dst.verts.ensure_lookup_table()
    dst.faces.ensure_lookup_table()
    return dst


FACET_BULGE = 0.002        # a faceted vertex may lie this far outside its part: the joints keep their small overlap
                           # (pulled fully onto the surface, the hand left the forearm in ClimbUp)


def _main_group(v, deform):
    return max(v[deform].items(), key=lambda kv: kv[1])[0] if v[deform] else -1


def inside_surface(bm, src):
    """Pull every vertex of bm that lies more than FACET_BULGE outside src (same vertex group's surface) back to that
    distance (1.17). The collapse puts merged vertices at the quadric optimum, which can lie a few millimetres outside
    the part; the faceted form then touched its neighbours where the base form does not (Torso_Core / Thigh_L in
    CrouchDeep). Like the voxel inset, this keeps the form (nearly) inside the base form, so its collision-free clips
    carry over."""
    sd = src.verts.layers.deform.verify()
    dd = bm.verts.layers.deform.verify()
    polys = collections.defaultdict(list)
    for f in src.faces:
        polys[_main_group(f.verts[0], sd)].append([v.co.copy() for v in f.verts])
    trees = {}
    for g, ps in polys.items():
        verts, faces = [], []
        for p in ps:
            faces.append(list(range(len(verts), len(verts) + len(p))))
            verts += p
        trees[g] = BVHTree.FromPolygons(verts, faces)
    moved = 0
    for v in bm.verts:
        tree = trees.get(_main_group(v, dd))
        hit = tree.find_nearest(v.co) if tree else (None,)
        out = (v.co - hit[0]).dot(hit[1]) if hit[0] is not None else 0.0
        if out > FACET_BULGE:
            v.co = hit[0] + hit[1] * FACET_BULGE
            moved += 1
    return moved


def faceted_copy(src, coll):
    """Collapse-decimated copy with flat facets; screens and visor are carried over unchanged."""
    bm = bmesh.new()
    bm.from_mesh(src.data)
    keeps = form_keep(src, bm)
    keep = [f for f in bm.faces if keeps(f)]
    raw = bmesh.new()
    raw.from_mesh(src.data)
    raw_keep = form_keep(src, raw)
    bmesh.ops.delete(raw, geom=[f for f in raw.faces if raw_keep(f)], context="FACES")
    rest = canonical_bm(raw)
    raw.free()
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
    surface = canonical_bm(bm)      # nearest-face ties on edges must not depend on the process's face order
    inside_surface(out, surface)
    surface.free()
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


def _voxelise_island(isl, tris, cell, uv_layer, int_layers, group):
    """Cells whose centre lies inside the closed island (ray parity) and VOXEL_INSET deep; one quad per open cube
    side with UV, material and part of the nearest point of the surface, as data for emit_voxel_quads. Returns []
    for thin parts without such a cell (bolts, seams, rings): they stay smooth, a whole block would stick out far."""
    loops = [lt for f in isl for lt in tris[f.index]]
    verts = [l.vert.co for lt in loops for l in lt]
    tree = BVHTree.FromPolygons(verts, [(3 * i, 3 * i + 1, 3 * i + 2) for i in range(len(loops))])
    lo = Vector([min(v[i] for v in verts) for i in range(3)])
    hi = Vector([max(v[i] for v in verts) for i in range(3)])
    occ = set()

    def inside(o):
        """Ray parity, majority of three skewed rays: an axis ray grazes the edges of the boxy parts, which lie on the
        cell grid, and miscounted depending on the triangulation (1.17)."""
        odd = 0
        for ray in VOXEL_RAYS:
            n, p = 0, o
            while True:
                hit = tree.ray_cast(p, ray)
                if hit[0] is None:
                    break
                n += 1
                p = hit[0] + ray * 1e-5
            odd += n % 2
        return odd >= 2

    for ix in range(math.floor(lo.x / cell), math.ceil(hi.x / cell)):
        for iy in range(math.floor(lo.y / cell), math.ceil(hi.y / cell)):
            for iz in range(math.floor(lo.z / cell), math.ceil(hi.z / cell)):
                o = Vector(((ix + 0.5) * cell, (iy + 0.5) * cell, (iz + 0.5) * cell))
                if tree.find_nearest(o)[3] >= VOXEL_INSET * cell and inside(o):
                    occ.add((ix, iy, iz))
    quads = []
    for c in sorted(occ):
        for d in _VOX_DIRS:
            if (c[0] + d[0], c[1] + d[1], c[2] + d[2]) in occ:
                continue
            fc = Vector(((c[0] + 0.5 + d[0] / 2) * cell, (c[1] + 0.5 + d[1] / 2) * cell, (c[2] + 0.5 + d[2] / 2) * cell))
            _, near, ti, _ = tree.find_nearest(fc)
            lt = loops[ti]
            src_face = lt[0].face
            uv = None
            if uv_layer is not None:
                tri = [l.vert.co for l in lt]
                uv = mathutils.geometry.barycentric_transform(near, *tri, *[l[uv_layer].uv.to_3d() for l in lt])
                if not all(math.isfinite(x) for x in uv):         # degenerate triangle: its nearest corner
                    uv = min(lt, key=lambda l: (l.vert.co - near).length)[uv_layer].uv.to_3d()
                uv = (uv.x, uv.y)
            quads.append({"cell": c, "d": d, "uv": uv, "mat": src_face.material_index, "group": group,
                          "ints": tuple(src_face[sl] for sl, _ in int_layers)})
    return quads


# 1.17: greedy meshing - neighbouring block sides on one plane merge into rectangles when every map they show is the
# same (colour class, light, zones, badges, glow lines, materials, layers, wear, AO in coarse steps). The look stays,
# the triangle count drops. Without baked maps (QA workers before the bake) nothing merges; the surface is the same.
VOXEL_MERGE_MAPS = [("T_Robot_ColorMask.png", 2), ("T_Robot_LightMask.png", 2)] + \
    [(t, 2) for t in ZONE_TEXTURES + BADGE_TEXTURES + LINE_TEXTURES + MATERIAL_TEXTURES] + \
    [(t, 4) for t in LAYER_TEXTURES] + [("T_Robot_MetallicSmoothness.png", 4), ("T_Robot_Occlusion.png", 4)]


def voxel_appearance(uvs, tex_dir):
    """One integer per UV: equal numbers = equal in every map of VOXEL_MERGE_MAPS (levels = quantisation steps).
    None when a map is missing (no merging)."""
    if not tex_dir or not uvs or not all(os.path.exists(os.path.join(tex_dir, n)) for n, _ in VOXEL_MERGE_MAPS):
        return None
    uv = np.array(uvs, dtype=np.float64)
    cols = []
    for name, levels in VOXEL_MERGE_MAPS:
        img = bpy.data.images.load(os.path.join(tex_dir, name), check_existing=False)
        img.colorspace_settings.name = "Non-Color"
        w, h = img.size
        px = np.empty(w * h * 4, dtype=np.float32)
        img.pixels.foreach_get(px)
        bpy.data.images.remove(img)
        px = px.reshape(h, w, 4)
        x = np.clip((uv[:, 0] * w).astype(np.int64), 0, w - 1)
        y = np.clip((uv[:, 1] * h).astype(np.int64), 0, h - 1)
        cols.append(np.round(px[y, x] * (levels - 1)).astype(np.int8))
    keys = np.concatenate(cols, axis=1)
    _, ids = np.unique(keys, axis=0, return_inverse=True)
    return ids.reshape(-1).tolist()


def emit_voxel_quads(out, quads, cell, int_layers, uv_layer):
    """Greedy meshing of block sides into rectangles (quads with equal "key" on one plane) and the faces in out."""
    dl = out.verts.layers.deform.verify()
    ul = uv_layer_of(out) if uv_layer else None
    planes = collections.defaultdict(dict)
    for q in quads:
        k = next(i for i in range(3) if q["d"][i])
        a, b = [i for i in range(3) if i != k]
        planes[(q["group"], q["mat"], q["ints"], q["d"], q["cell"][k], q.get("key"))][(q["cell"][a], q["cell"][b])] = q
    for (group, mat, ints, d, layer, _), cells in sorted(planes.items(), key=lambda kv: repr(kv[0])):
        k = next(i for i in range(3) if d[i])
        a, b = [i for i in range(3) if i != k]
        done = set()
        for (ia, ib) in sorted(cells, key=lambda c: (c[1], c[0])):
            if (ia, ib) in done:
                continue
            ea = ia
            while (ea + 1, ib) in cells and (ea + 1, ib) not in done:
                ea += 1
            eb = ib
            while all((x, eb + 1) in cells and (x, eb + 1) not in done for x in range(ia, ea + 1)):
                eb += 1
            for x in range(ia, ea + 1):
                for y in range(ib, eb + 1):
                    done.add((x, y))
            q = cells[(ia, ib)]
            corners = []
            for pa, pb in ((ia, ib), (ea + 1, ib), (ea + 1, eb + 1), (ia, eb + 1)):
                p = [0.0, 0.0, 0.0]
                p[k] = (layer + (1 if d[k] > 0 else 0)) * cell
                p[a], p[b] = pa * cell, pb * cell
                corners.append(Vector(p))
            if Vector(d).dot((corners[1] - corners[0]).cross(corners[2] - corners[1])) < 0:
                corners.reverse()
            vs = [out.verts.new(c) for c in corners]
            for v in vs:
                v[dl][group] = 1.0
            f = out.faces.new(vs)
            f.material_index, f.smooth = mat, False
            if ul is not None and q["uv"] is not None:
                for l in f.loops:
                    l[ul].uv = q["uv"]
            for (_, dl_), val in zip(int_layers, ints):
                f[dl_] = val


def voxel_copies(part_objs, keys, coll, tex_dir=None):
    """Voxel copies of the part objects. Joints, fingers, thin parts, the visor and the screens stay smooth; the
    blocks lie inside the base form (VOXEL_INSET), so nothing covers the visor or the screens. With the baked maps
    in tex_dir, equal neighbouring block sides merge (emit_voxel_quads)."""
    built = []
    for key in keys:
        src = part_objs[key]
        raw = bmesh.new()
        raw.from_mesh(src.data)
        bm = canonical_bm(raw)          # the triangulation (nearest UV) must not depend on the process
        raw.free()
        bm.faces.ensure_lookup_table()
        tris = collections.defaultdict(list)
        for lt in bm.calc_loop_triangles():
            tris[lt[0].face.index].append(lt)
        keeps = form_keep(src, bm, VOXEL_KEEP)
        deform = bm.verts.layers.deform.verify()
        uv_layer = bm.loops.layers.uv.active
        out = bmesh.new()
        copier = FaceCopier(bm, out)
        smooth, quads = [], []
        for isl in _islands(bm):
            if keeps(isl[0]):
                smooth.extend(isl)
                continue
            group = max(isl[0].verts[0][deform].items(), key=lambda kv: kv[1])[0]
            got = _voxelise_island(isl, tris, VOXEL_CELL, uv_layer, copier.ints, group)
            if got:
                quads += got
            else:
                smooth.extend(isl)
        copier.copy(smooth)
        built.append((key, src, bm, out, copier, quads, uv_layer is not None))
    all_quads = [q for b in built for q in b[5]]
    ids = voxel_appearance([q["uv"] for q in all_quads], tex_dir) if all(q["uv"] for q in all_quads) else None
    for i, q in enumerate(all_quads):
        q["key"] = ids[i] if ids is not None else i         # no maps: every side stays its own face
    objs = collections.OrderedDict()
    for key, src, bm, out, copier, quads, has_uv in built:
        emit_voxel_quads(out, quads, VOXEL_CELL, copier.ints, has_uv)
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


def make_forms(part_objs, coll, arm_ob, tex_dir=None):
    """FORM_OBJS: the mesh-form copies of every part object, bound to the rig and hidden."""
    FORM_OBJS.clear()
    keys = [k for k in part_objs if not k.startswith("Form_")]
    for form in MESH_FORMS:
        t0 = time.time()
        if form == "Faceted":
            objs = collections.OrderedDict((k, faceted_copy(part_objs[k], coll)) for k in keys)
        else:
            objs = voxel_copies(part_objs, keys, coll, tex_dir)
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


__all__ = [_n for _n in dir() if not _n.startswith("__")]
