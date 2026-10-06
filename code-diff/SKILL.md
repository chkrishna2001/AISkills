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

# code-diff (Claude Code entry point)

The full workflow is tool-neutral and lives in `WORKFLOW.md` next to this file. **Read it now and follow it.** The engine is `scripts/cdiff.py` in this same folder; invoke it by its absolute path (resolve this skill's directory), e.g. `python <this-skill-dir>/scripts/cdiff.py status`.

Claude Code specifics:
- Phase 1: after writing `requirements.json`, show it to the user and wait for their reply before `lock --confirmed`.
- Phase 4: use option A. Spawn a fresh subagent with the Agent tool (`general-purpose`), paste `references/reviewer-prompt.md` verbatim with `{DIR}` = `.codediff/<task>` and `{REPO}` = the repo root, and give it no conversation context. Send `cdiff validate` errors back to the same subagent with SendMessage. If the user prefers a different model as reviewer, use option B (`cdiff reviewer-pack`).
- `.codediff/` state is git-ignored automatically; only `.codediff/rules.json` is meant to be committed.
