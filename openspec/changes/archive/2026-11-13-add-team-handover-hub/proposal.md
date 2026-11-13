## Why

Work constantly moves between teams (strategy, product, frontend, backend, mobile, ...) as Markdown handover documents, but there is no shared place that tracks who owes what: whether a handover was noted, done, or needs an answer back. AI agents now do much of the work for each team, so the handover channel must be something an agent can reliably query ("what is active for my team?"), act on ("mark my part done / answered"), and create into — without a permanently running server.

## What Changes

- Introduce a **handover hub**: a plain-file store (a directory, intended to live in a shared git repository) where each handover is a folder containing a Markdown document with YAML front matter plus an append-only log of per-team events (acknowledgements, completions, responses, comments).
- Each handover declares what it **expects** from its recipients: `ack` (just confirm it was noted), `done` (do the work and report completion), or `response` (return a Markdown answer). Each recipient team has its own independent state.
- The sender can review results, **return** a recipient's part for rework/clarification, or **close** the handover. Closed handovers are moved to an **archive** area so the active list stays small.
- A **zero-dependency CLI** (`ho`) that runs on demand — no daemon — with commands to create, list, show, ack, complete, respond, return, comment, close, and an **inbox** view per team. Every read command supports `--json` with a stable schema so agents can parse it; human-readable output is the default.
- An **agent guide** (`AGENTS.md`-style instructions shipped with the hub) telling AI agents the exact commands to find and process work for their team.
- Optional `sync` helper that wraps git pull/commit/push so teams share the hub through an ordinary repository.
- Non-goals for this change: a long-running HTTP server, web UI, notifications (email/Slack), authentication. The file format is designed so an API/MCP server could be layered on later without migration.

## Capabilities

### New Capabilities
- `handover-store`: On-disk layout and file format of handovers (document, front matter fields, event log, responses), ID generation, team registry, and the active/archive split.
- `handover-lifecycle`: Expectation types, per-recipient states and allowed transitions, sender review (return/close), derived overall status, and archiving rules.
- `handover-cli`: The command-line interface — commands, team identity resolution, inbox semantics, filtering, JSON output contract, exit codes, and the git sync helper.

### Modified Capabilities

(none — project has no existing specs)

## Impact

- New code: a single-file Python 3.10+ CLI using only the standard library, plus thin launchers for Windows (`ho.cmd`) and POSIX (`ho`).
- New on-disk data format that becomes a contract for teams and agents; changes to it later require migration.
- Teams need a shared git repository (or shared folder) to host the hub; no other infrastructure.
- No external dependencies, no network services.
