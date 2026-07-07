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

# Fake host: argv[1] captures every prompt received; optional argv[2] names a
# "leak file" whose content the host copies into the plan it drafts - the
# legitimate path by which flagged content enters an artifact.
FAKE_HOST = '''
import pathlib, sys
prompt = sys.stdin.read()
capture = pathlib.Path(sys.argv[1])
previous = capture.read_text(encoding="utf-8") if capture.exists() else ""
capture.write_text(previous + "\\n---CALL---\\n" + prompt, encoding="utf-8")
leak = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else None
if "Revise the artifact" in prompt:
    print("Revised artifact")
    print()
    print("Owner: host")
    print("No TBD")
elif "Draft plan.md" in prompt:
    print("# Plan")
    print()
    print("Owner: host")
    if leak is not None and leak.exists():
        print(leak.read_text(encoding="utf-8"))
    print("No TBD")
else:
    print("# Delivery")
    print()
    print("Owner: host")
    print("No TBD")
'''

# Verdict-emitting judge: revises `revise_count` times, then passes; a
# negative revise_count means revise forever with a UNIQUE blocking issue per
# call (so no-progress detectors cannot mask a missing revision cap). Records
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
if revise_count < 0:
    verdict = {"verdict": "revise", "blocking_issues": [f"Unique issue {count}."], "confidence": 0.9, "notes": ""}
elif count < revise_count:
    verdict = {"verdict": "revise", "blocking_issues": ["Add an explicit owner."], "confidence": 0.9, "notes": ""}
else:
    verdict = {"verdict": "pass", "blocking_issues": [], "confidence": 0.9, "notes": ""}
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
        host_leak: str = "",
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
        host_invoke = [python, (fixtures / "fake_host.py").as_posix(), (work / "host-capture.txt").as_posix()]
        if host_leak:
            host_invoke.append(host_leak)
        host_invoke_tail = ", ".join(f'"{item}"' for item in host_invoke)
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
              invoke: [{host_invoke_tail}]
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
    # Unparseable judge output must degrade to revise (never pass, never
    # crash): revision rounds must actually happen (review artifacts per the
    # contract's workspace layout) before the run ends failed at the cap.
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        spec = h.scaffold(work, judge_script="bad_judge")
        result = h.run(work, spec)
        state = h.state(work)
        ws = work / "loop-workspace"
        return check_all(
            expect(result.returncode != 0, "expected non-zero exit for unparseable judge"),
            expect(state.get("status") not in {"passed", "running", None}, f"state.status={state.get('status')!r} must be terminal and not passed"),
            expect((ws / "review-plan_gate-1.md").exists(), "no review artifact - runner crashed instead of degrading to revise"),
            expect((ws / "review-plan_gate-2.md").exists(), "no second review round - runner did not revise after degraded verdict"),
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


def read_captures(work: Path) -> tuple[str, str]:
    judge = work / "judge-capture.txt"
    host = work / "host-capture.txt"
    return (
        judge.read_text(encoding="utf-8") if judge.exists() else "",
        host.read_text(encoding="utf-8") if host.exists() else "",
    )


def scenario_prompt_redaction(h: Harness) -> Optional[str]:
    # The host copies a flagged file's content into the plan it drafts (the
    # legitimate leak path); the artifact send to the judge must be scrubbed.
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
        # judge_revise_count=0: no revision round, so the leaked line stays in
        # plan.md for the whole run (the fake host's revision output would
        # otherwise drop it).
        spec = h.scaffold(work, egress_yaml=egress, host_leak="inputs/secret.txt", judge_revise_count=0)
        result = h.run(work, spec)
        judge_cap, _ = read_captures(work)
        plan = work / "loop-workspace" / "plan.md"
        plan_text = plan.read_text(encoding="utf-8") if plan.exists() else ""
        return check_all(
            expect(result.returncode == 0, f"run failed: {result.stderr}"),
            expect(SECRET in plan_text, "fixture broken: leak never entered the artifact"),
            expect(judge_cap, "judge capture missing - judge never called"),
            expect(SECRET not in judge_cap, "flagged content reached the judge"),
        )


def scenario_host_prompt_scrub(h: Harness) -> Optional[str]:
    # The host EMITS the secret into plan.md, but must never RECEIVE it back:
    # the delivery/revise prompts embedding that artifact must be scrubbed
    # (contract section 7 - the host is a send like any other).
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
        spec = h.scaffold(work, egress_yaml=egress, host_leak="inputs/secret.txt", judge_revise_count=0)
        result = h.run(work, spec)
        _, host_cap = read_captures(work)
        plan = work / "loop-workspace" / "plan.md"
        plan_text = plan.read_text(encoding="utf-8") if plan.exists() else ""
        return check_all(
            expect(result.returncode == 0, f"run failed: {result.stderr}"),
            expect(SECRET in plan_text, "fixture broken: leak never entered the artifact"),
            expect(host_cap.count("---CALL---") >= 2, "host was not called for a delivery after the plan"),
            expect(SECRET not in host_cap, "flagged content was sent back to the host"),
        )


def scenario_context_non_send(h: Harness) -> Optional[str]:
    # A context source naming a flagged file must not inline its content into
    # any prompt - the host's plan prompt is where context lands first.
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
        judge_cap, host_cap = read_captures(work)
        return check_all(
            expect(result.returncode == 0, f"run failed: {result.stderr}"),
            expect(host_cap, "host capture missing - host never called"),
            expect(SECRET not in host_cap, "flagged context file content reached the host"),
            expect(SECRET not in judge_cap, "flagged context file content reached the judge"),
        )


def scenario_cmd_output_scrub(h: Harness) -> Optional[str]:
    # A cmd context source that prints a flagged file must be scrubbed before
    # its output reaches any prompt (host first, judge downstream).
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
        judge_cap, host_cap = read_captures(work)
        return check_all(
            expect(result.returncode == 0, f"run failed: {result.stderr}"),
            expect(host_cap, "host capture missing - host never called"),
            expect(SECRET not in host_cap, "cmd output leaked flagged content to the host"),
            expect(SECRET not in judge_cap, "cmd output leaked flagged content to the judge"),
        )


def scenario_default_redactions(h: Harness) -> Optional[str]:
    # The default redaction globs (.env etc.) apply even when privacy.egress
    # is empty - a runner that only honors explicit redact lists leaks here.
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        (work / ".env").write_text(f"API_KEY={SECRET}\n", encoding="utf-8")
        spec = h.scaffold(work, host_leak=".env", judge_revise_count=0)
        result = h.run(work, spec)
        judge_cap, host_cap = read_captures(work)
        return check_all(
            expect(result.returncode == 0, f"run failed: {result.stderr}"),
            expect(judge_cap, "judge capture missing - judge never called"),
            expect(SECRET not in judge_cap, "default-glob (.env) content reached the judge"),
            expect(SECRET not in host_cap, "default-glob (.env) content was sent back to the host"),
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
    # A judge that keeps revising - with a UNIQUE blocking issue every round,
    # so a no-progress detector cannot mask a missing cap - must be stopped
    # by max_revisions exactly: initial round + max_revisions rounds, then a
    # terminal failure with no delivery ever drafted.
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        spec = h.scaffold(work, judge_revise_count=-1, max_revisions=1)
        result = h.run(work, spec)
        state = h.state(work)
        judge_state = work / "judge-state.json"
        calls = 0
        if judge_state.exists():
            calls = int(json.loads(judge_state.read_text(encoding="utf-8")).get("count", 0))
        return check_all(
            expect(result.returncode != 0, "expected non-zero exit at revision cap"),
            expect(state.get("status") not in {"passed", "running", None}, f"state.status={state.get('status')!r} must be terminal and not passed"),
            expect(calls == 2, f"judge called {calls} times; max_revisions=1 means exactly 2 rounds (initial + one revision)"),
            expect(not (work / "loop-workspace" / "delivery-1.md").exists(), "delivery drafted despite the plan gate never passing"),
        )


SCENARIOS = [
    Scenario("happy-path", "gates pass after one revision; artifacts, state, log exist", scenario_happy_path),
    Scenario("judge-degrade", "unparseable judge output degrades to revise and fails at cap", scenario_judge_degrade),
    Scenario("consent-fail-closed", "non-local member requires recorded consent before first send", scenario_consent_fail_closed),
    Scenario("prompt-redaction", "flagged-file content in an artifact never reaches a member", scenario_prompt_redaction),
    Scenario("host-prompt-scrub", "flagged-file content in an artifact never reaches the host either", scenario_host_prompt_scrub),
    Scenario("default-redactions", "the default redaction globs apply even with no egress entries", scenario_default_redactions),
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
