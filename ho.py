#!/usr/bin/env python3
"""ho - team handover hub.

A zero-dependency CLI (Python 3.10+, standard library only) for passing
Markdown handovers between teams through a plain-file hub, usually a shared
git repository.

The module is split into sections so that a future HTTP/MCP server can reuse
everything except the CLI:

    errors       - HoError and exit codes
    front matter - restricted YAML-subset parser/serializer
    store        - hub resolution, IDs, handover/event files, archiving
    lifecycle    - transition table, replay, action checks
    views        - summary / inbox / list / show dicts
    validate     - hub integrity checks
    sync         - git helper
    cli          - argparse wiring, text/JSON rendering
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

__version__ = "0.2.0"  # package version; pyproject.toml reads it (single source)

# JSON contract version: every --json document carries it as `schema_version`, and
# `ho --version --json` reports the same number as `contract`. Bump it only for breaking
# changes to --json shapes or exit codes (see README, "Contract version").
SCHEMA_VERSION = 1

# Format version of hub.json, independent of the JSON output contract.
HUB_FORMAT_VERSION = 1

# Optional capabilities announced in `ho --version --json` as `features` (additive, see README).
FEATURES = ("hub-lock",)

# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_NOT_FOUND = 2
EXIT_LIFECYCLE = 3
EXIT_INTEGRITY = 4


class HoError(Exception):
    """An error with a stable machine-readable code and a process exit code."""

    def __init__(self, code: str, message: str, exit_code: int = EXIT_USAGE):
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code


class FrontMatterError(HoError):
    def __init__(self, path, line: int | None, detail: str):
        self.path = path
        self.line = line
        self.detail = f"line {line}: {detail}" if line else detail
        super().__init__("invalid_front_matter", f"{path}: {self.detail}", EXIT_INTEGRITY)


# ---------------------------------------------------------------------------
# front matter: a strict YAML subset
#
#   key: plain scalar          -> str
#   key: "quoted \"string\""   -> str (JSON-style escapes)
#   key: 'single ''quoted'''   -> str
#   key: true | false          -> bool
#   key: [a, "b, c", d]        -> list[str]
#
# Anything else (nesting, block lists, null, anchors, multi-line values) is
# rejected with the file and line number.
# ---------------------------------------------------------------------------

_FM_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):(?:[ \t]+(.*?))?[ \t]*$")
_FM_PLAIN_RE = re.compile(r"^\w(?:[\w .@/+:-]*[\w.@/+-])?$")
_FM_NUMBER_RE = re.compile(r"^[-+]?(?:\.?\d[\d_.]*(?:[eE][-+]?\d+)?|0x[0-9a-fA-F]+|0o[0-7]+|\.inf|\.nan)$", re.I)
_FM_RESERVED = {"true", "false", "yes", "no", "on", "off", "null", "~", "y", "n"}
_FM_BAD_START = set("{}[]&*!|>%@`#,?-'\"")


def _fm_plain_ok(value: str) -> bool:
    return (
        bool(_FM_PLAIN_RE.match(value))
        and ": " not in value
        and " #" not in value
        and value.lower() not in _FM_RESERVED
        and not _FM_NUMBER_RE.match(value)
    )


def _fm_scalar_out(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if not isinstance(value, str):
        raise TypeError(f"unsupported front matter value: {value!r}")
    if _fm_plain_ok(value):
        return value
    return json.dumps(value, ensure_ascii=False)


def serialize_front_matter(fields) -> str:
    """Serialize an ordered iterable of (key, value) pairs; None values are skipped."""
    lines = []
    for key, value in fields:
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            out = "[" + ", ".join(_fm_scalar_out(v) for v in value) + "]"
        else:
            out = _fm_scalar_out(value)
        lines.append(f"{key}: {out}\n")
    return "".join(lines)


def serialize_document(fields, body: str) -> str:
    return "---\n" + serialize_front_matter(fields) + "---\n\n" + body


def _fm_parse_quoted(text: str, pos: int, path, line: int):
    """Parse a quoted string starting at text[pos]; return (value, end_pos)."""
    quote = text[pos]
    if quote == '"':
        try:
            value, end = json.JSONDecoder().raw_decode(text, pos)
        except ValueError:
            raise FrontMatterError(path, line, "invalid or unterminated double-quoted string")
        if not isinstance(value, str):
            raise FrontMatterError(path, line, "invalid double-quoted string")
        return value, end
    out = []
    i = pos + 1
    while i < len(text):
        ch = text[i]
        if ch == "'":
            if i + 1 < len(text) and text[i + 1] == "'":
                out.append("'")
                i += 2
                continue
            return "".join(out), i + 1
        out.append(ch)
        i += 1
    raise FrontMatterError(path, line, "unterminated single-quoted string")


def _fm_parse_plain(text: str, path, line: int, in_list: bool) -> str:
    if not text:
        raise FrontMatterError(path, line, "empty value is not supported (use \"\" for an empty string)")
    if in_list and any(c in text for c in "[]{}"):
        raise FrontMatterError(path, line, "nested lists and maps are not supported")
    if text[0] in _FM_BAD_START and not (text[0] == "-" and len(text) > 1 and text[1] != " "):
        raise FrontMatterError(path, line, f"unsupported value {text!r}: quote it or use the documented subset")
    if ": " in text or text.endswith(":"):
        raise FrontMatterError(path, line, f"unsupported value {text!r}: values containing ': ' must be quoted")
    if " #" in text:
        raise FrontMatterError(path, line, f"unsupported value {text!r}: comments are not supported; quote the value")
    if text.lower() in ("null", "~"):
        raise FrontMatterError(path, line, "null values are not supported")
    return text


def _fm_parse_list(text: str, path, line: int) -> list:
    if not text.endswith("]"):
        raise FrontMatterError(path, line, "unterminated list (expected ']')")
    inner = text[1:-1]
    items = []
    i = 0
    n = len(inner)
    if not inner.strip():
        return items
    while True:
        while i < n and inner[i] in " \t":
            i += 1
        if i >= n:
            raise FrontMatterError(path, line, "empty list item")
        if inner[i] in "\"'":
            value, i = _fm_parse_quoted(inner, i, path, line)
            items.append(value)
            while i < n and inner[i] in " \t":
                i += 1
        else:
            j = inner.find(",", i)
            j = n if j < 0 else j
            raw = inner[i:j].strip()
            if not raw:
                raise FrontMatterError(path, line, "empty list item")
            items.append(_fm_parse_plain(raw, path, line, in_list=True))
            i = j
        if i >= n:
            return items
        if inner[i] != ",":
            raise FrontMatterError(path, line, f"expected ',' in list at {inner[i:]!r}")
        i += 1


def _fm_parse_value(text: str, path, line: int):
    if text.startswith("["):
        return _fm_parse_list(text, path, line)
    if text[:1] in ("\"", "'"):
        value, end = _fm_parse_quoted(text, 0, path, line)
        if text[end:].strip():
            raise FrontMatterError(path, line, f"unexpected text after quoted string: {text[end:]!r}")
        return value
    if text == "true":
        return True
    if text == "false":
        return False
    return _fm_parse_plain(text, path, line, in_list=False)


def parse_front_matter_lines(lines, path, first_line: int = 2) -> dict:
    data: dict = {}
    for offset, raw in enumerate(lines):
        line_no = first_line + offset
        text = raw.rstrip("\r\n")
        if not text.strip() or text.lstrip().startswith("#") and not text.startswith((" ", "\t")):
            continue
        if text[:1] in (" ", "\t", "-"):
            raise FrontMatterError(
                path, line_no, "indented lines and block lists are not supported; use flow lists like [a, b]"
            )
        m = _FM_KEY_RE.match(text)
        if not m:
            raise FrontMatterError(path, line_no, f"expected 'key: value', got {text!r}")
        key, value = m.group(1), m.group(2)
        if key in data:
            raise FrontMatterError(path, line_no, f"duplicate key '{key}'")
        if value is None or value == "":
            raise FrontMatterError(path, line_no, f"key '{key}' has no value (nested values are not supported)")
        data[key] = _fm_parse_value(value, path, line_no)
    return data


def parse_document(text: str, path="<string>"):
    """Split a Markdown document with front matter into (dict, body).

    Tolerates a UTF-8 BOM and CRLF line endings (e.g. git autocrlf checkouts).
    """
    if text.startswith("﻿"):
        text = text[1:]
    lines = re.split(r"(?<=\n)", text)
    if not lines or lines[0].rstrip("\r\n").rstrip() != "---":
        raise FrontMatterError(path, 1, "missing front matter (file must start with a '---' line)")
    end = None
    for i in range(1, len(lines)):
        if lines[i].rstrip("\r\n").rstrip() == "---":
            end = i
            break
    if end is None:
        raise FrontMatterError(path, None, "front matter is not closed with a '---' line")
    data = parse_front_matter_lines(lines[1:end], path, first_line=2)
    body = "".join(lines[end + 1:])
    if body.startswith("\r\n"):
        body = body[2:]
    elif body.startswith("\n"):
        body = body[1:]
    return data, body


# ---------------------------------------------------------------------------
# store
# ---------------------------------------------------------------------------

ID_RE = re.compile(r"^HO-(\d{8})-([a-z2-7]{4})$")
TEAM_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
YEAR_RE = re.compile(r"^\d{4}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TS_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?Z$")
EVENT_NAME_RE = re.compile(r"^(\d{8}T\d{6}\.\d{3}Z)--([a-z0-9][a-z0-9-]*)--([a-z]+)(?:-(\d+))?\.md$")
ID_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"

PRIORITIES = ("high", "normal", "low")
PRIORITY_RANK = {"high": 0, "normal": 1, "low": 2}
EXPECTATIONS = ("ack", "done", "response")
REQUIRED_FIELDS = ("id", "title", "from", "to", "expects", "created")
FIELD_ORDER = ("id", "title", "from", "to", "expects", "priority", "created", "due", "tags", "refs", "auto_close")


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def format_ts(value: dt.datetime) -> str:
    value = value.astimezone(dt.timezone.utc)
    return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"


def parse_ts(text, require_ms: bool = False) -> dt.datetime | None:
    if not isinstance(text, str):
        return None
    m = TS_RE.match(text)
    if not m:
        return None
    frac = m.group(7)
    if require_ms and (frac is None or len(frac) != 3):
        return None
    try:
        return dt.datetime(
            *(int(g) for g in m.groups()[:6]),
            microsecond=int((frac or "0").ljust(6, "0")),
            tzinfo=dt.timezone.utc,
        )
    except ValueError:
        return None


def valid_date(text) -> bool:
    if not isinstance(text, str) or not DATE_RE.match(text):
        return False
    try:
        dt.date.fromisoformat(text)
    except ValueError:
        return False
    return True


def read_text(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


def write_text_atomic(path: Path, content: str) -> None:
    """Write via a temp file in the same directory and rename into place."""
    tmp = path.parent / f".tmp-{secrets.token_hex(6)}-{path.name}"
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def team_error(team: str, teams: dict, role: str = "team") -> HoError:
    known = ", ".join(teams) or "(none registered)"
    return HoError("unknown_team", f"Unknown {role} '{team}'. Registered teams: {known}.", EXIT_NOT_FOUND)


def load_hub_config(path: Path) -> dict:
    try:
        data = json.loads(path.read_bytes().decode("utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise HoError("invalid_hub", f"{path}: cannot read hub.json: {exc}", EXIT_INTEGRITY)
    if not isinstance(data, dict) or not isinstance(data.get("teams"), list):
        raise HoError("invalid_hub", f"{path}: hub.json must be an object with a 'teams' list", EXIT_INTEGRITY)
    teams: dict = {}
    for i, entry in enumerate(data["teams"]):
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            raise HoError("invalid_hub", f"{path}: teams[{i}] must be an object with 'id' and 'name'", EXIT_INTEGRITY)
        tid = entry["id"]
        if not TEAM_RE.match(tid):
            raise HoError(
                "invalid_hub",
                f"{path}: team id '{tid}' must be lowercase letters, digits and hyphens",
                EXIT_INTEGRITY,
            )
        if tid in teams:
            raise HoError("invalid_hub", f"{path}: duplicate team id '{tid}'", EXIT_INTEGRITY)
        name = entry.get("name", tid)
        if not isinstance(name, str) or not name.strip():
            raise HoError("invalid_hub", f"{path}: team '{tid}' needs a non-empty 'name'", EXIT_INTEGRITY)
        teams[tid] = name
    data["teams_by_id"] = teams
    return data


class Hub:
    def __init__(self, root: Path):
        self.root = root
        self.config = load_hub_config(root / "hub.json")
        self.teams: dict = self.config["teams_by_id"]

    @property
    def active_dir(self) -> Path:
        return self.root / "active"

    @property
    def archive_dir(self) -> Path:
        return self.root / "archive"

    def require_team(self, team: str, role: str = "team") -> str:
        if team not in self.teams:
            raise team_error(team, self.teams, role)
        return team

    def iter_handover_dirs(self, active: bool = True, archive: bool = True):
        """Yield (path, archived) for every handover directory."""
        if active and self.active_dir.is_dir():
            for p in sorted(self.active_dir.iterdir()):
                if p.is_dir() and ID_RE.match(p.name):
                    yield p, False
        if archive and self.archive_dir.is_dir():
            for year in sorted(self.archive_dir.iterdir()):
                if year.is_dir() and YEAR_RE.match(year.name):
                    for p in sorted(year.iterdir()):
                        if p.is_dir() and ID_RE.match(p.name):
                            yield p, True

    def id_exists(self, hid: str) -> bool:
        if (self.active_dir / hid).exists():
            return True
        return any(p.name == hid for p, _ in self.iter_handover_dirs(active=False))

    def new_id(self, created: dt.datetime) -> str:
        day = created.strftime("%Y%m%d")
        for _ in range(20):
            hid = f"HO-{day}-" + "".join(secrets.choice(ID_ALPHABET) for _ in range(4))
            if not self.id_exists(hid):
                return hid
        raise HoError("id_collision", "Could not generate a unique handover ID; try again.", EXIT_INTEGRITY)

    def resolve_id(self, ref: str):
        """Return (path, archived) for a full ID or a unique suffix of >= 4 characters."""
        ref = (ref or "").strip()
        if ref[:3].upper() == "HO-":
            ref = "HO-" + ref[3:].lower()
        dirs = list(self.iter_handover_dirs())
        if ID_RE.match(ref):
            matches = [d for d in dirs if d[0].name == ref]
        else:
            if len(ref) < 4:
                raise HoError("invalid_id", f"'{ref}' is too short: use the full ID or at least 4 characters.")
            low = ref.lower()
            matches = [d for d in dirs if d[0].name.lower().endswith(low)]
        ids = sorted({p.name for p, _ in matches})
        if not ids:
            raise HoError("handover_not_found", f"No handover matches '{ref}'.", EXIT_NOT_FOUND)
        if len(ids) > 1:
            raise HoError("ambiguous_id", f"'{ref}' matches several handovers: {', '.join(ids)}. Use more characters.")
        if len(matches) > 1:
            places = ", ".join(str(p) for p, _ in matches)
            raise HoError(
                "duplicate_id", f"{ids[0]} exists in more than one place ({places}); run 'ho validate'.", EXIT_INTEGRITY
            )
        return matches[0]

    def load(self, ref: str) -> "Handover":
        path, archived = self.resolve_id(ref)
        return Handover.load(self, path, archived)


def check_meta(meta: dict, dir_name: str | None = None, teams: dict | None = None) -> list:
    """Return a list of problems with a handover's front matter."""
    problems = []
    for key in REQUIRED_FIELDS:
        if key not in meta:
            problems.append(f"missing required field '{key}'")
    hid = meta.get("id")
    if "id" in meta:
        if not isinstance(hid, str) or not ID_RE.match(hid):
            problems.append("field 'id' must look like HO-YYYYMMDD-xxxx")
        elif dir_name and hid != dir_name:
            problems.append(f"field 'id' ({hid}) does not match directory name {dir_name}")
    if "title" in meta and (not isinstance(meta["title"], str) or not meta["title"].strip()):
        problems.append("field 'title' must be a non-empty string")
    sender = meta.get("from")
    if "from" in meta and (not isinstance(sender, str) or not TEAM_RE.match(sender)):
        problems.append("field 'from' must be a team id")
        sender = None
    to = meta.get("to")
    if "to" in meta:
        if not isinstance(to, list) or not to:
            problems.append("field 'to' must be a non-empty list like [backend, frontend]")
            to = []
        else:
            for t in to:
                if not TEAM_RE.match(t):
                    problems.append(f"field 'to' contains invalid team id {t!r}")
            if len(set(to)) != len(to):
                problems.append("field 'to' contains duplicate teams")
            if sender and sender in to:
                problems.append("field 'to' must not contain the sender")
    else:
        to = []
    if "expects" in meta and meta["expects"] not in EXPECTATIONS:
        problems.append(f"field 'expects' must be one of {', '.join(EXPECTATIONS)}")
    if "created" in meta and parse_ts(meta["created"]) is None:
        problems.append("field 'created' must be an ISO 8601 UTC timestamp like 2026-10-10T14:42:09.364Z")
    if "priority" in meta and meta["priority"] not in PRIORITIES:
        problems.append(f"field 'priority' must be one of {', '.join(PRIORITIES)}")
    if "due" in meta and not valid_date(meta["due"]):
        problems.append("field 'due' must be a date like 2026-10-31")
    for key in ("tags", "refs"):
        if key in meta and (not isinstance(meta[key], list) or not all(isinstance(v, str) for v in meta[key])):
            problems.append(f"field '{key}' must be a list")
    if isinstance(meta.get("refs"), list):
        for r in meta["refs"]:
            if not ID_RE.match(r):
                problems.append(f"field 'refs' contains invalid handover ID {r!r}")
    if "auto_close" in meta and not isinstance(meta["auto_close"], bool):
        problems.append("field 'auto_close' must be true or false")
    if teams is not None:
        for t in ([sender] if sender else []) + [t for t in to if isinstance(t, str)]:
            if t not in teams:
                problems.append(f"unknown team '{t}' (not in hub.json)")
    return problems


def normalize_meta(meta: dict) -> dict:
    out = dict(meta)
    out.setdefault("priority", "normal")
    out.setdefault("due", None)
    out.setdefault("tags", [])
    out.setdefault("refs", [])
    out.setdefault("auto_close", False)
    out["created"] = format_ts(parse_ts(out["created"]))
    return out


def meta_fields(meta: dict):
    for key in FIELD_ORDER:
        value = meta.get(key)
        if key in ("tags", "refs") and not value:
            continue
        if key == "auto_close" and not value:
            continue
        yield key, value


def read_events(path: Path, teams: dict | None = None):
    """Load and sort the events of a handover directory; return (events, problems)."""
    events, problems = [], []
    edir = path / "events"
    if not edir.exists():
        return events, problems
    if not edir.is_dir():
        return events, [(edir, "'events' must be a directory")]
    for f in sorted(edir.iterdir()):
        if f.name.startswith("."):
            continue
        if not f.is_file() or not f.name.endswith(".md"):
            problems.append((f, "unexpected entry in events/ (only .md event files are allowed)"))
            continue
        try:
            fm, body = parse_document(read_text(f), f)
        except FrontMatterError as exc:
            problems.append((f, exc.detail))
            continue
        except (OSError, UnicodeDecodeError) as exc:
            problems.append((f, f"cannot read file: {exc}"))
            continue
        errs = []
        at = parse_ts(fm.get("at"), require_ms=True)
        if "at" not in fm:
            errs.append("missing required field 'at'")
        elif at is None:
            errs.append("field 'at' must be a UTC timestamp with milliseconds like 2026-10-10T14:42:09.364Z")
        team = fm.get("team")
        if "team" not in fm:
            errs.append("missing required field 'team'")
        elif not isinstance(team, str) or not TEAM_RE.match(team):
            errs.append("field 'team' must be a team id")
        elif teams is not None and team not in teams:
            errs.append(f"unknown team '{team}' (not in hub.json)")
        action = fm.get("action")
        if "action" not in fm:
            errs.append("missing required field 'action'")
        elif action not in ACTIONS:
            errs.append(f"unknown action {action!r} (known: {', '.join(ACTIONS)})")
        target = fm.get("target")
        if action == "return":
            if not isinstance(target, str) or not TEAM_RE.match(target):
                errs.append("a 'return' event needs a 'target' team")
            elif teams is not None and target not in teams:
                errs.append(f"unknown target team '{target}' (not in hub.json)")
        if errs:
            problems.extend((f, e) for e in errs)
            continue
        event = {"at": format_ts(at), "team": team, "action": action, "body": body, "file": f.name, "path": f}
        if action == "return":
            event["target"] = target
        events.append(event)
    events.sort(key=lambda e: (e["at"], e["file"]))
    return events, problems


def inspect_handover(hub: Hub, path: Path, archived: bool, teams: dict | None = None):
    """Parse a handover directory; return (Handover or None, [(path, problem), ...])."""
    problems = []
    doc = path / "handover.md"
    meta = body = None
    if not doc.is_file():
        problems.append((doc, "missing handover.md"))
    else:
        try:
            meta, body = parse_document(read_text(doc), doc)
        except FrontMatterError as exc:
            problems.append((doc, exc.detail))
        except (OSError, UnicodeDecodeError) as exc:
            problems.append((doc, f"cannot read file: {exc}"))
        if meta is not None:
            problems.extend((doc, m) for m in check_meta(meta, path.name, teams))
            if not body.strip():
                problems.append((doc, "body is empty"))
    events, event_problems = read_events(path, teams)
    problems.extend(event_problems)
    if problems:
        return None, problems
    return Handover(hub, path, archived, normalize_meta(meta), body, events), []


def copy_attachments(sources, dest_dir: Path) -> list:
    names = []
    if not sources:
        return names
    dest_dir.mkdir(parents=True, exist_ok=True)
    for src in sources:
        target = dest_dir / src.name
        shutil.copy2(src, target)
        names.append(src.name)
    return names


# ---------------------------------------------------------------------------
# lifecycle
# ---------------------------------------------------------------------------

RECIPIENT_ACTIONS = ("ack", "done", "respond", "decline")
SENDER_ACTIONS = ("return", "close")
PARTICIPANT_ACTIONS = ("comment", "seen")
ACTIONS = RECIPIENT_ACTIONS + SENDER_ACTIONS + PARTICIPANT_ACTIONS
STATES = ("pending", "acknowledged", "done", "answered", "declined", "returned")
EXPECT_ACTION = {"ack": "ack", "done": "done", "response": "respond"}
BODY_REQUIRED = {"respond", "decline", "return", "comment"}

# A recipient part is satisfied in these states, per expectation.
SATISFIED = {
    "ack": {"acknowledged", "declined"},
    "done": {"done", "declined"},
    "response": {"answered", "declined"},
}

# (expects, current recipient state, action) -> new recipient state.
TRANSITIONS = {
    ("ack", "pending", "ack"): "acknowledged",
    ("ack", "pending", "decline"): "declined",
    ("ack", "returned", "ack"): "acknowledged",
    ("ack", "returned", "decline"): "declined",

    ("done", "pending", "ack"): "acknowledged",
    ("done", "pending", "done"): "done",
    ("done", "pending", "decline"): "declined",
    ("done", "acknowledged", "done"): "done",
    ("done", "acknowledged", "decline"): "declined",
    ("done", "returned", "ack"): "acknowledged",
    ("done", "returned", "done"): "done",
    ("done", "returned", "decline"): "declined",

    ("response", "pending", "ack"): "acknowledged",
    ("response", "pending", "respond"): "answered",
    ("response", "pending", "decline"): "declined",
    ("response", "acknowledged", "respond"): "answered",
    ("response", "acknowledged", "decline"): "declined",
    ("response", "returned", "ack"): "acknowledged",
    ("response", "returned", "respond"): "answered",
    ("response", "returned", "decline"): "declined",
}


def _body_required(action: str) -> HoError:
    return HoError(
        "body_required",
        f"'{action}' requires a non-empty Markdown body (use -m <text>, --body-file <path> or --body -).",
    )


class Handover:
    def __init__(self, hub: Hub, path: Path, archived: bool, meta: dict, body: str, events: list):
        self.hub = hub
        self.path = path
        self.archived = archived
        self.meta = meta
        self.body = body
        self.events = events
        self.id = meta["id"]
        self.replay()

    @classmethod
    def load(cls, hub: Hub, path: Path, archived: bool) -> "Handover":
        hv, problems = inspect_handover(hub, path, archived)
        if problems:
            where, msg = problems[0]
            more = f" (and {len(problems) - 1} more problem(s))" if len(problems) > 1 else ""
            raise HoError(
                "integrity_error",
                f"{where}: {msg}{more}. Fix the file or run 'ho validate'.",
                EXIT_INTEGRITY,
            )
        return hv

    # -- derived state -----------------------------------------------------

    @property
    def sender(self) -> str:
        return self.meta["from"]

    @property
    def recipients(self) -> list:
        return self.meta["to"]

    @property
    def expects(self) -> str:
        return self.meta["expects"]

    def is_satisfied(self, team: str) -> bool:
        return self.states[team] in SATISFIED[self.expects]

    @property
    def status(self) -> str:
        if self.closed:
            return "closed"
        return "resolved" if all(self.is_satisfied(t) for t in self.recipients) else "open"

    def is_participant(self, team: str) -> bool:
        return team == self.sender or team in self.recipients

    def replay(self) -> None:
        created = self.meta["created"]
        self.states = {t: "pending" for t in self.recipients}
        self.closed = False
        self.last_activity = created
        self.team_latest = {self.sender: created}  # any event, including 'seen'
        self.team_latest_activity = {self.sender: created}  # excluding 'seen'
        self.valid_events = []
        self.invalid_events = []
        for ev in self.events:
            try:
                change = self.check(ev["team"], ev["action"], ev.get("target"), ev["body"], force=True)
            except HoError as exc:
                self.invalid_events.append({"file": ev["file"], "path": str(ev["path"]), "reason": exc.message})
                continue
            self._apply(ev, change)

    def _apply(self, ev: dict, change) -> None:
        if change:
            target, new_state = change
            self.states[target] = new_state
        if ev["action"] == "close":
            self.closed = True
        team, at = ev["team"], ev["at"]
        self.team_latest[team] = max(self.team_latest.get(team, at), at)
        if ev["action"] != "seen":
            self.last_activity = max(self.last_activity, at)
            self.team_latest_activity[team] = max(self.team_latest_activity.get(team, at), at)
        self.valid_events.append(ev)

    def has_updates_for(self, team: str) -> bool:
        own = self.team_latest.get(team)
        return any(
            other != team and (own is None or at > own) for other, at in self.team_latest_activity.items()
        )

    # -- checks ------------------------------------------------------------

    def allowed_actions(self, team: str) -> list:
        if self.closed or self.archived or not self.is_participant(team):
            return []
        if team in self.recipients:
            state = self.states[team]
            acts = [a for a in RECIPIENT_ACTIONS if (self.expects, state, a) in TRANSITIONS]
            return acts + ["comment", "seen"]
        acts = [f"return --to {t}" for t in self.recipients if self.is_satisfied(t)]
        acts.append("close" if self.status == "resolved" else "close --force")
        return acts + ["comment", "seen"]

    def ensure_active(self) -> None:
        if self.archived or self.closed:
            raise HoError(
                "archived",
                f"{self.id} is closed and archived; it accepts no further actions. "
                f"Create a new handover with 'ho new ... --ref {self.id}' instead.",
                EXIT_LIFECYCLE,
            )

    def check(self, team: str, action: str, target: str | None, body: str, force: bool = False):
        """Validate an action against the current state.

        Returns (target_team, new_state) for actions that change a recipient's
        state, otherwise None. Raises HoError if the action is not allowed.
        """
        if self.closed:
            raise HoError("archived", f"{self.id} is already closed.", EXIT_LIFECYCLE)
        has_body = bool(body and body.strip())
        if action in RECIPIENT_ACTIONS:
            if team not in self.recipients:
                raise HoError(
                    "not_recipient",
                    f"Team '{team}' is not a recipient of {self.id} (recipients: {', '.join(self.recipients)}).",
                    EXIT_LIFECYCLE,
                )
            state = self.states[team]
            new_state = TRANSITIONS.get((self.expects, state, action))
            if new_state is None:
                allowed = ", ".join(self.allowed_actions(team))
                hint = ""
                if action in ("done", "respond") and action != EXPECT_ACTION[self.expects]:
                    hint = f" This handover expects '{self.expects}'; use '{EXPECT_ACTION[self.expects]}'."
                raise HoError(
                    "invalid_transition",
                    f"Cannot '{action}' {self.id}: expectation is '{self.expects}' and {team}'s state is "
                    f"'{state}'.{hint} Allowed actions: {allowed}.",
                    EXIT_LIFECYCLE,
                )
            if action in BODY_REQUIRED and not has_body:
                raise _body_required(action)
            return team, new_state
        if action in SENDER_ACTIONS and team != self.sender:
            raise HoError(
                "not_sender",
                f"Only the sender ({self.sender}) can '{action}' {self.id}; '{team}' is not the sender.",
                EXIT_LIFECYCLE,
            )
        if action == "return":
            if not target:
                raise HoError("target_required", "'return' needs --to <recipient team>.")
            if target not in self.recipients:
                raise HoError(
                    "not_recipient",
                    f"Team '{target}' is not a recipient of {self.id} (recipients: {', '.join(self.recipients)}).",
                    EXIT_LIFECYCLE,
                )
            if not self.is_satisfied(target):
                raise HoError(
                    "invalid_transition",
                    f"Cannot return {target}'s part of {self.id}: its state is '{self.states[target]}'; "
                    f"only a satisfied part (acknowledged/done/answered/declined) can be returned.",
                    EXIT_LIFECYCLE,
                )
            if not has_body:
                raise _body_required("return")
            return target, "returned"
        if action == "close":
            if self.status == "open":
                if not force:
                    raise HoError(
                        "invalid_transition",
                        f"{self.id} is still open (recipients: {self._states_text()}). To cancel it, "
                        f"use 'close --force' with a reason (-m <text>).",
                        EXIT_LIFECYCLE,
                    )
                if not has_body:
                    raise HoError("body_required", "Closing an open handover with --force requires a reason (-m <text>).")
            return None
        if action in PARTICIPANT_ACTIONS:
            if not self.is_participant(team):
                raise HoError(
                    "not_participant",
                    f"Team '{team}' is neither the sender nor a recipient of {self.id}.",
                    EXIT_LIFECYCLE,
                )
            if action in BODY_REQUIRED and not has_body:
                raise _body_required(action)
            return None
        raise HoError("unknown_action", f"Unknown action '{action}'.")

    def _states_text(self) -> str:
        return ", ".join(f"{t}={s}" for t, s in self.states.items())

    # -- writes ------------------------------------------------------------

    def next_timestamp(self) -> str:
        """Now, but strictly after every known event so replay order matches check order."""
        now = utc_now()
        now = now.replace(microsecond=now.microsecond // 1000 * 1000)
        latest = max([self.meta["created"]] + [e["at"] for e in self.events])
        latest_dt = parse_ts(latest)
        if now <= latest_dt:
            now = latest_dt + dt.timedelta(milliseconds=1)
        return format_ts(now)

    def write_event(self, team: str, action: str, body: str, target: str | None = None) -> dict:
        edir = self.path / "events"
        edir.mkdir(parents=True, exist_ok=True)
        at = self.next_timestamp()
        stamp = at.replace("-", "").replace(":", "")
        base = f"{stamp}--{team}--{action}"
        name, n = f"{base}.md", 2
        while (edir / name).exists():
            name, n = f"{base}-{n}.md", n + 1
        fields = [("at", at), ("team", team), ("action", action), ("target", target)]
        write_text_atomic(edir / name, serialize_document(fields, body))
        event = {"at": at, "team": team, "action": action, "body": body, "file": name, "path": edir / name}
        if target:
            event["target"] = target
        self.events.append(event)
        return event

    def archive(self, at: str) -> None:
        year = at[:4]
        dest = self.hub.archive_dir / year / self.id
        if dest.exists():
            raise HoError("duplicate_id", f"{dest} already exists; run 'ho validate'.", EXIT_INTEGRITY)
        made_year = not dest.parent.exists()
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.rename(self.path, dest)
        except OSError as exc:
            if made_year:
                with contextlib.suppress(OSError):
                    dest.parent.rmdir()
            raise HoError(
                "io_error",
                f"Could not move {self.path} to {dest}: {exc}. Close any program using these files and retry.",
                EXIT_INTEGRITY,
            )
        self.path = dest
        self.archived = True


def perform_action(hub: Hub, ref: str, team: str, action: str, body: str = "",
                   target: str | None = None, force: bool = False):
    """Run one lifecycle action atomically; return (handover, event, auto_closed)."""
    hv = hub.load(ref)
    hv.ensure_active()
    change = hv.check(team, action, target, body, force=force)
    written = []
    try:
        event = hv.write_event(team, action, body, target)
        written.append(event["path"])
        hv._apply(event, change)
        auto_closed = False
        if action == "close":
            hv.archive(event["at"])
        elif hv.meta.get("auto_close") and hv.status == "resolved":
            close = hv.write_event(hv.sender, "close", "Auto-closed: all recipients are satisfied.\n")
            written.append(close["path"])
            hv._apply(close, None)
            hv.archive(close["at"])
            auto_closed = True
    except BaseException:
        for p in written:
            with contextlib.suppress(OSError):
                p.unlink()
        raise
    return hv, event, auto_closed


def create_handover(hub: Hub, sender: str, to: list, expects: str, title: str, body: str,
                    priority: str = "normal", due: str | None = None, tags=(), refs=(),
                    attachments=(), auto_close: bool = False) -> "Handover":
    created_dt = utc_now()
    hid = hub.new_id(created_dt)
    meta = {
        "id": hid, "title": title, "from": sender, "to": list(to), "expects": expects,
        "priority": priority, "created": format_ts(created_dt), "due": due,
        "tags": list(tags), "refs": list(refs), "auto_close": auto_close,
    }
    hub.active_dir.mkdir(parents=True, exist_ok=True)
    final = hub.active_dir / hid
    tmp = hub.active_dir / f".tmp-{hid}-{secrets.token_hex(4)}"
    try:
        tmp.mkdir()
        with open(tmp / "handover.md", "w", encoding="utf-8", newline="") as fh:
            fh.write(serialize_document(meta_fields(meta), body))
        copy_attachments(list(attachments), tmp / "attachments")
        if final.exists():
            raise HoError("id_collision", f"{final} already exists; try again.", EXIT_INTEGRITY)
        os.rename(tmp, final)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
    return Handover.load(hub, final, archived=False)


# ---------------------------------------------------------------------------
# views
# ---------------------------------------------------------------------------

def summary(hv: Handover, team: str | None = None) -> dict:
    return {
        "id": hv.id,
        "title": hv.meta["title"],
        "from": hv.sender,
        "to": list(hv.recipients),
        "expects": hv.expects,
        "status": hv.status,
        "recipients": dict(hv.states),
        "my_state": hv.states.get(team) if team else None,
        "priority": hv.meta["priority"],
        "due": hv.meta["due"],
        "tags": list(hv.meta["tags"]),
        "refs": list(hv.meta["refs"]),
        "auto_close": hv.meta["auto_close"],
        "created": hv.meta["created"],
        "last_activity": hv.last_activity,
        "archived": hv.archived,
        "path": str((hv.path / "handover.md").resolve()),
    }


def event_view(ev: dict) -> dict:
    out = {"at": ev["at"], "team": ev["team"], "action": ev["action"]}
    if ev.get("target"):
        out["target"] = ev["target"]
    out["body"] = ev["body"]
    out["file"] = ev["file"]
    return out


def show_view(hv: Handover, team: str | None = None) -> dict:
    data = summary(hv, team)
    data["body"] = hv.body
    data["events"] = [event_view(e) for e in hv.events]
    data["invalid_events"] = hv.invalid_events
    if team:
        data["allowed_actions"] = hv.allowed_actions(team)
    return data


def _load_all(hub: Hub, active: bool = True, archive: bool = False):
    handovers, warnings = [], []
    for path, archived in hub.iter_handover_dirs(active=active, archive=archive):
        hv, problems = inspect_handover(hub, path, archived)
        if problems:
            warnings.extend({"path": str(p), "message": m} for p, m in problems)
        else:
            handovers.append(hv)
    return handovers, warnings


def sort_key(s: dict):
    return (PRIORITY_RANK.get(s["priority"], 1), s["due"] or "9999-99-99", s["created"], s["id"])


def inbox_view(hub: Hub, team: str) -> dict:
    handovers, warnings = _load_all(hub, active=True)
    groups = {"action_required": [], "to_review": [], "updates": []}
    for hv in handovers:
        if hv.status == "closed" or not hv.is_participant(team):
            continue
        if team in hv.recipients and not hv.is_satisfied(team):
            group = "action_required"
        elif team == hv.sender and hv.status == "resolved":
            group = "to_review"
        elif hv.has_updates_for(team):
            group = "updates"
        else:
            continue
        groups[group].append(summary(hv, team))
    for items in groups.values():
        items.sort(key=sort_key)
    return {"team": team, **groups, "warnings": warnings}


def list_view(hub: Hub, status: str = "active", sender: str | None = None, to: str | None = None,
              expects: str | None = None, tags=(), team: str | None = None) -> dict:
    want_active = status in ("active", "open", "resolved", "all")
    want_archive = status in ("closed", "all")
    handovers, warnings = _load_all(hub, active=want_active, archive=want_archive)
    items = []
    for hv in handovers:
        st = hv.status
        if status == "active" and st == "closed":
            continue
        if status in ("open", "resolved", "closed") and st != status:
            continue
        if sender and hv.sender != sender:
            continue
        if to and to not in hv.recipients:
            continue
        if expects and hv.expects != expects:
            continue
        if any(t not in hv.meta["tags"] for t in tags):
            continue
        items.append(summary(hv, team))
    items.sort(key=lambda s: (s["created"], s["id"]))
    return {"items": items, "warnings": warnings}


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------

def validate_hub(hub: Hub) -> dict:
    errors = []
    checked = 0
    seen: dict = {}

    def err(path, message):
        errors.append({"path": str(path), "message": message})

    def check_dir(path: Path, archived: bool):
        nonlocal checked
        checked += 1
        seen.setdefault(path.name, []).append((path, archived))
        hv, problems = inspect_handover(hub, path, archived, teams=hub.teams)
        for p, m in problems:
            err(p, m)
        if hv is None:
            return
        for inv in hv.invalid_events:
            err(inv["path"], f"event is not valid at its position in the log: {inv['reason']}")
        if not archived and hv.closed:
            err(path, "handover is closed but still in active/ (move it to archive/<YYYY>/)")
        if archived and not hv.closed:
            err(path, "archived handover has no valid 'close' event")

    if hub.active_dir.exists():
        for p in sorted(hub.active_dir.iterdir()):
            if p.name.startswith("."):
                continue
            if not p.is_dir():
                err(p, "unexpected file in active/ (only handover directories are allowed)")
            elif not ID_RE.match(p.name):
                err(p, "directory name is not a handover ID (HO-YYYYMMDD-xxxx)")
            else:
                check_dir(p, False)
    if hub.archive_dir.exists():
        for year in sorted(hub.archive_dir.iterdir()):
            if year.name.startswith("."):
                continue
            if not year.is_dir() or not YEAR_RE.match(year.name):
                err(year, "archive/ must only contain year directories (YYYY)")
                continue
            for p in sorted(year.iterdir()):
                if p.name.startswith("."):
                    continue
                if not p.is_dir() or not ID_RE.match(p.name):
                    err(p, "not a handover directory (HO-YYYYMMDD-xxxx)")
                else:
                    check_dir(p, True)

    for hid, places in seen.items():
        if len(places) < 2:
            continue
        actives = [p for p, a in places if not a]
        archives = [p for p, a in places if a]
        if actives and archives:
            arch = archives[0]
            arch_events = {f.name for f in (arch / "events").glob("*.md")} if (arch / "events").is_dir() else set()
            for act in actives:
                stray = sorted(
                    f.name for f in (act / "events").glob("*.md") if f.name not in arch_events
                ) if (act / "events").is_dir() else []
                hint = (
                    f"; events not in the archived copy: {', '.join(stray)} - move them into {arch / 'events'} "
                    f"and delete {act}" if stray else f"; it holds no extra events, delete {act}"
                )
                err(act, f"duplicate ID {hid}: also archived at {arch}{hint}")
            for extra in archives[1:]:
                err(extra, f"duplicate ID {hid}: also archived at {arch}")
        else:
            for p, _ in places[1:]:
                err(p, f"duplicate ID {hid}: also at {places[0][0]}")
    return {"ok": not errors, "checked": checked, "errors": errors}


# ---------------------------------------------------------------------------
# sync
# ---------------------------------------------------------------------------

_SYNC_EVENT_RE = re.compile(
    r"^(?:active|archive/\d{4})/(HO-\d{8}-[a-z2-7]{4})/events/"
    r"(\d{8}T\d{6}\.\d{3}Z)--([a-z0-9][a-z0-9-]*)--([a-z]+)(?:-\d+)?\.md$"
)
_SYNC_DOC_RE = re.compile(r"^(?:active|archive/\d{4})/(HO-\d{8}-[a-z2-7]{4})/handover\.md$")


# Everything in the hub except its root lock file, even if the hub's .gitignore lost its line.
# The bracket keeps the exclude a pattern: a literal pathspec naming an ignored file makes
# 'git add' fail ("paths are ignored by one of your .gitignore files").
_SYNC_PATHSPEC = (".", ":(exclude,glob).ho.loc[k]")


def _git(root: Path, env, *args, check: bool = True):
    try:
        proc = subprocess.run(
            ["git", *args], cwd=root, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
    except FileNotFoundError:
        raise HoError("git_not_found", "git is not installed or not on PATH.")
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise HoError("git_error", f"'git {' '.join(args)}' failed:\n{detail}")
    return proc


def sync_message(hub: Hub, name_status: str):
    news, events = [], []
    for line in name_status.splitlines():
        parts = line.split("\t")
        if len(parts) < 2 or parts[0] != "A":
            continue
        path = parts[-1].replace("\\", "/")
        m = _SYNC_EVENT_RE.match(path)
        if m:
            hid, stamp, team, action = m.groups()
            events.append((stamp, f"{team} {action} {hid}"))
            continue
        m = _SYNC_DOC_RE.match(path)
        if m:
            sender = None
            with contextlib.suppress(Exception):
                sender = parse_document(read_text(hub.root / path), path)[0].get("from")
            news.append(f"{sender} new {m.group(1)}" if sender else f"new {m.group(1)}")
    parts = news + [text for _, text in sorted(events)]
    if not parts:
        return "ho: update hub", []
    subject = "ho: " + "; ".join(parts[:5]) + (f"; +{len(parts) - 5} more" if len(parts) > 5 else "")
    return subject, parts


def sync_hub(hub: Hub, env) -> dict:
    root = hub.root
    inside = _git(root, env, "rev-parse", "--is-inside-work-tree", check=False)
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        raise HoError(
            "not_a_git_repo",
            f"The hub at {root} is not inside a git working tree, so it cannot be synced. "
            f"Run 'git init' there and add a shared remote (or share the folder another way).",
        )
    ensure_gitignore(root)
    has_upstream = _git(root, env, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", check=False).returncode == 0
    remotes = _git(root, env, "remote").stdout.split()
    notes = []
    pulled = pushed = False
    if has_upstream:
        _git(root, env, "pull", "--rebase", "--autostash")
        pulled = True
    _git(root, env, "add", "-A", "--", *_SYNC_PATHSPEC)
    name_status = _git(root, env, "diff", "--cached", "--name-status", "--relative", "-M", "--", *_SYNC_PATHSPEC).stdout
    committed = False
    message, details = None, []
    if name_status.strip():
        message, details = sync_message(hub, name_status)
        args = ["commit", "-m", message]
        if len(details) > 1:
            args += ["-m", "\n".join(f"- {d}" for d in details)]
        _git(root, env, *args, "--", *_SYNC_PATHSPEC)
        committed = True
    if has_upstream:
        if _git(root, env, "push", check=False).returncode != 0:
            _git(root, env, "pull", "--rebase", "--autostash")
            _git(root, env, "push")
        pushed = True
    elif remotes:
        _git(root, env, "push", "-u", remotes[0], "HEAD")
        pushed = True
    else:
        notes.append("no git remote configured; changes were committed locally only")
    return {
        "pulled": pulled, "committed": committed, "pushed": pushed,
        "commit_message": message, "actions": details, "notes": notes,
    }


# ---------------------------------------------------------------------------
# hub lock
# ---------------------------------------------------------------------------

LOCK_NAME = ".ho.lock"
GITIGNORE_NAME = ".gitignore"
LOCK_TIMEOUT_DEFAULT = 300.0
LOCK_POLL_SECONDS = 0.1


def lock_timeout(env) -> float:
    """The wait bound in seconds from HO_LOCK_TIMEOUT (default 300; 0 = do not wait)."""
    raw = (env.get("HO_LOCK_TIMEOUT") or "").strip()
    if not raw:
        return LOCK_TIMEOUT_DEFAULT
    try:
        value = float(raw)
    except ValueError:
        value = -1.0
    if not value >= 0 or value == float("inf"):  # also rejects nan
        raise HoError("usage", f"HO_LOCK_TIMEOUT must be a non-negative number of seconds, got '{raw}'.")
    return value


def _try_lock(fd: int) -> bool:
    if os.name == "nt":
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    import fcntl
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(fd: int) -> None:
    if os.name == "nt":
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)


@contextlib.contextmanager
def hub_lock(root: Path, env, stderr):
    """Hold the exclusive hub lock on <root>/.ho.lock, waiting up to HO_LOCK_TIMEOUT seconds.

    The lock is an OS lock on an open handle (msvcrt on Windows, flock elsewhere), so the kernel
    releases it when the process ends, even on a crash. The file itself carries no meaning and is
    never deleted (a waiter may already have it open).
    """
    timeout = lock_timeout(env)
    path = root / LOCK_NAME
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        deadline = time.monotonic() + timeout
        noted = False
        while not _try_lock(fd):
            if time.monotonic() >= deadline:
                raise HoError(
                    "hub_locked",
                    f"The hub is locked by another ho process ({path}) and was not released within "
                    f"{timeout:g} s (HO_LOCK_TIMEOUT). Nothing was changed; run the command again later. "
                    f"Do not delete {LOCK_NAME}: the lock is released when that process ends.",
                )
            if not noted:
                stderr.write(f"ho: hub is locked by another ho process; waiting up to {timeout:g} s...\n")
                with contextlib.suppress(Exception):
                    stderr.flush()
                noted = True
            time.sleep(min(LOCK_POLL_SECONDS, max(0.0, deadline - time.monotonic())))
        try:
            yield path
        finally:
            with contextlib.suppress(OSError):
                _unlock(fd)
    finally:
        os.close(fd)


def ensure_gitignore(root: Path) -> bool:
    """Append .ho.lock to <root>/.gitignore unless a line already equals it; True if written."""
    path = root / GITIGNORE_NAME
    text = read_text(path) if path.is_file() else ""
    if any(line.strip() == LOCK_NAME for line in text.splitlines()):
        return False
    if text and not text.endswith("\n"):
        text += "\n"
    write_text_atomic(path, text + LOCK_NAME + "\n")
    return True


# ---------------------------------------------------------------------------
# cli
# ---------------------------------------------------------------------------

class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise HoError("usage", f"{message} (see '{self.prog} -h')")


class Ctx:
    def __init__(self, stdin, stdout, stderr, env, cwd: Path):
        self.stdin, self.stdout, self.stderr, self.env, self.cwd = stdin, stdout, stderr, env, cwd

    def path(self, value: str) -> Path:
        return (self.cwd / value).resolve()


def find_hub(ctx: Ctx, args) -> Hub:
    source = None
    if getattr(args, "hub", None):
        root, source = ctx.path(args.hub), "--hub"
    elif ctx.env.get("HO_HUB"):
        root, source = ctx.path(ctx.env["HO_HUB"]), "HO_HUB"
    if source:
        if not (root / "hub.json").is_file():
            raise HoError("hub_not_found", f"No hub.json in {root} (from {source}).", EXIT_NOT_FOUND)
        return Hub(root)
    for candidate in [ctx.cwd, *ctx.cwd.parents]:
        if (candidate / "hub.json").is_file():
            return Hub(candidate)
    raise HoError(
        "hub_not_found",
        "No hub found. Pass --hub <path>, set HO_HUB=<path>, or run ho inside the hub directory "
        "(a directory containing hub.json, or below it).",
        EXIT_NOT_FOUND,
    )


def acting_team(ctx: Ctx, args, hub: Hub, required: bool = True) -> str | None:
    team = getattr(args, "team", None) or ctx.env.get("HO_TEAM")
    if not team:
        if required:
            raise HoError(
                "team_required",
                "This command acts for a team: pass --team <id> or set HO_TEAM=<id>. "
                f"Registered teams: {', '.join(hub.teams) or '(none)'}.",
            )
        return None
    return hub.require_team(team.strip())


def _read_stdin(ctx: Ctx) -> str:
    stream = ctx.stdin
    raw = stream.buffer.read() if hasattr(stream, "buffer") else stream.read()
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise HoError("invalid_body", "stdin is not valid UTF-8.")
    return raw


def _read_file(ctx: Ctx, value: str) -> str:
    path = ctx.path(value)
    if not path.is_file():
        raise HoError("file_not_found", f"Body file not found: {path}")
    try:
        return path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HoError("invalid_body", f"{path} is not valid UTF-8.")


def read_body(ctx: Ctx, args) -> str:
    if getattr(args, "body_stdin", None) is not None:
        if args.body_stdin != "-":
            raise HoError("usage", "--body only accepts '-' (read stdin); use -m <text> or --body-file <path>.")
        return _read_stdin(ctx)
    if getattr(args, "body_file", None):
        return _read_file(ctx, args.body_file)
    if getattr(args, "message", None) is not None:
        return args.message if args.message.endswith("\n") or not args.message else args.message + "\n"
    return ""


def _split_list(values) -> list:
    out = []
    for value in values or []:
        for part in value.split(","):
            part = part.strip()
            if part and part not in out:
                out.append(part)
    return out


# -- text rendering ---------------------------------------------------------

def _item_line(s: dict, team: str | None = None, group: str | None = None) -> str:
    prio = f"[{s['priority']}] " if s["priority"] != "normal" else ""
    details = [f"from {s['from']}", f"expects {s['expects']}"]
    if group == "to_review" or (team is None or team == s["from"]):
        details.append(", ".join(f"{t}: {st}" for t, st in s["recipients"].items()))
    if s.get("my_state"):
        details.append(f"you: {s['my_state']}")
    if s["due"]:
        details.append(f"due {s['due']}")
    return f"  {s['id']}  {prio}{s['title']}  ({'; '.join(details)})"


def render_inbox(data: dict) -> str:
    out = [f"Inbox for {data['team']}"]
    for key, label in (("action_required", "Action required"), ("to_review", "To review"), ("updates", "Updates")):
        items = data[key]
        out.append(f"{label} ({len(items)}):")
        out.extend(_item_line(s, data["team"], key) for s in items)
        if not items:
            out.append("  (none)")
    return "\n".join(out)


def render_list(data: dict) -> str:
    if not data["items"]:
        return "No handovers."
    return "\n".join(
        f"{s['id']}  {s['status']:<8}  {s['from']} -> {', '.join(s['to'])}  {s['expects']:<8}  {s['title']}"
        for s in data["items"]
    )


def render_show(d: dict) -> str:
    out = [
        f"{d['id']}  {d['title']}",
        f"From: {d['from']}  To: {', '.join(d['to'])}  Expects: {d['expects']}",
        f"Status: {d['status']}{' (archived)' if d['archived'] else ''}  Priority: {d['priority']}"
        f"  Due: {d['due'] or '-'}  Created: {d['created']}",
    ]
    if d["tags"] or d["refs"]:
        out.append(f"Tags: {', '.join(d['tags']) or '-'}  Refs: {', '.join(d['refs']) or '-'}")
    out.append("Recipients: " + ", ".join(f"{t}={s}" for t, s in d["recipients"].items()))
    if d.get("allowed_actions") is not None:
        out.append(f"You can: {', '.join(d['allowed_actions']) or '(nothing)'}")
    out.append(f"Path: {d['path']}")
    out += ["", d["body"].rstrip("\n"), "", f"--- Events ({len(d['events'])}) ---"]
    for ev in d["events"]:
        target = f" -> {ev['target']}" if ev.get("target") else ""
        out.append(f"[{ev['at']}] {ev['team']} {ev['action']}{target}")
        if ev["body"].strip():
            out.append(ev["body"].rstrip("\n"))
        out.append("")
    for inv in d["invalid_events"]:
        out.append(f"! ignored {inv['file']}: {inv['reason']}")
    return "\n".join(out).rstrip("\n")


def render_write(data: dict, team: str) -> str:
    ev = data["event"]
    target = f" {ev['target']}" if ev.get("target") else ""
    text = f"{team}: {ev['action']}{target} {data['id']} -> status {data['status']}"
    if data.get("my_state"):
        text += f", {team} is {data['my_state']}"
    if data["archived"]:
        text += f"\n{'Auto-closed' if data.get('auto_closed') else 'Closed'} and archived: {data['path']}"
    return text


# -- commands -----------------------------------------------------------------

def cmd_init(ctx: Ctx, args):
    root = ctx.path(args.path or args.hub or ".")
    if (root / "hub.json").exists():
        raise HoError("hub_exists", f"A hub already exists at {root} (hub.json found).")
    teams = []
    for spec in _split_list([args.teams] if args.teams else []):
        tid, _, name = spec.partition("=")
        tid, name = tid.strip(), name.strip() or tid.strip().capitalize()
        if not TEAM_RE.match(tid):
            raise HoError("invalid_team", f"Invalid team id '{tid}': use lowercase letters, digits and hyphens.")
        if any(t["id"] == tid for t in teams):
            raise HoError("invalid_team", f"Team '{tid}' is listed twice.")
        teams.append({"id": tid, "name": name})
    template = Path(__file__).resolve().parent / "ho_templates" / "AGENTS.md"
    if not template.is_file():
        raise HoError("template_missing", f"Agent guide template not found: {template}", EXIT_INTEGRITY)
    team_lines = "\n".join(f"- `{t['id']}` - {t['name']}" for t in teams) or "- (none yet: add them to hub.json)"
    guide = read_text(template).replace("{{teams}}", team_lines)
    root.mkdir(parents=True, exist_ok=True)
    (root / "active").mkdir(exist_ok=True)
    (root / "archive").mkdir(exist_ok=True)
    ensure_gitignore(root)
    agents = root / "AGENTS.md"
    if not agents.exists():
        with open(agents, "w", encoding="utf-8", newline="") as fh:
            fh.write(guide)
    with open(root / "hub.json", "w", encoding="utf-8", newline="") as fh:
        fh.write(json.dumps({"schema_version": HUB_FORMAT_VERSION, "teams": teams}, indent=2, ensure_ascii=False) + "\n")
    data = {"hub": str(root), "teams": teams}
    text = f"Initialized hub at {root} with teams: {', '.join(t['id'] for t in teams) or '(none)'}"
    return data, text


def cmd_teams(ctx: Ctx, args):
    hub = find_hub(ctx, args)
    teams = [{"id": k, "name": v} for k, v in hub.teams.items()]
    text = "\n".join(f"{t['id']:<12} {t['name']}" for t in teams) or "No teams registered."
    return {"hub": str(hub.root), "teams": teams}, text


def cmd_new(ctx: Ctx, args):
    hub = find_hub(ctx, args)
    team = acting_team(ctx, args, hub)
    to = _split_list(args.to)
    if not to:
        raise HoError("usage", "--to needs at least one recipient team.")
    for t in to:
        hub.require_team(t, "recipient team")
    if team in to:
        raise HoError("usage", f"The sender ({team}) cannot also be a recipient.")
    title = (args.title or "").strip()
    if not title or "\n" in title:
        raise HoError("usage", "--title must be a non-empty single line.")
    if args.due and not valid_date(args.due):
        raise HoError("usage", f"--due must be a date like 2026-10-31, got '{args.due}'.")
    tags = _split_list(args.tag)
    refs = [hub.resolve_id(r)[0].name for r in _split_list(args.ref)]
    attachments = []
    for a in args.attach or []:
        p = ctx.path(a)
        if not p.is_file():
            raise HoError("file_not_found", f"Attachment not found (only files are supported): {p}")
        if any(x.name == p.name for x in attachments):
            raise HoError("usage", f"Two attachments are named '{p.name}'.")
        attachments.append(p)
    body = read_body(ctx, args)
    if not body.strip():
        raise HoError("empty_body", "The handover body is empty: pass --body-file <path>, --body - or -m <text>.")
    hv = create_handover(
        hub, team, to, args.expects, title, body, priority=args.priority, due=args.due,
        tags=tags, refs=refs, attachments=attachments, auto_close=args.auto_close,
    )
    data = summary(hv, team)
    text = f"Created {hv.id} for {', '.join(to)} (expects {args.expects})\n{data['path']}"
    return data, text


def cmd_inbox(ctx: Ctx, args):
    hub = find_hub(ctx, args)
    team = acting_team(ctx, args, hub)
    data = inbox_view(hub, team)
    return data, render_inbox(data)


def cmd_list(ctx: Ctx, args):
    hub = find_hub(ctx, args)
    team = acting_team(ctx, args, hub, required=False)
    for t in (args.sender, args.to):
        if t:
            hub.require_team(t)
    data = list_view(hub, args.status, args.sender, args.to, args.expects, _split_list(args.tag), team)
    return data, render_list(data)


def cmd_show(ctx: Ctx, args):
    hub = find_hub(ctx, args)
    team = acting_team(ctx, args, hub, required=False)
    hv = hub.load(args.id)
    data = show_view(hv, team)
    return data, render_show(data)


def _write_result(hv: Handover, event: dict, auto_closed: bool, team: str):
    data = summary(hv, team)
    data["event"] = event_view(event)
    data["auto_closed"] = auto_closed
    return data, render_write(data, team)


def cmd_action(ctx: Ctx, args):
    hub = find_hub(ctx, args)
    team = acting_team(ctx, args, hub)
    body = "" if args.command == "seen" else read_body(ctx, args)
    hv, event, auto = perform_action(hub, args.id, team, args.command, body)
    return _write_result(hv, event, auto, team)


def cmd_return(ctx: Ctx, args):
    hub = find_hub(ctx, args)
    team = acting_team(ctx, args, hub)
    hub.require_team(args.to, "target team")
    hv, event, auto = perform_action(hub, args.id, team, "return", read_body(ctx, args), target=args.to)
    return _write_result(hv, event, auto, team)


def cmd_close(ctx: Ctx, args):
    hub = find_hub(ctx, args)
    team = acting_team(ctx, args, hub)
    hv, event, auto = perform_action(hub, args.id, team, "close", read_body(ctx, args), force=args.force)
    return _write_result(hv, event, auto, team)


def cmd_validate(ctx: Ctx, args):
    hub = find_hub(ctx, args)
    data = validate_hub(hub)
    if data["ok"]:
        return data, f"OK: {data['checked']} handover(s) checked, no problems."
    lines = [f"{e['path']}: {e['message']}" for e in data["errors"]]
    lines.append(f"{len(data['errors'])} problem(s) in {data['checked']} handover(s).")
    return data, "\n".join(lines), EXIT_INTEGRITY


def cmd_sync(ctx: Ctx, args):
    hub = find_hub(ctx, args)
    data = sync_hub(hub, ctx.env)
    lines = [
        "Pulled remote changes." if data["pulled"] else "No upstream to pull from.",
        f"Committed: {data['commit_message']}" if data["committed"] else "Nothing to commit.",
        "Pushed." if data["pushed"] else "Not pushed.",
    ]
    lines += [f"Note: {n}" for n in data["notes"]]
    return data, "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--hub", help="hub directory (default: $HO_HUB, else search upwards for hub.json)")
    common.add_argument("--team", help="acting team id (default: $HO_TEAM)")
    common.add_argument("--json", action="store_true", help="print one JSON document to stdout")

    body = argparse.ArgumentParser(add_help=False)
    group = body.add_mutually_exclusive_group()
    group.add_argument("-m", "--message", help="Markdown body as text")
    group.add_argument("--body-file", help="read the Markdown body from a UTF-8 file")
    group.add_argument("--body", dest="body_stdin", metavar="-", help="'-' reads the Markdown body from stdin")

    parser = _Parser(prog="ho", description="Team handover hub: pass Markdown handovers between teams.")
    parser.add_argument("--version", action="store_true",
                        help="print the package version and JSON contract version, then exit")
    parser.add_argument("--json", dest="version_json", action="store_true",
                        help="with --version: print it as one JSON document")
    sub = parser.add_subparsers(dest="command", metavar="<command>", parser_class=_Parser)

    p = sub.add_parser("init", parents=[common], help="create a hub")
    p.add_argument("path", nargs="?", help="directory for the hub (default: --hub or current directory)")
    p.add_argument("--teams", help="teams as id=Name,id=Name")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("teams", parents=[common], help="list registered teams")
    p.set_defaults(func=cmd_teams)

    p = sub.add_parser("new", parents=[common, body], help="create a handover from the acting team")
    p.add_argument("--to", action="append", required=True, help="recipient team(s), comma-separated or repeated")
    p.add_argument("--expects", required=True, choices=EXPECTATIONS)
    p.add_argument("--title", required=True)
    p.add_argument("--priority", choices=PRIORITIES, default="normal")
    p.add_argument("--due", help="due date YYYY-MM-DD")
    p.add_argument("--tag", action="append", help="tag (repeatable or comma-separated)")
    p.add_argument("--ref", action="append", help="related handover ID (repeatable)")
    p.add_argument("--attach", action="append", help="file to copy into attachments/ (repeatable)")
    p.add_argument("--auto-close", action="store_true", help="close automatically once all recipients are satisfied")
    p.set_defaults(func=cmd_new, locked=True)

    p = sub.add_parser("inbox", parents=[common], help="what needs the acting team's attention")
    p.set_defaults(func=cmd_inbox)

    p = sub.add_parser("list", parents=[common], help="list handovers (default: active)")
    p.add_argument("--status", choices=("active", "open", "resolved", "closed", "all"), default="active")
    p.add_argument("--from", dest="sender", help="sender team")
    p.add_argument("--to", help="recipient team")
    p.add_argument("--expects", choices=EXPECTATIONS)
    p.add_argument("--tag", action="append")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("show", parents=[common], help="show a handover with its full history")
    p.add_argument("id")
    p.set_defaults(func=cmd_show)

    helps = {
        "ack": "recipient: confirm the handover was noted / taken in progress",
        "done": "recipient: report the work as done (expects done)",
        "respond": "recipient: answer with Markdown (expects response)",
        "decline": "recipient: decline with a reason",
        "comment": "participant: add a comment",
        "seen": "participant: mark all activity as read",
    }
    for name, text in helps.items():
        p = sub.add_parser(name, parents=[common] if name == "seen" else [common, body], help=text)
        p.add_argument("id")
        p.set_defaults(func=cmd_action, locked=True)

    p = sub.add_parser("return", parents=[common, body], help="sender: send a recipient's part back")
    p.add_argument("id")
    p.add_argument("--to", required=True, help="recipient team whose part is returned")
    p.set_defaults(func=cmd_return, locked=True)

    p = sub.add_parser("close", parents=[common, body], help="sender: close and archive")
    p.add_argument("id")
    p.add_argument("--force", action="store_true", help="close an open handover (requires a reason)")
    p.set_defaults(func=cmd_close, locked=True)

    p = sub.add_parser("validate", parents=[common], help="check hub integrity")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("sync", parents=[common], help="git pull --rebase, commit hub changes, push")
    p.set_defaults(func=cmd_sync, locked=True)
    return parser


def _run_locked(ctx: Ctx, args):
    """Run a writing command or sync under the hub lock (see the hub-lock spec).

    The hub is located first (a missing hub fails without locking); the command itself then reads
    the hub again, after the previous writer has finished. The .gitignore line is added only after
    the command succeeded, so a failed command leaves the hub unchanged (sync adds it itself,
    before staging).
    """
    root = find_hub(ctx, args).root
    with hub_lock(root, ctx.env, ctx.stderr):
        result = args.func(ctx, args)
        if args.func is not cmd_sync:
            ensure_gitignore(root)
    return result


def _dump(data: dict) -> str:
    return json.dumps({"schema_version": SCHEMA_VERSION, **data}, ensure_ascii=True, indent=2)


def main(argv=None, *, stdin=None, stdout=None, stderr=None, env=None, cwd=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    ctx = Ctx(
        stdin if stdin is not None else sys.stdin,
        stdout if stdout is not None else sys.stdout,
        stderr if stderr is not None else sys.stderr,
        dict(os.environ if env is None else env),
        Path(cwd).resolve() if cwd is not None else Path.cwd(),
    )
    want_json = "--json" in argv
    try:
        parser = build_parser()
        try:
            with contextlib.redirect_stdout(ctx.stdout):
                args = parser.parse_args(argv)
        except SystemExit as exc:  # -h / --help
            return exc.code if isinstance(exc.code, int) else EXIT_OK
        if args.version:
            if args.command:
                raise HoError("usage", "--version takes no command (see 'ho -h')")
            data = {"version": __version__, "contract": SCHEMA_VERSION, "features": list(FEATURES)}
            if want_json:
                ctx.stdout.write(_dump(data) + "\n")
            else:
                ctx.stdout.write(f"ho {__version__} (contract {SCHEMA_VERSION})\n")
            return EXIT_OK
        if args.version_json:
            raise HoError("usage", "--json before the command is only valid with --version (see 'ho -h')")
        if not args.command:
            parser.print_help(ctx.stderr)
            return EXIT_USAGE
        if getattr(args, "locked", False):
            result = _run_locked(ctx, args)
        else:
            result = args.func(ctx, args)
        data, text = result[0], result[1]
        code = result[2] if len(result) > 2 else EXIT_OK
        if want_json:
            ctx.stdout.write(_dump(data) + "\n")
        if code:
            ctx.stderr.write(text + "\n")
        elif not want_json:
            ctx.stdout.write(text + "\n")
        warnings = data.get("warnings") if isinstance(data, dict) else None
        for w in warnings or []:
            ctx.stderr.write(f"warning: {w['path']}: {w['message']}\n")
        return code
    except HoError as exc:
        ctx.stderr.write(f"ho: error: {exc.message}\n")
        if want_json:
            ctx.stdout.write(_dump({"error": {"code": exc.code, "message": exc.message}}) + "\n")
        return exc.exit_code


if __name__ == "__main__":
    # Consoles get Unicode through the console API; pipes and files get UTF-8
    # (Windows would otherwise use the ANSI code page and lose non-Latin text).
    for _stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError, OSError):
            if _stream.isatty():
                _stream.reconfigure(errors="replace")
            else:
                _stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
