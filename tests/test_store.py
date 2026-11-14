"""Scenarios from specs/handover-store/spec.md plus store internals."""

import json
import re
import shutil
from unittest import mock

from helpers import HubTestCase, ho, run_ho


class Store(HubTestCase):
    # -- Requirement: Hub layout --------------------------------------------------
    def test_freshly_initialized_hub(self):
        entries = sorted(p.name for p in self.hub.iterdir())
        self.assertEqual(entries, [".gitignore", "AGENTS.md", "active", "archive", "hub.json"])
        self.assertIn(".ho.lock", (self.hub / ".gitignore").read_text(encoding="utf-8").splitlines())
        self.assertEqual(list((self.hub / "active").iterdir()), [])
        self.assertEqual(list((self.hub / "archive").iterdir()), [])
        config = json.loads((self.hub / "hub.json").read_text(encoding="utf-8"))
        self.assertEqual([t["id"] for t in config["teams"]], ["strategy", "product", "frontend", "backend", "mobile"])
        self.assertEqual(config["teams"][1]["name"], "Product")

    def test_init_refuses_existing_hub(self):
        before = self.snapshot()
        r = run_ho("init", self.hub, "--teams", "x=X", "--json", cwd=self.tmp)
        self.assertEqual(r.code, 1)
        self.assertEqual(r.json["error"]["code"], "hub_exists")
        self.assertUnchanged(before)

    def test_handover_exists_in_exactly_one_place(self):
        hid = self.new(to="backend", expects="ack")
        self.ok("ack", hid, team="backend")
        self.ok("close", hid, team="product")
        archived = self.hub / "archive" / hid[3:7] / hid
        shutil.copytree(archived, self.hub / "active" / hid)
        (self.hub / "active" / hid / "events" / "29990101T000000.000Z--backend--comment.md").write_text(
            "---\nat: 2999-01-01T00:00:00.000Z\nteam: backend\naction: comment\n---\n\nlate\n", encoding="utf-8")
        r = self.ho("validate", "--json")
        self.assertEqual(r.code, 4)
        dup = [e for e in r.json["errors"] if "duplicate ID" in e["message"]]
        self.assertEqual(len(dup), 1)
        self.assertEqual(dup[0]["path"], str(self.hub / "active" / hid))
        self.assertIn("29990101T000000.000Z--backend--comment.md", dup[0]["message"])
        r = self.ho("show", hid, "--json")
        self.assertEqual((r.code, r.json["error"]["code"]), (4, "duplicate_id"))

    # -- Requirement: Team registry -----------------------------------------------
    def test_unknown_team_rejected(self):
        before = self.snapshot()
        r = self.ho("new", "--to", "ios", "--expects", "ack", "--title", "x", "-m", "b", "--json", team="product")
        self.assertEqual(r.code, 2)
        self.assertEqual(r.json["error"]["code"], "unknown_team")
        msg = r.json["error"]["message"]
        self.assertIn("'ios'", msg)
        self.assertIn("strategy, product, frontend, backend, mobile", msg)
        self.assertUnchanged(before)
        self.assertEqual(self.ho("inbox", team="ios").code, 2)

    def test_invalid_hub_json(self):
        (self.hub / "hub.json").write_text('{"teams": [{"id": "Bad Team"}]}', encoding="utf-8")
        r = self.ho("teams", "--json")
        self.assertEqual((r.code, r.json["error"]["code"]), (4, "invalid_hub"))

    # -- Requirement: Handover document format ------------------------------------
    def test_valid_document(self):
        body = "# Checkout\n\n- item\n\nТекст ✓\n"
        hid = self.new(to="backend,frontend", expects="response", body=body,
                       extra=["--priority", "high", "--due", "2026-10-31", "--tag", "web,q4"])
        text = (self.hub / "active" / hid / "handover.md").read_text(encoding="utf-8")
        meta, parsed = ho.parse_document(text)
        self.assertEqual(meta["from"], "product")
        self.assertEqual(meta["to"], ["backend", "frontend"])
        self.assertEqual(meta["expects"], "response")
        self.assertEqual(meta["priority"], "high")
        self.assertEqual(meta["due"], "2026-10-31")
        self.assertEqual(meta["tags"], ["web", "q4"])
        self.assertRegex(meta["created"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")
        self.assertIn("to: [backend, frontend]\n", text)
        self.assertEqual(parsed, body)

    def test_empty_body_rejected(self):
        before = self.snapshot()
        for body in ("", "   \n\t\n"):
            r = self.ho("new", "--to", "backend", "--expects", "ack", "--title", "x", "--body", "-", "--json",
                        team="product", stdin=body)
            self.assertEqual(r.code, 1)
            self.assertEqual(r.json["error"]["code"], "empty_body")
        self.assertUnchanged(before)

    def test_sender_cannot_be_recipient(self):
        r = self.ho("new", "--to", "product,backend", "--expects", "ack", "--title", "x", "-m", "b", team="product")
        self.assertEqual(r.code, 1)

    # -- Requirement: Event log ---------------------------------------------------
    def test_events_are_separate_immutable_files(self):
        hid = self.new(to="backend,frontend", expects="response")
        self.ok("respond", hid, "-m", "A", team="backend")
        first = (self.handover_dir(hid) / "events").iterdir().__next__()
        content = first.read_bytes()
        self.ok("respond", hid, "-m", "B", team="frontend")
        self.ok("comment", hid, "-m", "C", team="product")
        self.assertEqual(first.read_bytes(), content)
        names = self.event_files(hid)
        self.assertEqual(len(names), 3)
        for n in names:
            self.assertRegex(n, r"^\d{8}T\d{6}\.\d{3}Z--[a-z]+--(respond|comment)\.md$")
        self.assertFalse([p for p in self.hub.rglob(".tmp-*")], "no temp files left behind")
        ev = ho.parse_document((self.handover_dir(hid) / "events" / names[0]).read_text(encoding="utf-8"))[0]
        self.assertEqual(set(ev), {"at", "team", "action"})
        self.assertRegex(ev["at"], r"\.\d{3}Z$")

    def test_return_event_has_target(self):
        hid = self.new(to="backend", expects="response")
        self.ok("respond", hid, "-m", "A", team="backend")
        self.ok("return", hid, "--to", "backend", "-m", "More", team="product")
        path = self.handover_dir(hid) / "events" / self.event_files(hid)[-1]
        self.assertEqual(ho.parse_document(path.read_text(encoding="utf-8"))[0]["target"], "backend")

    def test_concurrent_events_do_not_collide(self):
        # Same-millisecond events by the same team get distinct names.
        hid = self.new(to="backend", expects="done")
        frozen = ho.dt.datetime(2026, 10, 10, 12, 0, 0, tzinfo=ho.dt.timezone.utc)
        hub = ho.Hub(self.hub)
        hv = hub.load(hid)
        with mock.patch.object(ho.Handover, "next_timestamp", return_value=ho.format_ts(frozen)):
            a = hv.write_event("backend", "comment", "one\n")
            b = hv.write_event("backend", "comment", "two\n")
        self.assertEqual(a["file"], "20261010T120000.000Z--backend--comment.md")
        self.assertEqual(b["file"], "20261010T120000.000Z--backend--comment-2.md")

    def test_chronological_ordering(self):
        hid = self.new(to="backend", expects="done")
        edir = self.handover_dir(hid) / "events"
        edir.mkdir()
        # file names deliberately sort differently from 'at'
        for name, at in (("b.md", "2999-01-01T00:00:00.300Z"), ("a.md", "2999-01-01T00:00:00.100Z"),
                         ("c.md", "2999-01-01T00:00:00.200Z")):
            (edir / name).write_text(f"---\nat: {at}\nteam: backend\naction: comment\n---\n\n{name}\n",
                                     encoding="utf-8")
        ats = [e["at"] for e in self.show(hid)["events"]]
        self.assertEqual(ats, sorted(ats))
        self.assertEqual([e["file"] for e in self.show(hid)["events"]], ["a.md", "c.md", "b.md"])

    # -- Requirement: Handover identifiers ----------------------------------------
    def test_id_format(self):
        hid = self.new()
        today = ho.utc_now().strftime("%Y%m%d")
        self.assertRegex(hid, rf"^HO-{today}-[a-z2-7]{{4}}$")
        fixed = ho.dt.datetime(2026, 10, 10, tzinfo=ho.dt.timezone.utc)
        self.assertTrue(re.match(r"^HO-20261010-[a-z2-7]{4}$", ho.Hub(self.hub).new_id(fixed)))

    def test_id_collision_retries_then_fails(self):
        hid = self.new()
        hub = ho.Hub(self.hub)
        created = ho.dt.datetime.strptime(hid[3:11], "%Y%m%d").replace(tzinfo=ho.dt.timezone.utc)
        # first attempt hits the existing ID, the retry succeeds
        with mock.patch.object(ho.secrets, "choice", side_effect=list(hid[-4:]) + list("zzzz")):
            self.assertEqual(hub.new_id(created), f"HO-{hid[3:11]}-zzzz")
        # every attempt collides -> error instead of overwriting
        (self.hub / "active" / f"HO-{hid[3:11]}-aaaa").mkdir(exist_ok=True)
        with mock.patch.object(ho.secrets, "choice", return_value="a") as m:
            with self.assertRaises(ho.HoError) as cm:
                hub.new_id(created)
        self.assertEqual(cm.exception.code, "id_collision")
        self.assertGreater(m.call_count, 4)

    # -- Requirement: Attachments -------------------------------------------------
    def test_attachment_survives_archiving(self):
        spec = self.tmp / "api.yaml"
        spec.write_text("openapi: 3.1.0\n", encoding="utf-8")
        hid = self.new(to="backend", expects="ack", extra=["--attach", str(spec)])
        self.assertTrue((self.hub / "active" / hid / "attachments" / "api.yaml").is_file())
        self.ok("ack", hid, team="backend")
        self.ok("close", hid, team="product")
        archived = self.hub / "archive" / hid[3:7] / hid / "attachments" / "api.yaml"
        self.assertEqual(archived.read_text(encoding="utf-8"), "openapi: 3.1.0\n")

    def test_missing_attachment_rejected(self):
        before = self.snapshot()
        r = self.ho("new", "--to", "backend", "--expects", "ack", "--title", "x", "-m", "b",
                    "--attach", "nope.txt", team="product")
        self.assertEqual(r.code, 1)
        self.assertUnchanged(before)

    # -- Requirement: Archiving ---------------------------------------------------
    def test_close_moves_to_archive(self):
        hid = self.new(to="backend", expects="ack")
        self.ok("ack", hid, team="backend")
        self.ok("close", hid, team="product")
        year = ho.utc_now().strftime("%Y")
        self.assertFalse((self.hub / "active" / hid).exists())
        dest = self.hub / "archive" / year / hid
        self.assertTrue((dest / "handover.md").is_file())
        names = sorted(p.name for p in (dest / "events").iterdir())
        self.assertEqual([n.split("--", 1)[1] for n in names], ["backend--ack.md", "product--close.md"])
        self.assertEqual(self.ok("list", "--json").json["items"], [])
        self.assertEqual(self.ids(self.ok("list", "--status", "closed", "--json").json["items"]), [hid])

    def test_archive_failure_rolls_back_close_event(self):
        hid = self.new(to="backend", expects="ack")
        self.ok("ack", hid, team="backend")
        before = self.snapshot()
        with mock.patch.object(ho.os, "rename", side_effect=OSError("locked")):
            r = self.ho("close", hid, "--json", team="product")
        self.assertEqual(r.code, 4)
        self.assertEqual(r.json["error"]["code"], "io_error")
        self.assertUnchanged(before)

    # -- Requirement: Integrity validation ----------------------------------------
    def test_hand_edited_file_broken(self):
        hid = self.new(to="backend", expects="ack")
        doc = self.hub / "active" / hid / "handover.md"
        doc.write_text(doc.read_text(encoding="utf-8").replace("expects: ack\n", ""), encoding="utf-8")
        r = self.ho("validate", "--json")
        self.assertEqual(r.code, 4)
        self.assertFalse(r.json["ok"])
        self.assertIn({"path": str(doc), "message": "missing required field 'expects'"}, r.json["errors"])
        r = self.ho("validate")
        self.assertEqual(r.code, 4)
        self.assertIn(f"{doc}: missing required field 'expects'", r.err)
        # the CLI refuses to act on it
        r = self.ho("ack", hid, "--json", team="backend")
        self.assertEqual((r.code, r.json["error"]["code"]), (4, "integrity_error"))

    def test_validate_reports_each_kind_of_problem(self):
        hid = self.new(to="backend", expects="ack")
        hdir = self.hub / "active" / hid
        edir = hdir / "events"
        edir.mkdir()
        (edir / "1.md").write_text("---\nat: 2999-01-01T00:00:00.000Z\nteam: ios\naction: ack\n---\n", encoding="utf-8")
        (edir / "2.md").write_text("---\nat: 2999-01-01T00:00:00.000Z\nteam: backend\naction: wave\n---\n",
                                   encoding="utf-8")
        (edir / "3.md").write_text("---\nat: [x\n---\n", encoding="utf-8")
        hid2 = self.new(to="backend", expects="ack")
        edir2 = self.hub / "active" / hid2 / "events"
        edir2.mkdir()
        (edir2 / "x.md").write_text("---\nat: 2999-01-01T00:00:00.000Z\nteam: frontend\naction: ack\n---\n",
                                    encoding="utf-8")
        doc2 = self.hub / "active" / hid2 / "handover.md"
        doc2.write_text(doc2.read_text(encoding="utf-8").replace("to: [backend]", "to: [backend, ios]"),
                        encoding="utf-8")
        r = self.ho("validate", "--json")
        self.assertEqual(r.code, 4)
        by_path = {}
        for e in r.json["errors"]:
            by_path.setdefault(e["path"], []).append(e["message"])
        self.assertIn("unknown team 'ios' (not in hub.json)", by_path[str(edir / "1.md")])
        self.assertTrue(any("unknown action" in m for m in by_path[str(edir / "2.md")]))
        self.assertTrue(any(m.startswith("line 2:") for m in by_path[str(edir / "3.md")]))
        self.assertIn("unknown team 'ios' (not in hub.json)", by_path[str(doc2)])
        # once the registry problem is fixed, the frontend event is reported as invalid in order
        doc2.write_text(doc2.read_text(encoding="utf-8").replace("to: [backend, ios]", "to: [backend]"),
                        encoding="utf-8")
        r = self.ho("validate", "--json")
        msgs = [e["message"] for e in r.json["errors"] if e["path"] == str(edir2 / "x.md")]
        self.assertEqual(len(msgs), 1)
        self.assertIn("not valid at its position", msgs[0])

    def test_validate_ok(self):
        self.new()
        r = self.ok("validate", "--json")
        self.assertEqual((r.json["ok"], r.json["checked"], r.json["errors"]), (True, 1, []))
