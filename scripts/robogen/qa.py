"""QA of the clips (self-intersection, ground, contacts) and clip measurements."""

from robogen.clips import *  # noqa: F401,F403 - one shared namespace, see robogen/__init__.py


# ----------------------------------------------------------------------------------------------
# QA
# ----------------------------------------------------------------------------------------------


def group_faces(ob):
    me = ob.data
    vg_of_vertex = np.full(len(me.vertices), -1, dtype=np.int32)
    for v in me.vertices:
        if v.groups:
            vg_of_vertex[v.index] = v.groups[0].group
    names = {g.index: g.name for g in ob.vertex_groups}
    groups = {}
    for p in me.polygons:
        g = vg_of_vertex[p.vertices[0]]
        groups.setdefault(names[g], []).append(list(p.vertices))
    return groups


def evaluated_coords(ob):
    dg = bpy.context.evaluated_depsgraph_get()
    ev = ob.evaluated_get(dg)
    me = ev.to_mesh()
    co = np.empty(len(me.vertices) * 3, dtype=np.float32)     # the native type: a plain copy, no conversion
    me.vertices.foreach_get("co", co)
    ev.to_mesh_clear()
    mw = np.array(ob.matrix_world)
    co = co.astype(np.float64).reshape(-1, 3) @ mw[:3, :3].T + mw[:3, 3]
    return co


class Topology:
    """Per vertex group: its vertex indices and its polygons in local indices - computed once per mesh. order/starts
    list all groups' vertices back to back, so the bounding boxes of all groups are two numpy calls per frame."""

    def __init__(self, groups):
        self.groups = {}
        for g, polys in groups.items():
            idx = sorted({i for p in polys for i in p})
            remap = {o: n for n, o in enumerate(idx)}
            self.groups[g] = (np.array(idx, dtype=np.int64), [[remap[i] for i in p] for p in polys])
        self.index = {g: i for i, g in enumerate(self.groups)}
        self.order = np.concatenate([idx for idx, _ in self.groups.values()])
        self.starts = np.cumsum([0] + [len(idx) for idx, _ in self.groups.values()])[:-1]

    def pair_index(self, pairs):
        return (np.array([self.index[a] for a, _ in pairs], dtype=np.int64),
                np.array([self.index[b] for _, b in pairs], dtype=np.int64))


class GroupTrees:
    """BVH trees of the vertex groups of one mesh state, built only when a pair needs them. Groups whose
    bounding boxes are apart cannot overlap, so the box test answers most pairs without a tree (same result
    as testing every pair; the margin covers float32 rounding inside the trees)."""

    MARGIN = 1e-5

    def __init__(self, co, topo):
        self.co, self.topo, self.trees = co, topo, {}
        pts = co[topo.order]
        self.lo = np.minimum.reduceat(pts, topo.starts, axis=0) - self.MARGIN
        self.hi = np.maximum.reduceat(pts, topo.starts, axis=0) + self.MARGIN

    def tree(self, g):
        t = self.trees.get(g)
        if t is None:
            idx, polys = self.topo.groups[g]
            t = self.trees[g] = BVHTree.FromPolygons(self.co[idx].tolist(), polys)
        return t

    def boxes_touch(self, ia, ib):
        """Box test for many pairs at once (index arrays): False where the pair cannot overlap."""
        return ~((self.lo[ia] > self.hi[ib]).any(axis=1) | (self.lo[ib] > self.hi[ia]).any(axis=1))

    def overlap(self, a, b, boxes_checked=False):
        if not boxes_checked:
            ia, ib = self.topo.index[a], self.topo.index[b]
            if (self.lo[ia] > self.hi[ib]).any() or (self.lo[ib] > self.hi[ia]).any():
                return False
        return bool(self.tree(a).overlap(self.tree(b)))


# Contacts that are intended: fabric accessories may be squeezed by the drooping head.
ACCEPTED_OVERLAPS = {("*", "Head", "Socket_Neck"), ("*", "Eyes", "Socket_Neck"),
                     # held items are gripped by the fingers
                     ("*", "Fingers_R", "Prop_R"), ("*", "Prop_R", "Thumb_R"),
                     ("*", "Fingers_L", "Prop_L"), ("*", "Prop_L", "Thumb_L"),
                     # the hat rides on the right hand
                     ("HatTip", "Fingers_R", "Socket_Head"), ("HatTip", "Hand_R", "Socket_Head"),
                     ("HatTip", "Socket_Head", "Thumb_R")}


# Joints that open on purpose: the hat leaves the head in "HatTip".
ACCEPTED_DETACH = {("HatTip", "Socket_Head", "Head")}


# Bone groups that may touch the floor in a clip (1.19): rolling over the head and back, the accessories on the head and
# back sockets and the taller TV head squash into the floor instead of lifting every robot off it (clips.ROLL_SQUASH).
ACCEPTED_GROUND = {"LandRoll": ("Socket_Head", "Antenna", "Socket_Back", "Cape", "Head")}


def type_emotes():
    """Clips that belong to one robot type (ROBOT_TYPES[...]["emote"]): checked and played only with that type."""
    names = {a.name for a in ANIMS}         # a --groups filter may have dropped them
    return {spec["emote"]: name for name, spec in ROBOT_TYPES.items() if spec.get("emote") in names}


def run_qa(ob, arm_ob, actions, label="Classic", quiet=False, clips=None):
    """QA of one mesh over the given clips (default: all, in ANIMS order): rest-pose baseline once, then every
    frame of every clip. Returns {"rest_min_z", "rest_height", "actions": {clip: entry}}."""
    scene = bpy.context.scene
    groups = group_faces(ob)
    bones = arm_ob.data.bones
    parent = {b.name: (b.parent.name if b.parent else None) for b in bones}
    names = sorted(groups)
    allowed = set()
    for a in names:
        for b in names:
            if parent.get(a) == b or parent.get(b) == a:
                allowed.add((a, b))
    allowed.add(("Fingers_L", "Thumb_L"))
    allowed.add(("Fingers_R", "Thumb_R"))
    # baseline: pairs already touching in rest pose are joints by construction
    ad = arm_ob.animation_data
    saved = ad.action
    ad.action = None
    for pb in arm_ob.pose.bones:
        pb.location = (0, 0, 0)
        pb.rotation_quaternion = (1, 0, 0, 0)
        pb.scale = (1, 1, 1)
    bpy.context.view_layer.update()
    rest_co = evaluated_coords(ob)
    topo = Topology(groups)
    rest_trees = GroupTrees(rest_co, topo)
    baseline = set()
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if rest_trees.overlap(a, b):
                baseline.add((a, b))
    # the eye screen is a decal (it shrinks when blinking), not a joint
    joints = [(a, b) for a in names for b in names if parent.get(a) == b and a != "Eyes"]
    qa = {"rest_min_z": float(rest_co[:, 2].min()), "rest_height": float(rest_co[:, 2].max() - rest_co[:, 2].min()),
          "actions": {}}
    LOG.log(logging.DEBUG if quiet else logging.INFO, "  [%s] rest pose: height %.3f m, min z %.4f m, baseline "
            "joint overlaps %d", label, qa["rest_height"], qa["rest_min_z"], len(baseline))
    vgroup = np.empty(len(ob.data.vertices), dtype=object)
    for g, polys in groups.items():
        for poly in polys:
            vgroup[poly] = g
    foot_groups = {"L": "Foot_L", "R": "Foot_R"}
    foot_idx = {s: sorted({i for p in groups[foot_groups[s]] for i in p}) for s in foot_groups}
    wanted = None if clips is None else set(clips)
    for name, frames, loop, fn, desc, *_ in ANIMS:
        if wanted is not None and name not in wanted:
            continue
        act = actions[name]
        assign_action(arm_ob, act)
        min_z = 1e9
        min_z_where = None
        new_overlaps = {}
        detached = {}
        flight_frames = 0
        stance_speed = []
        prev_foot = None
        first = last = None
        pairs = [(a, b) for i, a in enumerate(names) for b in names[i + 1:]
                 if not ((a, b) in allowed or (a, b) in baseline or (name, a, b) in ACCEPTED_OVERLAPS or
                         ("*", a, b) in ACCEPTED_OVERLAPS or (name in HIDE_HELD_ITEM and "Prop_R" in (a, b)))]
        joint_pairs = [(child, par) for child, par in joints
                       if ((min(child, par), max(child, par)) in baseline or (child, par) in baseline or
                           (par, child) in baseline) and (name, child, par) not in ACCEPTED_DETACH]
        pa, pb = topo.pair_index(pairs)
        ja, jb = topo.pair_index(joint_pairs)
        squash = ACCEPTED_GROUND.get(name)
        floor_mask = None if not squash else np.array([g not in squash for g in vgroup])
        for f in range(0, frames + 1):
            scene.frame_set(f)
            co = evaluated_coords(ob)
            if f == 0:
                first = co.copy()
            if f == frames:
                last = co
            zs = co[:, 2] if floor_mask is None else np.where(floor_mask, co[:, 2], np.inf)
            iz = int(zs.argmin())
            if co[iz, 2] < min_z:
                min_z = float(co[iz, 2])
                min_z_where = "%s@%d" % (vgroup[iz], f)
            trees = GroupTrees(co, topo)
            for k in np.flatnonzero(trees.boxes_touch(pa, pb)):
                a, b = pairs[k]
                if trees.overlap(a, b, boxes_checked=True):
                    new_overlaps.setdefault("%s/%s" % (a, b), []).append(f)
            touch = trees.boxes_touch(ja, jb)
            for k, (child, par) in enumerate(joint_pairs):
                if not (touch[k] and trees.overlap(child, par, boxes_checked=True)):
                    detached.setdefault("%s/%s" % (child, par), []).append(f)
            feet_z = {s: float(co[foot_idx[s], 2].min()) for s in foot_idx}
            if all(z > 0.004 for z in feet_z.values()):
                flight_frames += 1
            foot_pos = {s: co[foot_idx[s]].mean(axis=0) for s in foot_idx}
            if prev_foot is not None:
                for s in foot_idx:
                    if feet_z[s] < 0.003 and prev_feet_z[s] < 0.003:
                        stance_speed.append(abs(foot_pos[s][1] - prev_foot[s][1]) * FPS)
            prev_foot, prev_feet_z = foot_pos, feet_z
        loop_err = float(np.abs(first - last).max()) if loop else None
        entry = {
            "frames": frames,
            "loop": loop,
            "min_z": round(min_z, 4),
            "min_z_at": min_z_where,
            "new_overlaps": {k: [v[0], v[-1], len(v)] for k, v in new_overlaps.items()},
            "detached_joints": {k: [v[0], v[-1], len(v)] for k, v in detached.items()},
            "flight_frames": flight_frames,
            "loop_error": loop_err,
            "stance_speed_mps": round(float(np.median(stance_speed)), 3) if stance_speed else None,
        }
        qa["actions"][name] = entry
        log_qa_entry(label, name, entry, quiet)
    ad.action = saved
    return qa


def qa_problem(entry):
    return entry["min_z"] < -0.006 or bool(entry["new_overlaps"]) or bool(entry["detached_joints"])


def log_qa_entry(label, name, entry, quiet=False):
    loop_err = entry["loop_error"]
    lvl = logging.DEBUG if quiet else logging.INFO
    if qa_problem(entry) or (entry["loop"] and loop_err > 1e-4):
        lvl = logging.WARNING
    LOG.log(lvl, "  QA[%s] %-9s min_z=%+.4f (%s) overlaps=%s detached=%s flight=%d loop_err=%s speed=%s", label, name,
            entry["min_z"], entry["min_z_at"], entry["new_overlaps"] or "-", entry["detached_joints"] or "-",
            entry["flight_frames"], "-" if loop_err is None else "%.1e" % loop_err, entry["stance_speed_mps"])


def char_space(v):
    """Blender world (front -Y, Z up) -> character space [right, up, forward] (= Unity local space)."""
    return [round(-v[0], 4) + 0.0, round(v[2], 4) + 0.0, round(-v[1], 4) + 0.0]    # + 0.0: no "-0.0"


def contact_ranges(flags, loop):
    """Frames with ground contact as [[first, last], ...]; footsteps = first frames (cyclic for loops)."""
    ranges, start = [], None
    for f, on in enumerate(flags):
        if on and start is None:
            start = f
        if not on and start is not None:
            ranges.append([start, f - 1])
            start = None
    if start is not None:
        ranges.append([start, len(flags) - 1])
    steps = []
    for a, b in ranges:
        if a == 0 and not loop:
            continue                # standing at the start is not a step
        prev = flags[a - 1] if a > 0 else (flags[-2] if len(flags) > 1 else False)
        if not prev and not (loop and a == len(flags) - 1):
            steps.append(a)
    return ranges, steps


# Servo sound events (1.13): a joint group starts a movement when its summed angular speed rises above SERVO_ON and
# to SERVO_RISE times the lowest speed since the last movement; the movement ends when the speed falls below
# SERVO_FALL of its peak (so every step or wave gets its own event). Intensity = peak / SERVO_FULL (0..1).
SERVO_GROUPS = collections.OrderedDict([
    ("Head", ("Head",)), ("Torso", ("Pelvis", "Spine")),
    ("Arm_L", ("Shoulder_L", "UpperArm_L", "LowerArm_L", "Hand_L")),
    ("Arm_R", ("Shoulder_R", "UpperArm_R", "LowerArm_R", "Hand_R")),
    ("Leg_L", ("UpperLeg_L", "LowerLeg_L", "Foot_L")), ("Leg_R", ("UpperLeg_R", "LowerLeg_R", "Foot_R")),
])
SERVO_ON, SERVO_RISE, SERVO_FALL, SERVO_FULL = 90.0, 1.8, 0.6, 900.0     # degrees per second, factors


def servo_events(rotations, loop):
    """rotations: per frame {bone: local quaternion}. Returns [[frame, group, intensity, frames], ...] by frame."""
    events = []
    for group, bones in SERVO_GROUPS.items():
        speed = [0.0]
        for q0, q1 in zip(rotations, rotations[1:]):
            speed.append(sum(math.degrees(q0[b].rotation_difference(q1[b]).angle) for b in bones) * FPS)
        if loop and len(speed) > 1:
            speed[0] = speed[-1]            # frame 0 continues the last frame of the cycle
        start, peak, low = None, 0.0, speed[0]
        for f, v in enumerate(speed):
            if start is None:
                low = min(low, v)
                if v > SERVO_ON and v > SERVO_RISE * low:
                    start, peak = f, v
            else:
                peak = max(peak, v)
                if v < SERVO_FALL * peak:
                    events.append([start, group, round(min(1.0, peak / SERVO_FULL), 2), f - start])
                    start, low = None, v
        if start is not None:
            events.append([start, group, round(min(1.0, peak / SERVO_FULL), 2), len(speed) - start])
    return sorted(events)


HAND_CONTACT = 0.035        # metres: a hand counts as lying on the ground (rounded fist; 0.02 flickered mid-stroke)


def measure_clips(ob, arm_ob, actions, names=None):
    """Measured game data per clip: root motion, foot contacts, footsteps, natural speed, height, hand grips.
    names: only these clips (cached_clip_data measures the ones without a stored result)."""
    scene = bpy.context.scene
    groups = group_faces(ob)
    foot_idx = {s: sorted({i for p in groups["Foot_" + s] for i in p}) for s in ("L", "R")}
    hand_idx = {s: sorted({i for g in ("Hand_" + s, "Fingers_" + s) for p in groups[g] for i in p}) for s in ("L", "R")}
    root_pb = arm_ob.pose.bones["Root"]
    grip_pb = {s: arm_ob.pose.bones["Fingers_" + s] for s in ("L", "R")}
    mw = arm_ob.matrix_world
    root_rest = arm_ob.data.bones["Root"].matrix_local.to_3x3().inverted()
    out = {}
    for a in [a for a in ANIMS if names is None or a.name in names]:
        assign_action(arm_ob, actions[a.name])
        heights, contact, speeds, hands, roots = [], {"L": [], "R": []}, [], {"L": [], "R": []}, []
        rotations = []
        prev = None
        for f in range(a.frames + 1):
            scene.frame_set(f)
            rotations.append({b: arm_ob.pose.bones[b].rotation_quaternion.copy()
                              for bones in SERVO_GROUPS.values() for b in bones})
            co = evaluated_coords(ob)
            heights.append(float(co[:, 2].max() - max(0.0, co[:, 2].min())))
            cur = {}
            for s in ("L", "R"):
                # the contacts that carry the body: the feet, or the hands for crawling (gait "hands"); they give
                # contacts, footsteps and the natural speed
                if a.gait == "hands":
                    pts = co[hand_idx[s]]
                    on = float(pts[:, 2].min()) < HAND_CONTACT
                else:
                    pts = co[foot_idx[s]]
                    on = float(pts[:, 2].min()) < 0.006
                contact[s].append(on)
                cur[s] = (on, pts.mean(axis=0))
                if prev and on and prev[s][0]:
                    speeds.append(float(np.linalg.norm((cur[s][1] - prev[s][1])[:2])) * FPS)
                hands[s].append(char_space(mw @ grip_pb[s].head))
            prev = cur
            m = mw @ root_pb.matrix
            r = m.to_3x3() @ root_rest
            roots.append((m.translation.copy(), math.atan2(r[1][0], r[0][0])))
        d = roots[-1][0] - roots[0][0]
        yaw = 0.0                   # unwrapped (DanceSpin turns a full circle)
        for (_, y0), (_, y1) in zip(roots, roots[1:]):
            yaw += math.degrees((y1 - y0 + math.pi) % (2 * math.pi) - math.pi)
        entry = {
            "rootMotion": {"move": char_space(d), "turnRight": round(-yaw, 2) + 0.0},
            "height": {"min": round(min(heights), 3), "max": round(max(heights), 3)},
            "naturalSpeed": round(float(np.median(speeds)), 3) if len(speeds) > 3 and a.loop else 0.0,
            "feet": {}, "footsteps": [],
        }
        for s in ("L", "R"):
            ranges, steps = contact_ranges(contact[s], a.loop)
            entry["feet"][s] = ranges
            entry["footsteps"] += [[f, ("Hand_" if a.gait == "hands" else "Foot_") + s] for f in steps]
        entry["footsteps"].sort()
        entry["servo"] = servo_events(rotations, a.loop)
        if a.name in CLIP_PROPS or a.group == "Phase6":
            entry["hands"] = hands
        out[a.name] = entry
    return out


def type_bounds(part_objs, cfg):
    """Rest-pose size of a configuration. coreWidth leaves out forearms, hands and held items (they swing
    freely); it is the number to compare with the character capsule diameter."""
    free = ("LowerArm_", "Hand_", "Fingers_", "Thumb_", "Prop_")
    lo, hi = [1e9] * 3, [-1e9] * 3
    core = [1e9, -1e9]
    for g in config_groups(cfg):
        ob = part_objs[g]
        names = {vg.index: vg.name for vg in ob.vertex_groups}
        mw = ob.matrix_world
        for v in ob.data.vertices:
            w = mw @ v.co
            for i in range(3):
                lo[i], hi[i] = min(lo[i], w[i]), max(hi[i], w[i])
            if not v.groups or not names[v.groups[0].group].startswith(free):
                core = [min(core[0], w.x), max(core[1], w.x)]
    return {"width": round(hi[0] - lo[0], 3), "coreWidth": round(core[1] - core[0], 3),
            "depth": round(hi[1] - lo[1], 3), "height": round(hi[2] - lo[2], 3)}


__all__ = [_n for _n in dir() if not _n.startswith("__")]
