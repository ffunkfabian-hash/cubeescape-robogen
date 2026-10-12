#!/bin/bash
# Headless viewer tests: look migration (v1 and v2 -> v3), unknown IDs, rejected future versions, round trip of every
# type look, GIF and video export.
#   viewer/test_viewer.sh [output dir]      default: ../output (needs RobotViewer.app)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="${1:-$HERE/../output}"
BIN="$OUT/RobotViewer.app/Contents/MacOS/RobotViewer"
TMP="$(mktemp -d)"
fail=0
ok() { echo "PASS $1"; }
bad() { echo "FAIL $1"; fail=1; }

"$BIN" --snapshot "$TMP/v1.png" --look "$HERE/tests/look_v1.json" --save-look "$TMP/v1_migrated.json" --size 300x300 >/dev/null
# 1.10: the current look version is 3 (skin, armor2, zones2, badges, lines), so v1 files now migrate to 3
python3 - "$TMP/v1_migrated.json" <<'PY' && ok "v1 look migrated to v3" || bad "v1 look migrated to v3"
import json, sys
l = json.load(open(sys.argv[1]))
assert l["format"] == "CubeEscapeRobotLook" and l["version"] == 3
assert l["skin"] == "Custom" and l["badges"] == [] and l["zones2"] == [] and l["lines"]["pattern"] == "None"
assert l["variants"]["Head"] == "TV" and l["variants"]["Hands"] == "Claw" and l["variants"]["Feet"] == "Boots"
assert l["finish"] == "Glanz" and l["autoExpression"] is False and l["autoDisplay"] is True
assert abs(l["visor"]["r"] - 0.006) < 1e-6
PY

"$BIN" --snapshot "$TMP/v2.png" --look "$HERE/tests/look_v2.json" --save-look "$TMP/v2_migrated.json" --size 300x300 >/dev/null
python3 - "$HERE/tests/look_v2.json" "$TMP/v2_migrated.json" <<'PY' && ok "v2 look migrated to v3" || bad "v2 look migrated to v3"
import json, sys
old, l = json.load(open(sys.argv[1])), json.load(open(sys.argv[2]))
assert l["version"] == 3 and l["skin"] == "Custom" and l["badges"] == [] and l["lines"]["pattern"] == "None"
assert l["armor2"] == old["armor"] and l["zones2"] == []          # a v2 look keeps exactly its colours
for k in ("armor", "mech", "metal", "lights", "screen", "eyes", "visor", "fabric", "variants", "finish", "expression"):
    assert l[k] == old[k], k
PY

msg3="$("$BIN" --snapshot "$TMP/u3.png" --look "$HERE/tests/look_v3_unknown.json" --save-look "$TMP/u3.json" --size 300x300)"
python3 - "$TMP/u3.json" <<'PY' && ok "unknown 1.10 IDs replaced" || bad "unknown 1.10 IDs replaced"
import json, sys
l = json.load(open(sys.argv[1]))
assert l["skin"] == "Custom" and l["zones2"] == ["Pads"] and [b["id"] for b in l["badges"]] == ["Cross", "Cloud"]
assert l["lines"]["pattern"] == "None" and l["lines"]["strength"] == 2
assert l["expression"] == "Damage" and l["display"] == "Heartbeat"
PY
[ "$(grep -c 'look warning' <<<"$msg3")" -ge 4 ] && ok "1.10 warnings reported" || bad "1.10 warnings reported"

msg="$("$BIN" --snapshot "$TMP/u.png" --look "$HERE/tests/look_unknown_ids.json" --save-look "$TMP/u.json" --size 300x300)"
python3 - "$TMP/u.json" <<'PY' && ok "unknown IDs replaced by defaults" || bad "unknown IDs replaced by defaults"
import json, sys
l = json.load(open(sys.argv[1]))
assert l["variants"]["Head"] == "Classic" and l["variants"]["AccBack"] == "None" and "Tail" not in l["variants"]
assert l["expression"] == "Normal" and l["display"] == "Status" and l["glow"] == 2.5
PY
[ "$(grep -c 'look warning' <<<"$msg")" -ge 5 ] && ok "warnings reported" || bad "warnings reported"

if "$BIN" --snapshot "$TMP/f.png" --look "$HERE/tests/look_future_version.json" --size 300x300 >/dev/null 2>&1; then
  bad "future look version rejected"
else
  ok "future look version rejected"
fi

for look in "$OUT"/Looks/*.robotlook.json; do
  name="$(basename "$look" .robotlook.json)"
  if "$BIN" --snapshot "$TMP/$name.png" --look "$look" --save-look "$TMP/$name.json" --size 300x300 >/dev/null &&
     python3 -c "import json,sys; a=json.load(open(sys.argv[1])); b=json.load(open(sys.argv[2])); assert a==b, set(k for k in a if a[k]!=b.get(k))" "$look" "$TMP/$name.json"; then
    ok "round trip $name"
  else
    bad "round trip $name"
  fi
done
if "$BIN" --snapshot "$TMP/x.png" --anim Wave --gif "$TMP/wave.gif" --size 200x200 >/dev/null &&
   "$BIN" --snapshot "$TMP/x.png" --anim Wave --video "$TMP/wave.mp4" --size 200x200 >/dev/null &&
   [ "$(magick identify "$TMP/wave.gif" | wc -l)" -ge 10 ] && [ -s "$TMP/wave.mp4" ]; then
  ok "clip export (gif, mp4)"
else
  bad "clip export (gif, mp4)"
fi
rm -rf "$TMP"
exit $fail
