"""Scenarios from specs/handover-cli/spec.md (except git sync, see test_git_sync.py)."""

import json
import subprocess
import sys
from unittest import mock

from helpers import ROOT, HubTestCase, clean_env, ho, run_ho


class Cli(HubTestCase):
    # -- Requirement: On-demand execution ------------------------------------------
    def test_no_server_needed(self):
        self.new(to="backend")
        proc = subprocess.run(
            [sys.executable, str(ROOT / "ho.py"), "inbox", "--team", "backend", "--json"],
            cwd=self.hub, env=clean_env(), capture_output=True, timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout.decode("ascii"))
        self.assertEqual(len(data["action_required"]), 1)

    # -- Requirement: Hub resolution ----------------------------------------------
    def test_hub_found_from_subdirectory(self):
        hid = self.new()
        r = run_ho("list", "--json", cwd=self.hub / "active" / hid)
        self.assertEqual(r.code, 0, r)
        self.assertEqual(self.ids(r.json["items"]), [hid])

    def test_hub_from_option_and_env(self):
        hid = self.new()
        r = run_ho("list", "--hub", self.hub, "--json", cwd=self.tmp)
        self.assertEqual(self.ids(r.json["items"]), [hid])
        r = run_ho("list", "--json", cwd=self.tmp, env=clean_env() | {"HO_HUB": str(self.hub)})
        self.assertEqual(self.ids(r.json["items"]), [hid])

    def test_hub_not_found(self):
        r = run_ho("list", "--json", cwd=self.tmp)
        self.assertEqual((r.code, r.json["error"]["code"]), (2, "hub_not_found"))
        for hint in ("--hub", "HO_HUB", "hub.json"):
            self.assertIn(hint, r.json["error"]["message"])
        r = run_ho("list", "--hub", self.tmp, cwd=self.tmp)
        self.assertEqual(r.code, 2)

    # -- Requirement: Team identity -----------------------------------------------
    def test_team_from_environment(self):
        hid = self.new(to="backend")
        r = self.ho("inbox", "--json", env=clean_env() | {"HO_TEAM": "backend"})
        self.assertEqual(r.json["team"], "backend")
        self.assertEqual(self.ids(r.json["action_required"]), [hid])
        # --team wins over HO_TEAM
        r = self.ho("inbox", "--json", team="frontend", env=clean_env() | {"HO_TEAM": "backend"})
        self.assertEqual(r.json["team"], "frontend")

    def test_missing_team(self):
        hid = self.new(to="backend", expects="ack")
        before = self.snapshot()
        r = self.ho("ack", hid, "--json")
        self.assertEqual((r.code, r.json["error"]["code"]), (1, "team_required"))
        self.assertIn("--team", r.json["error"]["message"])
        self.assertIn("HO_TEAM", r.json["error"]["message"])
        self.assertIn("--team", r.err)
        self.assertUnchanged(before)

    # -- Requirement: Commands ----------------------------------------------------
    def test_create_from_markdown_file(self):
        brief = self.tmp / "brief.md"
        content = "# Checkout redesign\n\n| a | b |\n|---|---|\n\nКириллица и emoji 🚀\n"
        brief.write_bytes(content.encode("utf-8"))
        r = self.ok("new", "--to", "backend,frontend", "--expects", "response", "--title", "Checkout redesign",
                    "--body-file", "../brief.md", team="product")
        hid = r.out.split()[1]
        self.assertRegex(hid, r"^HO-\d{8}-[a-z2-7]{4}$")
        shown = self.show(hid)
        self.assertEqual(shown["body"], content)
        self.assertEqual(shown["status"], "open")
        self.assertFalse(shown["archived"])

    def test_body_from_stdin(self):
        hid = self.new(to="backend", expects="response")
        answer = "## Answer\r\n\r\nEndpoint: `POST /orders` — ok ✓\n"
        self.ok("respond", hid[-4:], "--body", "-", team="backend", stdin=answer)
        self.assertEqual(self.show(hid)["events"][0]["body"], answer)

    def test_body_option_only_accepts_dash(self):
        hid = self.new(to="backend", expects="response")
        r = self.ho("respond", hid, "--body", "text", team="backend")
        self.assertEqual(r.code, 1)
        r = self.ho("respond", hid, "-m", "a", "--body-file", "x.md", team="backend")
        self.assertEqual(r.code, 1, "body sources are mutually exclusive")

    def test_new_options(self):
        ref = self.new(title="Earlier")
        hid = self.new(to="backend", expects="done", extra=["--priority", "low", "--due", "2026-12-01",
                                                            "--tag", "a", "--tag", "b,c", "--ref", ref[-4:]])
        s = self.show(hid)
        self.assertEqual((s["priority"], s["due"], s["tags"], s["refs"]), ("low", "2026-12-01", ["a", "b", "c"], [ref]))
        for bad in (["--due", "2026-13-01"], ["--ref", "zzzzzz"], ["--priority", "urgent"], ["--expects", "maybe"]):
            args = ["new", "--to", "backend", "--title", "x", "-m", "b"]
            if bad[0] != "--expects":
                args += ["--expects", "ack"]
            r = self.ho(*args, *bad, team="product")
            self.assertIn(r.code, (1, 2), bad)

    def test_teams(self):
        r = self.ok("teams", "--json")
        self.assertEqual(r.json["teams"][0], {"id": "strategy", "name": "Strategy"})
        self.assertIn("backend", self.ok("teams").out)

    def test_show_works_for_archived(self):
        hid = self.new(to="backend", expects="ack", body="# Original\n")
        self.ok("ack", hid, "-m", "noted", team="backend")
        self.ok("close", hid, team="product")
        s = self.show(hid, team="backend")
        self.assertEqual(s["status"], "closed")
        self.assertTrue(s["archived"])
        self.assertEqual(s["body"], "# Original\n")
        self.assertEqual([(e["team"], e["action"], e["body"]) for e in s["events"]],
                         [("backend", "ack", "noted\n"), ("product", "close", "")])
        self.assertEqual(s["allowed_actions"], [])
        self.assertIn("(archived)", self.ok("show", hid).out)

    def test_list_filters(self):
        a = self.new(sender="product", to="backend", expects="done", extra=["--tag", "api"])
        b = self.new(sender="strategy", to="product,frontend", expects="response")
        c = self.new(sender="product", to="frontend", expects="ack")
        self.ok("ack", c, team="frontend")
        d = self.new(sender="product", to="mobile", expects="ack")
        self.ok("close", d, "--force", "-m", "cancel", team="product")

        def ids(*args):
            return sorted(self.ids(self.ok("list", "--json", *args).json["items"]))

        self.assertEqual(ids(), sorted([a, b, c]))
        self.assertEqual(ids("--status", "open"), sorted([a, b]))
        self.assertEqual(ids("--status", "resolved"), sorted([c]))
        self.assertEqual(ids("--status", "closed"), sorted([d]))
        self.assertEqual(ids("--status", "all"), sorted([a, b, c, d]))
        self.assertEqual(ids("--from", "product"), sorted([a, c]))
        self.assertEqual(ids("--to", "frontend"), sorted([b, c]))
        self.assertEqual(ids("--expects", "response"), sorted([b]))
        self.assertEqual(ids("--tag", "api"), sorted([a]))
        self.assertEqual(self.ho("list", "--from", "ios").code, 2)

    # -- Requirement: ID shorthand ------------------------------------------------
    def test_short_id(self):
        with mock.patch.object(ho.Hub, "new_id", side_effect=["HO-20261010-ab3x", "HO-20261011-ab3x",
                                                               "HO-20261010-qqab"]):
            self.new(title="One")
            self.new(title="Two")
            self.new(title="Three")
        self.assertEqual(self.show("qqab")["title"], "Three")
        self.assertEqual(self.show("HO-20261010-ab3x")["title"], "One")
        self.assertEqual(self.show("ho-20261010-AB3X")["title"], "One")
        self.assertEqual(self.show("1-ab3x")["title"], "Two")
        r = self.ho("show", "ab3x", "--json")
        self.assertEqual((r.code, r.json["error"]["code"]), (1, "ambiguous_id"))
        self.assertIn("HO-20261010-ab3x", r.json["error"]["message"])
        self.assertIn("HO-20261011-ab3x", r.json["error"]["message"])
        self.assertEqual(self.ho("show", "3x").code, 1, "fewer than 4 characters")
        r = self.ho("show", "zzzz", "--json")
        self.assertEqual((r.code, r.json["error"]["code"]), (2, "handover_not_found"))

    # -- Requirement: Inbox ---------------------------------------------------------
    def test_new_handover_appears_for_recipient(self):
        hid = self.new(sender="product", to="backend", expects="done")
        inbox = self.inbox("backend")
        self.assertEqual(self.ids(inbox["action_required"]), [hid])
        item = inbox["action_required"][0]
        self.assertEqual(item["my_state"], "pending")
        for key in ("id", "title", "from", "expects", "my_state", "recipients", "priority", "due",
                    "last_activity", "path"):
            self.assertIn(key, item)
        self.assertTrue(item["path"].endswith("handover.md"))
        self.assertEqual(self.inbox("frontend"), {"schema_version": 1, "team": "frontend", "action_required": [],
                                                  "to_review": [], "updates": [], "warnings": []})
        self.assertEqual(self.ids(self.inbox("product")["action_required"]), [])

    def test_acknowledged_part_still_owing_work_stays_in_action_required(self):
        hid = self.new(to="backend", expects="response")
        self.ok("ack", hid, team="backend")
        self.assertEqual(self.ids(self.inbox("backend")["action_required"]), [hid])

    def test_responses_appear_for_sender(self):
        hid = self.new(to="backend,frontend", expects="response")
        self.ok("respond", hid, "-m", "a", team="backend")
        self.assertNotIn(hid, self.ids(self.inbox("product")["to_review"]))
        self.assertIn(hid, self.ids(self.inbox("product")["updates"]), "answer is an update before all are in")
        self.ok("respond", hid, "-m", "b", team="frontend")
        inbox = self.inbox("product")
        self.assertEqual(self.ids(inbox["to_review"]), [hid])
        self.assertEqual(inbox["to_review"][0]["recipients"], {"backend": "answered", "frontend": "answered"})
        self.assertEqual(self.ids(inbox["updates"]), [])

    def test_comment_appears_as_update(self):
        hid = self.new(to="frontend", expects="done")
        self.ok("done", hid, team="frontend")
        self.ok("comment", hid, "-m", "Can you also check mobile web?", team="product")
        self.assertEqual(self.ids(self.inbox("frontend")["updates"]), [hid])
        self.ok("seen", hid, team="frontend")
        self.assertEqual(self.ids(self.inbox("frontend")["updates"]), [])
        self.ok("comment", hid, "-m", "One more", team="product")
        self.assertEqual(self.ids(self.inbox("frontend")["updates"]), [hid])
        self.ok("comment", hid, "-m", "Will do", team="frontend")
        self.assertEqual(self.ids(self.inbox("frontend")["updates"]), [])

    def test_archived_items_never_in_inbox(self):
        hid = self.new(to="backend,frontend", expects="ack")
        self.ok("ack", hid, team="backend")
        self.ok("comment", hid, "-m", "hi", team="backend")
        self.ok("close", hid, "--force", "-m", "stop", team="product")
        for team in ("product", "backend", "frontend", "mobile"):
            inbox = self.inbox(team)
            for group in ("action_required", "to_review", "updates"):
                self.assertEqual(inbox[group], [], (team, group))

    def test_inbox_ordering(self):
        with mock.patch.object(ho.Hub, "new_id", side_effect=[f"HO-20261010-{c}aaa" for c in "abcdef"]):
            a = self.new(title="normal, no due")
            b = self.new(title="high, late due", extra=["--priority", "high", "--due", "2026-12-01"])
            c = self.new(title="high, early due", extra=["--priority", "high", "--due", "2026-11-01"])
            d = self.new(title="high, no due", extra=["--priority", "high"])
            e = self.new(title="low", extra=["--priority", "low"])
            f = self.new(title="normal, due", extra=["--due", "2027-01-01"])
        self.assertEqual(self.ids(self.inbox("backend")["action_required"]), [c, b, d, f, a, e])

    def test_inbox_text(self):
        hid = self.new(to="backend", expects="response", title="Checkout", extra=["--priority", "high"])
        out = self.ok("inbox", team="backend").out
        self.assertIn("Action required (1):", out)
        self.assertIn(f"{hid}  [high] Checkout", out)
        self.assertIn("you: pending", out)

    # -- Requirement: JSON output contract ------------------------------------------
    def test_agent_parses_inbox(self):
        self.new(to="backend")
        r = self.ho("inbox", "--team", "backend", "--json")
        data = json.loads(r.out)
        for key in ("schema_version", "team", "action_required", "to_review", "updates"):
            self.assertIn(key, data)
        self.assertEqual(data["schema_version"], ho.SCHEMA_VERSION)

    def test_agent_creates_and_captures_id(self):
        r = self.ok("new", "--to", "backend", "--expects", "ack", "--title", "x", "-m", "b", "--json", team="product")
        data = json.loads(r.out)
        self.assertTrue((self.hub / "active" / data["id"]).is_dir())
        self.assertEqual(data["status"], "open")

    def test_every_command_emits_one_json_document(self):
        hid = self.new(to="backend,frontend", expects="response")
        other = self.new(to="backend", expects="ack")
        commands = [
            (["teams"], None), (["inbox"], "backend"), (["list"], None), (["show", hid], None),
            (["ack", hid], "backend"), (["comment", hid, "-m", "q"], "frontend"), (["seen", hid], "product"),
            (["respond", hid, "-m", "a"], "backend"), (["decline", hid, "-m", "no"], "frontend"),
            (["return", hid, "--to", "backend", "-m", "more"], "product"), (["done", other], "backend"),
            (["ack", other], "backend"), (["close", other], "product"), (["validate"], None),
            (["show", "nope"], None), (["sync"], None),
        ]
        for args, team in commands:
            with self.subTest(args=args):
                r = self.ho(*args, "--json", team=team)
                data = json.loads(r.out)  # exactly one document, nothing else on stdout
                self.assertEqual(data["schema_version"], 1)
                if r.code:
                    self.assertEqual(set(data["error"]), {"code", "message"})
                    self.assertTrue(r.err)
        r = run_ho("init", self.tmp / "hub2", "--json", cwd=self.tmp)
        self.assertEqual(json.loads(r.out)["teams"], [])

    def test_write_command_json(self):
        hid = self.new(to="backend", expects="response")
        data = self.ok("respond", hid, "-m", "x", "--json", team="backend").json
        self.assertEqual(data["id"], hid)
        self.assertEqual(data["status"], "resolved")
        self.assertEqual(data["my_state"], "answered")
        self.assertEqual((data["event"]["team"], data["event"]["action"]), ("backend", "respond"))

    def test_show_json(self):
        hid = self.new(to="backend", expects="response", body="# Q\n")
        self.ok("respond", hid, "-m", "A", team="backend")
        self.ok("return", hid, "--to", "backend", "-m", "R", team="product")
        s = self.show(hid, team="backend")
        self.assertEqual(s["body"], "# Q\n")
        self.assertEqual(s["recipients"], {"backend": "returned"})
        self.assertEqual([set(e) for e in s["events"]], [{"at", "team", "action", "body", "file"},
                                                         {"at", "team", "action", "target", "body", "file"}])
        self.assertEqual(s["allowed_actions"], ["ack", "respond", "decline", "comment", "seen"])
        self.assertEqual(self.show(hid)["recipients"], {"backend": "returned"})

    # -- Requirement: Errors and exit codes -----------------------------------------
    def test_invalid_transition_exit_code(self):
        hid = self.new(to="backend", expects="response")
        r = self.ho("done", hid, "--json", team="backend")
        self.assertEqual(r.code, 3)
        self.assertEqual(r.json["error"]["code"], "invalid_transition")
        self.assertIn("ho: error:", r.err)

    def test_exit_codes(self):
        self.assertEqual(self.ho("bogus").code, 1)
        self.assertEqual(self.ho("new", team="product").code, 1, "missing required options")
        self.assertEqual(self.ho("show", "HO-20990101-aaaa").code, 2)
        self.assertEqual(self.ho("inbox", team="nobody").code, 2)
        self.assertEqual(run_ho("teams", cwd=self.tmp).code, 2)
        self.assertEqual(self.ho().code, 1)
        r = self.ho("--help")
        self.assertEqual(r.code, 0)
        self.assertIn("inbox", r.out)
        r = self.ho("bogus", "--json")
        self.assertEqual(r.json["error"]["code"], "usage")

    # -- Requirement: Git sync (error path; full flow in test_git_sync.py) ----------
    def test_sync_outside_git(self):
        r = self.ho("sync", "--json", env=clean_env() | {"GIT_CEILING_DIRECTORIES": str(self.tmp)})
        self.assertEqual((r.code, r.json["error"]["code"]), (1, "not_a_git_repo"))

    def test_other_commands_do_not_touch_git(self):
        with mock.patch.object(ho.subprocess, "run", side_effect=AssertionError("git called")):
            hid = self.new(to="backend", expects="ack")
            self.ok("ack", hid, team="backend")
            self.ok("close", hid, team="product")
            self.ok("validate")

    # -- Requirement: Agent guide -----------------------------------------------------
    def test_agent_onboarding(self):
        text = (self.hub / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("ho inbox --json", text)
        self.assertIn("ho show <ID> --json", text)
        self.assertIn("HO_TEAM", text)
        self.assertIn("`ack` -> `ack`, `done` -> `done`, `response` -> `respond`", text)
        for cmd in ("ho return", "ho close", "ho sync", "--body-file"):
            self.assertIn(cmd, text)
        self.assertIn("`backend` - Backend", text, "team list is filled in")
