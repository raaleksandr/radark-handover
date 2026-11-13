## 1. Project setup

- [x] 1.1 Create `ho.py` skeleton (Python 3.10+, stdlib only) with sections for store, lifecycle, views and CLI, plus `schema_version = 1` constant
- [x] 1.2 Add launchers `ho.cmd` (Windows) and `ho` (POSIX sh) that run `ho.py` with the available Python and forward all arguments
- [x] 1.3 Create `tests/` with a unittest helper that builds a temporary hub and runs CLI commands in-process, capturing stdout/stderr/exit code

## 2. Store: files and formats

- [x] 2.1 Implement the restricted front-matter parser/serializer (scalars, quoted strings, booleans, flow lists) with errors that name the file and line; unit-test round trips and rejections
- [x] 2.2 Implement hub resolution (`--hub`, `HO_HUB`, upward search for `hub.json`) and `hub.json` loading/validation of the team registry
- [x] 2.3 Implement ID generation `HO-<YYYYMMDD>-<4 base32>` with collision retry, and ID/unique-suffix lookup across `active/` and `archive/<YYYY>/`
- [x] 2.4 Implement reading/writing `handover.md` with required-field validation and non-empty body check
- [x] 2.5 Implement event file naming (`<ts ms>--<team>--<action>[-n].md`), atomic write via temp file + `os.replace`, and chronological event loading
- [x] 2.6 Implement attachments copy into `attachments/` and archiving by directory move to `archive/<YYYY>/<ID>/`, with rollback of the close event on failure

## 3. Lifecycle

- [x] 3.1 Define the transition table `(expects, state, action) → state` with body-required flags and the satisfied-state rules
- [x] 3.2 Implement event replay producing per-recipient states, overall status (`open`/`resolved`/`closed`), last activity, and per-team latest-event times; skip and collect events invalid at their position
- [x] 3.3 Implement recipient actions (`ack`, `done`, `respond`, `decline`) with recipient and transition checks and helpful errors listing allowed actions
- [x] 3.4 Implement sender actions `return --to` (only from satisfied state, body required) and `close` (resolved, or `--force` + reason; sender only), plus `comment` and `seen` for participants
- [x] 3.5 Implement auto-close: when an action makes an `auto_close` handover resolved, write a sender `close` event and archive in the same command
- [x] 3.6 Reject any action on archived handovers with a hint to create a new handover using `--ref`
- [x] 3.7 Unit-test every scenario in `specs/handover-lifecycle/spec.md`

## 4. Views

- [x] 4.1 Implement handover summary dict (id, title, from, to, expects, status, recipients, my_state, priority, due, created, last_activity, path)
- [x] 4.2 Implement `inbox` grouping (`action_required`, `to_review`, `updates`) and ordering (priority, due, created)
- [x] 4.3 Implement `list` filters (status, from, to, expects, tag; default active) and `show` (body + full event history), including archived items

## 5. CLI

- [x] 5.1 Wire argparse subcommands: `init`, `teams`, `new`, `inbox`, `list`, `show`, `ack`, `done`, `respond`, `decline`, `comment`, `seen`, `return`, `close`, `validate`, `sync`
- [x] 5.2 Implement team identity resolution (`--team`, `HO_TEAM`) and body input (`-m`, `--body-file`, `--body -`, UTF-8 verbatim)
- [x] 5.3 Implement `--json` output for every command (single JSON document, `schema_version`, error object) and concise human-readable text output
- [x] 5.4 Implement exit codes 0/1/2/3/4 and guarantee failed commands leave the hub unchanged
- [x] 5.5 Implement `init` (creates `hub.json`, `active/`, `archive/`, `AGENTS.md` from `templates/AGENTS.md`; refuses if hub exists) and `teams`
- [x] 5.6 Implement `validate` covering front matter, required fields, unknown teams/actions, invalid-in-order events and duplicate IDs (active + archive), each with file path
- [x] 5.7 Implement `sync` (pull --rebase --autostash, add hub, commit with generated summary message, push; error when not in a git repo)
- [x] 5.8 CLI tests for every scenario in `specs/handover-cli/spec.md` and `specs/handover-store/spec.md`, including a two-clone git test showing concurrent responses merge without conflict

## 6. Documentation

- [x] 6.1 Write `templates/AGENTS.md`: set `HO_TEAM`, `ho sync` first and last, `ho inbox --json`, `ho show <ID> --json`, expectation→action mapping (`ack`→`ack`, `done`→`done`, `response`→`respond`), how to return/close, and how to create a handover from a Markdown file
- [x] 6.2 Write `README.md`: concept, hub setup as a shared git repo, file format (including front-matter subset), command reference with examples, and a full product→backend→product walkthrough
- [x] 6.3 Run the walkthrough end to end on Windows (PowerShell) and in a POSIX shell and confirm outputs match the README
