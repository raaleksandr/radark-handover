# Proposal

Handover: **HO-20261113-cbcj** ("ho --version and a JSON contract version"), from the engine team
(radark-factory), part of the product's backlog item RB-003.

## Why

Tools that drive `ho` unattended parse its `--json` output and trust its shape. When `ho` and such a tool are
released separately, a changed shape only shows up as a confusing failure deep in a run. A caller needs a cheap,
hub-independent way to ask `ho` once, up front, which package version it is and which JSON contract it speaks, so
it can stop early with one clear message when `ho` is missing, too old or incompatible. Today `ho` has no version
command at all, and the contract number exists only implicitly as `schema_version`.

## What Changes

- New top-level flag `ho --version`: prints `ho <version> (contract <n>)`, e.g. `ho 0.2.0 (contract 1)`, exit 0.
- `ho --version --json`: prints one JSON document `{"schema_version": 1, "version": "0.2.0", "contract": 1}`,
  exit 0. The shape proposed in the handover is accepted unchanged.
- Both forms need no hub and no team: they work without `HO_HUB`/`HO_TEAM`, outside any hub, and even when
  `HO_HUB` points to a missing directory; they read and write nothing.
- The JSON contract version is made explicit: `contract` is defined to be the same number as the `schema_version`
  field every `--json` output carries, now and in the future (one number, never diverging). It covers the shapes of
  all `--json` outputs and the exit codes.
- The rule for when the contract number changes is written down in the spec and README: a removed or renamed JSON
  field, a changed type or meaning of a field, or a changed or removed exit code (or the meaning of one) bumps it; a
  new field, a new command, a new flag, or a new error code within an existing exit code does not.
- The package version gets a single source in `ho.py` (`__version__`) that `pyproject.toml` reads, and is bumped
  from `0.1.0` to `0.2.0` (a new feature). Releasing, tagging, pushing and reinstalling remain the owner's steps.
- No existing field, command or exit code changes; the contract stays at `1`. Not **BREAKING**.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `handover-cli`: adds a version requirement (`ho --version` and its JSON form, hub- and team-independent) and a
  contract-versioning requirement (what the contract number is, that it equals `schema_version`, and when it
  changes).

## Impact

- `ho.py`: top-level `--version` handling in the parser/`main`, a `__version__` constant; `SCHEMA_VERSION` stays `1`.
- `pyproject.toml`: version read from `ho.__version__` (dynamic), now `0.2.0`.
- `README.md`: command reference, both outputs, the contract rule; `CLAUDE.md`'s contract rule points to it.
- `tests/`: new tests for both forms, environment independence and version consistency.
- Consumers: purely additive; the engine team builds its start-up check on the shape above.
