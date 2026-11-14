# Proposal

Requested by handover **HO-20261114-23cx** ("Hub lock: ho serialises hub writes and sync (for RB-008)") from
radark-factory, which needs it for its `parallel-sessions` change (several agent sessions, one per repository,
calling `ho` against the same hub at the same time).

## Why

A hub is one plain directory, usually one git working copy, shared by every `ho` process on the machine. Nothing
serialises them today: two `ho sync` runs (or a `sync` and a `done`) at the same moment can collide on git's index
lock, interrupt each other's `pull --rebase`, or commit half of another command's files. Once the engine runs
sessions in parallel this becomes a routine failure, so `ho` itself must serialise the commands that write.

## What Changes

- **Hub lock.** Every command that writes to the hub — `new`, `ack`, `done`, `respond`, `decline`, `comment`,
  `seen`, `return`, `close` — and `sync` hold an exclusive OS-level lock on `<hub>/.ho.lock` for their whole run.
  Read commands (`teams`, `inbox`, `list`, `show`, `validate`) and `--version` never take or wait for it. `init`
  does not lock (the hub does not exist yet).
- **Bounded wait.** A command that finds the lock taken waits, polling, for up to 300 seconds (configurable with the
  new environment variable `HO_LOCK_TIMEOUT`, seconds, `0` = do not wait), printing one note on stderr while it
  waits. Past the bound it fails with the new error code `hub_locked`, exit code `1`, and the hub unchanged.
- **No stuck lock.** The lock is a kernel lock on an open file handle (`msvcrt.locking` on Windows, `fcntl.flock`
  elsewhere), released by the OS when the process exits or crashes. The file's existence means nothing; it is
  never deleted.
- **Ignored by git.** `ho init` writes a `.gitignore` containing `.ho.lock`; on existing hubs the first locked
  command appends that line to `<hub>/.gitignore` if it is missing. `sync` additionally never stages `.ho.lock`.
- **Feature signal.** `ho --version --json` gains an additive field `"features": ["hub-lock"]` (a list, so later
  capabilities can be announced the same way). The JSON contract stays `1`; the text form of `--version` is
  unchanged.

No breaking change: a new field, a new error code inside an existing exit code and a new environment variable are
all additions under README "Contract version".

## Capabilities

### New Capabilities
- `hub-lock`: serialisation of concurrent `ho` processes on one hub — which commands lock, the lock file, the
  bounded wait and its error, release on process end, and keeping the lock file out of git.

### Modified Capabilities
- `handover-cli`: `Version information` gains the `features` list in `--json`; `Errors and exit codes` lists the
  `hub_locked` error under exit code `1`.
- `handover-store`: `Hub layout` — an initialized hub also contains a `.gitignore` that ignores `.ho.lock`.

## Impact

- `ho.py`: a lock helper (stdlib `msvcrt` / `fcntl`, `time`), wrapping of the write commands and `sync`, `init`
  writes `.gitignore`, `sync` excludes `.ho.lock` from staging, `--version --json` adds `features`.
- `ho_templates/AGENTS.md` and README: lock behaviour, `HO_LOCK_TIMEOUT`, `hub_locked`, `features`.
- Tests: new multi-process tests (real child processes) plus unit tests; the full suite must stay green.
- radark-factory: can require `"hub-lock" in features` when `parallel_sessions > 1`; older engines ignore the field.
