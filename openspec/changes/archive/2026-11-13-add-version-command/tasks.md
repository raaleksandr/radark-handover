# Tasks

Handover: HO-20261113-cbcj. Assumptions (shape accepted as proposed, `contract` == `schema_version` forever,
`--version` with any command or other option is a usage error, version bumped to 0.2.0) are recorded in design.md.

## 1. Version source and contract constants

- [x] 1.1 Add `__version__ = "0.2.0"` to `ho.py`; switch `pyproject.toml` to `dynamic = ["version"]` with `[tool.setuptools.dynamic] version = {attr = "ho.__version__"}` (verify: `python -c "import ho; print(ho.__version__)"` prints `0.2.0`; `pyproject.toml` has no static `version =` under `[project]`).
- [x] 1.2 Introduce `HUB_FORMAT_VERSION = 1` and use it for `hub.json` in `ho init`; keep `SCHEMA_VERSION = 1` for `--json` output, with a comment that it is the JSON contract version (verify: existing tests pass unchanged; a fresh `python ho.py init` hub's `hub.json` still has `"schema_version": 1`).

## 2. `ho --version` and `ho --version --json`

- [x] 2.1 Add top-level `--version` (and `--json` accepted with it) to the parser and handle it in `main()` before any hub/team lookup, per design.md Decision 5 (verify: `python ho.py --version` prints `ho 0.2.0 (contract 1)`; `python ho.py --version --json` prints `{"schema_version": 1, "version": "0.2.0", "contract": 1}` (indented); both exit 0; `ho -h` lists `--version`).
- [x] 2.2 Add tests in `tests/test_cli.py`: text output exact and stderr empty; JSON keys/types and `version == ho.__version__`, `contract == ho.SCHEMA_VERSION`; both argument orders; no `HO_HUB`/`HO_TEAM` and outside a hub; `HO_HUB` pointing to a missing directory; `contract` equals `schema_version` of `inbox --json`; `ho --version inbox` exits 1 (verify: `python -m unittest tests.test_cli` passes).
- [x] 2.3 Document in `README.md`: `--version` in the command reference with both exact outputs; a "Contract version" subsection under "JSON output" with the rule (bump: removed/renamed field, changed type or meaning, changed/removed exit code or its meaning; no bump: new field, command, flag, error code) and that `contract` always equals `schema_version`; in `CLAUDE.md` point the contract rule to that section (verify: the README's documented outputs match the actual command output byte for byte; no private names, paths or hub IDs in the diff).

## 3. Verify, commit, report

- [x] 3.1 Run `python -m unittest discover -s tests` and `openspec validate add-version-command --strict` (verify: all tests pass, record the count; validation passes; failing or flaky tests are handled and reported per hub `AGENTS.md` §10).
- [x] 3.2 Review the diff for private data, then commit locally — no push, tag, release or reinstall (verify: `git log -1 --stat`; `git status` clean).
- [x] 3.3 Report with `ho done HO-20261113-cbcj -m "..."` listing the change name `add-version-command`, the commit hash(es), the test count, the exact outputs of `ho --version` and `ho --version --json` (from `python ho.py`, since reinstalling is the owner's step), the decision that `contract` always equals `schema_version`, and a note that the installed `ho` needs a reinstall to get the flag (verify: `ho show HO-20261113-cbcj --json` shows `my_state` `done`).

## Workflow follow-up

- After the engine team closes HO-20261113-cbcj, archive this change (and sync its delta into `openspec/specs/handover-cli/spec.md`) in the separate archive phase.

## Apply notes

- 3.1: 97 tests pass (7 new); `openspec validate add-version-command --strict` passes.
- Assumption (no user available): a top-level `--json` without `--version` (e.g. `ho --json inbox`) stays a usage
  error (exit 1), as before this change; `--json` goes after the command for every other command.
