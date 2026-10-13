"""The animation clips, their categories and per-clip game data."""

from robogen.motion import *  # noqa: F401,F403 - one shared namespace, see robogen/__init__.py


# ---------------------------------- the ten animations ----------------------------------

STAND = P()
# gait: "feet" or "hands" (crawling) - which contacts give the natural speed, the contacts and the footsteps
Anim = collections.namedtuple("Anim", "name frames loop fn desc group unity gait")
ANIMS = []  # Anim records in registration order
GROUPS = ["Basis", "Phase1", "Phase2", "Phase3", "Phase4", "Phase5", "Phase6", "Phase7", "Phase8"]
GROUP_LABELS = {"Basis": "Basis", "Phase1": "Spielsysteme", "Phase2": "Werkzeuge & Interaktion", "Phase3": "Charakter",
                "Phase4": "Tänze", "Phase5": "Emotes", "Phase6": "Klettern & Hängen", "Phase7": "Tempo & Ducken",
                "Phase8": "Kriechen"}


# 1.16: categories by content for the viewer, ANIMATIONS.md and the game (the groups above are build phases and stay
# for contact sheets, QA packages and --groups). Every clip in exactly one category, in display order; IDs permanent.
CLIP_CATEGORIES = collections.OrderedDict([
    ("Move", ("Gehen & Laufen", ["Idle", "Walk", "WalkStart", "WalkStop", "Run", "RunFast", "Sprint", "TurnLeft",
                                 "TurnRight", "MudWalk", "SlopeSlide"])),
    ("Air", ("Springen & Fallen", ["Jump", "DoubleJump", "Fall", "Land"])),
    ("Crouch", ("Ducken & Kriechen", ["Crouch", "CrouchWalk", "CrouchDeep", "CrawlIdle", "Crawl"])),
    ("Climb", ("Klettern & Hängen", ["LedgeGrab", "HangIdle", "ShimmyLeft", "ShimmyRight", "HangSwing", "ClimbUp",
                                     "LadderUp", "LadderDown", "Vault"])),
    ("Interact", ("Interaktion & Werkzeuge", ["Interact", "PickUp", "Push", "PushStart", "PushStep", "ToolAim",
                                               "ToolUse", "Scan", "TerminalTyping", "PortalEnter"])),
    ("Damage", ("Treffer & Gefahren", ["Hit", "HitHeavy", "Electrocuted", "GasCough", "Death", "Respawn"])),
    ("Idle", ("Warten & Charakter", ["IdleScratch", "IdleFootTap", "IdleYawn", "Talk", "Listen", "EdgeBalance",
                                     "Celebrate"])),
    ("Emote", ("Gesten", ["Wave", "Bow", "Clap", "Think", "Point", "Shrug", "NodYes", "ShakeNo", "HatTip",
                          "FlagWave"])),
    ("Rest", ("Sitzen & Schlafen", ["SitDown", "SitIdle", "Sleep"])),
    ("Dance", ("Tänze", ["DanceBob", "DanceRobot", "DanceWave", "DanceShuffle", "DanceDisco", "DanceSpin",
                         "DanceMoonwalk", "DanceSway"])),
])


def clip_category(name):
    return next(c for c, (_, clips) in CLIP_CATEGORIES.items() if name in clips)


def check_categories():
    """Every registered clip in exactly one category, no category entry without a clip."""
    names = [a.name for a in ANIMS]
    listed = [c for _, clips in CLIP_CATEGORIES.values() for c in clips]
    missing = [n for n in names if n not in listed]
    extra = [c for c in listed if c not in names]
    twice = sorted({c for c in listed if listed.count(c) > 1})
    if missing or extra or twice:
        raise ValueError("CLIP_CATEGORIES: missing %s, unknown %s, twice %s" % (missing, extra, twice))


def anim(name, frames, loop, desc, group="Basis", unity="", gait="feet"):
    """Registers a clip. unity: import hint (e.g. 'Additive', 'Oberkörper-Maske', 'Root Motion')."""
    def deco(fn):
        ANIMS.append(Anim(name, frames, loop, fn, desc, group, unity, gait))
        return fn
    return deco


@anim("Idle", 120, True, "Atmen, neugierige Kopfbewegungen, Blinzeln")
def idle(f):
    t = f / 120.0
    br = math.sin(2 * math.pi * 2 * t)
    look = keyed(f, [
        (0, P(head=(0, 0, 0))),
        (14, P(head=(-4, 8, 22))),
        (42, P(head=(-3, 9, 24))),
        (58, P(head=(0, 0, 0))),
        (72, P(head=(6, -7, -16))),
        (98, P(head=(5, -8, -18))),
        (113, P(head=(0, 0, 0))),
        (120, P(head=(0, 0, 0))),
    ])["head"]
    yaw = look[2]
    eyes = (min(blink(f, 52), blink(f, 107)), 0.010 * max(-1, min(1, yaw / 20.0)), 0.0)
    return P(
        pelvis=(0.003 * math.sin(2 * math.pi * t), 0, -0.014 - 0.004 * (0.5 + 0.5 * br)),
        spine=(1.2 * br, 0, 1.5 * math.sin(2 * math.pi * t)),
        head=(look[0] - 0.8 * br, look[1], look[2]),
        shoulder_LR=(0, -1.5 * (0.5 + 0.5 * br), 0),
        uarm_L=arm(1.5 * br, 2 + 1.0 * br),
        uarm_R=arm(1.5 * br, 2 + 1.0 * br),
        larm_LR=arm(-6),
        fing_LR=curl(18 + 4 * math.sin(2 * math.pi * t)),
        eyes=eyes,
    )


WALK_FRAMES = 20
WALK_STRIDE = 0.30
WALK_STANCE = 0.60


@anim("Walk", WALK_FRAMES, True, "freundlicher mechanischer Gang")
def walk(f):
    t = f / WALK_FRAMES
    c = math.cos(2 * math.pi * t)
    feet = {}
    for side, off in (("L", 0.0), ("R", 0.5)):
        feet[side] = gait_foot(t + off, WALK_STRIDE, WALK_STANCE, 0.075, -12, 24, outward=0.004)
    return P(
        pelvis=(0.012 * math.sin(2 * math.pi * t), 0, -0.046 - 0.012 * math.cos(4 * math.pi * t)),
        pelvis_rot=(2, 0, -6 * c),
        spine=(4 + 1.5 * math.cos(4 * math.pi * t), -1.5 * math.sin(2 * math.pi * t), 5 * c),
        head=(-4 - 1.5 * math.cos(4 * math.pi * t), 1.5 * math.sin(2 * math.pi * t), -1.5 * c),
        uarm_L=arm(20 * c, 5),
        uarm_R=arm(-20 * c, 5),
        larm_L=arm(-14 - 12 * max(0.0, -c)),
        larm_R=arm(-14 - 12 * max(0.0, c)),
        shoulder_L=(0, -2 * max(0.0, -c), 0),
        shoulder_R=(0, -2 * max(0.0, c), 0),
        fing_LR=curl(28),
        foot_L=feet["L"],
        foot_R=feet["R"],
    )


RUN_FRAMES = 16
RUN_STRIDE = 0.30
RUN_STANCE = 0.36


@anim("Run", RUN_FRAMES, True, "dynamischer Lauf mit Flugphase")
def run(f):
    t = f / RUN_FRAMES
    c = math.cos(2 * math.pi * t)
    feet = {}
    for side, off in (("L", 0.0), ("R", 0.5)):
        feet[side] = gait_foot(t + off, RUN_STRIDE, RUN_STANCE, 0.068, -6, 28, kick=0.02, outward=0.004)
    bob = math.cos(4 * math.pi * (t - RUN_STANCE / 2))
    return P(
        pelvis=(0.008 * math.sin(2 * math.pi * t), 0, -0.058 - 0.018 * bob),
        pelvis_rot=(2, 0, -9 * c),
        spine=(14 + 2 * bob, 0, 9 * c),
        head=(-12 - 2 * bob, 0, -3 * c),
        uarm_L=arm(38 * c, 9),
        uarm_R=arm(-38 * c, 9),
        larm_L=arm(-78 + 12 * c),
        larm_R=arm(-78 - 12 * c),
        hand_LR=arm(-8),
        shoulder_L=(0, -3 * max(0.0, -c), 0),
        shoulder_R=(0, -3 * max(0.0, c), 0),
        fing_LR=curl(72),
        thumb_LR=curl(45),
        foot_L=feet["L"],
        foot_R=feet["R"],
    )


JUMP_KEYS = [
    (0, STAND),
    (7, P(pelvis=(0, 0, -0.105), spine=(14, 0, 0), head=(-8, 0, 0), uarm_LR=arm(38, 10), larm_LR=arm(-22),
          fing_LR=curl(40))),
    (11, P(pelvis=(0, 0, 0.0), spine=(-2, 0, 0), head=(-10, 0, 0), uarm_LR=arm(-95, 34, 10), larm_LR=arm(-20),
           foot_LR=(0, 0.02, 0.0, 30), fing_LR=curl(10), eyes=(1.08, 0, 0.002)), "out"),
    (14, P(pelvis=(0, 0, 0.0), spine=(-4, 0, 0), head=(-10, 0, 0), uarm_LR=arm(-100, 40, 12), larm_LR=arm(-15),
           foot_LR=(0, 0.03, 0.05, 25), fing_LR=curl(5), eyes=(1.08, 0, 0.002))),
    (21, P(pelvis=(0, 0, 0.0), spine=(4, 0, 0), head=(-6, 0, 0), uarm_LR=arm(-95, 50, 10), larm_LR=arm(-24),
           foot_LR=(0.01, -0.03, 0.15, -8), fing_LR=curl(20))),
    (28, P(pelvis=(0, 0, 0.0), spine=(2, 0, 0), head=(4, 0, 0), uarm_LR=arm(-45, 50), larm_LR=arm(-25),
           foot_LR=(0.01, -0.02, 0.05, -10), fing_LR=curl(20))),
    (31, P(pelvis=(0, 0, -0.02), spine=(4, 0, 0), head=(4, 0, 0), uarm_LR=arm(-38, 45), larm_LR=arm(-25),
           foot_LR=(0.01, 0.0, 0.0, 0), fing_LR=curl(20)), "in"),
    (34, P(pelvis=(0, 0, -0.115), spine=(16, 0, 0), head=(8, 0, 0), uarm_LR=arm(-28, 18), larm_LR=arm(-30),
           foot_LR=(0.01, 0.0, 0.0, 0), fing_LR=curl(35), eyes=(0.75, 0, 0)), "out"),
    (40, STAND),
]


@anim("Jump", 40, False, "Absprung (0-11), Flug (11-30), Landung (31-40)")
def jump(f):
    return keyed(f, JUMP_KEYS)


@anim("Fall", 24, True, "kontrolliertes Fallen, Arme ausbalancierend")
def fall(f):
    t = f / 24.0
    s1 = math.sin(2 * math.pi * t)
    s2 = math.sin(2 * math.pi * t + 1.3)
    return P(
        pelvis=(0, 0, -0.005),
        pelvis_rot=(-4, 0, 2 * s1),
        spine=(-5 + 1.5 * s2, 0, -2 * s1),
        head=(10 + 2 * s2, 2 * s1, 0),
        uarm_L=arm(-35 + 8 * s1, 62 + 6 * s2),
        uarm_R=arm(-35 - 8 * s1, 62 - 6 * s2),
        larm_L=arm(-30 + 6 * s2),
        larm_R=arm(-30 - 6 * s2),
        fing_LR=curl(8),
        thumb_LR=curl(0),
        foot_L=(0.015, -0.03 + 0.03 * s1, 0.07 + 0.015 * s2, -8),
        foot_R=(0.015, -0.03 - 0.03 * s1, 0.07 - 0.015 * s2, -8),
        eyes=(1.08, 0, 0.002),
    )


LAND_KEYS = [
    (0, P(pelvis=(0, 0, -0.01), spine=(-3, 0, 0), head=(10, 0, 0), uarm_LR=arm(-35, 55), larm_LR=arm(-30),
          fing_LR=curl(8))),
    (3, P(pelvis=(0, 0, -0.125), spine=(18, 0, 0), head=(10, 0, 0), uarm_LR=arm(-30, 25), larm_LR=arm(-38),
          fing_LR=curl(35), eyes=(0.7, 0, 0)), "out"),
    (8, P(pelvis=(0, 0, -0.035), spine=(4, 0, 0), head=(-3, 0, 0), uarm_LR=arm(-6, 8), larm_LR=arm(-14),
          fing_LR=curl(20))),
    (12, P(pelvis=(0, 0, -0.022), spine=(1, 0, 0), head=(1, 0, 0), uarm_LR=arm(2, 3), larm_LR=arm(-6))),
    (18, STAND),
]


@anim("Land", 18, False, "Abfedern nach dem Sprung")
def land(f):
    return keyed(f, LAND_KEYS)


@anim("Crouch", 40, True, "mechanisches Ducken (gehaltene Pose, leichtes Hydraulik-Wippen)")
def crouch(f):
    t = f / 40.0
    w = math.sin(2 * math.pi * t)
    return P(
        pelvis=(0, 0.060, -0.165 - 0.005 * w),
        pelvis_rot=(6, 0, 0),
        spine=(36 + 1.0 * w, 0, 0),
        head=(-32 - 1.0 * w, 0, 0),         # 1.14: the taller head would touch backpacks and wings at -36
        shoulder_LR=(0, 2, 0),
        uarm_LR=arm(-28, 14),
        larm_LR=arm(-48),
        hand_LR=arm(-10),
        fing_LR=curl(35),
        foot_LR=(0.075, 0.0, 0.0, 0.0),
        eyes=(1.0, 0, 0.003),
    )


INTERACT_KEYS = [
    (0, STAND),
    (9, P(head=(10, 0, -8), spine=(3, 0, -4), uarm_R=arm(-55, 4), larm_R=arm(-40), hand_R=arm(-10),
          fing_R=curl(8), thumb_R=curl(30), eyes=(1.0, -0.006, -0.004))),
    (16, P(pelvis=(0, 0, -0.016), head=(12, 0, -6), spine=(6, 0, -5), uarm_R=arm(-74, -4), larm_R=arm(-18),
           hand_R=arm(-18), fing_R=curl(4), thumb_R=curl(35), uarm_L=arm(6, 6), eyes=(1.0, -0.006, -0.004))),
    (19, P(pelvis=(0, 0, -0.018), head=(12, 0, -6), spine=(7, 0, -5), uarm_R=arm(-80, -4), larm_R=arm(-10),
           hand_R=arm(-22), fing_R=curl(4), thumb_R=curl(35), uarm_L=arm(6, 6), eyes=(1.0, -0.006, -0.004)), "out"),
    (25, P(pelvis=(0, 0, -0.018), head=(12, 0, -6), spine=(7, 0, -5), uarm_R=arm(-80, -4), larm_R=arm(-10),
           hand_R=arm(-22), fing_R=curl(4), thumb_R=curl(35), uarm_L=arm(6, 6), eyes=(0.55, -0.006, -0.004))),
    (30, P(pelvis=(0, 0, -0.014), head=(4, 6, -4), spine=(4, 0, -3), uarm_R=arm(-55, 4), larm_R=arm(-40),
           hand_R=arm(-10), fing_R=curl(10), thumb_R=curl(25), eyes=(1.0, 0, 0))),
    (37, P(head=(14, 4, 0), uarm_R=arm(-10, 3), larm_R=arm(-14))),
    (48, STAND),
]


@anim("Interact", 48, False, "Schalter/Konsole bedienen (Druck bei Frame 19-25)")
def interact(f):
    return keyed(f, INTERACT_KEYS)


PUSH_FRAMES = 32


@anim("Push", PUSH_FRAMES, True, "schweres Objekt schieben")
def push(f):
    t = f / PUSH_FRAMES
    c = math.cos(2 * math.pi * t)
    feet = {}
    for side, off in (("L", 0.0), ("R", 0.5)):
        feet[side] = gait_foot(t + off, 0.20, 0.70, 0.045, -6, 18, center=0.07, outward=0.012)
    return P(
        pelvis=(0.010 * math.sin(2 * math.pi * t), 0.02, -0.062 - 0.008 * math.cos(4 * math.pi * t)),
        pelvis_rot=(10, 0, -4 * c),
        spine=(16 + 1.0 * math.cos(4 * math.pi * t), 0, 3 * c),
        head=(-20, 0, -1.5 * c),
        uarm_L=arm(-78 + 3 * c, -6),
        uarm_R=arm(-78 - 3 * c, -6),
        larm_L=arm(-28 - 4 * c),
        larm_R=arm(-28 + 4 * c),
        hand_LR=arm(-12),
        fing_LR=curl(78),
        thumb_LR=curl(50),
        foot_L=feet["L"],
        foot_R=feet["R"],
        eyes=(0.85, 0, 0.003),
    )


DEATH_KEYS = [
    (0, STAND),
    (3, P(head=(-6, 12, 14), spine=(-5, 0, 6), uarm_L=arm(-10, 18), uarm_R=arm(5, 12), larm_LR=arm(-25),
          fing_LR=curl(5), eyes=(0.25, 0, 0)), "out"),
    (5, P(head=(-4, 10, 12), spine=(-4, 0, 5), uarm_L=arm(-8, 16), uarm_R=arm(4, 10), larm_LR=arm(-22),
          fing_LR=curl(5), eyes=(1.0, 0, 0))),
    (8, P(head=(2, -12, -10), spine=(-2, 0, -6), uarm_L=arm(4, 10), uarm_R=arm(-10, 18), larm_LR=arm(-20),
          fing_LR=curl(10), eyes=(0.15, 0, 0)), "out"),
    (10, P(head=(2, -10, -9), spine=(-2, 0, -5), uarm_LR=arm(0, 12), larm_LR=arm(-18), eyes=(0.9, 0, 0))),
    (18, P(pelvis=(0, 0.01, -0.05), head=(26, 4, 0), spine=(10, 0, 0), shoulder_LR=(0, 4, 0), uarm_LR=arm(4, 6),
           larm_LR=arm(-6), fing_LR=curl(28), thumb_LR=curl(15), eyes=(0.5, 0, -0.004))),
    (29, P(pelvis=(0, 0.10, -0.125), pelvis_rot=(0, 0, 0), head=(34, 4, 0), spine=(16, 0, 0),
           shoulder_LR=(0, 6, 0), uarm_LR=arm(-12, 12), larm_LR=arm(-14), fing_LR=curl(30),
           foot_LR=(0.10, -0.08, 0.0, 0.0), eyes=(0.3, 0, -0.006)), "in"),
    (33, P(pelvis=(0, 0.11, -0.235), pelvis_rot=(-6, 0, 0), head=(32, 3, 0), spine=(26, 0, 0),
           shoulder_LR=(0, 6, 0), uarm_LR=arm(-10, 14), larm_LR=arm(-14), fing_LR=curl(30),
           foot_LR=(0.12, -0.33, 0.05, -45), eyes=(0.25, 0, -0.006)), "smooth"),
    (37, P(pelvis=(0, 0.06, -0.335), pelvis_rot=(-32, 0, 0), head=(30, 2, 0), spine=(30, 0, 0),
           shoulder_LR=(0, 6, 0), uarm_LR=arm(-8, 16), larm_LR=arm(-14), fing_LR=curl(30),
           foot_LR=(0.13, -0.40, 0.0, -68), eyes=(0.22, 0, -0.006)), "in"),
    (42, P(pelvis=(0, 0.06, -0.318), pelvis_rot=(-30, 0, 0), head=(38, 2, 0), spine=(36, 0, 0),
           shoulder_LR=(0, 6, 0), uarm_LR=arm(-6, 18), larm_LR=arm(-16), fing_LR=curl(32),
           foot_LR=(0.13, -0.40, 0.0, -68), eyes=(0.3, 0, -0.006)), "out"),
    (51, P(pelvis=(0, 0.06, -0.332), pelvis_rot=(-32, 0, 0), head=(46, 4, 4), spine=(40, 0, 0),
           shoulder_LR=(0, 8, 0), uarm_LR=arm(-2, 16), larm_LR=arm(-12), fing_LR=curl(35),
           foot_LR=(0.13, -0.40, 0.0, -68), eyes=(0.1, 0, -0.008))),
    (64, P(pelvis=(0, 0.06, -0.334), pelvis_rot=(-32, 0, 0), head=(52, 5, 6), spine=(42, 0, 0),
           shoulder_LR=(0, 8, 0), uarm_LR=arm(0, 16), larm_LR=arm(-10), fing_LR=curl(38),
           foot_LR=(0.13, -0.40, 0.0, -68), eyes=(0.02, 0, -0.008))),
]


@anim("Death", 64, False, "Energieverlust, Zusammensacken, Augen erlöschen")
def death(f):
    return keyed(f, DEATH_KEYS)


# ================================ Phase 1: systems the game already has ================================


def wave(t, k, phase=0.0):
    """Loop-safe oscillation: integer k cycles per clip."""
    return math.sin(2 * math.pi * (k * t + phase))


def pulse(f, center, width):
    """Smooth 0..1..0 bump around a frame."""
    d = abs(f - center) / width
    return max(0.0, 1.0 - d) ** 2 * (3 - 2 * max(0.0, 1.0 - d)) if d < 1 else 0.0


@anim("DoubleJump", 24, False, "zweiter Sprung in der Luft: Beine anziehen, Arme schlagen, endet in Fall-Pose", "Phase1")
def double_jump(f):
    return keyed(f, [
        (0, P(spine=(4, 0, 0), head=(-6, 0, 0), uarm_LR=arm(-60, 45), larm_LR=arm(-30),
              foot_LR=(0.01, -0.02, 0.10, -8), fing_LR=curl(20))),
        (4, P(pelvis_rot=(8, 0, 0), spine=(14, 0, 0), head=(-10, 0, 0), uarm_LR=arm(35, 22), larm_LR=arm(-18),
              foot_LR=(0.01, -0.04, 0.15, -6), fing_LR=curl(60), thumb_LR=curl(40), eyes=(1.15, 0, 0.003)), "out"),
        (9, P(spine=(-6, 0, 0), head=(-10, 0, 0), uarm_LR=arm(-95, 45), larm_LR=arm(-15),
              foot_LR=(0.02, 0.04, 0.06, 18), fing_LR=curl(5), eyes=(1.12, 0, 0.003)), "out"),
        (16, P(spine=(2, 0, 0), head=(4, 0, 0), uarm_LR=arm(-50, 55), larm_LR=arm(-28),
               foot_LR=(0.015, -0.02, 0.08, -8), fing_LR=curl(10), eyes=(1.08, 0, 0.002))),
        (24, fall(0)),
    ])


HIT_KEYS = [
    (0, STAND),
    (2, P(pelvis=(0, 0.012, -0.022), spine=(-9, 4, 6), head=(-14, -6, 8), uarm_L=arm(8, 16), uarm_R=arm(6, 12),
          larm_LR=arm(-20), fing_LR=curl(0), eyes=(0.3, 0, 0)), "out"),
    (5, P(pelvis=(0, 0.004, -0.016), spine=(4, -1, -2), head=(5, 2, -2), uarm_LR=arm(-4, 4), larm_LR=arm(-10),
          eyes=(1.0, 0, 0))),
    (12, STAND),
]


@anim("Hit", 12, False, "kurzer Treffer (Hitze/Strom), startet und endet in der Grundpose", "Phase1",
      "Additive (Referenz Frame 0)")
def hit(f):
    return keyed(f, HIT_KEYS)


@anim("HitHeavy", 26, False, "schwerer Treffer: Rückwärtsschritt, Taumeln, Fangen", "Phase1")
def hit_heavy(f):
    return keyed(f, [
        (0, STAND),
        (3, P(pelvis=(0, 0.03, -0.04), spine=(-16, -6, 10), head=(-20, -8, 10), uarm_LR=arm(20, 30),
              larm_LR=arm(-30), fing_LR=curl(0), eyes=(0.2, 0, 0), foot_R=(0.02, 0.09, 0.05, 0)), "out"),
        (8, P(pelvis=(0, 0.05, -0.06), spine=(10, 0, 4), head=(12, 0, 0), uarm_LR=arm(-15, 15), larm_LR=arm(-24),
              eyes=(0.6, 0, -0.003), foot_R=(0.03, 0.13, 0.0, 0))),
        (14, P(pelvis=(0, 0.03, -0.035), spine=(3, 0, 2), head=(4, 0, 0), uarm_LR=arm(-5, 8), larm_LR=arm(-14),
               foot_R=(0.015, 0.07, 0.04, 0))),
        (20, P(pelvis=(0, 0.008, -0.022), spine=(1, 0, 0), foot_R=(0.0, 0.0, 0.0, 0))),
        (26, STAND),
    ])


@anim("Electrocuted", 18, True, "Stromschlag: Zittern, steife Arme, flackernde Augen", "Phase1")
def electrocuted(f):
    t = f / 18.0
    flick = 0.65 + 0.5 * wave(t, 13, 0.1) * wave(t, 5)
    return P(
        pelvis=(0.004 * wave(t, 7, 0.1), 0.004 * wave(t, 9, 0.3), -0.035 + 0.006 * wave(t, 11, 0.2)),
        spine=(-6 + 4 * wave(t, 7), 3 * wave(t, 9, 0.5), 4 * wave(t, 5, 0.2)),
        head=(-8 + 6 * wave(t, 11, 0.1), 8 * wave(t, 7, 0.4), 6 * wave(t, 9)),
        uarm_L=arm(-25 + 10 * wave(t, 9), 40 + 8 * wave(t, 7)),
        uarm_R=arm(-25 + 10 * wave(t, 7, 0.3), 40 + 8 * wave(t, 11, 0.6)),
        larm_L=arm(-15 + 12 * wave(t, 11)),
        larm_R=arm(-15 + 12 * wave(t, 9, 0.2)),
        hand_LR=arm(10 * wave(t, 7, 0.4)),
        fing_LR=curl(-6 + 10 * wave(t, 13)),
        thumb_LR=curl(-10),
        foot_LR=(0.03, 0.0, 0.0, 0.0),
        eyes=(max(0.12, min(1.25, flick)), 0, 0),
    )


@anim("GasCough", 48, True, "Gas: gebeugt, zwei Hustenstöße, wedelt vor dem Visier", "Phase1")
def gas_cough(f):
    t = f / 48.0
    c = pulse(f, 7, 4) + pulse(f, 31, 4)
    return P(
        pelvis=(0.004 * wave(t, 1), 0.02, -0.035 - 0.012 * c),
        pelvis_rot=(4, 0, 0),
        spine=(16 + 10 * c, 0, 3 * wave(t, 1)),
        head=(10 + 12 * c, 3 * wave(t, 1, 0.2), -4),
        uarm_L=arm(-78 + 4 * wave(t, 4), -16),
        larm_L=arm(-62, -10),
        hand_L=arm(0, 0, 22 * wave(t, 4)),
        fing_L=curl(2),
        uarm_R=arm(-28, 6),
        larm_R=arm(-62),
        fing_R=curl(40),
        foot_LR=(0.02, 0.0, 0.0, 0.0),
        eyes=(0.5 - 0.35 * c, 0, -0.003),
    )


@anim("SlopeSlide", 32, True, "auf steilem Hang rutschen, seitlich wie beim Surfen", "Phase1")
def slope_slide(f):
    t = f / 32.0
    return P(
        pelvis=(0.004 * wave(t, 1), 0.02, -0.07 + 0.006 * wave(t, 2)),
        pelvis_rot=(0, 0, 25),
        spine=(-6 + 2 * wave(t, 1), 2 * wave(t, 1, 0.3), -12 + 3 * wave(t, 1)),
        head=(8, 0, -14),
        uarm_L=arm(-20 + 10 * wave(t, 1), 60 + 8 * wave(t, 1, 0.25)),
        uarm_R=arm(10 - 10 * wave(t, 1), 55 - 8 * wave(t, 1, 0.6)),
        larm_LR=arm(-25),
        fing_LR=curl(5),
        foot_L=(0.02, -0.10, 0.0, 0.0),
        foot_R=(0.04, 0.10, 0.0, 0.0),
        footyaw_LR=(25.0,),
        eyes=(1.12, 0, 0.002),
    )


@anim("PushStart", 16, False, "an die Kiste lehnen: Grundpose -> Push (Frame 0)", "Phase1")
def push_start(f):
    end = push(0)
    mid = lerp_pose(STAND, end, 0.5)
    mid["foot_R"] = (end["foot_R"][0], end["foot_R"][1] * 0.5, 0.04, 0.0)
    mid["foot_L"] = (end["foot_L"][0], end["foot_L"][1] * 0.5, 0.0, 0.0)
    return keyed(f, [(0, STAND), (8, mid), (16, end)])


@anim("PushStep", 24, False, "Kiste rutscht einen Schritt: Ausholen, Stoß, zurück in Push (Frame 0)", "Phase1")
def push_step(f):
    base = push(0)

    def mod(**kw):
        p = dict(base)
        p.update({k: tuple(v) for k, v in kw.items()})
        return p
    return keyed(f, [
        (0, base),
        (6, mod(pelvis=(0, 0.035, -0.075), spine=(22, 0, 0), larm_L=arm(-42), larm_R=arm(-42), eyes=(0.7, 0, 0.003))),
        (11, mod(pelvis=(0, 0.0, -0.058), spine=(12, 0, 0), uarm_L=arm(-86, -6), uarm_R=arm(-86, -6),
                 larm_L=arm(-6), larm_R=arm(-6), eyes=(0.55, 0, 0.003)), "out"),
        (16, mod(pelvis=(0, 0.012, -0.062))),
        (24, base),
    ])


MUD_FRAMES = 32


@anim("MudWalk", MUD_FRAMES, True, "schwerer Gang durch Schlamm, Füße ziehen sich heraus", "Phase1")
def mud_walk(f):
    t = f / MUD_FRAMES
    c = math.cos(2 * math.pi * t)
    feet = {}
    for side, off in (("L", 0.0), ("R", 0.5)):
        feet[side] = gait_foot(t + off, 0.18, 0.66, 0.10, -4, 30, outward=0.02)
    return P(
        pelvis=(0.016 * math.sin(2 * math.pi * t), 0, -0.050 - 0.010 * math.cos(4 * math.pi * t)),
        pelvis_rot=(4, 0, -5 * c),
        spine=(10, 3 * math.sin(2 * math.pi * t), 6 * c),
        head=(-8, -2 * math.sin(2 * math.pi * t), -3 * c),
        uarm_L=arm(16 * c, 22),
        uarm_R=arm(-16 * c, 22),
        larm_LR=arm(-30),
        fing_LR=curl(30),
        foot_L=feet["L"],
        foot_R=feet["R"],
        eyes=(0.8, 0, 0.002),
    )


RESPAWN_DOWN = P(pelvis=(0, 0.02, -0.11), pelvis_rot=(6, 0, 0), spine=(20, 0, 0), head=(38, 0, 0),
                 shoulder_LR=(0, 6, 0), uarm_LR=arm(-5, 6), larm_LR=arm(-8), fing_LR=curl(30),
                 foot_LR=(0.04, 0.0, 0.0, 0.0), eyes=(0.02, 0, -0.008))


def respawn_eyes(p, value):
    p = dict(p)
    p["eyes"] = (value, p["eyes"][1], p["eyes"][2])
    return p


@anim("Respawn", 48, False, "Hochfahren am Checkpoint: Augen flackern an, Systemcheck, Grundpose", "Phase1")
def respawn(f):
    return keyed(f, [
        (0, RESPAWN_DOWN),
        (8, respawn_eyes(RESPAWN_DOWN, 0.35), "linear"),
        (10, respawn_eyes(RESPAWN_DOWN, 0.05), "linear"),
        (12, respawn_eyes(RESPAWN_DOWN, 0.8), "linear"),
        (14, respawn_eyes(RESPAWN_DOWN, 0.2), "linear"),
        (17, respawn_eyes(RESPAWN_DOWN, 1.0), "linear"),
        (24, P(pelvis=(0, 0, -0.03), spine=(2, 0, 0), head=(-8, 0, 0), uarm_LR=arm(-10, 10), larm_LR=arm(-25),
               fing_LR=curl(0), eyes=(1.1, 0, 0.002))),
        (30, P(pelvis=(0, 0, -0.02), head=(0, 6, 25), uarm_LR=arm(-8, 8), larm_LR=arm(-22), fing_LR=curl(55),
               eyes=(1.0, 0.01, 0))),
        (36, P(pelvis=(0, 0, -0.02), head=(0, -6, -25), uarm_LR=arm(-4, 6), larm_LR=arm(-14), fing_LR=curl(15),
               eyes=(1.0, -0.01, 0))),
        (42, P(pelvis=(0, 0, -0.035), head=(3, 0, 0), eyes=(1.0, 0, 0))),
        (48, STAND),
    ])


# ================================ Phase 2: tools and interaction ================================

TOOL_CLIPS = {"ToolAim", "ToolUse", "Scan"}


def aim_pose(t=0.0):
    br = wave(t, 2)
    return P(
        pelvis=(0, 0, -0.016 - 0.003 * br),
        spine=(2 + 0.8 * br, 0, 8),
        head=(-1, 0, -8),
        uarm_R=arm(-84 + 1.0 * br, -6),
        larm_R=arm(-8),
        hand_R=arm(-4),
        fing_R=curl(75),
        thumb_R=curl(45),
        uarm_L=arm(4 + 1.0 * br, 6),
        larm_L=arm(-10),
        eyes=(0.85, -0.004, 0),
    )


@anim("ToolAim", 40, True, "Werkzeug (Lampe/Scanner/Portal-Emitter) nach vorn halten, rechte Hand an Prop_R",
      "Phase2", "Oberkörper-Maske")
def tool_aim(f):
    return aim_pose(f / 40.0)


@anim("ToolUse", 20, False, "Werkzeug auslösen: Rückstoß und zurück in ToolAim (Frame 0)", "Phase2",
      "Oberkörper-Maske")
def tool_use(f):
    base = aim_pose(0)

    def mod(**kw):
        p = dict(base)
        p.update({k: tuple(v) for k, v in kw.items()})
        return p
    return keyed(f, [
        (0, base),
        (3, mod(uarm_R=arm(-98, -6), larm_R=arm(-22), spine=(-3, 0, 10), head=(-5, 0, -8), eyes=(1.25, -0.004, 0)),
         "out"),
        (8, mod(uarm_R=arm(-81, -6), larm_R=arm(-6), spine=(3, 0, 8), eyes=(0.9, -0.004, 0))),
        (20, base),
    ])


@anim("Scan", 64, True, "Umgebung scannen: Körper schwenkt, Augen pulsieren", "Phase2", "Oberkörper-Maske")
def scan(f):
    t = f / 64.0
    sw = wave(t, 1)
    return P(
        pelvis=(0, 0, -0.018),
        spine=(4, 0, 6 + 20 * sw),
        head=(6, 0, -6 + 14 * wave(t, 1, 0.04)),
        uarm_R=arm(-62, -4),
        larm_R=arm(-26),
        hand_R=arm(-6),
        fing_R=curl(75),
        thumb_R=curl(45),
        uarm_L=arm(3, 6),
        larm_L=arm(-12),
        eyes=(1.0 + 0.14 * wave(t, 4), 0.008 * sw, 0),
    )


@anim("PickUp", 44, False, "Gegenstand aufheben, kurz betrachten, einstecken", "Phase2")
def pick_up(f):
    return keyed(f, [
        (0, STAND),
        (10, P(pelvis=(0, 0.07, -0.155), pelvis_rot=(6, 0, 0), spine=(50, 0, 6), head=(-2, 0, -4),
               uarm_R=arm(-66, 4), larm_R=arm(-4), hand_R=arm(-6), fing_R=curl(4), thumb_R=curl(0),
               uarm_L=arm(-24, 14), larm_L=arm(-24), foot_LR=(0.08, 0.0, 0.0, 0.0))),
        (14, P(pelvis=(0, 0.07, -0.155), pelvis_rot=(6, 0, 0), spine=(50, 0, 6), head=(-2, 0, -4),
               uarm_R=arm(-66, 4), larm_R=arm(-4), hand_R=arm(-6), fing_R=curl(78), thumb_R=curl(45),
               uarm_L=arm(-24, 14), larm_L=arm(-24), foot_LR=(0.08, 0.0, 0.0, 0.0)), "linear"),
        (23, P(pelvis=(0, 0, -0.02), spine=(4, 0, 0), head=(14, 0, -10), uarm_R=arm(-58, -10), larm_R=arm(-72),
               hand_R=arm(-10), fing_R=curl(78), thumb_R=curl(45), eyes=(1.12, -0.006, -0.002))),
        (32, P(pelvis=(0, 0, -0.02), spine=(4, 0, 0), head=(14, 10, -10), uarm_R=arm(-58, -10), larm_R=arm(-72),
               hand_R=arm(-10), fing_R=curl(78), thumb_R=curl(45), eyes=(1.0, -0.006, -0.002))),
        (38, P(pelvis=(0, 0, -0.016), uarm_R=arm(4, 10), larm_R=arm(-30), fing_R=curl(60), eyes=(0.4, 0, 0))),
        (44, STAND),
    ])


@anim("TerminalTyping", 32, True, "am Terminal tippen, Kopf liest mit", "Phase2")
def terminal_typing(f):
    t = f / 32.0
    return P(
        pelvis=(0, 0.01, -0.02),
        spine=(7, 0, 0),
        head=(16, 0, 7 * wave(t, 1)),
        uarm_LR=arm(-46, -3),
        larm_L=arm(-50 + 6 * wave(t, 4)),
        larm_R=arm(-50 + 6 * wave(t, 4, 0.5)),
        hand_LR=arm(16),
        fing_L=curl(10 + 22 * max(0.0, wave(t, 8))),
        fing_R=curl(10 + 22 * max(0.0, wave(t, 8, 0.5))),
        thumb_LR=curl(20),
        eyes=(0.95, 0.009 * wave(t, 1), -0.003),
    )


@anim("PortalEnter", 20, False, "durch ein Portal: Zusammenzucken, Strecken, Augen-Blitz", "Phase2")
def portal_enter(f):
    return keyed(f, [
        (0, STAND),
        (4, P(pelvis=(0, 0, -0.06), spine=(10, 0, 0), head=(6, 0, 0), uarm_LR=arm(-22, 6), larm_LR=arm(-60),
              fing_LR=curl(60), eyes=(1.3, 0, 0.004)), "out"),
        (8, P(pelvis=(0, 0, 0.0), spine=(-6, 0, 0), head=(-8, 0, 0), uarm_LR=arm(25, 30), larm_LR=arm(-10),
              fing_LR=curl(0), foot_LR=(0.0, 0.0, 0.0, 20), eyes=(0.2, 0, 0))),
        (13, P(pelvis=(0, 0, -0.03), head=(0, 10, 0), uarm_LR=arm(0, 8), larm_LR=arm(-12), eyes=(1.1, 0, 0))),
        (16, P(pelvis=(0, 0, -0.02), head=(0, -6, 0), eyes=(0.9, 0, 0))),
        (20, STAND),
    ])


# ================================ Phase 3: character and life ================================


@anim("IdleScratch", 90, False, "Idle-Variante: kratzt sich am Ohr", "Phase3")
def idle_scratch(f):
    up = P(head=(2, 8, -6), uarm_R=arm(-148, -12), larm_R=arm(-64), hand_R=arm(-10), fing_R=curl(35),
           thumb_R=curl(20), uarm_L=arm(2, 4), eyes=(0.8, -0.006, 0))
    keys = [(0, STAND), (14, up)]
    for i, fr in enumerate(range(18, 50, 5)):
        p = dict(up)
        p["fing_R"] = (15.0 if i % 2 else 50.0,)
        p["hand_R"] = arm(-20 + (6 if i % 2 else -6))
        keys.append((fr, p))
    keys += [(53, with_(up, uarm_R=arm(-130, -30), larm_R=arm(-48))),
             (60, P(head=(2, -3, -4), uarm_R=arm(-20, 8), larm_R=arm(-20), eyes=(0.3, 0, 0))),
             (66, P(eyes=(1.0, 0, 0))), (90, STAND)]
    return keyed(f, keys)


@anim("IdleFootTap", 72, False, "Idle-Variante: Gewicht verlagern, ungeduldig mit dem Fuß tippen", "Phase3")
def idle_foot_tap(f):
    lean = P(pelvis=(0.015, 0, -0.015), pelvis_rot=(0, 2, 0), head=(0, 6, 4), uarm_L=arm(2, 6),
             foot_R=(0.02, -0.04, 0.0, 0.0), eyes=(1.0, 0.004, 0))
    keys = [(0, STAND), (10, lean)]
    for i, fr in enumerate(range(14, 58, 6)):
        p = dict(lean)
        p["foot_R"] = (0.02, -0.04, 0.0, -18.0 if i % 2 == 0 else 0.0)
        p["head"] = (2 if i % 2 == 0 else 0, 6, 4)
        keys.append((fr, p, "linear"))
    keys += [(64, lean), (72, STAND)]
    return keyed(f, keys)


@anim("IdleYawn", 90, False, "Idle-Variante: Strecken und „Gähnen“ (Augen zu Schlitzen)", "Phase3")
def idle_yawn(f):
    stretch = P(pelvis=(0, 0, -0.005), spine=(-10, 0, 0), head=(-20, 0, 0), uarm_LR=arm(-40, 115),
                larm_LR=arm(-20), fing_LR=curl(0), thumb_LR=curl(0), foot_LR=(0.0, 0.0, 0.0, 10), eyes=(0.12, 0, 0))
    hold = dict(stretch)
    hold["uarm_L"] = arm(-38, 118)
    hold["uarm_R"] = arm(-42, 112)
    return keyed(f, [
        (0, STAND),
        (18, stretch),
        (36, hold),
        (50, P(pelvis=(0, 0, -0.02), spine=(4, 0, 0), head=(6, 0, 0), eyes=(0.3, 0, 0))),
        (58, P(head=(0, 8, 6), eyes=(0.15, 0, 0))),
        (64, P(head=(0, -8, -6), eyes=(1.0, 0, 0))),
        (72, P(head=(0, 4, 3))),
        (90, STAND),
    ])


def stepping_feet(t, total_yaw, steps, sign, end=1.0, widen=0.02):
    """Feet that turn with the body in alternating steps (lead foot first). Returns pose kwargs."""
    lead, trail = ("L", "R") if sign > 0 else ("R", "L")
    per = total_yaw / steps
    slot = end / (2 * steps)
    kw = {}
    for side, offset in ((lead, 0), (trail, 1)):
        fyaw, lift = 0.0, 0.0
        for i in range(steps):
            a = (2 * i + offset) * slot + 0.02 * end
            b = a + 1.25 * slot
            w = ease((t - a) / (b - a))
            fyaw += sign * per * w
            if 0 < w < 1:
                lift = 0.045 * math.sin(math.pi * w)
        x0 = LEG_X if side == "L" else -LEG_X
        r = x0 + (widen if x0 > 0 else -widen)       # radial stance width, rotates with the foot
        ang = math.radians(fyaw)
        s = 1 if side == "L" else -1
        kw["foot_" + side] = ((r * math.cos(ang) - x0) * s, r * math.sin(ang), lift, 0.0)
        kw["footyaw_" + side] = (fyaw,)
    return kw


def turn_pose(f, frames, sign):
    """Turn in place by 90 deg (root motion) in four 45-deg foot steps, lead foot first."""
    t = f / frames
    yaw = sign * 90.0 * ease(t)
    bob = math.sin(math.pi * t)
    return P(root=(0.0, 0.0, yaw), pelvis=(0, 0, -0.012 - 0.018 * bob), pelvis_rot=(0, 0, 0),
             spine=(3 * bob, 0, sign * 6 * math.sin(math.pi * min(1, t * 1.6))),
             head=(0, 0, sign * 10 * math.sin(math.pi * min(1, t * 1.4))),
             uarm_L=arm(-8 * bob * sign, 8 * bob), uarm_R=arm(8 * bob * sign, 8 * bob), larm_LR=arm(-10 * bob),
             eyes=(1.0, sign * 0.008 * bob, 0), **stepping_feet(t, 90.0, 2, sign))


@anim("TurnLeft", 24, False, "90° nach links auf der Stelle (Root Motion)", "Phase3", "Root Motion (Rotation)")
def turn_left(f):
    return turn_pose(f, 24, 1)


@anim("TurnRight", 24, False, "90° nach rechts auf der Stelle (Root Motion)", "Phase3", "Root Motion (Rotation)")
def turn_right(f):
    return turn_pose(f, 24, -1)


@anim("WalkStart", 12, False, "Anlaufen: Grundpose -> Walk (Frame 0)", "Phase3")
def walk_start(f):
    end = walk(0)
    mid = lerp_pose(STAND, end, 0.5)
    mid["foot_L"] = (end["foot_L"][0], end["foot_L"][1] * 0.5, 0.05, -4.0)
    mid["pelvis"] = (0.0, 0.0, -0.05)
    mid["spine"] = (7.0, 0.0, 0.0)
    return keyed(f, [(0, STAND), (6, mid), (12, end)])


@anim("WalkStop", 16, False, "Abbremsen: Walk (Frame 10) -> Grundpose mit Nachwippen", "Phase3")
def walk_stop(f):
    start = walk(WALK_FRAMES // 2)
    mid = lerp_pose(start, STAND, 0.5)
    mid["foot_L"] = (start["foot_L"][0] * 0.5, start["foot_L"][1] * 0.4, 0.05, 0.0)
    settle = P(pelvis=(0, 0, -0.038), spine=(5, 0, 0), head=(-3, 0, 0), uarm_LR=arm(-6, 4), larm_LR=arm(-14))
    return keyed(f, [(0, start), (6, mid), (10, settle), (16, STAND)])


@anim("Talk", 72, True, "im Gespräch (z. B. mit M-4): Gesten, Nicken", "Phase3")
def talk(f):
    t = f / 72.0
    return P(
        pelvis=(0.004 * wave(t, 1), 0, -0.015),
        spine=(2 * wave(t, 2), 0, 4 * wave(t, 1)),
        head=(5 * wave(t, 3), 4 * wave(t, 1), 6 * wave(t, 1, 0.2)),
        uarm_R=arm(-30 + 10 * wave(t, 2), 10),
        larm_R=arm(-60 + 12 * wave(t, 3, 0.1)),
        hand_R=arm(-10, 0, 15 * wave(t, 2)),
        fing_R=curl(5),
        uarm_L=arm(-10 + 6 * wave(t, 2, 0.5), 8),
        larm_L=arm(-30 + 8 * wave(t, 3, 0.4)),
        fing_L=curl(12),
        eyes=(min(1.0, blink(f, 40)), 0.004 * wave(t, 1), 0),
    )


@anim("Listen", 72, True, "zuhören: Kopf geneigt, langsames Nicken", "Phase3")
def listen(f):
    t = f / 72.0
    nod = pulse(f, 20, 6) + pulse(f, 50, 6)
    return P(
        pelvis=(0.008 * wave(t, 1), 0, -0.016),
        pelvis_rot=(0, 1.5 * wave(t, 1), 0),
        spine=(1, 0, 2 * wave(t, 1)),
        head=(3 + 8 * nod, 10, 6),
        uarm_LR=arm(1, 4),
        larm_LR=arm(-8),
        fing_LR=curl(22),
        eyes=(min(blink(f, 30), blink(f, 62)), -0.004, 0.002),
    )


@anim("Celebrate", 48, False, "Freude: Hüpfer, Fäuste hoch, „^^“-Augen", "Phase3")
def celebrate(f):
    happy = (0.35, 0, 0.006)
    up = P(pelvis=(0, 0, 0.10), spine=(-6, 0, 0), head=(-12, 0, 0), uarm_LR=arm(-30, 125), larm_LR=arm(-30),
           fing_LR=curl(80), thumb_LR=curl(45), foot_LR=(0.0, 0.0, 0.10, 10), eyes=happy)
    apex = dict(up)
    apex["pelvis"] = (0, 0, 0.12)
    apex["foot_L"] = apex["foot_R"] = (0.0, 0.0, 0.12, 6)
    return keyed(f, [
        (0, STAND),
        (6, P(pelvis=(0, 0, -0.08), spine=(10, 0, 0), uarm_LR=arm(25, 10), larm_LR=arm(-20), fing_LR=curl(60),
              eyes=(0.8, 0, 0))),
        (10, up, "out"),
        (15, apex),
        (20, P(pelvis=(0, 0, -0.07), spine=(8, 0, 0), head=(-6, 0, 0), uarm_LR=arm(-40, 105), larm_LR=arm(-40),
               fing_LR=curl(80), eyes=happy), "in"),
        (26, P(pelvis=(0, 0, -0.02), head=(-10, 6, 0), uarm_LR=arm(-40, 112), larm_LR=arm(-25), fing_LR=curl(80),
               eyes=happy)),
        (32, P(pelvis=(0, 0, -0.03), head=(-10, -6, 0), uarm_LR=arm(-40, 128), larm_LR=arm(-45), fing_LR=curl(80),
               eyes=happy)),
        (38, P(pelvis=(0, 0, -0.02), uarm_LR=arm(-10, 20), larm_LR=arm(-20), eyes=happy)),
        (48, STAND),
    ])


@anim("EdgeBalance", 48, True, "an der Kante: vor und zurück wanken, Arme rudern", "Phase3")
def edge_balance(f):
    t = f / 48.0
    sway = wave(t, 1)
    return P(
        pelvis=(0, -0.012 * sway, -0.02),
        pelvis_rot=(5 * sway, 0, 0),
        spine=(8 * sway, 0, 3 * wave(t, 1, 0.3)),
        head=(14 - 4 * sway, 0, 4 * wave(t, 2)),
        uarm_L=arm(-56 + 30 * wave(t, 2), 78),
        uarm_R=arm(-56 + 30 * wave(t, 2, 0.5), 78),
        larm_LR=arm(-20),
        fing_LR=curl(5),
        thumb_LR=curl(0),
        foot_LR=(0.0, 0.0, 0.0, 0.0),
        eyes=(1.15, 0.006 * wave(t, 3), 0.002),
    )


# ================================ Phase 4: dances (120 BPM, 1 beat = 15 frames) ================================

BEAT = 15


def beat_bounce(f, depth=0.022):
    """Down on every beat."""
    return -depth * abs(math.sin(math.pi * f / BEAT))


@anim("DanceBob", 60, True, "Groove: Wippen auf jeden Schlag, Seitschritt, Kopfnicken", "Phase4")
def dance_bob(f):
    t = f / 60.0
    side = wave(t, 1)
    return P(
        pelvis=(0.03 * side, 0, -0.02 + beat_bounce(f)),
        pelvis_rot=(0, 3 * side, -6 * side),
        spine=(3, -4 * side, 6 * side),
        head=(6 * abs(math.sin(math.pi * f / BEAT)) - 2, 6 * side, -4 * side),
        uarm_L=arm(-35 + 25 * wave(t, 2), 18),
        uarm_R=arm(-35 + 25 * wave(t, 2, 0.5), 18),
        larm_LR=arm(-70),
        fing_LR=curl(65),
        thumb_LR=curl(40),
        foot_L=(0.03 + 0.03 * max(0.0, side), 0.0, 0.035 * max(0.0, wave(t, 2, 0.25)), 0.0),
        foot_R=(0.03 + 0.03 * max(0.0, -side), 0.0, 0.035 * max(0.0, wave(t, 2, 0.75)), 0.0),
        eyes=(0.75 + 0.25 * abs(math.cos(math.pi * f / BEAT)), 0.006 * side, 0.002),
    )


ROBOT_POSES = [
    P(uarm_L=arm(-90, -12), larm_L=arm(-90), uarm_R=arm(0, 4), larm_R=arm(0), head=(0, 0, 30), eyes=(0.8, 0.01, 0)),
    P(uarm_L=arm(0, 85), larm_L=arm(0, 0, 0), uarm_R=arm(0, 85), larm_R=arm(0), head=(0, 0, 0), spine=(0, 0, 0),
      pelvis=(0, 0, -0.04), eyes=(1.0, 0, 0)),
    P(uarm_R=arm(-90, -12), larm_R=arm(-90), uarm_L=arm(0, 4), larm_L=arm(0), head=(0, 0, -30), eyes=(0.8, -0.01, 0)),
    P(uarm_LR=arm(-90, 0), larm_LR=arm(0), spine=(0, 0, 20), head=(0, 0, -20), pelvis=(0, 0, -0.03)),
    P(uarm_LR=arm(-90, 0), larm_LR=arm(0), spine=(0, 0, -20), head=(0, 0, 20), pelvis=(0, 0, -0.03)),
    P(uarm_L=arm(-40, 30), larm_L=arm(-90), uarm_R=arm(-40, 30), larm_R=arm(-90), head=(10, 0, 0),
      pelvis=(0, 0, -0.06), eyes=(0.5, 0, 0)),
    P(uarm_L=arm(0, 80), larm_L=arm(-90, 0, 0), uarm_R=arm(0, 4), larm_R=arm(0), head=(-8, 0, 25)),
    P(uarm_R=arm(0, 80), larm_R=arm(-90, 0, 0), uarm_L=arm(0, 4), larm_L=arm(0), head=(-8, 0, -25)),
]


@anim("DanceRobot", 120, True, "der Roboter-Tanz: abgehackte Posen im Takt", "Phase4")
def dance_robot(f):
    keys = []
    for i, pose in enumerate(ROBOT_POSES + [ROBOT_POSES[0]]):
        fr = i * BEAT
        if i:
            keys.append((fr - BEAT + 4, keys[-1][1], "linear"))   # hold, then snap in 4 frames
        p = dict(pose)
        p["fing_L"] = p["fing_R"] = (5.0,)
        keys.append((fr, p, "out"))
    return keyed(f, keys)


@anim("DanceWave", 60, True, "Armwelle von der linken zur rechten Hand und zurück", "Phase4")
def dance_wave(f):
    t = f / 60.0
    chain = []   # L hand, L forearm, L upper arm, shoulders, R upper arm, R forearm, R hand
    for i in range(7):
        chain.append(math.sin(2 * math.pi * (t - i * 0.07)))
    return P(
        pelvis=(0, 0, -0.02 + beat_bounce(f, 0.012)),
        spine=(0, 0, 3 * wave(t, 1)),
        head=(0, 6 * wave(t, 1, 0.25), 0),
        uarm_L=arm(0, 82 + 12 * chain[2]),
        larm_L=(0.0, -22 * chain[1], 0.0),
        hand_L=(0.0, -25 * chain[0], 0.0),
        shoulder_L=(0, -6 * chain[3], 0),
        shoulder_R=(0, -6 * chain[3], 0),
        uarm_R=arm(0, 82 + 12 * chain[4]),
        larm_R=(0.0, -22 * chain[5], 0.0),
        hand_R=(0.0, -25 * chain[6], 0.0),
        fing_LR=curl(0),
        thumb_LR=curl(0),
        eyes=(0.8, 0.01 * wave(t, 1), 0),
    )


@anim("DanceShuffle", 30, True, "schneller Shuffle (Running Man) auf der Stelle", "Phase4")
def dance_shuffle(f):
    t = f / 30.0
    feet = {}
    for side, off in (("L", 0.0), ("R", 0.5)):
        u = (t + off) % 1.0
        if u < 0.5:      # planted foot slides back
            y = -0.10 + 0.20 * (u / 0.5)
            feet[side] = (0.02, y, 0.0, 0.0)
        else:            # knee lift and reach forward
            w = (u - 0.5) / 0.5
            feet[side] = (0.02, lerp(0.10, -0.10, ease(w)), 0.085 * math.sin(math.pi * w), -6 * math.sin(math.pi * w))
    c = wave(t, 1)
    return P(
        pelvis=(0, 0, -0.04 - 0.012 * abs(wave(t, 2))),
        pelvis_rot=(4, 0, -6 * c),
        spine=(6, 0, 6 * c),
        head=(-6 + 3 * abs(wave(t, 2)), 0, -3 * c),
        uarm_L=arm(-30 * c - 20, 12),
        uarm_R=arm(30 * c - 20, 12),
        larm_LR=arm(-95),
        fing_LR=curl(75),
        thumb_LR=curl(45),
        foot_L=feet["L"],
        foot_R=feet["R"],
        eyes=(0.6, 0, 0.003),
    )


@anim("DanceDisco", 60, True, "Disco-Zeigepose: rechte Hand oben/unten im Takt, Hüftschwung", "Phase4")
def dance_disco(f):
    up = P(pelvis=(0.03, 0, -0.025), pelvis_rot=(0, 4, -10), spine=(-4, -6, 10), head=(-12, -6, -10),
           uarm_R=arm(-150, -20), larm_R=arm(-6), fing_R=curl(80), thumb_R=curl(50), uarm_L=arm(10, 14),
           larm_L=arm(-70), fing_L=curl(70), foot_R=(0.05, -0.06, 0.0, 12), eyes=(0.7, -0.01, 0.003))
    down = P(pelvis=(-0.03, 0, -0.03), pelvis_rot=(0, -4, 8), spine=(6, 6, -8), head=(10, 4, 8),
             uarm_R=arm(-58, -14), larm_R=arm(-6), fing_R=curl(80), thumb_R=curl(50), uarm_L=arm(10, 14),
             larm_L=arm(-70), fing_L=curl(70), foot_R=(0.05, -0.06, 0.0, 12), eyes=(0.9, 0.01, 0))
    return keyed(f, [(0, up), (BEAT, down, "out"), (2 * BEAT, up, "out"), (3 * BEAT, down, "out"), (4 * BEAT, up, "out")])


@anim("DanceSpin", 60, False, "360°-Drehung in kleinen Schritten, Schlusspose (Root Motion)", "Phase4",
      "Root Motion (Rotation)")
def dance_spin(f):
    t = min(1.0, f / 44.0)
    spin = math.sin(math.pi * t)
    pose = P(root=(0.0, 0.0, 360.0 * ease(t)), pelvis=(0, 0, -0.02 - 0.015 * spin), uarm_LR=arm(-10, 70 * spin),
             larm_LR=arm(-15), fing_LR=curl(5), head=(-6 * spin, 0, 0), eyes=(0.6 + 0.4 * (1 - spin), 0, 0),
             **stepping_feet(ease(t), 360.0, 8, 1, widen=0.045))
    if f <= 44:
        return pose
    finale = P(root=(0.0, 0.0, 360.0), pelvis=(0, 0, -0.01), spine=(-4, 0, 0), head=(-10, 6, 0),
               uarm_R=arm(-150, -25), larm_R=arm(-4), fing_R=curl(80), thumb_R=curl(50), uarm_L=arm(0, 30),
               larm_L=arm(-10), footyaw_LR=(360.0,), eyes=(0.35, 0, 0.006))
    return keyed(f, [(44, pose), (52, finale, "out"), (60, finale)])


# ================================ Phase 5: emotes ================================

SIT = P(pelvis=(0, 0.06, -0.334), pelvis_rot=(-32, 0, 0), spine=(28, 0, 0), head=(-2, 0, 0), uarm_LR=arm(-36, 12),
        larm_LR=arm(-26), fing_LR=curl(30), foot_LR=(0.13, -0.40, 0.0, -68))


def with_(pose, **kw):
    p = dict(pose)
    for k, v in kw.items():
        if k.endswith("_LR"):
            p[k[:-3] + "_L"] = p[k[:-3] + "_R"] = tuple(v)
        else:
            p[k] = tuple(v)
    return p


@anim("Wave", 48, False, "freundlich winken (rechte Hand)", "Phase5")
def wave_hello(f):
    up = P(head=(0, -6, -6), spine=(0, -3, 0), uarm_R=arm(-12, 78), larm_R=arm(0, 85), hand_R=arm(0), fing_R=curl(0),
           thumb_R=curl(0), eyes=(0.9, -0.006, 0))
    keys = [(0, STAND), (9, up)]
    for i, fr in enumerate((13, 17, 21, 25, 29, 33)):
        keys.append((fr, with_(up, larm_R=arm(0, 66 if i % 2 == 0 else 100))))
    keys += [(38, up), (48, STAND)]
    return keyed(f, keys)


@anim("Bow", 48, False, "höfliche Verbeugung", "Phase5")
def bow(f):
    down = P(pelvis=(0, 0.04, -0.025), pelvis_rot=(8, 0, 0), spine=(30, 0, 0), head=(16, 0, 0), uarm_LR=arm(8, 6),
             larm_LR=arm(-8), fing_LR=curl(10), eyes=(0.3, 0, -0.004))
    return keyed(f, [(0, STAND), (14, down), (30, down), (48, STAND)])


@anim("Clap", 30, True, "Klatschen", "Phase5")
def clap(f):
    t = f / 30.0
    c = 0.5 + 0.5 * math.cos(4 * math.pi * t)          # 1 = hands apart, 0 = together (two claps per loop)
    return P(spine=(3, 0, 0), head=(-4, 0, 0), uarm_LR=arm(-56, -10 + 10 * c), larm_LR=arm(-48 + 6 * c),
             hand_LR=arm(0, 0, 0), fing_LR=curl(4), thumb_LR=curl(5), pelvis=(0, 0, -0.016 - 0.006 * (1 - c)),
             eyes=(0.85, 0, 0.002))


@anim("Think", 72, True, "Nachdenken, Hand am Kinn", "Phase5")
def think(f):
    t = f / 72.0
    return P(pelvis=(0.008 * wave(t, 1), 0, -0.016), spine=(4, 0, -4), head=(-15 + 2 * wave(t, 2), -4, 8 + 4 * wave(t, 1)),
             uarm_R=arm(-66, -16), larm_R=arm(-96), hand_R=arm(-30), fing_R=curl(65), thumb_R=curl(40),
             uarm_L=arm(2, 6), eyes=(0.8, 0.008 * wave(t, 1), 0.004))


@anim("Point", 40, False, "nach vorn zeigen", "Phase5")
def point(f):
    p = P(spine=(2, 0, -8), head=(0, 0, -6), uarm_R=arm(-86, -4), larm_R=arm(-2), hand_R=arm(0), fing_R=curl(0),
          thumb_R=curl(40), uarm_L=arm(4, 6), eyes=(0.85, -0.004, 0))
    return keyed(f, [(0, STAND), (10, p, "out"), (30, p), (40, STAND)])


@anim("Shrug", 36, False, "Achselzucken", "Phase5")
def shrug(f):
    p = P(shoulder_LR=(0, -6, 0), uarm_LR=arm(-10, 26), larm_LR=arm(-72), hand_LR=arm(0, 0, -30), fing_LR=curl(0),
          thumb_LR=curl(0), head=(0, 10, 0), eyes=(0.9, 0, 0.003))
    return keyed(f, [(0, STAND), (10, p, "out"), (22, p), (36, STAND)])


@anim("NodYes", 30, False, "Ja-Nicken", "Phase5")
def nod_yes(f):
    return keyed(f, [(0, STAND), (6, P(head=(14, 0, 0))), (12, P(head=(-3, 0, 0))), (18, P(head=(12, 0, 0))),
                     (24, P(head=(-2, 0, 0))), (30, STAND)])


@anim("ShakeNo", 30, False, "Nein-Kopfschütteln", "Phase5")
def shake_no(f):
    return keyed(f, [(0, STAND), (6, P(head=(2, 0, 18))), (12, P(head=(2, 0, -18))), (18, P(head=(2, 0, 16))),
                     (24, P(head=(2, 0, -12))), (30, STAND)])


@anim("SitDown", 40, False, "hinsetzen: Grundpose -> SitIdle (Frame 0)", "Phase5")
def sit_down(f):
    return keyed(f, [
        (0, STAND),
        (12, P(pelvis=(0, 0.10, -0.125), spine=(16, 0, 0), head=(-6, 0, 0), uarm_LR=arm(-20, 12), larm_LR=arm(-20),
               foot_LR=(0.10, -0.08, 0.0, 0.0)), "in"),
        (18, P(pelvis=(0, 0.11, -0.235), pelvis_rot=(-6, 0, 0), spine=(24, 0, 0), head=(-4, 0, 0),
               uarm_LR=arm(-28, 12), larm_LR=arm(-22), foot_LR=(0.12, -0.33, 0.05, -45))),
        (26, with_(SIT, pelvis=(0, 0.06, -0.326)), "out"),
        (40, SIT),
    ])


@anim("SitIdle", 90, True, "sitzen: atmen, umschauen, mit den Füßen wippen", "Phase5")
def sit_idle(f):
    t = f / 90.0
    br = wave(t, 3)
    tap = max(0.0, wave(t, 6))
    return with_(SIT, spine=(28 + br, 0, 2 * wave(t, 1)), head=(-2 + 2 * br, 4 * wave(t, 1, 0.3), 14 * wave(t, 1)),
                 foot_L=(0.13, -0.40, 0.0, -68 + 10 * tap), foot_R=(0.13, -0.40, 0.0, -68 + 10 * max(0.0, wave(t, 6, 0.5))),
                 eyes=(min(blink(f, 30), blink(f, 75)), 0.008 * wave(t, 1), 0))


@anim("Sleep", 120, True, "eingeschlafen im Sitzen, ruhiges Atmen", "Phase5")
def sleep(f):
    t = f / 120.0
    br = wave(t, 3)
    return P(pelvis=(0, 0.06, -0.334), pelvis_rot=(-32, 0, 0), spine=(42 + 2 * br, 0, 0), head=(50 + 3 * br, 0, 4),
             shoulder_LR=(0, 8 - 2 * br, 0), uarm_LR=arm(0, 16), larm_LR=arm(-10), fing_LR=curl(38),
             foot_LR=(0.13, -0.40, 0.0, -68), eyes=(0.12, 0, -0.008))


@anim("DanceMoonwalk", 40, True, "Moonwalk: Füße gleiten nach hinten", "Phase5")
def moonwalk(f):
    t = f / 40.0

    def foot(u):
        u %= 1.0
        if u < 0.5:
            return (0.02, -0.12 + 0.24 * (u / 0.5), 0.0, 0.0)
        w = (u - 0.5) / 0.5
        return (0.02, lerp(0.12, -0.12, ease(w)), 0.010 * math.sin(math.pi * w), 32 * math.sin(math.pi * w) ** 0.4)
    c = wave(t, 1)
    return P(pelvis=(0.008 * c, 0.02, -0.030 - 0.010 * abs(wave(t, 2))), spine=(-4, 0, 3 * c), head=(-4, 0, -6),
             uarm_L=arm(10 * c, 12), uarm_R=arm(-10 * c, 12), larm_LR=arm(-30), fing_LR=curl(50),
             foot_L=foot(t), foot_R=foot(t + 0.5), eyes=(0.75, 0, 0.003))


@anim("DanceSway", 64, True, "langsames Schunkeln mit erhobenen Armen", "Phase5")
def dance_sway(f):
    t = f / 64.0
    sw = wave(t, 1)
    return P(pelvis=(0.03 * sw, 0, -0.025 + 0.008 * abs(sw)), pelvis_rot=(0, 4 * sw, 0), spine=(0, -6 * sw, 0),
             head=(-10, 8 * sw, 0), uarm_L=arm(-25, 122 + 8 * sw), uarm_R=arm(-25, 122 - 8 * sw), larm_LR=arm(-15),
             fing_LR=curl(10), foot_LR=(0.03, 0.0, 0.0, 0.0), eyes=(0.5, 0.008 * sw, 0.004))


# ================================ Phase 6: climbing and hanging ================================

# The ledge in root space: top 1.20 m above the root, front edge 0.22 m in front of it. The hands grip the edge
# by IK (knuckles 0.24 m forward, 1.215 m up), so a game can align the character to any ledge from these numbers.
LEDGE_TOP, LEDGE_EDGE = 1.20, 0.22
GRIP_X, GRIP_Y, GRIP_Z = 0.35, -0.215, 1.232
HANG_POLE = (1.0, 0.3, -0.2)                      # elbows out


def grip(dx=0.0, w=1.0, pole=HANG_POLE):
    return (GRIP_X + dx, GRIP_Y, GRIP_Z) + tuple(pole) + (w,)


HANG = P(pelvis=(0, 0.06, 0.135), spine=(-3, 0, 0), head=(-14, 0, 0), uarm_LR=arm(-30, 140), larm_LR=arm(0, 14),
         fing_LR=curl(20), thumb_LR=curl(65), foot_LR=(0.0, 0.08, 0.185, 18), eyes=(1.0, 0, 0.004),
         handik_LR=grip())


@anim("LedgeGrab", 20, False, "aus dem Fallen eine Kante greifen -> HangIdle (Frame 0)", "Phase6",
      "Kante: Oberseite 1,20 m über Root, Vorderkante 0,22 m vor Root")
def ledge_grab(f):
    return keyed(f, [
        (0, fall(0)),
        (5, P(pelvis=(0, 0.03, 0.06), spine=(-4, 0, 0), head=(-12, 0, 0), uarm_LR=arm(-30, 132), larm_LR=arm(0, 10),
              fing_LR=curl(5), foot_LR=(0.01, 0.0, 0.12, -8), eyes=(1.12, 0, 0.004), handik_LR=grip(w=0.0)), "out"),
        (9, with_(HANG, pelvis=(0, 0.06, 0.115), foot_LR=(0.0, -0.02, 0.20, 10)), "in"),
        (14, with_(HANG, foot_LR=(0.0, 0.11, 0.17, 20))),
        (20, HANG),
    ])


@anim("HangIdle", 60, True, "an einer Kante hängen (Hände greifen die Vorderkante per IK)", "Phase6",
      "Kante: Oberseite 1,20 m über Root, Vorderkante 0,22 m vor Root, Griffe ±0,35 m")
def hang_idle(f):
    t = f / 60.0
    return with_(HANG, pelvis=(0.004 * wave(t, 1), 0.06 + 0.012 * wave(t, 1, 0.2), 0.135),
                 head=(-14, 2 * wave(t, 1), 4 * wave(t, 1, 0.3)),
                 foot_L=(0.0, 0.08 + 0.03 * wave(t, 1), 0.185, 18), foot_R=(0.0, 0.08 + 0.03 * wave(t, 1, 0.15), 0.185, 18),
                 eyes=(min(1.0, blink(f, 40)), 0.006 * wave(t, 1, 0.3), 0.004))


def shimmy(f, sign):
    t = f / 36.0
    a = wave(t, 1)
    return with_(HANG, pelvis=(sign * 0.03 * a, 0.06, 0.135 - 0.01 * abs(a)), spine=(-3, sign * 3 * a, 0),
                 handik_L=grip(sign * 0.05 * a), handik_R=grip(-sign * 0.05 * a),
                 foot_L=(0.0 + 0.02 * a, 0.08, 0.185, 18), foot_R=(0.0 - 0.02 * a, 0.08, 0.185, 18),
                 head=(-14, 0, sign * 5))


@anim("ShimmyLeft", 36, True, "hängend nach links hangeln", "Phase6", "Controller bewegt seitlich")
def shimmy_left(f):
    return shimmy(f, 1)


@anim("ShimmyRight", 36, True, "hängend nach rechts hangeln", "Phase6", "Controller bewegt seitlich")
def shimmy_right(f):
    return shimmy(f, -1)


@anim("HangSwing", 48, True, "an einer Stange vor dem Gesicht hängen und schwingen", "Phase6",
      "Stange wie Kantengriff: 1,20 m über Root, 0,22 m vor Root")
def hang_swing(f):
    # the arms cannot reach above the big head, so the bar is in front of the face (same grip as the ledge)
    t = f / 48.0
    sw = wave(t, 1)
    return with_(HANG, pelvis=(0, 0.06 + 0.035 * sw, 0.135 - 0.008 * abs(sw)), spine=(-3 + 6 * sw, 0, 0),
                 head=(-14 - 4 * sw, 0, 0),
                 foot_LR=(0.0, 0.08 - 0.07 * sw, 0.185 + 0.02 * abs(sw), 18 - 10 * sw))


@anim("ClimbUp", 48, False, "aus dem Hängen auf die Kante ziehen (Root Motion 1,20 m hoch, 0,36 m vor)", "Phase6",
      "Root Motion (Position)")
def climb_up(f):
    push = (0.6, 1.0, 0.2)                         # elbows back while pressing down on the ledge
    k22 = P(root=(0, -0.10, 0), root_z=(0.85,), pelvis=(0, 0, -0.05), spine=(26, 0, 0), head=(-12, 0, 0),
            uarm_LR=arm(-30, 22), larm_LR=arm(-60), fing_LR=curl(20), foot_L=(0.02, -0.02, 0.06, 0),
            foot_R=(0.02, 0.02, 0.04, 15), handik_LR=grip(pole=push))
    k32 = P(root=(0, -0.22, 0), root_z=(1.18,), pelvis=(0, 0.02, -0.04), spine=(20, 0, 0), head=(-6, 0, 0),
            uarm_LR=arm(-20, 14), larm_LR=arm(-30), foot_L=(0.03, -0.32, 0.04, 0), foot_R=(0.03, 0.02, 0.0, 0),
            handik_LR=grip(w=0.0, pole=push))
    k42 = P(root=(0, -0.36, 0), root_z=(1.20,), pelvis=(0, 0, -0.04), spine=(8, 0, 0), uarm_LR=arm(-6, 8),
            larm_LR=arm(-14), foot_LR=(0.0, -0.36, 0.0, 0), handik_LR=grip(w=0.0, pole=push))
    # feet: lift first (sole above the ledge top), then step forward onto it - never through the ledge
    k28 = with_(lerp_pose(k22, k32, 0.6), root_z=(1.10,), foot_L=(0.03, 0.03, 0.11, 0), foot_R=(0.02, 0.02, 0.02, 10),
                handik_LR=grip(w=0.0, pole=push))
    k36 = with_(lerp_pose(k32, k42, 0.4), foot_R=(0.03, 0.0, 0.06, 0), foot_L=(0.02, -0.34, 0.015, 0))
    p = keyed(f, [
        (0, HANG),
        (12, P(root_z=(0.45,), pelvis=(0, 0.04, 0.0), spine=(10, 0, 0), head=(-8, 0, 0), uarm_LR=arm(-10, 70),
               larm_LR=arm(0, 70), fing_LR=curl(20), thumb_LR=curl(30), foot_LR=(0.0, 0.07, 0.10, 15),
               handik_LR=grip(pole=push))),
        (22, k22), (28, k28), (32, k32), (36, k36), (42, k42),
        (48, P(root=(0, -0.36, 0), root_z=(1.20,), foot_LR=(0.0, -0.36, 0.0, 0), handik_LR=grip(w=0.0, pole=push))),
    ])
    if 22 < f < 28:                                  # hands leave the ledge while the knee comes up
        ik = p["handik_L"]
        p = with_(p, handik_LR=ik[:6] + (1.0 - ease((f - 22) / 6.0),))
    press = ease(f / 10.0) if f <= 22 else 1.0 - ease((f - 22) / 8.0)   # palms flat on the ledge while pressing
    p = with_(p, hand_LR=tuple(x + HAND_PRESS * press * y for x, y in zip(p["hand_L"], arm(1.0))))
    return p


HAND_PRESS = -45       # wrist angle (deg) for a flat palm on the ledge

LADDER_Y = -0.27        # rung plane 0.27 m in front of the root
LADDER_RAIL = 0.33      # the hands hold the rails (beside the big head)


def ladder(f, frames=32):
    t = f / frames

    def foot(u):
        u %= 1.0
        if u < 0.5:
            return (0.03, -0.10, 0.15 * (1 - u / 0.5), 0.0)
        w = (u - 0.5) / 0.5
        return (0.03, -0.10 - 0.02 * math.sin(math.pi * w), 0.15 * ease(w) + 0.03 * math.sin(math.pi * w), 0.0)

    def hand(u):
        """Knuckle on the rung while gripping (moves down with the in-place climb), then reach up."""
        u %= 1.0
        if u < 0.5:
            y, z = LADDER_Y, 1.08 - 0.15 * (u / 0.5)
        else:
            w = (u - 0.5) / 0.5
            y, z = LADDER_Y + 0.07 * math.sin(math.pi * w), 0.93 + 0.15 * ease(w)
        return (LADDER_RAIL, y, z, 1.0, 0.2, -0.6, 1.0)

    def arm_reach(u):
        u %= 1.0
        k = u / 0.5 if u < 0.5 else 1 - ease((u - 0.5) / 0.5)
        rx = -150 + 90 * k                       # FK fallback only (IK weight 1)
        return arm(rx, 14 * math.cos(math.radians(rx))), arm(-20 - 70 * k)

    uL, lL = arm_reach(t + 0.5)
    uR, lR = arm_reach(t)
    return P(pelvis=(0.01 * wave(t, 1), 0.02, -0.01), spine=(6, 0, 3 * wave(t, 1)), head=(-12, 0, 0),
             uarm_L=uL, larm_L=lL, uarm_R=uR, larm_R=lR, fing_LR=curl(70), thumb_LR=curl(45),
             handik_L=hand(t + 0.5), handik_R=hand(t),
             foot_L=foot(t), foot_R=foot(t + 0.5), eyes=(0.95, 0, 0.004))


@anim("LadderUp", 32, True, "Leiter hochklettern (Controller steigt ca. 0,28 m/s)", "Phase6", "in-place, vertikal")
def ladder_up(f):
    return ladder(f)


@anim("LadderDown", 32, True, "Leiter hinunterklettern", "Phase6", "in-place, vertikal")
def ladder_down(f):
    return ladder(32 - f)


@anim("Vault", 32, False, "über ein niedriges Hindernis flanken", "Phase6")
def vault(f):
    return keyed(f, [
        (0, STAND),
        (6, P(pelvis=(0, 0, -0.07), spine=(30, 0, 0), head=(-10, 0, 0), uarm_LR=arm(-70, 8), larm_LR=arm(-10),
              fing_LR=curl(10))),
        (12, P(pelvis=(0.05, 0, 0.20), spine=(24, 0, -10), head=(-6, 0, 0), uarm_LR=arm(-52, 6), larm_LR=arm(-4),
               foot_LR=(0.04, 0.0, 0.27, -10), eyes=(1.1, 0, 0.002)), "out"),
        (20, P(pelvis=(-0.02, 0, 0.12), spine=(8, 0, 6), uarm_LR=arm(10, 40), larm_LR=arm(-20),
               foot_LR=(0.02, -0.04, 0.10, -6))),
        (25, P(pelvis=(0, 0, -0.10), spine=(14, 0, 0), uarm_LR=arm(-20, 25), larm_LR=arm(-30)), "in"),
        (32, STAND),
    ])

# ================================ Phase 7: game tempo and crouching ================================


def fast_gait(f, frames, stride, stance, lift, kick, lean, pelvis_z, swing, bob_amp, tilt=4):
    t = f / frames
    c = math.cos(2 * math.pi * t)
    feet = {}
    for side, off in (("L", 0.0), ("R", 0.5)):
        feet[side] = gait_foot(t + off, stride, stance, lift, -4, 10, kick=kick, outward=0.006)
    bob = math.cos(4 * math.pi * (t - stance / 2))
    return P(
        pelvis=(0.008 * math.sin(2 * math.pi * t), 0.010, pelvis_z - bob_amp * bob),
        pelvis_rot=(tilt, 0, -10 * c),
        spine=(lean + 4 - tilt + 2 * bob, 0, 10 * c),
        head=(-lean + 2 - 2 * bob, 0, -4 * c),
        uarm_L=arm(swing * c, 10),
        uarm_R=arm(-swing * c, 10),
        larm_L=arm(-84 + 14 * c),
        larm_R=arm(-84 - 14 * c),
        hand_LR=arm(-10),
        shoulder_L=(0, -4 * max(0.0, -c), 0),
        shoulder_R=(0, -4 * max(0.0, c), 0),
        fing_LR=curl(78),
        thumb_LR=curl(48),
        foot_L=feet["L"],
        foot_R=feet["R"],
        eyes=(0.92, 0, 0.003),
    )


@anim("RunFast", 12, True, "schneller Lauf mit langer Flugphase (Grundtempo ca. 3 m/s)", "Phase7",
      "in-place; Animator-Tempo = Geschwindigkeit / naturalSpeed")
def run_fast(f):
    return fast_gait(f, 12, 0.38, 0.30, 0.058, 0.0, 20, -0.084, 48, 0.016)


@anim("Sprint", 10, True, "Cartoon-Sprint, stark vorgebeugt, Arme pumpen (Grundtempo ca. 4,2 m/s)", "Phase7",
      "in-place; Animator-Tempo = Geschwindigkeit / naturalSpeed")
def sprint(f):
    return fast_gait(f, 10, 0.40, 0.28, 0.050, 0.0, 28, -0.090, 58, 0.014, tilt=-8)


CROUCH_DEEP = P(pelvis=(0, 0.100, -0.232), pelvis_rot=(-14, 0, 0), spine=(46, 0, 0), head=(10, 0, 0),
                shoulder_LR=(0, 4, 0), uarm_LR=arm(-30, 22), larm_LR=arm(-50), hand_LR=arm(-10), fing_LR=curl(40),
                foot_LR=(0.150, 0.0, 0.0, 0.0), footyaw_L=(32,), footyaw_R=(-32,), eyes=(0.9, 0, 0.004))


@anim("CrouchDeep", 40, True, "tiefes Ducken: kompakt zusammengefaltet für niedrige Durchgänge", "Phase7",
      "Kapsel: siehe clips.CrouchDeep.height")
def crouch_deep(f):
    w = wave(f / 40.0, 1)
    return with_(CROUCH_DEEP, pelvis=(0, 0.100, -0.232 - 0.003 * w), spine=(46 + w, 0, 0), head=(10 - w, 0, 0))


@anim("CrouchWalk", 28, True, "geduckt schleichen (Grundtempo ca. 0,3 m/s)", "Phase7",
      "in-place; Animator-Tempo = Geschwindigkeit / naturalSpeed")
def crouch_walk(f):
    t = f / 28.0
    c = math.cos(2 * math.pi * t)
    feet = {}
    for side, off in (("L", 0.0), ("R", 0.5)):
        feet[side] = gait_foot(t + off, 0.20, 0.66, 0.028, -4, 6, outward=0.075)
    return P(
        pelvis=(0.016 * math.sin(2 * math.pi * t), 0.050, -0.135 - 0.006 * math.cos(4 * math.pi * t)),
        pelvis_rot=(-6, 0, -5 * c),
        spine=(44 + math.cos(4 * math.pi * t), 0, 5 * c),
        head=(-30, 0, -3 * c),              # 1.14: the taller head would touch backpacks and wings at -34
        shoulder_LR=(0, 2, 0),
        uarm_L=arm(-26 + 12 * c, 14),
        uarm_R=arm(-26 - 12 * c, 14),
        larm_LR=arm(-50),
        hand_LR=arm(-10),
        fing_LR=curl(38),
        foot_L=feet["L"],
        foot_R=feet["R"],
        eyes=(0.85, 0.006 * c, 0.003),
    )


# ================================ Phase 8 (1.16): crawling under the 0.65 m crouch capsule ================================
# Belly crawl: the robot lies face down, forearms pull, toes push. The big head decides the height: chest close to the
# ground, head tilted a little forward. Game: crouch capsule 0.65 m (decision 13.10.2026), clips stay below 0.62 m.
CRAWL_BASE = dict(pelvis=(0, -0.12, -0.30), pelvis_rot=(84, 0, 0), spine=(4, 0, 0), head=(-26, 0, 0),
                  shoulder_LR=(0, 6, 0), hand_LR=arm(-10), fing_LR=curl(55), thumb_LR=curl(35))
CRAWL_HAND = (0.43, -0.72, 0.075)    # resting knuckle target beside the head (x out, y forward = minus, z)
CRAWL_POLE = (1.0, 0.0, 1.0)         # elbows out and up
CRAWL_FOOT = (0.03, 0.47, 0.004, 85)  # on the toes, legs stretched back
CRAWL_FRAMES = 40
CRAWL_REACH = 0.22                   # metres a hand pulls per stroke
CRAWL_STANCE = 0.55                  # share of the cycle a hand lies on the ground


def crawl_pose(**kw):
    p = dict(CRAWL_BASE)
    p.update(kw)
    return P(**p)


@anim("CrawlIdle", 60, True, "bäuchlings liegen und atmen (Kriechhaltung)", "Phase8")
def crawl_idle(f):
    t = f / 60.0
    br = math.sin(2 * math.pi * t)
    x, y, z = CRAWL_HAND
    return crawl_pose(pelvis=(0, -0.12, -0.30 + 0.004 * br), handik_LR=(x, y, z, *CRAWL_POLE, 1.0),
                      foot_LR=CRAWL_FOOT, eyes=(1.0, 0, 0.004))


def crawl_hand(u):
    """Knuckle target of one hand over the stroke (u = 0..1): pulls back on the ground, then reaches forward."""
    x, y0, z0 = CRAWL_HAND
    front = y0 - CRAWL_REACH / 2
    if u < CRAWL_STANCE:
        return (x, front + CRAWL_REACH * u / CRAWL_STANCE, z0)
    s = (u - CRAWL_STANCE) / (1 - CRAWL_STANCE)
    return (x, front + CRAWL_REACH * (1 - ease(s)), z0 + 0.07 * math.sin(math.pi * s))


@anim("Crawl", CRAWL_FRAMES, True, "robben: Unterarme ziehen im Wechsel, Fußspitzen schieben (Grundtempo ca. 0,3 m/s)",
      "Phase8", "in-place; Animator-Tempo = Geschwindigkeit / naturalSpeed (gemessen an den Händen)", gait="hands")
def crawl(f):
    t = f / CRAWL_FRAMES
    c = math.cos(2 * math.pi * t)
    hands = {s: crawl_hand((t + off) % 1.0) for s, off in (("L", 0.0), ("R", 0.5))}
    ox, oy, lift, pitch = CRAWL_FOOT
    # each foot pushes with the opposite hand (the left foot while the right hand pulls)
    feet = {}
    for s, off in (("L", 0.5), ("R", 0.0)):
        w = 2 * math.pi * (t + off)
        feet[s] = (ox, oy + 0.04 * math.cos(w), lift + 0.012 * max(0.0, math.sin(w)), pitch)
    return crawl_pose(pelvis=(0.010 * c, -0.12, -0.30 + 0.004 * math.cos(4 * math.pi * t)),
                      pelvis_rot=(84, 0, -4 * c), spine=(4, 0, 5 * c), head=(-26, 0, -4 * c),
                      handik_L=(*hands["L"], *CRAWL_POLE, 1.0), handik_R=(*hands["R"], *CRAWL_POLE, 1.0),
                      foot_L=feet["L"], foot_R=feet["R"], eyes=(1.0, 0.005 * c, 0.004))


# ---------------------------------- 1.12 type emotes ----------------------------------

@anim("HatTip", 56, False, "Hut lüften mit Verbeugung (Typ Gentleman; der Kopf-Sockel liegt in der Hand)", "Phase5")
def hat_tip(f):
    # the arms cannot reach the crown of the head: the hat lifts and slides towards the hand that comes up beside the
    # head, then rides on the hand (ATTACHMENTS) through the bow and back
    lifted = (0, -24, -0.210, -0.053)    # the right brim edge rests on the fingers (1.14: head wider and taller)
    reach = P(head=(-4, 6, 4), spine=(-2, 0, -3), hat=lifted, fing_R=curl(55), thumb_R=curl(45),
              handik_R=(0.385, -0.040, 1.180, 1.0, 0.3, -0.2, 1.0), uarm_L=arm(4, 6))
    sweep = P(pelvis=(0, 0.03, -0.022), pelvis_rot=(6, 0, 0), spine=(20, 0, 0), head=(12, 0, 0), hat=lifted,
              fing_R=curl(55), thumb_R=curl(45), handik_R=(0.450, -0.200, 0.900, 1.0, 0.3, -0.2, 1.0),
              uarm_L=arm(10, 4), larm_L=arm(-12), eyes=(0.35, 0, -0.004))
    return keyed(f, [(0, STAND), (HAT_GRAB, reach), (22, sweep), (34, sweep), (44, reach), (HAT_RELEASE, reach),
                     (56, STAND)])


@anim("FlagWave", 60, False, "Fahne schwenken (Typ King; Gegenstand in der rechten Hand bleibt sichtbar)", "Phase5")
def flag_wave(f):
    t = f / 60.0
    env = ease(min(1.0, f / 10.0)) * ease(min(1.0, (60 - f) / 12.0))
    sw = math.sin(2 * math.pi * 3 * t) * env
    return P(spine=(-4 * env, 0, -6 * env), head=(-10 * env, -4 * env, -12 * env),
             uarm_R=arm(-100 * env, 22 * env + 10 * sw), larm_R=arm(-8 * env, 0, 32 * sw), hand_R=arm(0, 0, 0),
             fing_R=curl(15 + 50 * env), thumb_R=curl(10 + 40 * env), uarm_L=arm(4 * env, 10 * env),
             eyes=(1.0 - 0.1 * env, -0.004 * env, 0.004 * env))


# ---------------------------------- per-clip game data ----------------------------------
# Everything the game needs besides the bone curves. Exported to robot_config.json ("clips") and the viewer.
# Expression/display keys: [frame, name]; "$base" = the look's own choice. Emission keys: [frame, factor], linear.

def _flicker(f0, f1, a, b, step=2):
    return [[k, a if (k - f0) // step % 2 == 0 else b] for k in range(f0, f1, step)]


CLIP_EXPRESSIONS = {
    "Death": _flicker(0, 27, "Glitch", "$base") + [[27, "Off"]],
    "Hit": [[0, "Angry"], [10, "$base"]], "HitHeavy": [[0, "Angry"], [10, "$base"]],
    "Respawn": [[0, "Off"], [17, "Loading"], [24, "$base"]],
    "SitDown": [[0, "$base"], [31, "Cute"]], "PickUp": [[0, "$base"], [21, "Love"]],
}
for _names, _expr in (
        (("Celebrate", "DanceBob", "DanceShuffle", "DanceDisco", "DanceSway", "Wave", "Talk", "NodYes", "HatTip",
          "FlagWave"), "Happy"),
        (("DanceRobot",), "Pixel"), (("DanceWave", "DanceSpin", "DanceMoonwalk", "DoubleJump", "Vault"), "Excited"),
        (("Electrocuted",), "Glitch"), (("GasCough",), "Crying"), (("IdleYawn",), "Sleepy"), (("Sleep",), "Sleeping"),
        (("Interact", "TerminalTyping", "CrouchWalk", "CrouchDeep"), "Focused"),
        (("Scan", "ToolAim", "ToolUse", "Point"), "Target"),
        (("Fall", "SlopeSlide", "EdgeBalance", "PortalEnter", "LedgeGrab"), "Surprised"),
        (("Push", "PushStart", "PushStep", "MudWalk", "ClimbUp", "LadderUp", "LadderDown", "HangIdle", "ShimmyLeft",
          "ShimmyRight", "HangSwing", "RunFast", "Sprint"), "Determined"),
        (("Listen", "SitIdle"), "Cute"), (("Clap",), "Laugh"), (("Bow",), "Shy"), (("Think", "IdleScratch"), "Question"),
        (("Shrug",), "Skeptical"), (("ShakeNo",), "Sad"), (("IdleFootTap",), "LookLeft")):
    for _n in _names:
        CLIP_EXPRESSIONS[_n] = [[0, _expr]]

CLIP_DISPLAYS = {
    "Death": [[0, "Lightning"], [21, "Warning"]],
    "Respawn": [[0, "Loading"], [24, "$base"]],
}
for _names, _disp in (
        (("Electrocuted", "Hit", "HitHeavy", "Sprint"), "Lightning"), (("GasCough", "MudWalk"), "Warning"),
        (("Celebrate", "DanceSpin"), "Star"), (("Sleep",), "Clock"), (("Scan",), "Signal"),
        (("Run", "RunFast", "LadderUp", "ClimbUp"), "Heart"), (("Talk", "Wave"), "Hello"), (("Think",), "Loading")):
    for _n in _names:
        CLIP_DISPLAYS[_n] = [[0, _disp]]

# Emission factor for eyes, chest display and lights ("power"): Death switches the robot off, Respawn on.
CLIP_EMISSION = {
    "Death": [[0, 1.0], [18, 1.0], [20, 0.4], [21, 1.0], [23, 0.25], [24, 0.8], [27, 0.15], [36, 0.03], [64, 0.0]],
    "Respawn": [[0, 0.0], [8, 0.35], [10, 0.05], [12, 0.8], [14, 0.2], [17, 1.0], [24, 1.15], [30, 1.0]],
    "Electrocuted": [[0, 1.0], [2, 1.8], [4, 0.3], [6, 1.6], [9, 0.5], [12, 1.9], [15, 0.4], [18, 1.0]],
    "Hit": [[0, 1.0], [1, 1.7], [5, 1.0]],
    "HitHeavy": [[0, 1.0], [1, 2.0], [4, 0.4], [7, 1.3], [10, 1.0]],
    "Sleep": [[0, 0.35], [60, 0.28], [120, 0.35]],
    "IdleYawn": [[0, 1.0], [30, 0.7], [60, 0.7], [90, 1.0]],
    "Celebrate": [[0, 1.0], [20, 1.4], [48, 1.0]],
}

# Semantic events (besides the measured footsteps) for Unity animation events / sounds.
CLIP_EVENTS = {
    "Jump": [[10, "TakeOff"]], "Land": [[0, "Impact"]], "Interact": [[19, "Press"]], "Death": [[37, "Thud"]],
    "DoubleJump": [[2, "Boost"]], "HitHeavy": [[1, "Impact"]], "Clap": [[8, "Clap"], [23, "Clap"]],
    "ClimbUp": [[42, "Arrive"]], "LedgeGrab": [[9, "Grab"]], "Vault": [[12, "Hop"], [25, "Land"]],
}

# Helper geometry the viewer shows to check contacts (the game provides the real geometry).
CLIP_PROPS = {n: "ledge" for n in ("LedgeGrab", "HangIdle", "ShimmyLeft", "ShimmyRight", "ClimbUp")}
CLIP_PROPS.update({"HangSwing": "bar", "LadderUp": "ladder", "LadderDown": "ladder", "Push": "box",
                   "PushStart": "box", "PushStep": "box"})


def clip_meta(a):
    """Static per-clip data (measured data is added by measure_clips)."""
    def check(keys, valid):
        for _, v in keys:
            assert v == "$base" or v in valid, "unknown name %s in clip %s" % (v, a.name)
        return keys
    return {
        "name": a.name, "group": a.group, "category": clip_category(a.name), "frames": a.frames, "fps": FPS,
        "loop": a.loop, "gait": a.gait,
        "hideHeldItem": a.name in HIDE_HELD_ITEM,
        "expression": check(CLIP_EXPRESSIONS.get(a.name, []), make_textures.EXPRESSIONS),
        "display": check(CLIP_DISPLAYS.get(a.name, []), make_textures.DISPLAYS),
        "emission": CLIP_EMISSION.get(a.name, []),
        "events": CLIP_EVENTS.get(a.name, []),
        "prop": None,
    }


def prop_info(name, hands):
    """Helper geometry for the viewer, derived from the clip's constants and measured hand positions."""
    kind = CLIP_PROPS.get(name)
    if kind == "ledge":
        return {"type": "ledge", "top": LEDGE_TOP, "edge": LEDGE_EDGE}
    if kind == "ladder":
        return {"type": "ladder", "forward": -LADDER_Y, "rail": LADDER_RAIL, "spacing": 0.30}
    if kind in ("bar", "box") and hands:
        up = (hands["L"][0][1] + hands["R"][0][1]) / 2
        fwd = (hands["L"][0][2] + hands["R"][0][2]) / 2
        if kind == "bar":                   # bar centre just below the knuckles
            return {"type": "bar", "up": round(up - 0.03, 3), "forward": round(fwd + 0.005, 3)}
        return {"type": "box", "forward": round(fwd + 0.01, 3), "top": 0.9}
    return None


def add_preview_tool(arm_ob, coll):
    """Flashlight proxy parented to Prop_R - only for preview renders, never exported."""
    bpy.context.view_layer.update()
    pb = arm_ob.pose.bones["Prop_R"]
    m = arm_ob.matrix_world @ pb.matrix
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=14, radius1=0.020, radius2=0.026, depth=0.13)
    bmesh.ops.translate(bm, vec=(0, 0, 0.045), verts=bm.verts[:])
    me = bpy.data.meshes.new("Preview_Tool")
    bm.to_mesh(me)
    bm.free()
    mat = bpy.data.materials.new("Preview_Tool")
    if mat.node_tree is None:
        mat.use_nodes = True
    b = mat.node_tree.nodes.get("Principled BSDF")
    b.inputs["Base Color"].default_value = (0.25, 0.27, 0.3, 1)
    b.inputs["Metallic"].default_value = 0.8
    b.inputs["Emission Color"].default_value = (0.2, 0.9, 1.0, 1)
    b.inputs["Emission Strength"].default_value = 0.6
    me.materials.append(mat)
    tool = bpy.data.objects.new("Preview_Tool", me)
    coll.objects.link(tool)
    # cone axis (+Z) along the bone (+Y)
    tool.matrix_world = m @ Matrix.Rotation(math.radians(-90), 4, "X")
    tool.parent = arm_ob
    tool.parent_type = "BONE"
    tool.parent_bone = "Prop_R"
    bone_tail = arm_ob.matrix_world @ pb.matrix @ Matrix.Translation((0, pb.bone.length, 0))
    tool.matrix_parent_inverse = bone_tail.inverted()
    tool.hide_render = True
    return tool


def write_animation_table(out, data=None):
    """ANIMATIONS.md next to the outputs: always in sync with the generated clips (and their measured data)."""
    data = data or {}
    lines = ["# Cube Escape Roboter – Animationsliste (Generator %s)" % VERSION, "",
             "Automatisch erzeugt von `generate_robot.py`. 30 fps, alle Clips in-place (außer *Root Motion*).",
             "Tempo = Grundtempo des In-place-Clips (Animator-Tempo = Spielgeschwindigkeit / Tempo), "
             "Höhe = kleinste/größte Körperhöhe im Clip, Schritte = Frames mit Fußaufsatz. "
             "Alle Werte maschinenlesbar in `robot_config.json` › `clips`.", ""]
    by_name = {a.name: a for a in ANIMS}
    for cat, (label, names) in CLIP_CATEGORIES.items():
        clips = [by_name[n] for n in names if n in by_name]
        if not clips:
            continue
        lines += ["## %s (`%s`)" % (label, cat), "",
                  "| Clip | Frames | Loop | Tempo | Höhe | Schritte | Unity | Beschreibung |",
                  "|---|---|---|---|---|---|---|---|"]
        for a in clips:
            d = data.get(a.name, {})
            speed = "%.2f m/s" % d["naturalSpeed"] if d.get("naturalSpeed") else "–"
            height = "%.2f–%.2f m" % (d["height"]["min"], d["height"]["max"]) if "height" in d else "–"
            steps = ", ".join(str(f) for f, _ in d.get("footsteps", [])) or "–"
            lines.append("| %s | 0–%d | %s | %s | %s | %s | %s | %s |" % (
                a.name, a.frames, "ja" if a.loop else "nein", speed, height, steps, a.unity or "–", a.desc))
        lines.append("")
    with open(os.path.join(out, "ANIMATIONS.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def build_animations(arm_ob, quiet=False):
    solver = Solver(arm_ob)
    actions = {}
    for a in ANIMS:
        act, slot = write_action(arm_ob, solver, a.name, a.frames, a.fn, a.loop)
        actions[a.name] = act
        LOG.log(logging.DEBUG if quiet else logging.INFO, "  action %-15s %3d frames  loop=%-5s %s  (%s)", a.name, a.frames, a.loop, a.group, a.desc)
    # Unity-friendly default: rest pose action assigned last is Idle
    assign_action(arm_ob, actions["Idle"])
    # NLA tracks keep every action visible and editable in the .blend
    ad = arm_ob.animation_data
    for name in reversed([a.name for a in ANIMS]):
        track = ad.nla_tracks.new()
        track.name = name
        strip = track.strips.new(name, 0, actions[name])
        strip.action_slot = actions[name].slots[0]
        track.mute = True
    return actions


# Clips in which a held item (Prop_R) is hidden by the game: climbing, hanging, crawling, thinking with the hand at the
# chin.
HIDE_HELD_ITEM = {"Think", "LedgeGrab", "HangIdle", "ShimmyLeft", "ShimmyRight", "HangSwing", "ClimbUp", "LadderUp",
                  "LadderDown", "Vault", "PickUp", "TerminalTyping", "Clap", "Wave", "EdgeBalance", "HatTip", "CrawlIdle",
                  "Crawl"}


__all__ = [_n for _n in dir() if not _n.startswith("__")]
