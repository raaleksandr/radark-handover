## Purpose

Defines what a handover expects from each recipient team, the per-recipient states and allowed transitions, how the sender reviews, returns or closes a handover, and when it is archived.

## ADDED Requirements

### Requirement: Expectation types
Every handover SHALL declare one expectation that applies to all its recipients:
- `ack` — the recipient only confirms the handover was noted;
- `done` — the recipient performs the work and reports completion (optionally with a note);
- `response` — the recipient must return a non-empty Markdown answer.

#### Scenario: Expectation recorded
- **WHEN** a handover is created with expectation `response`
- **THEN** every recipient's part is satisfied only by a response with a non-empty body (or a decline)

### Requirement: Per-recipient state
Each recipient team SHALL have its own state, derived solely from the event log, independent of other recipients. States are: `pending` (nothing done yet), `acknowledged` (noted; for `done`/`response` this means taken in progress), `done`, `answered`, `declined`, and `returned` (sender sent the part back). A recipient part is **satisfied** when its state is `acknowledged` under `ack`, `done` under `done`, `answered` under `response`, or `declined` under any expectation.

#### Scenario: Independent recipients
- **WHEN** a handover to backend and frontend expecting `done` has a `done` event from backend only
- **THEN** backend's state is `done` (satisfied) and frontend's state is `pending` (not satisfied)

### Requirement: Recipient actions
A recipient team SHALL be able to perform these actions on its own part:
- `ack` — allowed from `pending` or `returned`; results in `acknowledged`.
- `done` — allowed only when expectation is `done`, from `pending`, `acknowledged` or `returned`; results in `done`; body optional.
- `respond` — allowed only when expectation is `response`, from `pending`, `acknowledged` or `returned`; body required; results in `answered`.
- `decline` — allowed from any non-satisfied state; body (reason) required; results in `declined`.
A team that is not a recipient SHALL NOT perform recipient actions. An action not allowed from the current state or under the expectation SHALL be rejected with an error stating the current state, the expectation and the allowed actions; no event is written.

#### Scenario: Respond to a question
- **WHEN** backend responds with a Markdown answer to a `response` handover where backend is `pending`
- **THEN** a `respond` event with the answer body is recorded and backend's state becomes `answered`

#### Scenario: Wrong action for expectation
- **WHEN** backend runs `done` on a handover expecting `response`
- **THEN** the action is rejected, the error says the expectation is `response` and suggests `respond`, and no event file is written

#### Scenario: Respond without body
- **WHEN** backend runs `respond` with an empty body
- **THEN** the action is rejected and no event file is written

#### Scenario: Non-recipient cannot act
- **WHEN** mobile runs `ack` on a handover addressed only to backend
- **THEN** the action is rejected with an error that mobile is not a recipient

### Requirement: Sender return
The sender team SHALL be able to `return` a single recipient's part with a required Markdown message (e.g. follow-up question, rework request, rejection of an answer). Return SHALL be allowed only when that recipient's part is satisfied, and SHALL set that recipient's state to `returned`, which counts as not satisfied. Previous responses remain in the log.

#### Scenario: Answer needs clarification
- **WHEN** product returns backend's answered part with message "Please add the error codes"
- **THEN** backend's state becomes `returned`, the handover appears again in backend's inbox as requiring action, and backend's previous answer is still visible in the history

#### Scenario: Return a pending part rejected
- **WHEN** product tries to return frontend's part while frontend is `pending`
- **THEN** the action is rejected

### Requirement: Comments
Any participant (sender or recipient) SHALL be able to add a `comment` event with a non-empty body at any time while the handover is active. Comments SHALL NOT change any recipient's state.

#### Scenario: Clarifying question from recipient
- **WHEN** frontend comments "Which screens are in scope?" on a pending handover
- **THEN** a comment event is recorded, frontend's state stays `pending`, and the handover shows new activity for the sender

### Requirement: Overall status
Each handover SHALL have a derived overall status: `open` if at least one recipient part is not satisfied, `resolved` if all recipient parts are satisfied, and `closed` once a `close` event exists.

#### Scenario: Resolved when all satisfied
- **WHEN** both recipients of an `ack` handover have acknowledged
- **THEN** the overall status is `resolved`

### Requirement: Closing and archiving
Only the sender team SHALL be able to `close` a handover. Closing a `resolved` handover SHALL be allowed with an optional note. Closing an `open` handover SHALL require explicit force and a non-empty reason (cancellation). Closing SHALL record a `close` event and archive the handover. Closed handovers SHALL NOT accept further events.

#### Scenario: Sender closes resolved handover
- **WHEN** product closes a resolved handover
- **THEN** a `close` event is recorded and the handover is moved to the archive

#### Scenario: Cancel open handover
- **WHEN** product closes an open handover without force
- **THEN** the action is rejected and the error explains that force and a reason are required

#### Scenario: Recipient cannot close
- **WHEN** backend tries to close a handover sent by product
- **THEN** the action is rejected

#### Scenario: No events after close
- **WHEN** any team attempts an action on an archived handover
- **THEN** the action is rejected and the error suggests creating a new handover referencing the closed one

### Requirement: Auto-close
A handover created with `auto_close: true` SHALL be closed and archived automatically, by the operation that makes it `resolved`, recording a `close` event attributed to the sender with a note that it was auto-closed.

#### Scenario: FYI handover disappears once noted
- **WHEN** an `ack` handover with auto-close to backend and frontend receives the second acknowledgement
- **THEN** a `close` event is recorded and the handover is moved to the archive without sender action

### Requirement: Seen marker
A team SHALL be able to record a `seen` event on a handover it participates in. A `seen` event SHALL NOT change any state; it only marks that the team has read all activity up to that moment.

#### Scenario: Clear an update
- **WHEN** product has unread comments from frontend on a handover and records `seen`
- **THEN** the handover no longer appears under product's updates until a newer event by another team arrives
