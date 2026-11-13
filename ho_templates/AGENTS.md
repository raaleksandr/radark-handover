# Handover hub - guide for AI agents

This directory is a **handover hub**: teams pass Markdown documents ("handovers")
to each other here and record what they did with them. Never edit files in
`active/` or `archive/` by hand - always use the `ho` CLI, which keeps the
history consistent.

Registered teams:

{{teams}}

## 1. Identify your team

Set your team once per session (or pass `--team <id>` to every command):

```sh
export HO_TEAM=backend          # POSIX shells
$env:HO_TEAM = "backend"        # PowerShell
```

If you are not running inside the hub directory, also set `HO_HUB=<path to hub>`.

## 2. Always sync first and last

```sh
ho sync      # FIRST: get other teams' handovers and answers
...          # work
ho sync      # LAST: publish what you did
```

## 3. Find your work

Always use `--json` when you parse output:

```sh
ho inbox --json
```

The result has three arrays:

- `action_required` - handovers addressed to your team that still need your action
  (including parts the sender returned to you);
- `to_review` - handovers your team sent where every recipient is finished:
  review the results, then `close` or `return`;
- `updates` - handovers you take part in with new activity (comments, answers)
  since your team last acted; read them and run `ho seen <ID>` when done.

Each item has `id`, `title`, `from`, `expects`, `my_state`, `recipients`,
`priority`, `due` and `path` (the absolute path of `handover.md`).

## 4. Read a handover

```sh
ho show <ID> --json
```

`body` is the original Markdown; `events` is the full history (answers,
comments, returns) in order; `allowed_actions` lists what your team can do now.
IDs can be shortened to their last 4+ characters (e.g. `ab3x`).

## 5. Act according to `expects`

| `expects`  | what is wanted                       | command                                          |
|------------|--------------------------------------|--------------------------------------------------|
| `ack`      | just confirm you noted it            | `ho ack <ID>`                                    |
| `done`     | do the work, then report completion  | `ho done <ID> -m "what was done"`                |
| `response` | answer back with Markdown            | `ho respond <ID> --body-file answer.md`          |

Mapping: `ack` -> `ack`, `done` -> `done`, `response` -> `respond`.

- `ho ack <ID>` on a `done`/`response` handover means "taken in progress".
- Can't or won't do it? `ho decline <ID> -m "reason"`.
- Need clarification? `ho comment <ID> -m "question"` (state does not change).
- Bodies: `-m "text"`, `--body-file <path>` (UTF-8, recommended for long Markdown)
  or `--body -` to read stdin.

## 6. If your team is the sender

- Answer not good enough? Send that team's part back:
  `ho return <ID> --to <team> -m "Please add the error codes"`.
- Everything fine? `ho close <ID>` (moves it to the archive).
- Cancel a handover that is still open: `ho close <ID> --force -m "reason"`.

## 7. Create a handover

Write the document as a Markdown file, then:

```sh
ho new --to backend,frontend --expects response --title "Checkout redesign" --body-file brief.md --json
```

Options: `--priority high|normal|low`, `--due 2026-10-31`, `--tag <tag>`,
`--ref <ID>` (related handover), `--attach <file>`, `--auto-close`
(close automatically once all recipients are satisfied - handy for FYI `ack`s).
The JSON result's `id` is the new handover's ID.

## 8. Errors

With `--json`, failures print `{"error": {"code": ..., "message": ...}}`.
Exit codes: `1` bad input, `2` not found, `3` action not allowed in the current
state (read `message` - it lists the allowed actions), `4` hub integrity problem
(run `ho validate`).
