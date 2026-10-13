"""Geometry helpers that build the parts (boxes, prisms, cylinders, tubes ...)."""

from robogen.materials import *  # noqa: F401,F403 - one shared namespace, see robogen/__init__.py


# ----------------------------------------------------------------------------------------------
# Geometry kit (bmesh, world coordinates, every part rigidly bound to one bone)
# ----------------------------------------------------------------------------------------------



def add_part(name, bm, mat, bone):
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    slot, var = VARIANT
    PARTS.append({"name": name, "bone": bone, "mat": mat, "bm": bm, "slot": slot, "variant": var,
                  "group": group_key(slot, var)})
    return bm


def to_matrix(rot):
    if rot is None:
        return Matrix.Identity(3)
    if isinstance(rot, Matrix):
        return rot.to_3x3()
    if isinstance(rot, Quaternion):
        return rot.to_matrix()
    return Euler([math.radians(a) for a in rot], "XYZ").to_matrix()


def place(bm, center, rot=None):
    m = Matrix.Translation(Vector(center)) @ to_matrix(rot).to_4x4()
    bmesh.ops.transform(bm, matrix=m, verts=bm.verts[:])


def bevel(bm, edges, r, segs):
    if r <= 0 or not edges:
        return
    verts = list({v for e in edges for v in e.verts})
    bmesh.ops.bevel(bm, geom=verts + list(edges), offset=r, offset_type="OFFSET", segments=segs,
                    profile=0.5, affect="EDGES", clamp_overlap=True)


def frame_from_dir(d, side=Vector((0, 1, 0))):
    """3x3 frame whose Z axis points along d (Y stays close to 'side')."""
    z = Vector(d).normalized()
    x = side.cross(z)
    if x.length < 1e-6:
        x = Vector((1, 0, 0)).cross(z)
    x.normalize()
    y = z.cross(x)
    return Matrix((x, y, z)).transposed()


def rbox(name, size, center, mat, bone, r=0.02, segs=2, taper_top=(1, 1), taper_bot=(1, 1), rot=None,
         deform=None):
    """Rounded (optionally tapered) box. Taper scales the +Z / -Z face in local X/Y."""
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    for v in bm.verts:
        tx, ty = taper_top if v.co.z > 0 else taper_bot
        v.co = Vector((v.co.x * size[0] * tx, v.co.y * size[1] * ty, v.co.z * size[2]))
    r = min(r, 0.49 * min(size[0] * min(taper_top[0], taper_bot[0]), size[1] * min(taper_top[1], taper_bot[1]), size[2]))
    bevel(bm, bm.edges[:], r, segs)
    if deform:
        for v in bm.verts:
            v.co = deform(v.co.copy())
    place(bm, center, rot)
    return add_part(name, bm, mat, bone)


def rrect(w, h, r, seg=4):
    """Rounded rectangle outline (counter-clockwise)."""
    r = min(r, w * 0.5 - 1e-4, h * 0.5 - 1e-4)
    pts = []
    corners = [(w / 2 - r, h / 2 - r, 0), (-w / 2 + r, h / 2 - r, 90), (-w / 2 + r, -h / 2 + r, 180),
               (w / 2 - r, -h / 2 + r, 270)]
    for cx, cy, a0 in corners:
        for i in range(seg + 1):
            a = math.radians(a0 + 90.0 * i / seg)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def ellipse(w, h, n=20):
    return [(w / 2 * math.cos(2 * math.pi * i / n), h / 2 * math.sin(2 * math.pi * i / n)) for i in range(n)]


def prism(name, pts, depth, axis, center, mat, bone, r=0.0, segs=2, back_scale=1.0, rot=None, deform=None,
          cuts_x=(), world_deform=None):
    """Extrudes a 2D outline. axis 'Y': outline in XZ (front view), 'Z': outline in XY, 'X': outline in YZ."""

    def P(u, v, w):
        if axis == "Y":
            return Vector((u, w, v))
        if axis == "X":
            return Vector((w, u, v))
        return Vector((u, v, w))

    bm = bmesh.new()
    front = [bm.verts.new(P(u, v, -depth / 2)) for u, v in pts]
    back = [bm.verts.new(P(u * back_scale, v * back_scale, depth / 2)) for u, v in pts]
    bm.faces.new(front)
    bm.faces.new(back)
    n = len(pts)
    for i in range(n):
        j = (i + 1) % n
        bm.faces.new((front[i], front[j], back[j], back[i]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    fset = set(front)
    ring = [e for e in bm.edges if (e.verts[0] in fset) == (e.verts[1] in fset)]
    bevel(bm, ring, min(r, depth * 0.49), segs)
    for cx in cuts_x:
        geom = bm.verts[:] + bm.edges[:] + bm.faces[:]
        bmesh.ops.bisect_plane(bm, geom=geom, plane_co=(cx, 0, 0), plane_no=(1, 0, 0))
    if deform:
        for v in bm.verts:
            v.co = deform(v.co.copy())
    place(bm, center, rot)
    if world_deform:
        for v in bm.verts:
            v.co = world_deform(v.co.copy())
    return add_part(name, bm, mat, bone)


VISOR_BEND = 0.55


def visor_bend(co):
    """Wraps face parts around the rounded head (y grows with x^2)."""
    co.y += VISOR_BEND * co.x * co.x
    return co


def cylinder(name, r, length, center, mat, bone, axis=(0, 0, 1), segs=12, r2=None, bev=0.0, bsegs=2,
             scale=(1, 1, 1)):
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=segs, radius1=r,
                          radius2=r if r2 is None else r2, depth=length)
    ring = [e for e in bm.edges if abs(e.verts[0].co.z - e.verts[1].co.z) < 1e-6]
    bevel(bm, ring, min(bev, length * 0.45), bsegs)
    for v in bm.verts:
        v.co = Vector((v.co.x * scale[0], v.co.y * scale[1], v.co.z * scale[2]))
    q = Vector((0, 0, 1)).rotation_difference(Vector(axis).normalized())
    place(bm, center, q)
    return add_part(name, bm, mat, bone)


def cyl_between(name, a, b, r, mat, bone, segs=12, r2=None, bev=0.0):
    a, b = Vector(a), Vector(b)
    return cylinder(name, r, (b - a).length, (a + b) / 2, mat, bone, axis=b - a, segs=segs, r2=r2, bev=bev)


def tube(name, pts, r, mat, bone, segs=6):
    """Closed tube of radius r along a polyline (cables)."""
    pts = [Vector(p) for p in pts]
    bm = bmesh.new()
    rings = []
    for i, p in enumerate(pts):
        fr = frame_from_dir(pts[min(i + 1, len(pts) - 1)] - pts[max(i - 1, 0)])
        rings.append([bm.verts.new(p + fr @ Vector((r * math.cos(2 * math.pi * j / segs),
                                                    r * math.sin(2 * math.pi * j / segs), 0.0)))
                      for j in range(segs)])
    for r0, r1 in zip(rings, rings[1:]):
        for j in range(segs):
            bm.faces.new((r0[j], r0[(j + 1) % segs], r1[(j + 1) % segs], r1[j]))
    bm.faces.new(list(reversed(rings[0])))
    bm.faces.new(rings[-1])
    return add_part(name, bm, mat, bone)


def bowed(a, b, out, n=6):
    """Points from a to b that bulge by the vector out in the middle (a loose cable)."""
    a, b, out = Vector(a), Vector(b), Vector(out)
    return [a.lerp(b, i / (n - 1)) + out * math.sin(math.pi * i / (n - 1)) for i in range(n)]


def torus(name, R, r, center, mat, bone, axis=(0, 0, 1), segs=24, rsegs=8, arc=360.0):
    """Ring (optionally an open arc in the local XY plane starting at +X), local Z mapped to axis."""
    bm = bmesh.new()
    closed = arc >= 359.9
    n = segs if closed else segs + 1
    rings = []
    for i in range(n):
        a = math.radians(arc * i / segs)
        ring = []
        for j in range(rsegs):
            b = 2 * math.pi * j / rsegs
            rr = R + r * math.cos(b)
            ring.append(bm.verts.new((rr * math.cos(a), rr * math.sin(a), r * math.sin(b))))
        rings.append(ring)
    for i in range(segs):
        r0, r1 = rings[i], rings[(i + 1) % n]
        for j in range(rsegs):
            bm.faces.new((r0[j], r0[(j + 1) % rsegs], r1[(j + 1) % rsegs], r1[j]))
    if not closed:
        bm.faces.new(rings[0])
        bm.faces.new(rings[-1])
    q = Vector((0, 0, 1)).rotation_difference(Vector(axis).normalized())
    place(bm, center, q)
    return add_part(name, bm, mat, bone)


def sphere(name, r, center, mat, bone, segs=14, rings=8, scale=(1, 1, 1)):
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=segs, v_segments=rings, radius=r)
    for v in bm.verts:
        v.co = Vector((v.co.x * scale[0], v.co.y * scale[1], v.co.z * scale[2]))
    place(bm, center)
    return add_part(name, bm, mat, bone)


def deform_part(bm, fn):
    """Applies a world-space deform (e.g. visor_bend) to a part that is already placed."""
    for v in bm.verts:
        v.co = fn(v.co.copy())
    return bm


def ellipse_ring(name, a, b, r, mat, bone, segs=48, rsegs=8):
    """Tube of radius r along an ellipse (half axes a in X, b in Z) around the origin, facing -Y; place it with
    conform_front."""
    bm = bmesh.new()
    rings = []
    for i in range(segs):
        t = 2 * math.pi * i / segs
        c = Vector((a * math.cos(t), 0.0, b * math.sin(t)))
        n = Vector((math.cos(t) / a, 0.0, math.sin(t) / b)).normalized()     # in-plane outward normal
        rings.append([bm.verts.new(c + n * (r * math.cos(2 * math.pi * j / rsegs)) +
                                   Vector((0, r * math.sin(2 * math.pi * j / rsegs), 0))) for j in range(rsegs)])
    for i in range(segs):
        r0, r1 = rings[i], rings[(i + 1) % segs]
        for j in range(rsegs):
            bm.faces.new((r0[j], r0[(j + 1) % rsegs], r1[(j + 1) % rsegs], r1[j]))
    return add_part(name, bm, mat, bone)


def conform_front(bm, target, center_xz, gap=0.010):
    """Moves a part built around y = 0 onto the front surface of another part (ray cast along +Y), so a rim follows
    a rounded shell; gap = how far the part's y = 0 plane stays in front of the surface."""
    src = next(p for p in PARTS if p["name"] == target)
    tree = BVHTree.FromBMesh(src["bm"])
    cx, cz = center_xz
    fallback = min(v.co.y for v in src["bm"].verts)
    for v in bm.verts:
        x, z = v.co.x + cx, v.co.z + cz
        hit = tree.ray_cast(Vector((x, -1.0, z)), Vector((0, 1, 0)))
        y0 = hit[0].y if hit[0] is not None else fallback
        v.co = Vector((x, y0 - gap + v.co.y, z))
    return bm


__all__ = [_n for _n in dir() if not _n.startswith("__")]
