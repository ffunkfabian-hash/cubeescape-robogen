"""Procedural materials, skins, decals, eyes and display."""

from robogen.layout import *  # noqa: F401,F403 - one shared namespace, see robogen/__init__.py


# ----------------------------------------------------------------------------------------------
# Materials (procedural authoring materials, later baked into one atlas material)
# ----------------------------------------------------------------------------------------------

MAT_ORDER = ["White", "Dark", "Grey", "Visor", "Orange", "Fabric", "EyeScreen", "Display"]
BODY_MATS = ["White", "Dark", "Grey", "Visor", "Orange", "Fabric"]
MAT_NAMES = {k: "M_Robot_" + k for k in MAT_ORDER}
CHANNELS = {}  # material key -> {"base", "rough", "metal", "emit"} output sockets

EYE_W, EYE_H = 0.086, 0.080
GLOW_W, GLOW_H = 0.150, 0.136


class NodeKit:
    """Tiny helper to write shader node graphs compactly."""

    def __init__(self, mat):
        self.nt = mat.node_tree
        self.n = self.nt.nodes
        self.l = self.nt.links
        self.x = -1400

    def node(self, kind, **props):
        nd = self.n.new(kind)
        nd.location = (self.x, 0)
        self.x += 40
        for k, v in props.items():
            setattr(nd, k, v)
        return nd

    def link(self, a, b):
        self.l.new(a, b)

    def value(self, v):
        nd = self.node("ShaderNodeValue")
        nd.outputs[0].default_value = v
        return nd.outputs[0]

    def rgb(self, c):
        nd = self.node("ShaderNodeRGB")
        nd.outputs[0].default_value = (c[0], c[1], c[2], 1.0)
        return nd.outputs[0]

    def math(self, op, a, b=None, clamp=False, c=None):
        nd = self.node("ShaderNodeMath", operation=op, use_clamp=clamp)
        for i, v in enumerate((a, b, c)):
            if v is None:
                continue
            if isinstance(v, (int, float)):
                nd.inputs[i].default_value = v
            else:
                self.link(v, nd.inputs[i])
        return nd.outputs[0]

    def mix(self, fac, a, b):
        """Color mix; a/b are sockets or rgb tuples, fac socket or float."""
        nd = self.node("ShaderNodeMix", data_type="RGBA", clamp_factor=True)
        if isinstance(fac, (int, float)):
            nd.inputs[0].default_value = fac
        else:
            self.link(fac, nd.inputs[0])
        for idx, v in ((6, a), (7, b)):
            if isinstance(v, tuple):
                nd.inputs[idx].default_value = (v[0], v[1], v[2], 1.0)
            else:
                self.link(v, nd.inputs[idx])
        return nd.outputs[2]

    def scale_color(self, col, fac):
        nd = self.node("ShaderNodeMix", data_type="RGBA", blend_type="MULTIPLY", clamp_factor=True)
        nd.inputs[0].default_value = 1.0
        self.link(col, nd.inputs[6])
        if isinstance(fac, (int, float)):
            nd.inputs[7].default_value = (fac, fac, fac, 1.0)
        else:
            self.link(fac, nd.inputs[7])
        return nd.outputs[2]

    def coords(self):
        tc = self.node("ShaderNodeTexCoord")
        sep = self.node("ShaderNodeSeparateXYZ")
        self.link(tc.outputs["Object"], sep.inputs[0])
        return tc.outputs["Object"], sep.outputs[0], sep.outputs[1], sep.outputs[2]

    def noise(self, vec, scale, detail=3.0, rough=0.55):
        nd = self.node("ShaderNodeTexNoise")
        self.link(vec, nd.inputs["Vector"])
        nd.inputs["Scale"].default_value = scale
        nd.inputs["Detail"].default_value = detail
        nd.inputs["Roughness"].default_value = rough
        return nd.outputs["Fac"]

    def map_range(self, v, a, b, c=0.0, d=1.0, smooth=False):
        nd = self.node("ShaderNodeMapRange", interpolation_type="SMOOTHSTEP" if smooth else "LINEAR")
        self.link(v, nd.inputs[0])
        nd.inputs[1].default_value = a
        nd.inputs[2].default_value = b
        nd.inputs[3].default_value = c
        nd.inputs[4].default_value = d
        return nd.outputs[0]

    def stretch(self, vec, sx, sy, sz):
        nd = self.node("ShaderNodeVectorMath", operation="MULTIPLY")
        self.link(vec, nd.inputs[0])
        nd.inputs[1].default_value = (sx, sy, sz)
        return nd.outputs[0]

    def ellipse_dist(self, x, z, cx, cz, rx, rz, mirror_x=True):
        ax = self.math("ABSOLUTE", x) if mirror_x else x
        dx = self.math("DIVIDE", self.math("SUBTRACT", ax, cx), rx)
        dz = self.math("DIVIDE", self.math("SUBTRACT", z, cz), rz)
        return self.math("SQRT", self.math("ADD", self.math("MULTIPLY", dx, dx), self.math("MULTIPLY", dz, dz)))


def new_material(key):
    mat = bpy.data.materials.new(MAT_NAMES[key])
    if mat.node_tree is None:
        mat.use_nodes = True
    mat.node_tree.nodes.clear()
    kit = NodeKit(mat)
    bsdf = kit.n.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (300, 0)
    out = kit.n.new("ShaderNodeOutputMaterial")
    out.location = (600, 0)
    out.name = "Output"
    bsdf.name = "BSDF"
    kit.link(bsdf.outputs[0], out.inputs["Surface"])
    return mat, kit, bsdf


def finish_material(key, mat, kit, bsdf, base, rough, metal, emit, emit_strength=0.0, viewport=(0.8, 0.8, 0.8)):
    kit.link(base, bsdf.inputs["Base Color"])
    kit.link(rough, bsdf.inputs["Roughness"])
    kit.link(metal, bsdf.inputs["Metallic"])
    kit.link(emit, bsdf.inputs["Emission Color"])
    bsdf.inputs["Emission Strength"].default_value = emit_strength
    mat.diffuse_color = (viewport[0], viewport[1], viewport[2], 1.0)
    mat.use_fake_user = True
    CHANNELS[key] = {"base": base, "rough": rough, "metal": metal, "emit": emit}


# Skins: every colour of the robot comes from one palette (data/skins.json). The generator composes a BaseColor/Emission
# pair per skin from shared baked layers and bakes masks (colour, badges, glow lines) for free tinting at runtime.
SKIN_FILE = os.path.normpath(os.path.join(SCRIPTS_DIR, "..", "data", "skins.json"))
SKIN_FORMAT, SKIN_FORMAT_VERSION = "CubeEscapeRobotSkins", 1
PALETTE_KEYS = ("armor", "mech", "metal", "eye", "screen", "accent", "visor", "fabric", "armor2")
PALETTE_DEFAULTS = {"visor": (0.006, 0.007, 0.010), "fabric": (0.78, 0.10, 0.12)}
# Finishes (IDs are permanent, saved in looks): roughness factor and metalness offset on the armour.
# 1.10: Candy and Chrom with less metal - in the dark game scenes a near-mirror armour read as black.
FINISH_PARAMS = collections.OrderedDict([("Standard", (1.0, 0.0)), ("Matt", (1.9, -0.25)), ("Satin", (1.35, 0.05)),
                                         ("Glanz", (0.45, 0.0)), ("Candy", (0.35, 0.08)), ("Chrom", (0.30, 0.32))])
FINISHES = list(FINISH_PARAMS)
# Badges: painted onto the armour at fixed anchors (decals of make_textures.BADGE_ART or procedural patterns). Each badge
# is one channel of a mask texture: index i -> BADGE_TEXTURES[i // 4], channel "RGBA"[i % 4]. Append only (IDs).
BADGES = ["HazardStripes", "Cross", "Cloud", "Number", "Biohazard", "Glyph", "RacingStripes", "Target", "Star", "Gear",
          "Leaf"]
BADGE_TEXTURES = ["T_Robot_BadgeMask.png", "T_Robot_BadgeMask2.png", "T_Robot_BadgeMask3.png"]
# Glow line patterns: emissive lines (on the armour, "Joints" on ring parts), one channel each of LINE_TEXTURES.
LINE_PATTERNS = ["Circuit", "Rings", "Seams", "Bolt", "Joints"]
LINE_TEXTURES = ["T_Robot_LineMask.png", "T_Robot_LineMask2.png"]
MAX_BADGES = 2
# Paint zones for the second armour colour (armor2): parts by name (without _L/_R). One channel each of ZONE_TEXTURES.
PAINT_ZONES = collections.OrderedDict([
    ("Head", ("Head_Shell", "Dome_Shell", "Boxy_Shell", "TV_Shell")),
    ("Ears", ("Ear_Ring", "Ear_Cap", "Dome_EarDisc", "Boxy_Bolt")),
    ("Chest", ("Chest_Plate", "Abdomen_Plate", "Backpack", "Battery_Cell")),
    ("Pads", ("Shoulder_Pad", "Slim_Pad", "Square_Pad")),
    ("Arms", ("Upper_Arm", "Forearm")),
    ("Hips", ("Hips",)),
    ("Legs", ("Thigh", "KneeCap", "Shin")),
    ("Feet", ("Foot_Shell", "Sneaker_Shell", "Mech_AnklePlate", "Mech_Heel")),
])
ZONE_TEXTURES = ["T_Robot_ZoneMask.png", "T_Robot_ZoneMask2.png"]
# 1.11 Weathering. Surface layers: equalised fields (amount 0..1 = covered share of the painted surface), one channel
# each; Wear (scratches) and Grime are raw fields for the wear amount. Materials: pattern of the armour (1 = armour
# colour, 0 = material colour). Append only (IDs, channel = position).
LAYERS = ["Rust", "Moss", "Frost", "Embers", "Camo", "Wear", "Grime"]
EQUALISED_LAYERS = ["Rust", "Moss", "Frost", "Embers", "Camo"]
LAYER_TEXTURES = ["T_Robot_LayerMask.png", "T_Robot_LayerMask2.png"]
MATERIALS = ["Wood", "Stone", "Sand", "Paper", "Concrete", "Brass", "Circuit"]
MATERIAL_TEXTURES = ["T_Robot_MaterialMask.png", "T_Robot_MaterialMask2.png"]
# defaults per layer/material: colour (sRGB) and surface (roughness factor, metal offset) like the finishes
LAYER_DEFAULTS = collections.OrderedDict([
    ("Rust", {"color": "#8a3a12", "roughness": 1.8, "metalOffset": -0.35}),
    ("Moss", {"color": "#4e6b1d", "roughness": 2.0, "metalOffset": -0.45}),
    ("Frost", {"color": "#cfeaff", "roughness": 0.7, "metalOffset": -0.2}),
    ("Embers", {"color": "#ff5c0a", "roughness": 1.6, "metalOffset": -0.3}),
    ("Camo", {"color": "#56602e", "roughness": 1.5, "metalOffset": -0.25}),
])
# "base" = a fitting armour colour (the comparison sheet and the viewer's material picker use it)
MATERIAL_DEFAULTS = collections.OrderedDict([
    ("Wood", {"color": "#5b3a1f", "base": "#c08a52", "roughness": 1.6, "metalOffset": -0.4}),
    ("Stone", {"color": "#4a4741", "base": "#a49c8c", "roughness": 1.9, "metalOffset": -0.45}),
    ("Sand", {"color": "#8f7449", "base": "#d2b07a", "roughness": 2.0, "metalOffset": -0.45}),
    ("Paper", {"color": "#a8987c", "base": "#e6dcc8", "roughness": 2.0, "metalOffset": -0.45}),
    ("Concrete", {"color": "#5d5b57", "base": "#9a958d", "roughness": 1.9, "metalOffset": -0.45}),
    ("Brass", {"color": "#7a5420", "base": "#c8963a", "roughness": 0.55, "metalOffset": 0.45}),
    ("Circuit", {"color": "#d8a63a", "base": "#1f7a3e", "roughness": 0.9, "metalOffset": -0.2}),
])
WEAR_DEFAULT = 0.5          # the baked look; 0 = clean, 1 = battered
# 1.13 Spectral: special materials are runtime effects on top of the composed skin (nothing extra is baked; without
# the effect the skin shows its palette). Formulas: docs/TECHNIK.md › 11; identical in preview_special_nodes() and the
# viewer shader. color = main colour, color2 = second colour, strength 0..1, speed = animation tempo (1 = default).
# IDs are permanent (append only). "urp" describes the later Unity material (game version Spektrum).
SPECIALS = collections.OrderedDict([
    ("Glass", {"color": "#9ed6f8", "color2": "#e8f8ff", "strength": 1.0, "speed": 0.0, "label": "Glas/Kristall",
               "description": "durchscheinend, Kanten heller (Fresnel), glatt",
               "urp": "Transparent (Alpha 0,35 innen → 0,95 am Rand per Fresnel), Smoothness 0,95, Metallic 0, "
                      "Rim-Emission in Farbe 2; Tiefe zuerst schreiben (Depth Prepass), damit sich Teile nicht "
                      "durchmischen"}),
    ("Hologram", {"color": "#7fa8ff", "color2": "#d8b0ff", "strength": 1.0, "speed": 1.0, "label": "Hologramm",
                  "description": "leuchtend, halbdurchsichtig, wandernde Scanlinien",
                  "urp": "Transparent additiv/Unlit: Emission Farbe × (0,35 + 1,6·Fresnel) × Scanlinien "
                         "(40 Linien je Meter Höhe, laufen mit 0,5·Tempo m/s nach oben), Alpha 0,25 + 0,6·Fresnel"}),
    ("Glitch", {"color": "#ff3df0", "color2": "#30f0ff", "strength": 1.0, "speed": 1.0, "label": "Glitch",
                "description": "zufällige Bänder springen in zwei Signalfarben",
                "urp": "Opak: Höhe in 12 Bänder je Meter, Zufallswert je Band und Takt (6·Tempo Hz); Bänder über "
                       "0,78 nehmen Farbe 1, über 0,9 Farbe 2, beide leuchten"}),
    ("Iridescent", {"color": "#ffffff", "color2": "#ffffff", "strength": 0.85, "speed": 0.0, "label": "Schillern",
                    "description": "Panzerung schillert je nach Blickwinkel in Regenbogenfarben",
                    "urp": "Opak: Farbton = frac(1,2·Fresnel + 0,35·Höhe), Sättigung 0,55, nur auf der Panzerung, "
                           "Helligkeit aus der Grundfarbe; alternativ Thin-Film-Lit"}),
    ("Sketch", {"color": "#efe6dc", "color2": "#2a2622", "strength": 1.0, "speed": 0.0, "label": "Skizze",
                "description": "Papierweiß mit Tuschekontur und Schraffur in dunklen Bereichen",
                "urp": "Unlit: Papierfarbe, Kontur aus Fresnel (0,25–0,5), Schraffur diagonal 45 Linien je Meter "
                       "(Strichanteil 30 %) in Bereichen dunkler als 62 %; optional Outline-Pass"}),
    ("Rainbow", {"color": "#ffffff", "color2": "#ffffff", "strength": 0.9, "speed": 1.0, "label": "Regenbogen",
                 "description": "Regenbogen wandert über die Panzerung (animiert)",
                 "urp": "Opak: Farbton = frac(0,8·Höhe + 0,3·Seite + 0,15·Tempo·Zeit), Sättigung 0,75, nur auf der "
                        "Panzerung, leichte Emission"}),
    ("Galaxy", {"color": "#2e1d6c", "color2": "#f077f8", "strength": 1.0, "speed": 1.0, "label": "Galaxie",
                "description": "Nebel und funkelnde Sterne auf der Panzerung (langsam ziehend)",
                "urp": "Opak: Nebel = 3D-Rauschen (3 Oktaven, Maßstab 3) zwischen Farbe 1 und 2, driftet mit "
                       "0,02·Tempo; Sterne = Zellraster 120/m mit Schwelle 0,985, leuchtend"}),
])
# 1.14 Reshape: body forms. "mesh" forms are geometry of their own (every part object rebuilt: same rig, same UVs on
# the texture atlas, so every skin, mask and badge still fits), "shader" forms a runtime cut-out (formula in
# docs/TECHNIK.md › 15, identical in preview and viewer), "parts" forms extra parts (group Form_<id>). A skin names its
# form; IDs are permanent (append only).
FORMS = collections.OrderedDict([
    ("Faceted", {"kind": "mesh", "label": "Facettiert",
                 "description": "flache, scharfkantige Facetten wie gefaltetes Papier (Origami, Geo)",
                 "urp": "eigene Meshes (Forms/…_Faceted.fbx), flach schattiert; dasselbe Material und dieselben Clips"}),
    ("Voxel", {"kind": "mesh", "label": "Voxel",
               "description": "aus Würfeln von 3 cm gebaut, jede Würfelseite einfarbig (Pixel)",
               "urp": "eigene Meshes (Forms/…_Voxel.fbx); Gelenke, Finger, Kleinteile, Visier und Bildschirme bleiben glatt"}),
    ("Perforated", {"kind": "shader", "label": "Durchbrochen",
                    "description": "runde Löcher in der Panzerung, innen sichtbar (Bone)",
                    "urp": "Alpha-Clip im Body-Shader: Lochraster im UV-Raum (perforation.cells je UV-Einheit, jede "
                           "zweite Reihe um eine halbe Zelle versetzt), Loch bei Abstand zur Zellmitte < "
                           "perforation.radius × Zelle, nur wo ColorMask R > 0,5; Cull Off; derselbe Clip auch im "
                           "ShadowCaster-, DepthOnly- und DepthNormals-Pass (sonst Schatten ohne Löcher und "
                           "Tiefe, wo die Farbe fehlt)"}),
    ("Wired", {"kind": "parts", "label": "Offene Kabel",
               "description": "Kabelstränge an Armen, Beinen, Hals und Rumpf (Wire)",
               "urp": "zusätzliches Teil Form_Wired im modularen FBX bzw. Forms/…_Wired.fbx, starr an den Knochen"}),
])
MESH_FORMS = [f for f, d in FORMS.items() if d["kind"] == "mesh"]
QA_FORMS = ["Form_" + f for f, d in FORMS.items() if d["kind"] in ("mesh", "parts")]   # checked like robot types
PERFORATION = {"spacing": 0.045, "radius": 0.33}    # metres between hole centres, hole radius as share of a cell
GLOW_PARTS = ("Ear_Ring", "Neck_Ring", "Waist_Ring", "Elbow_Band", "Battery_Ring")    # line pattern "Joints"


def part_base(name):
    return name[:-2] if name.endswith(("_L", "_R")) else name


def paint_zone(name):
    """1-based zone index of a part (0 = none)."""
    base = part_base(name)
    for i, parts in enumerate(PAINT_ZONES.values()):
        if base in parts:
            return i + 1
    return 0


def srgb_channel_to_linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def parse_color(v):
    """Linear RGB tuple from a linear [r, g, b] list or an sRGB '#rrggbb' string."""
    if isinstance(v, str):
        if len(v) != 7 or v[0] != "#":
            raise ValueError("colour %r: expected '#rrggbb'" % v)
        return tuple(round(srgb_channel_to_linear(int(v[i:i + 2], 16) / 255.0), 5) for i in (1, 3, 5))
    if len(v) != 3 or not all(0.0 <= float(x) <= 1.0 for x in v):
        raise ValueError("colour %r: expected three values 0..1" % (v,))
    return tuple(float(x) for x in v)


def load_skins(path=SKIN_FILE):
    """data/skins.json -> ordered {id: record} with parsed (linear) colours. Errors name the skin."""
    with open(path, encoding="utf-8") as fh:
        doc = json.load(fh)
    if doc.get("format") != SKIN_FORMAT or doc.get("version") != SKIN_FORMAT_VERSION:
        raise ValueError("%s: expected %s version %d" % (path, SKIN_FORMAT, SKIN_FORMAT_VERSION))
    skins = collections.OrderedDict()
    for rec in doc["skins"]:
        sid = rec["id"]
        try:
            if sid in skins:
                raise ValueError("duplicate id")
            pal_ = {k: parse_color(rec["palette"][k]) for k in PALETTE_KEYS if k in rec["palette"]}
            for k in ("armor", "mech", "metal", "eye", "screen", "accent"):
                if k not in pal_:
                    raise ValueError("palette lacks %s" % k)
            for k, v in PALETTE_DEFAULTS.items():
                pal_.setdefault(k, v)
            if rec.get("finish", "Standard") not in FINISH_PARAMS:
                raise ValueError("unknown finish %r" % rec["finish"])
            badges = [{"id": b["id"], "color": parse_color(b["color"])} for b in rec.get("badges", [])]
            if len(badges) > MAX_BADGES or any(b["id"] not in BADGES for b in badges):
                raise ValueError("badges %s (at most %d of %s)" % ([b["id"] for b in badges], MAX_BADGES, BADGES))
            if "armor2" in rec["palette"]:
                pal_["armor2"] = parse_color(rec["palette"]["armor2"])
            zones2 = list(rec.get("zones2", []))
            if any(z not in PAINT_ZONES for z in zones2) or (zones2 and "armor2" not in pal_):
                raise ValueError("zones2 %s need palette.armor2 and zones of %s" % (zones2, list(PAINT_ZONES)))
            material = None
            if rec.get("material"):
                m = rec["material"]
                if m["id"] not in MATERIALS:
                    raise ValueError("material %r" % m)
                material = {"id": m["id"], "color": parse_color(m.get("color", MATERIAL_DEFAULTS[m["id"]]["color"]))}
            layers = []
            for ly in rec.get("layers", []):
                if ly["id"] not in LAYER_DEFAULTS or not 0.0 <= float(ly["amount"]) <= 1.0:
                    raise ValueError("layer %r" % ly)
                layers.append({"id": ly["id"], "amount": float(ly["amount"]),
                               "color": parse_color(ly.get("color", LAYER_DEFAULTS[ly["id"]]["color"]))})
            special = None
            if rec.get("special"):
                sp = rec["special"]
                if sp["id"] not in SPECIALS:
                    raise ValueError("special %r" % sp)
                d = SPECIALS[sp["id"]]
                special = {"id": sp["id"], "color": parse_color(sp.get("color", d["color"])),
                           "color2": parse_color(sp.get("color2", d["color2"])),
                           "strength": float(sp.get("strength", d["strength"])),
                           "speed": float(sp.get("speed", d["speed"]))}
                if not 0.0 <= special["strength"] <= 1.0 or not 0.0 <= special["speed"] <= 4.0:
                    raise ValueError("special %r: strength 0..1, speed 0..4" % sp)
            if rec.get("form", "None") not in ["None"] + list(FORMS):
                raise ValueError("form %r (one of %s)" % (rec["form"], list(FORMS)))
            wear = float(rec.get("wear", WEAR_DEFAULT))
            if not 0.0 <= wear <= 1.0:
                raise ValueError("wear %r" % wear)
            lines = None
            if rec.get("lines"):
                ln = rec["lines"]
                if ln["pattern"] not in LINE_PATTERNS or not 0.0 < float(ln.get("strength", 1.0)) <= 2.0:
                    raise ValueError("lines %r" % ln)
                lines = {"pattern": ln["pattern"], "color": parse_color(ln["color"]),
                         "strength": float(ln.get("strength", 1.0))}
        except (KeyError, ValueError, TypeError) as exc:
            raise ValueError("%s: skin %s: %s" % (path, sid, exc)) from exc
        pal_.setdefault("armor2", pal_["armor"])
        skins[sid] = {"id": sid, "palette": pal_, "zones2": zones2, "finish": rec.get("finish", "Standard"),
                      "material": material, "layers": layers, "wear": wear, "badges": badges, "special": special,
                      "form": None if rec.get("form", "None") == "None" else rec["form"],
                      "lines": lines, "parts": dict(rec.get("parts", {})), "expression": rec.get("expression"),
                      "display": rec.get("display"), "theme": rec.get("theme", ""), "since": rec.get("since", ""),
                      "stage": rec.get("stage", ""), "startSet": bool(rec.get("startSet")),
                      "concept": rec.get("concept", "")}
    return skins


SKIN_DATA = load_skins()
SKINS = collections.OrderedDict((sid, rec["palette"]) for sid, rec in SKIN_DATA.items())   # id -> palette
DEFAULT_SKIN = "Classic"
# colour masks: ColorMask RGB = armour, mechanics, metal; LightMask = lights; visor = 1 - sum (body faces)
MASKS = {"White": (1, 0, 0, 0), "Dark": (0, 1, 0, 0), "Grey": (0, 0, 1, 0), "Visor": (0, 0, 0, 0),
         "Orange": (0, 0, 0, 1), "Fabric": (0, 0, 0, 0)}

PALETTE_NODES = []  # (node, palette key)


def pal(kit, key, factor=1.0):
    """Palette colour socket (optionally scaled). Nodes are re-coloured per skin before baking."""
    nd = kit.node("ShaderNodeRGB")
    nd.label = "PAL_" + key
    nd.outputs[0].default_value = (*SKINS[DEFAULT_SKIN][key], 1.0)
    PALETTE_NODES.append((nd, key))
    return nd.outputs[0] if factor == 1.0 else kit.scale_color(nd.outputs[0], factor)


def apply_skin(name):
    for nd, key in PALETTE_NODES:
        nd.outputs[0].default_value = (*SKINS[name][key], 1.0)


SRC = {}  # ImageMagick source images (bpy.data.images), filled by load_source_textures


def load_source_textures(paths):
    for key, path in paths.items():
        img = bpy.data.images.load(path, check_existing=True)
        img.colorspace_settings.name = "Non-Color"
        SRC[key] = img


def box_tex(k, key, vec, scale):
    """Tileable ImageMagick texture, box-projected in object space (1 tile = 1/scale m)."""
    nd = k.node("ShaderNodeTexImage", projection="BOX", projection_blend=0.3, interpolation="Cubic")
    nd.image = SRC[key]
    k.link(k.stretch(vec, scale, scale, scale), nd.inputs["Vector"])
    return nd.outputs["Color"]


def decal(k, us, vs, rect, px, extra_mask=None, image="decals"):
    """Projects a region of an ImageMagick atlas (decals or badges) onto the plane (us, vs) inside rect (u0, u1, v0, v1)."""
    u0, u1, v0, v1 = rect
    x, y, w, h = px
    size = float(SRC[image].size[0])
    u = k.math("DIVIDE", k.math("SUBTRACT", us, u0), u1 - u0)
    v = k.math("DIVIDE", k.math("SUBTRACT", vs, v0), v1 - v0)
    inside = k.math("MULTIPLY", k.math("MULTIPLY", k.math("GREATER_THAN", u, 0.0), k.math("LESS_THAN", u, 1.0)),
                    k.math("MULTIPLY", k.math("GREATER_THAN", v, 0.0), k.math("LESS_THAN", v, 1.0)))
    au = k.math("ADD", k.math("MULTIPLY", u, w / size), x / size)
    av = k.math("ADD", k.math("MULTIPLY", v, h / size), 1.0 - (y + h) / size)
    comb = k.node("ShaderNodeCombineXYZ")
    k.link(au, comb.inputs[0])
    k.link(av, comb.inputs[1])
    tex = k.node("ShaderNodeTexImage", extension="CLIP", interpolation="Cubic")
    tex.image = SRC[image]
    k.link(comb.outputs[0], tex.inputs["Vector"])
    mask = k.math("MULTIPLY", tex.outputs["Alpha"], inside)
    if extra_mask is not None:
        mask = k.math("MULTIPLY", mask, extra_mask)
    return tex.outputs["Color"], mask


def bump(k, bsdf, height, strength, distance=0.002):
    nd = k.node("ShaderNodeBump")
    nd.inputs["Strength"].default_value = strength
    nd.inputs["Distance"].default_value = distance
    k.link(height, nd.inputs["Height"])
    k.link(nd.outputs["Normal"], bsdf.inputs["Normal"])


def surface(k, scale):
    """Shared wear layers from the ImageMagick textures."""
    vec, x, y, z = k.coords()
    g = k.map_range(box_tex(k, "grunge", vec, scale), 0.15, 0.85)
    br = k.map_range(box_tex(k, "brushed", vec, scale * 1.6), 0.45, 1.0)
    sc = k.map_range(box_tex(k, "scratches", vec, scale * 1.3), 0.2, 0.75)
    return vec, x, y, z, g, br, sc


VISOR_ROUGHNESS, VISOR_METALLIC = 0.07, 0.15

# Badge anchors: (plane, rect (u0, u1, v0, v1) in m, side mask). Planes: "side" = (y * sign(x), z) on both outer sides
# (reads the same from either side), "front" = (x, z), "back" = (-x, z), each seen from outside.
def badge_anchors():
    return {
        "shoulder": [("side", (-0.07, 0.07, 0.77, 0.91), ("absx>", 0.29))],
        "thigh": [("front", (s * LEG_X - 0.062, s * LEG_X + 0.062, 0.268, 0.392), ("y<", -0.02)) for s in (1, -1)],
        "head_back": [("back", (-0.10, 0.10, 1.06, 1.26), ("y>", 0.12))],
        "back": [("back", (-0.11, 0.11, 0.67, 0.89), ("y>", 0.15))],
        "ear": [("side", (-0.07, 0.09, 1.02, 1.18), ("absx>", 0.24))],        # ear disc (Classic: ring + cap)
        "chest": [("front", (-0.046, 0.046, 0.664, 0.756), ("y<", -0.10))],   # below the LEDs and the display
    }



BADGE_PLACES = {"Cross": ("ear", "chest", "shoulder", "thigh", "back"),
                "Cloud": ("ear", "shoulder", "thigh", "head_back"),
                "Number": ("chest", "shoulder", "thigh", "back"), "Biohazard": ("ear", "chest", "shoulder", "back"),
                "Glyph": ("ear", "shoulder", "back", "head_back"), "Target": ("ear", "shoulder", "thigh", "head_back"),
                "Star": ("ear", "chest", "shoulder", "back"), "Gear": ("ear", "shoulder", "thigh", "back"),
                "Leaf": ("ear", "shoulder", "thigh", "head_back")}


def layer_fields(k, vec, g, sc):
    """Raw fields of the surface layers (baked, then equalised so that amount = covered share). Shared by armour,
    mechanics and metal, so rust and moss also reach joints. Wear = scratches, Grime = grime (raw, for the wear amount)."""
    geo = k.node("ShaderNodeNewGeometry")
    sep = k.node("ShaderNodeSeparateXYZ")
    k.link(geo.outputs["Normal"], sep.inputs[0])
    up = k.math("MAXIMUM", sep.outputs[2], 0.0)
    nmax = k.math("MAXIMUM", k.math("MAXIMUM", k.math("ABSOLUTE", sep.outputs[0]), k.math("ABSOLUTE", sep.outputs[1])),
                  k.math("ABSOLUTE", sep.outputs[2]))
    edge = k.map_range(nmax, 0.95, 0.6)                      # rounded edges and corners
    ao = k.node("ShaderNodeAmbientOcclusion", samples=8)
    ao.inputs["Distance"].default_value = 0.06
    crevice = k.math("SUBTRACT", 1.0, ao.outputs["AO"])
    blotch = k.noise(vec, 4.0, 4.0, 0.6)
    fine = k.noise(vec, 18.0, 3.0, 0.55)

    def blend(*terms):
        acc = None
        for w, t in terms:
            v = k.math("MULTIPLY", t, w)
            acc = v if acc is None else k.math("ADD", acc, v)
        return acc

    return {
        "layer_Rust": blend((0.4, blotch), (0.35, crevice), (0.15, edge), (0.1, g)),
        "layer_Moss": blend((0.45, up), (0.3, crevice), (0.25, blotch)),
        "layer_Frost": blend((0.5, up), (0.3, edge), (0.2, fine)),
        "layer_Embers": box_tex(k, "cracks", vec, 1.3),
        "layer_Camo": box_tex(k, "camo", vec, 0.9),
        "layer_Wear": sc,
        "layer_Grime": g,
    }


def material_fields(k, vec, x, y, z):
    """Armour patterns of the materials: 1 = armour colour, 0 = material colour (object space, baked)."""
    out = {}
    grain = k.node("ShaderNodeTexWave", wave_type="RINGS", rings_direction="SPHERICAL", wave_profile="SIN")
    k.link(k.stretch(vec, 1.0, 1.0, 0.12), grain.inputs["Vector"])
    grain.inputs["Scale"].default_value = 7.0
    grain.inputs["Distortion"].default_value = 7.0
    grain.inputs["Detail"].default_value = 3.0
    out["mat_Wood"] = k.map_range(k.math("ADD", k.math("MULTIPLY", grain.outputs["Fac"], 0.8),
                                         k.math("MULTIPLY", k.noise(k.stretch(vec, 1.0, 1.0, 0.2), 60.0, 2.0), 0.3)),
                                  0.25, 1.0)
    cells = k.node("ShaderNodeTexVoronoi", feature="DISTANCE_TO_EDGE")
    k.link(vec, cells.inputs["Vector"])
    cells.inputs["Scale"].default_value = 8.0
    mortar = k.map_range(cells.outputs["Distance"], 0.0, 0.06)
    carve = k.map_range(k.math("ABSOLUTE", k.math("SUBTRACT", k.math("FRACT", k.math("MULTIPLY", z, 6.0)), 0.5)),
                        0.44, 0.47)                          # engraved bands (Temple)
    speck = k.map_range(k.noise(vec, 45.0, 3.0), 0.3, 0.7, 0.75, 1.0)
    out["mat_Stone"] = k.math("MULTIPLY", k.math("MULTIPLY", mortar, k.math("SUBTRACT", 1.0, k.math("MULTIPLY", carve, 0.7))),
                              speck)
    ripple = k.node("ShaderNodeTexWave", wave_type="BANDS", bands_direction="Z", wave_profile="SIN")
    k.link(vec, ripple.inputs["Vector"])
    ripple.inputs["Scale"].default_value = 9.0
    ripple.inputs["Distortion"].default_value = 3.0
    out["mat_Sand"] = k.math("ADD", k.math("MULTIPLY", k.map_range(k.noise(vec, 160.0, 2.0), 0.3, 0.7), 0.7),
                             k.math("MULTIPLY", ripple.outputs["Fac"], 0.3))
    fibres = k.noise(k.stretch(vec, 1.0, 1.0, 0.08), 70.0, 4.0, 0.7)
    fold = k.node("ShaderNodeTexWave", wave_type="BANDS", bands_direction="DIAGONAL", wave_profile="SAW")
    k.link(vec, fold.inputs["Vector"])
    fold.inputs["Scale"].default_value = 2.5
    out["mat_Paper"] = k.map_range(k.math("SUBTRACT", k.math("ADD", k.math("MULTIPLY", fibres, 0.35), 0.75),
                                          k.math("MULTIPLY", k.map_range(fold.outputs["Fac"], 0.9, 1.0), 0.35)),
                                   0.4, 1.0)
    pores = k.node("ShaderNodeTexVoronoi", feature="F1")
    k.link(vec, pores.inputs["Vector"])
    pores.inputs["Scale"].default_value = 70.0
    out["mat_Concrete"] = k.math("MULTIPLY", k.map_range(pores.outputs["Distance"], 0.05, 0.25),
                                 k.map_range(k.noise(vec, 12.0, 4.0), 0.25, 0.75, 0.55, 1.0))
    out["mat_Brass"] = k.map_range(k.math("ADD", k.math("MULTIPLY", box_tex(k, "brushed", vec, 2.5), 0.5),
                                          k.math("MULTIPLY", k.noise(vec, 6.0, 3.0), 0.5)), 0.3, 0.8, 0.45, 1.0)
    out["mat_Circuit"] = k.math("SUBTRACT", 1.0, k.map_range(box_tex(k, "circuit", vec, 2.2), 0.25, 0.75))
    return out


def pattern_masks(k, vec, x, y, z):
    """Badge and glow line masks of the armour (0..1 scalars, keys "badge_<id>" / "line_<id>"); baked once into mask
    textures, the colours come from the skin (composed per skin, tinted at runtime)."""
    out = {}
    anchors = badge_anchors()

    def inrange(v, lo, hi):
        return k.math("MULTIPLY", k.math("GREATER_THAN", v, lo), k.math("LESS_THAN", v, hi))

    def side(cond):
        kind, val = cond
        if kind == "absx>":
            return k.math("GREATER_THAN", k.math("ABSOLUTE", x), val)
        return k.math("LESS_THAN", y, val) if kind == "y<" else k.math("GREATER_THAN", y, val)

    # each anchor plane is projected once (local 0..1 coordinates and its mask); the badges only offset into the atlas
    # (every node costs about 1 ms to create, so shared projections keep the material build short)
    planes = {}
    for anchor, entries in anchors.items():
        planes[anchor] = []
        for plane, (u0, u1, v0, v1), cond in entries:
            us = {"side": lambda: k.math("MULTIPLY", y, k.math("SIGN", x)), "front": lambda: x,
                  "back": lambda: k.math("MULTIPLY", x, -1.0)}[plane]()
            u = k.math("DIVIDE", k.math("SUBTRACT", us, u0), u1 - u0)
            v = k.math("DIVIDE", k.math("SUBTRACT", z, v0), v1 - v0)
            inside = k.math("MULTIPLY", k.math("MULTIPLY", inrange(u, 0.0, 1.0), inrange(v, 0.0, 1.0)), side(cond))
            planes[anchor].append((u, v, inside))
    c, size = make_textures.BADGE_CELL, float(SRC["badges"].size[0])
    for i, name in enumerate(make_textures.BADGE_ART):
        mask = None
        for anchor in BADGE_PLACES[name]:
            for u, v, inside in planes[anchor]:
                comb = k.node("ShaderNodeCombineXYZ")
                k.link(k.math("MULTIPLY_ADD", u, c / size, c=(i % 4) * c / size), comb.inputs[0])
                k.link(k.math("MULTIPLY_ADD", v, c / size, c=1.0 - ((i // 4) + 1) * c / size), comb.inputs[1])
                tex = k.node("ShaderNodeTexImage", extension="CLIP", interpolation="Cubic")
                tex.image = SRC["badges"]
                k.link(comb.outputs[0], tex.inputs["Vector"])
                m = k.math("MULTIPLY", tex.outputs["Alpha"], inside)
                mask = m if mask is None else k.math("MAXIMUM", mask, m)
        out["badge_" + name] = mask
    ax = k.math("ABSOLUTE", x)
    legx = k.math("ABSOLUTE", k.math("SUBTRACT", ax, LEG_X))
    # hazard stripes: diagonal bands (6 cm period) around the shins and the hips
    stripes = k.math("LESS_THAN", k.math("FRACT", k.math("DIVIDE", k.math("ADD", k.math("ADD", x, y), z), 0.06)), 0.5)
    region = k.math("MAXIMUM", k.math("MULTIPLY", k.math("LESS_THAN", legx, 0.10), inrange(z, 0.09, 0.235)),
                    k.math("MULTIPLY", k.math("LESS_THAN", ax, 0.21), inrange(z, 0.40, 0.545)))
    out["badge_HazardStripes"] = k.math("MULTIPLY", stripes, region)
    # racing stripes: a pair over head, chest and hips, one stripe down each leg
    upper = k.math("MULTIPLY", inrange(ax, 0.022, 0.052), k.math("GREATER_THAN", z, 0.40))
    legs = k.math("MULTIPLY", k.math("LESS_THAN", legx, 0.013), k.math("LESS_THAN", z, 0.40))
    out["badge_RacingStripes"] = k.math("MAXIMUM", upper, legs)
    # glow lines
    out["line_Circuit"] = k.map_range(box_tex(k, "circuit", vec, 1.6), 0.25, 0.75)
    out["line_Bolt"] = k.map_range(box_tex(k, "bolt", vec, 1.4), 0.25, 0.75)
    band = k.math("ABSOLUTE", k.math("SUBTRACT", k.math("FRACT", k.math("ADD", k.math("DIVIDE", z, 0.085), 0.5)), 0.5))
    out["line_Rings"] = k.map_range(k.math("MULTIPLY", band, 0.085), 0.0045, 0.0030)    # 7 mm rings every 8.5 cm
    geo = k.node("ShaderNodeNewGeometry")
    sep = k.node("ShaderNodeSeparateXYZ")
    k.link(geo.outputs["Normal"], sep.inputs[0])
    nmax = k.math("MAXIMUM", k.math("MAXIMUM", k.math("ABSOLUTE", sep.outputs[0]), k.math("ABSOLUTE", sep.outputs[1])),
                  k.math("ABSOLUTE", sep.outputs[2]))
    out["line_Seams"] = k.map_range(nmax, 0.80, 0.72)          # the middle of every rounded edge
    assert set(out) == {"badge_" + b for b in BADGES} | {"line_" + n for n in LINE_PATTERNS if n != "Joints"}
    return out


def build_materials():
    mats = {}
    atlas = make_textures.ATLAS

    # Armour: painted metal - grime, brushed streaks, scratches showing bare metal, painted decals.
    mat, k, b = new_material("White")
    vec, x, y, z, g, br, sc = surface(k, 1.6)
    base = k.mix(g, pal(k, "armor", 0.84), pal(k, "armor"))
    base = k.mix(k.math("MULTIPLY", br, 0.22), base, pal(k, "armor", 1.14))
    wear = k.math("MULTIPLY", sc, 0.55)
    base = k.mix(wear, base, (0.80, 0.81, 0.83))
    ax = k.math("ABSOLUTE", x)
    _, m_emblem = decal(k, y, z, (-0.066, 0.066, 0.780, 0.912), atlas["emblem"], k.math("GREATER_THAN", ax, 0.34))
    _, m_code = decal(k, k.math("MULTIPLY", x, -1.0), z, (-0.080, 0.080, 1.012, 1.052), atlas["code"],
                      k.math("GREATER_THAN", y, 0.15))
    sx, sy, sw, sh = atlas["stripes"]
    _, m_stripes = decal(k, k.math("MULTIPLY", x, -1.0), z, (-0.112, 0.112, 0.673, 0.701), (sx, sy, sw, sh // 2),
                         k.math("GREATER_THAN", y, 0.20))
    paint = k.math("MAXIMUM", k.math("MAXIMUM", m_emblem, m_code), m_stripes)
    paint = k.math("MULTIPLY", paint, k.math("SUBTRACT", 1.0, k.math("MULTIPLY", wear, 0.8)))
    base = k.mix(paint, base, pal(k, "mech", 1.4))
    rough = k.math("ADD", k.math("MULTIPLY", g, 0.14), 0.27)
    rough = k.math("SUBTRACT", rough, k.math("ADD", k.math("MULTIPLY", br, 0.05), k.math("MULTIPLY", wear, 0.10)))
    metal = k.math("ADD", 0.42, k.math("MULTIPLY", wear, 0.45))
    bump(k, b, k.math("SUBTRACT", k.math("MULTIPLY", g, 0.25), k.math("MULTIPLY", sc, 0.9)), 0.35)
    finish_material("White", mat, k, b, base, rough, metal, k.rgb((0, 0, 0)), viewport=(0.66, 0.68, 0.72))
    CHANNELS["White"].update(pattern_masks(k, vec, x, y, z))
    CHANNELS["White"].update(layer_fields(k, vec, g, sc))
    CHANNELS["White"].update(material_fields(k, vec, x, y, z))
    mats["White"] = mat

    # Anthracite mechanics.
    mat, k, b = new_material("Dark")
    vec, x, y, z, g, br, sc = surface(k, 2.2)
    base = k.mix(g, pal(k, "mech", 0.6), pal(k, "mech", 1.1))
    base = k.mix(k.math("MULTIPLY", sc, 0.35), base, pal(k, "metal"))
    rough = k.math("ADD", k.math("MULTIPLY", g, 0.16), 0.36)
    bump(k, b, k.math("SUBTRACT", k.math("MULTIPLY", g, 0.3), k.math("MULTIPLY", sc, 0.5)), 0.3)
    finish_material("Dark", mat, k, b, base, rough, k.value(0.70), k.rgb((0, 0, 0)), viewport=(0.06, 0.065, 0.075))
    CHANNELS["Dark"].update(layer_fields(k, vec, g, sc))
    mats["Dark"] = mat

    # Mid grey metal for hands, ear caps and small mechanics.
    mat, k, b = new_material("Grey")
    vec, x, y, z, g, br, sc = surface(k, 2.6)
    base = k.mix(g, pal(k, "metal", 0.78), pal(k, "metal"))
    base = k.mix(k.math("MULTIPLY", br, 0.25), base, pal(k, "metal", 1.2))
    rough = k.math("ADD", k.math("MULTIPLY", g, 0.10), 0.30)
    bump(k, b, k.math("SUBTRACT", k.math("MULTIPLY", g, 0.2), k.math("MULTIPLY", sc, 0.6)), 0.3)
    finish_material("Grey", mat, k, b, base, rough, k.value(0.60), k.rgb((0, 0, 0)), viewport=(0.34, 0.35, 0.38))
    CHANNELS["Grey"].update(layer_fields(k, vec, g, sc))
    mats["Grey"] = mat

    # Glossy black face visor.
    mat, k, b = new_material("Visor")
    finish_material("Visor", mat, k, b, k.rgb((0.006, 0.007, 0.010)), k.value(VISOR_ROUGHNESS), k.value(VISOR_METALLIC),
                    k.rgb((0, 0, 0)), viewport=(0.01, 0.01, 0.012))
    mats["Visor"] = mat

    # Status lights.
    mat, k, b = new_material("Orange")
    accent = pal(k, "accent")
    finish_material("Orange", mat, k, b, accent, k.value(0.25), k.value(0.0), accent, emit_strength=5.0,
                    viewport=(1.0, 0.45, 0.1))
    mats["Orange"] = mat

    # Fabric (accessories): baked light grey with weave grime, tinted per look by the fabric colour.
    mat, k, b = new_material("Fabric")
    vec, x, y, z, g, br, sc = surface(k, 3.0)
    base = k.mix(g, (0.62, 0.62, 0.62), (0.80, 0.80, 0.80))
    finish_material("Fabric", mat, k, b, base, k.value(0.75), k.value(0.0), k.rgb((0, 0, 0)), viewport=(0.8, 0.1, 0.1))
    mats["Fabric"] = mat

    mats["EyeScreen"] = build_eye_material()
    mats["Display"] = build_display_material()

    for key in BODY_MATS:
        kit = NodeKit(mats[key])
        m = MASKS[key]
        CHANNELS[key]["mask"] = kit.rgb(m[:3])
        CHANNELS[key]["mask_a"] = kit.value(m[3])
        CHANNELS[key]["zero"] = kit.value(0.0)
        zone = kit.node("ShaderNodeAttribute", attribute_type="GEOMETRY", attribute_name="paint_zone").outputs["Fac"]
        for i, z in enumerate(PAINT_ZONES):
            CHANNELS[key]["zone_" + z] = kit.math("LESS_THAN", kit.math("ABSOLUTE", kit.math("SUBTRACT", zone, i + 1)),
                                                  0.5)
        glow = kit.node("ShaderNodeAttribute", attribute_type="GEOMETRY", attribute_name="glow_part").outputs["Fac"]
        CHANNELS[key]["line_Joints"] = kit.math("GREATER_THAN", glow, 0.5)
        for ch in MASK_CHANNELS:            # badges and the other glow lines only on the armour
            CHANNELS[key].setdefault(ch, CHANNELS[key]["zero"])
    return mats


MASK_CHANNELS = (["badge_" + b for b in BADGES] + ["line_" + n for n in LINE_PATTERNS] +
                 ["layer_" + n for n in LAYERS] + ["mat_" + n for n in MATERIALS])


EXPRESSION_INDEX = {n: i for i, n in enumerate(make_textures.EXPRESSIONS)}
DISPLAY_STILLS = {"Heartbeat": "Heart", "Spinner": "Loading", "Talk": "Wave"}   # animated motif -> still for renders
DISPLAY_INDEX = {n: i for i, n in enumerate(make_textures.DISPLAYS)}


def atlas_cell(kind, index):
    """(scale_u, scale_v, offset_u, offset_v) of an atlas cell in Blender/Unity UV space (origin bottom-left)."""
    if kind == "eyes":
        cols, rows = make_textures.EYE_GRID
        cw, ch, H = 1.0 / cols, 1.0 / rows, 1.0
        col, row = index % cols, index // cols
        return cw, ch, col * cw, 1.0 - (row + 1) * ch
    cols, rows = make_textures.DISPLAY_GRID
    cw, ch = 1.0 / cols, make_textures.DISPLAY_CELL[1] / float(make_textures.DISPLAY_ATLAS_H)
    col, row = index % cols, index // cols
    return cw, ch, col * cw, 1.0 - (row + 1) * ch


def flipbook_cell(row, frame):
    """(scale_u, scale_v, offset_u, offset_v) of a flipbook frame; frame f of a motif = offset_u + f * scale_u."""
    (gx, _), (cw, ch), (w, h) = make_textures.FLIPBOOK_GRID, make_textures.FLIPBOOK_CELL, make_textures.FLIPBOOK_SIZE
    su, sv = cw / float(w), ch / float(h)
    return su, sv, frame * su, 1.0 - (row + 1) * sv


def _atlas_uv(nt, kind, index):
    su, sv, ou, ov = atlas_cell(kind, index)
    uv = nt.nodes.new("ShaderNodeUVMap")
    uv.uv_map = "UVMap"
    mp = nt.nodes.new("ShaderNodeMapping")
    mp.name = "AtlasCell"
    mp.inputs["Scale"].default_value = (su, sv, 1.0)
    mp.inputs["Location"].default_value = (ou, ov, 0.0)
    nt.links.new(uv.outputs[0], mp.inputs["Vector"])
    return mp


def set_atlas_cell(mat, kind, index):
    su, sv, ou, ov = atlas_cell(kind, index)
    mp = mat.node_tree.nodes["AtlasCell"]
    mp.inputs["Scale"].default_value = (su, sv, 1.0)
    mp.inputs["Location"].default_value = (ou, ov, 0.0)


def build_eye_material(name="M_RobotEyes", color=(0.02, 0.62, 0.95), expression="Normal"):
    """Additive emissive eye screen: atlas luminance x eye colour, bright cores turn white."""
    mat = bpy.data.materials.new(name)
    if mat.node_tree is None:
        mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    mp = _atlas_uv(nt, "eyes", EXPRESSION_INDEX[expression])
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = SRC["eyes"]
    tex.extension = "CLIP"
    nt.links.new(mp.outputs[0], tex.inputs["Vector"])
    col = nt.nodes.new("ShaderNodeRGB")
    col.name = "EyeColor"
    col.outputs[0].default_value = (*color, 1.0)
    mul = nt.nodes.new("ShaderNodeMix")
    mul.data_type, mul.blend_type = "RGBA", "MULTIPLY"
    mul.inputs[0].default_value = 1.0
    nt.links.new(tex.outputs["Color"], mul.inputs[6])
    nt.links.new(col.outputs[0], mul.inputs[7])
    core = nt.nodes.new("ShaderNodeMath")
    core.operation = "POWER"
    core.inputs[1].default_value = 6.0
    nt.links.new(tex.outputs["Color"], core.inputs[0])
    add = nt.nodes.new("ShaderNodeMix")
    add.data_type, add.blend_type = "RGBA", "ADD"
    nt.links.new(core.outputs[0], add.inputs[0])
    nt.links.new(mul.outputs[2], add.inputs[6])
    add.inputs[7].default_value = (0.8, 0.8, 0.8, 1.0)
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Strength"].default_value = 4.0
    nt.links.new(add.outputs[2], em.inputs["Color"])
    tr = nt.nodes.new("ShaderNodeBsdfTransparent")
    addsh = nt.nodes.new("ShaderNodeAddShader")
    nt.links.new(tr.outputs[0], addsh.inputs[0])
    nt.links.new(em.outputs[0], addsh.inputs[1])
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.name = "Output"
    nt.links.new(addsh.outputs[0], out.inputs["Surface"])
    try:
        mat.surface_render_method = "BLENDED"
    except (AttributeError, TypeError):
        pass
    mat.use_fake_user = True
    mat.diffuse_color = (*color, 1.0)
    return mat


def build_display_material(name="M_RobotDisplay", color=(0.30, 1.00, 0.62), motif="Status"):
    mat = bpy.data.materials.new(name)
    if mat.node_tree is None:
        mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    mp = _atlas_uv(nt, "displays", DISPLAY_INDEX[motif])
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = SRC["displays"]
    tex.extension = "CLIP"
    nt.links.new(mp.outputs[0], tex.inputs["Vector"])
    col = nt.nodes.new("ShaderNodeRGB")
    col.name = "DisplayColor"
    col.outputs[0].default_value = (*color, 1.0)
    mix = nt.nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    nt.links.new(tex.outputs["Color"], mix.inputs[0])
    mix.inputs[6].default_value = (0.01, 0.025, 0.02, 1.0)
    nt.links.new(col.outputs[0], mix.inputs[7])
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.name = "BSDF"
    bsdf.inputs["Base Color"].default_value = (0.01, 0.02, 0.015, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.15
    nt.links.new(mix.outputs[2], bsdf.inputs["Emission Color"])
    bsdf.inputs["Emission Strength"].default_value = 3.0
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.name = "Output"
    nt.links.new(bsdf.outputs[0], out.inputs["Surface"])
    mat.use_fake_user = True
    return mat


__all__ = [_n for _n in dir() if not _n.startswith("__")]
