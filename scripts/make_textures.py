"""Cube Escape robot - source textures made with ImageMagick.

Creates deterministic detail textures that the Blender shaders project onto the robot before baking:

    T_Src_Grunge.png      tileable multi-octave grime (albedo variation, roughness, micro bump)
    T_Src_Brushed.png     tileable brushed-metal streaks
    T_Src_Scratches.png   tileable fine scratches (worn paint, bump)
    T_Src_Decals.png      RGBA decal atlas: status display UI, cube emblem, unit code, hazard stripes

Every call uses a fixed seed, so a rerun produces identical images.

Usage:  python3 make_textures.py <out_dir> [--magick /path/to/magick]
Also imported by generate_robot.py (make_all).
"""

import concurrent.futures
import hashlib
import math
import os
import random
import shutil
import subprocess
import sys

SIZE = 1024
# Decal atlas layout in pixels (x, y, w, h), origin top-left. generate_robot.py reads this table.
ATLAS = {
    "screen": (0, 0, 512, 410),
    "emblem": (512, 0, 512, 512),
    "code": (0, 512, 512, 128),
    "stripes": (0, 768, 512, 128),
}


def find_magick(explicit=None):
    for cand in (explicit, shutil.which("magick"), "/opt/homebrew/bin/magick", "/usr/local/bin/magick"):
        if cand and os.path.exists(cand):
            return cand
    raise RuntimeError("ImageMagick 'magick' not found - install it (brew install imagemagick)")


def run(magick, args, log=print):
    cmd = [magick] + [str(a) for a in args]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError("magick failed (%d): %s\n%s" % (res.returncode, " ".join(cmd), res.stderr))
    return res


def tile_noise(magick, path, seed, blurs, weights, extra=()):
    """Tileable noise: random pixels blurred with wrap-around (virtual-pixel tile), octaves blended."""
    tmp = []
    for i, b in enumerate(blurs):
        t = path + ".oct%d.png" % i
        run(magick, ["-size", "%dx%d" % (SIZE, SIZE), "xc:gray50", "-seed", seed + i, "+noise", "Random",
                     "-colorspace", "Gray", "-virtual-pixel", "tile", "-blur", "0x%g" % b, "-auto-level", t])
        tmp.append(t)
    args = []
    for t, w in zip(tmp, weights):
        args += ["(", t, "-evaluate", "multiply", w, ")"]
    args += ["-background", "black", "-compose", "plus", "-flatten", "-auto-level"] + list(extra) + [path]
    run(magick, args)
    for t in tmp:
        os.remove(t)


def make_grunge(magick, out):
    path = os.path.join(out, "T_Src_Grunge.png")
    tile_noise(magick, path, 11, [48, 16, 5, 1.5], [0.45, 0.30, 0.17, 0.08],
               extra=["-sigmoidal-contrast", "3,50%", "-depth", "8"])
    return path


def make_brushed(magick, out):
    path = os.path.join(out, "T_Src_Brushed.png")
    run(magick, ["-size", "%dx%d" % (SIZE, SIZE), "xc:gray50", "-seed", 23, "+noise", "Random",
                 "-colorspace", "Gray", "-virtual-pixel", "tile", "-motion-blur", "0x60+90",
                 "-motion-blur", "0x30+270", "-blur", "0x0.6", "-auto-level", "-sigmoidal-contrast", "4,50%",
                 "-depth", "8", path])
    return path


def make_scratches(magick, out):
    path = os.path.join(out, "T_Src_Scratches.png")
    rnd = random.Random(42)
    draws = []
    for _ in range(170):
        x, y = rnd.uniform(0, SIZE), rnd.uniform(0, SIZE)
        length = rnd.uniform(12, 70)
        ang = rnd.choice([rnd.uniform(-0.5, 0.5), rnd.uniform(1.1, 2.0), rnd.uniform(-3.0, 3.0)])
        dx, dy = length * math.cos(ang), length * math.sin(ang)
        shade = rnd.randint(110, 255)
        draws += ["-stroke", "rgb(%d,%d,%d)" % (shade, shade, shade), "-strokewidth", "%.1f" % rnd.uniform(0.8, 1.8),
                  "-draw", "line %.1f,%.1f %.1f,%.1f" % (x, y, x + dx, y + dy)]
    base = path + ".raw.png"
    run(magick, ["-size", "%dx%d" % (SIZE, SIZE), "xc:black"] + draws + ["-blur", "0x0.5", base])
    # make it tileable: overlay a half-offset copy (scratches crossing the border reappear on the other side)
    run(magick, [base, "(", base, "-roll", "+%d+%d" % (SIZE // 2, SIZE // 2), ")", "-compose", "lighten",
                 "-composite", "-colorspace", "Gray", "-depth", "8", path])
    os.remove(base)
    return path


def make_decals(magick, out):
    """RGBA atlas. White/grey artwork on transparent background; colour comes from the shader palette."""
    path = os.path.join(out, "T_Src_Decals.png")
    font = "Arial-Bold"
    sx, sy, sw, sh = ATLAS["screen"]
    ex, ey, ew, eh = ATLAS["emblem"]
    cx, cy, cw, ch = ATLAS["code"]
    tx, ty, tw, th = ATLAS["stripes"]
    # status display: frame, three status bars, battery icon and heartbeat line (luminance mask)
    screen = [
        "(", "-size", "%dx%d" % (sw, sh), "xc:rgb(28,28,28)",
        "-fill", "none", "-stroke", "rgb(150,150,150)", "-strokewidth", 10,
        "-draw", "roundrectangle 14,14 %d,%d 30,30" % (sw - 14, sh - 14),
        "-stroke", "none", "-fill", "white",
        "-draw", "roundrectangle 52,62 330,104 12,12",
        "-draw", "roundrectangle 52,134 270,176 12,12",
        "-draw", "roundrectangle 52,206 380,248 12,12",
        "-fill", "rgb(120,120,120)",
        "-draw", "roundrectangle 340,62 460,104 12,12",
        "-draw", "roundrectangle 280,134 460,176 12,12",
        "-fill", "none", "-stroke", "white", "-strokewidth", 12,
        "-draw", "polyline 40,330 150,330 180,280 215,370 250,300 280,330 470,330",
        ")", "-geometry", "+%d+%d" % (sx, sy), "-composite",
    ]
    # cube emblem: isometric cube outline in a ring (Cube Escape)
    c = (ex + ew // 2, ey + eh // 2)
    r = 150
    top = (c[0], c[1] - r)
    ul, ur = (c[0] - int(r * 0.866), c[1] - r // 2), (c[0] + int(r * 0.866), c[1] - r // 2)
    ll, lr = (c[0] - int(r * 0.866), c[1] + r // 2), (c[0] + int(r * 0.866), c[1] + r // 2)
    bot = (c[0], c[1] + r)
    emblem = [
        "-fill", "none", "-stroke", "white", "-strokewidth", 30,
        "-draw", "circle %d,%d %d,%d" % (c[0], c[1], c[0] + 215, c[1]),
        "-strokewidth", 26,
        "-draw", "stroke-linejoin round polygon %d,%d %d,%d %d,%d %d,%d %d,%d %d,%d" % (top + ur + lr + bot + ll + ul),
        "-draw", "stroke-linejoin round polyline %d,%d %d,%d %d,%d" % (ul + c + ur),
        "-draw", "stroke-linecap round line %d,%d %d,%d" % (c + bot),
    ]
    code = [
        "(", "-size", "%dx%d" % (cw, ch), "xc:none", "-stroke", "none", "-strokewidth", 0, "-font", font, "-pointsize", 104, "-fill", "white",
        "-gravity", "center", "-annotate", "+0+4", "CE-01", ")", "-gravity", "northwest",
        "-geometry", "+%d+%d" % (cx, cy), "-composite",
    ]
    stripes = ["(", "-size", "%dx%d" % (tw, th), "xc:none", "-stroke", "none", "-strokewidth", 0, "-fill", "white"]
    for i in range(-2, 12):
        x0 = i * 56
        stripes += ["-draw", "polygon %d,0 %d,0 %d,%d %d,%d" % (x0, x0 + 28, x0 + 28 - th, th, x0 - th, th)]
    stripes += [")", "-geometry", "+%d+%d" % (tx, ty), "-composite"]
    run(magick, ["-size", "%dx%d" % (SIZE, SIZE), "xc:none"] + screen + emblem + code + stripes +
        ["-define", "png:color-type=6", path])
    return path


STAMP = ".make_textures.stamp"


def makers():
    return {
        "grunge": (make_grunge, "T_Src_Grunge.png"),
        "brushed": (make_brushed, "T_Src_Brushed.png"),
        "scratches": (make_scratches, "T_Src_Scratches.png"),
        "decals": (make_decals, "T_Src_Decals.png"),
        "eyes": (make_eye_atlas, "T_Robot_EyeAtlas.png"),
        "displays": (make_display_atlas, "T_Robot_DisplayAtlas.png"),
    }


def source_stamp(magick):
    """Everything the textures depend on: this script and the ImageMagick version."""
    with open(os.path.abspath(__file__), "rb") as fh:
        h = hashlib.sha256(fh.read())
    h.update(subprocess.run([magick, "-version"], capture_output=True, text=True).stdout.encode())
    return h.hexdigest()


def make_all(out_dir, magick=None, log=print, cache=True):
    """Builds all source textures in parallel (independent ImageMagick processes, distinct temp files).
    With cache, unchanged textures (same script, same ImageMagick) are reused."""
    os.makedirs(out_dir, exist_ok=True)
    mg = find_magick(magick)
    jobs = makers()
    paths = {k: os.path.join(out_dir, fn) for k, (_, fn) in jobs.items()}
    stamp_path = os.path.join(out_dir, STAMP)
    stamp = source_stamp(mg)
    if cache and os.path.exists(stamp_path) and all(os.path.exists(p) for p in paths.values()):
        with open(stamp_path, encoding="utf-8") as fh:
            if fh.read().strip() == stamp:
                log("  ImageMagick textures unchanged - reused from %s" % out_dir)
                return paths
    if os.path.exists(stamp_path):
        os.remove(stamp_path)
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = {k: pool.submit(fn, mg, out_dir) for k, (fn, _) in jobs.items()}
        paths = {k: f.result() for k, f in futures.items()}
    with open(stamp_path, "w", encoding="utf-8") as fh:
        fh.write(stamp)
    for k, p in paths.items():
        log("  ImageMagick %-9s -> %s" % (k, p))
    return paths


# ------------------------------------------------------------------------------------------------
# Eye expressions and chest display motifs (runtime-switchable atlases, white artwork = tint mask)
# ------------------------------------------------------------------------------------------------

EYE_CELL = (512, 256)          # one expression = both eyes; plane 0.33 m x 0.165 m
EYE_GRID = (4, 8)
EYE_CENTERS = ((119, 128), (393, 128))   # robot's right eye (image left), robot's left eye
EXPRESSIONS = ["Normal", "Happy", "Excited", "Love", "Angry", "Sad", "Sleepy", "Surprised",
               "Skeptical", "Confused", "Wink", "Focused", "Glitch", "Pixel", "Off", "Cute",
               "Laugh", "Crying", "Shy", "Dollar", "Question", "Exclaim", "Loading", "LookLeft",
               "LookRight", "Determined", "Sleeping", "Target"]
DISPLAY_CELL = (512, 410)      # chest screen 0.082 m x 0.066 m
DISPLAY_GRID = (4, 4)
DISPLAY_ATLAS_H = 2048
DISPLAYS = ["Status", "Heart", "Battery", "Smiley", "Wave", "Cube", "Hello", "Music",
            "Lightning", "Warning", "Thermo", "Clock", "Signal", "Star", "Planet", "Loading"]


def _star(cx, cy, r_out, r_in, n=5, rot=-90):
    pts = []
    for i in range(2 * n):
        r = r_out if i % 2 == 0 else r_in
        a = math.radians(rot + i * 180.0 / n)
        pts.append("%d,%d" % (cx + r * math.cos(a), cy + r * math.sin(a)))
    return "polygon " + " ".join(pts)


def _heart(cx, cy, s):
    pts = []
    for i in range(48):
        t = 2 * math.pi * i / 48
        x = 16 * math.sin(t) ** 3
        y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
        pts.append("%d,%d" % (cx + x * s, cy - y * s))
    return "polygon " + " ".join(pts)


def _spiral(cx, cy, r, turns=2.3):
    pts = []
    n = 60
    for i in range(n + 1):
        t = 0.1 + 0.9 * i / n
        a = 2 * math.pi * turns * t
        p = "%d,%d" % (cx + r * t * math.cos(a), cy + r * t * math.sin(a))
        if not pts or pts[-1] != p:
            pts.append(p)
    return "polyline " + " ".join(pts)


def _eye_draws(name):
    """MVG draw commands (white on black) for one expression cell."""
    d = []
    ell = lambda cx, cy, rx=66, ry=62: "ellipse %d,%d %d,%d 0,360" % (cx, cy, rx, ry)
    for idx, (cx, cy) in enumerate(EYE_CENTERS):
        inner = 1 if idx == 0 else -1          # +1: inner side is to the right in the image
        if name == "Normal":
            d += ["fill white", ell(cx, cy)]
        elif name == "Happy" or (name == "Wink" and idx == 1):
            d += ["fill none", "stroke white", "stroke-width 26", "stroke-linecap round",
                  "path 'M %d,%d Q %d,%d %d,%d'" % (cx - 52, cy + 22, cx, cy - 62, cx + 52, cy + 22), "stroke none"]
        elif name == "Wink":
            d += ["fill white", ell(cx, cy)]
        elif name == "Excited":
            d += ["fill white", _star(cx, cy + 4, 74, 32)]
        elif name == "Love":
            d += ["fill white", _heart(cx, cy + 4, 4.3)]
        elif name in ("Angry", "Sad"):
            d += ["fill white", ell(cx, cy)]
            hi, lo = cy - 75, cy - 12
            outer_y, inner_y = (hi, lo) if name == "Angry" else (lo, hi)
            xo, xi = cx - 80 * inner, cx + 80 * inner
            d += ["fill black", "polygon %d,%d %d,%d %d,%d %d,%d" % (xo, outer_y, xi, inner_y, xi, cy - 140, xo, cy - 140)]
        elif name == "Sleepy":
            d += ["fill white", ell(cx, cy + 6), "fill black", "rectangle %d,%d %d,%d" % (cx - 80, cy - 80, cx + 80, cy + 2)]
        elif name == "Surprised":
            d += ["fill white", "circle %d,%d %d,%d" % (cx, cy, cx + 76, cy), "fill black",
                  "circle %d,%d %d,%d" % (cx, cy, cx + 30, cy)]
        elif name == "Skeptical":
            if idx == 0:
                d += ["fill white", ell(cx, cy)]
            else:
                d += ["fill white", "roundrectangle %d,%d %d,%d 16,16" % (cx - 66, cy - 30, cx + 66, cy - 2)]
        elif name == "Confused":
            d += ["fill none", "stroke white", "stroke-width 15", "stroke-linecap round", _spiral(cx, cy, 64), "stroke none"]
        elif name == "Focused":
            d += ["fill white", "roundrectangle %d,%d %d,%d 14,14" % (cx - 70, cy - 14, cx + 70, cy + 14)]
        elif name == "Glitch":
            for k, (dy, off, w) in enumerate(((-58, 6, 40), (-38, -14, 58), (-14, 18, 66), (10, -6, 66),
                                             (34, 22, 56), (54, -10, 36))):
                d += ["fill white", "rectangle %d,%d %d,%d" % (cx - w + off, cy + dy - 9, cx + w + off, cy + dy + 9)]
            d += ["rectangle %d,%d %d,%d" % (cx + 70, cy - 70, cx + 84, cy - 56)]
        elif name == "Pixel":
            g = 18
            for gx in range(-4, 4):
                for gy in range(-4, 4):
                    px, py = (gx + 0.5) * g, (gy + 0.5) * g
                    if (px / 68) ** 2 + (py / 64) ** 2 <= 1.0:
                        d += ["fill white", "rectangle %d,%d %d,%d" % (cx + gx * g + 1, cy + gy * g + 1,
                                                                      cx + (gx + 1) * g - 1, cy + (gy + 1) * g - 1)]
        elif name == "Off":
            d += ["fill none", "stroke white", "stroke-width 22", "stroke-linecap round",
                  "line %d,%d %d,%d" % (cx - 44, cy - 44, cx + 44, cy + 44),
                  "line %d,%d %d,%d" % (cx - 44, cy + 44, cx + 44, cy - 44), "stroke none"]
        elif name == "Laugh":
            d += ["fill none", "stroke white", "stroke-width 24", "polyline %d,%d %d,%d %d,%d" % (
                cx - 40 * inner, cy - 42, cx + 36 * inner, cy, cx - 40 * inner, cy + 42), "stroke none"]
        elif name == "Crying":
            d += ["fill white", ell(cx, cy - 6, 58, 50),
                  "circle %d,%d %d,%d" % (cx - 46 * inner, cy + 76, cx - 46 * inner + 15, cy + 76),
                  "polygon %d,%d %d,%d %d,%d" % (cx - 46 * inner - 14, cy + 72, cx - 46 * inner + 14, cy + 72,
                                                  cx - 46 * inner, cy + 44)]
        elif name == "Shy":
            d += ["fill white", ell(cx, cy + 22, 54, 44), "fill none", "stroke white", "stroke-width 9"]
            for k in range(3):
                bx = cx - (70 - 22 * k) * inner
                d += ["line %d,%d %d,%d" % (bx, cy + 96, bx + 14 * inner, cy + 80)]
            d += ["stroke none"]
        elif name in ("Dollar", "Question", "Exclaim"):
            ch = {"Dollar": "$", "Question": "?", "Exclaim": "!"}[name]
            d += ["fill white", "font Arial-Black", "pointsize 168", "text %d,%d '%s'" % (
                cx - (48 if ch != "!" else 26), cy + 60, ch)]
        elif name == "Loading":
            for k in range(8):
                a = math.radians(k * 45 - 90)
                g = 70 + 23 * k
                d += ["fill rgb(%d,%d,%d)" % (g, g, g),
                      "circle %d,%d %d,%d" % (cx + 50 * math.cos(a), cy + 50 * math.sin(a),
                                              cx + 50 * math.cos(a) + 13, cy + 50 * math.sin(a))]
        elif name in ("LookLeft", "LookRight"):
            dx = -40 if name == "LookLeft" else 40
            d += ["fill white", ell(cx + dx, cy, 56, 58)]
        elif name == "Determined":
            d += ["fill white", ell(cx, cy + 4, 66, 54)]
            xo, xi = cx - 80 * inner, cx + 80 * inner
            d += ["fill black", "polygon %d,%d %d,%d %d,%d %d,%d" % (xo, cy - 30, xi, cy - 6, xi, cy - 140, xo, cy - 140),
                  "rectangle %d,%d %d,%d" % (cx - 80, cy + 34, cx + 80, cy + 80)]
        elif name == "Sleeping":
            d += ["fill none", "stroke white", "stroke-width 20",
                  "path 'M %d,%d Q %d,%d %d,%d'" % (cx - 50, cy - 4, cx, cy + 40, cx + 50, cy - 4), "stroke none"]
            if idx == 1:
                d += ["fill white", "font Arial-Black", "pointsize 64", "text %d,%d 'z'" % (cx + 50, cy - 40)]
        elif name == "Target":
            d += ["fill none", "stroke white", "stroke-width 11", "circle %d,%d %d,%d" % (cx, cy, cx + 50, cy),
                  "line %d,%d %d,%d" % (cx - 76, cy, cx - 30, cy), "line %d,%d %d,%d" % (cx + 30, cy, cx + 76, cy),
                  "line %d,%d %d,%d" % (cx, cy - 76, cx, cy - 30), "line %d,%d %d,%d" % (cx, cy + 30, cx, cy + 76),
                  "stroke none", "fill white", "circle %d,%d %d,%d" % (cx, cy, cx + 10, cy)]
        elif name == "Cute":
            d += ["fill white", ell(cx, cy + 4, 72, 72), "fill black",
                  "circle %d,%d %d,%d" % (cx - 26 * inner, cy - 24, cx - 26 * inner + 15, cy - 24),
                  "circle %d,%d %d,%d" % (cx - 4 * inner, cy - 4, cx - 4 * inner + 7, cy - 4)]
    return d


def make_eye_atlas(magick, out):
    path = os.path.join(out, "T_Robot_EyeAtlas.png")
    cw, ch = EYE_CELL
    cells = []
    for i, name in enumerate(EXPRESSIONS):
        cell = os.path.join(out, ".eye_%02d.png" % i)
        args = ["-size", "%dx%d" % (cw, ch), "xc:black"]
        for cmd in _eye_draws(name):
            if cmd.startswith(("fill ", "stroke ", "stroke-width", "stroke-linecap", "font ", "pointsize ")):
                key, val = cmd.split(" ", 1)
                args += {"fill": ["-fill", val], "stroke": ["-stroke", val], "stroke-width": ["-strokewidth", val],
                         "stroke-linecap": [], "font": ["-font", val], "pointsize": ["-pointsize", val]}[key]
            else:
                args += ["-draw", ("stroke-linecap round " if cmd.startswith(("line ", "path ")) else "") + cmd]
        # soft glow under the crisp shape (the shader tints it; bright core stays near white)
        args += ["(", "+clone", "-blur", "0x14", "-evaluate", "multiply", "0.9", ")", "-compose", "screen", "-composite",
                 "-colorspace", "Gray", cell]
        run(magick, args)
        cells.append(cell)
    gx, gy = EYE_GRID
    tmp = path + ".grid.png"
    run(magick, ["montage"] + cells + ["-tile", "%dx%d" % (gx, gy), "-geometry", "+0+0", "-background", "black", tmp])
    run(magick, [tmp, "-background", "black", "-gravity", "north", "-extent", "%dx%d" % (cw * gx, ch * gy),
                 "-colorspace", "Gray", "-depth", "8", path])
    os.remove(tmp)
    for c in cells:
        os.remove(c)
    return path


def make_display_atlas(magick, out):
    path = os.path.join(out, "T_Robot_DisplayAtlas.png")
    cw, ch = DISPLAY_CELL
    frame = ["-fill", "none", "-stroke", "rgb(110,110,110)", "-strokewidth", 10,
             "-draw", "roundrectangle 14,14 %d,%d 30,30" % (cw - 14, ch - 14), "-stroke", "none"]
    motifs = {
        "Status": ["-fill", "white", "-draw", "roundrectangle 52,62 330,104 12,12",
                   "-draw", "roundrectangle 52,134 270,176 12,12", "-draw", "roundrectangle 52,206 380,248 12,12",
                   "-fill", "rgb(120,120,120)", "-draw", "roundrectangle 340,62 460,104 12,12",
                   "-draw", "roundrectangle 280,134 460,176 12,12", "-fill", "none", "-stroke", "white",
                   "-strokewidth", 12, "-draw", "polyline 40,330 150,330 180,280 215,370 250,300 280,330 470,330"],
        "Heart": ["-fill", "white", "-draw", _heart(256, 190, 8.5), "-fill", "none", "-stroke", "rgb(150,150,150)",
                  "-strokewidth", 8, "-draw", "polyline 40,350 190,350 215,320 240,375 262,340 470,340"],
        "Battery": ["-fill", "none", "-stroke", "white", "-strokewidth", 16,
                    "-draw", "roundrectangle 90,120 400,290 18,18", "-stroke", "none", "-fill", "white",
                    "-draw", "rectangle 400,170 428,240", "-draw", "rectangle 116,146 186,264",
                    "-draw", "rectangle 206,146 276,264", "-draw", "rectangle 296,146 366,264"],
        "Smiley": ["-fill", "none", "-stroke", "white", "-strokewidth", 16, "-draw", "circle 256,205 256,70",
                   "-strokewidth", 18,
                   "-draw", "path 'M 180,240 Q 256,320 332,240'", "-stroke", "none", "-fill", "white",
                   "-draw", "ellipse 210,170 18,26 0,360", "-draw", "ellipse 302,170 18,26 0,360"],
        "Wave": ["-fill", "none", "-stroke", "rgb(90,90,90)", "-strokewidth", 4]
                + sum((["-draw", "line %d,40 %d,370" % (x, x)] for x in range(80, 480, 80)), [])
                + ["-draw", "line 40,205 470,205", "-stroke", "white", "-strokewidth", 12,
                   "-draw", "polyline " + " ".join("%d,%d" % (40 + i * 4.3, 205 - 110 * math.sin(i * 0.12) *
                                                              math.exp(-((i - 50) / 70.0) ** 2)) for i in range(101))],
        "Cube": ["-fill", "none", "-stroke", "white", "-strokewidth", 20,
                 "-draw", "stroke-linejoin round polygon 256,70 370,136 370,268 256,334 142,268 142,136",
                 "-draw", "stroke-linejoin round polyline 142,136 256,202 370,136", "-draw", "line 256,202 256,334"],
        "Hello": ["-fill", "white", "-font", "Arial-Black", "-pointsize", 190, "-gravity", "center",
                  "-annotate", "+0+0", "HI!", "-gravity", "northwest"],
        "Music": ["-fill", "white", "-draw", "ellipse 170,300 46,34 -20,340", "-draw", "ellipse 350,270 46,34 -20,340",
                  "-draw", "rectangle 200,110 222,300", "-draw", "rectangle 380,80 402,270",
                  "-draw", "polygon 200,110 402,80 402,130 200,160"],
        "Lightning": ["-fill", "white", "-draw", "polygon 290,50 150,225 245,225 205,360 360,170 262,170 320,50"],
        "Warning": ["-fill", "none", "-stroke", "white", "-strokewidth", 18,
                    "-draw", "stroke-linejoin round polygon 256,62 430,340 82,340", "-stroke", "none", "-fill", "white",
                    "-draw", "roundrectangle 243,150 269,262 8,8", "-draw", "circle 256,298 256,314"],
        "Thermo": ["-fill", "none", "-stroke", "white", "-strokewidth", 14,
                   "-draw", "roundrectangle 226,50 286,290 30,30", "-draw", "circle 256,318 256,268",
                   "-stroke", "none", "-fill", "white", "-draw", "circle 256,318 256,286",
                   "-draw", "rectangle 244,150 268,300", "-fill", "rgb(150,150,150)",
                   "-draw", "rectangle 310,90 360,100", "-draw", "rectangle 310,150 350,160",
                   "-draw", "rectangle 310,210 360,220"],
        "Clock": ["-fill", "none", "-stroke", "white", "-strokewidth", 16, "-draw", "circle 256,205 256,65",
                  "-strokewidth", 18, "-draw", "line 256,205 256,110", "-draw", "line 256,205 325,240",
                  "-stroke", "none", "-fill", "white", "-draw", "circle 256,205 256,219"]
                 + sum((["-draw", "circle %d,%d %d,%d" % (256 + 118 * math.cos(math.radians(a)),
                                                           205 + 118 * math.sin(math.radians(a)),
                                                           256 + 118 * math.cos(math.radians(a)) + 7,
                                                           205 + 118 * math.sin(math.radians(a)))]
                        for a in range(0, 360, 30)), []),
        "Signal": ["-fill", "white", "-draw", "roundrectangle 110,280 160,330 8,8",
                   "-draw", "roundrectangle 190,220 240,330 8,8", "-draw", "roundrectangle 270,150 320,330 8,8",
                   "-fill", "rgb(110,110,110)", "-draw", "roundrectangle 350,80 400,330 8,8"],
        "Star": ["-fill", "white", "-draw", _star(256, 215, 150, 62)],
        "Planet": ["-fill", "white", "-draw", "circle 256,205 256,120", "-fill", "none", "-stroke", "white",
                   "-strokewidth", 14, "-draw", "ellipse 256,215 170,46 -10,190",
                   "-stroke", "black", "-strokewidth", 30, "-draw", "ellipse 256,215 170,46 195,345",
                   "-stroke", "white", "-strokewidth", 14, "-draw", "ellipse 256,215 170,46 205,335"],
        "Loading": ["-fill", "none", "-stroke", "white", "-strokewidth", 12,
                    "-draw", "roundrectangle 70,170 442,240 20,20", "-stroke", "none", "-fill", "white",
                    "-draw", "roundrectangle 88,188 300,222 10,10", "-font", "Arial-Black", "-pointsize", 64,
                    "-draw", "text 120,330 'LOAD'"],
    }
    cells = []
    for i, name in enumerate(DISPLAYS):
        cell = os.path.join(out, ".disp_%02d.png" % i)
        run(magick, ["-size", "%dx%d" % (cw, ch), "xc:black"] + frame + motifs[name] + ["-colorspace", "Gray", cell])
        cells.append(cell)
    gx, gy = DISPLAY_GRID
    tmp = path + ".grid.png"
    run(magick, ["montage"] + cells + ["-tile", "%dx%d" % (gx, gy), "-geometry", "+0+0", "-background", "black", tmp])
    run(magick, [tmp, "-background", "black", "-gravity", "north", "-extent", "%dx%d" % (cw * gx, DISPLAY_ATLAS_H),
                 "-colorspace", "Gray", "-depth", "8", path])
    os.remove(tmp)
    for c in cells:
        os.remove(c)
    return path


if __name__ == "__main__":
    args = sys.argv[1:]
    mg = None
    if "--magick" in args:
        mg = args[args.index("--magick") + 1]
    make_all(args[0] if args else os.path.join(os.path.dirname(__file__), "..", "output", "SourceTextures"), mg)
