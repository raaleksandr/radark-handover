"""Test helpers: temporary hubs and in-process CLI runs."""

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import ho  # noqa: E402

TEAMS = "strategy=Strategy,product=Product,frontend=Frontend,backend=Backend,mobile=Mobile"


class Result:
    def __init__(self, code, out, err):
        self.code, self.out, self.err = code, out, err

    @property
    def json(self):
        return json.loads(self.out)

    def __repr__(self):
        return f"Result(code={self.code}, out={self.out!r}, err={self.err!r})"


def run_ho(*args, cwd, env=None, stdin=None):
    """Run the CLI in-process; stdin may be str or bytes."""
    if isinstance(stdin, str):
        stdin = stdin.encode("utf-8")
    out, err = io.StringIO(), io.StringIO()
    code = ho.main(
        [str(a) for a in args],
        stdin=io.BytesIO(stdin or b""),
        stdout=out,
        stderr=err,
        env=env if env is not None else clean_env(),
        cwd=cwd,
    )
    return Result(code, out.getvalue(), err.getvalue())


def clean_env():
    return {k: v for k, v in os.environ.items() if not k.startswith("HO_")}


class HubTestCase(unittest.TestCase):
    """Each test gets a fresh hub at self.hub with the standard five teams."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ho-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.hub = self.tmp / "hub"
        r = run_ho("init", self.hub, "--teams", TEAMS, cwd=self.tmp)
        self.assertEqual(r.code, 0, r)

    def ho(self, *args, team=None, stdin=None, cwd=None, env=None):
        argv = list(args)
        if team:
            argv += ["--team", team]
        return run_ho(*argv, cwd=cwd or self.hub, env=env, stdin=stdin)

    def ok(self, *args, **kw):
        r = self.ho(*args, **kw)
        self.assertEqual(r.code, 0, r)
        return r

    def new(self, sender="product", to="backend", expects="done", title="Task", body="# Do it\n", extra=()):
        r = self.ok("new", "--to", to, "--expects", expects, "--title", title, "-m", body, "--json", *extra,
                    team=sender)
        return r.json["id"]

    def show(self, hid, team=None):
        return self.ok("show", hid, "--json", team=team).json

    def inbox(self, team):
        return self.ok("inbox", "--json", team=team).json

    def ids(self, items):
        return [i["id"] for i in items]

    def snapshot(self):
        """Map of every file under the hub to its bytes (to prove failures change nothing).

        The hub lock file .ho.lock is left out: it carries no data, and a writing command creates it
        before it knows whether it will succeed (hub-lock spec).
        """
        return {
            p.relative_to(self.hub).as_posix(): p.read_bytes()
            for p in sorted(self.hub.rglob("*"))
            if p.is_file() and p.relative_to(self.hub).as_posix() != ho.LOCK_NAME
        } | {
            p.relative_to(self.hub).as_posix() + "/": b""
            for p in sorted(self.hub.rglob("*"))
            if p.is_dir()
        }

    def assertUnchanged(self, before, msg=None):
        self.assertEqual(self.snapshot(), before, msg)

    def handover_dir(self, hid):
        hits = list(self.hub.glob(f"active/{hid}")) + list(self.hub.glob(f"archive/*/{hid}"))
        self.assertEqual(len(hits), 1, hits)
        return hits[0]

    def event_files(self, hid):
        return sorted(p.name for p in (self.handover_dir(hid) / "events").glob("*.md"))
