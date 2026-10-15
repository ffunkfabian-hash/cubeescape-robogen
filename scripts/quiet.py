"""Quiet machine for heavy steps (1.20.2): full run and check_output.sh pause other sessions while they run.

    python3 scripts/quiet.py status              # what would happen: busy or quiet, and why
    python3 scripts/quiet.py run -- <command …>  # run the command, through pause_sessions.py when the machine is busy

Busy = processes of other agent sessions that load the machine (Unity, Blender, the generator, Cube Escape test
runs - started below another Claude/Codex process, not by this run), or a load average above ROBOGEN_QUIET_LOAD
(default 0.3 per core: the "quiet" bound of the time band) while other sessions' commands use the CPU (OTHERS_CPU) -
load from this session's previous steps or from the user's own programs alone pauses nobody. Then the command runs through Cube Escape's
pause_sessions.py (AGENTS.md › Ganze Leistung des Geräts): it stops only commands of other agent sessions (SIGSTOP),
always resumes them (SIGCONT) when the command ends, and never touches the user's own Unity editor or Blender - those
are not below an agent. The pause lasts exactly as long as the command. Never two pauses at once: if another session's pause_sessions.py run
is active, or another generator run holds the lock (QUIET_LOCK), the command runs normally - two pausing runs would
stop each other for good.

    ROBOGEN_NO_PAUSE=1         never pause (run normally, the decision is still printed)
    ROBOGEN_PAUSE_TOOL=<path>  other pause tool (default: the Cube Escape main checkout's)
    ROBOGEN_QUIET_LOAD=<x>     load average from which the machine counts as busy
    ROBOGEN_QUIET_ACTIVE       set for the command: nested heavy steps do not wrap themselves again
    ROBOGEN_QUIET_INFO         set for the command: one line "paused=<0|1>; reason" for its report

Pure Python (no bpy): generate_robot.py imports it inside Blender, check_output.sh calls it with python3.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys

PAUSE_TOOL = "/Users/ffunk/Claude/Cube Escape/.agents/skills/ce-testing/scripts/pause_sessions.py"
PAUSE_STATE = os.path.join(os.path.expanduser("~"), "Library", "Caches", "CubeEscape", "paused_sessions.json")
QUIET_LOCK = os.path.join(os.path.expanduser("~"), "Library", "Caches", "RobotGen", "quiet.lock")
LOAD_PER_CORE = 0.3         # = pipeline.LOAD_QUIET
OTHERS_CPU = 50.0           # % CPU (one core = 100) of other sessions' commands that makes a high load theirs
# programs that load the machine when another session runs them
HEAVY = re.compile(r"Unity\.app/Contents/MacOS/Unity|(^|/)Blender(\.app/Contents/MacOS/Blender)?(\s|$)|(^|[\s/])blender(\s|$)"
                   r"|generate_robot\.py|dotnet\s.*test|xcodebuild|swift-build|swift-frontend")
# the agent programs themselves (as pause_sessions.py: path or name of the first word)
AGENT = re.compile(r"/claude\.app/Contents/MacOS/claude(\s|$)|(^|/)bin/claude(\s|$)|(^|[\s/])codex(\.js)?(\s|$)")


def processes() -> dict:
    """pid -> (parent pid, command, % CPU)."""
    try:
        out = subprocess.run(["ps", "-axo", "pid=,ppid=,%cpu=,command="], capture_output=True, text=True,
                             timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    table = {}
    for line in out.splitlines():
        parts = line.strip().split(None, 3)
        if len(parts) >= 3 and parts[0].isdigit() and parts[1].isdigit():
            try:
                cpu = float(parts[2].replace(",", "."))
            except ValueError:
                cpu = 0.0
            table[int(parts[0])] = (int(parts[1]), parts[3] if len(parts) > 3 else "", cpu)
    return table


def _cpu(entry) -> float:
    return entry[2] if len(entry) > 2 else 0.0


def ancestry(pid: int, table: dict) -> list:
    chain = []
    while pid in table and pid not in chain and pid > 1:
        chain.append(pid)
        pid = table[pid][0]
    return chain


def other_sessions(table: dict, me: int | None = None) -> list:
    """Processes started by another agent session (pid list). Never ours (our ancestry and everything below the agent
    we run under) and never processes without an agent above them (the user's own Unity editor or Blender)."""
    me = os.getpid() if me is None else me
    own = ancestry(me, table)
    own_agent = next((p for p in own if AGENT.search(table[p][1])), None)
    found = []
    for pid, entry in table.items():
        if pid in own or AGENT.search(entry[1]):
            continue
        agents = [p for p in ancestry(pid, table)[1:] if AGENT.search(table[p][1])]
        if agents and (own_agent is None or own_agent not in agents):
            found.append(pid)
    return found


def other_sessions_load(table: dict, me: int | None = None) -> list:
    """Heavy processes of other agent sessions: (pid, short command)."""
    return [(pid, table[pid][1][:120]) for pid in other_sessions(table, me) if HEAVY.search(table[pid][1])]


def other_pause(table: dict, me: int | None = None) -> int | None:
    """pid of another pause_sessions.py run that is active right now (not ours)."""
    me = os.getpid() if me is None else me
    own = ancestry(me, table)
    for pid, entry in table.items():
        if pid not in own and "pause_sessions.py run" in entry[1]:
            return pid
    return None


def load_threshold() -> float:
    try:
        return float(os.environ["ROBOGEN_QUIET_LOAD"])
    except (KeyError, ValueError):
        return LOAD_PER_CORE * (os.cpu_count() or 1)


def decide(table: dict | None = None, load: float | None = None, me: int | None = None) -> dict:
    """{"busy": bool, "reasons": [...], "load": x, "threshold": y}."""
    table = processes() if table is None else table
    load = os.getloadavg()[0] if load is None else load
    limit = load_threshold()
    cpu = sum(_cpu(table[p]) for p in other_sessions(table, me))
    reasons = ["load %.1f above %.1f, other sessions use %.0f %% CPU" % (load, limit, cpu)] \
        if load > limit and cpu >= OTHERS_CPU else []
    others = other_sessions_load(table, me)
    if others:
        reasons.append("%d heavy process(es) of other sessions (%s)" % (
            len(others), ", ".join(os.path.basename(c.split()[0]) for _, c in others[:4])))
    return {"busy": bool(reasons), "reasons": reasons, "load": round(load, 1), "threshold": round(limit, 1),
            "others": others}


def pause_tool() -> str | None:
    path = os.environ.get("ROBOGEN_PAUSE_TOOL", PAUSE_TOOL)
    return path if os.path.isfile(path) else None


def _lock():
    """The generator's pause lock (held while the paused command runs), or None when another run holds it."""
    try:
        import fcntl
        os.makedirs(os.path.dirname(QUIET_LOCK), exist_ok=True)
        fh = open(QUIET_LOCK, "a")
    except (ImportError, OSError):
        return True                 # no locking possible: rely on the process check
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def plan(table: dict | None = None, load: float | None = None) -> dict:
    """The decision plus how the command will run: "pause" (through the tool) or "direct", with the reason. In pause
    mode the result holds the lock ("lock") - keep it until the command has ended."""
    if os.environ.get("ROBOGEN_QUIET_ACTIVE"):
        return {"mode": "direct", "why": "already inside a quiet run", "busy": None, "reasons": []}
    table = processes() if table is None else table
    d = decide(table, load)
    if not d["busy"]:
        d.update(mode="direct", why="machine quiet (load %.1f)" % d["load"])
    elif os.environ.get("ROBOGEN_NO_PAUSE"):
        d.update(mode="direct", why="busy (%s), but ROBOGEN_NO_PAUSE is set" % "; ".join(d["reasons"]))
    elif not pause_tool():
        d.update(mode="direct", why="busy (%s), but the pause tool is missing (%s) - running without pause" % (
            "; ".join(d["reasons"]), os.environ.get("ROBOGEN_PAUSE_TOOL", PAUSE_TOOL)), warning=True)
    else:
        busy = "; ".join(d["reasons"])
        other = other_pause(table) or (os.path.exists(PAUSE_STATE) and "state file")
        lock = None if other else _lock()
        if other or lock is None:
            d.update(mode="direct", why="busy (%s), but another session pauses right now (%s) - running normally"
                     % (busy, other or "generator lock"))
        else:
            d.update(mode="pause", why="busy: %s" % busy, lock=lock)
    return d


def command_for(cmd: list, p: dict) -> tuple:
    """(argv, env) to run cmd as plan p says."""
    env = dict(os.environ, ROBOGEN_QUIET_ACTIVE="1",
               ROBOGEN_QUIET_INFO="paused=%d; %s" % (p["mode"] == "pause", p["why"]))
    if p["mode"] != "pause":
        return list(cmd), env
    python = shutil.which("python3") or "/usr/bin/python3"
    return [python, pause_tool(), "run", "--"] + list(cmd), env


def run(cmd: list, log=print) -> int:
    p = plan()
    log("quiet: %s -> %s" % (p["why"], "running through pause_sessions.py (other sessions paused while it runs)"
                             if p["mode"] == "pause" else "running normally"))
    argv, env = command_for(cmd, p)
    return subprocess.call(argv, env=env)


def main(argv: list) -> int:
    if argv[:1] == ["status"]:
        p = plan()
        print("quiet: mode=%s - %s" % (p["mode"], p["why"]))
        for pid, cmd in p.get("others", []):
            print("  other session: %d %s" % (pid, cmd))
        return 0
    if argv[:1] == ["run"]:
        cmd = argv[1:]
        if cmd[:1] == ["--"]:
            cmd = cmd[1:]
        if cmd:
            return run(cmd)
    print(__doc__.strip())
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
