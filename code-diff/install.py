#!/usr/bin/env python3
"""Install the code-diff skill for the AI coding agent(s) you choose.

  python install.py                         # interactive: pick agents and scope
  python install.py --agent codex --agent cursor --project C:\\path\\to\\repo
  python install.py --agent claude --scope user
  python install.py --agent all --project . --dry-run
  python install.py --agent codex --project . --uninstall

Agents:   claude  codex  copilot  cursor  windsurf  cline  gemini  agents-md (generic AGENTS.md)
Scope:    project (default; files go into one repo) | user (claude, codex, gemini only)

What it does
  claude : copies the whole skill folder to .claude/skills/code-diff (self-contained).
  others : copies the engine (scripts, references, WORKFLOW.md) to <root>/.agent-skills/code-diff
           and writes a short instruction file that points the agent at it:
             codex/agents-md -> AGENTS.md        copilot  -> .github/copilot-instructions.md
             cursor          -> .cursor/rules/code-diff.mdc   windsurf -> .windsurf/rules/code-diff.md
             cline           -> .clinerules/code-diff.md      gemini   -> GEMINI.md
  Shared files (AGENTS.md, GEMINI.md, copilot-instructions.md) get a marked block, so existing
  content is preserved and re-running updates the block in place. --uninstall removes only what
  this script added. Stdlib only.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent
ENGINE_ITEMS = ["scripts", "references", "WORKFLOW.md"]
CLAUDE_ITEMS = ENGINE_ITEMS + ["SKILL.md"]
MARKER = ".code-diff-installed"
BEGIN, END = "<!-- code-diff:begin -->", "<!-- code-diff:end -->"

# agent -> (kind, project-relative path, user-scope absolute path or None)
HOME = Path.home()
AGENTS = {
    "claude":    ("skilldir", ".claude/skills/code-diff", HOME / ".claude/skills/code-diff"),
    "codex":     ("block",    "AGENTS.md",                 HOME / ".codex/AGENTS.md"),
    "agents-md": ("block",    "AGENTS.md",                 None),
    "copilot":   ("block",    ".github/copilot-instructions.md", None),
    "gemini":    ("block",    "GEMINI.md",                 HOME / ".gemini/GEMINI.md"),
    "cursor":    ("file",     ".cursor/rules/code-diff.mdc", None),
    "windsurf":  ("file",     ".windsurf/rules/code-diff.md", None),
    "cline":     ("file",     ".clinerules/code-diff.md",  None),
}

BODY = """## code-diff: requirement-traced review of AI-written changes

Use this workflow for any non-trivial coding task, and whenever the user asks to review a diff against a requirement or check for drift.

1. **Read `{workflow}` first and follow it.** The engine is `{cdiff}`; all of its commands are `{cdiff} <command>` (run `{cdiff} status` to see the current phase).
2. Before writing code: init, write `requirements.json` (with path `scope` per requirement), show it to the user, and **STOP until the user replies yes in chat**. Only then run `lock --confirmed`. Never confirm on the user's behalf.
3. After coding: `collect`, then get an independent review (a fresh subagent if your tool has one, otherwise `reviewer-pack` for the user to paste into a fresh chat or another model, then `import-contract`). Do not write or patch the contract yourself to make `validate` pass.
4. `validate`, `render`, show the user the decisions in plain words, record answers with `decide`, finish with `gate`. Only `gate` exit code 0 means the change was reviewed.
"""

CURSOR_FM = """---
description: Requirement-traced review of AI-written changes (code-diff). Use for non-trivial coding tasks or when asked to review a diff against requirements or check for drift.
alwaysApply: false
---

"""


def die(msg: str) -> None:
    print(f"install: {msg}", file=sys.stderr)
    sys.exit(2)


class Ctx:
    def __init__(self, dry: bool):
        self.dry = dry

    def say(self, msg: str) -> None:
        print(("[dry-run] " if self.dry else "") + msg)

    def copy_items(self, items: list[str], dest: Path) -> None:
        self.say(f"copy {', '.join(items)} -> {dest}")
        if self.dry:
            return
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        for it in items:
            s = SRC / it
            if s.is_dir():
                shutil.copytree(s, dest / it, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            else:
                shutil.copy2(s, dest / it)
        (dest / MARKER).write_text("installed by code-diff/install.py\n", encoding="utf-8")

    def remove_engine(self, dest: Path) -> None:
        if not dest.exists():
            return
        if not (dest / MARKER).exists():
            print(f"skip: {dest} was not installed by this script, leaving it alone")
            return
        self.say(f"remove {dest}")
        if not self.dry:
            shutil.rmtree(dest)

    def write_block(self, path: Path, body: str) -> None:
        block = f"{BEGIN}\n{body.rstrip()}\n{END}\n"
        old = path.read_text(encoding="utf-8") if path.exists() else ""
        if BEGIN in old and END in old:
            a, b = old.index(BEGIN), old.index(END) + len(END)
            new = old[:a] + block.rstrip("\n") + old[b:]
            self.say(f"update block in {path}")
        else:
            new = old + ("\n" if old and not old.endswith("\n\n") else "") + block
            self.say(f"{'append block to' if old else 'create'} {path}")
        if not self.dry:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(new, encoding="utf-8")

    def remove_block(self, path: Path) -> None:
        if not path.exists():
            return
        old = path.read_text(encoding="utf-8")
        if BEGIN not in old or END not in old:
            return
        a, b = old.index(BEGIN), old.index(END) + len(END)
        new = (old[:a].rstrip("\n") + "\n" + old[b:].lstrip("\n")).strip("\n")
        if new:
            self.say(f"remove block from {path}")
            if not self.dry:
                path.write_text(new + "\n", encoding="utf-8")
        else:
            self.say(f"remove {path} (only held our block)")
            if not self.dry:
                path.unlink()

    def write_file(self, path: Path, text: str) -> None:
        self.say(f"write {path}")
        if not self.dry:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    def remove_file(self, path: Path) -> None:
        if path.exists():
            self.say(f"remove {path}")
            if not self.dry:
                path.unlink()


def install_one(ctx: Ctx, agent: str, scope: str, root: Path, uninstall: bool) -> None:
    kind, rel, user_path = AGENTS[agent]
    if scope == "user" and user_path is None:
        die(f"{agent} has no user-level instruction file; use --scope project with --project <repo>")
    target = user_path if scope == "user" else root / rel
    engine = (HOME if scope == "user" else root) / ".agent-skills" / "code-diff"

    if kind == "skilldir":
        if uninstall:
            ctx.remove_engine(target)
        else:
            ctx.copy_items(CLAUDE_ITEMS, target)
        return

    cdiff = f'python "{engine / "scripts" / "cdiff.py"}"' if scope == "user" else \
        "python .agent-skills/code-diff/scripts/cdiff.py"
    workflow = str(engine / "WORKFLOW.md") if scope == "user" else ".agent-skills/code-diff/WORKFLOW.md"
    body = BODY.format(cdiff=cdiff, workflow=workflow)
    if uninstall:
        if kind == "block":
            ctx.remove_block(target)
        else:
            ctx.remove_file(target)
        return
    ctx.copy_items(ENGINE_ITEMS, engine)
    if kind == "block":
        ctx.write_block(target, body)
    else:
        ctx.write_file(target, (CURSOR_FM if agent == "cursor" else "") + body)


def maybe_remove_engine(ctx: Ctx, root: Path, scope: str, agents: list[str]) -> None:
    """After uninstall, drop the shared engine dir if no other agent still points at it."""
    base = HOME if scope == "user" else root
    engine = base / ".agent-skills" / "code-diff"
    still = False
    for name, (kind, rel, user_path) in AGENTS.items():
        if kind == "skilldir" or name in agents:
            continue
        p = user_path if scope == "user" else root / rel
        if p and p.exists() and (BEGIN in p.read_text(encoding="utf-8", errors="replace") or
                                 "code-diff" in p.name):
            still = True
    if not still:
        ctx.remove_engine(engine)
        parent = engine.parent
        if not ctx.dry and parent.exists() and not any(parent.iterdir()):
            parent.rmdir()


def interactive() -> tuple[list[str], str, Path | None]:
    names = list(AGENTS)
    print("Install code-diff for which agent(s)?")
    for i, n in enumerate(names, 1):
        print(f"  {i}. {n}")
    print(f"  {len(names) + 1}. all")
    raw = input("Numbers or names, comma separated: ").strip()
    picks: list[str] = []
    for tok in [t.strip() for t in raw.split(",") if t.strip()]:
        if tok.isdigit() and 1 <= int(tok) <= len(names):
            picks.append(names[int(tok) - 1])
        elif tok.isdigit() and int(tok) == len(names) + 1 or tok == "all":
            picks = names
            break
        elif tok in AGENTS:
            picks.append(tok)
        else:
            die(f"unknown choice {tok!r}")
    scope = input("Scope: [p]roject (default) or [u]ser? ").strip().lower()
    scope = "user" if scope.startswith("u") else "project"
    root = None
    if scope == "project":
        root = Path(input("Project folder [current directory]: ").strip() or ".")
    return sorted(set(picks), key=names.index), scope, root


def main() -> int:
    ap = argparse.ArgumentParser(description="Install the code-diff skill for chosen agents.",
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--agent", action="append", choices=list(AGENTS) + ["all"],
                    help="repeatable; omit for interactive mode")
    ap.add_argument("--scope", choices=["project", "user"], default=None)
    ap.add_argument("--project", help="target repo folder (project scope); default: current directory")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if a.agent:
        agents = list(AGENTS) if "all" in a.agent else sorted(set(a.agent), key=list(AGENTS).index)
        scope = a.scope or "project"
        root = Path(a.project) if a.project else None
    else:
        agents, scope, root = interactive()
        if a.scope:
            scope = a.scope
    if scope == "project":
        root = (root or Path.cwd()).resolve()
        if not root.is_dir():
            die(f"project folder {root} does not exist")
    else:
        root = HOME
        bad = [x for x in agents if AGENTS[x][2] is None]
        if bad and a.agent and "all" in a.agent:
            agents = [x for x in agents if AGENTS[x][2] is not None]
        elif bad:
            die(f"no user-level support for: {', '.join(bad)} (use --scope project)")

    ctx = Ctx(a.dry_run)
    for ag in agents:
        install_one(ctx, ag, scope, root, a.uninstall)
    if a.uninstall:
        maybe_remove_engine(ctx, root, scope, agents)
    if not a.uninstall:
        print("\nDone. Verify with: python " + ("<engine>/scripts/cdiff.py --help"))
        print("Next: in your agent, ask for a coding task and mention 'use the code-diff workflow'"
              + (" (Claude loads it automatically)." if "claude" in agents else "."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
