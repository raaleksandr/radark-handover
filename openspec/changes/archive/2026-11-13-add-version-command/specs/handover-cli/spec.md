# Spec Delta

## ADDED Requirements

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
