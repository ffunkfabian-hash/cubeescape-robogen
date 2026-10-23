#!/usr/bin/env python3
"""Forwarder to the shared lock (heavy_lock.py of the pinned ce-agent-tools, `./ce tools where`) - the one lock of Cube
Escape, Cubegen, Toolgen and the generator. Kept because generate_robot.py, quiet.py and unity/run_testbed.sh import or
call `heavy_lock` from the scripts folder.

    python3 scripts/heavy_lock.py run [--what LABEL] [--checkout PATH] -- <command ...>
    python3 scripts/heavy_lock.py status

The shared lock defaults the checkout to the current directory; this forwarder keeps the old behaviour (the
repository root). Do not put logic here - change the lock in the plugin repo (ce-agent-tools) and re-pin tools_version.
"""
import importlib.util
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _tools_dir():
    out = subprocess.run([sys.executable, os.path.join(ROOT, "ce"), "tools", "where"], capture_output=True, text=True)
    folder = out.stdout.strip().split(" (")[0]
    if out.returncode or not os.path.isfile(os.path.join(folder, "heavy_lock.py")):
        sys.exit("heavy_lock: shared tools not available - run ./ce tools fetch\n" + out.stderr)
    return folder


_path = os.path.join(_tools_dir(), "heavy_lock.py")
_spec = importlib.util.spec_from_file_location("ce_tools_heavy_lock", _path)
_shared = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _shared
_spec.loader.exec_module(_shared)

_shared.ROOT = ROOT
_init = _shared.HeavyLock.__init__


def _init_in_root(self, what, checkout=None, *args, **kwargs):
    _init(self, what, checkout or ROOT, *args, **kwargs)


_shared.HeavyLock.__init__ = _init_in_root

if __name__ == "__main__":
    argv = sys.argv[1:]
    if argv[:1] == ["run"] and "--checkout" not in argv:
        argv = ["run", "--checkout", ROOT] + argv[1:]
    sys.exit(_shared.main(argv))
else:
    sys.modules[__name__] = _shared
