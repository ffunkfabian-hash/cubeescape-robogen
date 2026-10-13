#!/bin/bash
# Headless viewer tests: look migration (v1 ... v5 -> v6), unknown IDs, rejected future versions, visible body forms,
# round trip of every type look, GIF and video export.
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
# the current look version is 6 (1.10: skin, armor2, zones2, badges, lines; 1.11: material, layers, wear;
# 1.13: special; 1.14: form)
python3 - "$TMP/v1_migrated.json" <<'PY' && ok "v1 look migrated to v6" || bad "v1 look migrated to v6"
import json, sys
l = json.load(open(sys.argv[1]))
assert l["format"] == "CubeEscapeRobotLook" and l["version"] == 6 and l["special"]["id"] == "None"
assert l["material"]["id"] == "None" and l["layers"] == [] and l["wear"] == 0.5
assert l["skin"] == "Custom" and l["badges"] == [] and l["zones2"] == [] and l["lines"]["pattern"] == "None"
assert l["variants"]["Head"] == "TV" and l["variants"]["Hands"] == "Claw" and l["variants"]["Feet"] == "Boots"
assert l["finish"] == "Glanz" and l["autoExpression"] is False and l["autoDisplay"] is True
assert abs(l["visor"]["r"] - 0.006) < 1e-6
PY

"$BIN" --snapshot "$TMP/v2.png" --look "$HERE/tests/look_v2.json" --save-look "$TMP/v2_migrated.json" --size 300x300 >/dev/null
python3 - "$HERE/tests/look_v2.json" "$TMP/v2_migrated.json" <<'PY' && ok "v2 look migrated to v6" || bad "v2 look migrated to v6"
import json, sys
old, l = json.load(open(sys.argv[1])), json.load(open(sys.argv[2]))
assert l["version"] == 6 and l["skin"] == "Custom" and l["badges"] == [] and l["lines"]["pattern"] == "None"
assert l["armor2"] == old["armor"] and l["zones2"] == []          # a v2 look keeps exactly its colours
for k in ("armor", "mech", "metal", "lights", "screen", "eyes", "visor", "fabric", "finish", "expression"):
    assert l[k] == old[k], k
# slots added later (1.12: AccEar) arrive with their default; every saved choice stays
assert all(l["variants"][k] == v for k, v in old["variants"].items())
assert all(v == "None" for k, v in l["variants"].items() if k not in old["variants"]), l["variants"]
PY

"$BIN" --snapshot "$TMP/v3.png" --look "$HERE/tests/look_v3.json" --save-look "$TMP/v3_migrated.json" --size 300x300 >/dev/null
python3 - "$HERE/tests/look_v3.json" "$TMP/v3_migrated.json" <<'PY' && ok "v3 look migrated to v6" || bad "v3 look migrated to v6"
import json, sys
old, l = json.load(open(sys.argv[1])), json.load(open(sys.argv[2]))
assert l["version"] == 6 and l["skin"] == old["skin"] and l["badges"] == old["badges"] and l["lines"] == old["lines"]
assert l["material"]["id"] == "None" and l["layers"] == [] and l["wear"] == 0.5
PY

"$BIN" --snapshot "$TMP/v4.png" --look "$HERE/tests/look_v4.json" --save-look "$TMP/v4_migrated.json" --size 300x300 >/dev/null
python3 - "$HERE/tests/look_v4.json" "$TMP/v4_migrated.json" <<'PY' && ok "v4 look migrated to v6" || bad "v4 look migrated to v6"
import json, sys
old, l = json.load(open(sys.argv[1])), json.load(open(sys.argv[2]))
assert l["version"] == 6 and l["special"]["id"] == "None"
for k in ("skin", "badges", "lines", "material", "layers", "wear", "armor", "finish", "expression"):
    assert l[k] == old[k], k
PY

"$BIN" --snapshot "$TMP/v5.png" --look "$HERE/tests/look_v5.json" --save-look "$TMP/v5_migrated.json" --size 300x300 >/dev/null
python3 - "$HERE/tests/look_v5.json" "$TMP/v5_migrated.json" <<'PY' && ok "v5 look migrated to v6" || bad "v5 look migrated to v6"
import json, sys
old, l = json.load(open(sys.argv[1])), json.load(open(sys.argv[2]))
assert l["version"] == 6 and l["form"] == "None" and l["special"] == old["special"]
PY

msg6="$("$BIN" --snapshot "$TMP/u6.png" --look "$HERE/tests/look_v6_unknown_form.json" --save-look "$TMP/u6.json" --size 300x300)"
python3 - "$TMP/u6.json" <<'PY' && ok "unknown form replaced" || bad "unknown form replaced"
import json, sys
assert json.load(open(sys.argv[1]))["form"] == "None"
PY
case "$msg6" in *Form*) ok "form warning reported" ;; *) bad "form warning reported" ;; esac

# 1.14 body forms: each form skin renders differently from the same skin in the base form (geometry, holes, cables)
for skin in Pixel Origami Bone Wire; do
  "$BIN" --snapshot "$TMP/form_$skin.png" --skin "$skin" --size 300x300 >/dev/null
  "$BIN" --snapshot "$TMP/base_$skin.png" --skin "$skin" --form None --size 300x300 >/dev/null
  # snapshots are deterministic (the same look differs by 0 px); the thin cables of Wire still change ~55 px
  diff=$(magick compare -metric AE -fuzz 2% "$TMP/form_$skin.png" "$TMP/base_$skin.png" null: 2>&1 || true)
  if awk -v d="${diff%% *}" 'BEGIN { exit !(d > 20) }'; then ok "form of $skin is visible (${diff%% *} px)"
  else bad "form of $skin is visible (${diff%% *} px)"; fi
done

msg5="$("$BIN" --snapshot "$TMP/u5.png" --look "$HERE/tests/look_v5_unknown_special.json" --save-look "$TMP/u5.json" --size 300x300)"
python3 - "$TMP/u5.json" <<'PY' && ok "unknown special material replaced" || bad "unknown special material replaced"
import json, sys
assert json.load(open(sys.argv[1]))["special"]["id"] == "None"
PY
case "$msg5" in *Spezialmaterial*) ok "special material warning reported" ;; *) bad "special material warning reported" ;; esac

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

# round trips: six viewer processes at a time (each start costs about half a second), reported in name order
export BIN TMP
cat > "$TMP/roundtrip.sh" <<'SH'
look="$1"; name="$(basename "$look" .robotlook.json)"
"$BIN" --snapshot "$TMP/$name.png" --look "$look" --save-look "$TMP/$name.json" --size 300x300 >/dev/null 2>&1 &&
python3 -c "import json,sys; a=json.load(open(sys.argv[1])); b=json.load(open(sys.argv[2])); assert a==b, set(k for k in a if a[k]!=b.get(k))" "$look" "$TMP/$name.json" > "$TMP/$name.err" 2>&1
echo $? > "$TMP/$name.rt"
SH
printf '%s\0' "$OUT"/Looks/*.robotlook.json | xargs -0 -P 6 -n 1 bash "$TMP/roundtrip.sh"   # paths may hold spaces
for look in "$OUT"/Looks/*.robotlook.json; do
  name="$(basename "$look" .robotlook.json)"
  if [ "$(cat "$TMP/$name.rt" 2>/dev/null)" = 0 ]; then ok "round trip $name"; else cat "$TMP/$name.err" 2>/dev/null; bad "round trip $name"; fi
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
