# Design

## Context

See proposal.md (Why) and handover HO-20261114-23cx. `ho.py` dispatches every command through `main()` →
`args.func(ctx, args)`; writing commands are `cmd_new`, `cmd_action` (ack/done/respond/decline/comment/seen),
`cmd_return`, `cmd_close`; `cmd_sync` runs `sync_hub()` (pull --rebase --autostash, `git add -A`, commit, push).
Writes are already atomic per file (`write_text_atomic`) and each action is a new file, so readers see either the
old or the new state of a file; the danger is two writers or a writer plus `sync` interleaving. Standard library
only; Python 3.10+; Windows and POSIX.

## Goals / Non-Goals

**Goals:**
- One writer or `sync` at a time per hub on one machine, with a bounded wait and a stable error.
- No lock can outlive its process.
- A machine-readable signal for radark-factory, without a contract bump.

**Non-Goals:**
- Cross-machine locking (different clones are already merged by git; the lock guards one working copy).
- Locking for readers (a reader may see the state before or after a concurrent write; files are atomic).
- Reporting who holds the lock (PID/team in the message) — possible later as an additive improvement.
- Retrying failed git operations, or making `sync` itself more robust beyond serialisation.

## Decisions

1. **Lock file `<hub>/.ho.lock`, OS lock on a handle.** Open (create if missing) with `os.open(O_RDWR|O_CREAT)`;
   POSIX: `fcntl.flock(fd, LOCK_EX | LOCK_NB)`; Windows: seek 0 and `msvcrt.locking(fd, LK_NBLCK, 1)`. Both are
   released by the kernel when the handle closes, including on process death. Non-blocking attempts in a poll loop
   (≈0.1–0.2 s sleep) give us our own timeout on both platforms (`msvcrt.LK_LOCK` only retries 10×1 s; `flock` has
   no timeout). Unlock and close in `finally`; never delete the file (deleting would race with a waiter that
   already opened it). Alternatives: lock inside `.git/` — rejected, a hub need not be a git repo and may be a
   subdirectory of one; "lock file exists" / `O_EXCL` — rejected by the handover (stale after a crash).
   Both `flock` and `msvcrt` locks also conflict between two handles in the same process, so in-process tests work,
   but the acceptance tests use real child processes.

2. **Where the lock is taken.** A context manager `hub_lock(hub, env, stderr)` wraps the body of the write commands
   and `cmd_sync`, right after `find_hub` and before any hub state is read for the write (so a lifecycle check is
   made against the state after the previous writer finished). Usage errors that need no hub (argparse) fail before
   locking. `init` does not lock: there is no hub yet, and it refuses an existing hub anyway. Reentrancy is not
   needed (no command calls another).

3. **Wait bound: default 300 s, `HO_LOCK_TIMEOUT` env var.** Matches the handover's "e.g. up to 5 minutes". An env
   var rather than a flag, because the engine and agents call many different commands and a flag would have to be
   added everywhere; also keeps tests fast (`HO_LOCK_TIMEOUT=0`/`1`). Parsed as a float ≥ 0; anything else →
   `HoError("usage", ...)` before any write. While waiting, one line `ho: hub is locked by another ho process;
   waiting up to N s...` goes to stderr (stdout stays clean for `--json`).

4. **Timeout error: code `hub_locked`, exit `1`.** README "Contract version" explicitly allows "a new error code
   within an existing exit code" without a contract bump; a new exit code `5` is not listed as additive and could
   break callers that treat unknown exit codes as fatal. Exit 1 already holds operational failures (`git_error`,
   `not_a_git_repo`), so callers distinguish by `error.code`. Raised before anything is written, so "a failed command
   never changes the hub" holds. *Assumption (unattended run): exit 1 rather than a new code; radark-factory asked
   us to choose and document it — reported in the `ho done` message.*

5. **Keeping `.ho.lock` out of git.** `ho init` writes `<hub>/.gitignore` with `.ho.lock`. Inside the lock, before
   the command's own work, `ensure_gitignore(hub)` appends `.ho.lock` to `<hub>/.gitignore` if no line equals it
   (creating the file, preserving existing content and newline at end). Done under the lock so two local processes
   cannot both append. The same idempotent content on two clones merges cleanly. As a second guard `sync_hub` stages
   with the pathspec `:(exclude).ho.lock`, so even a hub whose `.gitignore` was edited never commits the file.
   Alternative `.git/info/exclude` — rejected: needs git calls from non-sync commands (spec: only `sync` runs git),
   and is per clone, so other clones would still show the file. The `.gitignore` belongs to the hub, not to a team,
   so the "never rewrite another team's files" rule is kept; we only ever append one line.

6. **Signal: `"features": ["hub-lock"]` in `ho --version --json`.** A list (the handover's second option) so
   future optional capabilities are announced without new top-level fields. Constant `FEATURES = ("hub-lock",)` in
   `ho.py`. Text `--version` output is unchanged (spec pins it exactly). Contract stays `1`.

## Risks / Trade-offs

- [`sync` holds the lock during network pull/push, so writers wait for slow remotes] → bound is 300 s; `sync` is
  short in practice; agents get a clear `hub_locked` instead of a corrupt rebase.
- [Network filesystems may not honour `flock`/`msvcrt` locks] → documented: the hub working copy should be local;
  sharing between machines goes through git.
- [Two clones on the same machine are different hubs, so they do not lock each other] → correct by design, git
  merges them.
- [`.gitignore` appended on first write shows up as a hub change for the first `sync` after upgrade] → one-time,
  identical content across teams, merges cleanly.
- [Timing-based multi-process tests can be flaky] → tests synchronise through files/handshakes (child signals "lock
  held" before the parent proceeds) instead of sleeps where possible; no retries or skips to hide flakes.

## Migration Plan

Additive. Existing hubs get `.gitignore` on the first writing command. Older engines ignore `features`; newer
engines require `"hub-lock"` only when `parallel_sessions > 1`. Rollback: reinstall the previous `ho`; the leftover
`.ho.lock` and `.gitignore` line are harmless.

## Implementation Notes (apply phase)

- Commands opt in with `set_defaults(locked=True)`; `main()` runs them through `_run_locked`, which locates the hub
  (a missing hub fails without locking), takes `hub_lock`, and runs the command, which reads the hub again under
  the lock. `HO_LOCK_TIMEOUT` is parsed before `.ho.lock` is opened.
- *Assumption:* `ensure_gitignore` runs **after** a writing command succeeded (still under the lock), not before
  its work, so a failed command leaves the hub unchanged (handover-cli "Failed commands SHALL leave the hub
  unchanged"). `sync` calls it itself, after the "is a git working tree" check and before staging. `.ho.lock`
  itself may be created by a failing command; it carries no data, and the tests' hub snapshot ignores it.
- *Assumption:* the exclude pathspec is `:(exclude,glob).ho.loc[k]` rather than `:(exclude).ho.lock`: a literal
  pathspec that names an ignored file makes `git add` fail ("paths are ignored by one of your .gitignore files").
  The glob form matches only the hub-root `.ho.lock`.
- `HO_LOCK_TIMEOUT` also rejects `nan` and `inf`; empty means the default.
