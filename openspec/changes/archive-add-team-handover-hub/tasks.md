# Tasks

Handover: HO-20261113-qjjv. Assumptions are recorded in design.md (Risks / Trade-offs).

## 1. Archive the finished change add-team-handover-hub

- [x] 1.1 Confirm the starting point (verify: `openspec validate --all` passes and `openspec/changes/add-team-handover-hub/tasks.md` has 30/30 tasks checked, none unchecked).
- [x] 1.2 Run `openspec archive add-team-handover-hub -y`, or the manual fallback from design.md Decision 1 if the CLI fails (verify: `openspec/specs/handover-cli/spec.md`, `handover-lifecycle/spec.md`, `handover-store/spec.md` exist; `openspec/changes/archive/<date>-add-team-handover-hub/` holds proposal, design, tasks and specs; `openspec/changes/add-team-handover-hub/` is gone).
- [x] 1.3 Make sure each main spec's `## Purpose` carries the delta's Purpose text, not a placeholder (verify: `grep -n -A2 "## Purpose" openspec/specs/*/spec.md`).
- [x] 1.4 Check the requirements' meaning is unchanged (verify: the `### Requirement:` / `#### Scenario:` blocks of each `openspec/specs/<cap>/spec.md` match those of `openspec/changes/archive/*-add-team-handover-hub/specs/<cap>/spec.md`; a diff of the requirement sections shows no differences).

## 2. Update references

- [x] 2.1 Update `CLAUDE.md` line 5 to point to `openspec/specs/` and the archived change; update README if it mentions the old path (verify: `git grep -n "changes/add-team-handover-hub"` finds nothing outside `openspec/changes/archive/`).

## 3. Verify, commit, report

- [x] 3.1 Run `openspec validate --all` (verify: 0 failed; keep the output for the report).
- [x] 3.2 Run `python -m unittest discover -s tests` (verify: all pass; record the count; failing or flaky tests are handled and reported per hub `AGENTS.md` §10).
- [x] 3.3 Review the diff for private data (no owner paths, private team/product names, hub content), then commit locally — no push, tag, release or reinstall (verify: `git log -1 --stat`, `git status` clean).
- [ ] 3.4 Report with `ho done HO-20261113-qjjv -m "..."`: commit hash, test result (count, pass), `openspec validate --all` output, and the note that this change's own folder stays in `openspec/changes/` until its archive phase (verify: `ho show HO-20261113-qjjv --json` shows `my_state` `done`).

## Workflow follow-up

- After the product team closes HO-20261113-qjjv, archive this change (`archive-add-team-handover-hub`) in the separate archive phase; then `openspec/changes/` holds only `archive/`.
