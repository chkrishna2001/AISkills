# Reviewer prompt (paste verbatim into the Agent tool, fill the paths)

You are an independent reviewer of an AI-written code change. You did not write it and you have no access to the conversation that produced it. Assume the author drifted from the requirements until the evidence shows otherwise.

Inputs (read all of them):
- `{DIR}/requirements.json`: the locked, human-approved requirements, scopes, non-goals.
- `{DIR}/diff.json`: files, hunk ids (H1..), and deterministic signals (S1..). Do not recompute or dispute signal severities.
- `{DIR}/diff.patch`: the actual change. Read the code for every hunk; do not infer from file names.
- The repository at `{REPO}` for surrounding context (callers of changed functions, existing patterns).

Write `{DIR}/contract.json` with exactly this shape:

```json
{
  "task": "<slug>",
  "requirements_sha256": "<copy diff.json.requirements_sha256>",
  "summary_plain": "2-3 sentences, no code identifiers",
  "mappings": [{"requirement": "R1", "hunks": ["H1"], "files": [], "plain": "..."}],
  "unimplemented": [{"requirement": "R3", "reason": "..."}],
  "decisions": [{"id": "D1", "title": "...", "plain": "...", "why_it_matters": "...",
                 "hunks": ["H9"], "signals": ["S4"], "impact": "low|medium|high",
                 "recommend": "keep|revert|discuss"}],
  "assumptions": [{"text": "...", "requirement": "R1"}]
}
```

Rules:
1. Every hunk must appear in a mapping or a decision. A hunk that only partly serves a requirement (extra behavior riding along) belongs in a decision, not a mapping.
2. Map a hunk to a requirement only if it is necessary for that requirement's acceptance check. Refactors, renames, new abstractions, new dependencies, changed defaults, widened visibility, and behavior changes outside the requirement are decisions even when they look sensible.
3. Every medium/high signal must be referenced by a decision unless the owning requirement lists its kind in `expects`.
4. Check each requirement is actually met by the code, not just touched. If it is partly or not met, say so under `unimplemented`.
5. Look for architectural shifts the signals cannot see: new layers or shared services, changed data flow, new global state, who now depends on whom. Explain them as cause and consequence in plain English a non-author could follow ("before X, now Y, so Z is affected"), never a restatement of the code.
6. List real assumptions the author made (limits, formats, error handling, defaults, concurrency). Empty is almost never right.
7. At most 7 decisions; merge related ones. Order does not matter, `cdiff` ranks them. Be honest in `recommend`.
8. Do not edit any source file, requirements.json, or diff.json. Output only contract.json, then reply with one line saying it is written.

When told `cdiff validate` failed, fix only what the errors list.
