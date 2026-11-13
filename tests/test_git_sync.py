"""Git sync: sharing handovers between clones, and concurrent responses merging cleanly."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from helpers import TEAMS, clean_env, run_ho


@unittest.skipUnless(shutil.which("git"), "git is not installed")
class GitSync(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ho-git-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        empty_config = self.tmp / "gitconfig"
        empty_config.write_text("", encoding="utf-8")
        self.env = clean_env() | {
            "GIT_CONFIG_GLOBAL": str(empty_config),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "ho test", "GIT_AUTHOR_EMAIL": "ho@example.com",
            "GIT_COMMITTER_NAME": "ho test", "GIT_COMMITTER_EMAIL": "ho@example.com",
            "GIT_CEILING_DIRECTORIES": str(self.tmp),
        }
        self.remote = self.tmp / "remote.git"
        self.git(self.tmp, "init", "--bare", "-b", "main", str(self.remote))
        seed = self.tmp / "seed"
        self.git(self.tmp, "init", "-b", "main", str(seed))
        self.assertEqual(run_ho("init", seed, "--teams", TEAMS, cwd=self.tmp, env=self.env).code, 0)
        self.git(seed, "remote", "add", "origin", str(self.remote))
        r = self.ho(seed, "sync", "--json")  # no upstream yet: commits and pushes with -u
        self.assertEqual(r.code, 0, r)
        self.assertTrue(r.json["committed"] and r.json["pushed"])

    def git(self, cwd, *args):
        proc = subprocess.run(["git", *args], cwd=cwd, env=self.env, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout

    def clone(self, name):
        self.git(self.tmp, "clone", str(self.remote), name)
        return self.tmp / name

    def ho(self, cwd, *args, team=None):
        argv = list(args) + (["--team", team] if team else [])
        return run_ho(*argv, cwd=cwd, env=self.env)

    def test_share_a_new_handover(self):
        product, backend = self.clone("product"), self.clone("backend")
        r = self.ho(product, "new", "--to", "backend", "--expects", "done", "--title", "Ship it", "-m", "# Go",
                    "--json", team="product")
        hid = r.json["id"]
        r = self.ho(product, "sync", "--json")
        self.assertEqual(r.code, 0, r)
        self.assertEqual(r.json["commit_message"], f"ho: product new {hid}")
        self.assertEqual(self.ho(backend, "inbox", "--json", team="backend").json["action_required"], [])
        self.assertEqual(self.ho(backend, "sync").code, 0)
        inbox = self.ho(backend, "inbox", "--json", team="backend").json
        self.assertEqual([i["id"] for i in inbox["action_required"]], [hid])

    def test_concurrent_responses_merge_without_conflict(self):
        product, backend, frontend = self.clone("product"), self.clone("backend"), self.clone("frontend")
        hid = self.ho(product, "new", "--to", "backend,frontend", "--expects", "response", "--title", "Q",
                      "-m", "Question?", "--json", team="product").json["id"]
        self.ho(product, "sync")
        self.ho(backend, "sync")
        self.ho(frontend, "sync")
        # both answer "at the same time" on their own machines
        self.assertEqual(self.ho(backend, "respond", hid, "-m", "Backend answer", team="backend").code, 0)
        self.assertEqual(self.ho(frontend, "respond", hid, "-m", "Frontend answer", team="frontend").code, 0)
        r = self.ho(backend, "sync", "--json")
        self.assertEqual(r.code, 0, r)
        self.assertEqual(r.json["commit_message"], f"ho: backend respond {hid}")
        r = self.ho(frontend, "sync", "--json")  # pulls backend's commit with rebase, then pushes
        self.assertEqual(r.code, 0, r)
        self.assertTrue(r.json["pulled"] and r.json["pushed"])
        self.assertEqual(self.ho(product, "sync").code, 0)
        events = sorted(p.name for p in (product / "active" / hid / "events").iterdir())
        self.assertEqual([e.split("--", 1)[1] for e in events], ["backend--respond.md", "frontend--respond.md"])
        shown = self.ho(product, "show", hid, "--json").json
        self.assertEqual(shown["recipients"], {"backend": "answered", "frontend": "answered"})
        self.assertEqual({e["body"] for e in shown["events"]}, {"Backend answer\n", "Frontend answer\n"})
        inbox = self.ho(product, "inbox", "--json", team="product").json
        self.assertEqual([i["id"] for i in inbox["to_review"]], [hid])
        self.assertEqual(self.ho(product, "validate").code, 0)
        # closing archives the handover and the move is shared too
        self.ho(product, "close", hid, team="product")
        r = self.ho(product, "sync", "--json")
        self.assertEqual(r.json["commit_message"], f"ho: product close {hid}")
        self.ho(backend, "sync")
        self.assertFalse((backend / "active" / hid).exists())
        self.assertEqual(self.ho(backend, "show", hid, "--json").json["status"], "closed")

    def test_sync_with_uncommitted_local_changes(self):
        product, backend = self.clone("product"), self.clone("backend")
        hid = self.ho(product, "new", "--to", "backend", "--expects", "ack", "--title", "A", "-m", "x",
                      "--json", team="product").json["id"]
        self.ho(product, "sync")
        hid2 = self.ho(backend, "new", "--to", "product", "--expects", "ack", "--title", "B", "-m", "y",
                       "--json", team="backend").json["id"]
        r = self.ho(backend, "sync", "--json")  # local uncommitted handover + remote commit
        self.assertEqual(r.code, 0, r)
        self.assertTrue((backend / "active" / hid).is_dir())
        self.assertIn(f"backend new {hid2}", r.json["commit_message"])
        r = self.ho(backend, "sync", "--json")
        self.assertFalse(r.json["committed"])


if __name__ == "__main__":
    unittest.main()
