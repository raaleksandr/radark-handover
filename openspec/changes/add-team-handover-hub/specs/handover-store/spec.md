## Purpose

Defines how handovers between teams are stored as plain files: the hub layout, the handover document format, the append-only event log, the team registry, and the split between active and archived handovers.

## ADDED Requirements

### Requirement: Hub layout
A hub SHALL be a directory containing a `hub.json` configuration file, an `AGENTS.md` usage guide, an `active/` directory holding open handovers, and an `archive/` directory holding closed handovers grouped by year (`archive/<YYYY>/`). Each handover SHALL be a single directory named by its ID, located either at `active/<ID>/` or `archive/<YYYY>/<ID>/`, never both.

#### Scenario: Freshly initialized hub
- **WHEN** a hub is initialized in an empty directory
- **THEN** the directory contains `hub.json`, `AGENTS.md`, an empty `active/` directory and an empty `archive/` directory

#### Scenario: Handover exists in exactly one place
- **WHEN** the hub is validated
- **THEN** an ID present under both `active/` and `archive/` is reported as an integrity error

### Requirement: Team registry
`hub.json` SHALL list the teams allowed to send and receive handovers. Each team SHALL have a unique lowercase identifier (letters, digits, hyphens) and a human-readable name. Any operation referencing a team not in the registry SHALL be rejected.

#### Scenario: Unknown team rejected
- **WHEN** a handover is created with recipient `ios` and `ios` is not in the registry
- **THEN** the operation fails with an error naming the unknown team and listing registered teams

### Requirement: Handover document format
Each handover directory SHALL contain `handover.md`: a YAML front matter block followed by a Markdown body. The front matter SHALL contain `id`, `title`, `from` (sender team), `to` (non-empty list of recipient teams, not containing the sender), `expects` (`ack`, `done` or `response`), `created` (ISO 8601 UTC timestamp), and `priority` (`low`, `normal` or `high`, default `normal`). It MAY contain `due` (ISO date), `tags` (list), `refs` (list of related handover IDs) and `auto_close` (boolean, default false). The Markdown body SHALL be non-empty. `handover.md` SHALL NOT be modified after creation by the tool; all subsequent changes are recorded as events.

#### Scenario: Valid document
- **WHEN** a handover is created from product to backend and frontend expecting a response
- **THEN** `handover.md` front matter contains `from: product`, `to: [backend, frontend]`, `expects: response` and the body contains the supplied Markdown unchanged

#### Scenario: Empty body rejected
- **WHEN** a handover is created with an empty or whitespace-only body
- **THEN** the operation fails and no handover directory is created

### Requirement: Event log
Every state change, response, comment and closure SHALL be recorded as a separate, immutable Markdown file in the handover's `events/` directory. Each event file SHALL have front matter with `at` (ISO 8601 UTC timestamp with millisecond precision), `team` (acting team), `action`, and, for `return`, `target` (the recipient team being returned to); the Markdown body carries the message, answer or note (may be empty where the lifecycle allows). Event file names SHALL be unique and sort chronologically (timestamp first, then acting team and action). Existing event files SHALL never be rewritten.

#### Scenario: Concurrent events do not collide
- **WHEN** the backend and frontend teams each record a response to the same handover on separate machines and the changes are merged with git
- **THEN** both event files exist side by side without a merge conflict and both responses are visible

#### Scenario: Chronological ordering
- **WHEN** the events of a handover are listed
- **THEN** they are returned in the order of their `at` timestamps

### Requirement: Handover identifiers
Each handover SHALL have an ID of the form `HO-<YYYYMMDD>-<XXXX>`, where `<YYYYMMDD>` is the UTC creation date and `<XXXX>` is 4 random characters from a lowercase base32 alphabet, so IDs created independently on different machines are unlikely to collide. Creation SHALL fail rather than overwrite if the generated ID already exists in the hub (after retrying generation).

#### Scenario: ID format
- **WHEN** a handover is created on 2026-10-10
- **THEN** its ID matches `HO-20261010-[a-z2-7]{4}`

### Requirement: Attachments
A handover directory MAY contain additional files under `attachments/`. These files SHALL be preserved and moved together with the handover when it is archived.

#### Scenario: Attachment survives archiving
- **WHEN** a handover with `attachments/api.yaml` is closed
- **THEN** `archive/<YYYY>/<ID>/attachments/api.yaml` exists

### Requirement: Archiving
When a handover is closed it SHALL be moved from `active/<ID>/` to `archive/<YYYY>/<ID>/`, where `<YYYY>` is the year of closure, with its document, events and attachments intact. Archived handovers SHALL remain readable and searchable but SHALL NOT appear in active listings or inboxes.

#### Scenario: Close moves to archive
- **WHEN** the sender closes handover `HO-20261010-ab3x` in 2026
- **THEN** `active/HO-20261010-ab3x/` no longer exists and `archive/2026/HO-20261010-ab3x/` contains `handover.md` and all events including the `close` event

### Requirement: Integrity validation
The hub SHALL be checkable for integrity: malformed front matter, missing required fields, unknown teams, unknown actions, events that violate the lifecycle, and duplicate IDs SHALL each be reported with the offending file path.

#### Scenario: Hand-edited file broken
- **WHEN** a user manually removes the `expects` field from a `handover.md` and the hub is validated
- **THEN** validation reports a missing required field `expects` with that file's path and exits with a non-zero code
