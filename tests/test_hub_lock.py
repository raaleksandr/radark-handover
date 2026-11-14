"""Hub lock: writing commands and sync serialise on <hub>/.ho.lock (spec hub-lock).

The unit tests run in-process; the acceptance tests use real child processes (`python ho.py ...`) and a
small lock-holder process that signals through files, so they do not depend on sleeps for ordering.
"""

import io
import json
import os
import shutil
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

from helpers import ROOT, TEAMS, HubTestCase, clean_env, ho, run_ho

HO_SCRIPT = ROOT / "ho.py"

# Takes the hub lock, writes the "ready" file, then holds the lock until the "release" file
# appears or `hold` seconds have passed (or until it is killed).
HOLDER = r"""
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import ho
hub, ready, release, hold = Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]), float(sys.argv[5])
with ho.hub_lock(hub, {"HO_LOCK_TIMEOUT": "0"}, sys.stderr):
    ready.write_text("held", encoding="utf-8")
    deadline = time.monotonic() + hold
    while not release.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
"""

WRITE_ARGS = {
    "new": lambda hid: ["new", "--to", "backend", "--expects", "done", "--title", "T", "-m", "x"],
    "ack": lambda hid: ["ack", hid],
    "done": lambda hid: ["done", hid, "-m", "x"],
    "respond": lambda hid: ["respond", hid, "-m", "x"],
    "decline": lambda hid: ["decline", hid, "-m", "x"],
    "comment": lambda hid: ["comment", hid, "-m", "x"],
    "seen": lambda hid: ["seen", hid],
    "return": lambda hid: ["return", hid, "--to", "backend", "-m", "x"],
    "close": lambda hid: ["close", hid, "--force", "-m", "x"],
    "sync": lambda hid: ["sync"],
}
TEAM_FOR = {"new": "product", "return": "product", "close": "product", "sync": None}


class LockTimeoutSetting(unittest.TestCase):
    def test_default_and_values(self):
        self.assertEqual(ho.lock_timeout({}), 300.0)
        self.assertEqual(ho.lock_timeout({"HO_LOCK_TIMEOUT": ""}), 300.0)
        self.assertEqual(ho.lock_timeout({"HO_LOCK_TIMEOUT": "0"}), 0.0)
        self.assertEqual(ho.lock_timeout({"HO_LOCK_TIMEOUT": " 2.5 "}), 2.5)

    def test_invalid_values(self):
        for value in ("soon", "-1", "nan", "inf", "1s"):
            with self.subTest(value=value):
                with self.assertRaises(ho.HoError) as cm:
                    ho.lock_timeout({"HO_LOCK_TIMEOUT": value})
                self.assertEqual(cm.exception.code, "usage")
                self.assertEqual(cm.exception.exit_code, 1)


class LockPrimitive(HubTestCase):
    def test_second_acquisition_fails_while_held_and_succeeds_after(self):
        env0 = {"HO_LOCK_TIMEOUT": "0"}
        with ho.hub_lock(self.hub, env0, io.StringIO()):
            with self.assertRaises(ho.HoError) as cm:
                with ho.hub_lock(self.hub, env0, io.StringIO()):
                    pass
            self.assertEqual(cm.exception.code, "hub_locked")
            self.assertEqual(cm.exception.exit_code, 1)
            self.assertIn(".ho.lock", cm.exception.message)
            self.assertIn("0 s", cm.exception.message)
        with ho.hub_lock(self.hub, env0, io.StringIO()):
            pass
        self.assertTrue((self.hub / ".ho.lock").is_file())  # never deleted

    def test_waiting_note_goes_to_stderr(self):
        err = io.StringIO()
        with ho.hub_lock(self.hub, {"HO_LOCK_TIMEOUT": "0"}, io.StringIO()):
            with self.assertRaises(ho.HoError):
                with ho.hub_lock(self.hub, {"HO_LOCK_TIMEOUT": "0.3"}, err):
                    pass
        self.assertEqual(err.getvalue().count("waiting up to 0.3 s"), 1)


class GitignoreLine(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.root = Path(tempfile.mkdtemp(prefix="ho-ign-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.path = self.root / ".gitignore"

    def test_missing_file_is_created(self):
        self.assertTrue(ho.ensure_gitignore(self.root))
        self.assertEqual(self.path.read_bytes(), b".ho.lock\n")

    def test_line_appended_keeping_content(self):
        self.path.write_bytes(b"*.tmp\nnotes")
        self.assertTrue(ho.ensure_gitignore(self.root))
        self.assertEqual(self.path.read_bytes(), b"*.tmp\nnotes\n.ho.lock\n")

    def test_existing_line_leaves_bytes_unchanged(self):
        self.path.write_bytes(b"a\r\n.ho.lock\r\nb")
        self.assertFalse(ho.ensure_gitignore(self.root))
        self.assertEqual(self.path.read_bytes(), b"a\r\n.ho.lock\r\nb")


class LockedCommands(HubTestCase):
    """In-process: with the lock held, every writing command and sync fails; readers do not wait."""

    def test_writers_fail_with_hub_locked_and_readers_succeed(self):
        hid = self.new(to="backend", expects="done")
        env = clean_env() | {"HO_LOCK_TIMEOUT": "0"}
        before = self.snapshot()
        with ho.hub_lock(self.hub, env, io.StringIO()):
            for name, build in WRITE_ARGS.items():
                with self.subTest(command=name):
                    team = TEAM_FOR.get(name, "backend")
                    argv = build(hid) + ["--json"] + (["--team", team] if team else [])
                    r = run_ho(*argv, cwd=self.hub, env=env)
                    self.assertEqual(r.code, 1, r)
                    self.assertEqual(r.json, {"schema_version": 1, "error": {
                        "code": "hub_locked", "message": r.json["error"]["message"]}})
                    self.assertUnchanged(before)
            for argv in (["teams"], ["inbox", "--team", "backend"], ["list"], ["show", hid], ["validate"]):
                with self.subTest(command=argv[0]):
                    r = run_ho(*argv, "--json", cwd=self.hub, env=env)
                    self.assertEqual(r.code, 0, r)
            r = run_ho("--version", "--json", cwd=self.hub, env=env)
            self.assertEqual(r.code, 0, r)
        self.assertUnchanged(before)

    def test_invalid_bound_is_usage_error(self):
        before = self.snapshot()
        env = clean_env() | {"HO_LOCK_TIMEOUT": "soon"}
        r = run_ho("sync", "--json", cwd=self.hub, env=env)
        self.assertEqual(r.code, 1, r)
        self.assertEqual(r.json["error"]["code"], "usage")
        self.assertIn("HO_LOCK_TIMEOUT", r.json["error"]["message"])
        self.assertUnchanged(before)

    def test_existing_hub_gets_the_ignore_line(self):
        hid = self.new(to="backend", expects="done")
        (self.hub / ".gitignore").unlink()
        self.ok("ack", hid, team="backend")
        self.assertIn(".ho.lock", (self.hub / ".gitignore").read_text(encoding="utf-8").splitlines())

    def test_failed_command_does_not_add_the_ignore_line(self):
        hid = self.new(to="backend", expects="done")
        (self.hub / ".gitignore").unlink()
        before = self.snapshot()
        r = self.ho("close", hid, "--json", team="product")  # open handover without --force
        self.assertNotEqual(r.code, 0, r)
        self.assertUnchanged(before)

    def test_readers_do_not_create_the_lock_file(self):
        (self.hub / ".ho.lock").unlink(missing_ok=True)
        for argv in (["teams"], ["inbox", "--team", "backend"], ["list"], ["validate"]):
            self.ok(*argv)
        self.assertFalse((self.hub / ".ho.lock").exists())


def git_env(tmp: Path) -> dict:
    empty_config = tmp / "gitconfig"
    empty_config.write_text("", encoding="utf-8")
    return clean_env() | {
        "GIT_CONFIG_GLOBAL": str(empty_config),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "ho test", "GIT_AUTHOR_EMAIL": "ho@example.com",
        "GIT_COMMITTER_NAME": "ho test", "GIT_COMMITTER_EMAIL": "ho@example.com",
        "GIT_CEILING_DIRECTORIES": str(tmp),
    }


@unittest.skipUnless(shutil.which("git"), "git is not installed")
class LockFileNotVersioned(HubTestCase):
    def setUp(self):
        super().setUp()
        self.env = git_env(self.tmp)
        self.git("init", "-q", "-b", "main", ".")

    def git(self, *args):
        proc = subprocess.run(["git", *args], cwd=self.hub, env=self.env, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout

    def test_lock_file_never_shows_as_a_change(self):
        self.assertEqual(run_ho("sync", cwd=self.hub, env=self.env).code, 0)
        hid = self.new(to="backend", expects="done")
        self.assertTrue((self.hub / ".ho.lock").is_file())
        self.assertNotIn(".ho.lock", self.git("status", "--porcelain", "--ignored=no", "-uall"))
        self.assertEqual(run_ho("sync", cwd=self.hub, env=self.env).code, 0)
        files = self.git("ls-files").splitlines()
        self.assertIn(".gitignore", files)
        self.assertTrue(any(hid in f for f in files))
        self.assertNotIn(".ho.lock", files)
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_sync_excludes_lock_file_without_ignore_line(self):
        (self.hub / ".gitignore").write_text("*.tmp\n", encoding="utf-8")
        (self.hub / ".ho.lock").write_bytes(b"")
        with mock.patch.object(ho, "ensure_gitignore", return_value=False):
            r = run_ho("sync", "--json", cwd=self.hub, env=self.env)
        self.assertEqual(r.code, 0, r)
        self.assertTrue(r.json["committed"])
        files = self.git("ls-files").splitlines()
        self.assertIn("hub.json", files)
        self.assertNotIn(".ho.lock", files)


class MultiProcess(HubTestCase):
    """Acceptance tests with real child processes."""

    def setUp(self):
        super().setUp()
        self.env = clean_env()
        self.holder = None
        self.sig = self.tmp / "sig"
        self.sig.mkdir()

    def hold(self, seconds=120.0):
        """Start a lock holder; returns once it holds the lock."""
        ready, release = self.sig / "ready", self.sig / "release"
        ready.unlink(missing_ok=True)
        release.unlink(missing_ok=True)
        self.holder = subprocess.Popen(
            [sys.executable, "-c", HOLDER, str(ROOT), str(self.hub), str(ready), str(release), str(seconds)],
            env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.addCleanup(self._stop_holder, self.holder)
        deadline = time.monotonic() + 60
        while not ready.exists():
            if self.holder.poll() is not None:
                self.fail(f"lock holder exited early: {self.holder.communicate()}")
            if time.monotonic() > deadline:
                self.fail("lock holder did not take the lock within 60 s")
            time.sleep(0.02)
        return release

    @staticmethod
    def _stop_holder(proc):
        if proc.poll() is None:
            proc.kill()
        proc.communicate()

    def start(self, *args, team=None, timeout="60", cwd=None):
        argv = [sys.executable, str(HO_SCRIPT), *args] + (["--team", team] if team else [])
        return subprocess.Popen(
            argv, cwd=cwd or self.hub, env=self.env | {"HO_LOCK_TIMEOUT": timeout},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
        )

    def finish(self, proc, limit=120):
        out, err = proc.communicate(timeout=limit)
        return proc.returncode, out, err

    def test_two_concurrent_new_both_succeed(self):
        release = self.hold()
        procs = [self.start("new", "--to", "backend", "--expects", "done", "--title", f"T{i}", "-m", "x",
                            "--json", team="product") for i in range(2)]
        time.sleep(0.5)  # give both the chance to reach the lock; ordering does not depend on it
        self.assertTrue(all(p.poll() is None for p in procs), "writers must wait while the lock is held")
        release.write_text("go", encoding="utf-8")
        ids = []
        for p in procs:
            code, out, err = self.finish(p)
            self.assertEqual(code, 0, (out, err))
            ids.append(json.loads(out)["id"])
            self.assertIn("waiting up to 60 s", err)
        self.assertEqual(len(set(ids)), 2)
        listed = self.ok("list", "--json").json["items"]
        self.assertEqual(sorted(i["id"] for i in listed), sorted(ids))
        self.assertEqual(self.ok("validate", "--json").json["ok"], True)

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_concurrent_sync_and_new_on_git_hub(self):
        self.env = git_env(self.tmp)
        subprocess.run(["git", "init", "-q", "-b", "main", "."], cwd=self.hub, env=self.env, check=True)
        release = self.hold()
        sync = self.start("sync", "--json")
        new = self.start("new", "--to", "backend", "--expects", "ack", "--title", "T", "-m", "x", "--json",
                         team="product")
        release.write_text("go", encoding="utf-8")
        code, out, err = self.finish(sync)
        self.assertEqual(code, 0, (out, err))
        code, out, err = self.finish(new)
        self.assertEqual(code, 0, (out, err))
        hid = json.loads(out)["id"]
        code, out, err = self.finish(self.start("sync", "--json"))
        self.assertEqual(code, 0, (out, err))
        files = subprocess.run(["git", "ls-files"], cwd=self.hub, env=self.env, capture_output=True,
                               text=True, check=True).stdout.splitlines()
        self.assertTrue(any(f.startswith(f"active/{hid}/") for f in files), files)
        self.assertNotIn(".ho.lock", files)
        status = subprocess.run(["git", "status", "--porcelain"], cwd=self.hub, env=self.env,
                                capture_output=True, text=True, check=True).stdout
        self.assertEqual(status, "")

    def test_reader_does_not_wait_and_writer_times_out(self):
        hid = self.new(to="backend", expects="done")
        before = self.snapshot()
        self.hold()
        started = time.monotonic()
        code, out, err = self.finish(self.start("inbox", "--json", team="backend", timeout="0"))
        self.assertEqual(code, 0, (out, err))
        self.assertEqual([i["id"] for i in json.loads(out)["action_required"]], [hid])
        self.assertNotIn("waiting", err)
        self.assertLess(time.monotonic() - started, 30)
        code, out, err = self.finish(self.start("done", hid, "-m", "x", "--json", team="backend", timeout="1"))
        self.assertEqual(code, 1, (out, err))
        data = json.loads(out)
        self.assertEqual(set(data), {"schema_version", "error"})
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["error"]["code"], "hub_locked")
        self.assertIn(".ho.lock", data["error"]["message"])
        self.assertUnchanged(before)

    def test_lock_released_in_time(self):
        hid = self.new(to="backend", expects="done")
        self.hold(seconds=2)
        code, out, err = self.finish(self.start("done", hid, "-m", "x", "--json", team="backend", timeout="30"))
        self.assertEqual(code, 0, (out, err))
        self.assertIn("waiting up to 30 s", err)
        self.assertEqual(self.show(hid, team="backend")["recipients"], {"backend": "done"})

    def test_killed_holder_leaves_no_lock(self):
        self.hold()
        self.holder.kill()
        self.holder.communicate()
        self.assertTrue((self.hub / ".ho.lock").exists())
        code, out, err = self.finish(self.start("new", "--to", "backend", "--expects", "ack", "--title", "T",
                                                "-m", "x", "--json", team="product", timeout="0"))
        self.assertEqual(code, 0, (out, err))


if __name__ == "__main__":
    unittest.main()
