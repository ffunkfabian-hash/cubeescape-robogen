"""Preview renders, render cache, contact sheets, cards, icons and GIFs."""

from robogen.qa import *  # noqa: F401,F403 - one shared namespace, see robogen/__init__.py


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


# Value/RGB nodes hold their setting in the (linked) output (FinishRough, TintColor ...): content of the image. Only
# these - other nodes' output values are leftovers that differ between processes (1.20: a finish change was served
# stale from the cache because linked sockets were skipped).
_OUTPUT_VALUE_NODES = ("ShaderNodeValue", "ShaderNodeRGB")


def _tree_digest(tree, h, seen):
    # by identity: every material's embedded tree is called "Shader Nodetree" - a name would skip all but the first
    if tree is None or tree.as_pointer() in seen:
        return
    seen.add(tree.as_pointer())
    for nd in sorted(tree.nodes, key=lambda n: n.name):
        h.update(repr((nd.bl_idname, nd.name, _rna_values(nd, []))).encode())
        set_by_output = nd.bl_idname in _OUTPUT_VALUE_NODES
        for sock in list(nd.inputs) + list(nd.outputs):
            if (not sock.is_linked or (set_by_output and sock.is_output)) and hasattr(sock, "default_value"):
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
        if hit and robocache.get_file(cached, path):        # False: pruned meanwhile - render
            RENDER_CACHE["hits"] += 1
            return path
    bpy.ops.render.render(write_still=True)
    RENDER_CACHE["renders"] += 1
    if folder:
        robocache.put_file(path, cached)
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
    # without a special the values do not show, but they are reset: the render cache keys them (1.20), and a value
    # left from the previous skin in this worker would make the same image miss the cache
    nt.nodes["SpecialA"].outputs[0].default_value = (*sp["color"], 1.0) if sp else (1.0, 1.0, 1.0, 1.0)
    nt.nodes["SpecialB"].outputs[0].default_value = (*sp["color2"], 1.0) if sp else (1.0, 1.0, 1.0, 1.0)
    nt.nodes["SpecialStrength"].outputs[0].default_value = sp["strength"] if sp else 1.0


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
            folder = tex_dir if skin == DEFAULT_SKIN else skin_texture_dir(tex_dir)
            # "base_"/"emit_": half-size textures the comparison job left behind in this worker
            for nd in nt.nodes:
                if nd.type == "TEX_IMAGE" and nd.image.name.startswith(("T_Robot_BaseColor", "base_")):
                    nd.image = bpy.data.images.load(os.path.join(folder, "T_Robot_BaseColor%s.png" % suffix),
                                                    check_existing=True)
                elif nd.type == "TEX_IMAGE" and nd.image.name.startswith(("T_Robot_Emission", "emit_")):
                    nd.image = bpy.data.images.load(os.path.join(folder, "T_Robot_Emission%s.png" % suffix),
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


__all__ = [_n for _n in dir() if not _n.startswith("__")]
