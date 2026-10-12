"""Profiles a generator run with cProfile (main process only; use --workers 0 to see the work packages too).

Usage:
    Blender -b --factory-startup --python-exit-code 1 --python scripts/profile_run.py -- --tier quick --workers 0

Writes <out>/logs/profile.prof (open with snakeviz or pstats) and prints the top functions by own time and by
cumulative time.
"""

import cProfile
import io
import os
import pstats
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import generate_robot  # noqa: E402

profiler = cProfile.Profile()
code = profiler.runcall(generate_robot.main)
out = os.path.abspath(generate_robot.parse_args().out)
path = os.path.join(out, "logs", "profile.prof")
profiler.dump_stats(path)
for key in ("tottime", "cumulative"):
    buf = io.StringIO()
    pstats.Stats(profiler, stream=buf).sort_stats(key).print_stats(35)
    print("\n".join(line for line in buf.getvalue().splitlines() if line.strip())[:12000])
print("profile: %s" % path)
sys.exit(code)
