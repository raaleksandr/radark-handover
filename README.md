# radark-handover — `ho`, the team handover hub

A lightweight, file-based handover CLI for teams of AI agents (and people). It is the
communication layer of [radark-factory](https://github.com/raaleksandr/radark-factory),
but has no dependency on it and works on its own.

Work moves between teams (strategy, product, frontend, backend, mobile, …) as
Markdown documents. `ho` gives those **handovers** a shared home that tracks who
owes what: was it noted, is it done, did the answer come back, does the sender
accept it?

- **Plain files.** A hub is a directory, normally its own git repository shared
  by all teams. Everything is readable Markdown; no database, no server.
- **One CLI, made for agents.** `ho` runs on demand (Python 3.10+, standard
  library only). Every command supports `--json` with a stable schema, so an AI
  agent can ask "what is waiting for my team?" and act with one command.
- **Merge-friendly.** Each action is a new file, so teams working at the same
  time on different machines never get git conflicts on handover data.
- **Small active list.** Closed handovers move to `archive/<year>/`.

## Concepts

A handover is sent **from** one team **to** one or more recipient teams and
declares what it **expects** from each of them:

| `expects`  | the recipient must…                              | recipient command | satisfied state |
|------------|--------------------------------------------------|-------------------|-----------------|
| `ack`      | just confirm it was noted                        | `ho ack`          | `acknowledged`  |
| `done`     | do the work and report completion (note optional)| `ho done`         | `done`          |
| `response` | answer back with Markdown                        | `ho respond`      | `answered`      |

Every recipient has its own state: `pending` → (`acknowledged`) → `done` /
`answered`, or `declined` (always with a reason). `ho ack` on a `done` or
`response` handover means "taken in progress".

The sender reviews the results. It can **return** one recipient's part
(`returned`, with a message — the old answer stays in the history) or **close**
the handover, which archives it. The overall status is derived:

- `open` — at least one recipient part is not satisfied;
- `resolved` — all parts are satisfied (shows up in the sender's `to_review`);
- `closed` — closed by the sender (or automatically with `--auto-close`).

Anyone taking part can `comment` at any time; `seen` marks new activity as read.

## Install

With pip (or `uv tool install` / `pipx install`), which puts `ho` on your `PATH`:

```sh
pip install "radark-handover @ git+https://github.com/raaleksandr/radark-handover"
```

Or copy or clone this repository somewhere and put it on your `PATH`:

```sh
git clone https://github.com/raaleksandr/radark-handover ~/tools/ho
export PATH="$HOME/tools/ho:$PATH"      # POSIX: uses the `ho` launcher
```

```powershell
$env:Path = "D:\tools\ho;$env:Path"      # Windows: uses ho.cmd
```

The launchers find Python 3.10+ (`py -3` or `python` on Windows, `python3` or
`python` elsewhere). You can also call `python ho.py …` directly. If `ho` is
not executable after cloning on POSIX, run `chmod +x ho`.

## Set up a hub shared through git

```sh
mkdir handover-hub && cd handover-hub
git init -b main
ho init --teams strategy=Strategy,product=Product,frontend=Frontend,backend=Backend,mobile=Mobile
git remote add origin <url of an empty shared repository>
ho sync                                  # first commit + push
```

Every team clones that repository and points its people and agents at it:

```sh
export HO_HUB=/path/to/handover-hub      # or run ho inside the hub, or pass --hub
export HO_TEAM=backend                   # or pass --team
```

`ho init` writes `AGENTS.md` into the hub: the instructions to give an AI agent
(team setup, `ho sync` first and last, `ho inbox --json`, which action answers
which expectation). Teams are listed in `hub.json`; edit it to add a team.

A plain shared folder works too — just skip `ho sync`.

## Walkthrough: product → backend → product

The outputs below come from a real run; IDs, timestamps and paths will differ.

Product writes a brief and sends it to backend, expecting an answer:

```console
$ export HO_TEAM=product
$ ho new --to backend --expects response --title "Orders API for checkout" --priority high --body-file ../brief.md
Created HO-20261010-bizg for backend (expects response)
/work/handover-hub/active/HO-20261010-bizg/handover.md
$ ho sync
```

Backend syncs and finds it in its inbox. IDs can be shortened to their last 4+
characters:

```console
$ ho sync
$ ho inbox --team backend
Inbox for backend
Action required (1):
  HO-20261010-bizg  [high] Orders API for checkout  (from product; expects response; you: pending)
To review (0):
  (none)
Updates (0):
  (none)
$ ho ack bizg --team backend
backend: ack HO-20261010-bizg -> status open, backend is acknowledged
$ ho respond bizg --team backend --body-file answer.md
backend: respond HO-20261010-bizg -> status resolved, backend is answered
$ ho sync
```

Product sees the answer under "To review" and asks for more:

```console
$ ho inbox
Inbox for product
Action required (0):
  (none)
To review (1):
  HO-20261010-bizg  [high] Orders API for checkout  (from product; expects response; backend: answered)
Updates (0):
  (none)
$ ho return bizg --to backend -m "Please add the error codes."
product: return backend HO-20261010-bizg -> status open
```

Backend gets it back as action required, answers again, and product closes:

```console
$ ho inbox --team backend
Inbox for backend
Action required (1):
  HO-20261010-bizg  [high] Orders API for checkout  (from product; expects response; you: returned)
...
$ ho respond bizg --team backend -m "Errors: \`422\` invalid cart, \`409\` cart already ordered."
backend: respond HO-20261010-bizg -> status resolved, backend is answered
$ ho show bizg
HO-20261010-bizg  Orders API for checkout
From: product  To: backend  Expects: response
Status: resolved  Priority: high  Due: -  Created: 2026-10-10T15:05:29.051Z
Recipients: backend=answered
You can: return --to backend, close, comment, seen
Path: /work/handover-hub/active/HO-20261010-bizg/handover.md

# Orders API for the new checkout
...
--- Events (4) ---
[2026-10-10T15:05:29.610Z] backend ack

[2026-10-10T15:05:29.800Z] backend respond
## POST /orders
...
[2026-10-10T15:05:30.149Z] product return -> backend
Please add the error codes.

[2026-10-10T15:05:30.472Z] backend respond
Errors: `422` invalid cart, `409` cart already ordered.
$ ho close bizg -m "Thanks, all clear."
product: close HO-20261010-bizg -> status closed
Closed and archived: /work/handover-hub/archive/2026/HO-20261010-bizg/handover.md
$ ho list --status closed
HO-20261010-bizg  closed    product -> backend  response  Orders API for checkout
$ ho sync
```

(In this walkthrough every command ran in one clone; with separate machines,
each team runs `ho sync` before and after acting.)

## Command reference

Common options for every command: `--hub <path>` (else `HO_HUB`, else the
nearest parent directory containing `hub.json`), `--team <id>` (else
`HO_TEAM`), `--json`.

Bodies (Markdown) are given with `-m "text"`, `--body-file <path>` (UTF-8,
stored verbatim) or `--body -` (read stdin). On Windows PowerShell 5.1 prefer
`--body-file`: piping text into native programs there is not UTF-8 by default.

| command | who | what |
|---|---|---|
| `ho init [path] [--teams id=Name,…]` | anyone | create a hub (refuses if `hub.json` exists) |
| `ho teams` | anyone | list registered teams |
| `ho new --to <teams> --expects ack\|done\|response --title <t> (--body-file f \| --body - \| -m t)` | sender | create a handover; prints the ID. Options: `--priority high\|normal\|low`, `--due YYYY-MM-DD`, `--tag t` (repeatable), `--ref <ID>` (repeatable), `--attach <file>` (repeatable, copied to `attachments/`), `--auto-close` |
| `ho inbox` | team | `action_required`, `to_review`, `updates` for the acting team |
| `ho list [--status active\|open\|resolved\|closed\|all] [--from t] [--to t] [--expects e] [--tag t]` | anyone | list handovers; default `active` (open + resolved) |
| `ho show <ID>` | anyone | metadata, body, recipient states and full event history (also for archived); with a team also `allowed_actions` |
| `ho ack <ID>` | recipient | noted / taken in progress |
| `ho done <ID> [-m note]` | recipient | work finished (`expects: done`) |
| `ho respond <ID> <body>` | recipient | answer (`expects: response`), body required |
| `ho decline <ID> <reason>` | recipient | decline, reason required |
| `ho comment <ID> <body>` | participant | comment, no state change |
| `ho seen <ID>` | participant | mark activity as read (clears `updates`) |
| `ho return <ID> --to <team> <message>` | sender | send a satisfied part back, message required |
| `ho close <ID> [-m note]` | sender | close a resolved handover and archive it |
| `ho close <ID> --force -m <reason>` | sender | cancel an open handover |
| `ho validate` | anyone | check the hub's integrity; lists every problem with its file |
| `ho sync` | anyone | `git pull --rebase --autostash`, commit hub changes with a generated message (e.g. `ho: backend respond HO-20261010-bizg`), `git push` |
| `ho --version [--json]` | anyone | package version and JSON contract version; needs no hub and no team, takes no command |

No other command touches git.

`ho --version` prints one line, and `ho --version --json` one JSON document
(both exit 0, even without a hub, `HO_HUB` or `HO_TEAM`):

```text
ho 0.2.0 (contract 1)
```

```json
{
  "schema_version": 1,
  "version": "0.2.0",
  "contract": 1,
  "features": [
    "hub-lock"
  ]
}
```

Tools that drive `ho` can run it once at start-up: a missing `ho`, or an older
one without `--version` (exit 1, no JSON), or an unexpected `contract` means
stop with one clear message.

`features` names optional capabilities of this `ho` (additive; unknown entries
should be ignored). `"hub-lock"` means writing commands and `sync` serialise
through the hub lock (below); a tool that runs several `ho` processes on one hub
at the same time should require it (a missing `features` field means an older
`ho` without the lock).

### Hub lock

Several `ho` processes may work on the same hub at once (for example parallel
agent sessions). Every command that writes to the hub — `new`, `ack`, `done`,
`respond`, `decline`, `comment`, `seen`, `return`, `close` — and `sync` hold an
exclusive lock on `<hub>/.ho.lock` for their whole run, so they run one after
the other. Read commands (`teams`, `inbox`, `list`, `show`, `validate`) and
`--version` never take or wait for it; `init` does not lock.

- **Wait bound.** A command that finds the lock taken waits (one note on
  stderr; stdout stays a single JSON document) for up to 300 seconds, set with
  the environment variable `HO_LOCK_TIMEOUT` (seconds, `0` = do not wait). Past
  the bound it fails with error code `hub_locked`, exit code `1`, and the hub
  unchanged; run the command again later. An invalid `HO_LOCK_TIMEOUT` is a
  `usage` error.
- **No stuck lock.** The lock is an operating-system lock on an open file
  handle (`msvcrt.locking` on Windows, `fcntl.flock` elsewhere), released when
  the process ends, even on a crash or kill. The file's existence means
  nothing; never delete it. Keep the hub working copy on a local disk:
  network file systems may not honour these locks. Different clones are
  different hubs and do not lock each other — git merges them.
- **Not versioned.** `ho init` writes a `.gitignore` with `.ho.lock`; on older
  hubs the first successful writing command (or `sync`) appends the line, and
  `sync` never stages `.ho.lock` in any case.

### Inbox

- `action_required` — you are a recipient and your part is not satisfied
  (including `returned`, and `acknowledged` parts still owing `done`/`response`);
- `to_review` — you sent it and it is `resolved`: close it or return a part;
- `updates` — you take part in it, it is not listed above, and another team has
  done something since your team's latest action (`seen` counts as an action).

Groups are ordered by priority (high first), due date (earliest first, none
last), then creation time.

### JSON output

With `--json`, stdout carries exactly one JSON document (ASCII, with `\u`
escapes) that always has `schema_version: 1`:

- `inbox` → `{team, action_required[], to_review[], updates[], warnings[]}`;
- `list` → `{items[], warnings[]}`;
- `show` → handover summary plus `body`, `events[]` (`at`, `team`, `action`,
  `target` for returns, `body`, `file`), `invalid_events[]` and, with a team,
  `allowed_actions[]`;
- `new` and all actions → the handover summary (with `id`, `status`,
  `recipients`, `my_state`, `archived`, `path`, …) plus `event` and `auto_closed`;
- errors → `{"error": {"code": "...", "message": "..."}}` (also printed to stderr).

A handover summary contains `id, title, from, to, expects, status, recipients,
my_state, priority, due, tags, refs, auto_close, created, last_activity,
archived, path`.

#### Contract version

The shapes of all `--json` outputs and the exit codes form one contract with
one integer version. Every `--json` document carries it as `schema_version`,
and `ho --version --json` reports the same number as `contract`; the two are
always equal. The contract version is increased when:

- a JSON field is removed or renamed;
- a field's type or meaning changes;
- an exit code is changed or removed, or its meaning changes.

It does **not** change for additions: a new field, command, flag, or error
code within an existing exit code. Callers should ignore fields they do not
know. The format version in `hub.json` is separate and is not the contract.

### Exit codes

| code | meaning | example error codes |
|---|---|---|
| 0 | success | |
| 1 | usage or input error | `usage`, `team_required`, `body_required`, `empty_body`, `ambiguous_id`, `hub_exists`, `not_a_git_repo`, `git_error`, `hub_locked` (hub lock not free within `HO_LOCK_TIMEOUT`) |
| 2 | hub, team or handover not found | `hub_not_found`, `unknown_team`, `handover_not_found` |
| 3 | not allowed by the lifecycle | `invalid_transition`, `not_recipient`, `not_sender`, `not_participant`, `archived` |
| 4 | hub integrity problem | `integrity_error`, `invalid_hub`, `duplicate_id`, `io_error`; `validate` with problems |

A failed command never changes the hub.

## File format

```
handover-hub/
  hub.json                      # {"schema_version": 1, "teams": [{"id": "backend", "name": "Backend"}, ...]}
  AGENTS.md                     # guide for AI agents
  .gitignore                    # lists .ho.lock
  .ho.lock                      # hub lock file (created on demand, never versioned, carries no data)
  active/
    HO-20261010-bizg/
      handover.md               # the original document, never modified
      events/
        20261010T150529.610Z--backend--ack.md
        20261010T150529.800Z--backend--respond.md
      attachments/              # optional, from --attach
  archive/
    2026/
      HO-20261010-k2m9/ ...     # closed handovers, same layout
```

`active/` and `archive/` may be missing in a fresh clone (git does not keep
empty directories); `ho` creates them when needed.

**IDs** are `HO-<YYYYMMDD>-<xxxx>`: UTC creation date plus 4 random characters
from `a-z2-7`.

**`handover.md`:**

```markdown
---
id: HO-20261010-bizg
title: Orders API for checkout
from: product
to: [backend]
expects: response
priority: high
created: 2026-10-10T15:05:29.051Z
due: 2026-10-31
tags: [checkout, api]
refs: [HO-20261001-q7xa]
auto_close: false
---

# Orders API for the new checkout
...
```

Required: `id`, `title`, `from`, `to` (non-empty, without the sender),
`expects`, `created`. Optional: `priority` (default `normal`), `due`, `tags`,
`refs`, `auto_close` (default `false`). The body must not be empty.

**Event files** (`events/<UTC timestamp>--<team>--<action>[-n].md`) are written
once and never changed:

```markdown
---
at: 2026-10-10T15:05:30.149Z
team: product
action: return
target: backend
---

Please add the error codes.
```

Actions: `ack`, `done`, `respond`, `decline`, `comment`, `seen`, `return`
(with `target`), `close`. All state is derived by replaying events in `at`
order. An event that is not valid at its position (for example two machines
acted concurrently without syncing) is skipped and reported by `ho show`
(`invalid_events`) and `ho validate`.

### Front-matter subset

Front matter is a strict subset of YAML that `ho` parses itself (no PyYAML):

- one `key: value` per line; blank lines and `# comment` lines are allowed;
- values: plain text (`backend`, `2026-10-31`, `Orders API for checkout`),
  `"double quoted"` (JSON escapes), `'single quoted'` (`''` for a quote),
  `true` / `false`, flow lists `[a, b, "c, d"]`;
- not supported: nesting/indentation, block lists (`- item`), multi-line
  values, `null`, anchors, values containing `: ` or ` #` unless quoted.

Anything else is rejected with the file and line number. Prefer `ho new` over
writing handovers by hand, and run `ho validate` after manual edits.

## Development

```sh
python -m unittest discover -s tests
```

The suite runs in-process against temporary hubs; `tests/test_git_sync.py`
uses local bare repositories to show that concurrent responses from different
clones merge without conflicts (skipped if `git` is not installed).

`ho.py` is organized as `store` → `lifecycle` → `views` → `cli`; the first
three return plain dicts so a future HTTP or MCP server can reuse them with the
same JSON shapes.

## License

MIT — see [LICENSE](LICENSE).
