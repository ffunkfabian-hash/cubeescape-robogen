"""The robot's geometry: core body, slot variants, accessories and forms' parts."""

from robogen.parts import *  # noqa: F401,F403 - one shared namespace, see robogen/__init__.py


def eye_screen(name, bone):
    """Flat, slightly curved emissive plane in front of the visor that shows the eye expressions."""
    bm = bmesh.new()
    nx, nz = 16, 2
    verts = []
    for iz in range(nz + 1):
        row = []
        for ix in range(nx + 1):
            x = -EYE_SCREEN_W / 2 + EYE_SCREEN_W * ix / nx
            z = EYE_Z - EYE_SCREEN_H / 2 + EYE_SCREEN_H * iz / nz
            row.append(bm.verts.new((x, EYE_SCREEN_Y, z)))
        verts.append(row)
    for iz in range(nz):
        for ix in range(nx):
            bm.faces.new((verts[iz][ix], verts[iz][ix + 1], verts[iz + 1][ix + 1], verts[iz + 1][ix]))
    for v in bm.verts:
        v.co = visor_bend(v.co.copy())
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    for f in bm.faces:              # the screen must face forward (-Y)
        if f.normal.y > 0:
            f.normal_flip()
    PARTS.append({"name": name, "bone": bone, "mat": "EyeScreen", "bm": bm, "slot": None, "variant": None,
                  "group": "Body"})


EYE_SCREEN_W, EYE_SCREEN_H, EYE_SCREEN_Y = 0.33, 0.165, -0.2235
DISPLAY_RECT = (0.014, 0.096, 0.767, 0.833)     # x0, x1, z0, z1 of the chest screen


def build_robot():
    W, D, G, V, O, F = "White", "Dark", "Grey", "Visor", "Orange", "Fabric"
    xs = [i * 0.02 for i in range(-11, 12)]

    # ================= core head (shared by every head variant) =================
    prism("Head_Visor", rrect(0.400, 0.190, 0.076, 5), 0.060, "Y", (0, -0.190, 1.112), V, "Head", r=0.012, segs=3,
          cuts_x=xs, world_deform=visor_bend)
    eye_screen("Eye_Screen", "Eyes")
    cylinder("Neck", 0.068, 0.120, (0, 0.010, 0.905), D, "Head", segs=12, bev=0.006)
    cylinder("Neck_Ring", 0.082, 0.022, (0, 0.010, 0.928), G, "Head", segs=16, bev=0.006)

    # ---------------- Head: Classic (key art) ----------------
    with variant("Head", "Classic"):
        rbox("Head_Shell", (0.52, 0.43, 0.37), (0, 0.0, 1.118), W, "Head", r=0.14, segs=5, taper_bot=(0.90, 0.92))
        for s, side in ((1, "L"), (-1, "R")):
            cylinder("Ear_Base_" + side, 0.090, 0.060, (s * 0.250, 0.010, 1.100), D, "Head", axis=(1, 0, 0),
                     segs=18, bev=0.010)
            cylinder("Ear_Ring_" + side, 0.068, 0.026, (s * 0.288, 0.010, 1.100), W, "Head", axis=(1, 0, 0),
                     segs=18, bev=0.009)
            cylinder("Ear_Cap_" + side, 0.034, 0.014, (s * 0.304, 0.010, 1.100), G, "Head", axis=(1, 0, 0),
                     segs=12, bev=0.004)
        n = Vector((0, -1, 1)).normalized()
        for i, xx in enumerate((0.050, 0.088)):
            cylinder("Head_Sensor_%d" % i, 0.011, 0.020, (xx, -0.170, 1.259), D, "Head", axis=n, segs=10, bev=0.003)
        prism("Head_BackVent", rrect(0.200, 0.100, 0.03, 3), 0.030, "Y", (0, 0.203, 1.118), D, "Head", r=0.006)
        for i, zz in enumerate((1.093, 1.118, 1.143)):
            rbox("Head_VentSlat_%d" % i, (0.165, 0.012, 0.011), (0, 0.2205, zz), G, "Head", r=0.004, segs=1)

    # ---------------- Head: Dome (round helmet with fins) ----------------
    with variant("Head", "Dome"):
        rbox("Dome_Shell", (0.50, 0.44, 0.40), (0, 0.0, 1.130), W, "Head", r=0.19, segs=6, taper_bot=(0.90, 0.92))
        rbox("Dome_Brow", (0.42, 0.06, 0.03), (0, -0.168, 1.232), D, "Head", r=0.012, segs=2)
        for s, side in ((1, "L"), (-1, "R")):
            cylinder("Dome_EarDisc_" + side, 0.070, 0.040, (s * 0.238, 0.0, 1.110), D, "Head", axis=(1, 0, 0),
                     segs=16, bev=0.008)
            fin = [(0.06, -0.02), (-0.03, -0.035), (-0.13, 0.10), (-0.10, 0.12), (0.04, 0.03)]
            prism("Dome_Fin_" + side, fin, 0.030, "X", (s * 0.262, 0.02, 1.115), W, "Head", r=0.008, segs=2)
        cylinder("Dome_TopLight", 0.035, 0.030, (0, 0.0, 1.322), O, "Head", segs=14, bev=0.008)

    # ---------------- Head: Boxy (worker box with antenna stubs) ----------------
    with variant("Head", "Boxy"):
        rbox("Boxy_Shell", (0.50, 0.42, 0.36), (0, 0.0, 1.125), W, "Head", r=0.06, segs=2, taper_bot=(0.82, 1.0))
        for i, xx in enumerate((-0.10, 0.0, 0.10)):
            rbox("Boxy_Grill_%d" % i, (0.06, 0.24, 0.016), (xx, 0.02, 1.305), D, "Head", r=0.006, segs=1)
        for s, side in ((1, "L"), (-1, "R")):
            cylinder("Boxy_Stub_" + side, 0.030, 0.070, (s * 0.262, 0.0, 1.160), D, "Head", axis=(1, 0, 0),
                     segs=12, bev=0.006)
            sphere("Boxy_StubLight_" + side, 0.020, (s * 0.300, 0.0, 1.160), O, "Head", segs=10, rings=6)
            rbox("Boxy_Bolt_" + side, (0.030, 0.050, 0.050), (s * 0.222, 0.06, 1.040), G, "Head", r=0.008, segs=1)

    # ---------------- Head: TV (retro CRT with rabbit ears) ----------------
    with variant("Head", "TV"):
        rbox("TV_Shell", (0.52, 0.36, 0.34), (0, -0.02, 1.136), W, "Head", r=0.07, segs=3, taper_bot=(0.82, 1.0))
        rbox("TV_Back", (0.40, 0.26, 0.14), (0, 0.19, 1.128), G, "Head", r=0.06, segs=3, taper_top=(0.8, 0.8),
             rot=(-90, 0, 0))
        for nm, size, c in (("Top", (0.40, 0.03, 0.030), (0, -0.198, 1.232)),):
            rbox("TV_Bezel_" + nm, size, c, D, "Head", r=0.008, segs=1)
        for i, zz in enumerate((1.17, 1.07)):
            cylinder("TV_Knob_%d" % i, 0.026, 0.030, (-0.252, -0.05, zz + 0.04), G, "Head", axis=(1, 0, 0), segs=14,
                     bev=0.006)
        rbox("TV_Speaker", (0.020, 0.16, 0.12), (0.250, -0.02, 1.17), D, "Head", r=0.006, segs=1)
        for s in (1, -1):
            d = Vector((s * math.sin(math.radians(28)), 0.0, math.cos(math.radians(28))))
            base = Vector((s * 0.05, 0.02, 1.29))
            cyl_between("TV_Antenna_%s" % ("L" if s > 0 else "R"), base, base + d * 0.20, 0.007, G, "Head", segs=8)
            sphere("TV_AntennaTip_%s" % ("L" if s > 0 else "R"), 0.018, base + d * 0.205, O, "Head", segs=10,
                   rings=6)
        cylinder("TV_AntennaBase", 0.045, 0.030, (0, 0.02, 1.296), D, "Head", segs=14, bev=0.006)

    # ================= core torso, pelvis =================
    rbox("Torso_Core", (0.36, 0.27, 0.34), (0, 0.010, 0.730), D, "Spine", r=0.05, segs=2)
    rbox("Chest_Plate", (0.50, 0.33, 0.23), (0, 0.0, 0.790), W, "Spine", r=0.075, segs=3, taper_bot=(0.84, 0.92))
    left = [(-0.182, 0.669), (-0.007, 0.669), (-0.007, 0.582), (-0.165, 0.613)]
    prism("Abdomen_Plate_R", left, 0.285, "Y", (0, 0.0, 0), W, "Spine", r=0.018, segs=2)
    prism("Abdomen_Plate_L", [(-x, z) for x, z in reversed(left)], 0.285, "Y", (0, 0.0, 0), W, "Spine", r=0.018,
          segs=2)
    prism("Chest_Display", rrect(0.110, 0.094, 0.018, 3), 0.030, "Y", (0.055, -0.160, 0.800), D, "Spine", r=0.006)
    prism("Chest_Screen", rrect(0.082, 0.066, 0.010, 3), 0.012, "Y", (0.055, -0.175, 0.800), "Display", "Spine",
          r=0.003)
    for i, xx in enumerate((-0.135, -0.108, -0.081)):
        cylinder("Chest_LED_%d" % i, 0.0098, 0.016, (xx, -0.166, 0.816), O, "Spine", axis=(0, 1, 0), segs=10,
                 bev=0.003)
    rbox("Chest_Badge", (0.062, 0.014, 0.016), (-0.108, -0.166, 0.781), G, "Spine", r=0.004, segs=1)
    cylinder("Collar", 0.112, 0.030, (0, 0.010, 0.902), D, "Spine", segs=18, bev=0.008)

    with variant("Backpack", "Classic"):
        rbox("Backpack", (0.29, 0.08, 0.20), (0, 0.180, 0.770), W, "Spine", r=0.03, segs=2)
        for s in (1, -1):
            rbox("Backpack_Vent_%s" % ("L" if s > 0 else "R"), (0.070, 0.012, 0.120), (s * 0.070, 0.2205, 0.760),
                 D, "Spine", r=0.005, segs=1)
        prism("Backpack_Light", rrect(0.070, 0.018, 0.008, 2), 0.010, "Y", (0, 0.2205, 0.845), O, "Spine", r=0.003)
    with variant("Backpack", "Battery"):
        rbox("Battery_Mount", (0.20, 0.06, 0.16), (0, 0.165, 0.770), D, "Spine", r=0.015, segs=2)
        for s in (1, -1):
            sd = "L" if s > 0 else "R"
            cylinder("Battery_Cell_" + sd, 0.055, 0.200, (s * 0.068, 0.205, 0.770), W, "Spine", segs=16, bev=0.012)
            cylinder("Battery_CapTop_" + sd, 0.040, 0.026, (s * 0.068, 0.205, 0.878), D, "Spine", segs=14, bev=0.006)
            cylinder("Battery_Ring_" + sd, 0.057, 0.012, (s * 0.068, 0.205, 0.800), O, "Spine", segs=16, bev=0.003)

    cylinder("Waist", 0.140, 0.090, (0, 0.010, 0.560), D, "Pelvis", segs=16, bev=0.012, scale=(1, 0.80, 1))
    cylinder("Waist_Ring", 0.152, 0.022, (0, 0.010, 0.537), G, "Pelvis", segs=18, bev=0.007, scale=(1, 0.80, 1))
    rbox("Hips", (0.37, 0.27, 0.13), (0, 0.005, 0.470), W, "Pelvis", r=0.055, segs=3, taper_bot=(0.85, 0.90))
    rbox("Crotch", (0.110, 0.180, 0.085), (0, 0.0, 0.405), D, "Pelvis", r=0.025, segs=2)

    # ================= legs and feet =================
    for s, side in ((1, "L"), (-1, "R")):
        x0 = s * LEG_X
        sphere("Hip_Ball_" + side, 0.068, J["hip_" + side], D, "UpperLeg_" + side, segs=12, rings=7)
        rbox("Thigh_" + side, (0.145, 0.150, 0.135), (x0, 0.0, 0.330), W, "UpperLeg_" + side, r=0.045, segs=3,
             taper_bot=(0.92, 0.92))
        cylinder("Knee_" + side, 0.058, 0.135, J["knee_" + side], D, "LowerLeg_" + side, axis=(1, 0, 0), segs=14,
                 bev=0.010)
        rbox("KneeCap_" + side, (0.100, 0.040, 0.072), (x0, -0.068, 0.250), W, "LowerLeg_" + side, r=0.016, segs=2)
        rbox("Shin_" + side, (0.155, 0.165, 0.130), (x0, 0.005, 0.165), W, "LowerLeg_" + side, r=0.045, segs=3,
             taper_top=(0.86, 0.86))
        cylinder("Ankle_" + side, 0.044, 0.115, J["ankle_" + side], D, "Foot_" + side, axis=(1, 0, 0), segs=12,
                 bev=0.006)
        foot = "Foot_" + side
        with variant("Feet", "Boots"):
            prism("Foot_Shell_" + side, rrect(0.185, 0.300, 0.070, 4), 0.080, "Z", (x0, -0.050, 0.065), W, foot,
                  r=0.032, segs=3, deform=foot_deform)
            prism("Foot_Sole_" + side, rrect(0.195, 0.310, 0.075, 4), 0.030, "Z", (x0, -0.050, 0.015), D, foot,
                  r=0.008, segs=2)
        with variant("Feet", "Round"):
            prism("Sneaker_Shell_" + side, rrect(0.185, 0.295, 0.090, 5), 0.085, "Z", (x0, -0.048, 0.072), W, foot,
                  r=0.040, segs=4, deform=foot_deform)
            prism("Sneaker_Sole_" + side, rrect(0.195, 0.305, 0.092, 5), 0.034, "Z", (x0, -0.048, 0.017), O, foot,
                  r=0.010, segs=2)
            rbox("Sneaker_Strap_" + side, (0.150, 0.050, 0.020), (x0, -0.030, 0.108), D, foot, r=0.008, segs=1)
        with variant("Feet", "Mech"):
            rbox("Mech_Heel_" + side, (0.150, 0.115, 0.068), (x0, 0.030, 0.046), G, foot, r=0.020, segs=2)
            for i, dx in enumerate((-0.048, 0.048)):
                prism("Mech_Toe_%d_%s" % (i, side), rrect(0.088, 0.150, 0.030, 3), 0.060, "Z",
                      (x0 + dx, -0.120, 0.035), W, foot, r=0.018, segs=2, deform=foot_deform)
            rbox("Mech_Sole_" + side, (0.198, 0.300, 0.022), (x0, -0.052, 0.011), D, foot, r=0.008, segs=1)
            rbox("Mech_AnklePlate_" + side, (0.110, 0.035, 0.050), (x0, -0.075, 0.080), W, foot, r=0.012, segs=2)

    # ================= arms and hands =================
    for s, side in ((1, "L"), (-1, "R")):
        sh, el, wr = J["shoulder_" + side], J["elbow_" + side], J["wrist_" + side]
        d1, d2 = J["dir_upper_" + side], J["dir_fore_" + side]
        cylinder("Shoulder_Socket_" + side, 0.062, 0.075, (s * 0.232, 0, 0.830), D, "Shoulder_" + side,
                 axis=(1, 0, 0), segs=16, bev=0.008)
        sphere("Shoulder_Ball_" + side, 0.062, sh, D, "UpperArm_" + side, segs=12, rings=7)
        with variant("Pads", "Round"):
            rbox("Shoulder_Pad_" + side, (0.165, 0.190, 0.135), sh + Vector((s * 0.014, 0, 0.016)), W,
                 "UpperArm_" + side, r=0.064, segs=3)
        with variant("Pads", "Slim"):     # low profile: keeps the silhouette narrow (character capsule)
            rbox("Slim_Pad_" + side, (0.112, 0.170, 0.070), sh + Vector((s * -0.004, 0, 0.040)), W,
                 "UpperArm_" + side, r=0.030, segs=2, taper_top=(0.86, 0.90))
            rbox("Slim_PadStripe_" + side, (0.020, 0.150, 0.012), sh + Vector((s * 0.020, 0, 0.076)), O,
                 "UpperArm_" + side, r=0.004, segs=1)
        with variant("Pads", "Square"):
            rbox("Square_Pad_" + side, (0.170, 0.200, 0.120), sh + Vector((s * 0.018, 0, 0.012)), W,
                 "UpperArm_" + side, r=0.022, segs=2)
            rbox("Square_PadStripe_" + side, (0.030, 0.205, 0.020), sh + Vector((s * 0.070, 0, 0.068)), O,
                 "UpperArm_" + side, r=0.006, segs=1)
        cyl_between("Upper_Arm_" + side, sh + d1 * 0.05, el, 0.049, W, "UpperArm_" + side, segs=12, bev=0.008)
        sphere("Elbow_" + side, 0.056, el, D, "LowerArm_" + side, segs=12, rings=7)
        cyl_between("Elbow_Band_" + side, el + d2 * 0.012, el + d2 * 0.052, 0.052, D, "LowerArm_" + side, segs=14,
                    bev=0.006)
        fr = frame_from_dir(d2)
        rbox("Forearm_" + side, (0.110, 0.120, 0.120), el + d2 * 0.106, W, "LowerArm_" + side, r=0.04, segs=3,
             taper_bot=(0.80, 0.82), rot=fr)
        cyl_between("Wrist_" + side, wr - d2 * 0.010, wr + d2 * 0.028, 0.033, D, "Hand_" + side, segs=12)
        kn = J["knuckle_" + side]
        fdir = (Matrix.Rotation(math.radians(s * 12), 3, "Y") @ d2).normalized()
        ff = frame_from_dir(fdir)
        tdir = (Vector((-s * 0.25, -0.75, -0.62))).normalized()
        tb = wr + d2 * 0.044 + Vector((-s * 0.014, -0.046, 0))
        J["thumb_" + side] = tb
        J["thumb_dir_" + side] = tdir
        J["finger_dir_" + side] = fdir
        with variant("Hands", "Mitt"):
            rbox("Palm_" + side, (0.064, 0.098, 0.080), wr + d2 * 0.054, G, "Hand_" + side, r=0.022, segs=2, rot=fr)
            for i, off in enumerate((-0.031, 0.0, 0.031)):
                base = kn + Vector((0, off, 0))
                rbox("Finger_%d_%s" % (i, side), (0.030, 0.027, 0.058), base + fdir * 0.024, D, "Fingers_" + side,
                     r=0.011, segs=1, rot=ff)
            rbox("Thumb_" + side, (0.028, 0.028, 0.050), tb + tdir * 0.024, D, "Thumb_" + side, r=0.010, segs=1,
                 rot=frame_from_dir(tdir))
        with variant("Hands", "Claw"):
            cyl_between("Claw_Cuff_" + side, wr + d2 * 0.010, wr + d2 * 0.085, 0.040, G, "Hand_" + side, segs=14,
                        bev=0.008)
            inward = Vector((-s, 0, 0))
            for i, off in enumerate((-0.024, 0.024)):
                base = kn + Vector((0, off, 0))
                rbox("Claw_Prong_%d_%s" % (i, side), (0.022, 0.024, 0.050), base + fdir * 0.020, D, "Fingers_" + side,
                     r=0.008, segs=1, rot=ff)
                tip_dir = (fdir + inward * 0.9).normalized()
                rbox("Claw_Tip_%d_%s" % (i, side), (0.020, 0.022, 0.032), base + fdir * 0.042 + tip_dir * 0.012, W,
                     "Fingers_" + side, r=0.007, segs=1, rot=frame_from_dir(tip_dir))
            rbox("Claw_Thumb_" + side, (0.024, 0.026, 0.060), tb + tdir * 0.022, D, "Thumb_" + side, r=0.008,
                 segs=1, rot=frame_from_dir(tdir))

    # ================= 1.14 seams and bolts of the concept art, on the core plates (every type) =================
    # built around y = 0 and laid onto the plate's front (conform_front), half sunk in so they stay connected
    def front_detail(name, bm, target, cx, cz, mat, bone):
        conform_front(bm, target, (cx, cz), gap=0.0)
        add_part(name, bm, mat, bone)

    def strip(w, h, d=0.006):
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=(w, d, h), verts=bm.verts[:])
        for i in range(1, 8):           # cuts, so the strip bends with the rounded plate
            geom = bm.verts[:] + bm.edges[:] + bm.faces[:]
            bmesh.ops.bisect_plane(bm, geom=geom, plane_co=(w * (i / 8 - 0.5), 0, 0), plane_no=(1, 0, 0))
        return bm

    def bolt(r=0.011, d=0.008):
        bm = bmesh.new()
        bmesh.ops.create_cone(bm, cap_ends=True, segments=8, radius1=r, radius2=r * 0.8, depth=d)
        bmesh.ops.rotate(bm, verts=bm.verts[:], cent=(0, 0, 0), matrix=Matrix.Rotation(math.radians(90), 3, "X"))
        return bm

    for s, side in ((1, "L"), (-1, "R")):
        x0 = s * LEG_X
        front_detail("Shin_Seam_" + side, strip(0.120, 0.008), "Shin_" + side, x0, 0.180, D, "LowerLeg_" + side)
        front_detail("Thigh_Seam_" + side, strip(0.110, 0.008), "Thigh_" + side, x0, 0.315, D, "UpperLeg_" + side)
        for i, dx in enumerate((-0.030, 0.030)):
            front_detail("Shin_Bolt_%d_%s" % (i, side), bolt(), "Shin_" + side, x0 + dx, 0.142, G, "LowerLeg_" + side)
        front_detail("Hips_Bolt_" + side, bolt(), "Hips", s * 0.120, 0.470, G, "Pelvis")
        front_detail("Chest_Bolt_" + side, bolt(), "Chest_Plate", s * 0.165, 0.770, G, "Spine")

    # ================= 1.14 form "Wired": open cable strands (rigid per bone, they never cross a joint) =================
    with form_parts("Wired"):
        for s, side in ((1, "L"), (-1, "R")):
            sh, el, wr = J["shoulder_" + side], J["elbow_" + side], J["wrist_" + side]
            d1, d2 = J["dir_upper_" + side], J["dir_fore_" + side]
            out, back = Vector((s, 0, 0)), Vector((0, 1, 0))       # outside and back of the arm
            for i, (off, mat) in enumerate(((out * 0.054, O), ((out + back).normalized() * 0.054, D))):
                tube("Wire_UpperArm_%d_%s" % (i, side), bowed(sh + d1 * 0.075 + off, el - d1 * 0.03 + off,
                                                             off.normalized() * 0.012), 0.008, mat, "UpperArm_" + side)
                tube("Wire_Forearm_%d_%s" % (i, side), bowed(el + d2 * 0.05 + off * 1.15, wr - d2 * 0.03 + off,
                                                            off.normalized() * 0.012), 0.008, mat, "LowerArm_" + side)
            x0 = s * LEG_X
            for i, (dx, dy, mat) in enumerate(((0.068, -0.010, O), (0.052, -0.052, D))):
                tube("Wire_Thigh_%d_%s" % (i, side), bowed((x0 + s * dx, dy, 0.375), (x0 + s * dx, dy, 0.285),
                                                          (s * 0.012, 0, 0)), 0.008, mat, "UpperLeg_" + side)
                tube("Wire_Shin_%d_%s" % (i, side), bowed((x0 + s * dx, dy, 0.205), (x0 + s * dx, dy, 0.125),
                                                         (s * 0.012, 0, 0)), 0.008, mat, "LowerLeg_" + side)
            tube("Wire_Neck_" + side, bowed((s * 0.072, 0.035, 0.955), (s * 0.090, 0.035, 0.900), (s * 0.010, 0.0, 0)),
                 0.009, O if s > 0 else D, "Head")
            tube("Wire_Belly_" + side, bowed((s * 0.120, -0.140, 0.690), (s * 0.105, -0.115, 0.585), (0, -0.016, 0)),
                 0.009, D if s > 0 else O, "Spine")

    # ================= accessories (on socket bones) =================
    with variant("AccHead", "Antenna"):
        cylinder("Antenna_Base", 0.032, 0.050, (0, 0.0, 1.300), D, "Socket_Head", segs=14, bev=0.008)
        cylinder("Antenna_Rod", 0.007, 0.160, (0, 0.0, 1.400), G, "Antenna", segs=8)
        sphere("Antenna_Tip", 0.022, (0, 0.0, 1.482), O, "Antenna", segs=12, rings=7)
    with variant("AccHead", "PropellerCap"):
        sphere("Cap_Dome", 0.20, (0, 0.02, 1.270), F, "Socket_Head", segs=20, rings=10, scale=(1.0, 1.0, 0.55))
        prism("Cap_Bill", ellipse(0.22, 0.14, 20), 0.014, "Z", (0, -0.17, 1.272), F, "Socket_Head", r=0.005, segs=1)
        cylinder("Cap_Stem", 0.010, 0.050, (0, 0.02, 1.395), G, "Socket_Head", segs=8)
        for i, ang in enumerate((0, 90)):
            rbox("Cap_Blade_%d" % i, (0.24, 0.032, 0.008), (0, 0.02, 1.420), O if i else W, "Socket_Head", r=0.003,
                 segs=1, rot=(0, 0, ang))
            SPIN_PARTS.add("Cap_Blade_%d" % i)
        sphere("Cap_Hub", 0.018, (0, 0.02, 1.424), D, "Socket_Head", segs=10, rings=6)
        SPIN_PARTS.add("Cap_Hub")
    with variant("AccFace", "Shades"):
        for s in (1, -1):
            prism("Shades_Lens_%s" % ("L" if s > 0 else "R"), rrect(0.172, 0.080, 0.030, 4), 0.016, "Y",
                  (s * 0.090, -0.242, 1.120), V, "Head", r=0.004, segs=1,
                  cuts_x=[s * 0.088 + d for d in (-0.05, -0.025, 0, 0.025, 0.05)], world_deform=visor_bend)
            rbox("Shades_Arm_%s" % ("L" if s > 0 else "R"), (0.012, 0.20, 0.016), (s * 0.172, -0.135, 1.135), D,
                 "Head", r=0.004, segs=1)
        rbox("Shades_Bridge", (0.050, 0.014, 0.016), (0, -0.243, 1.130), D, "Head", r=0.004, segs=1)
    with variant("AccNeck", "BowTie"):
        for s in (1, -1):
            wing = [(0.0, 0.0), (s * 0.075, 0.035), (s * 0.075, -0.035)]
            prism("Bow_Wing_%s" % ("L" if s > 0 else "R"), wing if s < 0 else list(reversed(wing)), 0.020, "Y",
                  (0, -0.172, 0.872), F, "Socket_Neck", r=0.006, segs=1)
        rbox("Bow_Knot", (0.034, 0.026, 0.032), (0, -0.176, 0.872), F, "Socket_Neck", r=0.008, segs=1)
    with variant("AccNeck", "Scarf"):
        cylinder("Scarf_Ring", 0.124, 0.032, (0, 0.010, 0.908), F, "Socket_Neck", segs=20, bev=0.012)
        rbox("Scarf_Tail", (0.060, 0.022, 0.150), (0.075, 0.150, 0.835), F, "Socket_Neck", r=0.008, segs=1,
             rot=(-14, 0, -8))
    with variant("AccBack", "Jetpack"):
        rbox("Jet_Frame", (0.22, 0.060, 0.070), (0, 0.250, 0.790), D, "Socket_Back", r=0.012, segs=1)
        for s in (1, -1):
            sd = "L" if s > 0 else "R"
            cylinder("Jet_Tank_" + sd, 0.050, 0.230, (s * 0.078, 0.292, 0.745), W, "Socket_Back", segs=16, bev=0.012)
            sphere("Jet_Cap_" + sd, 0.046, (s * 0.078, 0.292, 0.860), D, "Socket_Back", segs=14, rings=7,
                   scale=(1, 1, 0.6))
            cylinder("Jet_Nozzle_" + sd, 0.034, 0.050, (s * 0.078, 0.292, 0.611), D, "Socket_Back", segs=14,
                     r2=0.044)
            cylinder("Jet_Flame_" + sd, 0.030, 0.006, (s * 0.078, 0.292, 0.586), O, "Socket_Back", segs=14)

    # ---------------- more head accessories ----------------
    with variant("AccHead", "Headphones"):
        torus("Phones_Band", 0.248, 0.013, (0, 0.010, 1.100), D, "Socket_Head", axis=(0, -1, 0), segs=20, rsegs=8,
              arc=180.0)
        rbox("Phones_Pad", (0.15, 0.050, 0.022), (0, 0.010, 1.352), F, "Socket_Head", r=0.008, segs=1)
        for s in (1, -1):
            sd = "L" if s > 0 else "R"
            cylinder("Phones_Cup_" + sd, 0.070, 0.040, (s * 0.288, 0.010, 1.100), G, "Socket_Head", axis=(1, 0, 0),
                     segs=20, bev=0.010)
            cylinder("Phones_Cushion_" + sd, 0.064, 0.020, (s * 0.262, 0.010, 1.100), F, "Socket_Head",
                     axis=(1, 0, 0), segs=20, bev=0.006)
            torus("Phones_Light_" + sd, 0.045, 0.005, (s * 0.309, 0.010, 1.100), O, "Socket_Head", axis=(1, 0, 0),
                  segs=20, rsegs=6)
    with variant("AccHead", "TopHat"):
        # glossy black like the visor (skin-independent); the band takes the fabric colour
        cylinder("Hat_Brim", 0.200, 0.014, (0, 0.0, 1.302), V, "Socket_Head", segs=28, bev=0.005)
        cylinder("Hat_Crown", 0.125, 0.220, (0, 0.0, 1.418), V, "Socket_Head", segs=24, r2=0.132, bev=0.010)
        cylinder("Hat_Band", 0.129, 0.040, (0, 0.0, 1.336), F, "Socket_Head", segs=24, r2=0.130)
    with variant("AccHead", "Crown"):
        cylinder("Crown_Ring", 0.110, 0.070, (0, 0.0, 1.330), G, "Socket_Head", segs=24, bev=0.008)
        cylinder("Crown_Velvet", 0.098, 0.030, (0, 0.0, 1.355), F, "Socket_Head", segs=20, bev=0.010)
        for i in range(6):
            a = 2 * math.pi * i / 6
            c = Vector((0.100 * math.cos(a), 0.100 * math.sin(a), 1.392))
            cylinder("Crown_Spike_%d" % i, 0.026, 0.070, c, G, "Socket_Head", segs=8, r2=0.004)
            sphere("Crown_Gem_%d" % i, 0.016, (0.113 * math.cos(a), 0.113 * math.sin(a), 1.330), O, "Socket_Head",
                   segs=10, rings=6)
    with variant("AccHead", "Mohawk"):
        for i, (yy, h) in enumerate(((-0.140, 0.085), (-0.070, 0.115), (0.0, 0.130), (0.070, 0.115), (0.140, 0.085))):
            fin = [(-0.040, 0.0), (0.040, 0.0), (0.026, h), (0.008, h)]
            prism("Mohawk_Fin_%d" % i, fin, 0.024, "X", (0, yy, 1.272), F, "Socket_Head", r=0.006, segs=1)

    # ---------------- more face accessories ----------------
    with variant("AccFace", "Monocle"):
        torus("Monocle_Ring", 0.056, 0.008, (-EYE_X, -0.236, EYE_Z), G, "Head", axis=(0, -1, 0), segs=24, rsegs=6)
        a = math.radians(-135)
        start = Vector((-EYE_X + 0.056 * math.cos(a) * -1, -0.236, EYE_Z + 0.056 * math.sin(a)))
        cyl_between("Monocle_Chain", start, (-0.255, -0.06, 1.05), 0.0045, G, "Head", segs=6)
    with variant("AccFace", "Mustache"):
        for s in (1, -1):
            sd = "L" if s > 0 else "R"
            prism("Mustache_" + sd, ellipse(0.105, 0.036, 18), 0.018, "Y", (s * 0.052, -0.229, 1.040), D, "Head",
                  r=0.006, segs=1, rot=(0, -s * 14, 0), cuts_x=[s * 0.052 + d for d in (-0.04, 0.0, 0.04)],
                  world_deform=visor_bend)
            prism("Mustache_Curl_" + sd, ellipse(0.034, 0.034, 14), 0.018, "Y", (s * 0.108, -0.226, 1.060), D,
                  "Head", r=0.006, segs=1, world_deform=visor_bend)
    with variant("AccFace", "Goggles"):
        n = Vector((0, -0.6, 0.8)).normalized()
        for s in (1, -1):
            sd = "L" if s > 0 else "R"
            c = Vector((s * 0.075, -0.166, 1.247))
            cylinder("Goggle_Rim_" + sd, 0.054, 0.036, c, G, "Head", axis=n, segs=18, bev=0.008)
            cylinder("Goggle_Glass_" + sd, 0.042, 0.040, c + n * 0.002, V, "Head", axis=n, segs=18)
        rbox("Goggle_Strap", (0.46, 0.030, 0.030), (0, -0.140, 1.262), D, "Head", r=0.008, segs=1, rot=(-37, 0, 0))

    # ---------------- more neck accessories ----------------
    with variant("AccNeck", "Medal"):
        for s, top in ((1, -0.030), (-1, -0.100)):
            a, b = Vector((top, -0.168, 0.888)), Vector((-0.065, -0.176, 0.745))
            d = (b - a)
            rbox("Medal_Ribbon_%d" % (0 if s > 0 else 1), (0.020, 0.006, d.length), (a + b) / 2, F, "Socket_Neck",
                 r=0.002, segs=1, rot=frame_from_dir(d))
        cylinder("Medal_Disc", 0.032, 0.012, (-0.065, -0.172, 0.722), G, "Socket_Neck", axis=(0, 1, 0), segs=20,
                 bev=0.004)
        prism("Medal_Star", [(0.016 * math.cos(math.radians(90 + k * 36)) * (1 if k % 2 == 0 else 0.45),
                              0.016 * math.sin(math.radians(90 + k * 36)) * (1 if k % 2 == 0 else 0.45))
                             for k in range(10)], 0.006, "Y", (-0.065, -0.180, 0.722), O, "Socket_Neck")
    with variant("AccNeck", "Bell"):
        torus("Bell_Collar", 0.122, 0.012, (0, 0.010, 0.905), F, "Socket_Neck", segs=28, rsegs=6)
        sphere("Bell_Body", 0.026, (0, -0.124, 0.870), G, "Socket_Neck", segs=14, rings=8)
        rbox("Bell_Slit", (0.030, 0.010, 0.006), (0, -0.148, 0.862), D, "Socket_Neck", r=0.002, segs=1)

    # ---------------- more back accessories ----------------
    with variant("AccBack", "Cape"):
        rows = [(0.830, 0.235, 0.170), (0.800, 0.268, 0.190), (0.760, 0.290, 0.205), (0.700, 0.302, 0.218),
                (0.620, 0.310, 0.228), (0.520, 0.320, 0.240)]
        bm = bmesh.new()
        grid = []
        cols = 8
        for z, y, hw in rows:
            grid.append([bm.verts.new((-hw + 2 * hw * c / cols, y + 0.012 * math.cos(math.pi * c / cols * 2), z))
                         for c in range(cols + 1)])
        for r_ in range(len(rows) - 1):
            for c in range(cols):
                bm.faces.new((grid[r_][c], grid[r_][c + 1], grid[r_ + 1][c + 1], grid[r_ + 1][c]))
        bmesh.ops.solidify(bm, geom=bm.faces[:], thickness=0.012)
        add_part("Cape_Cloth", bm, F, "Cape")
        cylinder("Cape_Clasp", 0.022, 0.085, (0, 0.200, 0.828), O, "Socket_Back", axis=(0, 1, 0), segs=14)
    with variant("AccBack", "Wings"):
        wing = [(0.0, 0.0), (0.10, 0.08), (0.27, 0.19), (0.30, 0.13), (0.21, 0.05), (0.25, -0.02), (0.12, -0.03)]
        for s in (1, -1):
            sd = "L" if s > 0 else "R"
            pts = [(s * u, v) for u, v in wing]
            prism("Wing_" + sd, pts if s > 0 else list(reversed(pts)), 0.022, "Y", (s * 0.065, 0.250, 0.790), W,
                  "Socket_Back", r=0.006, segs=1)
            prism("Wing_Edge_" + sd, [(s * 0.10, 0.075), (s * 0.27, 0.185), (s * 0.255, 0.165), (s * 0.095, 0.060)]
                  if s > 0 else [(s * 0.095, 0.060), (s * 0.255, 0.165), (s * 0.27, 0.185), (s * 0.10, 0.075)],
                  0.026, "Y", (s * 0.065, 0.250, 0.790), O, "Socket_Back", r=0.004, segs=1)
            cylinder("Wing_Joint_" + sd, 0.028, 0.050, (s * 0.068, 0.240, 0.790), D, "Socket_Back", axis=(0, 1, 0),
                     segs=12, bev=0.006)

    # ================= 1.12 Bolt-On: attachments =================
    # head tops (Socket_Head; the Classic shell is flat up to |x| 0.12 at z 1.303, rounded with r 0.14 beyond)
    with variant("AccHead", "MinerHelmet"):
        # the shell stays above the eye screen (z <= 1.22 at its front), which moves up when the robot looks up
        sphere("Miner_Shell", 0.290, (0, 0.010, 1.275), W, "Socket_Head", segs=24, rings=12, scale=(1.0, 0.88, 0.36))
        cylinder("Miner_Brim", 0.300, 0.016, (0, 0.010, 1.278), D, "Socket_Head", segs=28, bev=0.005,
                 scale=(1.0, 0.90, 1.0))
        rbox("Miner_Ridge", (0.050, 0.30, 0.030), (0, 0.035, 1.372), D, "Socket_Head", r=0.012, segs=1,
             rot=(-6, 0, 0))
        lamp_dir = Vector((0, -1.0, 0.30)).normalized()
        lamp = Vector((0, -0.224, 1.318))
        rbox("Miner_LampMount", (0.070, 0.030, 0.050), lamp - lamp_dir * 0.004, D, "Socket_Head", r=0.010, segs=1,
             rot=frame_from_dir(lamp_dir))
        cylinder("Miner_LampHousing", 0.040, 0.050, lamp + lamp_dir * 0.028, G, "Socket_Head", axis=lamp_dir,
                 segs=18, bev=0.008)
        cylinder("Miner_LampGlass", 0.031, 0.010, lamp + lamp_dir * 0.054, O, "Socket_Head", axis=lamp_dir, segs=18)
    with variant("AccHead", "Kabuto"):
        sphere("Kabuto_Bowl", 0.285, (0, 0.010, 1.240), W, "Socket_Head", segs=24, rings=12, scale=(1.0, 0.90, 0.44))
        torus("Kabuto_Rim", 0.272, 0.014, (0, 0.010, 1.240), D, "Socket_Head", segs=28, rsegs=6)
        for s in (1, -1):
            sd = "L" if s > 0 else "R"
            rbox("Kabuto_Guard_" + sd, (0.022, 0.300, 0.110), (s * 0.282, 0.050, 1.190), D, "Socket_Head",
                 r=0.008, segs=1, rot=(0, s * 24, 0))
            rbox("Kabuto_Wing_" + sd, (0.020, 0.075, 0.095), (s * 0.238, -0.140, 1.228), W, "Socket_Head",
                 r=0.008, segs=1, rot=(0, s * 30, s * 28))
            horn = [(0.0, 0.0), (0.028, 0.0), (0.150, 0.215), (0.122, 0.222)]
            prism("Kabuto_Horn_" + sd, [(s * u, v) for u, v in (horn if s > 0 else list(reversed(horn)))], 0.012,
                  "Y", (s * 0.030, -0.232, 1.300), O, "Socket_Head", r=0.003, segs=1)
        rbox("Kabuto_Neckguard", (0.420, 0.024, 0.115), (0, 0.235, 1.185), D, "Socket_Head", r=0.008, segs=1,
             rot=(-24, 0, 0))
        cylinder("Kabuto_Crest", 0.036, 0.016, (0, -0.236, 1.306), O, "Socket_Head", axis=(0, -1, 0), segs=16,
                 bev=0.004)
    with variant("AccHead", "Spikes"):
        spikes = (((0.0, 0.0, 1.296), (0.0, 0.0, 1.0)),
                  ((0.155, 0.0, 1.282), (0.55, 0.0, 0.84)), ((-0.155, 0.0, 1.282), (-0.55, 0.0, 0.84)),
                  ((0.0, 0.130, 1.282), (0.0, 0.55, 0.84)), ((0.0, -0.115, 1.290), (0.0, -0.35, 0.94)),
                  ((0.110, 0.110, 1.280), (0.42, 0.42, 0.80)), ((-0.110, 0.110, 1.280), (-0.42, 0.42, 0.80)))
        for i, (base, d) in enumerate(spikes):
            d = Vector(d).normalized()
            b = Vector(base)
            cylinder("Spike_Base_%d" % i, 0.040, 0.020, b, D, "Socket_Head", axis=d, segs=12, bev=0.005)
            cylinder("Spike_%d" % i, 0.032, 0.105 if i else 0.125, b + d * (0.062 if i else 0.072), G, "Socket_Head",
                     axis=d, segs=10, r2=0.002)
    with variant("AccHead", "Ears"):
        for s in (1, -1):
            sd = "L" if s > 0 else "R"
            outer = [(0.0, 0.0), (0.120, 0.0), (0.085, 0.165)]
            inner = [(0.026, 0.020), (0.094, 0.020), (0.074, 0.112)]
            for nm, pts, depth, y, mat in (("Ear_Fin_", outer, 0.050, 0.020, W),
                                           ("Ear_Inner_", inner, 0.012, -0.010, O)):
                pts = [(s * (u - 0.060), v) for u, v in pts]
                prism(nm + sd, pts if s > 0 else list(reversed(pts)), depth, "Y", (s * 0.130, y, 1.268), mat,
                      "Socket_Head", r=0.006 if mat == W else 0.002, segs=1, rot=(0, s * 14, 0))
    with variant("AccHead", "Dish"):
        cylinder("Dish_Mast", 0.012, 0.130, (0.120, 0.060, 1.355), D, "Socket_Head", segs=10)
        cylinder("Dish_Foot", 0.034, 0.020, (0.120, 0.060, 1.298), G, "Socket_Head", segs=14, bev=0.005)
        dd = Vector((0.35, -0.45, 0.82)).normalized()
        dc = Vector((0.120, 0.060, 1.440))
        sphere("Dish_Joint", 0.020, dc - dd * 0.012, G, "Socket_Head", segs=12, rings=7)
        cylinder("Dish_Bowl", 0.040, 0.050, dc + dd * 0.020, W, "Socket_Head", axis=dd, segs=24, r2=0.120,
                 bev=0.006)
        cylinder("Dish_Face", 0.106, 0.006, dc + dd * 0.046, D, "Socket_Head", axis=dd, segs=24)
        cyl_between("Dish_Feed", dc + dd * 0.046, dc + dd * 0.125, 0.006, G, "Socket_Head", segs=8)
        sphere("Dish_Tip", 0.014, dc + dd * 0.130, O, "Socket_Head", segs=10, rings=6)
    with variant("AccHead", "Chimney"):
        cx, cy = 0.115, 0.055
        cylinder("Chimney_Foot", 0.058, 0.026, (cx, cy, 1.296), D, "Socket_Head", segs=16, bev=0.006)
        cylinder("Chimney_Stack", 0.038, 0.170, (cx, cy, 1.390), G, "Socket_Head", segs=16, r2=0.034)
        cylinder("Chimney_Band", 0.041, 0.018, (cx, cy, 1.360), D, "Socket_Head", segs=16, bev=0.004)
        cylinder("Chimney_Top", 0.034, 0.030, (cx, cy, 1.482), G, "Socket_Head", segs=16, r2=0.052, bev=0.004)
        cylinder("Chimney_Glow", 0.030, 0.006, (cx, cy, 1.496), O, "Socket_Head", segs=16)
        EMITTERS.append({"id": "ChimneySmoke", "kind": "smoke", "slot": "AccHead", "variant": "Chimney",
                         "bone": "Socket_Head", "position": (cx, cy, 1.505), "direction": (0.0, 0.0, 1.0)})
    with variant("AccHead", "Halo"):
        # floats above the head (FLOATING_PARTS): rigid on the socket, so it follows every clip
        torus("Halo_Ring", 0.150, 0.013, (0, 0.020, 1.440), O, "Socket_Head", axis=(0, 0.26, 1.0), segs=32, rsegs=8)
    with variant("AccHead", "Sprout"):
        stem_a, stem_b = Vector((0.0, 0.020, 1.296)), Vector((0.012, 0.030, 1.415))
        cylinder("Sprout_Pot", 0.034, 0.020, stem_a, D, "Socket_Head", segs=14, bev=0.005)
        cyl_between("Sprout_Stem", stem_a, stem_b, 0.009, D, "Socket_Head", segs=8)
        for s in (1, -1):
            sd = "L" if s > 0 else "R"
            prism("Sprout_Leaf_" + sd, ellipse(0.120, 0.050, 16), 0.010, "Y",
                  stem_b + Vector((s * 0.058, 0.0, 0.020)), F, "Socket_Head", r=0.003, segs=1, rot=(0, -s * 28, 0))
        sphere("Sprout_Bud", 0.016, stem_b + Vector((0, 0, 0.010)), O, "Socket_Head", segs=10, rings=6)

    # ear pieces (Head; Classic ear cap ends at |x| 0.311). A stub from inside the shell keeps them mounted on every
    # head variant. Only on the left ear: the right hand passes the right ear within 1 cm (Wave, Think).
    with variant("AccEar", "LensEye"):
        cyl_between("Lens_Stub", (0.200, 0.010, 1.100), (0.310, 0.010, 1.100), 0.040, D, "Head", segs=14)
        cylinder("Lens_Housing", 0.078, 0.070, (0.345, 0.010, 1.100), D, "Head", axis=(1, 0, 0), segs=24, bev=0.010)
        cylinder("Lens_Ring", 0.070, 0.016, (0.384, 0.010, 1.100), G, "Head", axis=(1, 0, 0), segs=24, bev=0.004)
        cylinder("Lens_Glass", 0.056, 0.012, (0.388, 0.010, 1.100), V, "Head", axis=(1, 0, 0), segs=24)
        cylinder("Lens_Iris", 0.022, 0.006, (0.395, 0.010, 1.100), O, "Head", axis=(1, 0, 0), segs=16)
    with variant("AccEar", "Gears"):
        for s in (1,):
            sd = "L" if s > 0 else "R"
            cyl_between("Gear_Stub_" + sd, (s * 0.200, 0.010, 1.100), (s * 0.312, 0.010, 1.100), 0.030, D, "Head",
                        segs=12)
            for k, (c, rad, n, th) in enumerate((((s * 0.322, 0.010, 1.100), 0.064, 10, 0.020),
                                                 ((s * 0.312, 0.085, 1.175), 0.036, 7, 0.016))):
                c = Vector(c)
                cylinder("Gear_Disc_%d_%s" % (k, sd), rad, th, c, G, "Head", axis=(1, 0, 0), segs=20, bev=0.003)
                cylinder("Gear_Hub_%d_%s" % (k, sd), rad * 0.32, th + 0.010, c, D, "Head", axis=(1, 0, 0), segs=12)
                for t in range(n):
                    a = 2 * math.pi * (t + 0.5 * k) / n
                    off = Vector((0.0, math.cos(a), math.sin(a)))
                    rbox("Gear_Tooth_%d_%d_%s" % (k, t, sd), (th, 0.020, 0.024), c + off * (rad + 0.008), G, "Head",
                         r=0.003, segs=1, rot=(math.degrees(a) - 90, 0, 0))
    with variant("AccEar", "Dial"):
        for s in (1,):
            sd = "L" if s > 0 else "R"
            cyl_between("Dial_Stub_" + sd, (s * 0.200, 0.010, 1.100), (s * 0.312, 0.010, 1.100), 0.030, D, "Head",
                        segs=12)
            c = Vector((s * 0.322, 0.010, 1.100))
            cylinder("Dial_Rim_" + sd, 0.080, 0.024, c, G, "Head", axis=(1, 0, 0), segs=28, bev=0.005)
            cylinder("Dial_Face_" + sd, 0.068, 0.006, c + Vector((s * 0.012, 0, 0)), W, "Head", axis=(1, 0, 0),
                     segs=28)
            for t in range(4):
                a = math.pi / 2 * t
                off = Vector((0.0, math.cos(a), math.sin(a)))
                rbox("Dial_Tick_%d_%s" % (t, sd), (0.006, 0.010, 0.014), c + Vector((s * 0.016, 0, 0)) + off * 0.054,
                     D, "Head", r=0.002, segs=1, rot=(math.degrees(a) - 90, 0, 0))
            for k, (ln, ang) in enumerate(((0.048, 20), (0.034, 120))):
                a = math.radians(ang)
                off = Vector((0.0, math.cos(a), math.sin(a)))
                rbox("Dial_Hand_%d_%s" % (k, sd), (0.006, 0.010, ln), c + Vector((s * 0.018, 0, 0)) + off * ln / 2,
                     D, "Head", r=0.002, segs=1, rot=(ang - 90, 0, 0))
            cylinder("Dial_Pin_" + sd, 0.008, 0.012, c + Vector((s * 0.020, 0, 0)), O, "Head", axis=(1, 0, 0),
                     segs=10)

    # face rims (Head)
    with variant("AccFace", "LensRims"):
        for s in (1, -1):
            sd = "L" if s > 0 else "R"
            deform_part(torus("LensRim_" + sd, 0.064, 0.013, (s * EYE_X, -0.226, EYE_Z), G, "Head", axis=(0, -1, 0),
                              segs=28, rsegs=6), visor_bend)
        rbox("LensRim_Bridge", (0.040, 0.014, 0.018), (0, -0.228, EYE_Z + 0.012), G, "Head", r=0.005, segs=1)
    with variant("AccFace", "SpaceRing"):
        conform_front(ellipse_ring("SpaceRing_Bezel", 0.236, 0.126, 0.012, D, "Head"), "Head_Shell", (0, 1.112),
                      gap=0.005)
        for s in (1, -1):
            cylinder("SpaceRing_Light_" + ("L" if s > 0 else "R"), 0.016, 0.040, (s * 0.262, -0.120, 1.205), O, "Head",
                     axis=(s * 0.5, -0.6, 0.5), segs=12, bev=0.004)

    # back (Socket_Back, behind the Classic backpack that ends at y 0.22)
    with variant("AccBack", "Pouches"):
        # low enough that the head never reaches it (crouching tilts the head back over the shoulders)
        rbox("Pack_Body", (0.250, 0.100, 0.200), (0, 0.262, 0.735), F, "Socket_Back", r=0.030, segs=2)
        rbox("Pack_Flap", (0.256, 0.070, 0.050), (0, 0.268, 0.822), D, "Socket_Back", r=0.016, segs=1)
        for s in (1, -1):
            sd = "L" if s > 0 else "R"
            rbox("Pack_Pouch_" + sd, (0.090, 0.050, 0.090), (s * 0.062, 0.330, 0.700), F, "Socket_Back", r=0.016,
                 segs=1)
            rbox("Pack_PouchFlap_" + sd, (0.094, 0.030, 0.034), (s * 0.062, 0.340, 0.745), D, "Socket_Back",
                 r=0.008, segs=1)
            rbox("Pack_Strap_" + sd, (0.030, 0.012, 0.190), (s * 0.090, 0.315, 0.735), D, "Socket_Back", r=0.004,
                 segs=1)
        cylinder("Pack_Roll", 0.040, 0.270, (0, 0.262, 0.602), W, "Socket_Back", axis=(1, 0, 0), segs=16, bev=0.010)

    # ---------------- items in the right hand (Prop_R) ----------------
    grip = J["knuckle_R"] + Vector((0.030, -0.005, 0.0))
    d = Vector((-0.65, -0.70, 0.30)).normalized()       # outwards-forwards: away from the head when the arm is raised
    fr = frame_from_dir(d)
    out = Vector((-1.0, 0.0, 0.0))
    with variant("AccHand", "Wrench"):
        rbox("Wrench_Handle", (0.022, 0.012, 0.120), grip + d * 0.035, G, "Prop_R", r=0.005, segs=1, rot=fr)
        for k, off in enumerate((-0.019, 0.019)):
            rbox("Wrench_Jaw_%d" % k, (0.014, 0.014, 0.036), grip + d * 0.112 + fr.col[0] * off, G, "Prop_R",
                 r=0.004, segs=1, rot=fr)
        rbox("Wrench_Head", (0.052, 0.014, 0.018), grip + d * 0.096, G, "Prop_R", r=0.004, segs=1, rot=fr)
    with variant("AccHand", "Flower"):
        cyl_between("Flower_Stem", grip - d * 0.020, grip + d * 0.130, 0.006, D, "Prop_R", segs=8)
        head = grip + d * 0.140
        sphere("Flower_Center", 0.018, head, O, "Prop_R", segs=12, rings=7)
        for k in range(6):
            a = 2 * math.pi * k / 6
            off = fr.col[0] * math.cos(a) + fr.col[1] * math.sin(a)
            sphere("Flower_Petal_%d" % k, 0.022, head + off * 0.026, F, "Prop_R", segs=10, rings=6,
                   scale=(1.0, 1.0, 0.45))
        prism("Flower_Leaf", ellipse(0.044, 0.018, 12), 0.006, "Y", grip + d * 0.065 + out * 0.016, D, "Prop_R")
    with variant("AccHand", "Flag"):
        cyl_between("Flag_Pole", grip - d * 0.030, grip + d * 0.230, 0.007, G, "Prop_R", segs=8)
        sphere("Flag_Tip", 0.013, grip + d * 0.236, O, "Prop_R", segs=10, rings=6)
        rbox("Flag_Cloth", (0.110, 0.004, 0.075), grip + d * 0.180 - fr.col[0] * 0.058, F, "Prop_R", r=0.002, segs=1,
             rot=fr)


__all__ = [_n for _n in dir() if not _n.startswith("__")]
