"""Robot layout: joints, proportions (reshape), slots, variants and robot types."""

from robogen.core import *  # noqa: F401,F403 - one shared namespace, see robogen/__init__.py


# Eye layout (also used by the eye shaders)
EYE_X, EYE_Z = 0.088, 1.117
PARTS = []  # dicts: name, bone, mat, bm (filled by add_part in robogen.parts)

# ----------------------------------------------------------------------------------------------
# Robot layout (meters). All joint positions live here so geometry and rig agree.
# ----------------------------------------------------------------------------------------------

J = {}  # joint name -> Vector
LEG_X = 0.120
SHOULDER_X = 0.282


def define_joints():
    J["root"] = Vector((0, 0, 0))
    J["pelvis"] = Vector((0, 0, 0.470))
    J["spine"] = Vector((0, 0, 0.550))
    J["neck"] = Vector((0, 0, 0.900))
    J["head_top"] = Vector((0, 0, 1.300))
    J["eyes"] = Vector((0, -0.215, EYE_Z))
    for s, side in ((1, "L"), (-1, "R")):
        J["hip_" + side] = Vector((s * LEG_X, 0, 0.405))
        J["knee_" + side] = Vector((s * LEG_X, 0, 0.245))
        J["ankle_" + side] = Vector((s * LEG_X, 0, 0.095))
        J["toe_" + side] = Vector((s * LEG_X, -0.165, 0.030))
        J["clav_" + side] = Vector((s * 0.070, 0, 0.830))
        sh = Vector((s * SHOULDER_X, 0, 0.830))
        d1 = Vector((s * math.sin(math.radians(8)), 0, -math.cos(math.radians(8)))).normalized()
        el = sh + d1 * 0.165
        d2 = Vector((s * math.sin(math.radians(5)), -math.sin(math.radians(10)), -1.0)).normalized()
        wr = el + d2 * 0.165
        J["shoulder_" + side] = sh
        J["elbow_" + side] = el
        J["wrist_" + side] = wr
        J["dir_upper_" + side] = d1
        J["dir_fore_" + side] = d2
        J["knuckle_" + side] = wr + d2 * 0.090


# ------------------------------- 1.14 Reshape: base proportions -------------------------------
# The parts are modelled at the 1.13 measures and then reshaped per bone towards the concept art: stocky, a bigger head,
# chunky plates. Limb joints stay where they are, so every clip, IK target and ground contact keeps working; what hangs
# on the head and the torso (eyes, sockets, spring bones, emitters) moves with the reshape.
#   axes: per-axis scale about a pivot | radial: thicker around the bone line
# Pivots name a joint ("ankle" + side) or are points; "axes" with a joint pivot keeps the given z (feet stay on y = 0).
PROPORTIONS = {
    "Head": ("axes", (1.14, 1.06, 1.24), (0.0, 0.0, 0.935)),        # mostly taller: raised arms and pads pass its sides
    "Spine": ("axes", (1.05, 1.0, 1.0), (0.0, 0.010, 0.730)),       # deeper chests meet the forearms (Jump, EdgeBalance)
    "Pelvis": ("axes", (1.05, 1.0, 1.0), (0.0, 0.005, 0.470)),      # deeper hips meet the shins when crouching
    "UpperArm": ("radial", 1.16, ("shoulder", "dir_upper")),
    "LowerArm": ("radial", 1.20, ("elbow", "dir_fore")),
    "Hand": ("radial", 1.24, ("wrist", "dir_fore")),
    "Thumb": ("radial", 1.10, ("wrist", "dir_fore")),             # sticks out of the fist: meets pads (ClimbUp)
    "UpperLeg": ("axes", (1.18, 1.0, 1.0), ("hip", 0.0)),           # wider only: heels reach the thighs in Run
    "LowerLeg": ("axes", (1.16, 1.0, 1.0), ("knee", 0.0)),
    "Foot": ("axes", (1.05, 1.0, 1.0), ("ankle", 0.0)),           # wide Mech feet meet in turns
}
# variants with their own shape on a reshaped bone (group -> spec, None = keep the 1.13 shape): the pads are chunky
# already and rise beside the head and the thumbs (ClimbUp); round feet would meet the thighs in CrouchWalk
SHAPE_VARIANTS = {"Pads_Round": None, "Pads_Slim": None, "Pads_Square": None, "Feet_Round": None,
                  # the wide box head keeps its width (Sleep, Death: it rests on the square pads), grows in height
                  "Head_Boxy": ("axes", (1.04, 1.06, 1.24), (0.0, 0.0, 0.935))}
# bones that take the shape of another bone (sockets, spring bones, fingers); None = keep the 1.13 shape
SHAPE_OF = {"Eyes": "Head", "Socket_Head": "Head", "Antenna": "Head", "Socket_Neck": "Spine", "Socket_Back": "Spine",
            "Cape": "Spine", "Fingers": "Hand"}
_SHAPES = {}


def shape_matrix(bone, group=None):
    """4x4 reshape matrix of a bone (identity for bones and variants that keep their 1.13 shape)."""
    key = (bone, group if group in SHAPE_VARIANTS else None)
    if key in _SHAPES:
        return _SHAPES[key]
    base, side = bone, ""
    if bone[-2:] in ("_L", "_R"):
        base, side = bone[:-2], bone[-1]
    spec = SHAPE_VARIANTS[group] if key[1] else PROPORTIONS.get(SHAPE_OF.get(base, base))
    m = Matrix.Identity(4)
    if spec:
        kind, k, piv = spec
        if kind == "radial":
            a = J["%s_%s" % (piv[0], side)]
            d = (J["%s_%s" % (piv[1], side)] if piv[1] else Vector((0, 0, 1))).normalized()
            dd = Matrix([[d[i] * d[j] for j in range(3)] for i in range(3)])
            s3 = Matrix.Identity(3) * k + dd * (1 - k)
        else:
            if isinstance(piv[0], str):
                j = J["%s_%s" % (piv[0], side)]
                a = Vector((j.x, j.y, piv[1]))
            else:
                a = Vector(piv)
            s3 = Matrix.Diagonal(k)
        m = Matrix.Translation(a) @ s3.to_4x4() @ Matrix.Translation(-a)
    _SHAPES[key] = m
    return m


def shaped(bone, co):
    return (shape_matrix(bone) @ Vector(co).to_4d()).to_3d()


def apply_proportions():
    """Reshapes every part with the matrix of its bone and moves what rides on the head and torso along."""
    _SHAPES.clear()
    ident = Matrix.Identity(4)
    for p in PARTS:
        m = shape_matrix(p["bone"], p["group"])
        if m != ident:
            bmesh.ops.transform(p["bm"], matrix=m, verts=p["bm"].verts[:])
    for e in EMITTERS:
        e["position"] = tuple(shaped(e["bone"], e["position"]))
    J["head_top"] = shaped("Head", J["head_top"])
    J["eyes"] = shaped("Head", J["eyes"])


def foot_deform(co):
    """Toe slope: lowers the top surface towards the toe tip (local foot coordinates)."""
    if co.z > 0.0:
        t = max(0.0, min(1.0, (-0.060 - co.y) / 0.13))
        t = t * t * (3 - 2 * t)
        co.z -= (co.z + 0.04) * 0.32 * t
    return co


# ------------------------------- modular parts: slots, variants, accessories -------------------------------

VARIANT = [None, None]          # (slot, variant) the parts being built belong to; None = core body
SLOTS = collections.OrderedDict()   # slot -> [variants]; the first variant is the default
SLOT_LABELS = {"Head": "Kopf", "Hands": "Hände", "Feet": "Füße", "Pads": "Schulterpanzer", "Backpack": "Rucksack",
               "AccHead": "Kopf-Accessoire", "AccFace": "Gesicht", "AccNeck": "Hals", "AccBack": "Rücken",
               "AccHand": "In der Hand", "AccEar": "Ohren"}
ACCESSORY_SLOTS = ("AccHead", "AccFace", "AccNeck", "AccBack", "AccHand", "AccEar")
SPIN_PARTS = set()              # part names that spin in the viewer (propeller)
FLOATING_PARTS = {"Halo_Ring"}  # parts that float on purpose (rigid on their socket); skipped by the connectivity check
EMITTERS = []                   # particle emitters of accessories (smoke ...), exported for the viewer and the game
for _slot in ACCESSORY_SLOTS:
    SLOTS[_slot] = ["None"]

# Robot types: complete looks = variants + skin + eyes + display + accessories
ROBOT_TYPES = collections.OrderedDict([
    ("Classic", {"variants": {}, "skin": "Classic", "expression": "Normal", "display": "Status"}),
    ("Scout", {"variants": {"Head": "Dome", "Feet": "Round", "AccHead": "Antenna"}, "skin": "Arctic",
               "expression": "Focused", "display": "Wave"}),
    ("Worker", {"variants": {"Head": "Boxy", "Hands": "Claw", "Feet": "Mech", "Pads": "Square",
                             "Backpack": "Battery"}, "skin": "Sunrise", "expression": "Normal", "display": "Battery"}),
    ("Retro", {"variants": {"Head": "TV", "Pads": "Square"}, "skin": "Copper", "expression": "Pixel",
               "display": "Hello"}),
    ("Heavy", {"variants": {"Head": "Boxy", "Hands": "Claw", "Feet": "Mech", "Pads": "Square", "Backpack": "Battery",
                            "AccBack": "Jetpack"}, "skin": "Midnight", "expression": "Angry", "display": "Status"}),
    ("Party", {"variants": {"Feet": "Round", "AccHead": "PropellerCap", "AccNeck": "BowTie"}, "skin": "Mint",
               "expression": "Happy", "display": "Music"}),
    ("Agent", {"variants": {"Head": "Dome", "AccFace": "Shades", "AccNeck": "Scarf"}, "skin": "Neon",
               "expression": "Focused", "display": "Cube"}),
    ("Gentleman", {"variants": {"AccHead": "TopHat", "AccFace": "Monocle", "AccNeck": "BowTie"}, "skin": "Noir",
                   "expression": "Skeptical", "display": "Clock", "emote": "HatTip"}),
    ("Punk", {"variants": {"Pads": "Square", "AccHead": "Mohawk", "AccFace": "Shades", "AccNeck": "Bell"},
              "skin": "Toxic", "expression": "Determined", "display": "Lightning"}),
    ("King", {"variants": {"Head": "Dome", "AccHead": "Crown", "AccNeck": "Medal", "AccBack": "Cape",
                           "AccHand": "Flag"}, "skin": "Gold", "expression": "Happy", "display": "Star",
              "emote": "FlagWave"}),
    ("DJ", {"variants": {"Head": "Boxy", "Feet": "Round", "AccHead": "Headphones", "AccBack": "Wings"},
            "skin": "Vapor", "expression": "Laugh", "display": "Music"}),
    ("Gardener", {"variants": {"AccFace": "Mustache", "AccHand": "Flower"}, "skin": "Forest", "expression": "Shy",
                  "display": "Heart"}),
    ("Mechanic", {"variants": {"Backpack": "Battery", "AccFace": "Goggles", "AccHand": "Wrench"}, "skin": "Sand",
                  "expression": "Focused", "display": "Warning"}),
    # narrowest silhouette: fits the game's character capsule (radius 0.3 m)
    ("Runner", {"variants": {"Head": "Dome", "Pads": "Slim", "Feet": "Round"}, "skin": "Ocean",
                "expression": "Determined", "display": "Lightning"}),
    # 1.12 Bolt-On: together these carry every attachment, so the QA of all types covers each one in every clip
    ("Miner", {"variants": {"Hands": "Claw", "Feet": "Mech", "AccHead": "MinerHelmet"}, "skin": "Miner",
               "expression": "Focused", "display": "Warning"}),
    ("Samurai", {"variants": {"Pads": "Square", "AccHead": "Kabuto"}, "skin": "Samurai", "expression": "Determined",
                 "display": "Lightning"}),
    ("Steam", {"variants": {"AccHead": "Chimney", "AccFace": "Goggles", "AccEar": "Dial"}, "skin": "Steam",
               "expression": "Normal", "display": "Clock"}),
    ("Signal", {"variants": {"AccHead": "Dish", "AccEar": "LensEye"}, "skin": "Signal", "expression": "Focused",
                "display": "Wave"}),
    ("Celest", {"variants": {"AccHead": "Halo", "AccBack": "Wings"}, "skin": "Celest", "expression": "Happy",
                "display": "Star"}),
    ("Sapling", {"variants": {"Feet": "Round", "AccHead": "Sprout"}, "skin": "Wood", "expression": "Happy",
                 "display": "Heart"}),
    ("Astronaut", {"variants": {"Feet": "Mech", "AccHead": "Antenna", "AccFace": "SpaceRing", "AccBack": "Pouches"},
                   "skin": "Astronaut", "expression": "Normal", "display": "Status"}),
    ("Clockwork", {"variants": {"AccFace": "LensRims", "AccEar": "Gears"}, "skin": "Clockwork",
                   "expression": "Skeptical", "display": "Clock"}),
    ("Vertex", {"variants": {"Pads": "Slim", "AccHead": "Ears"}, "skin": "Vertex", "expression": "Happy",
                "display": "Cube"}),
    ("Spike", {"variants": {"Pads": "Square", "Hands": "Claw", "AccHead": "Spikes"}, "skin": "Spike",
               "expression": "Angry", "display": "Warning"}),
])


class variant:
    """with variant("Head", "Dome"): parts built inside belong to that slot variant."""

    def __init__(self, slot, name):
        self.slot, self.name = slot, name

    def __enter__(self):
        SLOTS.setdefault(self.slot, [])
        if self.name not in SLOTS[self.slot]:
            SLOTS[self.slot].append(self.name)
        VARIANT[:] = [self.slot, self.name]
        return self

    def __exit__(self, *exc):
        VARIANT[:] = [None, None]
        return False


class form_parts:
    """with form_parts("Wired"): parts of a "parts" form (group Form_<id>; not a slot, the look's form shows them)."""

    def __init__(self, name):
        self.name = name

    def __enter__(self):
        VARIANT[:] = ["Form", self.name]
        return self

    def __exit__(self, *exc):
        VARIANT[:] = [None, None]
        return False


def group_key(slot, var):
    return "Body" if slot is None else "%s_%s" % (slot, var)


def default_config():
    return {slot: vs[0] for slot, vs in SLOTS.items()}


def config_groups(cfg):
    """Object groups visible for a configuration (slot -> variant)."""
    groups = ["Body"]
    for slot, var in cfg.items():
        key = group_key(slot, var)
        if any(p["group"] == key for p in PARTS):
            groups.append(key)
    return groups


__all__ = [_n for _n in dir() if not _n.startswith("__")]
