#!/usr/bin/env python3
"""Release command (1.20.3): the manual release steps of AGENTS.md › Release as one command, from a worktree or the
main checkout. Dry run by default - it checks everything and prints what it would do; --apply does it.

    python3 scripts/release.py 1.20.3                       # dry run: checks + the planned steps
    python3 scripts/release.py 1.20.3 --apply               # merge, tag, push
    python3 scripts/release.py 1.20.3 -m "Generator 1.20.3 – Powdercoat: …" --apply
    python3 scripts/release.py 1.20.3 -F notes.txt --apply  # tag message from a file

Steps (the repository's rule: linear history, main only moves forward):
1. Checks - all before anything changes; any FAIL stops:
   - this checkout (the branch to release) and the main checkout are clean (no changes, no untracked files), the main
     checkout is on main;
   - fast-forward only: the branch contains main and the remote's main (otherwise: `git merge main` in the branch,
     test, run again);
   - tag v<version> and branch release/<version> exist neither on the remote nor locally (or already point at the
     release commit - then a second --apply resumes an interrupted release);
   - CHANGELOG.md has a section "## <version>"; VERSION in scripts/robogen/core.py differs = only a warning (patches
     without generator change keep it);
   - no file over 100 MB among the objects the push sends (GitHub refuses them; over 50 MB = warning);
   - no credentials in the added lines or files (private keys, GitHub/AWS/Slack/API tokens, password assignments,
     .env/.pem/id_rsa files); a line with "release-check: allow" is skipped on purpose.
2. Main checkout: `git merge --ff-only <branch>` (nothing to do when the branch is main).
3. Annotated tag v<version> (message: -m/-F, else the newest commit subject in the release that names the version)
   and branch release/<version> on the release commit.
4. One atomic push of main, the tag and release/<version> - never --force: if the remote moved, nothing is pushed.
5. Report: commit, tag and links (GitHub remotes).

The remote (default origin) is only read in the dry run (git ls-remote). Exit code 1 on a failed check or step.
Tests: tests/test_release.py (temporary repositories, no network).
"""
from __future__ import annotations

import argparse
import fnmatch
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = "main"
MAX_FILE_BYTES = 100 * 1024 * 1024      # GitHub rejects larger files
WARN_FILE_BYTES = 50 * 1024 * 1024      # GitHub warns from here
ALLOW_MARK = "release-check: allow"

# credentials in added lines: (name, pattern)
SECRET_PATTERNS = [
    ("private key", re.compile(r"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY-----")),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})")),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("Slack token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}")),
    ("API key (sk-…)", re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{24,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("password/secret assignment", re.compile(
        r"(?i)\b(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret)\b"
        r"[\"']?\s*[:=]\s*[\"'][^\"'\s]{8,}[\"']")),
]
# file names that hold credentials
SECRET_FILES = (".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*", "id_ecdsa*",
                ".netrc", ".npmrc", ".pypirc", "credentials*.json", "*.keystore", "*.mobileprovision")


class Failure(Exception):
    pass


def git(cwd: str, *args, check: bool = True, input_text: str | None = None) -> str:
    proc = subprocess.run(["git", "-C", cwd] + list(args), capture_output=True, text=True, input=input_text)
    if check and proc.returncode != 0:
        raise Failure("git %s failed: %s" % (" ".join(args), (proc.stderr or proc.stdout).strip()))
    return proc.stdout.strip()


def git_ok(cwd: str, *args) -> bool:
    return subprocess.run(["git", "-C", cwd] + list(args), capture_output=True).returncode == 0


def main_checkout(repo: str) -> str:
    common = git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if not common.endswith(".git"):
        raise Failure("no main checkout found (git common dir %s)" % common)
    return os.path.dirname(common)


def remote_refs(main: str, remote: str) -> dict:
    """ref -> sha of the remote's heads and tags (peeled tags as ref^{}); read only."""
    out = git(main, "ls-remote", "--heads", "--tags", remote)
    refs = {}
    for line in out.splitlines():
        sha, _, ref = line.partition("\t")
        if ref:
            refs[ref] = sha
    return refs


def web_base(url: str) -> str | None:
    """https://github.com/<owner>/<repo> for a GitHub remote URL, else None."""
    m = re.match(r"^(?:https://(?:[^@/]+@)?github\.com/|git@github\.com:|ssh://git@github\.com/)"
                 r"([^/]+)/(.+?)(?:\.git)?/?$", url.strip())
    return "https://github.com/%s/%s" % (m.group(1), m.group(2)) if m else None


def changelog_has(text: str, version: str) -> bool:
    return re.search(r"^##\s+%s(\s|$)" % re.escape(version), text, re.M) is not None


def generator_version(text: str) -> str | None:
    m = re.search(r'^VERSION = "([^"]+)"', text, re.M)
    return m.group(1) if m else None


def scan_secrets(diff_text: str) -> list:
    """(file, line, kind) for added lines with credentials in a unified diff (-U0)."""
    found, path, line_no = [], None, 0
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
            continue
        if line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            line_no = int(m.group(1)) if m else 0
            continue
        if line.startswith("+") and path:
            if ALLOW_MARK not in line:
                for kind, pattern in SECRET_PATTERNS:
                    if pattern.search(line):
                        found.append((path, line_no, kind))
                        break
            line_no += 1
    return found


def secret_files(paths) -> list:
    return [p for p in paths if any(fnmatch.fnmatch(os.path.basename(p), pat) for pat in SECRET_FILES)]


def big_objects(cwd: str, target: str, exclude: list) -> list:
    """(path, bytes) of blobs the push would send, largest first (only above WARN_FILE_BYTES)."""
    listing = git(cwd, "rev-list", "--objects", target, "--not", *exclude) if exclude else \
        git(cwd, "rev-list", "--objects", target)
    names = {}
    for line in listing.splitlines():
        sha, _, path = line.partition(" ")
        if path:
            names[sha] = path
    if not names:
        return []
    out = git(cwd, "cat-file", "--batch-check=%(objecttype) %(objectsize) %(objectname)",
              input_text="\n".join(names) + "\n")
    big = []
    for line in out.splitlines():
        kind, size, sha = line.split()
        if kind == "blob" and int(size) > WARN_FILE_BYTES:
            big.append((names[sha], int(size)))
    return sorted(big, key=lambda x: -x[1])


class Release:
    def __init__(self, version: str, repo: str, branch: str | None, remote: str, message: str | None,
                 out=None):
        self.version, self.repo, self.remote, self.message = version, repo, remote, message
        self.out = out or (lambda line: print(line, flush=True))
        self.tag, self.release_branch = "v" + version, "release/" + version
        self.branch = branch
        self.results = []           # (state, text): PASS / WARN / FAIL
        self.main = None
        self.target = None
        self.remote_main = None
        self.tag_exists = self.branch_exists = False

    # -- checks --------------------------------------------------------------------------------------------------
    def check(self, ok: bool, text: str, warn: bool = False) -> bool:
        self.results.append(("PASS" if ok else "WARN" if warn else "FAIL", text))
        return ok

    def failed(self) -> list:
        return [t for s, t in self.results if s == "FAIL"]

    def run_checks(self) -> None:
        if not self.check(re.fullmatch(r"\d+\.\d+\.\d+", self.version) is not None,
                          "version %s has the form x.y.z" % self.version):
            return
        self.main = main_checkout(self.repo)
        if not self.branch:
            self.branch = git(self.repo, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
        if not self.check(bool(self.branch), "the release checkout is on a branch (%s)" % (self.branch or "detached")):
            return
        if not git_ok(self.main, "rev-parse", "--verify", "--quiet", "refs/heads/" + self.branch):
            self.check(False, "branch %s exists" % self.branch)
            return
        self.target = git(self.main, "rev-parse", "refs/heads/" + self.branch)
        dirty = git(self.repo, "status", "--porcelain")
        self.check(not dirty, "release checkout %s is clean%s" % (self.repo, "" if not dirty else
                                                                   ": " + "; ".join(x.strip() for x in dirty.splitlines()[:5])))
        if os.path.realpath(self.main) != os.path.realpath(self.repo):
            dirty = git(self.main, "status", "--porcelain")
            self.check(not dirty, "main checkout %s is clean%s" % (self.main, "" if not dirty else
                                                                    ": " + "; ".join(x.strip() for x in dirty.splitlines()[:5])))
        head = git(self.main, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
        self.check(head == MAIN, "main checkout is on %s (%s)" % (MAIN, head or "detached"))
        main_sha = git(self.main, "rev-parse", "refs/heads/" + MAIN)
        ff = git_ok(self.main, "merge-base", "--is-ancestor", main_sha, self.target)
        self.check(ff, "fast-forward: %s contains %s%s" % (self.branch, MAIN, "" if ff else
                       " - merge %s into the branch first (git merge %s), test, run again" % (MAIN, MAIN)))
        try:
            refs = remote_refs(self.main, self.remote)
        except Failure as e:
            self.check(False, "remote %s readable: %s" % (self.remote, e))
            refs = None
        if refs is not None:
            self.remote_main = refs.get("refs/heads/" + MAIN)
            if self.remote_main is None:
                self.check(True, "remote %s has no %s yet" % (self.remote, MAIN))
            elif not git_ok(self.main, "cat-file", "-e", self.remote_main + "^{commit}"):
                self.check(False, "remote %s/%s (%s) has commits this repository lacks - fetch it, merge it into "
                                  "the branch, test, run again" % (self.remote, MAIN, self.remote_main[:9]))
                self.remote_main = None
            else:
                self.check(git_ok(self.main, "merge-base", "--is-ancestor", self.remote_main, self.target),
                           "fast-forward: %s contains %s/%s (%s) - no force push" % (
                               self.branch, self.remote, MAIN, self.remote_main[:9]))
            remote_tag = refs.get("refs/tags/%s^{}" % self.tag) or refs.get("refs/tags/" + self.tag)
            self.check(remote_tag in (None, self.target), "tag %s is not on %s yet%s" % (
                self.tag, self.remote, "" if remote_tag in (None, self.target) else " (it is, at %s)" % remote_tag[:9]))
            remote_rb = refs.get("refs/heads/" + self.release_branch)
            self.check(remote_rb in (None, self.target), "branch %s is not on %s yet%s" % (
                self.release_branch, self.remote, "" if remote_rb in (None, self.target) else
                " (it is, at %s)" % remote_rb[:9]))
        local_tag = git(self.main, "rev-parse", "--verify", "--quiet", "refs/tags/%s^{commit}" % self.tag, check=False)
        if local_tag:
            is_annotated = git(self.main, "cat-file", "-t", "refs/tags/" + self.tag) == "tag"
            same = local_tag == self.target and is_annotated
            self.tag_exists = self.check(same, "local tag %s %s" % (self.tag, "already exists on the release commit "
                                         "(annotated) - kept" if same else "exists elsewhere (%s%s) - delete or "
                                         "choose another version" % (local_tag[:9], "" if is_annotated else
                                                                     ", lightweight")))
        local_rb = git(self.main, "rev-parse", "--verify", "--quiet", "refs/heads/" + self.release_branch,
                       check=False)
        if local_rb:
            same = local_rb == self.target
            self.branch_exists = self.check(same, "local branch %s %s" % (
                self.release_branch, "already exists on the release commit - kept" if same else
                "exists elsewhere (%s)" % local_rb[:9]))
        self.check_contents(main_sha)
        if self.message is None:
            self.message = self.default_message(main_sha)
        self.check(bool(self.message), "tag message: %s" % (self.message.splitlines()[0] if self.message else
                                                             "none - give -m or -F (no commit subject names %s)"
                                                             % self.version))

    def check_contents(self, main_sha: str) -> None:
        changelog = git(self.main, "show", "%s:CHANGELOG.md" % self.target, check=False)
        self.check(changelog_has(changelog, self.version), "CHANGELOG.md has a section ## %s" % self.version)
        core = git(self.main, "show", "%s:scripts/robogen/core.py" % self.target, check=False)
        gen = generator_version(core)
        if gen is not None:
            self.check(gen == self.version, "VERSION in scripts/robogen/core.py is %s%s" % (
                gen, "" if gen == self.version else " (not %s - fine when the generator did not change)"
                % self.version), warn=True)
        # what the push sends: everything the remote does not have yet
        exclude = ["--remotes=" + self.remote] + ([self.remote_main] if self.remote_main else [])
        big = big_objects(self.main, self.target, exclude)
        too_big = [(p, s) for p, s in big if s > MAX_FILE_BYTES]
        self.check(not too_big, "no file over %d MB in the push%s" % (
            MAX_FILE_BYTES // 2 ** 20, "" if not too_big else ": " + ", ".join(
                "%s (%.0f MB)" % (p, s / 2 ** 20) for p, s in too_big[:5])))
        for p, s in big:
            if s <= MAX_FILE_BYTES:
                self.check(False, "large file %s (%.0f MB, GitHub warns from %d MB)" % (
                    p, s / 2 ** 20, WARN_FILE_BYTES // 2 ** 20), warn=True)
        base = self.remote_main or main_sha
        base = git(self.main, "merge-base", base, self.target, check=False) or base
        diff = git(self.main, "diff", "--no-color", "--no-ext-diff", "-U0", base, self.target)
        found = scan_secrets(diff)
        self.check(not found, "no credentials in the added lines%s" % ("" if not found else ": " + ", ".join(
            "%s:%d (%s)" % f for f in found[:5])))
        added = git(self.main, "diff", "--name-only", "--diff-filter=AR", base, self.target).splitlines()
        files = secret_files(added)
        self.check(not files, "no credential files added%s" % ("" if not files else ": " + ", ".join(files[:5])))

    def default_message(self, main_sha: str) -> str | None:
        """The newest commit subject in the release that names the version (the repository's tag style)."""
        log = git(self.main, "log", "--format=%s", "%s..%s" % (main_sha, self.target), check=False).splitlines()
        if not log:
            log = git(self.main, "log", "-1", "--format=%s", self.target).splitlines()
        pattern = re.compile(r"(?<![\d.])%s(?![\d.])" % re.escape(self.version))
        return next((s for s in log if pattern.search(s)), None)

    # -- steps ---------------------------------------------------------------------------------------------------
    def steps(self) -> list:
        """(description, git argv in the main checkout) - what --apply runs."""
        todo = []
        main_sha = git(self.main, "rev-parse", "refs/heads/" + MAIN)
        if main_sha != self.target:
            todo.append(("merge %s into %s (fast-forward)" % (self.branch, MAIN),
                         ["merge", "--ff-only", self.target]))
        if not self.tag_exists:
            todo.append(("annotated tag %s" % self.tag, ["tag", "-a", self.tag, "-m", self.message, self.target]))
        if not self.branch_exists:
            todo.append(("branch %s" % self.release_branch, ["branch", self.release_branch, self.target]))
        todo.append(("push %s, %s and %s to %s in one atomic push (no --force)" % (
            MAIN, self.tag, self.release_branch, self.remote),
            ["push", "--atomic", self.remote, "refs/heads/%s:refs/heads/%s" % (MAIN, MAIN),
             "refs/tags/%s:refs/tags/%s" % (self.tag, self.tag),
             "refs/heads/%s:refs/heads/%s" % (self.release_branch, self.release_branch)]))
        return todo

    def links(self) -> list:
        url = git(self.main, "remote", "get-url", self.remote, check=False)
        base = web_base(url)
        if not base:
            return ["remote %s: %s (no GitHub links)" % (self.remote, url)]
        return ["commit   %s/commit/%s" % (base, self.target), "tag      %s/releases/tag/%s" % (base, self.tag),
                "branch   %s/tree/%s" % (base, self.release_branch)]

    def run(self, apply: bool) -> int:
        try:
            self.run_checks()
        except Failure as e:
            self.check(False, str(e))
        self.out("Release %s (%s)%s" % (self.version, self.branch or "?", "" if apply else " - dry run"))
        for state, text in self.results:
            self.out("  %-4s %s" % (state, text))
        if self.failed():
            self.out("stopped: %d check(s) failed - nothing changed" % len(self.failed()))
            return 1
        steps = self.steps()
        self.out("%s (in %s):" % ("Steps" if apply else "Would run", self.main))
        for i, (text, argv) in enumerate(steps, 1):
            self.out("  %d. %s" % (i, text))
            self.out("     git %s" % " ".join(a if " " not in a and a else repr(a) for a in argv))
            if apply:
                try:
                    git(self.main, *argv)
                except Failure as e:
                    self.out("FAILED at step %d: %s" % (i, e))
                    self.out("Fix the cause and run the same command again: done steps are recognised and kept.")
                    return 1
        if not apply:
            self.out("dry run: nothing changed - run again with --apply")
            return 0
        self.out("released %s: %s on %s" % (self.tag, self.target[:9], MAIN))
        for line in self.links():
            self.out("  " + line)
        if os.path.realpath(self.repo) != os.path.realpath(self.main):
            self.out("worktree %s can go now: git -C %s worktree remove %s" % (self.repo, self.main, self.repo))
        return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("version", help="x.y.z (tag v<version>, branch release/<version>)")
    msg = ap.add_mutually_exclusive_group()
    msg.add_argument("-m", "--message", help="tag message")
    msg.add_argument("-F", "--message-file", help="tag message from a file")
    ap.add_argument("--branch", help="branch to release (default: the one checked out here)")
    ap.add_argument("--remote", default="origin")
    ap.add_argument("--repo", default=None, help="checkout to release from (default: this script's checkout)")
    ap.add_argument("--apply", action="store_true", help="do it (default: dry run)")
    args = ap.parse_args(argv)
    message = args.message
    if args.message_file:
        with open(args.message_file, encoding="utf-8") as fh:
            message = fh.read().strip()
    repo = args.repo or ROOT
    repo = git(repo, "rev-parse", "--show-toplevel") if os.path.isdir(repo) else repo
    return Release(args.version, repo, args.branch, args.remote, message).run(args.apply)


if __name__ == "__main__":
    sys.exit(main())
