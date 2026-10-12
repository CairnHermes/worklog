#!/usr/bin/env python3
"""
AI Web App Completion Agent  --  working MVP core (proof of concept)
====================================================================

What this proves (real, runnable, offline):

  1. IMPORT      -- takes a project ZIP (zip-slip safe) into an isolated
                    sandbox directory.
  2. ANALYZE     -- deterministic project inspection that finds REAL gaps:
                    stack detection, syntax errors (actual compiler output
                    with file + line), TODO/FIXME inventory, missing
                    entrypoint, missing test suite.
  3. FIX         -- the fix step runs in a pluggable `Fixer` slot. The LLM
                    call is the slot; this build ships a working offline
                    Fixer that applies a prepared patch, so the full
                    "modify actual project files" loop is proven end to
                    end. Swapping in a real model is a one-method change
                    (see LLMFixer).
  4. VERIFY      -- runs the project's test suite BEFORE and AFTER the fix
                    and reports red -> green.
  5. ROLLBACK    -- full snapshot/restore of the sandbox, so every agent
                    action is reversible (the client's explicit ask).
  6. DASHBOARD   -- emits progress.json + report.md: status, code changes,
                    errors seen, test results per step. This is the data
                    the Next.js dashboard renders.

The client asked for "a working MVP, not just a chatbot that generates
code. The agent must modify actual project files, run the application,
verify fixes, and preserve rollback options." That exact contract is what
this file demonstrates, on a broken sample project it builds itself:

    $ python PoC.py          # builds a broken project, runs the full
                             # import -> analyze -> fix -> verify -> rollback

Stack note: the pipeline is stack-agnostic by design (analyzer + fixer +
verifier are per-stack plugins). This PoC ships the Python stack fully and
the Node stack analyzer; Next.js-specific fixers slot in the same way in
week 2 of the build.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List


# ---------------------------------------------------------------------------
# Sandbox
# ---------------------------------------------------------------------------

class Sandbox:
    """Isolated directory the agent works in. Full snapshot + restore."""

    def __init__(self, root: str) -> None:
        self.root = root

    def import_zip(self, zip_path: str) -> int:
        """Extract with zip-slip protection. Returns file count."""
        count = 0
        base = os.path.realpath(self.root)
        with zipfile.ZipFile(zip_path) as z:
            for info in z.infolist():
                dest = os.path.realpath(os.path.join(self.root, info.filename))
                if not (dest == base or dest.startswith(base + os.sep)):
                    raise ValueError(f"zip-slip blocked: {info.filename}")
                if info.is_dir():
                    os.makedirs(dest, exist_ok=True)
                    continue
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with z.open(info) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)
                count += 1
        return count

    def snapshot(self) -> None:
        snap = self.root + ".snapshot"
        if os.path.exists(snap):
            shutil.rmtree(snap)
        shutil.copytree(self.root, snap, ignore=shutil.ignore_patterns("__pycache__"))

    def restore(self) -> bool:
        snap = self.root + ".snapshot"
        if not os.path.isdir(snap):
            return False
        shutil.rmtree(self.root)
        shutil.copytree(snap, self.root, ignore=shutil.ignore_patterns("__pycache__"))
        return True


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------

@dataclass
class Issue:
    kind: str            # syntax | todo | missing_entry | no_tests
    file: str
    detail: str
    severity: str = "warn"   # error | warn


class PythonAnalyzer:
    def __init__(self, root: str) -> None:
        self.root = root

    def run(self) -> List[Issue]:
        issues: List[Issue] = []
        py_files: List[str] = []
        for dirpath, _dirs, files in os.walk(self.root):
            if "__pycache__" in dirpath:
                continue
            for f in files:
                if f.endswith(".py"):
                    py_files.append(os.path.relpath(os.path.join(dirpath, f), self.root))
        py_files.sort()

        for rel in py_files:
            full = os.path.join(self.root, rel)
            src = open(full, encoding="utf-8", errors="replace").read()
            try:
                compile(src, rel, "exec")
            except SyntaxError as err:
                line_no = f"line {err.lineno}: " if err.lineno else ""
                issues.append(Issue("syntax", rel, line_no + (err.msg or "syntax error"),
                                    severity="error"))
            for i, line in enumerate(src.splitlines(), 1):
                if re.search(r"\b(TODO|FIXME)\b", line):
                    issues.append(Issue("todo", rel, f"line {i}: {line.strip()}"))

        if py_files and not any(f in ("main.py", "app.py") for f in py_files):
            issues.append(Issue("missing_entry", "-", "no main.py / app.py entrypoint"))
        if py_files and not any(f.startswith("test") for f in py_files):
            issues.append(Issue("no_tests", "-", "no test suite found", severity="warn"))
        return issues


class NodeAnalyzer:
    def __init__(self, root: str) -> None:
        self.root = root

    def run(self) -> List[Issue]:
        issues: List[Issue] = []
        pkg_path = os.path.join(self.root, "package.json")
        pkg: Dict[str, Any] = {}
        if os.path.exists(pkg_path):
            pkg = json.load(open(pkg_path, encoding="utf-8"))
        else:
            issues.append(Issue("missing_entry", "-", "no package.json found", severity="error"))
        if pkg and not (pkg.get("main") or pkg.get("scripts", {}).get("start")
                        or any(f in ("index.js", "index.ts") for f in os.listdir(self.root))):
            issues.append(Issue("missing_entry", "package.json", "no entrypoint (main/start/index)"))
        for dirpath, _d, files in os.walk(self.root):
            if "node_modules" in dirpath:
                continue
            for f in files:
                if f.endswith((".js", ".ts")):
                    rel = os.path.relpath(os.path.join(dirpath, f), self.root)
                    for i, line in enumerate(
                            open(os.path.join(dirpath, f), encoding="utf-8", errors="replace").read().splitlines(), 1):
                        if re.search(r"\b(TODO|FIXME)\b", line):
                            issues.append(Issue("todo", rel, f"line {i}: {line.strip()}"))
        return issues


# ---------------------------------------------------------------------------
# Fixer slot (this is where the LLM goes)
# ---------------------------------------------------------------------------

class Fixer:
    """Interface: take a project + issue, change files, return a note."""
    name = "base"

    def can_fix(self, issue: Issue) -> bool:
        return False

    def apply(self, sandbox: "Sandbox", issue: Issue) -> str:
        raise NotImplementedError


class PatchFixer(Fixer):
    """Offline stand-in for the LLM fixer: applies a prepared patch to a
    known issue. In production the LLM produces the same (file, old, new)
    patch shape -- that is the contract."""
    name = "patch-fixer (offline LLM slot)"

    def __init__(self, patches: List[Dict[str, Any]]) -> None:
        self.patches = patches
        self.used: List[str] = []

    def can_fix(self, issue: Issue) -> bool:
        return any(p["file"] == issue.file and p["kind"] == issue.kind for p in self.patches)

    def apply(self, sandbox: "Sandbox", issue: Issue) -> str:
        p = next(p for p in self.patches if p["file"] == issue.file and p["kind"] == issue.kind)
        path = os.path.join(sandbox.root, p["file"])
        text = open(path, encoding="utf-8").read()
        if p["old"] not in text:
            raise RuntimeError(f"patch target not found in {p['file']}")
        open(path, "w", encoding="utf-8").write(text.replace(p["old"], p["new"]))
        self.used.append(f"{p['file']}: {p['note']}")
        return f"{p['file']}: {p['note']}"


class LLMFixer(Fixer):
    """Production slot. Replace `_generate_patch` body with the model call
    (your LLM API); keep the return shape and the rest of the pipeline is
    untouched. Shown here so the swap is a one-method change."""
    name = "llm-fixer"

    def can_fix(self, issue: Issue) -> bool:
        return True

    def apply(self, sandbox: "Sandbox", issue: Issue) -> str:
        file_text = ""
        if issue.file != "-":
            file_text = open(os.path.join(sandbox.root, issue.file), encoding="utf-8").read()
        patch = self._generate_patch(issue, file_text)
        text = open(os.path.join(sandbox.root, patch["file"]), encoding="utf-8").read()
        if patch["old"] not in text:
            raise RuntimeError("LLM patch did not anchor")
        open(os.path.join(sandbox.root, patch["file"]), "w", encoding="utf-8").write(
            text.replace(patch["old"], patch["new"]))
        return f"{patch['file']}: {patch.get('note', 'llm fix')}"

    def _generate_patch(self, issue: Issue, file_text: str) -> Dict[str, Any]:
        # -> call the model here with (issue.detail, file_text) and return
        # {"file": ..., "old": ..., "new": ..., "note": ...}
        raise NotImplementedError("wire in the LLM API here")


# ---------------------------------------------------------------------------
# Verifier
# ---------------------------------------------------------------------------

class Verifier:
    """Runs the test suite (or node --check) inside the sandbox with a hard
    timeout. Returns (passed, detail)."""

    def run(self, sandbox: "Sandbox", stack: str, timeout: int = 30):
        root = sandbox.root
        if stack == "python" and any(f.startswith("test") for f in os.listdir(root) if f.endswith(".py")):
            cmd = [sys.executable, "-m", "unittest", "-v"]
        elif stack == "node":
            cmd = ["node", "--check"]
        else:
            return False, "no verifier for this stack yet"
        try:
            r = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return False, f"timed out after {timeout}s"
        return r.returncode == 0, (r.stdout + r.stderr)[-400:]


# ---------------------------------------------------------------------------
# Agent (the pipeline) + dashboard
# ---------------------------------------------------------------------------

@dataclass
class Step:
    name: str
    status: str
    detail: str = ""
    duration_s: float = 0.0
    changes: List[str] = field(default_factory=list)


class CompletionAgent:
    def __init__(self, sandbox: Sandbox, analyzers: Dict[str, Any],
                 fixers: List[Fixer], verifier: Verifier) -> None:
        self.sandbox = sandbox
        self.analyzers = analyzers
        self.fixers = fixers
        self.verifier = verifier
        self.steps: List[Step] = []
        self.issues_before: List[Issue] = []

    def _record(self, step: Step) -> None:
        self.steps.append(step)
        print(f"  [{step.status.upper():7}] {step.name}  {step.detail}")

    def detect_stack(self) -> str:
        root = self.sandbox.root
        if os.path.exists(os.path.join(root, "package.json")):
            return "node"
        return "python"

    def analyze(self) -> List[Issue]:
        step = Step("analyze", "run")
        t0 = time.time()
        stack = self.detect_stack()
        self.issues_before = self.analyzers.get(stack, PythonAnalyzer)(self.sandbox.root).run()
        errors = sum(1 for i in self.issues_before if i.severity == "error")
        step.status = "done"
        step.detail = f"{stack} stack, {len(self.issues_before)} issues ({errors} error(s))"
        step.duration_s = round(time.time() - t0, 3)
        self._record(step)
        return self.issues_before

    def fix(self) -> None:
        step = Step("fix", "run")
        t0 = time.time()
        for issue in list(self.issues_before):
            if issue.severity != "error":
                continue
            for fx in self.fixers:
                if fx.can_fix(issue):
                    try:
                        step.changes.append(fx.apply(self.sandbox, issue))
                        self.issues_before.remove(issue)
                        break
                    except Exception as ex:  # noqa: BLE001
                        step.detail = f"{issue.file}: fix failed: {ex}"
                        break
            else:
                step.detail = f"{issue.file}: unfixable here (needs {issue.kind} handling)"
        stack = self.detect_stack()
        self.issues_before = self.analyzers[stack](self.sandbox.root).run()
        left = [i for i in self.issues_before if i.severity == "error"]
        step.status = "done" if not left else "partial"
        if not step.detail:
            step.detail = f"fixed {len(step.changes)}, {len(left)} error(s) remaining"
        step.duration_s = round(time.time() - t0, 3)
        self._record(step)

    def verify(self, label: str):
        step = Step(f"verify ({label})", "run")
        t0 = time.time()
        ok, detail = self.verifier.run(self.sandbox, self.detect_stack())
        step.status = "done" if ok else "failed"
        step.detail = (detail.strip().splitlines()[-1][:110]
                       if detail.strip() else ("tests passed" if ok else "failed"))
        step.duration_s = round(time.time() - t0, 3)
        self._record(step)
        return ok, detail

    def rollback(self) -> bool:
        step = Step("rollback", "run")
        t0 = time.time()
        ok = self.sandbox.restore()
        step.status = "done" if ok else "failed"
        step.detail = "sandbox restored to pre-fix state" if ok else "no snapshot"
        step.duration_s = round(time.time() - t0, 3)
        self._record(step)
        return ok

    def dashboard(self, outdir: str, overall: str) -> str:
        os.makedirs(outdir, exist_ok=True)
        data = {
            "overall": overall,
            "stack": self.detect_stack(),
            "steps": [asdict(s) for s in self.steps],
            "remaining_issues": [asdict(i) for i in self.issues_before],
        }
        p = os.path.join(outdir, "progress.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        lines = ["# Agent run report", f"overall: **{overall}**", ""]
        for s in self.steps:
            lines.append(f"- **{s.name}** [{s.status}] {s.detail} ({s.duration_s}s)")
            for c in s.changes:
                lines.append(f"    - {c}")
        if self.issues_before:
            lines.append("")
            lines.append("Remaining issues:")
            for i in self.issues_before:
                lines.append(f"- [{i.severity}] {i.kind} {i.file} {i.detail}")
        r = os.path.join(outdir, "report.md")
        open(r, "w", encoding="utf-8").write("\n".join(lines))
        return r


# ---------------------------------------------------------------------------
# Sample broken project (what the agent will complete)
# ---------------------------------------------------------------------------

SAMPLE_MAIN = '''"""Tiny invoice CLI (sample unfinished project)."""
import json
from utils import total, format_total


def main(argv):
    lines = [json.loads(a) for a in argv]
    print("TOTAL: " + format_total(total(lines)))
    # TODO: support --csv output
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
'''

SAMPLE_UTILS = '''def format_total(value):
    return f"{value:.2f}"


def total(lines):
    return sum(l.get("amount", 0) for l in lines   # BUG: unclosed paren
'''

SAMPLE_TEST = '''import contextlib
import io
import unittest
from main import main


class TestMain(unittest.TestCase):
    def test_total(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            main(['{"amount": 10.5}', '{"amount": 4.25}'])
        self.assertIn("TOTAL: 14.75", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
'''


def make_sample_zip(zip_path: str) -> None:
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("main.py", SAMPLE_MAIN)
        z.writestr("utils.py", SAMPLE_UTILS)
        z.writestr("test_main.py", SAMPLE_TEST)
        z.writestr("README.md", "unfinished invoice CLI\n")


# ---------------------------------------------------------------------------
# Self-test / demo
# ---------------------------------------------------------------------------

def run() -> int:
    ok_all = True

    def check(name: str, cond: bool, extra: str = "") -> None:
        nonlocal ok_all
        ok_all = ok_all and bool(cond)
        print(f"  {'PASS' if cond else 'FAIL'}  {name}"
              + (f"  {extra}" if extra and not cond else ""))

    tmp = tempfile.mkdtemp(prefix="agent_poc_")
    zip_path = os.path.join(tmp, "project.zip")
    make_sample_zip(zip_path)

    root = os.path.join(tmp, "sandbox")
    os.makedirs(root)
    sb = Sandbox(root)

    # The LLM-slot patch: exactly what a model would emit for the known bug.
    fixer = PatchFixer([{
        "file": "utils.py", "kind": "syntax",
        "old": 'return sum(l.get("amount", 0) for l in lines   # BUG: unclosed paren',
        "new": 'return sum(l.get("amount", 0) for l in lines)',
        "note": "closed the paren in total()",
    }])
    agent = CompletionAgent(
        sb, {"python": PythonAnalyzer, "node": NodeAnalyzer}, [fixer], Verifier())

    print("== IMPORT ==")
    n = sb.import_zip(zip_path)
    check("import extracts 4 files (zip-slip safe)", n == 4, f"got {n}")

    print("\n== ANALYZE (pre-fix) ==")
    issues = agent.analyze()
    kinds = {i.kind for i in issues}
    check("finds the syntax error", "syntax" in kinds)
    check("inventories the TODOs", "todo" in kinds)

    print("\n== VERIFY (before fix) -> must FAIL red ==")
    red, _ = agent.verify("before")
    check("pre-fix tests are red", red is False)

    print("\n== FIX (via LLM slot, offline) ==")
    agent.fix()
    check("syntax error fixed", not [i for i in agent.issues_before if i.kind == "syntax"])

    print("\n== VERIFY (after fix) -> must be GREEN ==")
    green, _ = agent.verify("after")
    check("post-fix tests are green", green is True)

    print("\n== ROLLBACK (proves reversibility) ==")
    sb.snapshot()
    check("snapshot taken", os.path.isdir(root + ".snapshot"))
    p = os.path.join(root, "utils.py")
    with open(p, "a", encoding="utf-8") as f:
        f.write("\n# sabotage\n")
    restored = sb.restore()
    check("rollback restores sandbox",
          restored and "# sabotage" not in open(p, encoding="utf-8").read())

    print("\n== DASHBOARD (the data the web UI renders) ==")
    outdir = os.path.join(tmp, "dashboard")
    report = agent.dashboard(outdir, "completed")
    data = json.load(open(os.path.join(outdir, "progress.json")))
    check("progress.json has 4 pipeline steps", len(data["steps"]) == 4, f"got {len(data['steps'])}")
    check("report.md written", os.path.exists(report))

    print(f"\n{'ALL CHECKS PASSED' if ok_all else 'SOME CHECKS FAILED'}")
    print(f"(artifacts in {outdir})")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(run())
