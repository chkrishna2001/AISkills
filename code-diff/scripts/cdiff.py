#!/usr/bin/env python3
"""cdiff: deterministic backbone of the code-diff skill (Change Contract).

Everything that can be computed is computed here, not by the LLM:
  init      snapshot the base commit, scaffold requirements.json
  lock      freeze the human-confirmed requirements (sha256)
  collect   git diff -> hunks + deterministic signals (diff.json, diff.patch)
  validate  check contract.json against lock + diff.json (exit 1 on any error)
  reviewer-pack     single paste-able file for an independent review in any other tool
  import-contract   save the reviewer's reply as contract.json
  render    report.md with a computed risk level and ranked decisions
  decide    record a human decision (keep|revert|discuss)
  gate      exit 0 only if validate passes and every decision is `keep`
  status    show which phase the task is in

The LLM only writes the judgment parts (requirements text, plain-English
explanations, decisions) and cdiff refuses to proceed if they are incomplete.
Stdlib only. Python 3.9+.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import posixpath
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SEV_WEIGHT = {"high": 5, "medium": 2, "low": 0.5}
SEV_RANK = {"high": 3, "medium": 2, "low": 1}
MAX_DECISIONS_SHOWN = 5

# ---------------------------------------------------------------- helpers


def die(msg: str, code: int = 2) -> None:
    print(f"cdiff: {msg}", file=sys.stderr)
    sys.exit(code)


def git(*args: str, cwd: Path | None = None, check: bool = True) -> str:
    p = subprocess.run(
        ["git", "-c", "core.quotepath=off", *args],
        cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if check and p.returncode != 0:
        die(f"git {' '.join(args)} failed: {p.stderr.strip()}")
    return p.stdout


def repo_root() -> Path:
    out = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                         text=True, encoding="utf-8")
    if out.returncode != 0:
        die("not inside a git repository (code-diff needs git to anchor the base)")
    return Path(out.stdout.strip())


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load(path: Path):
    if not path.exists():
        die(f"missing {path.name} - run the earlier phase first (see `cdiff status`)")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        die(f"{path.name} is not valid JSON: {e}")


def save(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def glob_re(pat: str) -> re.Pattern:
    """`**` crosses directories, `*` and `?` do not. `dir/**` matches dir/anything."""
    out, i = "", 0
    while i < len(pat):
        if pat.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pat.startswith("**", i):
            out += ".*"
            i += 2
        elif pat[i] == "*":
            out += "[^/]*"
            i += 1
        elif pat[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(pat[i])
            i += 1
    return re.compile("^" + out + "$")


def matches(path: str, pats: list[str]) -> bool:
    return any(glob_re(p).match(path) for p in pats)


class Ctx:
    def __init__(self, task: str | None):
        self.root = repo_root()
        self.base_dir = self.root / ".codediff"
        cur = self.base_dir / "CURRENT"
        if task is None and cur.exists():
            task = cur.read_text(encoding="utf-8").strip()
        self.task = task
        self.dir = self.base_dir / task if task else None

    def need_task(self) -> Path:
        if not self.dir:
            die("no task selected: pass --task or run `cdiff init --task <slug>` first")
        if not self.dir.exists():
            die(f"task dir {self.dir} does not exist")
        return self.dir

    def p(self, name: str) -> Path:
        return self.need_task() / name


# ---------------------------------------------------------------- init / lock

REQ_TEMPLATE = {
    "task": "",
    "original_prompt": "FILL: the user's request, verbatim",
    "requirements": [
        {
            "id": "R1",
            "text": "FILL: one testable statement of what must be true after the change",
            "acceptance": "FILL: how a human would check it",
            "scope": ["FILL/glob/**"],
            "expects": [],
        }
    ],
    "non_goals": ["FILL: things explicitly NOT to change"],
    "allowed_extra": [],
}


def cmd_init(a) -> None:
    ctx = Ctx(a.task)
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,48}", a.task or ""):
        die("--task must be a short kebab-case slug")
    ctx.dir = ctx.base_dir / a.task
    if ctx.dir.exists() and not a.force:
        die(f"{ctx.dir} already exists (use --force to reinit, which discards the lock)")
    ctx.dir.mkdir(parents=True, exist_ok=True)

    dirty = git("status", "--porcelain", "--untracked-files=no", cwd=ctx.root).strip()
    if dirty and not a.allow_dirty:
        die("working tree has uncommitted changes; commit/stash them or pass --allow-dirty "
            "(they will be snapshotted into the base so they are not attributed to the agent)")
    base = git("stash", "create", cwd=ctx.root).strip() if dirty else ""
    base = base or git("rev-parse", "HEAD", cwd=ctx.root).strip()
    untracked = [l for l in git("ls-files", "--others", "--exclude-standard",
                                cwd=ctx.root).splitlines() if not l.startswith(".codediff/")]
    save(ctx.dir / "base.json", {"base": base, "created": now(), "preexisting_untracked": untracked})
    req = dict(REQ_TEMPLATE, task=a.task)
    save(ctx.dir / "requirements.json", req)
    (ctx.base_dir / "CURRENT").write_text(a.task + "\n", encoding="utf-8")
    gi = ctx.base_dir / ".gitignore"
    if not gi.exists():
        gi.write_text("*\n!.gitignore\n!rules.json\n", encoding="utf-8")
    print(f"initialised {ctx.dir}\nbase = {base[:12]}\nnext: fill requirements.json, show it to the "
          f"human, then `cdiff lock --confirmed`")


def req_hash(req: dict) -> str:
    core = {k: req.get(k) for k in ("requirements", "non_goals", "allowed_extra")}
    return sha(canon(core))


def cmd_lock(a) -> None:
    ctx = Ctx(a.task)
    if not a.confirmed:
        die("refusing to lock: requirements must be shown to and confirmed by the human first "
            "(then re-run with --confirmed)")
    req = load(ctx.p("requirements.json"))
    errs = check_requirements(req)
    if errs:
        die("requirements.json invalid:\n  - " + "\n  - ".join(errs))
    base = load(ctx.p("base.json"))
    save(ctx.p("lock.json"), {"sha256": req_hash(req), "base": base["base"], "locked_at": now(),
                              "requirement_ids": [r["id"] for r in req["requirements"]]})
    print(f"locked {len(req['requirements'])} requirement(s), sha256={req_hash(req)[:12]}")


def check_requirements(req: dict) -> list[str]:
    errs, seen = [], set()
    rs = req.get("requirements")
    if not isinstance(rs, list) or not rs:
        return ["requirements must be a non-empty list"]
    for r in rs:
        rid = r.get("id", "?")
        if not re.fullmatch(r"R\d+", str(rid)):
            errs.append(f"{rid}: id must look like R1, R2...")
        if rid in seen:
            errs.append(f"{rid}: duplicate id")
        seen.add(rid)
        for k in ("text", "acceptance"):
            if not str(r.get(k, "")).strip() or "FILL" in str(r.get(k, "")):
                errs.append(f"{rid}: `{k}` is empty or still a placeholder")
        sc = r.get("scope")
        if not isinstance(sc, list) or not sc or any("FILL" in s for s in sc):
            errs.append(f"{rid}: `scope` needs at least one real path glob (drift detection relies on it)")
    if any("FILL" in str(x) for x in req.get("non_goals", [])):
        errs.append("non_goals still contains a placeholder (use [] if truly none)")
    if "FILL" in str(req.get("original_prompt", "")):
        errs.append("original_prompt is still a placeholder")
    return errs


# ---------------------------------------------------------------- collect

DEP_MANIFESTS = ("package.json", "requirements*.txt", "pyproject.toml", "setup.py", "setup.cfg",
                 "Pipfile", "go.mod", "Cargo.toml", "Gemfile", "pom.xml", "build.gradle",
                 "build.gradle.kts", "*.csproj", "Directory.Packages.props", "composer.json")
LOCKFILES = ("package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "uv.lock",
             "Cargo.lock", "Gemfile.lock", "go.sum", "composer.lock", "Pipfile.lock")
TEST_PAT = re.compile(r"(^|/)(tests?|__tests__|spec)(/|$)|(^|/)test_[^/]*$|[._-](test|spec)\.[a-z]+$|Tests?\.cs$",
                      re.I)
SRC_EXT = {".py", ".js", ".jsx", ".ts", ".tsx", ".cs", ".go", ".java", ".rb", ".rs", ".kt", ".php"}

PUBLIC_API = {
    ".py": re.compile(r"^(?:async\s+)?def\s+([A-Za-z]\w*)|^class\s+([A-Za-z]\w*)"),
    ".js": re.compile(r"^export\s+"), ".jsx": re.compile(r"^export\s+"),
    ".ts": re.compile(r"^export\s+"), ".tsx": re.compile(r"^export\s+"),
    ".cs": re.compile(r"^\s*public\s+"), ".java": re.compile(r"^\s*public\s+"),
    ".go": re.compile(r"^func\s+(?:\([^)]*\)\s*)?[A-Z]"),
}
PY_IMPORT = re.compile(r"^\s*(?:from\s+(\.*[\w.]*)\s+import\s|import\s+([\w.]+))")
JS_IMPORT = re.compile(r"""(?:from\s+|import\s+|require\(\s*)['"]([^'"]+)['"]""")


def classify(path: str) -> str:
    name = posixpath.basename(path)
    if matches(path, [f"**/{p}" for p in DEP_MANIFESTS]) or name in DEP_MANIFESTS:
        return "dependency"
    if name in LOCKFILES:
        return "lockfile"
    if re.search(r"(^|/)migrations?/", path) or path.endswith(".sql"):
        return "migration"
    if (path.startswith(".github/") or name.startswith(("Dockerfile", "docker-compose"))
            or name in ("azure-pipelines.yml", ".gitlab-ci.yml", "Jenkinsfile")):
        return "ci"
    if TEST_PAT.search(path):
        return "test"
    if path.endswith((".md", ".rst", ".txt")):
        return "docs"
    if name.startswith(".env") or re.search(r"(config|settings)", name, re.I) or \
            path.endswith((".yaml", ".yml", ".toml", ".ini", ".json")):
        return "config"
    return "source" if posixpath.splitext(path)[1] in SRC_EXT else "other"


def parse_patch(text: str) -> list[dict]:
    files, cur, hunk = [], None, None
    for line in text.splitlines():
        if line.startswith("diff --git "):
            m = re.match(r"diff --git a/(.*) b/(.*)$", line)
            cur = {"path": m.group(2), "old_path": m.group(1), "status": "modified", "hunks": []}
            files.append(cur)
            hunk = None
        elif cur is None:
            continue
        elif line.startswith("new file mode"):
            cur["status"] = "added"
        elif line.startswith("deleted file mode"):
            cur["status"] = "deleted"
        elif line.startswith("rename from"):
            cur["status"] = "renamed"
        elif line.startswith("Binary files"):
            cur["hunks"].append({"old": [0, 0], "new": [0, 0], "added": [], "removed": [], "binary": True})
        elif line.startswith("@@"):
            m = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
            hunk = {"old": [int(m.group(1)), int(m.group(2) or 1)],
                    "new": [int(m.group(3)), int(m.group(4) or 1)], "added": [], "removed": []}
            cur["hunks"].append(hunk)
        elif hunk is not None:
            if line.startswith("+") and not line.startswith("+++"):
                hunk["added"].append(line[1:])
            elif line.startswith("-") and not line.startswith("---"):
                hunk["removed"].append(line[1:])
    return files


def module_of(path: str, dirs: set[str]) -> str:
    parts = path.split("/")
    if path in dirs:
        parts = parts + ["_"]
    if len(parts) == 1:
        return "."
    if parts[0] in ("src", "lib", "app") and len(parts) > 2:
        return f"{parts[0]}/{parts[1]}"
    return parts[0]


def resolve_import(file: str, ext: str, line: str, files: set[str], dirs: set[str]) -> str | None:
    """Return the target path (repo-relative, no ext) if the import is internal, else None."""
    if ext == ".py":
        m = PY_IMPORT.match(line)
        if not m:
            return None
        spec = m.group(1) if m.group(1) is not None else m.group(2)
        if spec.startswith("."):
            dots = len(spec) - len(spec.lstrip("."))
            base = posixpath.dirname(file)
            for _ in range(dots - 1):
                base = posixpath.dirname(base)
            return posixpath.normpath(posixpath.join(base, spec.lstrip(".").replace(".", "/")))
        segs = spec.split(".")
        for prefix in ("", "src/"):
            parts = list(segs)
            while parts:
                cand = prefix + "/".join(parts)
                if cand in dirs or cand + ".py" in files:
                    return cand
                parts.pop()
        return None
    if ext in (".js", ".jsx", ".ts", ".tsx"):
        m = JS_IMPORT.search(line)
        if m and m.group(1).startswith("."):
            return posixpath.normpath(posixpath.join(posixpath.dirname(file), m.group(1)))
    return None


def load_rules(ctx: Ctx) -> dict:
    p = ctx.base_dir / "rules.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def cmd_collect(a) -> None:
    ctx = Ctx(a.task)
    lock = load(ctx.p("lock.json"))
    base_info = load(ctx.p("base.json"))
    req = load(ctx.p("requirements.json"))
    if req_hash(req) != lock["sha256"]:
        die("requirements.json changed after lock - re-lock with human confirmation (`cdiff lock --confirmed`)")
    rules = load_rules(ctx)
    base = lock["base"]

    patch = git("diff", "--no-color", "-U0", "-M", base, "--", ".", ":(exclude).codediff", cwd=ctx.root)
    tracked_sha = sha(patch)
    files = parse_patch(patch)
    skip = set(base_info.get("preexisting_untracked", []))
    for path in git("ls-files", "--others", "--exclude-standard", cwd=ctx.root).splitlines():
        if path.startswith(".codediff/") or path in skip:
            continue
        fp = ctx.root / path
        try:
            data = fp.read_bytes()
        except OSError:
            continue
        if b"\0" in data[:8192] or len(data) > 2_000_000:
            hunks = [{"old": [0, 0], "new": [0, 0], "added": [], "removed": [], "binary": True}]
        else:
            lines = data.decode("utf-8", "replace").splitlines()
            hunks = [{"old": [0, 0], "new": [1, len(lines)], "added": lines, "removed": []}] if lines else []
            patch += f"diff --git a/{path} b/{path}\nnew file mode 100644\n--- /dev/null\n+++ b/{path}\n" \
                     f"@@ -0,0 +1,{len(lines)} @@\n" + "".join("+" + l + "\n" for l in lines)
        files.append({"path": path, "old_path": path, "status": "added", "hunks": hunks})

    tracked = set(git("ls-files", cwd=ctx.root).splitlines()) | {f["path"] for f in files}
    dirs = {"/".join(p.split("/")[:i]) for p in tracked for i in range(1, len(p.split("/")))}
    base_dirs = set()
    for p in git("ls-tree", "-r", "--name-only", base, cwd=ctx.root).splitlines():
        parts = p.split("/")
        base_dirs |= {"/".join(parts[:i]) for i in range(1, len(parts))}

    reqs = req["requirements"]
    extra = req.get("allowed_extra", [])
    any_scope = any(r.get("scope") for r in reqs)
    signals: list[dict] = []
    hid = 0

    def sig(kind, sev, file, hunks, detail):
        signals.append({"id": f"S{len(signals) + 1}", "kind": kind, "severity": sev, "file": file,
                        "hunks": hunks, "detail": detail})

    out_files, saw_source, saw_test = [], False, False
    for f in files:
        path, cat = f["path"], classify(f["path"])
        ext = posixpath.splitext(path)[1]
        hs = []
        for h in f["hunks"]:
            hid += 1
            hs.append(dict(h, id=f"H{hid}"))
        hids = [h["id"] for h in hs]
        added = sum(len(h["added"]) for h in hs)
        removed = sum(len(h["removed"]) for h in hs)
        scope_req = [r["id"] for r in reqs if matches(path, r.get("scope", []))]
        saw_source |= cat == "source"
        saw_test |= cat == "test"

        if any_scope and not scope_req and not matches(path, extra):
            sig("out_of_scope", "high", path, hids, "file is outside every requirement's scope")
        if f["status"] == "deleted":
            sig("file_deleted", "medium", path, hids, f"{removed} lines removed")
        if f["status"] == "renamed":
            sig("file_renamed", "low", path, hids, f"renamed from {f['old_path']}")
        if f["status"] == "added":
            parent = posixpath.dirname(path)
            if parent and parent not in base_dirs:
                sig("new_directory", "medium", path, hids, f"creates new directory {parent}/")
        if cat in ("dependency", "migration", "ci"):
            sig({"dependency": "dependency_manifest", "migration": "migration", "ci": "ci_config"}[cat],
                "high" if cat != "ci" else "medium", path, hids, f"{cat} file changed (+{added}/-{removed})")
        elif cat == "config":
            sig("config", "medium", path, hids, f"config file changed (+{added}/-{removed})")
        elif cat == "lockfile":
            sig("lockfile", "low", path, hids, "lockfile changed")
        for pat in rules.get("protected_paths", []):
            if glob_re(pat).match(path):
                sig("protected_path", "high", path, hids, f"matches protected path `{pat}`")
                break

        api = PUBLIC_API.get(ext)
        if api and cat in ("source",):
            for h in hs:
                for sign, lines in (("+", h["added"]), ("-", h["removed"])):
                    for l in lines:
                        m = api.match(l)
                        if m and not (ext == ".py" and l.lstrip() != l):
                            name = next((g for g in (m.groups() if m.groups() else ()) if g), None)
                            if name and name.startswith("_"):
                                continue
                            sig("public_api", "medium", path, [h["id"]],
                                f"{'added' if sign == '+' else 'removed'}: {l.strip()[:90]}")
        if cat in ("source", "test"):
            src_mod = module_of(path, dirs)
            seen_edges = set()
            for h in hs:
                for l in h["added"]:
                    tgt = resolve_import(path, ext, l, tracked, dirs)
                    if tgt is not None:
                        tm = module_of(tgt, dirs)
                        if src_mod != "." and tm != src_mod and (src_mod, tm) not in seen_edges:
                            seen_edges.add((src_mod, tm))
                            sig("cross_module_import", "medium", path, [h["id"]],
                                f"new dependency {src_mod} -> {tm}: {l.strip()[:80]}")
                    for rule in rules.get("forbidden_imports", []):
                        if matches(path, [rule["in"]]) and re.search(rule["import_regex"], l):
                            sig("forbidden_import", "high", path, [h["id"]],
                                f"violates rule: {rule.get('why', rule['import_regex'])}")
        out_files.append({"path": path, "old_path": f["old_path"], "status": f["status"], "category": cat,
                          "added": added, "removed": removed, "scope_requirements": scope_req,
                          "hunks": [{"id": h["id"], "old": h["old"], "new": h["new"],
                                     "added": len(h["added"]), "removed": len(h["removed"]),
                                     "binary": bool(h.get("binary"))} for h in hs]})

    if saw_source and not saw_test:
        sig("no_tests_changed", "low", "", [], "source files changed but no test files were touched")

    all_hunks = [h["id"] for f in out_files for h in f["hunks"]]
    (ctx.p("diff.patch")).write_text(patch, encoding="utf-8")
    save(ctx.p("diff.json"), {
        "task": ctx.task, "base": base, "collected": now(), "requirements_sha256": lock["sha256"],
        "patch_sha256": tracked_sha, "files": out_files, "hunk_ids": all_hunks, "signals": signals,
        "totals": {"files": len(out_files), "hunks": len(all_hunks),
                   "added": sum(f["added"] for f in out_files), "removed": sum(f["removed"] for f in out_files)}})
    by = {}
    for s in signals:
        by[s["severity"]] = by.get(s["severity"], 0) + 1
    print(f"collected {len(out_files)} file(s), {len(all_hunks)} hunk(s), signals: {by or 'none'}")
    print("next: spawn the independent reviewer to write contract.json, then `cdiff validate`")


# ---------------------------------------------------------------- validate

CONTRACT_SCHEMA = """contract.json shape:
{
  "task": "<slug>",
  "requirements_sha256": "<copy from diff.json>",
  "summary_plain": "2-3 sentences, no code identifiers",
  "mappings": [{"requirement": "R1", "hunks": ["H1","H2"], "files": ["optional/path"], "plain": "what this part does, in plain English"}],
  "unimplemented": [{"requirement": "R3", "reason": "..."}],
  "decisions": [{"id": "D1", "title": "short", "plain": "what the agent did that was not asked for, plain English",
                 "why_it_matters": "consequence for the human", "hunks": ["H9"], "signals": ["S4"],
                 "impact": "low|medium|high", "recommend": "keep|revert|discuss"}],
  "assumptions": [{"text": "...", "requirement": "R1"}]
}"""


def expand_mapping(m: dict, diff: dict) -> set[str]:
    hs = set(m.get("hunks", []))
    for path in m.get("files", []):
        hs |= {h["id"] for f in diff["files"] if f["path"] == path for h in f["hunks"]}
    return hs


def validate_contract(ctx: Ctx) -> tuple[list[str], list[str], dict, dict, dict]:
    lock = load(ctx.p("lock.json"))
    req = load(ctx.p("requirements.json"))
    diff = load(ctx.p("diff.json"))
    con = load(ctx.p("contract.json"))
    errs, warns = [], []
    if req_hash(req) != lock["sha256"]:
        errs.append("requirements.json differs from the locked version")
    if diff["requirements_sha256"] != lock["sha256"]:
        errs.append("diff.json was collected against different requirements - re-run `collect`")
    live = git("diff", "--no-color", "-U0", "-M", lock["base"], "--", ".", ":(exclude).codediff", cwd=ctx.root)
    if sha(live) != diff["patch_sha256"]:
        errs.append("working tree changed since `collect` - re-run `collect` and the review")
    if con.get("requirements_sha256") != lock["sha256"]:
        errs.append("contract.requirements_sha256 does not match the lock (reviewer used stale/other requirements)")

    rids = {r["id"] for r in req["requirements"]}
    hids = set(diff["hunk_ids"])
    sigs = {s["id"]: s for s in diff["signals"]}
    covered, mapped_by_hunk = set(), {}
    for i, m in enumerate(con.get("mappings", [])):
        if m.get("requirement") not in rids:
            errs.append(f"mappings[{i}]: unknown requirement {m.get('requirement')!r}")
        if not str(m.get("plain", "")).strip():
            errs.append(f"mappings[{i}] ({m.get('requirement')}): `plain` is empty")
        hs = expand_mapping(m, diff)
        if not hs:
            errs.append(f"mappings[{i}] ({m.get('requirement')}): maps no hunks")
        for h in hs:
            if h not in hids:
                errs.append(f"mappings[{i}]: unknown hunk {h}")
            mapped_by_hunk.setdefault(h, set()).add(m.get("requirement"))
        covered |= hs

    decisions = con.get("decisions", [])
    dec_ids, dec_hunks, dec_sigs = set(), set(), set()
    for d in decisions:
        did = d.get("id", "?")
        if did in dec_ids:
            errs.append(f"decision {did}: duplicate id")
        dec_ids.add(did)
        for k in ("title", "plain", "why_it_matters"):
            if not str(d.get(k, "")).strip():
                errs.append(f"decision {did}: `{k}` is empty")
        if d.get("impact") not in ("low", "medium", "high"):
            errs.append(f"decision {did}: impact must be low|medium|high")
        if d.get("recommend") not in ("keep", "revert", "discuss"):
            errs.append(f"decision {did}: recommend must be keep|revert|discuss")
        for h in d.get("hunks", []):
            if h not in hids:
                errs.append(f"decision {did}: unknown hunk {h}")
        for s in d.get("signals", []):
            if s not in sigs:
                errs.append(f"decision {did}: unknown signal {s}")
        dec_hunks |= set(d.get("hunks", []))
        dec_sigs |= set(d.get("signals", []))
    covered |= dec_hunks

    for h in sorted(hids - covered, key=lambda x: int(x[1:])):
        errs.append(f"hunk {h} is not explained: map it to a requirement or raise a decision (no silent changes)")

    done = {m.get("requirement") for m in con.get("mappings", [])} | \
           {u.get("requirement") for u in con.get("unimplemented", [])}
    for r in sorted(rids - done):
        errs.append(f"requirement {r} has no mapping and is not declared unimplemented")
    for u in con.get("unimplemented", []):
        if not str(u.get("reason", "")).strip():
            errs.append(f"unimplemented {u.get('requirement')}: reason is empty")

    expects = {r["id"]: set(r.get("expects", [])) for r in req["requirements"]}
    for s in diff["signals"]:
        if SEV_RANK[s["severity"]] < 2 or s["id"] in dec_sigs:
            continue
        if s["kind"] != "out_of_scope" and s["hunks"] and all(
                s["kind"] in set().union(*(expects.get(r, set()) for r in mapped_by_hunk.get(h, set())))
                for h in s["hunks"]):
            continue  # signal explicitly expected by the requirement that owns these hunks
        errs.append(f"signal {s['id']} [{s['severity']} {s['kind']}] {s['file']} - {s['detail']}: "
                    f"must be raised as a decision or declared in the owning requirement's `expects`")

    if len(decisions) > 7:
        warns.append(f"{len(decisions)} decisions: reviewer will stop reading - merge related ones")
    if not con.get("assumptions"):
        warns.append("no assumptions listed (rarely true - did the reviewer look for them?)")
    return errs, warns, req, diff, con


def cmd_validate(a) -> int:
    ctx = Ctx(a.task)
    errs, warns, *_ = validate_contract(ctx)
    for w in warns:
        print(f"warn: {w}")
    if errs:
        print(f"INVALID ({len(errs)} problem(s)):")
        for e in errs:
            print(f"  x {e}")
        print("\n" + CONTRACT_SCHEMA)
        return 1
    print("contract valid: every hunk explained, every requirement accounted for, every risky signal surfaced")
    return 0


# ---------------------------------------------------------------- decide / gate


def fingerprint(d: dict, sigs: dict) -> str:
    parts = sorted(f"{sigs[s]['kind']}:{sigs[s]['file']}" for s in d.get("signals", []) if s in sigs)
    return sha(canon([d["title"], parts]))[:16]


def cmd_decide(a) -> None:
    ctx = Ctx(a.task)
    errs, _, req, diff, con = validate_contract(ctx)
    d = next((x for x in con["decisions"] if x["id"] == a.id), None)
    if not d:
        die(f"no decision {a.id} in contract.json")
    path = ctx.p("decisions.json")
    cur = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    sigs = {s["id"]: s for s in diff["signals"]}
    cur[a.id] = {"choice": a.choice, "note": a.note or "", "at": now(), "fingerprint": fingerprint(d, sigs)}
    save(path, cur)
    print(f"{a.id}: {a.choice}")


def cmd_gate(a) -> int:
    ctx = Ctx(a.task)
    errs, _, req, diff, con = validate_contract(ctx)
    if errs:
        print("GATE FAIL: contract invalid (run `cdiff validate`)")
        return 1
    path = ctx.p("decisions.json")
    rec = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    sigs = {s["id"]: s for s in diff["signals"]}
    bad = []
    for d in con["decisions"]:
        r = rec.get(d["id"])
        if not r or r["fingerprint"] != fingerprint(d, sigs):
            bad.append(f"{d['id']} {d['title']}: undecided")
        elif r["choice"] == "revert":
            files = sorted({f["path"] for f in diff["files"] for h in f["hunks"] if h["id"] in d.get("hunks", [])})
            bad.append(f"{d['id']} {d['title']}: REVERT requested - undo in {', '.join(files)}, "
                       f"then re-run collect + review")
        elif r["choice"] == "discuss":
            bad.append(f"{d['id']} {d['title']}: marked discuss - unresolved")
    if bad:
        print("GATE FAIL:")
        for b in bad:
            print(f"  x {b}")
        return 1
    print("GATE PASS: contract valid and all decisions kept")
    return 0


# ---------------------------------------------------------------- render


def risk_level(diff: dict, con: dict) -> tuple[str, float]:
    score = sum(SEV_WEIGHT[s["severity"]] for s in diff["signals"])
    if any(s["severity"] == "high" for s in diff["signals"]) or score >= 12:
        return "HIGH", score
    if any(s["severity"] == "medium" for s in diff["signals"]) or score >= 4:
        return "MEDIUM", score
    return "LOW", score


def cmd_render(a) -> int:
    ctx = Ctx(a.task)
    errs, warns, req, diff, con = validate_contract(ctx)
    if errs and not a.force:
        print("refusing to render an invalid contract (run `cdiff validate`, or --force for a draft)")
        return 1
    sigs = {s["id"]: s for s in diff["signals"]}
    lock = load(ctx.p("lock.json"))
    dpath = ctx.p("decisions.json")
    rec = json.loads(dpath.read_text(encoding="utf-8")) if dpath.exists() else {}
    level, score = risk_level(diff, con)
    t = diff["totals"]

    def dec_rank(d):
        top = max([SEV_RANK[sigs[s]["severity"]] for s in d.get("signals", []) if s in sigs] or [0])
        return (-top, -{"low": 1, "medium": 2, "high": 3}.get(d.get("impact"), 0), d["id"])

    decs = sorted(con.get("decisions", []), key=dec_rank)
    L = []
    L.append(f"# Change Contract: {req['task']}")
    L.append(f"\n**Risk: {level}** (score {score:g}) | {t['files']} files, {t['hunks']} hunks, "
             f"+{t['added']}/-{t['removed']} | base `{lock['base'][:10]}` | requirements locked `{lock['sha256'][:10]}`")
    if errs:
        L.append("\n> DRAFT - contract has validation errors; do not trust this report.")
    L.append(f"\n{con.get('summary_plain', '')}")
    L.append(f"\n_Reviewer: {con.get('reviewed_by', 'subagent/unspecified')}_")
    L.append(f"\n## 1. Decisions for you ({len(decs)})\n")
    if not decs:
        L.append("None. Every change maps to a requirement and no risky signal fired.")
    for d in decs[:MAX_DECISIONS_SHOWN]:
        r = rec.get(d["id"])
        st = f"**{r['choice'].upper()}**" if r and r["fingerprint"] == fingerprint(d, sigs) else "pending"
        L.append(f"### {d['id']} - {d['title']} [{d['impact']} impact | agent suggests: {d['recommend']} | {st}]")
        L.append(f"- **What happened:** {d['plain']}")
        L.append(f"- **Why it matters:** {d['why_it_matters']}")
        for s in d.get("signals", []):
            if s in sigs:
                L.append(f"- *Detected ({sigs[s]['severity']}):* {sigs[s]['kind']} in `{sigs[s]['file']}` - {sigs[s]['detail']}")
        L.append(f"- Hunks: {', '.join(d.get('hunks', [])) or '-'}\n")
    if len(decs) > MAX_DECISIONS_SHOWN:
        L.append(f"_{len(decs) - MAX_DECISIONS_SHOWN} lower-ranked decision(s) in the appendix._")

    L.append("\n## 2. Requirement coverage\n")
    L.append("| Req | Requirement | Status | Files | What was done |")
    L.append("|---|---|---|---|---|")
    unimpl = {u["requirement"]: u["reason"] for u in con.get("unimplemented", [])}
    for r in req["requirements"]:
        ms = [m for m in con.get("mappings", []) if m["requirement"] == r["id"]]
        hs = set().union(*(expand_mapping(m, diff) for m in ms)) if ms else set()
        files = sorted({f["path"] for f in diff["files"] if any(h["id"] in hs for h in f["hunks"])})
        status = "implemented" if ms and r["id"] not in unimpl else ("NOT DONE" if r["id"] in unimpl else "?")
        plain = " ".join(m["plain"] for m in ms) or unimpl.get(r["id"], "")
        L.append(f"| {r['id']} | {r['text']} | {status} | {', '.join(f'`{f}`' for f in files) or '-'} | {plain} |")

    L.append("\n## 3. Architecture / risk signals (deterministic)\n")
    if diff["signals"]:
        L.append("| ID | Severity | Kind | File | Detail |")
        L.append("|---|---|---|---|---|")
        for s in sorted(diff["signals"], key=lambda s: (-SEV_RANK[s["severity"]], int(s["id"][1:]))):
            L.append(f"| {s['id']} | {s['severity']} | {s['kind']} | `{s['file']}` | {s['detail']} |")
    else:
        L.append("No signals.")

    L.append("\n## 4. Assumptions the agent made\n")
    for x in con.get("assumptions", []) or []:
        L.append(f"- {x['text']}" + (f" ({x['requirement']})" if x.get("requirement") else ""))
    if not con.get("assumptions"):
        L.append("- none recorded")

    L.append("\n## 5. Non-goals (from locked requirements)\n")
    L += [f"- {n}" for n in req.get("non_goals", [])] or ["- none"]

    if len(decs) > MAX_DECISIONS_SHOWN:
        L.append("\n## Appendix: remaining decisions\n")
        for d in decs[MAX_DECISIONS_SHOWN:]:
            L.append(f"- **{d['id']} {d['title']}** [{d['impact']}] - {d['plain']} _Why:_ {d['why_it_matters']}")

    L.append("\n## Appendix: files\n")
    L.append("| File | Kind | Status | +/- | Reqs in scope |")
    L.append("|---|---|---|---|---|")
    for f in diff["files"]:
        L.append(f"| `{f['path']}` | {f['category']} | {f['status']} | +{f['added']}/-{f['removed']} | "
                 f"{', '.join(f['scope_requirements']) or '**none**'} |")
    out = ctx.p("report.md")
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"wrote {out}  (risk {level}, {len(decs)} decision(s))")
    return 0


# ---------------------------------------------------------------- reviewer pack (tool-neutral review)

PACK_PREAMBLE = """# Independent review task (paste this whole file into a FRESH chat/session)

Use a different session from the one that wrote the code, ideally a different model.
The inputs below REPLACE the file paths mentioned in the prompt. You cannot browse the repo:
rely on the patch, and record any context you wished you had as an assumption.
OVERRIDE of rule 8: do not write files. Reply with ONLY the complete contract.json content in a
single ```json block and nothing else.

---

"""


def cmd_reviewer_pack(a) -> int:
    ctx = Ctx(a.task)
    req = load(ctx.p("requirements.json"))
    diff = load(ctx.p("diff.json"))
    prompt_file = Path(__file__).resolve().parent.parent / "references" / "reviewer-prompt.md"
    if not prompt_file.exists():
        die(f"missing {prompt_file}")
    prompt = prompt_file.read_text(encoding="utf-8").split("\n", 2)[2]  # drop title + blank line
    prompt = prompt.replace("{DIR}/", "the ").replace("{REPO}", "(not available)")
    patch = ctx.p("diff.patch").read_text(encoding="utf-8")
    fence = "`" * 3
    parts = [PACK_PREAMBLE, prompt,
             f"\n\n---\n\n## Input 1: requirements.json (locked)\n\n{fence}json\n",
             json.dumps(req, indent=2, ensure_ascii=False),
             f"\n{fence}\n\n## Input 2: diff.json\n\n{fence}json\n",
             json.dumps(diff, indent=1, ensure_ascii=False),
             f"\n{fence}\n\n## Input 3: diff.patch\n\n{fence}diff\n", patch, f"\n{fence}\n"]
    out = ctx.p("reviewer-pack.md")
    text = "".join(parts)
    out.write_text(text, encoding="utf-8")
    kb = len(text.encode("utf-8")) // 1024
    print(f"wrote {out} ({kb} KB)")
    if kb > 300:
        print("warn: large pack; some chat UIs will truncate it. Consider splitting the change into smaller tasks.")
    print("paste it into a fresh session, save the reply to a file, then: cdiff import-contract <file>")
    return 0


def cmd_import_contract(a) -> int:
    ctx = Ctx(a.task)
    raw = sys.stdin.read() if a.file == "-" else Path(a.file).read_text(encoding="utf-8")
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.S)
    body = m.group(1) if m else raw[raw.find("{"): raw.rfind("}") + 1]
    try:
        con = json.loads(body)
    except json.JSONDecodeError as e:
        die(f"could not find valid JSON in the reply: {e}")
    if not isinstance(con, dict) or "mappings" not in con:
        die("JSON found, but it does not look like a contract (no `mappings`)")
    con["reviewed_by"] = a.reviewer or "external-session"
    save(ctx.p("contract.json"), con)
    print(f"saved contract.json (reviewed_by={con['reviewed_by']}); next: cdiff validate")
    return 0


# ---------------------------------------------------------------- status


def cmd_status(a) -> int:
    ctx = Ctx(a.task)
    d = ctx.need_task()
    steps = [("init", "base.json"), ("lock", "lock.json"), ("collect", "diff.json"),
             ("review", "contract.json"), ("render", "report.md")]
    nxt = None
    for name, f in steps:
        ok = (d / f).exists()
        print(f"  [{'x' if ok else ' '}] {name:8} {f}")
        if not ok and nxt is None:
            nxt = name
    print(f"next phase: {nxt or 'gate (human decisions)'}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="cdiff", description=__doc__.split("\n")[0])
    ap.add_argument("--task", help="task slug (default: .codediff/CURRENT)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init")
    p.add_argument("--task", dest="task", default=argparse.SUPPRESS)
    p.add_argument("--force", action="store_true")
    p.add_argument("--allow-dirty", action="store_true")
    p = sub.add_parser("lock")
    p.add_argument("--confirmed", action="store_true", help="human has approved requirements.json")
    for n in ("collect", "validate", "gate", "status", "reviewer-pack"):
        sub.add_parser(n)
    p = sub.add_parser("import-contract")
    p.add_argument("file", help="file with the reviewer's reply, or - for stdin")
    p.add_argument("--reviewer", help="who reviewed, e.g. 'gpt-5 fresh chat'")
    p = sub.add_parser("render")
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("decide")
    p.add_argument("id")
    p.add_argument("choice", choices=["keep", "revert", "discuss"])
    p.add_argument("--note")
    a = ap.parse_args()
    fn = globals()["cmd_" + a.cmd.replace("-", "_")]
    return fn(a) or 0


if __name__ == "__main__":
    sys.exit(main())
