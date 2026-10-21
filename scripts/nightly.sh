#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
BLENDER="${BLENDER:-/Applications/Blender.app/Contents/MacOS/Blender}"
"$BLENDER" -b --factory-startup --python-exit-code 1 --python scripts/generate_robot.py -- --tier full
bash scripts/check_output.sh output
