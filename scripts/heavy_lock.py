#!/usr/bin/env python3
"""Forwarder to the embedded shared lock (scripts/ce_tools/heavy_lock.py, 1.20.7) - the one heavy-run lock of Cube
Escape, Cubegen, Toolgen and the generator. Kept because generate_robot.py, quiet.py and unity/run_testbed.sh import or
call `heavy_lock` from the scripts folder.

    python3 scripts/heavy_lock.py run [--what LABEL] [--checkout PATH] -- <command ...>
    python3 scripts/heavy_lock.py status

The embedded copy defaults the checkout to the current directory; this forwarder keeps the old behaviour (the
repository root). Do not put logic here - change the lock in the plugin repo (ce-agent-tools) and run ce_sync.py.
"""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ce_tools", "heavy_lock.py")
_spec = importlib.util.spec_from_file_location("ce_tools_heavy_lock", _path)
_embedded = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _embedded
_spec.loader.exec_module(_embedded)

_embedded.ROOT = ROOT
_init = _embedded.HeavyLock.__init__


def _init_in_root(self, what, checkout=None, *args, **kwargs):
    _init(self, what, checkout or ROOT, *args, **kwargs)


_embedded.HeavyLock.__init__ = _init_in_root

if __name__ == "__main__":
    argv = sys.argv[1:]
    if argv[:1] == ["run"] and "--checkout" not in argv:
        argv = ["run", "--checkout", ROOT] + argv[1:]
    sys.exit(_embedded.main(argv))
else:
    sys.modules[__name__] = _embedded
