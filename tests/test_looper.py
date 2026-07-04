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


def run_cmd(argv: list[str], cwd: Path, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        input=stdin,
        check=False,
    )


def write_loop_yaml(
    path: Path,
    *,
    judge_role: str = "judge",
    judge_script: Path | None = None,
    judge_local: bool = True,
) -> None:
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
                local: {str(judge_local).lower()}

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

    def test_fixed_passes_does_not_bypass_failed_programmatic_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "inputs").mkdir()
            (work / "inputs" / "process-notes.md").write_text("Need a useful loop.\n", encoding="utf-8")
            write_loop_yaml(work / "loop.yaml")
            shutil.copyfile(RUNNER_TEMPLATE, work / "run-loop.py")

            loop_yaml = (work / "loop.yaml").read_text(encoding="utf-8")
            loop_yaml = loop_yaml.replace("verdict_policy: revise_until_clean", "verdict_policy: fixed_passes")
            loop_yaml = loop_yaml.replace("verdict_source: reviewer-1\n", "")
            loop_yaml = loop_yaml.replace("        criteria:", "    criteria:")
            fail_check = work / "fail_check.py"
            fail_check.write_text("import sys\nsys.exit(9)\n", encoding="utf-8")
            lines = []
            for line in loop_yaml.splitlines():
                if "check_contains.py" in line:
                    lines.append(f'      check: ["{Path(sys.executable).as_posix()}", "{fail_check.as_posix()}"]')
                else:
                    lines.append(line)
            loop_yaml = "\n".join(lines) + "\n"
            (work / "loop.yaml").write_text(loop_yaml, encoding="utf-8")

            compiled = run_cmd(
                [sys.executable, str(LOOPER), "compile", "loop.yaml", "--out", "loop.resolved.json"],
                work,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = run_cmd([sys.executable, "run-loop.py"], work)
            self.assertNotEqual(result.returncode, 0)
            state = json.loads((work / "loop-workspace" / "state.json").read_text(encoding="utf-8"))
            self.assertNotEqual(state["status"], "passed")
            self.assertIn(state["failure"], {"delivery_gate_max_revisions_reached", "no_progress_detected"})

    def test_runner_redacts_configured_files_before_judge_send(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "inputs").mkdir()
            secret_file = work / "inputs" / "secret.txt"
            secret_file.write_text("SUPERSECRET-LOOPER-VALUE\n", encoding="utf-8")
            (work / "inputs" / "process-notes.md").write_text("Need a useful loop.\n", encoding="utf-8")
            capture_file = work / "judge-stdin.txt"
            judge_script = work / "spy_judge.py"
            judge_script.write_text(
                "import json\n"
                "import pathlib\n"
                "import sys\n"
                "\n"
                "prompt = sys.stdin.read()\n"
                f"capture = pathlib.Path({str(capture_file)!r})\n"
                "previous = capture.read_text(encoding='utf-8') if capture.exists() else ''\n"
                "capture.write_text(previous + '\\n---CALL---\\n' + prompt, encoding='utf-8')\n"
                "if 'SUPERSECRET-LOOPER-VALUE' in prompt:\n"
                "    sys.exit(7)\n"
                "print('```json')\n"
                "print(json.dumps({'verdict': 'pass', 'blocking_issues': [], 'confidence': 1.0, 'notes': 'ok'}))\n"
                "print('```')\n",
                encoding="utf-8",
            )
            write_loop_yaml(work / "loop.yaml", judge_script=judge_script)
            shutil.copyfile(RUNNER_TEMPLATE, work / "run-loop.py")

            loop_yaml = (work / "loop.yaml").read_text(encoding="utf-8")
            loop_yaml = loop_yaml.replace(
                "privacy:\n  egress: []",
                "privacy:\n  egress:\n    - to: reviewer-1\n      sends: [plan, deliveries]\n      redact: [\"inputs/**\"]\n      consent: required",
            )
            (work / "loop.yaml").write_text(loop_yaml, encoding="utf-8")

            compiled = run_cmd(
                [sys.executable, str(LOOPER), "compile", "loop.yaml", "--out", "loop.resolved.json"],
                work,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            workspace = work / "loop-workspace"
            workspace.mkdir()
            (workspace / "plan.md").write_text("Plan leaked SUPERSECRET-LOOPER-VALUE before redaction.\n", encoding="utf-8")
            result = run_cmd([sys.executable, "run-loop.py"], work)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            judge_prompt = capture_file.read_text(encoding="utf-8")
            self.assertNotIn("SUPERSECRET-LOOPER-VALUE", judge_prompt)
            self.assertIn("[redacted:inputs/secret.txt]", judge_prompt)

    def test_fixed_passes_reviewer_gate_completes_cleanly(self) -> None:
        # Regression: the synthetic "fixed_passes reviewer pass" marker used to
        # feed the no-progress detector and fail the run before its passes
        # completed.
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "inputs").mkdir()
            (work / "inputs" / "process-notes.md").write_text("Need a useful loop.\n", encoding="utf-8")
            write_loop_yaml(work / "loop.yaml")
            shutil.copyfile(RUNNER_TEMPLATE, work / "run-loop.py")

            loop_yaml = (work / "loop.yaml").read_text(encoding="utf-8")
            loop_yaml = loop_yaml.replace("verdict_policy: revise_until_clean", "verdict_policy: fixed_passes")
            loop_yaml = loop_yaml.replace("verdict_source: reviewer-1\n", "")
            loop_yaml = loop_yaml.replace("        criteria:", "    criteria:")
            (work / "loop.yaml").write_text(loop_yaml, encoding="utf-8")

            compiled = run_cmd(
                [sys.executable, str(LOOPER), "compile", "loop.yaml", "--out", "loop.resolved.json"],
                work,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = run_cmd([sys.executable, "run-loop.py"], work)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            state = json.loads((work / "loop-workspace" / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "passed")

    def test_judge_output_with_nested_json_parses_as_pass(self) -> None:
        # Regression: the old fence regex truncated nested objects at the first
        # closing brace, degrading every structured verdict to revise.
        import importlib.util

        spec = importlib.util.spec_from_file_location("run_loop_template", RUNNER_TEMPLATE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        text = (
            "Here is my verdict.\n"
            "```json\n"
            '{"verdict": "pass", "blocking_issues": [], "confidence": 0.9,'
            ' "notes": "ok", "meta": {"scores": {"clarity": 5}}}\n'
            "```\n"
            "Trailing commentary."
        )
        verdict = module.parse_judge_output(text)
        self.assertEqual(verdict["verdict"], "pass")
        self.assertNotIn("warning", verdict)

    def test_consent_fails_closed_for_cross_vendor_member_without_egress(self) -> None:
        # Regression: a non-local member with no privacy.egress entry used to be
        # sent artifacts with no consent prompt at all.
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "inputs").mkdir()
            (work / "inputs" / "process-notes.md").write_text("Need a useful loop.\n", encoding="utf-8")
            write_loop_yaml(work / "loop.yaml", judge_local=False)
            shutil.copyfile(RUNNER_TEMPLATE, work / "run-loop.py")

            compiled = run_cmd(
                [sys.executable, str(LOOPER), "compile", "loop.yaml", "--out", "loop.resolved.json"],
                work,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr)

            # Closed stdin means consent cannot be granted: refuse and stop.
            refused = run_cmd([sys.executable, "run-loop.py"], work, stdin="")
            self.assertEqual(refused.returncode, 2, refused.stderr + refused.stdout)
            self.assertFalse((work / "judge-state.json").exists(), "judge was invoked without consent")
            state = json.loads((work / "loop-workspace" / "state.json").read_text(encoding="utf-8"))
            self.assertNotEqual(state["status"], "passed")

            # Explicit consent lets the run proceed to completion.
            shutil.rmtree(work / "loop-workspace")
            granted = run_cmd([sys.executable, "run-loop.py"], work, stdin="yes\n")
            self.assertEqual(granted.returncode, 0, granted.stderr + granted.stdout)
            self.assertTrue((work / "judge-state.json").exists())

    def test_nested_env_file_redacted_from_context_and_judge(self) -> None:
        # Regression: bare ".env" globs only matched top-level files, and
        # context gathering ignored configured redactions entirely.
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "inputs").mkdir()
            (work / "inputs" / "process-notes.md").write_text("Need a useful loop.\n", encoding="utf-8")
            (work / "config").mkdir()
            (work / "config" / ".env").write_text("NESTED-LOOPER-SECRET-VALUE=1\n", encoding="utf-8")
            capture_file = work / "judge-stdin.txt"
            judge_script = work / "spy_judge.py"
            judge_script.write_text(
                "import json\n"
                "import pathlib\n"
                "import sys\n"
                "\n"
                "prompt = sys.stdin.read()\n"
                f"capture = pathlib.Path({str(capture_file)!r})\n"
                "previous = capture.read_text(encoding='utf-8') if capture.exists() else ''\n"
                "capture.write_text(previous + '\\n---CALL---\\n' + prompt, encoding='utf-8')\n"
                "print('```json')\n"
                "print(json.dumps({'verdict': 'pass', 'blocking_issues': [], 'confidence': 1.0, 'notes': 'ok'}))\n"
                "print('```')\n",
                encoding="utf-8",
            )
            write_loop_yaml(work / "loop.yaml", judge_script=judge_script)
            shutil.copyfile(RUNNER_TEMPLATE, work / "run-loop.py")

            loop_yaml = (work / "loop.yaml").read_text(encoding="utf-8")
            loop_yaml = loop_yaml.replace(
                "- file: ./inputs/process-notes.md",
                "- file: ./inputs/process-notes.md\n    - file: ./config/.env",
            )
            (work / "loop.yaml").write_text(loop_yaml, encoding="utf-8")

            compiled = run_cmd(
                [sys.executable, str(LOOPER), "compile", "loop.yaml", "--out", "loop.resolved.json"],
                work,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = run_cmd([sys.executable, "run-loop.py"], work)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            context = (work / "loop-workspace" / "context.md").read_text(encoding="utf-8")
            self.assertNotIn("NESTED-LOOPER-SECRET-VALUE", context)
            self.assertIn("[redacted]", context)
            judge_prompt = capture_file.read_text(encoding="utf-8")
            self.assertNotIn("NESTED-LOOPER-SECRET-VALUE", judge_prompt)

    def test_runner_rejects_workspace_outside_loop_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "loop"
            work.mkdir()
            (work / "inputs").mkdir()
            (work / "inputs" / "process-notes.md").write_text("Need a useful loop.\n", encoding="utf-8")
            write_loop_yaml(work / "loop.yaml")
            shutil.copyfile(RUNNER_TEMPLATE, work / "run-loop.py")

            compiled = run_cmd(
                [sys.executable, str(LOOPER), "compile", "loop.yaml", "--out", "loop.resolved.json"],
                work,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            resolved = json.loads((work / "loop.resolved.json").read_text(encoding="utf-8"))
            resolved["workspace"]["dir"] = "../escaped-workspace"
            (work / "loop.resolved.json").write_text(json.dumps(resolved), encoding="utf-8")

            result = run_cmd([sys.executable, "run-loop.py"], work)
            self.assertEqual(result.returncode, 2)
            self.assertIn("escapes the loop directory", result.stderr)
            self.assertFalse((Path(tmp) / "escaped-workspace").exists())

    def test_cli_errors_are_clean_for_missing_files_and_runner_help(self) -> None:
        result = run_cmd([sys.executable, str(LOOPER), "compile", "does-not-exist.yaml"], ROOT)
        self.assertEqual(result.returncode, 2)
        self.assertIn("looper: error: Could not read", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

        help_result = run_cmd([sys.executable, str(RUNNER_TEMPLATE), "--help"], ROOT)
        self.assertEqual(help_result.returncode, 0)
        self.assertIn("Run a compiled Looper loop", help_result.stdout)

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
