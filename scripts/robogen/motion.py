"""Animation basics: pose helpers, the solver (IK, springs, attachments) and gait helpers."""

from robogen.forms import *  # noqa: F401,F403 - one shared namespace, see robogen/__init__.py


# ----------------------------------------------------------------------------------------------
# Animation system: poses in armature-aligned delta rotations, analytic two-bone leg IK
# ----------------------------------------------------------------------------------------------

THIGH = 0.160
SHIN = 0.150
ANKLE_H = 0.095
TOE_OFF = Vector((0, -0.200, -ANKLE_H))    # toe contact relative to ankle
HEEL_OFF = Vector((0, 0.102, -ANKLE_H))    # heel contact relative to ankle

ARM_BONES = ["Shoulder", "UpperArm", "LowerArm", "Hand", "Fingers", "Thumb"]
CHANNEL_DEFAULTS = {
    "pelvis": (0.0, 0.0, -0.012),
    "pelvis_rot": (0.0, 0.0, 0.0),
    "spine": (0.0, 0.0, 0.0),
    "head": (0.0, 0.0, 0.0),
    "eyes": (1.0, 0.0, 0.0),        # scale_z, offset_x, offset_z
    "root": (0.0, 0.0, 0.0),        # x, y (m, world), yaw (deg) - root motion for turns and spins
    "root_z": (0.0,),               # height (m) - root motion for climbing
    "hat": (0.0, 0.0, 0.0, 0.0),    # Socket_Head: pitch, roll (deg) about its base, shift x, lift z (m) - emotes
}
for _s in ("L", "R"):
    CHANNEL_DEFAULTS["shoulder_" + _s] = (0.0, 0.0, 0.0)
    CHANNEL_DEFAULTS["uarm_" + _s] = (0.0, 0.0, 0.0)
    CHANNEL_DEFAULTS["larm_" + _s] = (0.0, 0.0, 0.0)
    CHANNEL_DEFAULTS["hand_" + _s] = (0.0, 0.0, 0.0)
    CHANNEL_DEFAULTS["fing_" + _s] = (15.0,)
    CHANNEL_DEFAULTS["thumb_" + _s] = (10.0,)
    CHANNEL_DEFAULTS["foot_" + _s] = (0.0, 0.0, 0.0, 0.0)   # outward, forward(+y back), lift, pitch
    CHANNEL_DEFAULTS["footyaw_" + _s] = (0.0,)               # world yaw of the foot (deg), about its own ankle
    # arm IK: knuckle target x (outward), y (+ = back), z in world (left convention, mirrored), elbow pole, weight
    CHANNEL_DEFAULTS["handik_" + _s] = (0.30, -0.20, 1.0, 1.0, 0.0, -0.3, 0.0)


def P(**kw):
    """Pose dict with defaults. Left/right channels use left-side conventions and are mirrored."""
    pose = dict(CHANNEL_DEFAULTS)
    for k, v in kw.items():
        if k.endswith("_LR"):
            pose[k[:-3] + "_L"] = v
            pose[k[:-3] + "_R"] = v
        else:
            pose[k] = v
    return {k: tuple(v) for k, v in pose.items()}


def arm(rx=0.0, abd=0.0, twist=0.0):
    """Arm rotation in left-side convention: rx<0 swings forward, abd>0 moves the arm outwards."""
    return (rx, -abd, twist)


def curl(deg):
    return (deg,)


def ease(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


def lerp(a, b, t):
    return a + (b - a) * t


def lerp_pose(a, b, t):
    return {k: tuple(lerp(x, y, t) for x, y in zip(a[k], b[k])) for k in a}


def keyed(frame, keys):
    """keys: list of (frame, pose[, 'linear'|'smooth'|'out'|'in']). Smooth interpolation between keys."""
    if frame <= keys[0][0]:
        return keys[0][1]
    for i in range(len(keys) - 1):
        f0, p0 = keys[i][0], keys[i][1]
        f1, p1 = keys[i + 1][0], keys[i + 1][1]
        if f0 <= frame <= f1:
            t = (frame - f0) / max(1e-6, f1 - f0)
            mode = keys[i + 1][2] if len(keys[i + 1]) > 2 else "smooth"
            if mode == "smooth":
                t = ease(t)
            elif mode == "out":
                t = 1 - (1 - t) ** 2
            elif mode == "in":
                t = t * t
            return lerp_pose(p0, p1, t)
    return keys[-1][1]


def q_euler(rx, ry, rz):
    return Euler((math.radians(rx), math.radians(ry), math.radians(rz)), "XYZ").to_quaternion()


def rx_q(deg):
    return Quaternion((1, 0, 0), math.radians(deg))


class Solver:
    def __init__(self, arm_ob):
        self.arm = arm_ob
        bones = arm_ob.data.bones
        self.rest = {b.name: b.matrix_local.to_quaternion() for b in bones}
        self.rest_inv = {k: q.inverted() for k, q in self.rest.items()}
        self.head = {b.name: b.head_local.copy() for b in bones}
        self.parent = {b.name: (b.parent.name if b.parent else None) for b in bones}
        mats = {b.name: b.matrix_local.copy() for b in bones}
        self.length = {b.name: b.length for b in bones}
        self.rest_rel = {n: (mats[p].inverted() @ m if p else m) for n, (m, p) in
                         ((n, (mats[n], self.parent[n])) for n in mats)}
        # local axis of the Eyes bone that matches armature Z (for blink scaling)
        m = bones["Eyes"].matrix_local.to_3x3()
        self.eye_axis = max(range(3), key=lambda i: abs(m.col[i].z))
        self.ik_error = 0.0

    def local(self, bone, q_delta):
        return self.rest_inv[bone] @ q_delta @ self.rest[bone]

    def pose_matrix(self, sample, bone, cache):
        """Armature-space matrix of a bone for one solved sample (Blender's pose formula)."""
        m = cache.get(bone)
        if m is None:
            loc, q, scale = sample.get(bone, (None, None, None))
            basis = (Matrix.Translation(loc) if loc is not None else Matrix.Identity(4)) @ \
                (q.to_matrix().to_4x4() if q is not None else Matrix.Identity(4))
            if scale is not None:
                basis = basis @ Matrix.Diagonal(Vector(scale)).to_4x4()
            par = self.parent[bone]
            m = (self.pose_matrix(sample, par, cache) if par else Matrix.Identity(4)) @ self.rest_rel[bone] @ basis
            cache[bone] = m
        return m

    def leg_ik(self, side, w_pelvis, pelvis_off, foot, foot_yaw=0.0, root=(0.0, 0.0, 0.0), pelvis_yaw=0.0,
               root_z=0.0):
        s = 1 if side == "L" else -1
        outward, fy, lift, pitch = foot
        q_fyaw = Quaternion((0, 0, 1), math.radians(foot_yaw))
        q_root = Quaternion((0, 0, 1), math.radians(root[2]))
        q_root_inv = q_root.inverted()
        hip_rest = self.head["UpperLeg_" + side]
        pel = self.head["Pelvis"]
        hip = pel + pelvis_off + w_pelvis @ (hip_rest - pel)
        ankle_rest = self.head["Foot_" + side]
        base = Vector((ankle_rest.x + s * outward, fy, ankle_rest.z + lift))
        contact = TOE_OFF if pitch > 0 else HEEL_OFF
        target = base + q_fyaw @ (contact + rx_q(pitch) @ (-contact))
        # targets are world positions; the rig below Root lives in the (possibly turning) root frame
        # x/y of the feet are world positions (turns), the height is relative to the (climbing) root
        target = q_root_inv @ (target - Vector((root[0], root[1], 0.0)))
        v = w_pelvis.inverted() @ (target - hip)
        d_raw = v.length
        d = max(abs(THIGH - SHIN) + 1e-3, min(THIGH + SHIN - 1e-4, d_raw))
        self.ik_error = max(self.ik_error, abs(d_raw - d))
        t = v.normalized()
        # knee pole: perpendicular to the hip->ankle line inside the sagittal plane, pointing forward/up
        rel = Quaternion((0, 0, 1), math.radians(foot_yaw - root[2] - pelvis_yaw))
        tl = rel.inverted() @ t
        pole = Vector((0.0, tl.z, -tl.y))
        if pole.length < 1e-6:
            pole = Vector((0.0, -1.0, 0.0))
        pole = (rel @ pole).normalized()
        delta = math.acos(max(-1, min(1, (THIGH ** 2 + d ** 2 - SHIN ** 2) / (2 * THIGH * d))))
        gamma = math.acos(max(-1, min(1, (THIGH ** 2 + SHIN ** 2 - d ** 2) / (2 * THIGH * SHIN))))
        knee = t * (THIGH * math.cos(delta)) + pole * (THIGH * math.sin(delta))
        u1 = knee.normalized()
        hinge = pole.cross(u1).normalized()
        up = -u1
        q_upper = Matrix((hinge, up.cross(hinge), up)).transposed().to_quaternion()
        q_lower = Quaternion((1, 0, 0), math.pi - gamma)
        w_lower = w_pelvis @ q_upper @ q_lower
        q_foot = w_lower.inverted() @ q_root_inv @ q_fyaw @ rx_q(pitch)
        return q_upper, q_lower, q_foot

    def arm_ik(self, side, w_shoulder, shoulder_pos, q_hand, target, pole):
        """Upper/lower arm rotations (armature-aligned deltas) that put the knuckle (Fingers head) on target.
        Works in the root frame; w_shoulder = world delta rotation of the Shoulder bone; pole = elbow direction."""
        h = self.head
        v1 = h["LowerArm_" + side] - h["UpperArm_" + side]
        v2 = (h["Hand_" + side] - h["LowerArm_" + side]) + q_hand @ (h["Fingers_" + side] - h["Hand_" + side])
        d = target - shoulder_pos
        dist = d.length

        def reach(phi):
            return (v1 + rx_q(-phi) @ v2).length

        lo, hi = 0.0, 150.0          # elbow flexion (deg); reach shrinks monotonically
        if dist >= reach(lo):
            phi = lo
        elif dist <= reach(hi):
            phi = hi
        else:
            for _ in range(40):
                mid = (lo + hi) / 2
                if reach(mid) > dist:
                    lo = mid
                else:
                    hi = mid
            phi = (lo + hi) / 2
        self.arm_error = abs(reach(phi) - dist)
        w_loc = v1 + rx_q(-phi) @ v2
        xl = w_loc.normalized()
        back = Vector((0, 1, 0))      # the elbow sticks out backwards when the forearm flexes forwards
        yl = (back - xl * back.dot(xl)).normalized()
        xw = d.normalized()
        yw = pole - xw * pole.dot(xw)
        if yw.length < 1e-6:
            yw = Vector((0, 0, -1)) - xw * (-xw.z)
        yw.normalize()
        fl = Matrix((xl, yl, xl.cross(yl))).transposed()
        fw = Matrix((xw, yw, xw.cross(yw))).transposed()
        r_world = (fw @ fl.transposed()).to_quaternion()
        q_upper = w_shoulder.inverted() @ r_world
        return q_upper, rx_q(-phi)

    def solve(self, pose):
        out = {}
        q_pel = q_euler(*pose["pelvis_rot"])
        pel_off = Vector(pose["pelvis"])
        out["Pelvis"] = (self.rest_inv["Pelvis"] @ pel_off, self.local("Pelvis", q_pel), None)
        out["Spine"] = (None, self.local("Spine", q_euler(*pose["spine"])), None)
        out["Head"] = (None, self.local("Head", q_euler(*pose["head"])), None)
        hp, hr, hx, hz = pose["hat"]
        out["Socket_Head"] = (self.rest_inv["Socket_Head"] @ Vector((hx, 0, hz)),
                              self.local("Socket_Head", q_euler(hp, hr, 0)), None)
        sz, ox, oz = pose["eyes"]
        scale = [1.0, 1.0, 1.0]
        scale[self.eye_axis] = sz
        out["Eyes"] = (self.rest_inv["Eyes"] @ Vector((ox, 0, oz)), Quaternion(), Vector(scale))
        for side, s in (("L", 1), ("R", -1)):
            def mirrored(v):
                return (v[0], s * v[1], s * v[2])
            out["Shoulder_" + side] = (None, self.local("Shoulder_" + side, q_euler(*mirrored(pose["shoulder_" + side]))), None)
            out["UpperArm_" + side] = (None, self.local("UpperArm_" + side, q_euler(*mirrored(pose["uarm_" + side]))), None)
            out["LowerArm_" + side] = (None, self.local("LowerArm_" + side, q_euler(*mirrored(pose["larm_" + side]))), None)
            q_hand = q_euler(*mirrored(pose["hand_" + side]))
            out["Hand_" + side] = (None, self.local("Hand_" + side, q_hand), None)
            ik = pose["handik_" + side]
            if ik[6] > 1e-4:
                # chain positions in the root frame (targets are world positions, like the feet)
                rx_, ry_, ryaw = pose["root"]
                q_root_inv = Quaternion((0, 0, 1), math.radians(ryaw)).inverted()
                tgt = q_root_inv @ (Vector((s * ik[0], ik[1], ik[2])) - Vector((rx_, ry_, pose["root_z"][0])))
                pole = q_root_inv @ Vector((s * ik[3], ik[4], ik[5]))
                h = self.head
                w_sp = q_pel @ q_euler(*pose["spine"])
                w_sh = w_sp @ q_euler(*mirrored(pose["shoulder_" + side]))
                spine_pos = h["Pelvis"] + pel_off + q_pel @ (h["Spine"] - h["Pelvis"])
                sh_pos = spine_pos + w_sp @ (h["Shoulder_" + side] - h["Spine"])
                arm_pos = sh_pos + w_sh @ (h["UpperArm_" + side] - h["Shoulder_" + side])
                qu, ql = self.arm_ik(side, w_sh, arm_pos, q_hand, tgt, pole)
                fk_u = q_euler(*mirrored(pose["uarm_" + side]))
                fk_l = q_euler(*mirrored(pose["larm_" + side]))
                wgt = min(1.0, ik[6])
                self.ik_error = max(self.ik_error, self.arm_error * wgt)
                out["UpperArm_" + side] = (None, self.local("UpperArm_" + side, fk_u.slerp(qu, wgt)), None)
                out["LowerArm_" + side] = (None, self.local("LowerArm_" + side, fk_l.slerp(ql, wgt)), None)
            out["Fingers_" + side] = (None, self.local("Fingers_" + side, q_euler(0, s * pose["fing_" + side][0], 0)), None)
            out["Thumb_" + side] = (None, self.local("Thumb_" + side, q_euler(0, s * pose["thumb_" + side][0], 0)), None)
            qu, ql, qf = self.leg_ik(side, q_pel, pel_off, pose["foot_" + side], pose["footyaw_" + side][0],
                                     pose["root"], pose["pelvis_rot"][2], pose["root_z"][0])
            out["UpperLeg_" + side] = (None, self.local("UpperLeg_" + side, qu), None)
            out["LowerLeg_" + side] = (None, self.local("LowerLeg_" + side, ql), None)
            out["Foot_" + side] = (None, self.local("Foot_" + side, qf), None)
        rx_, ry_, ryaw = pose["root"]
        out["Root"] = (self.rest_inv["Root"] @ Vector((rx_, ry_, pose["root_z"][0])),
                       self.local("Root", Quaternion((0, 0, 1), math.radians(ryaw))), None)
        return out


# Spring bones: secondary motion baked into every clip. A point mass at the bone tip follows the posed rest direction
# (blended towards gravity) as a damped spring; the result is limited so the part never swings into the body.
# out = the bone-local axis that points away from the body; swing = (min, max) degrees towards out, side = +-degrees.
SPRINGS = collections.OrderedDict([
    # side: small, the cape turns in its own plane and its upper corners rise towards the backpack
    ("Cape", {"freq": 1.5, "damping": 0.32, "gravity": 0.5, "wind": 38.0, "out": 2, "swing": (0.0, 70.0),
              "side": 7.0}),
    ("Antenna", {"freq": 2.8, "damping": 0.16, "gravity": 0.0, "wind": 16.0, "out": None, "swing": (-24.0, 24.0),
                 "side": 24.0}),
])
# Relative wind of the in-place locomotion clips (0..1): lifts the cape and bends the antenna backwards.
CLIP_WIND = {"Walk": 0.3, "Run": 0.75, "RunFast": 0.9, "Sprint": 1.0, "CrouchWalk": 0.2, "SlopeSlide": 0.6,
             "Fall": 0.35, "DoubleJump": 0.3}


# Type emotes: a socket rides rigidly on a hand between two frames (the hat in "HatTip").
HAT_GRAB, HAT_RELEASE = 12, 46      # HatTip key frames where the hand holds the hat (same pose at both)
ATTACHMENTS = {"HatTip": ("Socket_Head", "Hand_R", HAT_GRAB, HAT_RELEASE)}


def bake_attachment(solver, samples, name):
    if name not in ATTACHMENTS:
        return
    bone, hand, f0, f1 = ATTACHMENTS[name]
    par = solver.parent[bone]
    c0 = {}
    grip = solver.pose_matrix(samples[f0], hand, c0).inverted() @ solver.pose_matrix(samples[f0], bone, c0)
    for f in range(f0 + 1, f1 + 1):
        cache = {}
        want = solver.pose_matrix(samples[f], hand, cache) @ grip
        basis = (solver.pose_matrix(samples[f], par, cache) @ solver.rest_rel[bone]).inverted() @ want
        loc, rot, _ = basis.decompose()
        samples[f][bone] = (loc, rot, None)


def bake_springs(solver, samples, loop, name):
    """Adds the spring bones to the solved samples (in place)."""
    n = len(samples)
    if not all(b in solver.rest_rel for b in SPRINGS):
        return
    statics = []
    for sample in samples:
        cache = {}
        statics.append({b: solver.pose_matrix(sample, solver.parent[b], cache) @ solver.rest_rel[b] for b in SPRINGS})
    dt, sub = 1.0 / FPS, 4
    wind = CLIP_WIND.get(name, 0.0)
    for b, cfg in SPRINGS.items():
        length = solver.length[b]
        w = 2 * math.pi * cfg["freq"]

        def target(f):
            m = statics[f][b]
            d = m.col[1].to_3d().normalized()
            if cfg["gravity"]:
                d = d.lerp(Vector((0, 0, -1)), cfg["gravity"]).normalized()
            if wind:
                back = (m.col[cfg["out"]].to_3d() if cfg["out"] is not None else -m.col[2].to_3d()).normalized()
                a = math.radians(cfg["wind"] * wind)
                d = (d * math.cos(a) + back * math.sin(a)).normalized()
            return m.translation + d * length

        # loops: four cycles to settle, the last one is kept; one extra step gives the start of the next cycle
        frames = list(range(n - 1)) * 4 + [0] if loop else list(range(n))
        x, v = target(frames[0]), Vector((0, 0, 0))
        tips = {}
        for k, f in enumerate(frames):
            t1 = target(f)
            h = statics[f][b].translation
            for _ in range(sub):
                a = (t1 - x) * (w * w) - v * (2 * cfg["damping"] * w)
                v = v + a * (dt / sub)
                x = h + (x + v * (dt / sub) - h).normalized() * length
            if not loop or len(frames) - n <= k < len(frames) - 1:
                tips[f] = x.copy()
        if loop:
            seam = x - tips[0]                  # next cycle start minus this cycle start
            tips = {f: tips[f] - seam * (f / (n - 1)) for f in tips}       # closes the cycle without a jump
            tips[n - 1] = tips[0]
        for f in range(n):
            m = statics[f][b]
            d = m.to_3x3().inverted() @ (tips[f] - m.translation)
            d.normalize()
            out = cfg["out"]
            # pitch towards the out axis (or the bone's local X/Z for a free antenna), roll sideways
            ax_p, ax_r = (out, 0) if out is not None else (2, 0)
            pitch = math.degrees(math.atan2(d[ax_p], d[1]))
            roll = math.degrees(math.atan2(d[ax_r], d[1]))
            pitch = max(cfg["swing"][0], min(cfg["swing"][1], pitch))
            roll = max(-cfg["side"], min(cfg["side"], roll))
            dl = [0.0, 1.0, 0.0]
            dl[ax_p] = math.tan(math.radians(pitch))
            dl[ax_r] = math.tan(math.radians(roll))
            samples[f][b] = (None, Vector((0, 1, 0)).rotation_difference(Vector(dl).normalized()), None)


def write_action(arm_ob, solver, name, n_frames, pose_fn, loop):
    act = bpy.data.actions.new(name)
    act.use_fake_user = True
    slot = act.slots.new(id_type="OBJECT", name=arm_ob.name)
    cb = anim_utils.action_ensure_channelbag_for_slot(act, slot)
    solver.ik_error = 0.0
    samples = []
    worst = (0.0, 0)
    for f in range(n_frames + 1):
        before = solver.ik_error
        solver.ik_error = 0.0
        samples.append(solver.solve(pose_fn(f)))
        if solver.ik_error > worst[0]:
            worst = (solver.ik_error, f)
        solver.ik_error = max(before, solver.ik_error)
    bake_attachment(solver, samples, name)
    bake_springs(solver, samples, loop, name)
    if loop:
        samples[-1] = samples[0]
    curves = {}
    prev_q = {}
    for f, sample in enumerate(samples):
        for bone, (loc, q, scale) in sample.items():
            q = q.normalized()
            if bone in prev_q and prev_q[bone].dot(q) < 0:
                q = -q
            prev_q[bone] = q
            chans = [("rotation_quaternion", list(q))]
            if loc is not None:
                chans.append(("location", list(loc)))
            if scale is not None:
                chans.append(("scale", list(scale)))
            for prop, vals in chans:
                for i, v in enumerate(vals):
                    curves.setdefault((bone, prop, i), []).extend((float(f), float(v)))
    for (bone, prop, i), co in curves.items():
        fc = cb.fcurves.new('pose.bones["%s"].%s' % (bone, prop), index=i, group_name=bone)
        fc.keyframe_points.add(len(co) // 2)
        fc.keyframe_points.foreach_set("co", co)
        for kp in fc.keyframe_points:
            kp.interpolation = "LINEAR"
        fc.update()
    act.use_frame_range = True
    act.frame_start = 0
    act.frame_end = n_frames
    act.use_cyclic = loop
    act["cube_escape_loop"] = int(loop)
    if solver.ik_error > 0.003:
        LOG.warning("  %s: IK clamped by %.1f mm (frame %d)", name, solver.ik_error * 1000, worst[1])
    return act, slot


def assign_action(arm_ob, act):
    ad = arm_ob.animation_data or arm_ob.animation_data_create()
    ad.action = act
    ad.action_slot = act.slots[0]


# ---------------------------------- gait helpers ----------------------------------


def gait_foot(u, stride, stance, lift, p_strike, p_off, kick=0.0, center=0.0, outward=0.0):
    """Foot channel for an in-place locomotion cycle. u = 0 at heel strike."""
    u %= 1.0
    if u < stance:
        s = u / stance
        y = center - stride / 2 + stride * s
        p = 0.0
        if s < 0.18:
            p = p_strike * (1 - ease(s / 0.18))
        elif s > 0.62:
            p = p_off * ((s - 0.62) / 0.38) ** 1.6
        return (outward, y, 0.0, p)
    w = (u - stance) / (1 - stance)
    e = ease(w)
    y = lerp(center + stride / 2, center - stride / 2, e) + kick * math.sin(math.pi * w) * (1 - w) * 1.6
    z = lift * math.sin(math.pi * w) ** 0.9
    p = lerp(p_off, p_strike, ease(min(1.0, w * 2.2)))
    return (outward, y, z, p)


def blink(f, center, width=2.5):
    d = abs(f - center) / width
    return 1.0 - 0.92 * max(0.0, 1.0 - d)


__all__ = [_n for _n in dir() if not _n.startswith("__")]
