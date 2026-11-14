# Tasks

Handover: HO-20261114-23cx (from radark-factory, expects `done`). Recorded assumptions (unattended propose run):
timeout uses exit code `1` with error code `hub_locked` (not a new exit code), default bound 300 s via
`HO_LOCK_TIMEOUT`, signal is `"features": ["hub-lock"]` in `ho --version --json`, contract stays `1`.
See design.md, Decisions 3–6.

## 1. Lock primitive

- [x] 1.1 Add `hub_lock(hub, env, stderr)` context manager to `ho.py` (stdlib only): open/create `<hub>/.ho.lock`, non-blocking `fcntl.flock` (POSIX) / `msvcrt.locking(LK_NBLCK)` on byte 0 (Windows) in a poll loop, one stderr "waiting" note, release and close in `finally`, never delete the file. Verify: unit test that a second acquisition on the same hub fails with `hub_locked` at `HO_LOCK_TIMEOUT=0` while the first is held and succeeds after release.
- [x] 1.2 Parse `HO_LOCK_TIMEOUT` (float ≥ 0, default 300); invalid values raise `HoError("usage", ...)`; timeout raises `HoError("hub_locked", "<message naming .ho.lock and the bound>", EXIT_USAGE)`. Verify: unit tests for default, `0`, invalid value (`soon`, `-1`).
- [x] 1.3 Add `ensure_gitignore(hub)`: append `.ho.lock` to `<hub>/.gitignore` if no line equals it, creating the file and keeping existing content. Verify: unit tests for missing file, file without the line (no trailing newline), file already containing it (unchanged bytes).

## 2. Wire the lock into commands

- [x] 2.1 Wrap `cmd_new`, `cmd_action` (ack/done/respond/decline/comment/seen), `cmd_return`, `cmd_close` and `cmd_sync` in `hub_lock` right after `find_hub`, calling `ensure_gitignore` inside the lock before the command's work. Leave `teams`, `inbox`, `list`, `show`, `validate`, `--version` and `init` unlocked. Verify: test that with the lock held and `HO_LOCK_TIMEOUT=0` every writing command and `sync` fails with exit `1` / `hub_locked` and the hub snapshot (`assertUnchanged`) is unchanged, while every read command succeeds.
- [x] 2.2 `cmd_init` writes `<hub>/.gitignore` containing `.ho.lock`. Verify: update the "freshly initialized hub" test.
- [x] 2.3 `sync_hub` stages with pathspec `:(exclude).ho.lock` (in `git add` and the cached diff). Verify: git sync test where `.gitignore` lacks the line yet the commit does not contain `.ho.lock`, and `git status --porcelain` never lists it after a writing command.
- [x] 2.4 Add `FEATURES = ("hub-lock",)` and `"features": list(FEATURES)` to `ho --version --json`; text output unchanged; `SCHEMA_VERSION` stays `1`. Verify: version tests assert `features` contains `"hub-lock"`, `contract == 1`, and the exact text line is unchanged.

## 3. Multi-process acceptance tests

- [x] 3.1 New `tests/test_hub_lock.py` with real child processes (`sys.executable ho.py ...` or a small helper script that takes the lock and signals via a file): two concurrent `ho new` processes both exit `0` and both handovers exist; concurrent `ho sync` + `ho new` on a git hub both succeed and the new handover is committed after a final `sync`. Synchronise with handshake files, not bare sleeps. Verify: tests pass on Windows (this machine).
- [x] 3.2 Test: a helper process holds the lock; `ho inbox --json` with `HO_LOCK_TIMEOUT=0` returns at once with exit `0`; `ho done <ID> --json` with `HO_LOCK_TIMEOUT=1` gives exit `1`, `error.code == "hub_locked"`, and the hub is unchanged. Verify: test passes.
- [x] 3.3 Test: lock held ~2 s then released, `ho done` with `HO_LOCK_TIMEOUT=30` waits and succeeds; a killed lock holder leaves `.ho.lock` on disk yet `ho new` with `HO_LOCK_TIMEOUT=0` succeeds. Verify: tests pass.

## 4. Documentation

- [x] 4.1 README: section on the hub lock (which commands lock, `.ho.lock`, `HO_LOCK_TIMEOUT` default 300 s, keep the hub working copy on a local disk), `hub_locked` added to the exit-code table under `1`, `features` added to the `--version --json` example and text on how tools check `"hub-lock"`. Verify: README examples match real output of `python ho.py --version --json`.
- [x] 4.2 `ho_templates/AGENTS.md`: short note that `ho` may wait for another `ho` process and what `hub_locked` means (re-run later; do not delete `.ho.lock`). Verify: `ho init` in a temp dir produces the updated guide.

## 5. Verification and report

- [x] 5.1 Run `python -m unittest discover -s tests`; all pass (record test counts). Run `openspec validate add-hub-lock`. Verify: both succeed.
- [x] 5.2 Commit the implementation locally (no push, tag, release or reinstall). Verify: `git log` shows the commit(s).
- [x] 5.3 Report with `ho done HO-20261114-23cx -m "..."` naming: change `add-hub-lock`, commit hash(es), test counts, lock file `<hub>/.ho.lock`, wait bound (300 s, `HO_LOCK_TIMEOUT`), timeout error `hub_locked` / exit code `1`, and the exact `ho --version --json` output from this tree (`python ho.py --version --json`; the installed `ho` is updated by the owner). Then `ho sync`. Verify: `ho show HO-20261114-23cx --json` shows our state `done`.
