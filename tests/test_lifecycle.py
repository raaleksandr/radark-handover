"""Scenarios from specs/handover-lifecycle/spec.md, plus transition-table and replay checks."""

import unittest

from helpers import HubTestCase, ho


class TransitionTable(unittest.TestCase):
    def test_table_matches_spec(self):
        for (expects, state, action), new in ho.TRANSITIONS.items():
            self.assertIn(expects, ho.EXPECTATIONS)
            self.assertIn(state, ho.STATES)
            self.assertNotIn(state, ho.SATISFIED[expects], "transitions start only from non-satisfied states")
            if action == "ack":
                self.assertIn(state, ("pending", "returned"))
                self.assertEqual(new, "acknowledged")
            if action == "done":
                self.assertEqual(expects, "done")
            if action == "respond":
                self.assertEqual(expects, "response")
        # decline is allowed from every non-satisfied state
        for expects, open_states in {"ack": ("pending", "returned"),
                                     "done": ("pending", "acknowledged", "returned"),
                                     "response": ("pending", "acknowledged", "returned")}.items():
            for state in open_states:
                self.assertEqual(ho.TRANSITIONS[(expects, state, "decline")], "declined")
        self.assertNotIn(("ack", "acknowledged", "ack"), ho.TRANSITIONS)
        self.assertNotIn(("done", "acknowledged", "ack"), ho.TRANSITIONS)


class Lifecycle(HubTestCase):
    # -- Requirement: Expectation types ----------------------------------------
    def test_expectation_recorded(self):
        hid = self.new(to="backend,frontend", expects="response")
        self.assertEqual(self.show(hid)["expects"], "response")
        self.ok("ack", hid, team="backend")
        self.assertEqual(self.show(hid)["recipients"]["backend"], "acknowledged")
        self.assertEqual(self.show(hid)["status"], "open", "ack does not satisfy a response handover")
        self.assertEqual(self.ho("respond", hid, "-m", "  ", team="backend").code, 1)
        self.ok("respond", hid, "-m", "# Answer", team="backend")
        self.ok("decline", hid, "-m", "Not our area", team="frontend")
        shown = self.show(hid)
        self.assertEqual(shown["recipients"], {"backend": "answered", "frontend": "declined"})
        self.assertEqual(shown["status"], "resolved")

    # -- Requirement: Per-recipient state --------------------------------------
    def test_independent_recipients(self):
        hid = self.new(to="backend,frontend", expects="done")
        self.ok("done", hid, team="backend")
        shown = self.show(hid)
        self.assertEqual(shown["recipients"], {"backend": "done", "frontend": "pending"})
        self.assertEqual(shown["status"], "open")

    # -- Requirement: Recipient actions ----------------------------------------
    def test_respond_to_a_question(self):
        hid = self.new(expects="response")
        r = self.ok("respond", hid, "-m", "## Answer\n\nUse `/v2`.", "--json", team="backend")
        self.assertEqual(r.json["recipients"]["backend"], "answered")
        ev = self.show(hid)["events"]
        self.assertEqual([(e["team"], e["action"]) for e in ev], [("backend", "respond")])
        self.assertEqual(ev[0]["body"], "## Answer\n\nUse `/v2`.\n")

    def test_wrong_action_for_expectation(self):
        hid = self.new(expects="response")
        before = self.snapshot()
        r = self.ho("done", hid, "--json", team="backend")
        self.assertEqual(r.code, 3)
        err = r.json["error"]
        self.assertEqual(err["code"], "invalid_transition")
        self.assertIn("'response'", err["message"])
        self.assertIn("respond", err["message"])
        self.assertUnchanged(before)

    def test_respond_without_body(self):
        hid = self.new(expects="response")
        before = self.snapshot()
        for args in (["respond", hid], ["respond", hid, "-m", ""], ["respond", hid, "--body", "-"]):
            r = self.ho(*args, "--json", team="backend", stdin="\n  \n")
            self.assertNotEqual(r.code, 0)
            self.assertEqual(r.json["error"]["code"], "body_required")
        self.assertUnchanged(before)

    def test_non_recipient_cannot_act(self):
        hid = self.new(to="backend", expects="ack")
        before = self.snapshot()
        r = self.ho("ack", hid, "--json", team="mobile")
        self.assertEqual(r.code, 3)
        self.assertEqual(r.json["error"]["code"], "not_recipient")
        self.assertIn("mobile", r.json["error"]["message"])
        # the sender is not a recipient either
        self.assertEqual(self.ho("ack", hid, team="product").code, 3)
        self.assertUnchanged(before)

    def test_ack_then_done_and_returned_actions(self):
        hid = self.new(expects="done")
        self.ok("ack", hid, team="backend")
        self.assertEqual(self.ho("ack", hid, team="backend").code, 3, "ack only from pending/returned")
        self.ok("done", hid, "-m", "Shipped", team="backend")
        self.assertEqual(self.ho("decline", hid, "-m", "x", team="backend").code, 3, "decline only when unsatisfied")
        self.ok("return", hid, "--to", "backend", "-m", "Tests missing", team="product")
        self.assertEqual(self.show(hid)["recipients"]["backend"], "returned")
        self.ok("ack", hid, team="backend")
        self.ok("done", hid, team="backend")
        self.assertEqual(self.show(hid)["recipients"]["backend"], "done")

    # -- Requirement: Sender return --------------------------------------------
    def test_answer_needs_clarification(self):
        hid = self.new(to="backend", expects="response")
        self.ok("respond", hid, "-m", "First answer", team="backend")
        self.ok("return", hid, "--to", "backend", "-m", "Please add the error codes", team="product")
        shown = self.show(hid)
        self.assertEqual(shown["recipients"]["backend"], "returned")
        self.assertEqual(shown["status"], "open")
        self.assertIn(hid, self.ids(self.inbox("backend")["action_required"]))
        bodies = [e["body"] for e in shown["events"]]
        self.assertIn("First answer\n", bodies)
        ret = shown["events"][-1]
        self.assertEqual((ret["action"], ret["target"]), ("return", "backend"))

    def test_return_pending_part_rejected(self):
        hid = self.new(to="backend,frontend", expects="response")
        self.ok("respond", hid, "-m", "a", team="backend")
        before = self.snapshot()
        r = self.ho("return", hid, "--to", "frontend", "-m", "why?", "--json", team="product")
        self.assertEqual(r.code, 3)
        self.assertEqual(r.json["error"]["code"], "invalid_transition")
        self.assertUnchanged(before)

    def test_return_rules(self):
        hid = self.new(to="backend", expects="response")
        self.ok("respond", hid, "-m", "a", team="backend")
        before = self.snapshot()
        self.assertEqual(self.ho("return", hid, "--to", "backend", "-m", "x", team="backend").code, 3, "only sender")
        self.assertEqual(self.ho("return", hid, "--to", "backend", team="product").code, 1, "body required")
        self.assertEqual(self.ho("return", hid, "--to", "mobile", "-m", "x", team="product").code, 3, "not recipient")
        self.assertUnchanged(before)

    # -- Requirement: Comments ---------------------------------------------------
    def test_clarifying_question_from_recipient(self):
        hid = self.new(to="frontend", expects="done")
        self.assertNotIn(hid, self.ids(self.inbox("product")["updates"]))
        self.ok("comment", hid, "-m", "Which screens are in scope?", team="frontend")
        shown = self.show(hid)
        self.assertEqual(shown["recipients"]["frontend"], "pending")
        self.assertEqual(shown["events"][-1]["action"], "comment")
        self.assertIn(hid, self.ids(self.inbox("product")["updates"]))

    def test_comment_rules(self):
        hid = self.new(to="frontend", expects="done")
        self.assertEqual(self.ho("comment", hid, team="frontend").code, 1, "body required")
        self.assertEqual(self.ho("comment", hid, "-m", "hi", team="mobile").code, 3, "participants only")
        self.ok("comment", hid, "-m", "FYI", team="product")

    # -- Requirement: Overall status ---------------------------------------------
    def test_resolved_when_all_satisfied(self):
        hid = self.new(to="backend,frontend", expects="ack")
        self.ok("ack", hid, team="backend")
        self.assertEqual(self.show(hid)["status"], "open")
        self.ok("ack", hid, team="frontend")
        self.assertEqual(self.show(hid)["status"], "resolved")

    # -- Requirement: Closing and archiving --------------------------------------
    def test_sender_closes_resolved_handover(self):
        hid = self.new(to="backend", expects="ack")
        self.ok("ack", hid, team="backend")
        r = self.ok("close", hid, "-m", "Thanks", "--json", team="product")
        self.assertEqual(r.json["status"], "closed")
        self.assertTrue(r.json["archived"])
        self.assertFalse((self.hub / "active" / hid).exists())
        self.assertTrue((self.hub / "archive" / hid[3:7] / hid).is_dir())
        self.assertEqual(self.show(hid)["events"][-1]["action"], "close")

    def test_cancel_open_handover(self):
        hid = self.new(to="backend", expects="done")
        before = self.snapshot()
        r = self.ho("close", hid, "--json", team="product")
        self.assertEqual(r.code, 3)
        self.assertIn("--force", r.json["error"]["message"])
        self.assertIn("reason", r.json["error"]["message"])
        r = self.ho("close", hid, "--force", "--json", team="product")
        self.assertEqual(r.json["error"]["code"], "body_required")
        self.assertUnchanged(before)
        self.ok("close", hid, "--force", "-m", "Plans changed", team="product")
        self.assertEqual(self.show(hid)["status"], "closed")

    def test_recipient_cannot_close(self):
        hid = self.new(to="backend", expects="ack")
        self.ok("ack", hid, team="backend")
        before = self.snapshot()
        r = self.ho("close", hid, "--json", team="backend")
        self.assertEqual(r.code, 3)
        self.assertEqual(r.json["error"]["code"], "not_sender")
        self.assertUnchanged(before)

    def test_no_events_after_close(self):
        hid = self.new(to="backend", expects="ack")
        self.ok("close", hid, "--force", "-m", "cancel", team="product")
        before = self.snapshot()
        attempts = [
            ("ack", hid), ("comment", hid, "-m", "x"), ("seen", hid), ("decline", hid, "-m", "x"),
        ]
        for args in attempts:
            r = self.ho(*args, "--json", team="backend")
            self.assertEqual(r.code, 3, args)
            self.assertEqual(r.json["error"]["code"], "archived")
            self.assertIn(f"--ref {hid}", r.json["error"]["message"])
        for args in (("close", hid, "-m", "x"), ("comment", hid, "-m", "x")):
            self.assertEqual(self.ho(*args, team="product").code, 3)
        self.assertUnchanged(before)

    # -- Requirement: Auto-close -------------------------------------------------
    def test_fyi_handover_disappears_once_noted(self):
        hid = self.new(to="backend,frontend", expects="ack", extra=["--auto-close"])
        r = self.ok("ack", hid, "--json", team="backend")
        self.assertFalse(r.json["auto_closed"])
        r = self.ok("ack", hid, "--json", team="frontend")
        self.assertTrue(r.json["auto_closed"])
        self.assertEqual(r.json["status"], "closed")
        self.assertFalse((self.hub / "active" / hid).exists())
        shown = self.show(hid)
        self.assertTrue(shown["archived"])
        close = shown["events"][-1]
        self.assertEqual((close["team"], close["action"]), ("product", "close"))
        self.assertIn("Auto-closed", close["body"])
        self.assertNotIn(hid, self.ids(self.inbox("product")["to_review"]))

    # -- Requirement: Seen marker ------------------------------------------------
    def test_clear_an_update(self):
        hid = self.new(to="frontend", expects="done")
        self.ok("comment", hid, "-m", "Question 1", team="frontend")
        self.ok("comment", hid, "-m", "Question 2", team="frontend")
        self.assertIn(hid, self.ids(self.inbox("product")["updates"]))
        r = self.ok("seen", hid, "--json", team="product")
        self.assertEqual(r.json["recipients"]["frontend"], "pending", "seen changes no state")
        self.assertNotIn(hid, self.ids(self.inbox("product")["updates"]))
        # product's seen is not an update for frontend (frontend is action_required anyway, check via done)
        self.ok("done", hid, team="frontend")
        self.ok("seen", hid, team="product")
        self.assertNotIn(hid, self.ids(self.inbox("frontend")["updates"]))
        self.ok("comment", hid, "-m", "Question 3", team="frontend")
        self.assertNotIn(hid, self.ids(self.inbox("product")["updates"]), "resolved -> listed in to_review instead")
        self.assertIn(hid, self.ids(self.inbox("product")["to_review"]))

    def test_seen_rules(self):
        hid = self.new(to="frontend", expects="done")
        self.assertEqual(self.ho("seen", hid, team="mobile").code, 3)


class Replay(HubTestCase):
    def write_event(self, hid, name, at, team, action, body="", target=None):
        edir = self.handover_dir(hid) / "events"
        edir.mkdir(exist_ok=True)
        fields = [("at", at), ("team", team), ("action", action), ("target", target)]
        (edir / name).write_text(ho.serialize_document(fields, body), encoding="utf-8")

    def test_invalid_events_are_skipped_and_reported(self):
        hid = self.new(to="backend,frontend", expects="response")
        self.ok("respond", hid, "-m", "ok", team="backend")
        # merged from another machine: frontend acks twice, mobile acts, frontend answers
        self.write_event(hid, "29990101T000000.001Z--frontend--ack.md", "2999-01-01T00:00:00.001Z", "frontend", "ack")
        self.write_event(hid, "29990101T000000.002Z--frontend--ack.md", "2999-01-01T00:00:00.002Z", "frontend", "ack")
        self.write_event(hid, "29990101T000000.003Z--mobile--comment.md", "2999-01-01T00:00:00.003Z", "mobile",
                         "comment", "hi")
        shown = self.show(hid)
        self.assertEqual(shown["recipients"]["frontend"], "acknowledged")
        reasons = {i["file"]: i["reason"] for i in shown["invalid_events"]}
        self.assertEqual(sorted(reasons), ["29990101T000000.002Z--frontend--ack.md",
                                           "29990101T000000.003Z--mobile--comment.md"])
        # next action is stamped after the far-future events, so replay order matches
        self.ok("respond", hid, "-m", "answer", team="frontend")
        self.assertEqual(self.show(hid)["status"], "resolved")

    def test_event_after_close_is_invalid(self):
        hid = self.new(to="backend", expects="ack")
        self.ok("ack", hid, team="backend")
        self.ok("close", hid, team="product")
        self.write_event(hid, "29990101T000000.000Z--backend--comment.md", "2999-01-01T00:00:00.000Z", "backend",
                         "comment", "late")
        shown = self.show(hid)
        self.assertEqual(len(shown["invalid_events"]), 1)
        self.assertIn("closed", shown["invalid_events"][0]["reason"])


if __name__ == "__main__":
    unittest.main()
