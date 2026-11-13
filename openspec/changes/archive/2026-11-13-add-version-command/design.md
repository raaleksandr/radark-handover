# Design

## Context

`ho.py` builds an argparse parser whose top level has only subcommands; `--json` is defined on each subcommand
(the `common` parent parser), and `main()` detects JSON mode with `"--json" in argv`. Every `--json` document is
produced by `_dump()`, which prepends `schema_version: SCHEMA_VERSION` (`1`). The same constant is also written
into `hub.json` by `ho init`. The package version lives only in `pyproject.toml` (`0.1.0`); `ho.py` does not know
it, and `ho.py` must also work when run from the source tree without being installed. See proposal.md for
motivation and the specs delta (`specs/handover-cli/spec.md`) for the required behaviour.

## Goals / Non-Goals

**Goals:**
- `ho --version` / `ho --version --json` exactly as specified, fast, with no hub or team access.
- One source of truth for the package version that works installed and from the source tree.
- An explicit, documented contract number and bump rule.

**Non-Goals:**
- No `version` subcommand, no `-V` short flag (can be added later without a contract bump).
- No change to any existing output, error code or exit code; `SCHEMA_VERSION` stays `1`.
- No release, tag, push or reinstall.

## Decisions

1. **Shape: accept the engine's proposal unchanged.** Text `ho <version> (contract <n>)`; JSON
   `{"schema_version": 1, "version": "<version>", "contract": <n>}` built through the existing `_dump()`, so it
   carries `schema_version` like every other `--json` output. Alternative (a `version` subcommand) rejected: the
   engine asked for `--version`, and a flag is the common CLI convention.

2. **`contract` and `schema_version` are the same number forever.** `contract` is defined as the value of
   `SCHEMA_VERSION`; the JSON form reports it under its descriptive name so callers can check compatibility
   without knowing `schema_version`'s history. Alternative (two independently versioned numbers) rejected: two
   numbers that describe the same thing invite drift and leave callers unsure which one to check.

3. **Decouple `hub.json`'s format version from the output contract.** `ho init` writes `schema_version` into
   `hub.json`; that is the hub file format, not the CLI output contract. The implementation introduces a separate
   constant (`HUB_FORMAT_VERSION = 1`) for `hub.json` so a future contract bump does not silently change the hub
   format (and vice versa). Values and files stay identical today, so this is internal only.

4. **Version source: `__version__ = "0.2.0"` in `ho.py`, read by `pyproject.toml`** through
   `[project] dynamic = ["version"]` and `[tool.setuptools.dynamic] version = {attr = "ho.__version__"}`
   (setuptools reads the attribute statically, so the stdlib-only rule is unaffected). Alternative
   (`importlib.metadata.version("radark-handover")`) rejected: it fails or reports a stale installed version when
   running `python ho.py` from the tree, and costs an import at every start. A test asserts that the text, JSON and
   `ho.__version__` agree.

5. **Parsing.** Register `--version` (store_true) on the top-level parser so it appears in `ho -h`, and accept
   `--json` alongside it at top level. In `main()`, when `--version` is set and no command is given, render the
   version (via the normal `want_json` path) and return `0` before any hub or team lookup. `--version` together
   with a command, or with any other argument, is a usage error (exit `1`) — this keeps one meaning per
   invocation. argparse's built-in `action="version"` is not used because it cannot produce the JSON form and
   exits through `SystemExit` outside the normal output path.
   Note: argparse may let a subcommand's `--json` default overwrite a top-level value; JSON mode is still decided
   by `"--json" in argv`, so this does not matter, but tests cover both argument orders (`--version --json` and
   `--json --version`).

6. **Contract bump rule (spec + README).** Bump for: a removed or renamed field, a changed type or meaning of a
   field, a changed or removed exit code or a changed meaning of one. No bump for: a new field, command, flag or
   error code. `CLAUDE.md`'s existing rule (bump `SCHEMA_VERSION`, agree with the engine team first) stays, and
   points to the README section.

7. **Package version bump to `0.2.0`.** A new user-visible feature on a `0.x` line; the handover allows it.

## Risks / Trade-offs

- [`--version` with other options, e.g. `ho --version --hub x`] → treated as a usage error per Decision 5; if a
  caller turns out to pass such options, relaxing it later is additive. Assumption recorded because no user was
  available to ask.
- [`pyproject.toml` dynamic version and an older setuptools] → `requires = ["setuptools>=68"]` already supports
  `attr:` with static reading; the apply phase verifies with a build-free check (`python -c` reading the
  attribute) since installing is the owner's step.
- [An older installed `ho` has no `--version`] → it exits `1` with a usage error and no JSON, which the engine
  already treats as "too old" (per the handover).
- [Hub-independence regressions] → a test runs `--version` with `HO_HUB` set to a non-existent path and asserts
  success.

## Migration Plan

Additive only. Callers that do not use `--version` see no difference. Rollback is reverting the commit.
