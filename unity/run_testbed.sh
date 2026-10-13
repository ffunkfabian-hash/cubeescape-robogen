#!/bin/bash
# Unity test package: imports the generator output into the separate URP project unity/RobotTestbed (never the game),
# checks the import (bones, clips, loop/root motion, textures, pose on the floor, looks), renders every skin, type,
# expression and some animation frames with the URP shader, then runs the EditMode tests.
#   unity/run_testbed.sh [generator output]      default: ../output      report: <output>/UnityTestbed
# Needs Unity (version in RobotTestbed/ProjectSettings/ProjectVersion.txt, via Unity Hub) and ImageMagick.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
OUT="$(cd "${1:-$ROOT/output}" && pwd)"
PROJ="$HERE/RobotTestbed"
VERSION="$(sed -n 's/^m_EditorVersion: //p' "$PROJ/ProjectSettings/ProjectVersion.txt")"
UNITY="${UNITY_EDITOR:-/Applications/Unity/Hub/Editor/$VERSION/Unity.app/Contents/MacOS/Unity}"
REPORT="$OUT/UnityTestbed"
[ -x "$UNITY" ] || { echo "Unity $VERSION not found at $UNITY (set UNITY_EDITOR)" >&2; exit 2; }
[ -f "$OUT/robot_config.json" ] || { echo "no generator output in $OUT" >&2; exit 2; }
if pgrep -f "Unity.*-projectPath $PROJ" >/dev/null; then echo "Unity already runs on $PROJ" >&2; exit 2; fi
mkdir -p "$REPORT"
t0=$(date +%s)

# one Unity start: import, prefabs, checks, shots and the EditMode tests (TestbedTestRun, since 1.13; a second
# start only for the tests cost about 10 s)
echo "> pipeline: import, prefabs, checks, shots, EditMode tests (log: $REPORT/pipeline.log)"
rm -f "$REPORT/tests.xml" "$REPORT/report.json"
code=0
ROBOT_OUTPUT="$OUT" ROBOT_REPORT="$REPORT" "$UNITY" -batchmode -quit -projectPath "$PROJ" \
  -executeMethod RobotTestbed.Editor.TestbedPipeline.Run -logFile "$REPORT/pipeline.log" || code=$?
t1=$(date +%s)

if command -v magick >/dev/null && [ -d "$REPORT/shots" ]; then
  export MAGICK_THREAD_LIMIT=1
  for kind in skins types expressions anim; do
    files=("$REPORT/shots/$kind"/*.png)
    [ -e "${files[0]}" ] || continue
    args=()
    for f in "${files[@]}"; do args+=(-label "$(basename "$f" .png)" "$f"); done
    magick montage "${args[@]}" -tile 6x -geometry +3+3 -background "#0a0f15" -fill "#d8e6f0" -pointsize 14 \
      -depth 8 "$REPORT/unity_$kind.png"
  done
fi

# Unity keeps running the last good assemblies when scripts do not compile - that must fail the run
if grep -h "error CS" "$REPORT/pipeline.log" 2>/dev/null | sort -u | head -5 | grep .; then
  echo "FAIL scripts do not compile (Unity used the previous assemblies)"
  code=3
fi

python3 - "$REPORT" "$code" "$((t1 - t0))" <<'PY'
import json, os, sys, xml.etree.ElementTree as ET
report, code, tp = sys.argv[1], int(sys.argv[2]), sys.argv[3]
path = os.path.join(report, "report.json")
if os.path.exists(path):
    r = json.load(open(path))
    for c in r["checks"]:
        print("%s %s%s" % ("PASS" if c["ok"] else "FAIL", c["name"], (" - " + c["detail"]) if c["detail"] and not c["ok"] else ""))
    print("pipeline: %d/%d checks, %s s (Unity %s)" % (r["passed"], r["total"], tp, r["unity"]))
else:
    print("pipeline: no report (exit %d) - see pipeline.log" % code)
xml = os.path.join(report, "tests.xml")
if os.path.exists(xml):
    run = ET.parse(xml).getroot()
    print("tests: %s passed, %s failed of %s, %s s (in the pipeline process)" % (run.get("passed"), run.get("failed"),
                                                                              run.get("total"), run.get("duration")))
    for case in run.iter("test-case"):
        if case.get("result") != "Passed":
            print("FAIL test %s" % case.get("fullname"))
else:
    print("tests: no results - see pipeline.log")
PY
[ "$code" -eq 0 ]
