# code-diff: Change Contract workflow (tool-neutral)

**Problem:** AI writes code faster than people can review it. The danger is **intent drift**: the agent solves a slightly different problem, or shifts the architecture, and every hunk looks reasonable. A normal diff says *what lines changed*; the reviewer needs *which requirement each change serves, what was not asked for, and what decisions were made on their behalf*.

**Design rule:** everything computable is computed by `scripts/cdiff.py` (Python 3.9+ standard library, plus git). The agent supplies only judgment (requirement wording, plain-English explanations, decisions), and `cdiff` rejects the result if it is incomplete. Do not hand-write what the script produces (hunk ids, signals, risk level, report).

`cdiff` below means `python <install-dir>/scripts/cdiff.py` (the exact command is in your instruction file). State lives in `<repo>/.codediff/<task>/` (git-ignored automatically): `base.json` `requirements.json` `lock.json` `diff.json` `diff.patch` `contract.json` `decisions.json` `report.md`.

## Phases (each has a mechanical exit condition)

| # | Phase | Who | Command | Done when |
|---|---|---|---|---|
| 0 | Init | script | `cdiff init --task <slug>` | `base.json` written (base = HEAD, or a snapshot of pre-existing dirty state with `--allow-dirty`) |
| 1 | Requirements | agent + **human** | edit `requirements.json`, show it, then `cdiff lock --confirmed` | the human said yes in chat; lock stores a sha256 |
| 2 | Implement | agent | (normal coding) | code written; no more `requirements.json` edits |
| 3 | Collect | script | `cdiff collect` | `diff.json` has hunks `H1..` and signals `S1..` |
| 4 | Review | **independent reviewer** | see below | `contract.json` passes `cdiff validate` |
| 5 | Render | script | `cdiff render` | `report.md` written |
| 6 | Decide | **human** | `cdiff decide D1 keep\|revert\|discuss` | `cdiff gate` exits 0 |

`cdiff status` shows the current phase.

### Phase 1: requirements (before writing code)

Restate the user's request as a checklist in `requirements.json`, **show it to the user and STOP until they reply yes in chat.** Never pass `--confirmed` without that reply, and never decide it on the user's behalf. Per requirement:
- `text`: one testable statement. `acceptance`: how a human would check it.
- `scope`: path globs the change should touch (`src/reports/**`). This is the drift detector: any changed file outside every scope becomes a **high** `out_of_scope` signal. Include test paths if tests are expected.
- `expects`: signal kinds this requirement legitimately causes (e.g. `["dependency_manifest"]` for "add library X", `["public_api"]` for "add an endpoint"). Expected signals need no decision; every other medium/high signal does. `out_of_scope` can never be expected.
- Top-level `non_goals` (things not to change) and `allowed_extra` globs (e.g. `**/*.md`).

If scope or `expects` turn out wrong mid-task, tell the user, edit, and re-run `lock --confirmed` after their yes. Editing after lock without re-locking makes `collect` and `validate` fail.

### Phase 4: independent review (the author must not grade its own homework)

Pick the first option your tool supports:

**A. Subagent** (Claude Code Agent tool, Cursor/Copilot/Codex sub-tasks if available). Spawn a fresh agent that has not seen the conversation. Give it only `requirements.json`, `diff.json`, `diff.patch`, the repo path, and `references/reviewer-prompt.md` verbatim. It writes `contract.json`.

**B. Reviewer pack** (any tool, and the strongest check when you use a different model). Run `cdiff reviewer-pack`. It writes `reviewer-pack.md`: the prompt plus all inputs in one file. The human pastes it into a FRESH chat (ideally another model), saves the reply to a file, then run `cdiff import-contract <file> --reviewer "<model>"`.

Then run `cdiff validate`. On failure, send the printed errors back to the same reviewer and repeat. **Do not patch the contract yourself to make it pass.** Self-review is allowed only if the user explicitly says so; set `"reviewed_by": "self"` so the report shows it.

### Phases 5-6: report and decisions

`cdiff render` computes risk (HIGH if any high signal), ranks decisions (top 5 shown, rest in an appendix) and builds the coverage tables. Show the user `report.md` Section 1 first, in plain words, then ask for each decision and record the answer with `cdiff decide`. `revert` means: undo those hunks, then re-run phases 3-5 (decision fingerprints keep answers that are still valid). Finish with `cdiff gate`; only exit code 0 means "reviewed".

## What is deterministic

`collect` signals (kind: severity): `out_of_scope`: high | `dependency_manifest`, `migration`: high | `protected_path`, `forbidden_import` (from rules): high | `file_deleted`, `new_directory`, `ci_config`, `config`, `public_api`, `cross_module_import`: medium | `file_renamed`, `lockfile`, `no_tests_changed`: low. Public-API detection covers py/js/ts/cs/java/go and import-edge detection covers py/js/ts, both regex-based: misses are possible, false positives are cheap.

`validate` fails (exit 1) if: requirements changed since lock; the working tree changed since collect; any hunk is unmapped and not in a decision ("no silent changes"); unknown requirement/hunk/signal ids; a requirement is neither mapped nor declared `unimplemented`; any medium/high signal is neither in a decision nor in the owning requirement's `expects`; a decision lacks plain text, `why_it_matters`, impact or recommendation.

`gate` fails unless every decision is `keep`, recorded against the current fingerprint.

Optional project-wide architecture rules in `.codediff/rules.json` (commit it):
```json
{"protected_paths": ["src/core/**", "migrations/**"],
 "forbidden_imports": [{"in": "src/ui/**", "import_regex": "^(db|sqlalchemy)", "why": "UI must not touch the DB"}]}
```

## Writing style for the contract (layman terms)

Cause and consequence, not code restatement: "Before, each page loaded its own data; now all pages go through one shared service, so 4 other pages depend on it", never "refactored fetchData into ReportService". Max ~7 decisions; merge related ones, since a long list stops being read. `recommend` must be an honest opinion, and `assumptions` should rarely be empty (limits, formats, error handling, defaults chosen).

## Limits

Requirements are only as good as phase 1. Signals are heuristics, not proof. A clean report means no unexplained change was *detected*, not that the code is correct; tests still apply. Whether an agent pauses for the human's yes in phase 1 depends on that agent following its instructions; `--confirmed` is a speed bump, not a lock.
