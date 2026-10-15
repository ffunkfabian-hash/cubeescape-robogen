#!/bin/bash
# Checks a generator output in parallel: verify_robot.py (Blender), viewer build + tests, Unity test package.
# The three only read the output (the viewer writes RobotViewer.app, Unity its report folder), so they run side by
# side; a sequential chain took about a minute longer.
#   scripts/check_output.sh [output]      default: output (full run); e.g. output/_standard
# 1.20.2: on a busy machine (other sessions' Unity/Blender/generator, high load) the checks run through Cube Escape's
# pause_sessions.py (scripts/quiet.py); ROBOGEN_NO_PAUSE=1 never pauses.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ -z "${ROBOGEN_QUIET_ACTIVE:-}" ]; then
  exec python3 "$ROOT/scripts/quiet.py" run -- "$ROOT/scripts/check_output.sh" "$@"
fi
OUT="$(cd "${1:-$ROOT/output}" && pwd)"
BLENDER="${BLENDER:-/Applications/Blender.app/Contents/MacOS/Blender}"
LOGS="$OUT/logs/checks"
mkdir -p "$LOGS"
t0=$(date +%s)

run() {     # name, command...: runs in the background, writes <name>.log and <name>.status ("exit seconds")
  local name=$1; shift
  ( s=$(date +%s); "$@" > "$LOGS/$name.log" 2>&1; echo "$? $(( $(date +%s) - s ))" > "$LOGS/$name.status" ) &
}

rm -f "$LOGS"/*.status
run verify "$BLENDER" -b "$OUT/CubeEscape_Robot.blend" --python-exit-code 1 --python "$ROOT/scripts/verify_robot.py" -- --out "$OUT"
run viewer bash -c "'$ROOT/viewer/build.sh' '$OUT' && '$ROOT/viewer/test_viewer.sh' '$OUT'"
run unity "$ROOT/unity/run_testbed.sh" "$OUT"
wait

fail=0
for name in verify viewer unity; do
  code=99; secs="?"
  [ -f "$LOGS/$name.status" ] && read -r code secs < "$LOGS/$name.status"     # missing = the process died
  case $name in
    verify) summary=$(grep -E "checks passed" "$LOGS/$name.log" | tail -1) ;;
    viewer) summary="$(grep -c '^PASS' "$LOGS/$name.log") PASS, $(grep -c '^FAIL' "$LOGS/$name.log") FAIL" ;;
    unity)  summary=$(grep -E "^(pipeline|tests):" "$LOGS/$name.log" | tr '\n' ' ') ;;
  esac
  if [ "$name" = unity ] && [ "$code" = 2 ]; then
    echo "SKIP unity ($secs s): Unity not found or already running - see $LOGS/unity.log"
    continue
  fi
  [ "$code" = 0 ] && state=PASS || { state=FAIL; fail=1; }
  echo "$state $name ($secs s): $summary"
  [ "$state" = FAIL ] && grep -E "FAIL|Error|error CS" "$LOGS/$name.log" | head -5 | sed 's/^/    /'
done
echo "checks: $(( $(date +%s) - t0 )) s in parallel (logs: $LOGS); quiet: ${ROBOGEN_QUIET_INFO:-?}"
exit $fail
