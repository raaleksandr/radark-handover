import unittest

from helpers import ho


class FrontMatterRoundTrip(unittest.TestCase):
    def roundtrip(self, fields, body="Body\n"):
        text = ho.serialize_document(fields, body)
        data, parsed_body = ho.parse_document(text, "x.md")
        self.assertEqual(data, {k: v for k, v in fields if v is not None})
        self.assertEqual(parsed_body, body)
        return text

    def test_plain_values(self):
        text = self.roundtrip([("id", "HO-20261010-ab3x"), ("from", "product"), ("created", "2026-10-10T14:42:09.364Z")])
        self.assertIn("id: HO-20261010-ab3x\n", text)
        self.assertIn("created: 2026-10-10T14:42:09.364Z\n", text)

    def test_strings_needing_quotes(self):
        for value in ["a: b", "true", "null", "123", "#tag", "- x", "[x]", "a, b", "", " lead", "it's", 'say "hi"',
                      "x #y", "{a}", "tab\there", "line\nbreak", "@me", "1.5", "yes"]:
            with self.subTest(value=value):
                text = self.roundtrip([("title", value)])
                self.assertNotIn(f"title: {value}\n", text) if value else None

    def test_unicode_and_lists(self):
        self.roundtrip([("title", "Редизайн оплаты"), ("to", ["backend", "frontend"]), ("tags", ["a, b", "c"]),
                        ("refs", []), ("auto_close", True), ("flag", False)])

    def test_body_verbatim(self):
        for body in ["", "\n\nleading blank lines\n", "no trailing newline", "---\nnot front matter\n---\n",
                     "crlf\r\nbody\r\n", "unicode ✓ ünïcödé\n"]:
            with self.subTest(body=body):
                self.roundtrip([("a", "b")], body)

    def test_hand_written_variants(self):
        text = "﻿---\r\nid: x\r\n# comment\r\nto: [ a ,'b c', \"d\" ]\r\ntitle: 'it''s'\r\n---\r\n\r\nBody\r\n"
        data, body = ho.parse_document(text)
        self.assertEqual(data, {"id": "x", "to": ["a", "b c", "d"], "title": "it's"})
        self.assertEqual(body, "Body\r\n")


class FrontMatterRejections(unittest.TestCase):
    def assertRejected(self, text, line, fragment):
        with self.assertRaises(ho.FrontMatterError) as cm:
            ho.parse_document(text, "dir/file.md")
        self.assertEqual(cm.exception.line, line)
        self.assertIn("dir/file.md", cm.exception.message)
        self.assertIn(fragment, cm.exception.message)
        self.assertEqual(cm.exception.exit_code, ho.EXIT_INTEGRITY)

    def test_missing_start(self):
        self.assertRejected("id: x\n", 1, "missing front matter")

    def test_not_closed(self):
        with self.assertRaises(ho.FrontMatterError) as cm:
            ho.parse_document("---\nid: x\n")
        self.assertIn("not closed", cm.exception.message)

    def test_block_list(self):
        self.assertRejected("---\nto:\n  - a\n---\n", 2, "no value")

    def test_indented(self):
        self.assertRejected("---\nid: x\n  nested: y\n---\n", 3, "indented")

    def test_duplicate_key(self):
        self.assertRejected("---\nid: x\nid: y\n---\n", 3, "duplicate key")

    def test_bad_line(self):
        self.assertRejected("---\njust text\n---\n", 2, "expected 'key: value'")

    def test_unsupported_values(self):
        cases = {
            "{a: 1}": "unsupported value",
            "a: b": "must be quoted",
            "x # c": "comments",
            "null": "null values",
            "[a, [b]]": "nested",
            "[a,, b]": "empty list item",
            "[a, b": "unterminated list",
            "\"open": "double-quoted",
            "'open": "unterminated single-quoted",
            "\"a\" b": "unexpected text",
            "&anchor x": "unsupported value",
        }
        for value, fragment in cases.items():
            with self.subTest(value=value):
                self.assertRejected(f"---\nid: ok\nk: {value}\n---\n", 3, fragment)


if __name__ == "__main__":
    unittest.main()
