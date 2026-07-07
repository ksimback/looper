#!/usr/bin/env python3
"""Looper runner conformance harness (contract v1).

Checks that a runner honors the observable obligations in RUNNER-CONTRACT.md:
gates, caps, consent, redaction, state, and exit codes. Scenarios scaffold a
fixture loop in a temp directory (deterministic fake host/judge scripts - no
real model CLIs), compile it with the repo compiler, invoke the candidate
runner, and assert on what the contract makes observable: exit codes, state
file contents, artifacts, and what reached the fake judge's stdin.

Usage:
    python conformance/check_runner.py path/to/runner[.py] [--only NAME]

A .py runner is invoked with this same Python interpreter; anything else is
executed directly. The resolved spec path is passed as the single argument,
with the loop directory as the working directory.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
from typing import Any, Callable, Optional

ROOT = Path(__file__).resolve().parents[1]
COMPILER = ROOT / "scripts" / "looper.py"

FAKE_HOST = '''
import pathlib, sys
prompt = sys.stdin.read()
if len(sys.argv) > 1:
    capture = pathlib.Path(sys.argv[1])
    previous = capture.read_text(encoding="utf-8") if capture.exists() else ""
    capture.write_text(previous + "\\n---CALL---\\n" + prompt, encoding="utf-8")
if "Revise the artifact" in prompt:
    print("Revised artifact")
    print()
    print("Owner: host")
    print("No TBD")
elif "Draft plan.md" in prompt:
    print("# Plan")
    print()
    print("Owner: host")
    print("No TBD")
else:
    print("# Delivery")
    print()
    print("Owner: host")
    print("No TBD")
'''

# Verdict-emitting judge: revises `revise_count` times, then passes. Records
# every prompt it receives so scenarios can assert what crossed the boundary.
FAKE_JUDGE = '''
import json, pathlib, sys
state_path = pathlib.Path(sys.argv[1])
revise_count = int(sys.argv[2])
capture = pathlib.Path(sys.argv[3]) if len(sys.argv) > 3 else None
prompt = sys.stdin.read()
if capture is not None:
    previous = capture.read_text(encoding="utf-8") if capture.exists() else ""
    capture.write_text(previous + "\\n---CALL---\\n" + prompt, encoding="utf-8")
state = {"count": 0}
if state_path.exists():
    state = json.loads(state_path.read_text(encoding="utf-8"))
count = int(state.get("count", 0))
state["count"] = count + 1
state_path.write_text(json.dumps(state), encoding="utf-8")
verdict = (
    {"verdict": "revise", "blocking_issues": ["Add an explicit owner."], "confidence": 0.9, "notes": ""}
    if count < revise_count
    else {"verdict": "pass", "blocking_issues": [], "confidence": 0.9, "notes": ""}
)
print("```json")
print(json.dumps(verdict))
print("```")
'''

BAD_JUDGE = '''
print("Looks pretty good, but this is not JSON.")
'''

CHECK_CONTAINS = '''
import pathlib, sys
path = pathlib.Path(sys.argv[1])
needle = sys.argv[2]
if path.exists() and needle in path.read_text(encoding="utf-8"):
    raise SystemExit(0)
print(f"{needle!r} not found in {path}", file=sys.stderr)
raise SystemExit(1)
'''

SECRET = "CONFORMANCE-SECRET-VALUE-93af"


class Scenario:
    def __init__(self, name: str, doc: str, fn: Callable[["Harness"], Optional[str]]):
        self.name = name
        self.doc = doc
        self.fn = fn


class Harness:
    def __init__(self, runner: Path, python: str):
        self.runner = runner
        self.python = python

    def runner_argv(self, spec: Path) -> list:
        if self.runner.suffix == ".py":
            return [self.python, str(self.runner), str(spec)]
        return [str(self.runner), str(spec)]

    def scaffold(
        self,
        work: Path,
        *,
        judge_role: str = "judge",
        judge_local: bool = True,
        judge_script: str = "fake_judge",
        judge_revise_count: int = 1,
        egress_yaml: str = "  egress: []",
        extra_context: str = "",
        max_revisions: int = 2,
    ) -> Path:
        """Write fixture scripts + loop.yaml into `work`, compile, return spec path."""
        fixtures = work / "fixtures"
        fixtures.mkdir(parents=True, exist_ok=True)
        (fixtures / "fake_host.py").write_text(textwrap.dedent(FAKE_HOST), encoding="utf-8")
        (fixtures / "fake_judge.py").write_text(textwrap.dedent(FAKE_JUDGE), encoding="utf-8")
        (fixtures / "bad_judge.py").write_text(textwrap.dedent(BAD_JUDGE), encoding="utf-8")
        (fixtures / "check_contains.py").write_text(textwrap.dedent(CHECK_CONTAINS), encoding="utf-8")
        (work / "inputs").mkdir(exist_ok=True)
        (work / "inputs" / "process-notes.md").write_text("Need a useful loop.\n", encoding="utf-8")

        python = Path(self.python).as_posix()
        judge_state = (work / "judge-state.json").as_posix()
        capture = (work / "judge-capture.txt").as_posix()
        if judge_script == "fake_judge":
            judge_invoke = (
                f'["{python}", "{(fixtures / "fake_judge.py").as_posix()}", '
                f'"{judge_state}", "{judge_revise_count}", "{capture}"]'
            )
        else:
            judge_invoke = f'["{python}", "{(fixtures / "bad_judge.py").as_posix()}"]'

        loop_yaml = textwrap.dedent(
            f"""
            version: 1
            meta:
              name: conformance-loop
              description: Conformance fixture loop
              author: conformance
              created: 2026-07-07

            goal:
              statement: Produce a checked delivery.
              context_sources:
                - file: ./inputs/process-notes.md
            __EXTRA_CONTEXT__
              definition_of_done: The delivery includes an owner and no TBD.
              verification:
                - id: has-owner
                  type: programmatic
                  check: ["{python}", "{(fixtures / 'check_contains.py').as_posix()}", "loop-workspace/delivery-1.md", "Owner:"]
                  expect: exit_zero
                - id: covers-goal
                  type: judge
                  rubric: The artifact includes an owner and no unresolved TBD.

            host:
              cli: fake-host
              model: fixture
              invoke: ["{python}", "{(fixtures / 'fake_host.py').as_posix()}", "{(work / 'host-capture.txt').as_posix()}"]
              timeout_sec: 30

            council:
              - id: reviewer-1
                role: {judge_role}
                cli: fake-judge
                model: fixture
                invoke: {judge_invoke}
                timeout_sec: 30
                scope: [plan, delivery]
                local: {str(judge_local).lower()}

            gates:
              plan_gate:
                when: after_plan
                members: [reviewer-1]
                verdict_policy: revise_until_clean
                verdict_source: reviewer-1
                criteria: [covers-goal]
                max_revisions: {max_revisions}
              delivery_gate:
                when: after_each_delivery
                members: [reviewer-1]
                verdict_policy: revise_until_clean
                verdict_source: reviewer-1
                criteria: [has-owner, covers-goal]
                max_revisions: {max_revisions}

            loop_control:
              max_iterations: 2
              budget:
                wall_clock_min: 5
              human_checkpoints: []
              stop_conditions:
                - all deliveries pass their gate clean
                - max_iterations reached

            privacy:
            __EGRESS__

            workspace:
              dir: ./loop-workspace
              layout: [plan.md, "delivery-{{n}}.md", "review-{{n}}.md", state.json]
            """
        ).strip() + "\n"
        # Sentinels are replaced after dedent so injected blocks supply their
        # own indentation relative to the dedented document.
        loop_yaml = loop_yaml.replace(
            "\n__EXTRA_CONTEXT__", ("\n" + extra_context.rstrip()) if extra_context else ""
        )
        loop_yaml = loop_yaml.replace("__EGRESS__", egress_yaml.strip("\n"))
        (work / "loop.yaml").write_text(loop_yaml, encoding="utf-8")

        compiled = subprocess.run(
            [self.python, str(COMPILER), "compile", "loop.yaml", "--out", "loop.resolved.json"],
            cwd=str(work), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False,
        )
        if compiled.returncode != 0:
            raise RuntimeError(f"fixture failed to compile: {compiled.stderr}")
        return work / "loop.resolved.json"

    def run(self, work: Path, spec: Path, stdin: str = "") -> "subprocess.CompletedProcess[str]":
        return subprocess.run(
            self.runner_argv(spec), cwd=str(work), input=stdin,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            encoding="utf-8", errors="replace", timeout=300, check=False,
        )

    def state(self, work: Path) -> dict:
        path = work / "loop-workspace" / "state.json"
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))


def expect(condition: Any, message: str) -> Optional[str]:
    return None if condition else message


def check_all(*failures: Optional[str]) -> Optional[str]:
    real = [item for item in failures if item]
    return "; ".join(real) if real else None


# --- Scenarios -------------------------------------------------------------

def scenario_happy_path(h: Harness) -> Optional[str]:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        spec = h.scaffold(work, judge_revise_count=1)
        result = h.run(work, spec)
        ws = work / "loop-workspace"
        state = h.state(work)
        return check_all(
            expect(result.returncode == 0, f"expected exit 0, got {result.returncode}: {result.stderr}"),
            expect((ws / "plan.md").exists(), "plan.md missing"),
            expect((ws / "delivery-1.md").exists(), "delivery-1.md missing"),
            expect(state.get("status") == "passed", f"state.status={state.get('status')!r}, want 'passed'"),
            expect((ws / "run-log.md").exists() and (ws / "run-log.md").stat().st_size > 0, "run log missing or empty"),
        )


def scenario_judge_degrade(h: Harness) -> Optional[str]:
    # Unparseable judge output must degrade to revise (never pass) and the
    # run must end failed at the revision cap.
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        spec = h.scaffold(work, judge_script="bad_judge")
        result = h.run(work, spec)
        state = h.state(work)
        return check_all(
            expect(result.returncode != 0, "expected non-zero exit for unparseable judge"),
            expect(state.get("status") not in {"passed", "running", None}, f"state.status={state.get('status')!r} must be terminal and not passed"),
        )


def scenario_consent_fail_closed(h: Harness) -> Optional[str]:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        spec = h.scaffold(work, judge_local=False)
        refused = h.run(work, spec, stdin="")
        judge_called = (work / "judge-state.json").exists()
        state_refused = h.state(work)
        first = check_all(
            expect(refused.returncode != 0, "closed stdin must not allow the send"),
            expect(not judge_called, "judge was invoked without consent"),
            expect(state_refused.get("status") != "passed", "run must not pass without consent"),
        )
        if first:
            return first
        # Fresh workspace, explicit consent: the run must proceed and record it.
        import shutil as _shutil
        _shutil.rmtree(work / "loop-workspace", ignore_errors=True)
        granted = h.run(work, spec, stdin="yes\n")
        state_granted = h.state(work)
        return check_all(
            expect(granted.returncode == 0, f"consented run failed: {granted.stderr}"),
            expect((work / "judge-state.json").exists(), "judge never invoked after consent"),
            expect(bool(state_granted.get("consent")), "granted consent not recorded in state"),
        )


def scenario_prompt_redaction(h: Harness) -> Optional[str]:
    # Flagged-file content seeded into an artifact must not reach the judge.
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        (work / "inputs").mkdir()
        (work / "inputs" / "secret.txt").write_text(SECRET + "\n", encoding="utf-8")
        egress = (
            "  egress:\n"
            "    - to: reviewer-1\n"
            "      sends: [plan, deliveries]\n"
            '      redact: ["inputs/**"]\n'
            "      consent: required\n"
        )
        spec = h.scaffold(work, egress_yaml=egress)
        ws = work / "loop-workspace"
        ws.mkdir()
        (ws / "plan.md").write_text(f"Plan leaked {SECRET} before redaction.\nOwner: host\nNo TBD\n", encoding="utf-8")
        result = h.run(work, spec)
        capture = work / "judge-capture.txt"
        captured = capture.read_text(encoding="utf-8") if capture.exists() else ""
        return check_all(
            expect(result.returncode == 0, f"run failed: {result.stderr}"),
            expect(captured, "judge capture missing - judge never called"),
            expect(SECRET not in captured, "flagged content reached the judge"),
        )


def scenario_host_prompt_scrub(h: Harness) -> Optional[str]:
    # Flagged-file content seeded into an artifact must not reach the host
    # either - the host is a send like any other (contract section 7).
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        (work / "inputs").mkdir()
        (work / "inputs" / "secret.txt").write_text(SECRET + "\n", encoding="utf-8")
        egress = (
            "  egress:\n"
            "    - to: reviewer-1\n"
            "      sends: [plan, deliveries]\n"
            '      redact: ["inputs/**"]\n'
            "      consent: required\n"
        )
        spec = h.scaffold(work, egress_yaml=egress)
        ws = work / "loop-workspace"
        ws.mkdir()
        (ws / "plan.md").write_text(f"Plan leaked {SECRET} before redaction.\nOwner: host\nNo TBD\n", encoding="utf-8")
        result = h.run(work, spec)
        capture = work / "host-capture.txt"
        captured = capture.read_text(encoding="utf-8") if capture.exists() else ""
        return check_all(
            expect(result.returncode == 0, f"run failed: {result.stderr}"),
            expect(captured, "host capture missing - host never called"),
            expect(SECRET not in captured, "flagged content reached the host"),
        )


def scenario_context_non_send(h: Harness) -> Optional[str]:
    # A context source naming a flagged file must not inline its content.
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        (work / "inputs").mkdir()
        (work / "inputs" / "secret.txt").write_text(SECRET + "\n", encoding="utf-8")
        egress = (
            "  egress:\n"
            "    - to: reviewer-1\n"
            "      sends: [plan, deliveries]\n"
            '      redact: ["inputs/secret.txt"]\n'
            "      consent: required\n"
        )
        spec = h.scaffold(work, egress_yaml=egress, extra_context="    - file: ./inputs/secret.txt")
        result = h.run(work, spec)
        capture = work / "judge-capture.txt"
        captured = capture.read_text(encoding="utf-8") if capture.exists() else ""
        return check_all(
            expect(result.returncode == 0, f"run failed: {result.stderr}"),
            expect(SECRET not in captured, "flagged context file content reached the judge"),
        )


def scenario_cmd_output_scrub(h: Harness) -> Optional[str]:
    # A cmd context source that prints a flagged file must be scrubbed.
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        (work / "inputs").mkdir()
        (work / "inputs" / "secret.txt").write_text(SECRET + "\n", encoding="utf-8")
        python = Path(h.python).as_posix()
        cmd_line = (
            f'    - cmd: ["{python}", "-c", '
            '"import pathlib; print(pathlib.Path(\'inputs/secret.txt\').read_text())"]'
        )
        egress = (
            "  egress:\n"
            "    - to: reviewer-1\n"
            "      sends: [plan, deliveries]\n"
            '      redact: ["inputs/secret.txt"]\n'
            "      consent: required\n"
        )
        spec = h.scaffold(work, egress_yaml=egress, extra_context=cmd_line)
        result = h.run(work, spec)
        capture = work / "judge-capture.txt"
        captured = capture.read_text(encoding="utf-8") if capture.exists() else ""
        return check_all(
            expect(result.returncode == 0, f"run failed: {result.stderr}"),
            expect(SECRET not in captured, "cmd output leaked flagged content to the judge"),
        )


def scenario_workspace_escape(h: Harness) -> Optional[str]:
    with tempfile.TemporaryDirectory() as tmp:
        outer = Path(tmp)
        work = outer / "loop"
        work.mkdir()
        spec = h.scaffold(work)
        resolved = json.loads(spec.read_text(encoding="utf-8"))
        resolved["workspace"]["dir"] = "../escaped-workspace"
        spec.write_text(json.dumps(resolved), encoding="utf-8")
        result = h.run(work, spec)
        return check_all(
            expect(result.returncode != 0, "runner accepted a workspace outside the loop directory"),
            expect(not (outer / "escaped-workspace").exists(), "runner created a directory outside the loop directory"),
        )


def scenario_revision_cap(h: Harness) -> Optional[str]:
    # A judge that keeps revising must exhaust max_revisions and fail the
    # run with a terminal state, not loop forever or pass.
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        spec = h.scaffold(work, judge_revise_count=99, max_revisions=1)
        result = h.run(work, spec)
        state = h.state(work)
        return check_all(
            expect(result.returncode != 0, "expected non-zero exit at revision cap"),
            expect(state.get("status") not in {"passed", "running", None}, f"state.status={state.get('status')!r} must be terminal and not passed"),
        )


SCENARIOS = [
    Scenario("happy-path", "gates pass after one revision; artifacts, state, log exist", scenario_happy_path),
    Scenario("judge-degrade", "unparseable judge output degrades to revise and fails at cap", scenario_judge_degrade),
    Scenario("consent-fail-closed", "non-local member requires recorded consent before first send", scenario_consent_fail_closed),
    Scenario("prompt-redaction", "flagged-file content in an artifact never reaches a member", scenario_prompt_redaction),
    Scenario("host-prompt-scrub", "flagged-file content in an artifact never reaches the host either", scenario_host_prompt_scrub),
    Scenario("context-non-send", "flagged context files are not inlined into prompts", scenario_context_non_send),
    Scenario("cmd-output-scrub", "cmd context-source output is scrubbed of flagged content", scenario_cmd_output_scrub),
    Scenario("workspace-escape", "workspace.dir outside the loop directory is refused", scenario_workspace_escape),
    Scenario("revision-cap", "max_revisions bounds the gate and ends the run failed", scenario_revision_cap),
]


def main(argv: "Optional[list]" = None) -> int:
    parser = argparse.ArgumentParser(description="Looper runner conformance harness (contract v1)")
    parser.add_argument("runner", type=Path, help="Path to the runner under test")
    parser.add_argument("--only", help="Run a single scenario by name")
    parser.add_argument("--python", default=sys.executable, help="Interpreter for .py runners and fixtures")
    args = parser.parse_args(argv)

    runner = args.runner.resolve()
    if not runner.exists():
        print(f"conformance: error: runner not found: {runner}", file=sys.stderr)
        return 2

    harness = Harness(runner, args.python)
    selected = [s for s in SCENARIOS if args.only in (None, s.name)]
    if not selected:
        print(f"conformance: error: unknown scenario {args.only!r}", file=sys.stderr)
        return 2

    failures = 0
    for scenario in selected:
        try:
            problem = scenario.fn(harness)
        except Exception as exc:  # a crashed scenario is a failure, not a crash
            problem = f"scenario raised {type(exc).__name__}: {exc}"
        if problem:
            failures += 1
            print(f"FAIL  {scenario.name}: {problem}")
        else:
            print(f"pass  {scenario.name}")

    total = len(selected)
    print(f"conformance: {total - failures}/{total} scenarios passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
