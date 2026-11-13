## Context

The repository is empty apart from OpenSpec scaffolding, so there are no existing conventions to follow. Requirements come from proposal.md and the three specs (`handover-store`, `handover-lifecycle`, `handover-cli`). Key constraints from the request:

- Handovers are real Markdown documents, not one-liners.
- Several teams, possibly on different machines, write concurrently.
- AI agents are the main consumers: they must find "what is active for my team" and act with a single command, without a server that has to be kept running.
- Finished items must leave the active view.

The target machines include Windows (PowerShell) and POSIX shells; Python 3.12 and Node 24 are both present on the author's machine.

## Goals / Non-Goals

**Goals:**
- Plain files that are readable and greppable without the tool, and diff/merge cleanly in git.
- One CLI that is the single, safe way to change state, with a stable JSON contract for agents.
- Zero infrastructure: a git remote (or shared folder) is the only shared component.
- A core that a future HTTP/MCP server can reuse without changing the file format.

**Non-Goals:**
- Long-running server, web UI, notifications, authentication/authorization (team identity is trusted, as in git itself).
- Editing a handover's original document after creation (use comments, or close and create a new handover with `--ref`).
- Multi-hop routing/forwarding to new teams on an existing handover (create a new handover with `--ref`).

## Decisions

### 1. Files in a git repository instead of a server or tracker
The hub is a directory, intended to be its own small git repository shared by all teams (a folder inside an existing monorepo also works).
- *Why:* no process to keep alive, full history and blame for free, works offline, agents can read files directly, and git already solves distribution and backup.
- *Alternatives:* a small HTTP server with SQLite (needs hosting, uptime, auth; harder for agents without network tools); GitHub Issues / Jira (Markdown is fine, but per-team multi-recipient state, "answer back" semantics and offline agent access are awkward, and it ties the workflow to a vendor API); an MCP server (good later as a façade, but still a running process — see decision 7).

### 2. Event-sourced handover: immutable document + one file per event
`handover.md` is written once; every action is a new file in `events/` named `<UTC yyyymmddThhmmss.fffZ>--<team>--<action>.md`. All state (per-recipient state, overall status, last activity) is derived by replaying events in order.
- *Why:* two teams acting at the same time create two different files, so git merges never conflict on handover data; history is preserved (a returned answer stays visible); no "status" field can drift out of sync with the log.
- *Alternatives:* status fields in front matter of `handover.md` (simple to grep, but every action rewrites the same file → constant merge conflicts across teams); a single append-only `log.md` per handover (appends from two branches conflict at the same line).
- Same-millisecond collision by the same team is handled by appending `-2`, `-3`, … before `.md`.

### 3. Python 3.10+, standard library only, single module
The CLI is one file `ho.py` (with an internal split into store / lifecycle / views / cli sections) plus launchers `ho.cmd` (Windows) and `ho` (POSIX sh) that call it with the available Python.
- *Why:* Python is preinstalled or trivially available on dev machines and CI, agents use it fluently, `argparse`/`json`/`pathlib`/`subprocess` cover everything, and a single file can be copied into any hub without packaging.
- *Alternatives:* Node.js (equally viable, but stdlib lacks an argument parser comparable to argparse and global install is more friction on agent sandboxes); Go/Rust binary (no runtime needed, but cross-compilation and distribution add work disproportionate to the tool's size).

### 4. Restricted front-matter format, JSON config
Front matter uses a strict YAML subset written by the tool: `key: scalar` lines, flow lists `[a, b]`, booleans `true/false`, quoted strings when needed. The tool ships its own small parser/serializer and rejects anything outside the subset with a clear validation error. `hub.json` uses JSON because it is human-editable and in the stdlib.
- *Why:* avoids a PyYAML dependency while keeping files that GitHub, editors and agents render as normal Markdown front matter.
- *Alternative:* JSON front matter (stdlib, but unusual in Markdown and uglier for humans).

### 5. State machine as a table
Recipient transitions are defined as a single table `(expects, current_state, action) → new_state` plus body-required flags; sender actions (`return`, `close`) and participant actions (`comment`, `seen`) are checked separately. The same table drives validation of the CLI actions and of the existing log in `validate`, and produces the "allowed actions" list in error messages.

### 6. Inbox "updates" without read-state files
Unread activity is computed as "an event by another team newer than this team's latest event on the handover". `seen` is just an event, so marking read is a normal git-mergeable file and needs no per-user state store.
- *Alternative:* a `.read/<team>.json` cursor file (rewritten constantly → merge conflicts).

### 7. Layering for a future server
Code is organized so that `store` (paths, parse, write, archive), `lifecycle` (replay, transitions) and `views` (inbox/list/show dicts) return plain dicts; the CLI only parses arguments and renders text/JSON. A later MCP or HTTP server can import the same module and expose the same JSON shapes (`schema_version` = 1).

### 8. Safe writes
Every write command validates everything first (hub, team, ID, transition, body), then writes the new event to a temporary file in the same directory and renames it into place (`os.replace`). Archiving is a directory rename within the hub (same filesystem), preceded by writing the `close` event. On rename failure the close event is removed so the hub is unchanged.

### 9. Git sync as an explicit command
`ho sync` runs `git pull --rebase --autostash`, `git add -A <hub>`, commits with a message built from the new/changed event files (e.g. `ho: backend respond HO-20261010-ab3x; product new HO-20261010-k2m9`), and `git push`. Other commands never touch git, so they stay fast and predictable; AGENTS.md instructs agents to `sync` before reading the inbox and after acting.

### 10. Repository layout for this project
```
ho.py            # the CLI (copied/symlinked into or referenced from a hub)
ho.cmd, ho       # launchers
ho_templates/AGENTS.md  # agent guide written by `ho init`
tests/           # unittest suite (stdlib), runs against temp hubs
README.md
```

## Risks / Trade-offs

- [Clock skew between machines makes event order slightly wrong] → order is by `at`, then file name; skew only affects near-simultaneous events from different machines.
- [Agents forget to `sync`, or two machines act concurrently, so after merge the log contains an event that is invalid in replayed order (e.g. a `respond` after the sender already closed elsewhere)] → replay skips events that are invalid at their position and `validate` reports each one with its path; nothing is lost, and AGENTS.md makes `sync` the first and last step to keep this rare. A special case is an event written into `active/<ID>/` on one machine while another archived it: after merge the ID exists in both places; `validate` reports it as the duplicate-ID integrity error, naming the stray event files so they can be moved into the archived folder by hand.
- [Hand edits break files] → `validate` with exact file paths; the CLI refuses to act on a handover whose files fail to parse.
- [No authentication: any team can act as another] → accepted for trusted internal teams; git history records the real author. Can be added in a future server layer.
- [Archive grows indefinitely] → archive is partitioned by year and never read by inbox/list defaults, so performance of daily commands is bounded by active items.
- [Restricted YAML subset surprises users who hand-write front matter] → document the subset in README and prefer `ho new` for creation.

## Migration Plan

New tool, no existing data. Rollout: create a hub repository, run `ho init --teams strategy=Strategy,product=Product,frontend=Frontend,backend=Backend,mobile=Mobile`, commit, and point each team's agent at it with `HO_HUB` and `HO_TEAM`. Rollback is deleting the repository; data is plain Markdown and stays readable without the tool.

## Open Questions

- Should write commands offer an opt-in `HO_AUTOSYNC=1` that runs `sync` automatically? Can be added later without changing format or specs of existing commands.
- Should an optional generated `INDEX.md` (for humans browsing on a git web UI) be produced by `sync`? Deferred; would be a derived file excluded from conflict-prone paths.
