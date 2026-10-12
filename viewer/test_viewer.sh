#!/bin/bash
# Headless viewer tests: look migration (v1 -> v2), unknown IDs, rejected future versions, snapshots of every type.
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
python3 - "$TMP/v1_migrated.json" <<'PY' && ok "v1 look migrated to v2" || bad "v1 look migrated to v2"
import json, sys
l = json.load(open(sys.argv[1]))
assert l["format"] == "CubeEscapeRobotLook" and l["version"] == 2
assert l["variants"]["Head"] == "TV" and l["variants"]["Hands"] == "Claw" and l["variants"]["Feet"] == "Boots"
assert l["finish"] == "Glanz" and l["autoExpression"] is False and l["autoDisplay"] is True
assert abs(l["visor"]["r"] - 0.006) < 1e-6
PY

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
rm -rf "$TMP"
exit $fail
