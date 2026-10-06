---
name: code-diff
description: >-
  Requirement-traced code review ("Change Contract") for AI-written changes.
  Use when starting any non-trivial coding task the user wants to be able to
  review properly, or when asked to "explain the diff", "check for drift",
  "what did you change that I didn't ask for", or review agent-written code
  against a requirement. Locks the requirements before coding, then maps every
  diff hunk to a requirement or to an explicit human decision, with
  deterministic checks that refuse to pass if anything is unexplained.
---

# code-diff: Change Contract workflow

**Problem:** AI writes code faster than people can review it, and the danger is not a bad line but **intent drift**: the agent solves a slightly different problem, or shifts the architecture, and every hunk looks reasonable. A normal diff says *what lines changed*; the reviewer needs *which requirement each change serves, what was not asked for, and what decisions were made on their behalf*.

**Design rule:** everything computable is computed by `scripts/cdiff.py` (stdlib Python, needs git). The LLM supplies only judgment (requirement wording, plain-English explanation, decisions), and `cdiff` rejects the result if it is incomplete. Do not hand-write what the script produces (hunk ids, signals, risk level, report).

```
python C:\Users\chkri\source\repos\AISkills\code-diff\scripts\cdiff.py <cmd>    # "cdiff" below
```

State lives in `<repo>/.codediff/<task>/` (git-ignored automatically):
`base.json` `requirements.json` `lock.json` `diff.json` `diff.patch` `contract.json` `decisions.json` `report.md`.

## The phases (each has a mechanical exit condition)

| # | Phase | Who | Command | Done when |
|---|---|---|---|---|
| 0 | Init | script | `cdiff init --task <slug>` | `base.json` written (base = HEAD, or snapshot of pre-existing dirty state with `--allow-dirty`) |
| 1 | Requirements | agent + **human** | edit `requirements.json`, show it, then `cdiff lock --confirmed` | human said yes; lock stores sha256 |
| 2 | Implement | agent | (normal coding) | code written; no further `requirements.json` edits |
| 3 | Collect | script | `cdiff collect` | `diff.json` has hunks `H1..` and signals `S1..` |
| 4 | Review | **independent subagent** | see below | `contract.json` passes `cdiff validate` |
| 5 | Render | script | `cdiff render` | `report.md` |
| 6 | Decide | **human** | `cdiff decide D1 keep\|revert\|discuss` | `cdiff gate` exits 0 |

`cdiff status` shows the current phase.

### Phase 1: requirements (before writing code)

Restate the user's request as a checklist in `requirements.json` and **show it to the user, wait for a yes**. Never pass `--confirmed` without that yes. Per requirement:
- `text`: one testable statement. `acceptance`: how a human would check it.
- `scope`: path globs the change should touch (`src/reports/**`). This is the drift detector: any changed file outside every scope becomes a **high** `out_of_scope` signal. Include test paths if tests are expected.
- `expects`: signal kinds this requirement legitimately causes (e.g. `["dependency_manifest"]` for "add library X", `["public_api"]` for "add an endpoint"). Expected signals do not need a decision; everything else medium/high does. `out_of_scope` can never be expected.
- Top-level `non_goals` (things not to change) and `allowed_extra` globs (e.g. `**/*.md`).

If scope or `expects` turn out wrong mid-task, that is itself a decision: tell the user, edit, and re-run `lock --confirmed`. Editing after lock without re-locking makes `collect` and `validate` fail.

### Phase 4: independent review (the author must not grade its own homework)

Spawn a **fresh subagent** (Agent tool, `general-purpose`) that has not seen the conversation. Give it only:
1. paths to `requirements.json`, `diff.json`, `diff.patch`, and the repo;
2. the prompt in `references/reviewer-prompt.md` (paste it verbatim).

It writes `contract.json`. Then run `cdiff validate`. On failure, send the printed errors back to the same reviewer (SendMessage) and repeat; do **not** patch the contract yourself to make it pass. Skipping the subagent is allowed only if the user says so, and the report must then say "self-reviewed".

### Phase 5-6: report and decisions

`cdiff render` computes risk (HIGH if any high signal), ranks decisions (top 5 shown, rest in appendix) and builds coverage tables. Show the user `report.md` Section 1 first, plainly, then ask for each decision. Record answers with `cdiff decide`. `revert` means: undo those hunks, then re-run phases 3-5 (decision fingerprints keep answers that are still valid). Finish with `cdiff gate`; only exit 0 means "reviewed".

## What is deterministic

`collect` signals (kind: severity): `out_of_scope`: high | `dependency_manifest`, `migration`: high | `protected_path`, `forbidden_import` (from rules): high | `file_deleted`, `new_directory`, `ci_config`, `config`, `public_api`, `cross_module_import`: medium | `file_renamed`, `lockfile`, `no_tests_changed`: low. Public-API and import detection cover py/js/ts (imports) and py/js/ts/cs/java/go (public symbols), regex-based, so treat misses as possible and false positives as cheap.

`validate` fails (exit 1) if: requirements changed since lock; working tree changed since collect; any hunk is unmapped and not in a decision ("no silent changes"); unknown requirement/hunk/signal ids; a requirement is neither mapped nor declared `unimplemented`; any medium/high signal is neither in a decision nor in the owning requirement's `expects`; a decision lacks plain text, `why_it_matters`, impact or recommendation.

`gate` fails unless every decision is `keep` and recorded against the current fingerprint.

Optional project-wide architecture rules in `.codediff/rules.json` (commit it):
```json
{"protected_paths": ["src/core/**", "migrations/**"],
 "forbidden_imports": [{"in": "src/ui/**", "import_regex": "^(db|sqlalchemy)", "why": "UI must not touch the DB"}]}
```

## Writing style for the contract (layman terms)

Cause and consequence, not code restatement: "Before, each page loaded its own data; now all pages go through one shared service, so 4 other pages depend on it", never "refactored fetchData into ReportService". Max ~7 decisions; merge related ones, since a long list stops being read. `recommend` must be an honest opinion, and `assumptions` should never be empty (streaming, formatting, error handling, limits, defaults chosen).

## Limits

Requirements are only as good as phase 1. Signals are heuristics, not proof. A clean report means no unexplained change was *detected*, not that the code is correct; tests still apply. Same-session subagent review is independent of the author's reasoning, not of the model.
