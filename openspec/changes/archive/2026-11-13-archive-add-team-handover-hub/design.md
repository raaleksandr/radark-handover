# Design

## Context

See proposal.md — Why. `openspec/changes/add-team-handover-hub/` holds three `## ADDED Requirements` deltas,
each preceded by a `## Purpose` section; `openspec/specs/` is empty. The OpenSpec CLI is available and
`openspec validate --all` currently passes (with warnings about long requirement texts only).

## Goals / Non-Goals

**Goals:**
- Main specs for `handover-cli`, `handover-lifecycle`, `handover-store` with exactly the requirements and
  scenarios of the old change.
- The old change folder lives only under `openspec/changes/archive/`.
- No reference to `openspec/changes/add-team-handover-hub/` outside the archive.

**Non-Goals:**
- Rewording or splitting requirements (the "requirement text is very long" warnings stay; fixing them would
  change spec text and belongs in a separate change if wanted).
- Any change to `ho.py`, templates, tests or the JSON contract.
- Archiving this change (`archive-add-team-handover-hub`) — that is a separate phase after the product team
  closes the handover.

## Decisions

1. **Use `openspec archive add-team-handover-hub -y`** rather than moving files by hand: it applies the deltas
   and names the archive folder consistently. Fallback (if the CLI fails): write each delta's requirements into
   `openspec/specs/<capability>/spec.md` under `# <capability> Specification`, `## Purpose`, `## Requirements`,
   and `git mv` the folder to `openspec/changes/archive/<YYYY-MM-DD>-add-team-handover-hub/`.
2. **Preserve the Purpose text.** If the archive writes a placeholder Purpose (e.g. "TBD ..."), replace it with
   the `## Purpose` paragraph from the corresponding delta — a format-only fix the handover permits.
3. **Verify meaning is unchanged by comparison:** the `### Requirement:` and `#### Scenario:` blocks in each main
   spec must equal those in the archived delta.
4. **References:** `CLAUDE.md` line 5 is the only hit outside the change folder (README has none); it will point
   to `openspec/specs/` with the archived change as the original design. The generic `openspec/changes/<name>/`
   mention in CLAUDE.md's rules stays — it describes where new changes go.

## Risks / Trade-offs

- [Acceptance check "`openspec/changes/` has only `archive/`" vs. this change's own folder] → This change's folder
  `openspec/changes/archive-add-team-handover-hub/` necessarily stays until its own archive phase (the workflow
  archives a change only after the author closes the handover). Assumption, made without a user to ask: after
  apply, `openspec/changes/` holds `archive/` plus this change; once this change is archived it holds only
  `archive/`. This is stated in the `ho done` report.
- [The handover says "no separate new OpenSpec change is needed"] → The run's workflow and the repository rule
  "every change is an OpenSpec change" require one, so this lightweight `skip_specs` change records the work; it
  adds no spec deltas and so does not interfere with the archive.
- [Archive date in the folder name] → Uses the date of the apply session; it is only a label.
- [Public repository] → Only spec files and a doc path are touched; review the diff for private paths or names
  before committing.
