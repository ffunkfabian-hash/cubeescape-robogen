"""One-off tool: reads the colour dots of the skin concept sheets in docs/concepts (never part of a generator run).

Every card on the sheets shows four colour dots next to the small renders. This script samples them at fixed card
positions (median of a 5 x 5 patch) and writes the result to data/concept_palettes.json, plus a check image with a
marker on every sampled spot. The names were typed from the cards (faster and more reliable than text recognition).
The skins in data/skins.json take these colours as a starting point; they are reviewed by eye, not copied blindly.

Usage:  python3 scripts/concept_palettes.py [--check /tmp/concept_check.png]
"""

import argparse
import json
import os
import shutil
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))

# Sheet layout (pixels of the 1536 x 1024 images): x of the first dot per card column, y of the dot row per card row
# and the dot offsets (measured on the check image, see --check).
SHEETS = {
    "C": {
        "file": "docs/concepts/skins_c_neue_konzepte.webp",
        "title": "Spieler Roboter - neue Skin Konzepte",
        "cols": [170, 415, 667, 920, 1174, 1432],
        "rows": [172, 410, 657, 890],
        "dots": [0, 17, 42, 60],
        "names": ["Chrono", "Pixel", "Steam", "Origami", "Geo", "Bamboo",
                  "Void", "Ice", "Toxic", "Candy", "Bone", "Holo",
                  "Wire", "Paper", "Concrete", "Golden", "Rainbow", "Scribble",
                  "Circuit", "Lumin", "Obsidian", "Celest", "Arbor", "Nebula"],
    },
    "A": {
        "file": "docs/concepts/skins_a.webp",
        "title": "Roboter - Skin Konzepte",
        "cols": [177, 428, 686, 940, 1190, 1443],
        "rows": [173, 428, 682],
        "dots": [0, 18, 41, 59],
        "names": ["Astronaut", "Scout", "Miner", "Samurai", "Moss", "Glass",
                  "Wood", "Arc", "Tank", "Medic", "Ocean", "Spike",
                  "Pixel", "Ghost", "Scrap", "Observer", "Clockwork", "Inferno"],
    },
}
PATCH = 5


def find_magick():
    for cand in (shutil.which("magick"), "/opt/homebrew/bin/magick", "/usr/local/bin/magick"):
        if cand and os.path.exists(cand):
            return cand
    raise RuntimeError("ImageMagick 'magick' not found")


def read_ppm(magick, path):
    """(width, height, bytes) of the image as 8-bit RGB."""
    data = subprocess.run([magick, path, "-depth", "8", "ppm:-"], capture_output=True, check=True).stdout
    parts, pos = [], 0
    while len(parts) < 4:                       # magic, width, height, maxval (no comments from ImageMagick)
        while data[pos:pos + 1].isspace():
            pos += 1
        end = pos
        while not data[end:end + 1].isspace():
            end += 1
        parts.append(data[pos:end])
        pos = end
    return int(parts[1]), int(parts[2]), data[pos + 1:]


def patch_stats(img, x, y, r=PATCH // 2):
    """(median colour, sum of channel ranges) of a (2r+1)^2 patch."""
    w, _, px = img
    rgb = [[], [], []]
    for yy in range(y - r, y + r + 1):
        for xx in range(x - r, x + r + 1):
            i = 3 * (yy * w + xx)
            for c in range(3):
                rgb[c].append(px[i + c])
    med = tuple(sorted(v)[len(v) // 2] for v in rgb)
    return med, sum(max(v) - min(v) for v in rgb)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=os.path.join(ROOT, "data", "concept_palettes.json"))
    parser.add_argument("--check", default="", help="write a check image with markers on the sampled spots")
    args = parser.parse_args()
    magick = find_magick()
    result = {"format": "CubeEscapeConceptPalettes", "version": 1,
              "note": "colour dots sampled from the concept sheets in docs/concepts by scripts/concept_palettes.py",
              "sheets": {}}
    checks = []
    for key, sheet in SHEETS.items():
        img = read_ppm(magick, os.path.join(ROOT, sheet["file"]))
        cards, draws, crops = [], [], []
        for i, name in enumerate(sheet["names"]):
            row, col = divmod(i, len(sheet["cols"]))
            y0, x0 = sheet["rows"][row], sheet["cols"][col]
            dots = []
            for dx in sheet["dots"]:
                x, y = x0 + dx, y0
                dots.append("#%02x%02x%02x" % patch_stats(img, x, y)[0])
                draws += ["-draw", "circle %d,%d %d,%d" % (x, y, x + 2, y)]
            cards.append({"number": i + 1, "name": name, "dots": dots})
            crops.append((name, x0 - 15, y0 - 12))
        result["sheets"][key] = {"file": sheet["file"], "title": sheet["title"], "cards": cards}
        if args.check:          # dot area of every card, 4x, with a marker on each sampled spot
            marked = args.check + ".%s.marked.png" % key
            subprocess.run([magick, os.path.join(ROOT, sheet["file"]), "-fill", "none", "-stroke", "#ff00ff",
                            "-strokewidth", "1"] + draws + [marked], check=True)
            for name, cx, cy in crops:
                tile = args.check + ".%s.%02d.png" % (key, len(checks))
                subprocess.run([magick, marked, "-crop", "95x24+%d+%d" % (cx, cy), "+repage", "-scale", "400%",
                                "-gravity", "south", "-background", "black", "-fill", "white", "-splice", "0x16",
                                "-annotate", "+0+0", "%s %s" % (key, name), tile], check=True)
                checks.append(tile)
            os.remove(marked)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)
        fh.write("\n")
    if checks:
        subprocess.run([magick, "montage"] + checks + ["-tile", "6x", "-geometry", "+2+2", "-background", "#333",
                                                       args.check], check=True)
        for c in checks:
            os.remove(c)
    print("wrote %s (%d cards)" % (args.out, sum(len(s["cards"]) for s in result["sheets"].values())))


if __name__ == "__main__":
    main()
