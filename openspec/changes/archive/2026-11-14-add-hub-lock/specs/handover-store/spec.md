## MODIFIED Requirements

### Requirement: Hub layout
A hub SHALL be a directory containing a `hub.json` configuration file, an `AGENTS.md` usage guide, a `.gitignore` that lists the hub lock file `.ho.lock`, an `active/` directory holding open handovers, and an `archive/` directory holding closed handovers grouped by year (`archive/<YYYY>/`). Each handover SHALL be a single directory named by its ID, located either at `active/<ID>/` or `archive/<YYYY>/<ID>/`, never both. A `.ho.lock` file MAY exist in the hub root; it carries no data and is never versioned.

#### Scenario: Freshly initialized hub
- **WHEN** a hub is initialized in an empty directory
- **THEN** the directory contains `hub.json`, `AGENTS.md`, a `.gitignore` containing the line `.ho.lock`, an empty `active/` directory and an empty `archive/` directory

#### Scenario: Handover exists in exactly one place
- **WHEN** the hub is validated
- **THEN** an ID present under both `active/` and `archive/` is reported as an integrity error
