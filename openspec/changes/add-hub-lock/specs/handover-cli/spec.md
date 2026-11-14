## MODIFIED Requirements

### Requirement: Errors and exit codes
The CLI SHALL exit with `0` on success, `1` for usage or input errors, `2` when a handover, team or hub is not found, `3` for an action not allowed by the lifecycle, and `4` for hub integrity errors. A writing command or `sync` that could not get the hub lock within its wait bound SHALL exit with `1` and error code `hub_locked`. Error messages SHALL go to stderr; with `--json`, stdout SHALL contain a JSON object `{"error": {"code": <name>, "message": <text>}}`. Failed commands SHALL leave the hub unchanged.

#### Scenario: Invalid transition exit code
- **WHEN** backend runs `ho done` on a `response` handover with `--json`
- **THEN** the exit code is `3` and stdout is a JSON object with an `error` whose code is `invalid_transition`

#### Scenario: Hub lock timeout exit code
- **WHEN** `ho sync --json` cannot get the hub lock within `HO_LOCK_TIMEOUT`
- **THEN** the exit code is `1` and stdout is a JSON object with an `error` whose code is `hub_locked`

### Requirement: Version information
`ho --version` SHALL print one line `ho <version> (contract <n>)` and exit `0`, where `<version>` is the installed radark-handover package version and `<n>` the JSON contract version. With `--json` it SHALL instead print one JSON object `{"schema_version", "version", "contract", "features"}`: `version` a string, `contract` an integer, `features` a list of strings naming optional capabilities of this `ho`. `features` SHALL contain `"hub-lock"` when writing commands and `sync` serialise through the hub lock. It SHALL need no hub and no team and SHALL read or write no hub files.

#### Scenario: Human-readable version
- **WHEN** `ho --version` is run with package version `0.2.0` and contract `1`
- **THEN** stdout is exactly `ho 0.2.0 (contract 1)` followed by a newline, stderr is empty and the exit code is `0`

#### Scenario: Machine-readable version
- **WHEN** `ho --version --json` is run
- **THEN** stdout parses as a single JSON object with `schema_version` (integer), `version` (string equal to the package version), `contract` (integer) and `features` (list of strings), and the exit code is `0`

#### Scenario: Hub lock is announced
- **WHEN** `ho --version --json` is run
- **THEN** its `features` list contains `"hub-lock"` and `contract` is still `1`

#### Scenario: No hub and no team needed
- **WHEN** `ho --version --json` is run outside any hub with neither `HO_HUB` nor `HO_TEAM` set, or with `HO_HUB` pointing to a directory that does not exist
- **THEN** it succeeds with exit code `0` and the same output

#### Scenario: Version with a command is a usage error
- **WHEN** `ho --version inbox` is run
- **THEN** the exit code is `1` (usage error)
