# handover-cli Specification

## Purpose
Defines the on-demand command-line interface (`ho`) that people and AI agents use to create, find, act on and archive handovers in a hub, including the per-team inbox and the machine-readable JSON output contract.

## Requirements

### Requirement: On-demand execution
The CLI SHALL run as a short-lived process per command, require no background service, and depend on nothing beyond the language runtime's standard library. It SHALL run on Windows, macOS and Linux.

#### Scenario: No server needed
- **WHEN** an agent runs `ho inbox --team backend --json` on a machine where no other `ho` process is running
- **THEN** the command prints the inbox and exits

### Requirement: Hub resolution
The CLI SHALL locate the hub from the `--hub <path>` option, else the `HO_HUB` environment variable, else by searching the current directory and its parents for `hub.json`. If no hub is found the CLI SHALL fail with an error explaining the three ways to specify it.

#### Scenario: Hub found from subdirectory
- **WHEN** `ho list` is run from `<hub>/active/HO-20261010-ab3x/`
- **THEN** the hub at `<hub>` is used

### Requirement: Team identity
Commands that act on behalf of a team or compute a team view SHALL take the team from `--team <id>`, else the `HO_TEAM` environment variable. If neither is set such commands SHALL fail with an error. The team MUST exist in the registry.

#### Scenario: Team from environment
- **WHEN** `HO_TEAM=backend` is set and `ho inbox` is run
- **THEN** the inbox for backend is shown

#### Scenario: Missing team
- **WHEN** `ho ack HO-20261010-ab3x` is run without `--team` and without `HO_TEAM`
- **THEN** the command fails with an error asking for `--team` or `HO_TEAM`

### Requirement: Commands
The CLI SHALL provide the following commands:
- `init [--teams <id=Name,...>]` — create a hub in the current or given directory (refuses if one exists).
- `teams` — list registered teams.
- `new --to <teams> --expects <ack|done|response> --title <text> (--body-file <path> | --body -) [--priority] [--due] [--tag ...] [--ref ...] [--attach <path> ...] [--auto-close]` — create a handover from the acting team; prints the new ID.
- `inbox` — the acting team's inbox (see Inbox requirement).
- `list [--status open|resolved|closed|all] [--from <team>] [--to <team>] [--expects ...] [--tag ...]` — list handovers; default lists active (open and resolved) handovers.
- `show <ID>` — full handover: metadata, body, per-recipient states, and the full event history with bodies; works for active and archived handovers.
- `ack`, `done`, `respond`, `decline`, `comment`, `seen` `<ID>` — recipient/participant actions, with `-m <text>`, `--body-file <path>` or `--body -` for the Markdown body.
- `return <ID> --to <team>` — sender returns a recipient's part, with a required body.
- `close <ID> [--force]` — sender closes and archives, with optional (or, with `--force`, required) body.
- `validate` — check hub integrity.
- `sync` — synchronize the hub through git.
Body text read from a file or stdin SHALL be stored verbatim (UTF-8).

#### Scenario: Create from a Markdown file
- **WHEN** product runs `ho new --to backend,frontend --expects response --title "Checkout redesign" --body-file brief.md`
- **THEN** a new active handover is created with the contents of `brief.md` as its body and its ID is printed

#### Scenario: Body from stdin
- **WHEN** backend runs `ho respond HO-20261010-ab3x --body -` with Markdown piped on stdin
- **THEN** the piped Markdown is stored as the response body

### Requirement: ID shorthand
Commands taking an ID SHALL accept the full ID or any unique suffix of at least 4 characters (e.g. `ab3x`). An ambiguous suffix SHALL fail with the list of matching IDs.

#### Scenario: Short ID
- **WHEN** `ho show ab3x` is run and only `HO-20261010-ab3x` ends with `ab3x`
- **THEN** that handover is shown

### Requirement: Inbox
`inbox` SHALL show, for the acting team, only active handovers in three groups:
- `action_required` — the team is a recipient and its part is not satisfied (including `returned` parts and `acknowledged` parts still owing `done`/`response`);
- `to_review` — the team is the sender and the handover is `resolved` (ready to close or return);
- `updates` — handovers the team participates in, not already listed above, with at least one event by another team newer than the team's own latest event (creation counts as the sender's event).
Within each group, items SHALL be ordered by priority (high first), then due date (earliest first, none last), then creation time (oldest first). Each item SHALL include the ID, title, sender, expectation, the team's own state (if recipient), all recipients' states, priority, due date, last activity time and the absolute path to `handover.md`.

#### Scenario: New handover appears for recipient
- **WHEN** product sends a `done` handover to backend
- **THEN** it appears under backend's `action_required` with state `pending` and does not appear in frontend's inbox

#### Scenario: Responses appear for sender
- **WHEN** all recipients of product's `response` handover have answered
- **THEN** it appears under product's `to_review`

#### Scenario: Comment appears as update
- **WHEN** frontend, whose part is `done`, receives a comment from product
- **THEN** the handover appears under frontend's `updates` until frontend records any event on it (including `seen`)

#### Scenario: Archived items never in inbox
- **WHEN** a handover has been closed
- **THEN** it appears in no team's inbox

### Requirement: JSON output contract
Every command SHALL accept `--json`. With `--json` the command SHALL write exactly one JSON document to stdout and nothing else to stdout. The document SHALL include a `schema_version` field. `inbox` returns an object with `team`, `action_required`, `to_review` and `updates` arrays; `list` returns an object with an `items` array; `show` returns the full handover including `body`, `recipients` (team → state) and `events` (each with `at`, `team`, `action`, `target` when present, and `body`); write commands return the affected handover's summary including its new status and, for `new`, the `id`. Without `--json`, output SHALL be concise human-readable text.

#### Scenario: Agent parses inbox
- **WHEN** an agent runs `ho inbox --team backend --json`
- **THEN** stdout parses as a single JSON object containing `schema_version`, `team`, `action_required`, `to_review` and `updates`

#### Scenario: Agent creates and captures ID
- **WHEN** an agent runs `ho new ... --json`
- **THEN** stdout is a JSON object whose `id` field is the new handover's ID

### Requirement: Errors and exit codes
The CLI SHALL exit with `0` on success, `1` for usage or input errors, `2` when a handover, team or hub is not found, `3` for an action not allowed by the lifecycle, and `4` for hub integrity errors. Error messages SHALL go to stderr; with `--json`, stdout SHALL contain a JSON object `{"error": {"code": <name>, "message": <text>}}`. Failed commands SHALL leave the hub unchanged.

#### Scenario: Invalid transition exit code
- **WHEN** backend runs `ho done` on a `response` handover with `--json`
- **THEN** the exit code is `3` and stdout is a JSON object with an `error` whose code is `invalid_transition`

### Requirement: Git sync
`sync` SHALL, when the hub is inside a git working tree, pull remote changes (rebasing local commits and stashing uncommitted changes), commit any hub changes with a generated message summarizing the actions, and push. If the hub is not in a git repository, `sync` SHALL fail with an explanatory error. Other commands SHALL NOT perform git operations.

#### Scenario: Share a new handover
- **WHEN** product creates a handover and runs `ho sync`
- **THEN** a commit containing the new handover directory is pushed and another team running `ho sync` then sees it in its inbox

### Requirement: Agent guide
`init` SHALL write an `AGENTS.md` into the hub that explains, for AI agents, how to set `HO_TEAM`, how to check the inbox, how to read a handover, which action to use for each expectation, how to answer, return and close, and that `--json` output should be used for parsing.

#### Scenario: Agent onboarding
- **WHEN** an agent reads `AGENTS.md` in a new hub
- **THEN** it contains the exact command for fetching the team's inbox as JSON and the mapping `ack`→`ack`, `done`→`done`, `response`→`respond`

### Requirement: Version information
`ho --version` SHALL print one line `ho <version> (contract <n>)` and exit `0`, where `<version>` is the installed radark-handover package version and `<n>` the JSON contract version. With `--json` it SHALL instead print one JSON object `{"schema_version", "version", "contract"}`: `version` a string, `contract` an integer. It SHALL need no hub and no team and SHALL read or write no hub files.

#### Scenario: Human-readable version
- **WHEN** `ho --version` is run with package version `0.2.0` and contract `1`
- **THEN** stdout is exactly `ho 0.2.0 (contract 1)` followed by a newline, stderr is empty and the exit code is `0`

#### Scenario: Machine-readable version
- **WHEN** `ho --version --json` is run
- **THEN** stdout parses as a single JSON object with `schema_version` (integer), `version` (string equal to the package version) and `contract` (integer), and the exit code is `0`

#### Scenario: No hub and no team needed
- **WHEN** `ho --version --json` is run outside any hub with neither `HO_HUB` nor `HO_TEAM` set, or with `HO_HUB` pointing to a directory that does not exist
- **THEN** it succeeds with exit code `0` and the same output

#### Scenario: Version with a command is a usage error
- **WHEN** `ho --version inbox` is run
- **THEN** the exit code is `1` (usage error)

### Requirement: JSON contract version
The JSON contract version SHALL be one integer covering the shapes of all `--json` outputs and the exit codes. It SHALL always equal the `schema_version` field of every `--json` output. It SHALL be increased when a JSON field is removed or renamed, a field's type or meaning changes, or an exit code or its meaning changes; it SHALL NOT change for additions such as a new field, command, flag or error code.

#### Scenario: Contract equals schema_version
- **WHEN** an agent runs `ho --version --json` and then `ho inbox --team backend --json`
- **THEN** the `contract` of the first output equals the `schema_version` of the second

#### Scenario: Additive change keeps the contract
- **WHEN** a release adds a new optional field to `show --json` and changes nothing else
- **THEN** `contract` and `schema_version` stay the same as in the previous release
