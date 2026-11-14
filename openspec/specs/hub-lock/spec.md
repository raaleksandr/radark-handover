# hub-lock Specification

## Purpose
Serialises concurrent `ho` processes working on the same hub so that writes and `sync` never interleave, while
read commands stay instant and a crashed process can never leave the hub locked.

## Requirements

### Requirement: Exclusive lock for writing commands
Every command that writes to the hub (`new`, `ack`, `done`, `respond`, `decline`, `comment`, `seen`, `return`,
`close`) and `sync` SHALL hold an exclusive lock on the file `<hub>/.ho.lock` from before it reads hub state for its
write until it has finished, so that at most one such command runs on a hub at a time. Read commands (`teams`,
`inbox`, `list`, `show`, `validate`) and `ho --version` SHALL neither take nor wait for the lock. `init` SHALL NOT
take the lock.

#### Scenario: Two concurrent writers both succeed
- **WHEN** two processes run `ho new` (or `ho sync` and `ho new`) on the same hub at the same moment
- **THEN** both exit `0`, one runs after the other, and both changes are present in the hub (and, after `sync`, committed)

#### Scenario: Reader does not wait
- **WHEN** another process holds the hub lock and `ho inbox --json` is run with `HO_LOCK_TIMEOUT=0`
- **THEN** it returns its normal result with exit code `0` immediately

### Requirement: Bounded wait
A command that needs the lock and finds it held SHALL wait, retrying, until the lock is free or the wait bound has
passed. While waiting it MAY write a note to stderr; under `--json` stdout SHALL stay a single JSON document.

#### Scenario: Lock released in time
- **WHEN** the lock is held for 2 seconds and `ho done <ID>` is started with `HO_LOCK_TIMEOUT=30`
- **THEN** it waits, then succeeds with exit code `0`

### Requirement: Wait bound setting
The wait bound SHALL default to 300 seconds and SHALL be set by the environment variable `HO_LOCK_TIMEOUT`
(non-negative number of seconds; `0` means fail at once if the lock is held). An invalid value SHALL be a usage
error (exit `1`, error code `usage`) that leaves the hub unchanged.

#### Scenario: Invalid bound
- **WHEN** `ho sync` is run with `HO_LOCK_TIMEOUT=soon`
- **THEN** the exit code is `1` with error code `usage` and the hub is unchanged

### Requirement: Lock timeout error
When the wait bound passes without the lock, the command SHALL fail with exit code `1` and error code `hub_locked`,
with a message naming the lock file and the bound, and SHALL leave the hub unchanged.

#### Scenario: Lock held past the bound
- **WHEN** the lock is held for longer than the bound and `ho done <ID> --json` is run with `HO_LOCK_TIMEOUT=1`
- **THEN** the exit code is `1`, stdout is `{"schema_version": 1, "error": {"code": "hub_locked", "message": ...}}`, and no hub file was created or changed

### Requirement: Lock released by the operating system
The lock SHALL be an operating-system lock tied to an open file handle of the `ho` process, so that it is released
when the process ends for any reason, including a crash or kill. The existence of `.ho.lock` SHALL NOT mean the hub
is locked, and `ho` SHALL NOT delete the file.

#### Scenario: Killed holder
- **WHEN** a process holding the lock is killed and `ho new` is then run with `HO_LOCK_TIMEOUT=0`
- **THEN** it succeeds although `.ho.lock` still exists

### Requirement: Lock file is not versioned
`.ho.lock` SHALL never be staged or committed by `ho sync`. `ho init` SHALL write a `.gitignore` in the hub that
lists `.ho.lock`; a writing command on a hub whose `.gitignore` is missing or lacks that line SHALL add the line
(creating the file if needed, keeping existing lines).

#### Scenario: Lock file never shows as a change
- **WHEN** a writing command has run in a hub that is a git working copy
- **THEN** `git status --porcelain` does not list `.ho.lock`, and the next `ho sync` commit does not contain it

#### Scenario: Existing hub gets the ignore line
- **WHEN** `ho ack` runs in a hub created before this change (no `.gitignore`)
- **THEN** `<hub>/.gitignore` exists afterwards and contains the line `.ho.lock`
