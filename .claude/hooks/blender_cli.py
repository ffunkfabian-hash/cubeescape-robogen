#!/usr/bin/env python3
"""`blender` command line for Claude Code cloud sessions: Blender as a Python module (PyPI `bpy`).

The cloud's network blocks download.blender.org, but PyPI is open: `session-start.sh` installs `bpy` into a venv and
this file as `<venv>/bin/blender`. It understands the batch arguments the generators use
(`-b`, `--factory-startup`, `-noaudio`, `-t`, `--python-exit-code`, `-P/--python`, `--python-expr`, a .blend file,
`--version`); everything after `--` stays in `sys.argv` for the script, as in Blender. `bpy.app.binary_path` points
back here, so worker processes started from a script find the same command.
Not the real application: no GUI, rendering headless on the CPU (EEVEE via Mesa's EGL).
"""
import os
import runpy
import sys
import traceback

SKIPPED = {"-b", "--background", "--factory-startup", "-noaudio", "--noaudio", "-y", "--enable-autoexec",
           "--disable-autoexec", "--quiet", "-q"}


def main():
    argv = sys.argv[1:]
    if "--" in argv:
        argv = argv[:argv.index("--")]
    exit_code, actions, blend, show_version = 0, [], None, False
    it = iter(argv)
    for arg in it:
        if arg in SKIPPED:
            continue
        if arg == "--python-exit-code":
            exit_code = int(next(it))
        elif arg in ("-P", "--python"):
            actions.append(("file", next(it)))
        elif arg == "--python-expr":
            actions.append(("expr", next(it)))
        elif arg in ("-t", "--threads"):
            next(it)  # the module build picks its own thread count
        elif arg in ("-v", "--version"):
            show_version = True
        elif arg.endswith(".blend"):
            blend = arg
        else:
            print(f"blender (bpy module): ignoring argument {arg!r}", file=sys.stderr)

    import bpy

    bpy.app.binary_path = os.path.abspath(sys.argv[0])  # writable only in the module build
    if show_version:
        print(f"Blender {bpy.app.version_string} (bpy module)")
        return 0
    if blend:
        bpy.ops.wm.open_mainfile(filepath=blend)
    sys.argv = [bpy.app.binary_path] + sys.argv[1:]
    for kind, source in actions:
        try:
            if kind == "file":
                runpy.run_path(source, run_name="__main__")
            else:
                exec(compile(source, "<string>", "exec"), {"__name__": "__main__"})  # noqa: S102 - --python-expr
        except SystemExit:
            raise
        except Exception:  # noqa: BLE001 - like Blender: traceback, then --python-exit-code
            traceback.print_exc()
            return exit_code
    return 0


if __name__ == "__main__":
    sys.exit(main())
