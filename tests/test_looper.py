from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[1]
LOOPER = ROOT / "scripts" / "looper.py"
RUNNER_TEMPLATE = ROOT / "templates" / "run-loop.py"
FIXTURES = ROOT / "tests" / "fixtures"


def run_cmd(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


def write_loop_yaml(path: Path, *, judge_role: str = "judge", judge_script: Path | None = None) -> None:
    judge_script = judge_script or (FIXTURES / "fake_judge.py")
    judge_state = path.parent / "judge-state.json"
    python = Path(sys.executable).as_posix()
    path.write_text(
        textwrap.dedent(
            f"""
            version: 1
            meta:
              name: fixture-loop
              description: Fixture loop
              author: tests
              created: 2026-06-18

            goal:
              statement: Produce a checked LOOP.md.
              context_sources:
                - file: ./inputs/process-notes.md
              definition_of_done: LOOP.md includes an owner and no TBD.
              verification:
                - id: has-owner
                  type: programmatic
                  check: ["{python}", "{(FIXTURES / 'check_contains.py').as_posix()}", "loop-workspace/delivery-1.md", "Owner:"]
                  expect: exit_zero
                - id: covers-goal
                  type: judge
                  rubric: The artifact includes an owner and no unresolved TBD.

            host:
              cli: fake-host
              model: fixture
              invoke: ["{python}", "{(FIXTURES / 'fake_host.py').as_posix()}"]
              timeout_sec: 30

            council:
              - id: reviewer-1
                role: {judge_role}
                cli: fake-judge
                model: fixture
                invoke: ["{python}", "{judge_script.as_posix()}", "{judge_state.as_posix()}", "1"]
                timeout_sec: 30
                scope: [plan, delivery]
                local: true

            gates:
              plan_gate:
                when: after_plan
                members: [reviewer-1]
                verdict_policy: revise_until_clean
                verdict_source: reviewer-1
                criteria: [covers-goal]
                max_revisions: 2
              delivery_gate:
                when: after_each_delivery
                members: [reviewer-1]
                verdict_policy: revise_until_clean
                verdict_source: reviewer-1
                criteria: [has-owner, covers-goal]
                max_revisions: 2

            loop_control:
              max_iterations: 2
              budget:
                wall_clock_min: 5
              human_checkpoints: []
              stop_conditions:
                - all deliveries pass their gate clean
                - max_iterations reached

            privacy:
              egress: []

            workspace:
              dir: ./loop-workspace
              layout: [plan.md, "delivery-{{n}}.md", "review-{{n}}.md", state.json]
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )


class LooperTests(unittest.TestCase):
    def test_compile_and_runner_revise_then_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "inputs").mkdir()
            (work / "inputs" / "process-notes.md").write_text("Need a useful loop.\n", encoding="utf-8")
            write_loop_yaml(work / "loop.yaml")
            shutil.copyfile(RUNNER_TEMPLATE, work / "run-loop.py")

            compiled = run_cmd(
                [
                    sys.executable,
                    str(LOOPER),
                    "compile",
                    "loop.yaml",
                    "--out",
                    "loop.resolved.json",
                    "--render",
                    "LOOP.md",
                    "--session-prompt",
                    "RUN_IN_SESSION.md",
                ],
                work,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            self.assertTrue((work / "loop.resolved.json").exists())
            self.assertTrue((work / "LOOP.md").exists())
            loop_doc = (work / "LOOP.md").read_text(encoding="utf-8")
            self.assertIn("## Flow Preview", loop_doc)
            self.assertIn("+--------------------------------+", loop_doc)
            self.assertNotIn("flowchart TD", loop_doc)
            session_prompt = (work / "RUN_IN_SESSION.md").read_text(encoding="utf-8")
            self.assertIn("Run `fixture-loop` In This Session", session_prompt)
            self.assertIn("Do not use `run-loop.py`", session_prompt)
            self.assertIn("Max iterations: `2`", session_prompt)
            self.assertIn("run-log.md", session_prompt)
            self.assertIn("No-progress", session_prompt)

            result = run_cmd([sys.executable, "run-loop.py"], work)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertTrue((work / "loop-workspace" / "plan.md").exists())
            self.assertTrue((work / "loop-workspace" / "delivery-1.md").exists())
            self.assertTrue((work / "loop-workspace" / "review-plan_gate-1.md").exists())
            self.assertTrue((work / "loop-workspace" / "run-log.md").exists())
            state = json.loads((work / "loop-workspace" / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "passed")

    def test_compile_rejects_reviewer_as_verdict_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "inputs").mkdir()
            (work / "inputs" / "process-notes.md").write_text("Need a useful loop.\n", encoding="utf-8")
            write_loop_yaml(work / "loop.yaml", judge_role="reviewer")
            result = run_cmd([sys.executable, str(LOOPER), "compile", "loop.yaml"], work)
            self.assertEqual(result.returncode, 2)
            self.assertIn("must name a judge", result.stderr)

    def test_unparseable_judge_degrades_to_revision_and_stops_at_cap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "inputs").mkdir()
            (work / "inputs" / "process-notes.md").write_text("Need a useful loop.\n", encoding="utf-8")
            write_loop_yaml(work / "loop.yaml", judge_script=FIXTURES / "bad_judge.py")
            shutil.copyfile(RUNNER_TEMPLATE, work / "run-loop.py")

            compiled = run_cmd(
                [sys.executable, str(LOOPER), "compile", "loop.yaml", "--out", "loop.resolved.json"],
                work,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = run_cmd([sys.executable, "run-loop.py"], work)
            self.assertNotEqual(result.returncode, 0)
            review = (work / "loop-workspace" / "review-plan_gate-1.md").read_text(encoding="utf-8")
            self.assertIn("unparseable_judge_output", review)

    def test_session_prompt_command_renders_from_resolved_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "inputs").mkdir()
            (work / "inputs" / "process-notes.md").write_text("Need a useful loop.\n", encoding="utf-8")
            write_loop_yaml(work / "loop.yaml")

            compiled = run_cmd(
                [sys.executable, str(LOOPER), "compile", "loop.yaml", "--out", "loop.resolved.json"],
                work,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            rendered = run_cmd(
                [
                    sys.executable,
                    str(LOOPER),
                    "session-prompt",
                    "loop.resolved.json",
                    "--out",
                    "RUN_IN_SESSION.md",
                ],
                work,
            )
            self.assertEqual(rendered.returncode, 0, rendered.stderr)
            prompt = (work / "RUN_IN_SESSION.md").read_text(encoding="utf-8")
            self.assertIn("Use this prompt when the user wants to run", prompt)
            self.assertIn("covers-goal", prompt)
            self.assertIn("Execution Boundary", prompt)


if __name__ == "__main__":
    unittest.main()
