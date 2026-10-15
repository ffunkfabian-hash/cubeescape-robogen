"""Release command (scripts/release.py, 1.20.3) against temporary repositories: a bare "remote", its main checkout and
a worktree with the release branch - no network."""
import os
import subprocess
import unittest
from unittest import mock

from helpers import tempdir

import release

# built at run time, so this file itself holds no credential-shaped text
FAKE_TOKEN = "gh" + "p_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
FAKE_KEY = "-----BEGIN " + "RSA PRIVATE KEY-----"


def sh(cwd, *args):
    return subprocess.run(["git", "-C", cwd] + list(args), check=True, capture_output=True, text=True).stdout.strip()


class Repo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempdir(self)
        cfg = os.path.join(self.tmp, "gitconfig")
        with open(cfg, "w") as fh:
            fh.write("[user]\n\tname = Test\n\temail = test@example.com\n[init]\n\tdefaultBranch = main\n"
                     "[advice]\n\tdetachedHead = false\n")
        env = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": cfg, "GIT_CONFIG_NOSYSTEM": "1"})
        env.start()
        self.addCleanup(env.stop)
        self.remote = os.path.join(self.tmp, "remote.git")
        self.main = os.path.join(self.tmp, "main")
        sh(self.tmp, "init", "--bare", "-b", "main", self.remote)
        sh(self.tmp, "clone", self.remote, self.main)
        self.write(self.main, "CHANGELOG.md", "# Changelog\n\n## 1.0.0 „Base“\n")
        self.write(self.main, ".gitignore", "output/\n")
        self.commit(self.main, "Generator 1.0.0 – Base")
        sh(self.main, "push", "origin", "main")
        self.wt = os.path.join(self.tmp, "wt")
        sh(self.main, "worktree", "add", "-b", "claude/next-1.0.1", self.wt, "main")
        self.write(self.wt, "CHANGELOG.md", "# Changelog\n\n## 1.0.1 „Base“ – heute\n\n## 1.0.0 „Base“\n")
        self.write(self.wt, "tool.py", "print('hello')\n")
        self.commit(self.wt, "Generator 1.0.1 – Base: neues Werkzeug")
        self.lines = []

    def write(self, repo, name, text):
        path = os.path.join(repo, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def commit(self, repo, message):
        sh(repo, "add", "-A")
        sh(repo, "commit", "-q", "-m", message)
        return sh(repo, "rev-parse", "HEAD")

    def release(self, version="1.0.1", apply=False, repo=None, **kw):
        self.lines = []
        r = release.Release(version, repo or self.wt, kw.pop("branch", None), "origin", kw.pop("message", None),
                            out=self.lines.append)
        return r.run(apply), r

    def text(self):
        return "\n".join(self.lines)

    def remote_ref(self, ref):
        return subprocess.run(["git", "-C", self.remote, "rev-parse", "--verify", "--quiet", ref],
                              capture_output=True, text=True).stdout.strip()


class DryRun(Repo):
    def test_dry_run_changes_nothing(self):
        before = (sh(self.main, "rev-parse", "main"), self.remote_ref("refs/heads/main"))
        code, r = self.release()
        self.assertEqual(code, 0, self.text())
        self.assertIn("dry run: nothing changed", self.text())
        self.assertIn("git merge --ff-only", self.text())
        self.assertIn("git push --atomic origin", self.text())
        push = [line for line in self.lines if line.strip().startswith("git push")]
        self.assertEqual(len(push), 1)
        self.assertNotIn("--force", push[0])
        self.assertNotIn("+refs", push[0])
        self.assertEqual(r.message, "Generator 1.0.1 – Base: neues Werkzeug")    # newest subject naming the version
        self.assertEqual((sh(self.main, "rev-parse", "main"), self.remote_ref("refs/heads/main")), before)
        self.assertEqual(sh(self.main, "tag"), "")

    def test_bad_version_and_missing_changelog(self):
        self.assertEqual(self.release("1.0")[0], 1)
        code, _ = self.release("1.0.2", message="x")
        self.assertEqual(code, 1)
        self.assertIn("FAIL CHANGELOG.md has a section ## 1.0.2", self.text())

    def test_no_message_without_a_subject_naming_the_version(self):
        self.write(self.wt, "CHANGELOG.md", "## 1.0.3\n## 1.0.1\n")
        self.commit(self.wt, "Nachtrag")
        self.assertEqual(self.release("1.0.3")[0], 1)
        self.assertIn("give -m or -F", self.text())
        self.assertEqual(self.release("1.0.3", message="Generator 1.0.3")[0], 0, self.text())


class Checks(Repo):
    def test_dirty_checkouts(self):
        self.write(self.wt, "scratch.txt", "x")
        self.assertEqual(self.release()[0], 1)
        self.assertIn("FAIL release checkout", self.text())
        os.remove(os.path.join(self.wt, "scratch.txt"))
        self.write(self.main, "CHANGELOG.md", "edited")
        self.assertEqual(self.release()[0], 1)
        self.assertIn("FAIL main checkout %s is clean" % self.main, self.text())
        sh(self.main, "checkout", "--", "CHANGELOG.md")
        self.write(self.main, "output/big.bin", "ignored output")          # ignored files do not count
        self.assertEqual(self.release()[0], 0, self.text())

    def test_fast_forward_only(self):
        self.write(self.main, "other.txt", "main moved")
        self.commit(self.main, "anderer Stand auf main")
        self.assertEqual(self.release(apply=True)[0], 1)
        self.assertIn("merge main into the branch first", self.text())
        self.assertIn("nothing changed", self.text())

    def test_remote_moved(self):
        other = os.path.join(self.tmp, "other")
        sh(self.tmp, "clone", self.remote, other)
        self.write(other, "x.txt", "pushed elsewhere")
        self.commit(other, "woanders gepusht")
        sh(other, "push", "origin", "main")
        self.assertEqual(self.release(apply=True)[0], 1)
        self.assertIn("has commits this repository lacks", self.text())
        sh(self.main, "fetch", "origin")                       # known now, but still not in the branch
        self.assertEqual(self.release(apply=True)[0], 1)
        self.assertIn("FAIL fast-forward: claude/next-1.0.1 contains origin/main", self.text())

    def test_tag_or_release_branch_on_the_remote(self):
        sh(self.main, "tag", "-a", "v1.0.1", "-m", "old", "main")
        sh(self.main, "push", "origin", "v1.0.1")
        sh(self.main, "tag", "-d", "v1.0.1")
        self.assertEqual(self.release()[0], 1)
        self.assertIn("FAIL tag v1.0.1 is not on origin yet (it is", self.text())

    def test_big_file(self):
        with mock.patch.object(release, "MAX_FILE_BYTES", 1000), mock.patch.object(release, "WARN_FILE_BYTES", 500):
            self.write(self.wt, "data/medium.bin", "m" * 700)
            self.write(self.wt, "data/huge.bin", "h" * 5000)
            self.commit(self.wt, "Generator 1.0.1 – große Dateien")
            self.assertEqual(self.release()[0], 1)
        self.assertIn("FAIL no file over 0 MB in the push: data/huge.bin", self.text())
        self.assertIn("WARN large file data/medium.bin", self.text())

    def test_big_file_already_on_the_remote_is_not_sent_again(self):
        with mock.patch.object(release, "MAX_FILE_BYTES", 1000), mock.patch.object(release, "WARN_FILE_BYTES", 500):
            self.assertEqual(release.big_objects(self.main, "main", ["--remotes=origin"]), [])
            self.write(self.wt, "big.bin", "b" * 5000)
            self.commit(self.wt, "Generator 1.0.1 – groß")
            self.assertEqual([p for p, _ in release.big_objects(self.main, "claude/next-1.0.1",
                                                                ["--remotes=origin"])], ["big.bin"])

    def test_credentials(self):
        self.write(self.wt, "conf.py", "TOKEN = '%s'\n" % FAKE_TOKEN)
        self.commit(self.wt, "Generator 1.0.1 – Konfiguration")
        self.assertEqual(self.release()[0], 1)
        self.assertIn("FAIL no credentials in the added lines: conf.py:1 (GitHub token)", self.text())
        self.write(self.wt, "conf.py", "TOKEN = '%s'  # release-check: allow\n" % FAKE_TOKEN)
        self.commit(self.wt, "Generator 1.0.1 – Beispielwert")
        self.assertEqual(self.release()[0], 0, self.text())

    def test_credential_files(self):
        self.write(self.wt, "deploy/id_rsa", "x\n")
        self.commit(self.wt, "Generator 1.0.1 – Schlüssel")
        self.assertEqual(self.release()[0], 1)
        self.assertIn("FAIL no credential files added: deploy/id_rsa", self.text())


class Scanner(unittest.TestCase):
    def test_patterns(self):
        diff = ("+++ b/a.txt\n@@ -0,0 +1,4 @@\n+fine line\n+%s\n+password = \"hunter2hunter2\"\n"
                "+password = os.environ['PW']\n+++ b/b.py\n@@ -3,0 +10 @@\n+key = 'AKIA%s'\n"
                % (FAKE_KEY, "ABCDEFGHIJKLMNOP"))
        self.assertEqual(release.scan_secrets(diff), [("a.txt", 2, "private key"),
                                                      ("a.txt", 3, "password/secret assignment"),
                                                      ("b.py", 10, "AWS access key")])

    def test_the_release_tool_itself_is_clean(self):
        with open(release.__file__, encoding="utf-8") as fh:
            src = fh.read()
        diff = "+++ b/scripts/release.py\n@@ -0,0 +1 @@\n" + "".join("+" + line + "\n" for line in src.splitlines())
        self.assertEqual(release.scan_secrets(diff), [])

    def test_web_links(self):
        for url in ("https://github.com/ffunkfabian-hash/cubeescape-robogen", "git@github.com:o/r.git",
                    "https://github.com/o/r.git", "ssh://git@github.com/o/r"):
            self.assertTrue(release.web_base(url).startswith("https://github.com/"), url)
        self.assertEqual(release.web_base("git@github.com:o/r.git"), "https://github.com/o/r")
        self.assertIsNone(release.web_base("/tmp/remote.git"))


class Apply(Repo):
    def test_release_from_the_worktree(self):
        target = sh(self.wt, "rev-parse", "HEAD")
        code, _ = self.release(apply=True)
        self.assertEqual(code, 0, self.text())
        self.assertEqual(sh(self.main, "rev-parse", "main"), target)                       # fast-forward
        self.assertEqual(self.remote_ref("refs/heads/main"), target)
        self.assertEqual(self.remote_ref("refs/heads/release/1.0.1"), target)
        self.assertEqual(self.remote_ref("refs/tags/v1.0.1^{commit}"), target)
        self.assertEqual(sh(self.remote, "cat-file", "-t", "v1.0.1"), "tag")               # annotated
        self.assertEqual(sh(self.remote, "tag", "-l", "--format=%(contents:subject)", "v1.0.1"),
                         "Generator 1.0.1 – Base: neues Werkzeug")
        self.assertEqual(sh(self.main, "log", "--merges", "--oneline"), "")                # no merge commit
        self.assertIn("released v1.0.1", self.text())
        self.assertIn("worktree remove", self.text())

    def test_message_and_resume(self):
        target = sh(self.wt, "rev-parse", "HEAD")
        # an interrupted earlier attempt: merged and tagged, nothing pushed
        sh(self.main, "merge", "--ff-only", "claude/next-1.0.1")
        sh(self.main, "tag", "-a", "v1.0.1", "-m", "Generator 1.0.1 – eigener Text", target)
        code, _ = self.release(apply=True, message="ignored, the tag exists")
        self.assertEqual(code, 0, self.text())
        self.assertIn("local tag v1.0.1 already exists on the release commit", self.text())
        self.assertEqual(self.remote_ref("refs/tags/v1.0.1^{commit}"), target)
        self.assertEqual(sh(self.remote, "tag", "-l", "--format=%(contents:subject)", "v1.0.1"),
                         "Generator 1.0.1 – eigener Text")
        # a second run finds everything on the remote and refuses a new tag
        self.assertEqual(self.release("1.0.1", apply=True)[0], 0, self.text())

    def test_release_of_main_itself(self):
        sh(self.main, "merge", "--ff-only", "claude/next-1.0.1")
        code, _ = self.release(apply=True, repo=self.main)
        self.assertEqual(code, 0, self.text())
        self.assertNotIn("merge --ff-only", self.text())
        self.assertNotIn("worktree remove", self.text())
        self.assertTrue(self.remote_ref("refs/tags/v1.0.1"))

    def test_rejected_push_leaves_a_resumable_state(self):
        hook = os.path.join(self.remote, "hooks", "pre-receive")
        with open(hook, "w") as fh:
            fh.write("#!/bin/sh\necho rejected by test >&2\nexit 1\n")
        os.chmod(hook, 0o755)
        self.assertEqual(self.release(apply=True)[0], 1)
        self.assertIn("FAILED at step 4", self.text())
        self.assertEqual(self.remote_ref("refs/heads/main"), sh(self.main, "rev-parse", "HEAD~1"))   # atomic
        self.assertFalse(self.remote_ref("refs/tags/v1.0.1"))
        os.remove(hook)
        self.assertEqual(self.release(apply=True)[0], 0, self.text())
        self.assertEqual(self.remote_ref("refs/heads/release/1.0.1"), sh(self.main, "rev-parse", "HEAD"))


if __name__ == "__main__":
    unittest.main()
