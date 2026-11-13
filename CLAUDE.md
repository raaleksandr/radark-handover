# CLAUDE.md

radark-handover is `ho`: a zero-dependency CLI (Python 3.10+, standard library only, one module `ho.py`) for
passing Markdown handovers between teams through a plain-file hub, usually a git repository. Start with
[README.md](README.md); the accepted specs are in `openspec/specs/` (`handover-cli`, `handover-lifecycle`,
`handover-store`), the original design in `openspec/changes/archive/2026-11-13-add-team-handover-hub/`.

## Commands

- Tests: `python -m unittest discover -s tests` (all must pass; they run in-process against temporary hubs).
- Try it: `python ho.py init --hub <tmp dir> --teams "a=A,b=B"`, then `python ho.py new --hub <tmp dir> ...`.

## Rules

- **Standard library only**, single module `ho.py`; the template for `ho init` lives in `ho_templates/`.
- **The JSON output and exit codes are a contract.** radark-factory and AI agents parse `--json` output and rely
  on exit codes 1–4. Additive changes (a new field, command or flag) are fine; renaming or removing a field,
  changing its meaning or an exit code needs a `SCHEMA_VERSION` bump and is agreed with the radark-factory team
  first. The full rule is in README.md, "Contract version"; `ho --version --json` reports it as `contract`.
- Hub files must stay merge-friendly: each action is a new file; never rewrite another team's files.
- Every change is an OpenSpec change (`openspec/changes/<name>/`).
- **This repository is public.** Never commit a user's paths, team or product names, hub content or personal data.
  Examples use neutral team names (`product`, `frontend`, `backend`, `mobile`).
- Do not install, reinstall, tag, push or release from a session — those are the owner's steps.
- Docs and code comments in English.

## Working through a handover hub

When a radark pipeline runs you, `HO_HUB` and `HO_TEAM` are set.

- Before the first handover in a session, read `$HO_HUB/AGENTS.md`; run `ho sync` first and last. Use the
  installed `ho` for hub work, and `python ho.py` from this tree only against temporary test hubs.
- Work a handover asks for becomes an OpenSpec change that cites the handover ID; report it with
  `ho done <ID> -m "<summary>"`, archive the change only after the author closes the handover.
- Failing and flaky tests: follow `$HO_HUB/AGENTS.md` (if it has a rule for them); never hide a flake with
  retries or skips.
- Unclear question: `ho comment <ID> -m "..."` instead of guessing.
