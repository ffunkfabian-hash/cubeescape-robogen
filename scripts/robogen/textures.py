"""Texture baking (AO, layers), skin composition and the baked material."""

from robogen.assembly import *  # noqa: F401,F403 - one shared namespace, see robogen/__init__.py


# ----------------------------------------------------------------------------------------------
# Texture baking
# ----------------------------------------------------------------------------------------------


def halve(a):
    """2x2 box filter (h, w, c)."""
    h, w = a.shape[:2]
    return a.reshape(h // 2, 2, w // 2, 2, -1).mean(axis=(1, 3))


def unity_textures(results, layers, metal, smooth):
    """The packed Unity masks (UNITY_TEXTURES) as {name: (n, n, 4) float array}, n <= UNITY_TEXTURE_MAX."""
    def shrink(a):
        a = a if a.ndim == 3 else a[..., None]
        while a.shape[0] > UNITY_TEXTURE_MAX:
            a = halve(a)
        return a

    def rgba(*planes):
        n = planes[0].shape[0]
        out = np.zeros((n, n, 4), np.float32)
        for i, pl in enumerate(planes):
            if pl is not None:
                out[..., i] = pl[..., 0] if pl.ndim == 3 else pl
        return out

    def chans(kind, start):
        a = shrink(layers[kind])
        return [a[..., i] if i < a.shape[-1] else None for i in range(start, start + 4)]

    cm = shrink(np.clip(results["mask"][..., :3], 0, 1))
    zones = shrink(layers["zone_masks"])
    zone_id = np.where(zones.max(axis=-1) > 0.5, (zones.argmax(axis=-1) + 1) / 8.0, 0.0)
    lay = shrink(layers["layer_masks"])
    lines = shrink(layers["line_masks"])
    out = {
        "T_Robot_U_ColorLight.png": rgba(cm[..., 0], cm[..., 1], cm[..., 2], shrink(np.clip(results["mask_a"][..., :1], 0, 1))),
        "T_Robot_U_MetalWear.png": rgba(shrink(metal), lay[..., LAYERS.index("Wear")], lay[..., LAYERS.index("Grime")],
                                        shrink(smooth)),
        "T_Robot_U_Aux.png": rgba(lines[..., LINE_PATTERNS.index("Joints")], zone_id, lay[..., LAYERS.index("Camo")],
                                  None),
        "T_Robot_U_Layers.png": rgba(*[lay[..., LAYERS.index(n)] for n in ("Rust", "Moss", "Frost", "Embers")]),
        "T_Robot_U_Lines.png": rgba(*chans("line_masks", 0)),
        "T_Robot_U_Material.png": rgba(*chans("material_masks", 0)),
        "T_Robot_U_Material2.png": rgba(*chans("material_masks", 4)),
        "T_Robot_U_Badges.png": rgba(*chans("badge_masks", 0)),
        "T_Robot_U_Badges2.png": rgba(*chans("badge_masks", 4)),
        "T_Robot_U_Badges3.png": rgba(*chans("badge_masks", 8)),
    }
    assert list(out) == list(UNITY_TEXTURES)
    return out, cm.shape[0]


def write_png(path, pixels, width, height, channels, level=9):
    """Writes an 8-bit PNG. pixels: float array (h, w, c) in 0..1, row 0 = bottom (Blender order)."""
    arr = np.clip(np.round(pixels[::-1] * 255.0), 0, 255).astype(np.uint8)
    color_type = {1: 0, 3: 2, 4: 6}[channels]
    raw = b"".join(b"\x00" + arr[y].tobytes() for y in range(height))

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, level)) + chunk(b"IEND", b"")
    with open(path, "wb") as fh:
        fh.write(png)


def enable_gpu(device="gpu"):
    if device == "cpu":
        bpy.context.scene.cycles.device = "CPU"
        LOG.info("  Cycles device: CPU")
        return
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
        for dev_type in ("METAL", "OPTIX", "CUDA", "HIP", "ONEAPI"):
            try:
                prefs.compute_device_type = dev_type
            except TypeError:
                continue
            prefs.get_devices()
            gpus = [d for d in prefs.devices if d.type != "CPU"]
            if gpus:
                for d in prefs.devices:
                    d.use = d.type != "CPU"     # GPU only: deterministic, and the CPU is busy with the QA
                bpy.context.scene.cycles.device = "GPU"
                LOG.info("  Cycles device: %s (%s)", dev_type, ", ".join(d.name for d in gpus))
                return
    except Exception as exc:  # noqa: BLE001 - GPU is optional
        LOG.warning("  GPU setup failed, using CPU: %s", exc)
    bpy.context.scene.cycles.device = "CPU"
    LOG.info("  Cycles device: CPU")


def srgb_to_linear(c):
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(c):
    c = np.clip(c, 0, 1)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1 / 2.4) - 0.055)


BAKE_SCHEMA = "1.13-a"  # bump when the baking code changes in a way the cache key cannot see
# 1.13: the masks packed for Unity (Textures/Unity, at most 1024²) like the viewer packs them on load: zones as one ID
# channel, wear/grime next to metal/smoothness, joints and camo in an aux texture. Channels: robot_config.json >
# unityTextures. 10 textures instead of 14, a quarter of the pixels each.
UNITY_TEXTURES = collections.OrderedDict([
    ("T_Robot_U_ColorLight.png", "R armour, G mechanics, B metal (colour mask), A lights"),
    ("T_Robot_U_MetalWear.png", "R metallic, G wear (scratches), B grime, A smoothness"),
    ("T_Robot_U_Aux.png", "R glow pattern Joints, G paint zone ID (zone index + 1) / 8 - sample with point filter, "
                          "B camo field, A 0"),
    ("T_Robot_U_Layers.png", "R rust, G moss, B frost, A embers (equalised: amount a covers the share a)"),
    ("T_Robot_U_Lines.png", "glow line patterns 1-4 (linePatterns order)"),
    ("T_Robot_U_Material.png", "material patterns 1-4 (materials order)"),
    ("T_Robot_U_Material2.png", "material patterns 5-8"),
    ("T_Robot_U_Badges.png", "badges 1-4 (badges order)"),
    ("T_Robot_U_Badges2.png", "badges 5-8"),
    ("T_Robot_U_Badges3.png", "badges 9-12"),
])
UNITY_TEXTURE_MAX = 1024
STATIC_TEXTURES = ("T_Robot_MetallicSmoothness.png", "T_Robot_Occlusion.png", "T_Robot_ColorMask.png",
                   "T_Robot_LightMask.png", "T_Robot_Normal.png") + tuple(BADGE_TEXTURES + LINE_TEXTURES + ZONE_TEXTURES +
                                                                         LAYER_TEXTURES + MATERIAL_TEXTURES) + \
    tuple("Unity/" + n for n in UNITY_TEXTURES)
LAYER_SCALE = 16383.75  # cached float maps as uint16 over 0..4: steps of 6e-5, far below one 8-bit step
COMPOSE_TOLERANCE = 2e-3


def compare_skins():
    """The weathering comparison: Classic with every layer at three amounts, every material, three wear steps."""
    base = SKIN_DATA[DEFAULT_SKIN]
    out = []
    for lid, d in LAYER_DEFAULTS.items():
        for a in (0.3, 0.6, 1.0):
            out.append(("%s %d%%" % (lid, a * 100), dict(base, layers=[{"id": lid, "amount": a,
                                                                        "color": parse_color(d["color"])}])))
    for mid, d in MATERIAL_DEFAULTS.items():
        pal_ = dict(base["palette"], armor=parse_color(d["base"]))
        out.append((mid, dict(base, palette=pal_, material={"id": mid, "color": parse_color(d["color"])})))
    for w in (0.0, 0.5, 1.0):
        out.append(("Abnutzung %d%%" % (w * 100), dict(base, wear=w)))
    return out


def skin_texture_dir(tex_dir):
    """Composed preview skins (everything but the default skin): <out>/cache/skin_textures next to Textures."""
    return os.path.join(os.path.dirname(os.path.abspath(tex_dir)), "cache", "skin_textures")


def bake_textures(objs, mats, size, ao_samples, tex_dir, cache_dir=None, check_compose=False, device="auto",
                  compare_dir=None):
    """Atlas textures. Skins are not baked one by one: every colour of the body is linear in the palette (palette
    colours only pass through mixes and multiplies with scalar factors), so base = C + sum(w_k * palette_k) with
    grey weight maps w_k. One bake with the palette black gives C, one bake per three palette colours (one per RGB
    channel) gives the weights - six bakes for any number of skins, each skin is then composed with numpy.
    check_compose bakes the default skin directly and compares. With cache_dir, the layers and the static textures
    of an unchanged model (geometry, UVs, materials, source textures, settings) are reused without baking."""
    os.makedirs(tex_dir, exist_ok=True)
    geo_key = bake_geometry_key(objs, size, ao_samples)
    key = bake_key(geo_key, mats)
    ao_cache = os.path.join(cache_dir, "ao_%s.npz" % geo_key[:20]) if cache_dir else None
    stamp_path = os.path.join(tex_dir, ".bake.stamp")
    cache_path = os.path.join(cache_dir, "bake_%s.npz" % key[:20]) if cache_dir else None
    layers = None
    if cache_path and os.path.exists(cache_path) and read_text(stamp_path) == key and \
            all(os.path.exists(os.path.join(tex_dir, n)) for n in STATIC_TEXTURES):
        layers = load_layers(cache_path)
        LOG.info("  bake cache hit (%s): static textures and skin layers reused", key[:12])
        RUN_STATE["bake_hit"] = True
    if layers is None:
        if os.path.exists(stamp_path):
            os.remove(stamp_path)
        layers = bake_layers(objs, mats, size, ao_samples, tex_dir, check_compose, device, ao_cache)
        with open(stamp_path, "w", encoding="utf-8") as fh:
            fh.write(key)
        if cache_path:
            os.makedirs(cache_dir, exist_ok=True)
            for fn in os.listdir(cache_dir):
                if fn.startswith("bake_") and fn.endswith(".npz"):
                    os.remove(os.path.join(cache_dir, fn))
            save_layers(cache_path, layers)
    paths = {n: os.path.join(tex_dir, n) for n in STATIC_TEXTURES}
    # 1.17: only the default skin is a deliverable (the game and the viewer recolour through the masks); the other
    # composed skins are for the preview renders and live in cache/skin_textures, rewritten only when they change
    skin_dir = skin_texture_dir(tex_dir)
    os.makedirs(skin_dir, exist_ok=True)
    for fn in os.listdir(tex_dir):          # skins written into Textures by generators before 1.17
        if fn.startswith(("T_Robot_BaseColor_", "T_Robot_Emission_")) and fn.endswith(".png"):
            os.remove(os.path.join(tex_dir, fn))
    stamp_file = os.path.join(skin_dir, "stamps.json")
    stamps = {}
    if cache_dir and os.path.exists(stamp_file):        # --no-cache: every skin is written again
        with open(stamp_file, encoding="utf-8") as fh:
            stamps = json.load(fh)
    # what a composed skin depends on besides the bake (key) and its skin data: the code and its constants
    code = code_digest(("compose_skin", "decorate_skin", "cover", "linear_to_srgb", "write_png"), key, size,
                       LUMA.tolist(), LINE_PAINT, COVER_SOFT, BARE_METAL.tolist(), BADGES, LINE_PATTERNS,
                       list(PAINT_ZONES.items()), LAYERS, MATERIALS, WEAR_DEFAULT)

    def write_skin(skin):
        suffix = "" if skin == DEFAULT_SKIN else "_" + skin
        folder = tex_dir if skin == DEFAULT_SKIN else skin_dir
        names = ["T_Robot_BaseColor%s.png" % suffix, "T_Robot_Emission%s.png" % suffix]
        out = {n: os.path.join(folder, n) for n in names}
        stamp = hashlib.sha1((code + repr(sorted(SKIN_DATA[skin].items()))).encode()).hexdigest()
        if stamps.get(skin) == stamp and all(os.path.exists(f) for f in out.values()):
            return skin, stamp, out, False
        base, emit = decorate_skin(layers, SKIN_DATA[skin], *compose_skin(layers, SKINS[skin]))
        for name, arr in zip(names, (base * layers["ao_soft"][..., None], emit)):
            write_png(out[name], linear_to_srgb(arr), size, size, 3, level=6)   # 38 skins: level 9 cost seconds
        return skin, stamp, out, True

    t0 = time.time()
    written = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for skin, stamp, out, wrote in pool.map(write_skin, list(SKINS)):
            paths.update(out)
            stamps[skin] = stamp
            written += wrote
    write_json_atomic(stamp_file, stamps)
    LOG.info("  composed %d of %d skins in %.1fs (unchanged ones kept)", written, len(SKINS), time.time() - t0)
    if compare_dir:            # half-size textures for the weathering comparison renders
        os.makedirs(compare_dir, exist_ok=True)
        half = {k: (v[::2, ::2] if isinstance(v, np.ndarray) else {n: w[::2, ::2] for n, w in v.items()})
                for k, v in layers.items()}

        def write_compare(item):
            i, (_, rec) = item
            b, e = decorate_skin(half, rec, *compose_skin(half, rec["palette"]))
            write_png(os.path.join(compare_dir, "base_%02d.png" % i), linear_to_srgb(b * half["ao_soft"][..., None]),
                      size // 2, size // 2, 3, level=3)
            write_png(os.path.join(compare_dir, "emit_%02d.png" % i), linear_to_srgb(e), size // 2, size // 2, 3, level=3)

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(write_compare, enumerate(compare_skins())))
    for key_ in ("eyes", "displays", "flipbook"):       # runtime atlases ship next to the baked set
        dst = os.path.join(tex_dir, os.path.basename(SRC[key_].filepath))
        shutil.copyfile(bpy.path.abspath(SRC[key_].filepath), dst)
        paths[os.path.basename(dst)] = dst
    return paths


def bake_layers(objs, mats, size, ao_samples, tex_dir, check_compose, device, ao_cache=None):
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    enable_gpu("cpu" if device == "cpu" else "gpu")
    scene.cycles.use_denoising = False
    bake = scene.render.bake
    bake.margin = 12
    bake.margin_type = "EXTEND"
    bake.use_clear = True
    bake.target = "IMAGE_TEXTURES"
    # Cycles bakes every selected object in its own session; on Metal each costs a full device sync. One joined
    # copy of all parts turns 35 sessions per pass into one (the parts share the atlas and the materials).
    for o in objs:
        o.hide_render = True
    joined = joined_copy(objs, "Bake_All")
    for o in scene.objects:
        o.select_set(o == joined)
    bpy.context.view_layer.objects.active = joined
    dummy = bpy.data.images.new("bake_dummy", 32, 32, alpha=False, float_buffer=True)

    results = {}

    def bake_pass(label, channel, colorspace, samples=4, bake_type="EMIT"):
        img = bpy.data.images.new("bake_" + label, size, size, alpha=False, float_buffer=True)
        img.colorspace_settings.name = "Linear Rec.709" if colorspace == "linear" else "Non-Color"
        img.generated_color = (0, 0, 0, 1)
        restore = []
        for key in MAT_ORDER:
            mat = mats[key]
            nt = mat.node_tree
            tex = nt.nodes.new("ShaderNodeTexImage")
            tex.image = img if key in BODY_MATS else dummy   # screens keep their own atlases
            tex.name = "BakeTarget"
            nt.nodes.active = tex
            if channel is not None and key in BODY_MATS:
                out = nt.nodes["Output"]
                em = nt.nodes.new("ShaderNodeEmission")
                em.name = "BakeEmission"
                em.inputs["Strength"].default_value = 1.0
                if isinstance(channel, tuple):     # three scalar channels packed into R, G, B
                    comb = nt.nodes.new("ShaderNodeCombineColor")
                    comb.name = "BakeCombine"
                    for i, ch in enumerate(channel):
                        nt.links.new(CHANNELS[key][ch], comb.inputs[i])
                    src = comb.outputs[0]
                else:
                    src = CHANNELS[key][channel]
                if src.type == "VALUE":
                    comb = nt.nodes.new("ShaderNodeCombineColor")
                    comb.name = "BakeCombine"
                    for i in range(3):
                        nt.links.new(src, comb.inputs[i])
                    nt.links.new(comb.outputs[0], em.inputs["Color"])
                else:
                    nt.links.new(src, em.inputs["Color"])
                nt.links.new(em.outputs[0], out.inputs["Surface"])
            restore.append(mat)
        scene.cycles.samples = samples
        t0 = time.time()
        bpy.ops.object.bake(type=bake_type, margin=bake.margin, use_clear=True)
        LOG.info("  baked %-9s (%s, %d spp) in %.1fs", label, bake_type, samples, time.time() - t0)
        for key, mat in zip(MAT_ORDER, restore):
            nt = mat.node_tree
            for nm in ("BakeTarget", "BakeEmission", "BakeCombine"):
                nd = nt.nodes.get(nm)
                if nd:
                    nt.nodes.remove(nd)
            if key in BODY_MATS:
                nt.links.new(nt.nodes["BSDF"].outputs[0], nt.nodes["Output"].inputs["Surface"])
        px = np.empty(size * size * 4, dtype=np.float32)
        img.pixels.foreach_get(px)
        bpy.data.images.remove(img)
        return px.reshape(size, size, 4)

    keys = sorted({k for _, k in PALETTE_NODES})
    set_palette({})
    c_base, c_emit = bake_pass("SkinConst", "base", "linear"), bake_pass("SkinConstEmit", "emit", "linear")
    layers = {"c_base": c_base[..., :3], "c_emit": c_emit[..., :3], "w_base": {}, "w_emit": {}}
    for i in range(0, len(keys), 3):
        pack = keys[i:i + 3]
        set_palette({k: tuple(1.0 if c == j else 0.0 for c in range(3)) for j, k in enumerate(pack)})
        b = bake_pass("Skin_" + "+".join(pack), "base", "linear")
        e = bake_pass("SkinEmit_" + "+".join(pack), "emit", "linear")
        for j, k in enumerate(pack):
            layers["w_base"][k] = b[..., j] - c_base[..., j]
            layers["w_emit"][k] = e[..., j] - c_emit[..., j]
    apply_skin(DEFAULT_SKIN)
    if check_compose:
        direct = (bake_pass("Base_" + DEFAULT_SKIN, "base", "linear")[..., :3],
                  bake_pass("Emit_" + DEFAULT_SKIN, "emit", "linear")[..., :3])
        composed = compose_skin(layers, SKINS[DEFAULT_SKIN])
        err = max(float(np.abs(d - c).max()) for d, c in zip(direct, composed))
        LOG.info("  skin composition check (%s): max difference %.2e (linear)", DEFAULT_SKIN, err)
        if err > COMPOSE_TOLERANCE:
            raise RuntimeError("composed skin differs from the direct bake by %.2e - a palette colour no longer "
                               "enters linearly (bake skins one by one or fix the material)" % err)
    results = {}
    rma = bake_pass("Rough+Metal+LightMask", ("rough", "metal", "mask_a"), "data")
    results["rough"], results["metal"], results["mask_a"] = rma[..., 0:1], rma[..., 1:2], rma[..., 2:3]
    results["mask"] = bake_pass("ColorMask", "mask", "data", samples=1)
    cmask = np.clip(results["mask"][..., :3], 0, 1)
    layers["cmask"] = cmask
    armor, painted = cmask[..., 0], np.clip(cmask[..., 0] + cmask[..., 2], 0, 1)     # armour; armour + metal
    for kind, chans in (("badge_masks", ["badge_" + b for b in BADGES]),
                        ("line_masks", ["line_" + n for n in LINE_PATTERNS]),
                        ("zone_masks", ["zone_" + z for z in PAINT_ZONES]),
                        ("layer_masks", ["layer_" + n for n in LAYERS]),
                        ("material_masks", ["mat_" + n for n in MATERIALS])):
        planes = []
        for i in range(0, len(chans), 3):
            pack = chans[i:i + 3]
            px = bake_pass("Masks_" + "+".join(c.split("_", 1)[1] for c in pack),
                           tuple(pack + ["zero"] * (3 - len(pack))), "data")
            for j, ch in enumerate(pack):
                # badges and zones paint armour and metal (an ear cap), glow lines the armour, joints their parts
                limit = {"badge": painted, "zone": painted, "material": armor,
                         "layer": np.clip(cmask.sum(axis=-1), 0, 1)}.get(kind[:-6], armor if ch != "line_Joints" else 1.0)
                plane = np.clip(px[..., j], 0, 1)
                if ch[len("layer_"):] in EQUALISED_LAYERS and ch.startswith("layer_"):
                    plane = equalise(plane, limit > 0.5)
                planes.append(plane * limit)
        layers[kind] = np.stack(planes, axis=-1)
    bake.normal_space = "TANGENT"
    results["normal"] = bake_pass("Normal", None, "data", samples=4, bake_type="NORMAL")
    ao_cached = load_ao(ao_cache)
    if ao_cached is not None:
        ao = ao_cached
        LOG.info("  AO cache hit: geometry, UVs and AO settings unchanged")
    else:
        world = scene.world or bpy.data.worlds.new("BakeWorld")
        scene.world = world
        world.light_settings.distance = 0.10
        ao = np.clip(bake_ao_configs(objs, mats, size, ao_samples, dummy)[..., 0], 0, 1)
        save_ao(ao_cache, ao)

    layers["ao_soft"] = 1.0 - (1.0 - ao) * 0.55
    metal = np.clip(results["metal"][..., 0], 0, 1)
    smooth = 1.0 - np.clip(results["rough"][..., 0], 0, 1)
    files = {
        "T_Robot_MetallicSmoothness.png": (np.stack([metal, metal, metal, smooth], axis=-1), 4),
        "T_Robot_Occlusion.png": (np.stack([ao, ao, ao], axis=-1), 3),
        "T_Robot_ColorMask.png": (np.clip(results["mask"][..., :3], 0, 1), 3),
        "T_Robot_LightMask.png": (np.clip(results["mask_a"][..., :1], 0, 1), 1),
        "T_Robot_Normal.png": (np.clip(results["normal"][..., :3], 0, 1), 3),
    }
    for kind, names in (("badge_masks", BADGE_TEXTURES), ("line_masks", LINE_TEXTURES), ("zone_masks", ZONE_TEXTURES),
                        ("layer_masks", LAYER_TEXTURES), ("material_masks", MATERIAL_TEXTURES)):
        for t, name in enumerate(names):
            planes = layers[kind][..., 4 * t:4 * t + 4]
            if planes.shape[-1] < 4:            # unused channels stay black (room for later patterns)
                planes = np.concatenate([planes, np.zeros(planes.shape[:2] + (4 - planes.shape[-1],), np.float32)], -1)
            files[name] = (planes, 4)
    unity, usize = unity_textures(results, layers, metal, smooth)
    for name, (arr, ch) in files.items():
        path = os.path.join(tex_dir, name)
        write_png(path, arr, size, size, ch)
        LOG.info("  wrote %s", path)
    os.makedirs(os.path.join(tex_dir, "Unity"), exist_ok=True)
    for name, arr in unity.items():
        write_png(os.path.join(tex_dir, "Unity", name), arr, usize, usize, 4)
    LOG.info("  wrote %d Unity textures (%d²)", len(unity), usize)
    bpy.data.images.remove(dummy)
    remove_object(joined)
    for o in objs:
        o.hide_render = False
        o.select_set(True)
    return layers


def joined_copy(objs, name):
    """A temporary single object with copies of the given parts (same materials, UVs, normals, transforms)."""
    view_layer = bpy.context.view_layer
    copies = []
    for o in objs:
        c = o.copy()
        c.data = o.data.copy()
        c.hide_render = False
        bpy.context.scene.collection.objects.link(c)
        c.hide_set(False)
        copies.append(c)
    for o in bpy.context.scene.objects:
        o.select_set(o in copies)
    view_layer.objects.active = copies[0]
    if len(copies) > 1:
        bpy.ops.object.join()
    ob = view_layer.objects.active
    ob.name = name
    return ob


def remove_object(ob):
    me = ob.data
    bpy.data.objects.remove(ob, do_unlink=True)
    if me is not None and me.users == 0:
        bpy.data.meshes.remove(me)


def set_palette(colors):
    """Sets every palette node; keys missing in colors become black."""
    for nd, key in PALETTE_NODES:
        nd.outputs[0].default_value = (*colors.get(key, (0.0, 0.0, 0.0)), 1.0)


def compose_skin(layers, palette):
    """Linear base colour and emission of a skin from the baked layers (float32, no AO)."""
    base = layers["c_base"].astype(np.float32, copy=True)
    emit = layers["c_emit"].astype(np.float32, copy=True)
    for k, w in layers["w_base"].items():
        base += w[..., None] * np.asarray(palette[k], dtype=np.float32)
    for k, w in layers["w_emit"].items():
        emit += w[..., None] * np.asarray(palette[k], dtype=np.float32)
    return base, emit


def equalise(field, inside):
    """Rank transform inside the painted surface (via a 65536-bin histogram, no full sort): afterwards the share of
    pixels above 1 - a is a, so a layer amount reads as the covered share. Pixels outside stay as they are."""
    out = field.copy()
    vals = field[inside]
    if vals.size:
        bins = np.minimum((np.clip(vals, 0, 1) * 65535.0).astype(np.int64), 65535)
        counts = np.bincount(bins, minlength=65536)
        cdf = (np.cumsum(counts) - counts * 0.5) / vals.size       # mid-rank of each bin
        out[inside] = cdf[bins].astype(np.float32)
    return out


LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
LINE_PAINT = 0.85       # glow lines also tint the albedo (visible in daylight): share of the line colour x 0.6


COVER_SOFT = 0.1           # layer edge: cover = clamp((field - (1 - amount)) / COVER_SOFT, 0, 1)
BARE_METAL = np.array([0.62, 0.63, 0.65], dtype=np.float32)


def cover(field, amount, soft=COVER_SOFT):
    return np.clip((field - (1.0 - amount)) / soft, 0.0, 1.0)


def decorate_skin(layers, skin, base, emit):
    """Everything of a skin on top of the composed palette colours, in this order (same formulas as the viewer and the
    Unity shader): second colour in its zones -> wear (0 clean, 0.5 as baked, 1 battered) -> material pattern of the
    armour -> badges -> surface layers (rust, moss, frost, camo, embers) -> glow lines. Colour replacements keep the
    shading (luminance ratio to the region's palette colour)."""
    if not (skin["badges"] or skin["lines"] or skin["zones2"] or skin["material"] or skin["layers"] or
            abs(skin["wear"] - WEAR_DEFAULT) > 1e-6):
        return base, emit
    pal_ = skin["palette"]
    cm = layers["cmask"]
    lum = {k: float(np.asarray(pal_[k], np.float32) @ LUMA) for k in ("armor", "mech", "metal")}
    blum = base @ LUMA
    ratio = np.clip(blum / np.maximum(cm[..., 0] * lum["armor"] + cm[..., 2] * lum["metal"], 1e-3), 0.2, 1.6)[..., None]
    ratio_all = np.clip(blum / np.maximum(cm[..., 0] * lum["armor"] + cm[..., 1] * lum["mech"] + cm[..., 2] * lum["metal"],
                                          1e-3), 0.2, 1.6)[..., None]
    region = (cm[..., 0:1] * np.asarray(pal_["armor"], np.float32) + cm[..., 1:2] * np.asarray(pal_["mech"], np.float32) +
              cm[..., 2:3] * np.asarray(pal_["metal"], np.float32))
    if skin["zones2"]:
        m = np.clip(sum(layers["zone_masks"][..., list(PAINT_ZONES).index(z)] for z in skin["zones2"]), 0, 1)[..., None]
        base = base * (1.0 - m) + m * np.asarray(pal_["armor2"], dtype=np.float32) * ratio
        region = region * (1.0 - m) + m * np.asarray(pal_["armor2"], dtype=np.float32)
    w = skin["wear"]          # before material and layers: cleaning must not erase their patterns
    if w < WEAR_DEFAULT:                         # cleaner: scratches and grime back to the paint
        clean = (WEAR_DEFAULT - w) * 2.0
        sc = np.clip(layers["layer_masks"][..., LAYERS.index("Wear")][..., None] * 1.6, 0, 1) * clean
        base = base * (1.0 - sc) + sc * region
        gr = layers["layer_masks"][..., LAYERS.index("Grime")][..., None] * clean * 0.4
        base = base * (1.0 - gr) + gr * region
    elif w > WEAR_DEFAULT:                       # battered: more bare metal, dirt in the dents
        more = (w - WEAR_DEFAULT) * 2.0
        sc = np.clip(layers["layer_masks"][..., LAYERS.index("Wear")][..., None] * 4.0 * more, 0, 1) * 0.85
        base = base * (1.0 - sc) + sc * BARE_METAL
        base = base * (1.0 - 0.6 * more * layers["layer_masks"][..., LAYERS.index("Grime")][..., None])
    if skin["material"]:
        mat = skin["material"]
        p = layers["material_masks"][..., MATERIALS.index(mat["id"])][..., None]
        armor = cm[..., 0:1]
        base = base * (1.0 - armor) + armor * (base * p + np.asarray(mat["color"], np.float32) * ratio * (1.0 - p))
    for b in skin["badges"]:
        m = layers["badge_masks"][..., BADGES.index(b["id"])][..., None]
        base = base * (1.0 - m) + m * np.asarray(b["color"], dtype=np.float32) * ratio
    fields = {n: layers["layer_masks"][..., i][..., None] for i, n in enumerate(LAYERS)}
    by_id = {ly["id"]: ly for ly in skin["layers"]}
    for lid in ("Rust", "Moss", "Frost"):
        if lid in by_id:
            c = cover(fields[lid], by_id[lid]["amount"])
            col = np.asarray(by_id[lid]["color"], np.float32) * (0.6 + 0.6 * fields["Grime"])   # patchy, not flat
            base = base * (1.0 - c) + c * col * ratio_all
    if "Camo" in by_id:
        a, col = by_id["Camo"]["amount"], np.asarray(by_id["Camo"]["color"], np.float32)
        c1, c2 = cover(fields["Camo"], a), cover(fields["Camo"], a * 0.45)
        base = base * (1.0 - c1) + c1 * col * ratio_all
        base = base * (1.0 - c2) + c2 * col * 0.45 * ratio_all
    if "Embers" in by_id:
        a, col = by_id["Embers"]["amount"], np.asarray(by_id["Embers"]["color"], np.float32)
        char = cover(fields["Embers"], min(1.0, a * 2.5))
        glow = cover(fields["Embers"], a * 0.3, 0.04)                  # glowing veins in the charred area
        base = base * (1.0 - 0.88 * char)
        emit = emit + glow * col * 1.5
    if skin["lines"]:
        ln = skin["lines"]
        m = layers["line_masks"][..., LINE_PATTERNS.index(ln["pattern"])][..., None]
        col = np.asarray(ln["color"], dtype=np.float32)
        base = base * (1.0 - m * LINE_PAINT) + m * LINE_PAINT * 0.6 * col
        emit = emit + m * col * ln["strength"]
    return base, emit
    pal_ = skin["palette"]
    cm = layers["cmask"]
    ref = cm[..., 0] * float(np.asarray(pal_["armor"], np.float32) @ LUMA) + \
        cm[..., 2] * float(np.asarray(pal_["metal"], np.float32) @ LUMA)
    ratio = np.clip((base @ LUMA) / np.maximum(ref, 1e-3), 0.2, 1.6)[..., None]   # shading of the painted surface
    if skin["zones2"]:
        m = np.clip(sum(layers["zone_masks"][..., list(PAINT_ZONES).index(z)] for z in skin["zones2"]), 0, 1)[..., None]
        base = base * (1.0 - m) + m * np.asarray(pal_["armor2"], dtype=np.float32) * ratio
    for b in skin["badges"]:
        m = layers["badge_masks"][..., BADGES.index(b["id"])][..., None]
        base = base * (1.0 - m) + m * np.asarray(b["color"], dtype=np.float32) * ratio
    if skin["lines"]:
        ln = skin["lines"]
        m = layers["line_masks"][..., LINE_PATTERNS.index(ln["pattern"])][..., None]
        col = np.asarray(ln["color"], dtype=np.float32)
        base = base * (1.0 - m * LINE_PAINT) + m * LINE_PAINT * 0.6 * col
        emit = emit + m * col * ln["strength"]
    return base, emit


def save_layers(path, layers):
    flat = {"c_base": layers["c_base"], "c_emit": layers["c_emit"], "ao_soft": layers["ao_soft"],
            "badge_masks": layers["badge_masks"], "line_masks": layers["line_masks"],
            "zone_masks": layers["zone_masks"], "cmask": layers["cmask"], "layer_masks": layers["layer_masks"],
            "material_masks": layers["material_masks"]}
    for kind in ("w_base", "w_emit"):
        for k, w in layers[kind].items():
            flat["%s__%s" % (kind, k)] = w
    q = {n: np.round(np.clip(a, 0.0, 4.0) * LAYER_SCALE).astype(np.uint16) for n, a in flat.items()}
    tmp = path + ".tmp.npz"
    np.savez_compressed(tmp, **q)
    os.replace(tmp, path)


def load_layers(path):
    layers = {"w_base": {}, "w_emit": {}}
    with np.load(path) as data:
        for n in data.files:
            a = data[n].astype(np.float32) / LAYER_SCALE
            if "__" in n:
                kind, k = n.split("__", 1)
                layers[kind][k] = a
            else:
                layers[n] = a
    return layers


def read_text(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return None


NODE_ATTRS = ("operation", "blend_type", "data_type", "interpolation_type", "use_clamp", "clamp_factor",
              "clamp_result", "projection", "projection_blend", "interpolation", "extension", "uv_map", "space",
              "noise_dimensions", "normalize", "vector_type", "convert_from", "convert_to", "mode", "target")


def _sig_value(v):
    if isinstance(v, float):
        return round(v, 7)
    try:
        return tuple(round(float(x), 7) for x in v)
    except TypeError:
        return v


def node_tree_signature(nt):
    """Everything in a material that changes a bake - except the palette colours (skins are composed)."""
    rows = []
    for nd in sorted(nt.nodes, key=lambda n: n.name):
        row = [nd.name, nd.bl_idname, nd.label]
        for a in NODE_ATTRS:
            if hasattr(nd, a):
                row.append((a, str(getattr(nd, a))))
        img = getattr(nd, "image", None)
        if img is not None:
            row.append(("image", img.name, img.filepath, img.colorspace_settings.name))
        for sock in nd.inputs:
            if not sock.is_linked and hasattr(sock, "default_value"):
                row.append((sock.identifier, _sig_value(sock.default_value)))
        if not nd.label.startswith("PAL_"):
            for sock in nd.outputs:
                if hasattr(sock, "default_value"):
                    row.append(("out", sock.identifier, _sig_value(sock.default_value)))
        rows.append(repr(row))
    rows += sorted(repr((l.from_node.name, l.from_socket.identifier, l.to_node.name, l.to_socket.identifier))
                   for l in nt.links)
    return "\n".join(rows)


def load_ao(path):
    if not path or not os.path.exists(path):
        return None
    with np.load(path) as data:
        return data["ao"].astype(np.float32) / LAYER_SCALE


def save_ao(path, ao):
    if not path:
        return
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    for fn in os.listdir(folder):
        if fn.startswith("ao_") and fn.endswith(".npz"):
            os.remove(os.path.join(folder, fn))
    tmp = path + ".tmp.npz"
    np.savez_compressed(tmp, ao=np.round(np.clip(ao, 0.0, 4.0) * LAYER_SCALE).astype(np.uint16))
    os.replace(tmp, path)


def bake_key(geo_key, mats):
    """Content hash of everything the bakes depend on: geometry key + materials + source textures."""
    h = hashlib.sha256(geo_key.encode())
    for key in MAT_ORDER:
        sig = node_tree_signature(mats[key].node_tree)
        h.update(key.encode())
        h.update(sig.encode())
    for k in sorted(SRC):
        with open(bpy.path.abspath(SRC[k].filepath), "rb") as fh:
            h.update(fh.read())
    return h.hexdigest()


def bake_geometry_key(objs, size, ao_samples):
    """Content hash of what the AO depends on: meshes, UVs, material slots, size and samples (in canonical corner
    order, see canonical_order) and the code that makes normals and bakes. The normals themselves are left out:
    their float sums depend on the face order, they follow from the geometry and apply_weighted_normals."""
    h = hashlib.sha256()
    h.update(repr((BAKE_SCHEMA, bpy.app.version_string, size, ao_samples, MAT_ORDER, BODY_MATS)).encode())
    for fn in (apply_weighted_normals, bake_layers, bake_ao_configs, joined_copy):
        h.update(inspect.getsource(fn).encode())
    for o in sorted(objs, key=lambda o: o.name):
        me = o.data
        h.update(repr((o.name, sorted((k, str(o[k])) for k in o.keys()),
                       [sl.material.name if sl.material else None for sl in o.material_slots])).encode())
        h.update(np.array(o.matrix_world, dtype=np.float64).tobytes())
        co, lv, ls, lt, mi = mesh_arrays(me)
        perm, order = canonical_order(me)
        h.update(co[lv[perm]].tobytes())
        h.update(lt[order].tobytes())
        h.update(mi[order].tobytes())
        for uvl in me.uv_layers:
            uv = np.empty(len(me.loops) * 2, dtype=np.float32)
            uvl.data.foreach_get("uv", uv)
            h.update(uvl.name.encode())
            h.update(uv.reshape(-1, 2)[perm].tobytes())
    return h.hexdigest()


def bake_ao_configs(objs, mats, size, samples, dummy):
    """AO must only see one valid look at a time: all head/feet/... variants overlap in space and would darken
    each other. Bakes several configurations into one image; the default look is baked last so the core body gets
    the AO of the standard robot."""
    scene = bpy.context.scene
    core = [o for o in objs if not o.get("slot")]
    by_slot = collections.OrderedDict()
    for o in objs:
        if o.get("slot"):
            by_slot.setdefault(o["slot"], []).append(o)
    passes = []
    n = max(len(v) for v in by_slot.values())
    for i in range(n):
        passes.append(core + [vs[i % len(vs)] for vs in by_slot.values()])
    defaults = [o for o in objs if o.get("slot") and SLOTS.get(o["slot"], [None])[0] == o["variant"]]
    passes.append(core + defaults)
    img = bpy.data.images.new("bake_AO", size, size, alpha=False, float_buffer=True)
    img.colorspace_settings.name = "Non-Color"
    for key in MAT_ORDER:
        nt = mats[key].node_tree
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = img if key in BODY_MATS else dummy
        tex.name = "BakeTarget"
        nt.nodes.active = tex
    scene.cycles.samples = samples
    t0 = time.time()
    for o in scene.objects:
        o.hide_render = True
    for i, visible in enumerate(passes):
        conf = joined_copy(visible, "Bake_AO_%d" % i)     # one bake session per configuration (see bake_layers)
        for o in scene.objects:
            o.select_set(o == conf)
        bpy.context.view_layer.objects.active = conf
        # margin at most half the gap between packed islands: neighbouring islands of other variants (baked in other
        # passes) must not be overwritten
        bpy.ops.object.bake(type="AO", margin=max(1, int(UV_MARGIN * size / 2)), use_clear=(i == 0))
        remove_object(conf)
    for key in MAT_ORDER:
        nt = mats[key].node_tree
        nt.nodes.remove(nt.nodes["BakeTarget"])
    LOG.info("  baked AO        (%d configurations, %d spp) in %.1fs", len(passes), samples, time.time() - t0)
    px = np.empty(size * size * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    bpy.data.images.remove(img)
    return px.reshape(size, size, 4)


def build_baked_material(tex_paths, name="M_Robot", tint=None):
    mat = bpy.data.materials.new(name)
    if mat.node_tree is None:
        mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (300, 0)
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (600, 0)
    nt.links.new(bsdf.outputs[0], out.inputs["Surface"])

    def tex(name, colorspace, y):
        img = bpy.data.images.load(tex_paths[name], check_existing=True)
        img.colorspace_settings.name = colorspace
        nd = nt.nodes.new("ShaderNodeTexImage")
        nd.image = img
        nd.location = (-400, y)
        return nd

    base = tex("T_Robot_BaseColor.png", "sRGB", 300)
    if tint is None:
        nt.links.new(base.outputs["Color"], bsdf.inputs["Base Color"])
    else:
        mul = nt.nodes.new("ShaderNodeMix")
        mul.data_type, mul.blend_type = "RGBA", "MULTIPLY"
        mul.inputs[0].default_value = 1.0
        nt.links.new(base.outputs["Color"], mul.inputs[6])
        rgb = nt.nodes.new("ShaderNodeRGB")
        rgb.name = "TintColor"
        rgb.outputs[0].default_value = (*tint, 1.0)
        nt.links.new(rgb.outputs[0], mul.inputs[7])
        nt.links.new(mul.outputs[2], bsdf.inputs["Base Color"])
    ms = tex("T_Robot_MetallicSmoothness.png", "Non-Color", 0)
    sep = nt.nodes.new("ShaderNodeSeparateColor")
    sep.location = (-150, 0)
    nt.links.new(ms.outputs["Color"], sep.inputs[0])
    inv = nt.nodes.new("ShaderNodeMath")
    inv.operation = "SUBTRACT"
    inv.inputs[0].default_value = 1.0
    inv.location = (-150, -150)
    nt.links.new(ms.outputs["Alpha"], inv.inputs[1])
    # finish of the skin on the armour (ColorMask R), like the runtime shader: roughness x factor, metalness + offset
    cm = nt.nodes.new("ShaderNodeSeparateColor")
    nt.links.new(tex("T_Robot_ColorMask.png", "Non-Color", -200).outputs["Color"], cm.inputs[0])

    def finished(value, op, name, default):
        par = nt.nodes.new("ShaderNodeValue")
        par.name = name
        par.outputs[0].default_value = default
        f = nt.nodes.new("ShaderNodeMath")
        f.operation, f.use_clamp = op, True
        nt.links.new(value, f.inputs[0])
        nt.links.new(par.outputs[0], f.inputs[1])
        mix = nt.nodes.new("ShaderNodeMix")
        mix.data_type = "FLOAT"
        nt.links.new(cm.outputs[0], mix.inputs[0])
        nt.links.new(value, mix.inputs[2])
        nt.links.new(f.outputs[0], mix.inputs[3])
        return mix.outputs[0]

    std_rough, std_metal = FINISH_PARAMS["Standard"]     # the default skin's finish until a preview sets its own
    nt.links.new(finished(sep.outputs[0], "ADD", "FinishMetal", std_metal), bsdf.inputs["Metallic"])
    nt.links.new(finished(inv.outputs[0], "MULTIPLY", "FinishRough", std_rough), bsdf.inputs["Roughness"])
    nrm = tex("T_Robot_Normal.png", "Non-Color", -650)
    nmap = nt.nodes.new("ShaderNodeNormalMap")
    nmap.location = (-150, -650)
    nmap.uv_map = "UVMap"
    nt.links.new(nrm.outputs["Color"], nmap.inputs["Color"])
    nt.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
    em = tex("T_Robot_Emission.png", "sRGB", -350)
    nt.links.new(em.outputs["Color"], bsdf.inputs["Emission Color"])
    bsdf.inputs["Emission Strength"].default_value = 5.0
    preview_special_nodes(mat, bsdf, cm.outputs[0])
    mat.diffuse_color = (0.78, 0.8, 0.83, 1.0)
    return mat


def preview_special_nodes(mat, bsdf, armor):
    """1.13 special materials for the preview renders (static, time = 0): every effect computes colour, emission and
    alpha; value nodes SpecialW_<id> (0/1, set by set_look) pick one. Same formulas as the viewer shader."""
    k = NodeKit(mat)
    links = {name: bsdf.inputs[name].links[0].from_socket for name in ("Base Color", "Emission Color", "Roughness",
                                                                         "Metallic")}
    c, emit = links["Base Color"], links["Emission Color"]

    def rgb_node(name):
        nd = k.node("ShaderNodeRGB")
        nd.name = name
        nd.outputs[0].default_value = (1, 1, 1, 1)
        return nd.outputs[0]

    def value_node(name, v):
        nd = k.node("ShaderNodeValue")
        nd.name = name
        nd.outputs[0].default_value = v
        return nd.outputs[0]

    def hsv(hue, sat):
        nd = k.node("ShaderNodeCombineColor", mode="HSV")
        k.link(hue, nd.inputs[0])
        nd.inputs[1].default_value = sat
        nd.inputs[2].default_value = 1.0
        return nd.outputs[0]

    def white(vec=None, w=None):
        nd = k.node("ShaderNodeTexWhiteNoise", noise_dimensions="1D" if w is not None else "3D")
        k.link(w if w is not None else vec, nd.inputs["W" if w is not None else "Vector"])
        return nd.outputs["Value"]

    a, b, st = rgb_node("SpecialA"), rgb_node("SpecialB"), value_node("SpecialStrength", 1.0)
    vec, x, _, h = k.coords()                      # object space = character space (Z up), metres
    lw = k.node("ShaderNodeLayerWeight")
    lw.inputs["Blend"].default_value = 0.5
    facing = lw.outputs["Facing"]
    f = k.math("POWER", facing, 3.0)
    bw = k.node("ShaderNodeRGBToBW")
    k.link(c, bw.inputs[0])
    lum = bw.outputs[0]
    arm_s = k.math("MULTIPLY", armor, st)
    effects = []        # (id, colour, emission (strength 1) or None, alpha or None, roughness or None, metal or None)
    # Glass: tint, see-through inside, bright rim
    effects.append(("Glass", k.mix(k.math("MULTIPLY", st, 0.6), c, a), k.scale_color(b, k.math("MULTIPLY", f, 0.25)),
                    k.math("MULTIPLY_ADD", f, 0.6, c=0.35), 0.05, 0.0))
    # Hologram: scan lines 40 per metre of height
    scan = k.math("MULTIPLY_ADD", k.math("GREATER_THAN", k.math("FRACT", k.math("MULTIPLY", h, 40.0)), 0.5), 0.35,
                  c=0.65)
    holo_e = k.math("MULTIPLY", k.math("MULTIPLY", k.math("MULTIPLY_ADD", f, 1.6, c=0.35), scan), st)
    effects.append(("Hologram", k.scale_color(a, 0.15), k.scale_color(a, holo_e),
                    k.math("MULTIPLY", k.math("MULTIPLY_ADD", f, 0.6, c=0.25), scan), 0.2, 0.0))
    # Glitch: 12 bands per metre, a random value per band
    r = white(w=k.math("FLOOR", k.math("MULTIPLY", h, 12.0)))
    g1, g2 = k.math("GREATER_THAN", r, 0.78), k.math("GREATER_THAN", r, 0.9)
    gcol = k.mix(g2, a, b)
    effects.append(("Glitch", k.mix(k.math("MULTIPLY", g1, st), c, gcol),
                    k.scale_color(gcol, k.math("MULTIPLY", k.math("MULTIPLY", g1, st), 1.2)), None, None, None))
    # Iridescent: hue from the viewing angle and the height, on the armour
    iri = hsv(k.math("FRACT", k.math("MULTIPLY_ADD", f, 1.2, c=k.math("MULTIPLY", h, 0.35))), 0.55)
    effects.append(("Iridescent", k.mix(arm_s, c, k.scale_color(iri, k.math("DIVIDE", lum, 0.5))), None, None, None,
                    None))
    # Sketch: paper, ink outline from the rim, diagonal hatching where the surface is dark
    outline = k.map_range(facing, 0.25, 0.5, smooth=True)
    dark = k.math("SUBTRACT", 1.0, k.math("MULTIPLY", lum, 1.6), clamp=True)
    hatch = k.math("GREATER_THAN", k.math("FRACT", k.math("MULTIPLY", k.math("ADD", x, h), 45.0)), 0.7)
    ink = k.math("MAXIMUM", outline, k.math("MULTIPLY", hatch, dark))
    effects.append(("Sketch", k.mix(k.math("MULTIPLY", ink, st), k.mix(st, c, a), b), None, None, 1.0, 0.0))
    # Rainbow (animated at runtime): hue along the height and the side, on the armour
    rb = hsv(k.math("FRACT", k.math("MULTIPLY_ADD", h, 0.8, c=k.math("MULTIPLY", x, 0.3))), 0.75)
    effects.append(("Rainbow", k.mix(arm_s, c, k.scale_color(rb, k.math("DIVIDE", lum, 0.45))),
                    k.scale_color(rb, k.math("MULTIPLY", arm_s, 0.15)), None, None, None))
    # Galaxy (animated at runtime): nebula noise between the two colours, stars on a 120/m cell grid
    n = k.noise(k.stretch(vec, 3.0, 3.0, 3.0), 1.0, detail=3.0)
    neb = k.scale_color(k.mix(n, a, b), k.math("MULTIPLY_ADD", n, 0.8, c=0.4))
    cell = k.node("ShaderNodeVectorMath", operation="FLOOR")
    k.link(k.stretch(vec, 120.0, 120.0, 120.0), cell.inputs[0])
    stars = k.math("GREATER_THAN", white(vec=cell.outputs[0]), 0.985)
    gal_e = k.mix(stars, k.scale_color(neb, 0.5), (2.0, 2.0, 2.0))
    effects.append(("Galaxy", k.mix(arm_s, c, neb), k.scale_color(gal_e, arm_s), None, None, None))
    assert [e[0] for e in effects] == list(SPECIALS)
    col, em_add, alpha = c, None, 1.0
    rough, metal = links["Roughness"], links["Metallic"]
    # visor and lights (nothing in the colour mask) keep their look, like in the viewer; fabric takes the effect
    sep = armor.node
    painted = (1.0 if mat.name.startswith("M_RobotFabric") else
               k.math("ADD", k.math("ADD", sep.outputs[0], sep.outputs[1]), sep.outputs[2], clamp=True))
    for sid, ecol, eem, ealpha, erough, emetal in effects:
        w = k.math("MULTIPLY", value_node("SpecialW_" + sid, 0.0), painted)
        col = k.mix(w, col, ecol)
        if eem is not None:
            term = k.scale_color(eem, k.math("MULTIPLY", w, 0.2))        # the BSDF multiplies emission by 5
            em_add = term if em_add is None else _add_rgb(k, em_add, term)
        if ealpha is not None:
            alpha = k.math("ADD", alpha, k.math("MULTIPLY", w, k.math("SUBTRACT", ealpha, 1.0)))
        if erough is not None:
            rough = k.math("ADD", rough, k.math("MULTIPLY", w, k.math("SUBTRACT", erough, rough)))
            metal = k.math("ADD", metal, k.math("MULTIPLY", w, k.math("SUBTRACT", emetal, metal)))
    k.link(col, bsdf.inputs["Base Color"])
    k.link(_add_rgb(k, emit, em_add), bsdf.inputs["Emission Color"])
    # 1.14 form "Perforated": round holes on a grid in UV space (every second row offset by half a cell), armour only
    uvn = k.node("ShaderNodeUVMap")
    uvn.uv_map = "UVMap"
    uvs = k.node("ShaderNodeSeparateXYZ")
    k.link(uvn.outputs[0], uvs.inputs[0])
    cells = value_node("PerfCells", 50.0)
    u, v = k.math("MULTIPLY", uvs.outputs[0], cells), k.math("MULTIPLY", uvs.outputs[1], cells)
    shift = k.math("MULTIPLY", k.math("FLOORED_MODULO", k.math("FLOOR", v), 2.0), 0.5)
    du = k.math("SUBTRACT", k.math("FRACT", k.math("ADD", u, shift)), 0.5)
    dv = k.math("SUBTRACT", k.math("FRACT", v), 0.5)
    dist = k.math("SQRT", k.math("ADD", k.math("MULTIPLY", du, du), k.math("MULTIPLY", dv, dv)))
    hole = k.math("MULTIPLY", k.math("LESS_THAN", dist, PERFORATION["radius"]),
                  k.math("GREATER_THAN", sep.outputs[0], 0.5))
    alpha = k.math("MULTIPLY", alpha, k.math("SUBTRACT", 1.0, k.math("MULTIPLY", hole, value_node("PerfW", 0.0))))
    keep = k.node("NodeReroute")         # alpha is linked only while a see-through special or the form needs it
    keep.name = "SpecialAlpha"
    k.link(alpha, keep.inputs[0])
    k.link(rough, bsdf.inputs["Roughness"])
    k.link(metal, bsdf.inputs["Metallic"])


def _add_rgb(k, a, b):
    nd = k.node("ShaderNodeMix", data_type="RGBA", blend_type="ADD")
    nd.inputs[0].default_value = 1.0
    k.link(a, nd.inputs[6])
    k.link(b, nd.inputs[7])
    return nd.outputs[2]


FINAL_SLOTS = {"White": 0, "Dark": 0, "Grey": 0, "Visor": 0, "Orange": 0, "Fabric": 1, "EyeScreen": 2, "Display": 3}


def remap_materials(ob, final_mats):
    """Authoring slots -> M_Robot / M_RobotFabric / M_RobotEyes / M_RobotDisplay (only the used ones)."""
    me = ob.data
    idx = [FINAL_SLOTS[MAT_ORDER[p.material_index]] for p in me.polygons]
    used = sorted(set(idx))
    me.materials.clear()
    for u in used:
        me.materials.append(final_mats[u])
    lookup = {u: i for i, u in enumerate(used)}
    me.polygons.foreach_set("material_index", [lookup[i] for i in idx])


def combine_objects(objs, name, coll):
    """Joined copy of several part objects (vertex groups and custom normals are kept)."""
    copies = []
    for o in objs:
        c = o.copy()
        c.data = o.data.copy()
        c.modifiers.clear()
        c.parent = None
        coll.objects.link(c)
        copies.append(c)
    for o in bpy.context.scene.objects:
        o.select_set(o in copies)
    bpy.context.view_layer.objects.active = copies[0]
    bpy.ops.object.join()
    res = bpy.context.view_layer.objects.active
    res.name = name
    res.data.name = name
    return res


__all__ = [_n for _n in dir() if not _n.startswith("__")]
