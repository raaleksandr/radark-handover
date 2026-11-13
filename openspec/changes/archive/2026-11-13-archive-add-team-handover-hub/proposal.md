# Proposal

Handover: **HO-20261113-qjjv** ("RB-011: archive OpenSpec change add-team-handover-hub"), from the product team.

## Why

The original design change `add-team-handover-hub` is fully implemented (30 of 30 tasks done) but was never
archived, so `openspec/specs/` is empty and the repository has no accepted source of truth for `ho`'s behaviour
and JSON contract. Upcoming work (a contract version, a hub lock) needs to be written as deltas against accepted
specs, so the finished change has to be archived first.

## What Changes

- Archive `openspec/changes/add-team-handover-hub` the normal OpenSpec way (`openspec archive
  add-team-handover-hub`): its three spec deltas become `openspec/specs/handover-cli/spec.md`,
  `openspec/specs/handover-lifecycle/spec.md` and `openspec/specs/handover-store/spec.md`, and the change folder
  moves to `openspec/changes/archive/<date>-add-team-handover-hub/`.
- Keep the requirements' meaning unchanged; only format adjustments the archive needs are allowed (e.g. carrying
  each delta's `## Purpose` text into the main spec).
- Update references to the old change path (`CLAUDE.md` line 5; README if any) to point to `openspec/specs/` and
  the archived change.
- No code, CLI behaviour, JSON field or exit code changes.

## Capabilities

### New Capabilities

(none in this change's own deltas — the capabilities `handover-cli`, `handover-lifecycle` and `handover-store`
reach `openspec/specs/` by archiving `add-team-handover-hub`, whose deltas already define them)

### Modified Capabilities

(none — this change declares `skip_specs: true`: it moves accepted requirements into place and changes no
requirement)

## Impact

- `openspec/specs/` (new main specs), `openspec/changes/` (old change moved to `archive/`), `CLAUDE.md` (path
  reference).
- No impact on `ho.py`, `ho_templates/`, tests, the JSON contract or exit codes; `SCHEMA_VERSION` stays as is.
- Consumers (radark-factory, agents) are unaffected.
