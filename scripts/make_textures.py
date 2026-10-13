"""Cube Escape robot - source textures made with ImageMagick.

Creates deterministic detail textures that the Blender shaders project onto the robot before baking:

    T_Src_Grunge.png      tileable multi-octave grime (albedo variation, roughness, micro bump)
    T_Src_Brushed.png     tileable brushed-metal streaks
    T_Src_Scratches.png   tileable fine scratches (worn paint, bump)
    T_Src_Decals.png      RGBA decal atlas: status display UI, cube emblem, unit code, hazard stripes
    T_Src_Badges.png      RGBA badge atlas (BADGE_ART): cross, cloud, number, biohazard, glyph, crash-test target,
                          star, gear, leaf
    T_Src_Circuit.png     tileable circuit traces (glow line pattern "Circuit")
    T_Src_Bolt.png        tileable zigzag lightning lines (glow line pattern "Bolt")
    T_Src_Cracks.png      tileable crack field (layer "Embers": bright on the cracks, fading away from them)
    T_Src_Camo.png        tileable low-frequency blotch field (layer "Camo", thresholded at runtime)
    T_Robot_DisplayFlipbook.png  animated chest display motifs (DISPLAY_ANIMS), one row of frames per motif

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


# One thread per ImageMagick process: the calls already run in parallel, and OpenMP threads of parallel processes
# spin against each other (a 28-tile montage: 1.6 s single-threaded, 3.8 s with threads on a busy machine).
MAGICK_ENV = dict(os.environ, MAGICK_THREAD_LIMIT="1")


def run(magick, args, log=print):
    cmd = [magick] + [str(a) for a in args]
    res = subprocess.run(cmd, capture_output=True, text=True, env=MAGICK_ENV)
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
        "badges": (make_badges, "T_Src_Badges.png"),
        "circuit": (make_circuit, "T_Src_Circuit.png"),
        "bolt": (make_bolt, "T_Src_Bolt.png"),
        "flipbook": (make_display_flipbook, "T_Robot_DisplayFlipbook.png"),
        "cracks": (make_cracks, "T_Src_Cracks.png"),
        "camo": (make_camo, "T_Src_Camo.png"),
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
EYE_GRID = (4, 10)             # 40 cells; the atlas grew from 4 x 8 in generator 1.10 (cells come from robot_config)
EYE_CENTERS = ((119, 128), (393, 128))   # robot's right eye (image left), robot's left eye
EXPRESSIONS = ["Normal", "Happy", "Excited", "Love", "Angry", "Sad", "Sleepy", "Surprised",
               "Skeptical", "Confused", "Wink", "Focused", "Glitch", "Pixel", "Off", "Cute",
               "Laugh", "Crying", "Shy", "Dollar", "Question", "Exclaim", "Loading", "LookLeft",
               "LookRight", "Determined", "Sleeping", "Target",
               "Damage", "LowPower", "Check", "Warning", "Blink"]          # 1.10
# How the game shows some expressions besides the atlas cell: a fixed tint instead of the look's eye colour and/or a
# glow factor. Data for Unity and the viewer (robot_config.json > expressionStyles).
EXPRESSION_STYLES = {
    "Damage": {"tint": [1.0, 0.06, 0.04]},
    "Warning": {"tint": [1.0, 0.36, 0.02]},
    "LowPower": {"glow": 0.45},
}
# Idle blinking (game/viewer rule, not a clip key): every 2.5-6 s for 0.12 s, only while one of these is shown.
BLINK = {"expression": "Blink", "interval": [2.5, 6.0], "duration": 0.12,
         "during": ["Normal", "Happy", "Determined", "LookLeft", "LookRight", "Cute", "Focused"]}
DISPLAY_CELL = (512, 410)      # chest screen 0.082 m x 0.066 m
DISPLAY_GRID = (4, 4)
DISPLAY_ATLAS_H = 2048
DISPLAYS = ["Status", "Heart", "Battery", "Smiley", "Wave", "Cube", "Hello", "Music",
            "Lightning", "Warning", "Thermo", "Clock", "Signal", "Star", "Planet", "Loading"]
# Animated chest display motifs (1.10): a row of frames per motif in a flipbook atlas, played at fps in a loop.
FLIPBOOK_CELL = (256, 205)     # half the static cell, same aspect
FLIPBOOK_GRID = (8, 4)         # frames per row, rows (= motifs, at most 4)
FLIPBOOK_SIZE = (2048, 1024)
DISPLAY_ANIMS = [("Heartbeat", 8, 10), ("Spinner", 8, 12), ("Talk", 8, 12)]      # (name, frames, fps)

# Badge artwork (white on transparent, 4 x 4 cells of 256 px); the generator projects these as decals.
BADGE_CELL = 256
BADGE_ART = ["Cross", "Cloud", "Number", "Biohazard", "Glyph", "Target", "Star", "Gear", "Leaf"]


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
        elif name == "Damage":
            d += ["fill none", "stroke white", "stroke-width 34", "stroke-linecap round",
                  "line %d,%d %d,%d" % (cx - 54, cy - 54, cx + 54, cy + 54),
                  "line %d,%d %d,%d" % (cx - 54, cy + 54, cx + 54, cy - 54), "stroke none"]
        elif name == "LowPower":
            d += ["fill rgb(150,150,150)", "roundrectangle %d,%d %d,%d 12,12" % (cx - 66, cy - 13, cx + 66, cy + 13),
                  "fill black", "rectangle %d,%d %d,%d" % (cx - 6 - 40 * inner, cy - 20, cx + 6 - 40 * inner, cy + 20)]
        elif name == "Check":
            if idx == 0:                        # one tick across the visor
                d += ["fill none", "stroke white", "stroke-width 40", "stroke-linecap round", "stroke-linejoin round",
                      "polyline %d,%d %d,%d %d,%d" % (186, 132, 236, 184, 336, 66), "stroke none"]
        elif name == "Warning":
            d += ["fill white", ell(cx, cy, 60, 58)]
        elif name == "Blink":
            d += ["fill white", "roundrectangle %d,%d %d,%d 8,8" % (cx - 60, cy + 6, cx + 60, cy + 22)]
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
            if cmd.startswith(("fill ", "stroke ", "stroke-width", "stroke-linecap", "stroke-linejoin", "font ",
                               "pointsize ")):
                key, val = cmd.split(" ", 1)
                args += {"fill": ["-fill", val], "stroke": ["-stroke", val], "stroke-width": ["-strokewidth", val],
                         "stroke-linecap": [], "stroke-linejoin": [], "font": ["-font", val],
                         "pointsize": ["-pointsize", val]}[key]
            else:
                args += ["-draw", ("stroke-linecap round stroke-linejoin round "
                                   if cmd.startswith(("line ", "path ", "polyline ")) else "") + cmd]
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


def _grid_montage(magick, cells, grid, size, path, background="black"):
    gx, gy = grid
    tmp = path + ".grid.png"
    run(magick, ["montage"] + cells + ["-tile", "%dx%d" % (gx, gy), "-geometry", "+0+0", "-background", background, tmp])
    run(magick, [tmp, "-background", background, "-gravity", "northwest", "-extent", "%dx%d" % size,
                 "-colorspace", "Gray", "-depth", "8", path])
    os.remove(tmp)
    for c in cells:
        os.remove(c)


def _flipbook_frame(name, f, n):
    """Draw arguments (512 x 410 like a static cell) for frame f of n of an animated display motif."""
    if name == "Heartbeat":
        def ecg(u):                             # one beat every 256 px
            u %= 256
            for a, b, ya, yb in ((100, 116, 0, -40), (116, 132, -40, 120), (132, 148, 120, -150), (148, 164, -150, 0),
                                 (190, 214, 0, -30), (214, 238, -30, 0)):
                if a <= u < b:
                    return ya + (yb - ya) * (u - a) / float(b - a)
            return 0
        pts = " ".join("%d,%d" % (x, 270 - 0.55 * ecg(x + f * 512 // n)) for x in range(36, 478, 4))
        beat = (f * 512 // n) % 256 < 64
        return ["-fill", "white", "-draw", _heart(110, 110, 3.4 if beat else 2.8),
                "-fill", "none", "-stroke", "white", "-strokewidth", 12, "-draw", "polyline " + pts]
    if name == "Spinner":
        out = []
        for k in range(8):
            a = math.radians(k * 45 - 90)
            g = 60 + int(195 * ((k - f) % 8) / 7.0)
            x, y = 256 + 115 * math.cos(a), 205 + 115 * math.sin(a)
            out += ["-fill", "rgb(%d,%d,%d)" % (g, g, g), "-draw", "circle %d,%d %d,%d" % (x, y, x + 30, y)]
        return out
    if name == "Talk":
        out = ["-fill", "white"]
        for k in range(9):
            h = 22 + 120 * abs(math.sin(k * 1.7 + f * 2 * math.pi / n)) * (0.55 + 0.45 * abs(math.sin(f * 1.3 + k)))
            x = 96 + k * 40
            out += ["-draw", "roundrectangle %d,%d %d,%d 10,10" % (x - 13, 205 - h, x + 13, 205 + h)]
        return out
    raise ValueError(name)


def make_display_flipbook(magick, out):
    path = os.path.join(out, "T_Robot_DisplayFlipbook.png")
    cw, ch = DISPLAY_CELL
    frame = ["-fill", "none", "-stroke", "rgb(110,110,110)", "-strokewidth", 10,
             "-draw", "roundrectangle 14,14 %d,%d 30,30" % (cw - 14, ch - 14), "-stroke", "none"]
    gx, gy = FLIPBOOK_GRID
    cells = []
    for row in range(gy):
        for f in range(gx):
            cell = os.path.join(out, ".flip_%d_%d.png" % (row, f))
            if row < len(DISPLAY_ANIMS) and f < DISPLAY_ANIMS[row][1]:
                name, n, _ = DISPLAY_ANIMS[row]
                art = frame + _flipbook_frame(name, f, n)
            else:
                art = []
            run(magick, ["-size", "%dx%d" % (cw, ch), "xc:black"] + art +
                ["-resize", "%dx%d!" % FLIPBOOK_CELL, "-colorspace", "Gray", cell])
            cells.append(cell)
    _grid_montage(magick, cells, FLIPBOOK_GRID, FLIPBOOK_SIZE, path)
    return path


def _pie(cx, cy, r, a0, a1, steps=12):
    pts = ["%d,%d" % (cx, cy)]
    for i in range(steps + 1):
        a = math.radians(a0 + (a1 - a0) * i / steps)
        pts.append("%d,%d" % (cx + r * math.cos(a), cy + r * math.sin(a)))
    return "polygon " + " ".join(pts)


def _badge_draws(name):
    c = BADGE_CELL // 2
    if name == "Cross":
        return ["-fill", "white", "-draw", "roundrectangle 98,36 158,220 10,10",
                "-draw", "roundrectangle 36,98 220,158 10,10"]
    if name == "Cloud":
        return ["-fill", "white", "-draw", "circle 88,150 88,104", "-draw", "circle 136,118 136,58",
                "-draw", "circle 184,146 184,104", "-draw", "roundrectangle 46,140 226,192 26,26"]
    if name == "Number":
        return ["-fill", "white", "-font", "Arial-Black", "-pointsize", 150, "-gravity", "center",
                "-annotate", "+0+6", "07", "-gravity", "northwest"]
    if name == "Biohazard":
        out = ["-fill", "white"]
        for a in (-90, 30, 150):
            r = math.radians(a)
            out += ["-draw", "circle %d,%d %d,%d" % (c + 44 * math.cos(r), c + 44 * math.sin(r),
                                                       c + 44 * math.cos(r) + 58, c + 44 * math.sin(r))]
        out += ["-fill", "black"]
        for a in (-90, 30, 150):
            r = math.radians(a)
            out += ["-draw", "circle %d,%d %d,%d" % (c + 64 * math.cos(r), c + 64 * math.sin(r),
                                                       c + 64 * math.cos(r) + 38, c + 64 * math.sin(r))]
        return out + ["-fill", "none", "-stroke", "white", "-strokewidth", 12, "-draw", "circle %d,%d %d,%d" % (
            c, c, c + 40, c), "-stroke", "none", "-fill", "black", "-draw", "circle %d,%d %d,%d" % (c, c, c + 16, c)]
    if name == "Glyph":                         # 侍 (samurai) for the Samurai skin, bold Hiragino from macOS
        return ["-fill", "white", "-font", "Hiragino-Kaku-Gothic-StdN-W8", "-pointsize", 196, "-gravity", "center",
                "-annotate", "+0+0", "\u4f8d", "-gravity", "northwest"]
    if name == "Target":                        # crash-test marker
        return ["-fill", "white", "-draw", _pie(c, c, 96, 0, 90), "-draw", _pie(c, c, 96, 180, 270),
                "-fill", "none", "-stroke", "white", "-strokewidth", 14, "-draw", "circle %d,%d %d,%d" % (
                    c, c, c + 104, c), "-stroke", "none"]
    if name == "Star":                          # Celest: five-pointed star with a small sparkle
        return ["-fill", "white", "-draw", _star(c - 8, c + 10, 104, 44), "-draw", _star(c + 84, c - 82, 30, 9, n=4)]
    if name == "Gear":                          # Clockwork: gear wheel with a hole
        pts = []
        for i in range(48):
            a = 2 * math.pi * i / 48
            r = 104 if (i // 3) % 2 == 0 else 80
            pts.append("%d,%d" % (c + r * math.cos(a), c + r * math.sin(a)))
        return ["-fill", "white", "-draw", "polygon " + " ".join(pts), "-fill", "black",
                "-draw", "circle %d,%d %d,%d" % (c, c, c + 34, c)]
    if name == "Leaf":                          # Arbor: leaf with a stem and a midrib
        pts = []
        for i in range(41):
            t = i / 40.0
            w = 70 * math.sin(math.pi * t) ** 0.8
            pts.append((c - 100 + 200 * t, w))
        outline = [(x, c - w) for x, w in pts] + [(x, c + w) for x, w in reversed(pts)]
        rot = lambda x, y: (c + (x - c) * 0.766 + (y - c) * 0.643, c - (x - c) * 0.643 + (y - c) * 0.766)
        poly = " ".join("%d,%d" % rot(x, y) for x, y in outline)
        x0, y0 = rot(c - 124, c)
        x1, y1 = rot(c + 60, c)
        return ["-fill", "white", "-draw", "polygon " + poly, "-stroke", "black", "-strokewidth", 9,
                "-draw", "line %d,%d %d,%d" % (rot(c - 90, c) + (x1, y1)), "-stroke", "white", "-strokewidth", 14,
                "-draw", "line %d,%d %d,%d" % ((x0, y0) + rot(c - 98, c)), "-stroke", "none"]
    raise ValueError(name)


def make_badges(magick, out):
    """RGBA: white artwork, alpha = shape (like the decal atlas)."""
    path = os.path.join(out, "T_Src_Badges.png")
    cells = []
    for i in range(16):
        cell = os.path.join(out, ".badge_%02d.png" % i)
        art = _badge_draws(BADGE_ART[i]) if i < len(BADGE_ART) else []
        run(magick, ["-size", "%dx%d" % (BADGE_CELL, BADGE_CELL), "xc:black"] + art + ["-colorspace", "Gray", cell])
        cells.append(cell)
    gray = path + ".gray.png"
    _grid_montage(magick, cells, (4, 4), (4 * BADGE_CELL, 4 * BADGE_CELL), gray)
    run(magick, [gray, "-alpha", "copy", "-channel", "RGB", "-evaluate", "set", "100%", "+channel",
                 "-define", "png:color-type=6", path])
    os.remove(gray)
    return path


def _tiled_polylines(lines, width, extra=()):
    """Draw arguments for polylines repeated at +-SIZE so that the texture tiles."""
    out = ["-fill", "none", "-stroke", "white", "-strokewidth", width]
    for pts in lines:
        for dx in (-SIZE, 0, SIZE):
            for dy in (-SIZE, 0, SIZE):
                out += ["-draw", "stroke-linecap round stroke-linejoin round polyline " +
                        " ".join("%d,%d" % (x + dx, y + dy) for x, y in pts)]
    return out + list(extra)


def make_circuit(magick, out):
    """Tileable circuit traces: horizontal/vertical/45-degree runs on a 32 px grid with solder pads at the ends."""
    path = os.path.join(out, "T_Src_Circuit.png")
    rnd = random.Random(77)
    lines, pads = [], []
    dirs = [(1, 0), (0, 1), (1, 1), (1, -1), (-1, 0), (0, -1)]
    for _ in range(24):
        x, y = rnd.randrange(32) * 32, rnd.randrange(32) * 32
        d = rnd.choice(dirs)
        pts = [(x, y)]
        for _ in range(rnd.randint(2, 4)):
            n = rnd.randint(2, 6)
            x, y = x + d[0] * n * 32, y + d[1] * n * 32
            pts.append((x, y))
            i = dirs.index(d)
            d = dirs[(i + rnd.choice((1, -1))) % 4] if i < 4 else dirs[rnd.randrange(4)]
        lines.append(pts)
        pads += [pts[0], pts[-1]]
    pad_draws = ["-stroke", "none", "-fill", "white"]
    for x, y in pads:
        for dx in (-SIZE, 0, SIZE):
            for dy in (-SIZE, 0, SIZE):
                pad_draws += ["-draw", "circle %d,%d %d,%d" % (x + dx, y + dy, x + dx + 11, y + dy)]
    run(magick, ["-size", "%dx%d" % (SIZE, SIZE), "xc:black"] + _tiled_polylines(lines, 7, pad_draws) +
        ["-blur", "0x0.7", "-colorspace", "Gray", "-depth", "8", path])
    return path


def make_bolt(magick, out):
    """Tileable zigzag lightning lines, four per tile, with the same x at top and bottom."""
    path = os.path.join(out, "T_Src_Bolt.png")
    rnd = random.Random(5)
    lines = []
    for k in range(4):
        x0 = 128 + k * 256
        pts = [(x0, 0)]
        for y in range(64, SIZE, 64):
            pts.append((x0 + rnd.randint(-46, 46), y))
        pts.append((x0, SIZE))
        lines.append(pts)
    run(magick, ["-size", "%dx%d" % (SIZE, SIZE), "xc:black"] + _tiled_polylines(lines, 9) +
        ["-blur", "0x0.7", "-colorspace", "Gray", "-depth", "8", path])
    return path


def make_cracks(magick, out):
    """Crack network as a field: two coarse tileable noises, the level-0.5 crossings of each are the cracks
    (1 on the crack, falling off over about 12 px). Combined with max, auto-levelled."""
    path = os.path.join(out, "T_Src_Cracks.png")
    fields = []
    for k, (seed, blurs, weights) in enumerate(((61, [64, 22, 7], [0.72, 0.23, 0.05]), (67, [90, 30], [0.8, 0.2]))):
        n = path + ".n%d.png" % k
        tile_noise(magick, n, seed, blurs, weights)
        f = path + ".f%d.png" % k
        run(magick, [n, "-alpha", "off", "-colorspace", "Gray", "-fx", "max(0, 1 - abs(u - 0.5) * 14)",
                     "-virtual-pixel", "tile", "-blur", "0x1.2", f])
        fields.append(f)
        os.remove(n)
    run(magick, [fields[0], fields[1], "-compose", "lighten", "-composite", "-auto-level", "-alpha", "off",
                 "-colorspace", "Gray", "-depth", "8", path])
    for f in fields:
        os.remove(f)
    return path


def make_camo(magick, out):
    """Low-frequency blotches (tileable); the runtime cuts it at two thresholds into two camo colours."""
    path = os.path.join(out, "T_Src_Camo.png")
    tile_noise(magick, path, 83, [70, 30, 10], [0.55, 0.32, 0.13],
               extra=["-sigmoidal-contrast", "4,50%", "-alpha", "off", "-colorspace", "Gray", "-depth", "8"])
    return path


if __name__ == "__main__":
    args = sys.argv[1:]
    mg = None
    if "--magick" in args:
        mg = args[args.index("--magick") + 1]
    make_all(args[0] if args else os.path.join(os.path.dirname(__file__), "..", "output", "SourceTextures"), mg)
