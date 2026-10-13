"""robot_config.json, viewer config, player looks and the report."""

from robogen.pipeline import *  # noqa: F401,F403 - one shared namespace, see robogen/__init__.py


def viewer_config():
    return {
        "slots": [{"id": sl, "label": SLOT_LABELS.get(sl, sl), "variants": list(vs), "accessory": sl in ACCESSORY_SLOTS}
                  for sl, vs in SLOTS.items()],
        "robotTypes": [dict(name=n, **spec) for n, spec in ROBOT_TYPES.items()],
        "skins": [skin_config(n) for n in SKINS],
        "finishes": [{"id": n, "roughness": r, "metalOffset": m} for n, (r, m) in FINISH_PARAMS.items()],
        "badges": [{"id": b, "texture": BADGE_TEXTURES[i // 4], "channel": i % 4} for i, b in enumerate(BADGES)],
        "linePatterns": [{"id": n, "texture": LINE_TEXTURES[i // 4], "channel": i % 4}
                         for i, n in enumerate(LINE_PATTERNS)],
        "zones": [{"id": z, "texture": ZONE_TEXTURES[i // 4], "channel": i % 4, "parts": list(parts)}
                  for i, (z, parts) in enumerate(PAINT_ZONES.items())],
        "glowParts": list(GLOW_PARTS),
        # particle emitters of accessories; position/direction in character space (Unity local: right, up, forward)
        "emitters": [dict(e, position=char_space(Vector(e["position"])), direction=char_space(Vector(e["direction"])))
                     for e in EMITTERS],
        "springBones": list(SPRINGS),
        "unityTextures": [{"file": "Unity/" + n, "channels": d} for n, d in UNITY_TEXTURES.items()],
        "specials": [dict(id=n, **{k: (list(parse_color(v)) if k.startswith("color") else v) for k, v in d.items()})
                     for n, d in SPECIALS.items()],
        "forms": [dict(id=n, **d) for n, d in FORMS.items()],
        "perforation": dict(PERFORATION),
        "linePaint": LINE_PAINT,
        "layers": [catalog_entry(n, LAYER_TEXTURES, i, LAYER_DEFAULTS.get(n)) for i, n in enumerate(LAYERS)],
        "materials": [catalog_entry(n, MATERIAL_TEXTURES, i, MATERIAL_DEFAULTS[n]) for i, n in enumerate(MATERIALS)],
        "weathering": {"coverSoft": COVER_SOFT, "wearDefault": WEAR_DEFAULT, "bareMetal": [float(v) for v in BARE_METAL],
                       "equalised": EQUALISED_LAYERS},
        "expressions": list(make_textures.EXPRESSIONS),
        "expressionStyles": make_textures.EXPRESSION_STYLES,
        "blink": make_textures.BLINK,
        "displays": list(make_textures.DISPLAYS),
        "eyeGrid": list(make_textures.EYE_GRID),
        "displayGrid": list(make_textures.DISPLAY_GRID),
        "displayCellHeight": make_textures.DISPLAY_CELL[1] / float(make_textures.DISPLAY_ATLAS_H),
        "displayAnimations": [{"name": n, "row": i, "frames": f, "fps": fps}
                              for i, (n, f, fps) in enumerate(make_textures.DISPLAY_ANIMS)],
        "flipbook": {"texture": "T_Robot_DisplayFlipbook.png", "grid": list(make_textures.FLIPBOOK_GRID),
                     "cell": [make_textures.FLIPBOOK_CELL[0] / float(make_textures.FLIPBOOK_SIZE[0]),
                              make_textures.FLIPBOOK_CELL[1] / float(make_textures.FLIPBOOK_SIZE[1])]},
        "spinParts": sorted(SPIN_PARTS),
        "lookVersion": LOOK_VERSION,
        "groupLabels": GROUP_LABELS,
        "clipCategories": [{"id": c, "label": label, "clips": [n for n in names if n in {a.name for a in ANIMS}]}
                           for c, (label, names) in CLIP_CATEGORIES.items()],
    }


def catalog_entry(name, textures, i, defaults):
    """A layer or material for robot_config.json: mask channel plus default colour (linear) and surface."""
    e = {"id": name, "texture": textures[i // 4], "channel": i % 4}
    if defaults:
        e.update(color=list(parse_color(defaults["color"])), roughness=defaults["roughness"],
                 metalOffset=defaults["metalOffset"])
        if "base" in defaults:
            e["base"] = list(parse_color(defaults["base"]))
    return e


def skin_config(sid):
    """A skin for robot_config.json: palette as linear lists (flat, as before 1.10) plus the 1.10 fields."""
    rec = SKIN_DATA[sid]
    cfg = dict(name=sid, **{k: list(v) for k, v in rec["palette"].items()})
    cfg.update({"zones2": rec["zones2"], "finish": rec["finish"], "wear": rec["wear"],
                "material": dict(rec["material"], color=list(rec["material"]["color"])) if rec["material"] else None,
                "layers": [dict(ly, color=list(ly["color"])) for ly in rec["layers"]],
                "badges": [{"id": b["id"], "color": list(b["color"])} for b in rec["badges"]],
                "lines": dict(rec["lines"], color=list(rec["lines"]["color"])) if rec["lines"] else None,
                "special": (dict(rec["special"], color=list(rec["special"]["color"]),
                                 color2=list(rec["special"]["color2"])) if rec["special"] else None),
                "form": rec["form"] or "None",
                "parts": rec["parts"], "theme": rec["theme"], "since": rec["since"], "stage": rec["stage"],
                "startSet": rec["startSet"], "concept": rec["concept"]})
    for k in ("expression", "display"):
        if rec[k]:
            cfg[k] = rec[k]
    return cfg


def validate_skins():
    """Checks of the skin data that need the model (slots, expressions, displays); run once at the start."""
    displays = set(make_textures.DISPLAYS) | {n for n, _, _ in make_textures.DISPLAY_ANIMS}
    for sid, rec in SKIN_DATA.items():
        for slot, var in rec["parts"].items():
            if slot not in SLOTS or var not in SLOTS[slot]:
                raise ValueError("skin %s: unknown part %s=%s" % (sid, slot, var))
        if rec["expression"] and rec["expression"] not in make_textures.EXPRESSIONS:
            raise ValueError("skin %s: unknown expression %s" % (sid, rec["expression"]))
        if rec["display"] and rec["display"] not in displays:
            raise ValueError("skin %s: unknown display %s" % (sid, rec["display"]))
    for tname, spec in ROBOT_TYPES.items():
        if spec["skin"] not in SKIN_DATA:
            raise ValueError("robot type %s: unknown skin %s" % (tname, spec["skin"]))


# Hit capsules (1.13): one capsule per body bone, measured on the default mesh in the rest pose. Game: convert the
# points into bone space once (bind pose) and let the bone carry them; also the shape for a later ragdoll.
CAPSULE_BONES = ("Pelvis", "Spine", "Head", "UpperArm_L", "LowerArm_L", "Hand_L", "UpperArm_R", "LowerArm_R", "Hand_R",
                 "UpperLeg_L", "LowerLeg_L", "Foot_L", "UpperLeg_R", "LowerLeg_R", "Foot_R")


def hit_capsules(ob, arm_ob):
    """[{bone, a, b, radius, coverage}] in character space; radius = 93rd percentile of the distance to the bone
    axis, ends 0.3 r inside the 2nd/98th percentile along it; coverage = share of the bone's vertices inside (+5 mm)."""
    me = ob.data
    names = {vg.index: vg.name for vg in ob.vertex_groups}
    co = np.empty(len(me.vertices) * 3, dtype=np.float64)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    owner = np.array([names[v.groups[0].group] if v.groups else "" for v in me.vertices])
    out = []
    for bname in CAPSULE_BONES:
        pts = co[owner == bname]
        if len(pts) < 8:
            continue
        b = arm_ob.data.bones[bname]
        head, axis = np.array(b.head_local), np.array((b.tail_local - b.head_local).normalized())
        t = (pts - head) @ axis
        radial = np.linalg.norm((pts - head) - np.outer(t, axis), axis=1)
        r = float(np.percentile(radial, 93))
        t0, t1 = float(np.percentile(t, 2)), float(np.percentile(t, 98))
        mid = (t0 + t1) / 2
        t0, t1 = min(mid, t0 + 0.3 * r), max(mid, t1 - 0.3 * r)      # boxy parts: corners sit at the ends
        a, bb = head + axis * t0, head + axis * t1
        seg = bb - a
        u = np.clip(((pts - a) @ seg) / max(float(seg @ seg), 1e-9), 0, 1)
        dist = np.linalg.norm(pts - (a + np.outer(u, seg)), axis=1)
        out.append({"bone": bname, "a": char_space(Vector(a)), "b": char_space(Vector(bb)), "radius": round(r, 4),
                    "coverage": round(float((dist <= r + 0.005).mean()), 3)})
    return out


def write_config(out, clips, bounds, capsules=None):
    """robot_config.json: slots, variants, robot types, skins, atlas layout, clip data - for Unity and the viewer."""
    cfg = viewer_config()
    cfg["generator"] = VERSION
    cfg["coordinates"] = "character space [right, up, forward] in m (= Unity local space), angles in degrees"
    cfg["atlasCells"] = {
        "eyes": {n: atlas_cell("eyes", i) for i, n in enumerate(make_textures.EXPRESSIONS)},
        "displays": {n: atlas_cell("displays", i) for i, n in enumerate(make_textures.DISPLAYS)},
        "displayAnimations": {n: flipbook_cell(i, 0) for i, (n, _, _) in enumerate(make_textures.DISPLAY_ANIMS)},
    }
    cfg["typeBounds"] = bounds
    # 1.16: eye height for the first-person camera in the game (Eyes bone in the rest pose, character space, m)
    cfg["eyeHeight"] = round(J["eyes"].z, 3)
    cfg["lookFormat"] = {"format": LOOK_FORMAT, "version": LOOK_VERSION, "schema": "robot_look.schema.json"}
    cfg["clips"] = clips
    cfg["hitCapsules"] = capsules or []
    with open(os.path.join(out, "robot_config.json"), "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)


# ---------------------------------- player looks ----------------------------------
# A look is what the player saves. Names of skins, variants, expressions and displays are IDs: never rename them.
LOOK_FORMAT = "CubeEscapeRobotLook"
LOOK_VERSION = 6            # 1 = viewer 1.6 (no format/version), 2 = 1.7-1.9, 3 = 1.10, 4 = 1.11, 5 = 1.13 (migrated)
LOOK_COLORS = {"armor": "armor", "mech": "mech", "metal": "metal", "lights": "accent", "screen": "screen",
               "eyes": "eye", "visor": "visor", "fabric": "fabric", "armor2": "armor2"}     # look key -> skin key


def type_look(tname):
    spec = ROBOT_TYPES[tname]
    variants = default_config()
    variants.update(spec["variants"])
    skin = SKINS[spec["skin"]]
    look = {"format": LOOK_FORMAT, "version": LOOK_VERSION, "name": tname, "variants": variants}
    for k, sk in LOOK_COLORS.items():
        r, g, b = skin[sk]
        look[k] = {"r": round(r, 4), "g": round(g, 4), "b": round(b, 4)}
    rec = SKIN_DATA[spec["skin"]]
    rgb = lambda c: {"r": round(c[0], 4), "g": round(c[1], 4), "b": round(c[2], 4)}   # noqa: E731
    lines = rec["lines"] or {"pattern": "None", "color": skin["accent"], "strength": 1.0}
    look.update({"skin": spec["skin"], "finish": rec["finish"], "glow": 1.0, "expression": spec["expression"],
                 "display": spec["display"], "autoExpression": True, "autoDisplay": True,
                 "zones2": list(rec["zones2"]),
                 "badges": [{"id": b["id"], "color": rgb(b["color"])} for b in rec["badges"]],
                 "lines": {"pattern": lines["pattern"], "color": rgb(lines["color"]), "strength": lines["strength"]},
                 "material": ({"id": rec["material"]["id"], "color": rgb(rec["material"]["color"])} if rec["material"]
                              else {"id": "None", "color": rgb(skin["armor"])}),
                 "layers": [{"id": ly["id"], "amount": ly["amount"], "color": rgb(ly["color"])} for ly in rec["layers"]],
                 "wear": rec["wear"], "special": special_look(rec["special"]), "form": rec["form"] or "None"})
    return look


def special_look(sp):
    """Look field "special" (version 5); {"id": "None"} = no special material."""
    if not sp:
        return {"id": "None"}
    rgb = lambda c: {"r": round(c[0], 4), "g": round(c[1], 4), "b": round(c[2], 4)}   # noqa: E731
    return {"id": sp["id"], "color": rgb(sp["color"]), "color2": rgb(sp["color2"]),
            "strength": round(sp["strength"], 3), "speed": round(sp["speed"], 3)}


def look_schema():
    rgb = {"type": "object", "required": ["r", "g", "b"], "additionalProperties": False,
           "properties": {c: {"type": "number", "minimum": 0, "maximum": 1} for c in "rgb"}}
    variant_props = {sl: {"enum": list(vs)} for sl, vs in SLOTS.items()}
    props = {
        "format": {"const": LOOK_FORMAT},
        "version": {"type": "integer", "minimum": 1, "maximum": LOOK_VERSION},
        "name": {"type": "string", "maxLength": 40},
        "variants": {"type": "object", "properties": variant_props, "additionalProperties": False},
        "finish": {"enum": FINISHES},
        "glow": {"type": "number", "minimum": 0.2, "maximum": 2.5},
        "expression": {"enum": list(make_textures.EXPRESSIONS)},
        "display": {"enum": list(make_textures.DISPLAYS) + [n for n, _, _ in make_textures.DISPLAY_ANIMS]},
        "autoExpression": {"type": "boolean"},
        "autoDisplay": {"type": "boolean"},
        "skin": {"enum": list(SKINS) + ["Custom"], "description": "skin the look started from (version 3)"},
        "badges": {"type": "array", "maxItems": MAX_BADGES, "items": {
            "type": "object", "required": ["id", "color"], "additionalProperties": False,
            "properties": {"id": {"enum": BADGES}, "color": rgb}}},
        "zones2": {"type": "array", "uniqueItems": True, "items": {"enum": list(PAINT_ZONES)},
                   "description": "parts painted in armor2 (version 3)"},
        "material": {"type": "object", "required": ["id"], "additionalProperties": False,
                     "description": "armour material (version 4)",
                     "properties": {"id": {"enum": ["None"] + MATERIALS}, "color": rgb}},
        "layers": {"type": "array", "description": "surface layers (version 4)", "items": {
            "type": "object", "required": ["id", "amount"], "additionalProperties": False,
            "properties": {"id": {"enum": list(LAYER_DEFAULTS)}, "amount": {"type": "number", "minimum": 0, "maximum": 1},
                           "color": rgb}}},
        "wear": {"type": "number", "minimum": 0, "maximum": 1,
                 "description": "0 clean, 0.5 as baked, 1 battered (version 4)"},
        "special": {"type": "object", "required": ["id"], "additionalProperties": False,
                    "description": "special material (version 5): runtime effect on top of the skin",
                    "properties": {"id": {"enum": ["None"] + list(SPECIALS)}, "color": rgb, "color2": rgb,
                                   "strength": {"type": "number", "minimum": 0, "maximum": 1},
                                   "speed": {"type": "number", "minimum": 0, "maximum": 4}}},
        "form": {"enum": ["None"] + list(FORMS), "description": "body form (version 6): own geometry, holes or "
                                                                "extra parts; follows the skin"},
        "lines": {"type": "object", "required": ["pattern"], "additionalProperties": False, "properties": {
            "pattern": {"enum": ["None"] + LINE_PATTERNS}, "color": rgb,
            "strength": {"type": "number", "minimum": 0, "maximum": 2}}},
    }
    props.update({k: rgb for k in LOOK_COLORS})
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": "robot_look.schema.json",
            "title": "Cube Escape robot look (version %d)" % LOOK_VERSION,
            "description": "Player customisation. Names are permanent IDs; unknown values fall back to the default "
                           "with a warning. Versions 1-5 are migrated on load (missing fields take their defaults).",
            "type": "object", "required": ["format", "version", "variants", "expression", "display"],
            "properties": props, "additionalProperties": False}


def write_looks(out):
    look_dir = os.path.join(out, "Looks")
    os.makedirs(look_dir, exist_ok=True)
    for tname in ROBOT_TYPES:
        with open(os.path.join(look_dir, "%s.robotlook.json" % tname), "w", encoding="utf-8") as fh:
            json.dump(type_look(tname), fh, indent=2, sort_keys=True)
    with open(os.path.join(out, "robot_look.schema.json"), "w", encoding="utf-8") as fh:
        json.dump(look_schema(), fh, indent=2)


def write_report(out, report):
    path = os.path.join(out, "logs", "robot_report.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, default=str)
    LOG.info("report: %s", path)


__all__ = [_n for _n in dir() if not _n.startswith("__")]
