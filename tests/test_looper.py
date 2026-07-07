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

    def test_cmd_context_source_output_is_scrubbed_and_surfaced(self) -> None:
        # Regression: cmd context-source output used to be inlined verbatim,
        # so a command that printed a flagged file leaked it into context.md
        # and every downstream prompt.
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "inputs").mkdir()
            (work / "inputs" / "process-notes.md").write_text("Need a useful loop.\n", encoding="utf-8")
            (work / "inputs" / "secret.txt").write_text("SUPERSECRET-LOOPER-VALUE\n", encoding="utf-8")
            write_loop_yaml(work / "loop.yaml")
            shutil.copyfile(RUNNER_TEMPLATE, work / "run-loop.py")

            python = Path(sys.executable).as_posix()
            loop_yaml = (work / "loop.yaml").read_text(encoding="utf-8")
            loop_yaml = loop_yaml.replace(
                "- file: ./inputs/process-notes.md",
                "- file: ./inputs/process-notes.md\n"
                f'    - cmd: ["{python}", "-c", "import pathlib; print(pathlib.Path(\'inputs/secret.txt\').read_text())"]',
            )
            loop_yaml = loop_yaml.replace(
                "privacy:\n  egress: []",
                "privacy:\n  egress:\n    - to: reviewer-1\n      sends: [plan, deliveries]\n"
                "      redact: [\"inputs/secret.txt\"]\n      consent: required",
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
            self.assertNotIn("SUPERSECRET-LOOPER-VALUE", context)
            self.assertIn("[redacted:inputs/secret.txt]", context)
            run_log = (work / "loop-workspace" / "run-log.md").read_text(encoding="utf-8")
            self.assertIn("redaction_applied", run_log)
            state = json.loads((work / "loop-workspace" / "state.json").read_text(encoding="utf-8"))
            self.assertTrue(
                any("context command output" in item for item in state.get("warnings", [])),
                state.get("warnings"),
            )

    def test_artifact_leak_scrub_is_surfaced_in_state_and_log(self) -> None:
        # Content-scrubbing an artifact send is no longer silent: the runner
        # records which flagged files leaked into which prompt.
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "inputs").mkdir()
            (work / "inputs" / "process-notes.md").write_text("Need a useful loop.\n", encoding="utf-8")
            (work / "inputs" / "secret.txt").write_text("SUPERSECRET-LOOPER-VALUE\n", encoding="utf-8")
            write_loop_yaml(work / "loop.yaml")
            shutil.copyfile(RUNNER_TEMPLATE, work / "run-loop.py")

            loop_yaml = (work / "loop.yaml").read_text(encoding="utf-8")
            loop_yaml = loop_yaml.replace(
                "privacy:\n  egress: []",
                "privacy:\n  egress:\n    - to: reviewer-1\n      sends: [plan, deliveries]\n"
                "      redact: [\"inputs/**\"]\n      consent: required",
            )
            (work / "loop.yaml").write_text(loop_yaml, encoding="utf-8")

            compiled = run_cmd(
                [sys.executable, str(LOOPER), "compile", "loop.yaml", "--out", "loop.resolved.json"],
                work,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            workspace = work / "loop-workspace"
            workspace.mkdir()
            (workspace / "plan.md").write_text(
                "Plan leaked SUPERSECRET-LOOPER-VALUE before redaction.\n", encoding="utf-8"
            )
            result = run_cmd([sys.executable, "run-loop.py"], work)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            run_log = (workspace / "run-log.md").read_text(encoding="utf-8")
            self.assertIn("redaction_applied", run_log)
            state = json.loads((workspace / "state.json").read_text(encoding="utf-8"))
            self.assertTrue(
                any("prompt for reviewer-1" in item for item in state.get("warnings", [])),
                state.get("warnings"),
            )

    def test_host_prompts_are_scrubbed_of_flagged_content(self) -> None:
        # Regression: the host was the one recipient whose prompts were never
        # scrubbed - an artifact leak went verbatim to the host CLI.
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "inputs").mkdir()
            (work / "inputs" / "process-notes.md").write_text("Need a useful loop.\n", encoding="utf-8")
            (work / "inputs" / "secret.txt").write_text("SUPERSECRET-LOOPER-VALUE\n", encoding="utf-8")
            capture_file = work / "host-stdin.txt"
            spy_host = work / "spy_host.py"
            spy_host.write_text(
                "import pathlib\n"
                "import sys\n"
                "\n"
                "prompt = sys.stdin.read()\n"
                f"capture = pathlib.Path({str(capture_file)!r})\n"
                "previous = capture.read_text(encoding='utf-8') if capture.exists() else ''\n"
                "capture.write_text(previous + '\\n---CALL---\\n' + prompt, encoding='utf-8')\n"
                "print('# Artifact')\n"
                "print()\n"
                "print('Owner: host')\n"
                "print('No TBD')\n",
                encoding="utf-8",
            )
            write_loop_yaml(work / "loop.yaml")
            shutil.copyfile(RUNNER_TEMPLATE, work / "run-loop.py")

            python = Path(sys.executable).as_posix()
            loop_yaml = (work / "loop.yaml").read_text(encoding="utf-8")
            loop_yaml = loop_yaml.replace(
                f'invoke: ["{python}", "{(FIXTURES / "fake_host.py").as_posix()}"]',
                f'invoke: ["{python}", "{spy_host.as_posix()}"]',
            )
            loop_yaml = loop_yaml.replace(
                "privacy:\n  egress: []",
                "privacy:\n  egress:\n    - to: reviewer-1\n      sends: [plan, deliveries]\n"
                "      redact: [\"inputs/**\"]\n      consent: required",
            )
            (work / "loop.yaml").write_text(loop_yaml, encoding="utf-8")

            compiled = run_cmd(
                [sys.executable, str(LOOPER), "compile", "loop.yaml", "--out", "loop.resolved.json"],
                work,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            workspace = work / "loop-workspace"
            workspace.mkdir()
            (workspace / "plan.md").write_text(
                "Plan leaked SUPERSECRET-LOOPER-VALUE before redaction.\nOwner: host\nNo TBD\n",
                encoding="utf-8",
            )
            result = run_cmd([sys.executable, "run-loop.py"], work)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            host_prompts = capture_file.read_text(encoding="utf-8")
            self.assertNotIn("SUPERSECRET-LOOPER-VALUE", host_prompts)
            self.assertIn("[redacted:inputs/secret.txt]", host_prompts)
            state = json.loads((workspace / "state.json").read_text(encoding="utf-8"))
            self.assertTrue(
                any("prompt for host" in item for item in state.get("warnings", [])),
                state.get("warnings"),
            )

    def test_leak_attribution_names_all_matching_files_and_dedupes_log(self) -> None:
        # Two flagged files sharing the same content must both be named in
        # the warning, and repeated identical scrubs log one event.
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "inputs").mkdir()
            (work / "inputs" / "process-notes.md").write_text("Need a useful loop.\n", encoding="utf-8")
            (work / "inputs" / "secret-a.txt").write_text("SUPERSECRET-LOOPER-VALUE\n", encoding="utf-8")
            (work / "inputs" / "secret-b.txt").write_text("SUPERSECRET-LOOPER-VALUE\n", encoding="utf-8")
            write_loop_yaml(work / "loop.yaml")
            shutil.copyfile(RUNNER_TEMPLATE, work / "run-loop.py")

            loop_yaml = (work / "loop.yaml").read_text(encoding="utf-8")
            loop_yaml = loop_yaml.replace(
                "privacy:\n  egress: []",
                "privacy:\n  egress:\n    - to: reviewer-1\n      sends: [plan, deliveries]\n"
                "      redact: [\"inputs/secret-a.txt\", \"inputs/secret-b.txt\"]\n      consent: required",
            )
            (work / "loop.yaml").write_text(loop_yaml, encoding="utf-8")

            compiled = run_cmd(
                [sys.executable, str(LOOPER), "compile", "loop.yaml", "--out", "loop.resolved.json"],
                work,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            workspace = work / "loop-workspace"
            workspace.mkdir()
            (workspace / "plan.md").write_text(
                "Plan leaked SUPERSECRET-LOOPER-VALUE before redaction.\nOwner: host\nNo TBD\n",
                encoding="utf-8",
            )
            result = run_cmd([sys.executable, "run-loop.py"], work)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            state = json.loads((workspace / "state.json").read_text(encoding="utf-8"))
            warnings = [item for item in state.get("warnings", []) if "prompt for reviewer-1" in item]
            self.assertTrue(warnings, state.get("warnings"))
            self.assertIn("inputs/secret-a.txt", warnings[0])
            self.assertIn("inputs/secret-b.txt", warnings[0])
            run_log = (workspace / "run-log.md").read_text(encoding="utf-8")
            reviewer_events = [
                line for line in run_log.splitlines()
                if "redaction_applied" in line and "prompt for reviewer-1" in line
            ]
            self.assertEqual(len(reviewer_events), 1, run_log)

    def test_unscrubbable_flagged_file_is_surfaced_as_blind_spot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "inputs").mkdir()
            (work / "inputs" / "process-notes.md").write_text("Need a useful loop.\n", encoding="utf-8")
            (work / "inputs" / "huge.bin").write_text("A" * 1_100_000, encoding="utf-8")
            write_loop_yaml(work / "loop.yaml")
            shutil.copyfile(RUNNER_TEMPLATE, work / "run-loop.py")

            loop_yaml = (work / "loop.yaml").read_text(encoding="utf-8")
            loop_yaml = loop_yaml.replace(
                "privacy:\n  egress: []",
                "privacy:\n  egress:\n    - to: reviewer-1\n      sends: [plan, deliveries]\n"
                "      redact: [\"inputs/huge.bin\"]\n      consent: required",
            )
            (work / "loop.yaml").write_text(loop_yaml, encoding="utf-8")

            compiled = run_cmd(
                [sys.executable, str(LOOPER), "compile", "loop.yaml", "--out", "loop.resolved.json"],
                work,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = run_cmd([sys.executable, "run-loop.py"], work)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            state = json.loads((work / "loop-workspace" / "state.json").read_text(encoding="utf-8"))
            self.assertTrue(
                any("cannot be detected" in item and "huge.bin" in item for item in state.get("warnings", [])),
                state.get("warnings"),
            )
            run_log = (work / "loop-workspace" / "run-log.md").read_text(encoding="utf-8")
            self.assertIn("redaction_unscrubbable", run_log)

    def test_consent_prompt_shows_leak_warning_before_asking(self) -> None:
        # The scrub runs before the consent question, and the consent prompt
        # displays the leak warning for that member.
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "inputs").mkdir()
            (work / "inputs" / "process-notes.md").write_text("Need a useful loop.\n", encoding="utf-8")
            (work / "inputs" / "secret.txt").write_text("SUPERSECRET-LOOPER-VALUE\n", encoding="utf-8")
            write_loop_yaml(work / "loop.yaml", judge_local=False)
            shutil.copyfile(RUNNER_TEMPLATE, work / "run-loop.py")

            loop_yaml = (work / "loop.yaml").read_text(encoding="utf-8")
            loop_yaml = loop_yaml.replace(
                "privacy:\n  egress: []",
                "privacy:\n  egress:\n    - to: reviewer-1\n      sends: [plan, deliveries]\n"
                "      redact: [\"inputs/**\"]\n      consent: required",
            )
            (work / "loop.yaml").write_text(loop_yaml, encoding="utf-8")

            compiled = run_cmd(
                [sys.executable, str(LOOPER), "compile", "loop.yaml", "--out", "loop.resolved.json"],
                work,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            workspace = work / "loop-workspace"
            workspace.mkdir()
            (workspace / "plan.md").write_text(
                "Plan leaked SUPERSECRET-LOOPER-VALUE before redaction.\nOwner: host\nNo TBD\n",
                encoding="utf-8",
            )
            result = run_cmd([sys.executable, "run-loop.py"], work, stdin="yes\n")
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            consent_block = result.stdout.split("Type 'yes' to consent")[0]
            self.assertIn("Warning: flagged content", consent_block)

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

    def test_compile_rejects_invalid_specs(self) -> None:
        python = Path(sys.executable).as_posix()
        host_invoke = f'invoke: ["{python}", "{(FIXTURES / "fake_host.py").as_posix()}"]'
        duplicate_member = (
            "  - id: reviewer-1\n"
            "    role: judge\n"
            "    cli: fake-judge\n"
            "    model: fixture\n"
            '    invoke: ["echo"]\n'
            "    timeout_sec: 30\n"
        )
        cases = [
            ("empty argv", host_invoke, "invoke: []", "must not be empty"),
            ("negative budget", "wall_clock_min: 5", "wall_clock_min: -5", "positive number"),
            ("duplicate council id", "\ngates:", f"\n{duplicate_member}\ngates:", "Duplicate council member id"),
            (
                "verdict_source not a gate member",
                "members: [reviewer-1]",
                "members: []",
                "must also be listed",
            ),
            ("workspace escape", "dir: ./loop-workspace", "dir: ../escape", "relative path inside the loop directory"),
            (
                "context path traversal",
                "- file: ./inputs/process-notes.md",
                "- file: ../../outside.md",
                "relative path inside the loop directory",
            ),
            (
                "context source with file and cmd",
                "- file: ./inputs/process-notes.md",
                '- file: ./inputs/process-notes.md\n      cmd: ["ls"]',
                "exactly one of file or cmd",
            ),
        ]
        for label, old, new, expected in cases:
            with self.subTest(label):
                with tempfile.TemporaryDirectory() as tmp:
                    work = Path(tmp)
                    (work / "inputs").mkdir()
                    (work / "inputs" / "process-notes.md").write_text("Notes.\n", encoding="utf-8")
                    write_loop_yaml(work / "loop.yaml")
                    loop_yaml = (work / "loop.yaml").read_text(encoding="utf-8")
                    mutated = loop_yaml.replace(old, new, 1)
                    self.assertNotEqual(mutated, loop_yaml, f"mutation for {label} did not apply")
                    (work / "loop.yaml").write_text(mutated, encoding="utf-8")
                    result = run_cmd([sys.executable, str(LOOPER), "compile", "loop.yaml"], work)
                    self.assertEqual(result.returncode, 2, f"{label}: {result.stderr}")
                    self.assertIn(expected, result.stderr, label)
                    self.assertNotIn("Traceback", result.stderr, label)

    def test_compile_defaults_null_fields_and_timeouts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "inputs").mkdir()
            (work / "inputs" / "process-notes.md").write_text("Notes.\n", encoding="utf-8")
            write_loop_yaml(work / "loop.yaml")
            python = Path(sys.executable).as_posix()
            loop_yaml = (work / "loop.yaml").read_text(encoding="utf-8")
            loop_yaml = loop_yaml.replace(
                "- file: ./inputs/process-notes.md",
                f'- file: ./inputs/process-notes.md\n    - cmd: ["{python}", "-c", "print(1)"]',
            )
            (work / "loop.yaml").write_text(loop_yaml, encoding="utf-8")

            compiled = run_cmd(
                [sys.executable, str(LOOPER), "compile", "loop.yaml", "--out", "loop.resolved.json"],
                work,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            resolved = json.loads((work / "loop.resolved.json").read_text(encoding="utf-8"))
            self.assertEqual(resolved["goal"]["context_sources"][1]["timeout_sec"], 60)
            self.assertEqual(resolved["criteria_by_id"]["has-owner"]["timeout_sec"], 300)

            # A bare context_sources: key (YAML null) must compile, not crash.
            bare = (work / "loop.yaml").read_text(encoding="utf-8")
            head, _, tail = bare.partition("  context_sources:")
            _, _, rest = tail.partition("  definition_of_done:")
            (work / "loop.yaml").write_text(
                head + "  context_sources:\n  definition_of_done:" + rest, encoding="utf-8"
            )
            result = run_cmd([sys.executable, str(LOOPER), "compile", "loop.yaml"], work)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("Traceback", result.stderr)

    def test_session_prompt_tolerates_utf8_bom(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "inputs").mkdir()
            (work / "inputs" / "process-notes.md").write_text("Notes.\n", encoding="utf-8")
            write_loop_yaml(work / "loop.yaml")
            compiled = run_cmd(
                [sys.executable, str(LOOPER), "compile", "loop.yaml", "--out", "loop.resolved.json"],
                work,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            resolved_path = work / "loop.resolved.json"
            resolved_path.write_bytes(b"\xef\xbb\xbf" + resolved_path.read_bytes())
            rendered = run_cmd(
                [sys.executable, str(LOOPER), "session-prompt", "loop.resolved.json", "--out", "RUN.md"],
                work,
            )
            self.assertEqual(rendered.returncode, 0, rendered.stderr)

    def test_register_model_rejects_secret_material(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = Path(tmp) / "models.json"
            refused = run_cmd(
                [
                    sys.executable,
                    str(LOOPER),
                    "register-model",
                    "leaky",
                    "--invoke",
                    "llm",
                    "api_key=sk-abcdefghijklmnopqrstuvwx",
                    "--registry",
                    str(registry),
                ],
                Path(tmp),
            )
            self.assertEqual(refused.returncode, 2)
            self.assertIn("must never store secrets", refused.stderr)
            self.assertFalse(registry.exists())

            clean = run_cmd(
                [
                    sys.executable,
                    str(LOOPER),
                    "register-model",
                    "mycli",
                    "--invoke",
                    "mycli -p",
                    "--registry",
                    str(registry),
                ],
                Path(tmp),
            )
            self.assertEqual(clean.returncode, 0, clean.stderr)
            data = json.loads(registry.read_text(encoding="utf-8"))
            self.assertEqual(data["mycli"]["invoke"], ["mycli", "-p"])

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

    def test_pattern_library_templates_compile_with_placeholder_warning(self) -> None:
        loops_dir = ROOT / "templates" / "loops"
        template_dirs = sorted(
            path for path in loops_dir.iterdir() if path.is_dir()
        )
        self.assertTrue(template_dirs, "templates/loops/ has no template directories")
        catalog = (loops_dir / "README.md").read_text(encoding="utf-8")
        for template in template_dirs:
            with self.subTest(template.name):
                self.assertTrue(
                    (template / "loop.yaml").is_file(),
                    f"{template.name} is missing loop.yaml",
                )
                self.assertTrue(
                    (template / "README.md").is_file(),
                    f"{template.name} is missing README.md",
                )
                self.assertIn(
                    f"[{template.name}]({template.name}/)",
                    catalog,
                    f"{template.name} is not listed in templates/loops/README.md",
                )
                with tempfile.TemporaryDirectory() as tmp:
                    result = run_cmd(
                        [
                            sys.executable,
                            str(LOOPER),
                            "compile",
                            str(template / "loop.yaml"),
                            "--out",
                            str(Path(tmp) / "loop.resolved.json"),
                        ],
                        ROOT,
                    )
                    self.assertEqual(result.returncode, 0, f"{template.name}: {result.stderr}")
                    self.assertIn(
                        "unresolved template placeholders remain",
                        result.stderr,
                        f"{template.name} should carry {{{{PLACEHOLDER}}}} tokens",
                    )

    def test_compile_placeholder_warning_absent_after_substitution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "inputs").mkdir()
            (work / "inputs" / "process-notes.md").write_text("Notes.\n", encoding="utf-8")
            write_loop_yaml(work / "loop.yaml")

            loop_yaml = (work / "loop.yaml").read_text(encoding="utf-8")
            mutated = loop_yaml.replace(
                "statement: Produce a checked LOOP.md.",
                "statement: Produce a checked LOOP.md for {{PROJECT_NAME}}.",
                1,
            )
            self.assertNotEqual(mutated, loop_yaml)
            (work / "loop.yaml").write_text(mutated, encoding="utf-8")
            with_token = run_cmd([sys.executable, str(LOOPER), "compile", "loop.yaml"], work)
            self.assertEqual(with_token.returncode, 0, with_token.stderr)
            self.assertIn("unresolved template placeholders remain", with_token.stderr)
            self.assertIn("{{PROJECT_NAME}}", with_token.stderr)

            (work / "loop.yaml").write_text(
                mutated.replace("{{PROJECT_NAME}}", "demo-project"), encoding="utf-8"
            )
            filled = run_cmd([sys.executable, str(LOOPER), "compile", "loop.yaml"], work)
            self.assertEqual(filled.returncode, 0, filled.stderr)
            self.assertNotIn("unresolved template placeholders", filled.stderr)


class LintTests(unittest.TestCase):
    def _setup_work(self, tmp: str) -> Path:
        work = Path(tmp)
        (work / "inputs").mkdir()
        (work / "inputs" / "process-notes.md").write_text("Need a useful loop.\n", encoding="utf-8")
        write_loop_yaml(work / "loop.yaml")
        return work

    def _lint(self, work: Path, *flags: str) -> subprocess.CompletedProcess[str]:
        return run_cmd([sys.executable, str(LOOPER), "lint", "loop.yaml", *flags], work)

    def _mutate(self, work: Path, old: str, new: str, count: int = -1) -> None:
        text = (work / "loop.yaml").read_text(encoding="utf-8")
        mutated = text.replace(old, new) if count < 0 else text.replace(old, new, count)
        self.assertNotEqual(mutated, text, f"mutation did not apply: {old!r}")
        (work / "loop.yaml").write_text(mutated, encoding="utf-8")

    def test_clean_fixture_loop_has_no_findings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = self._setup_work(tmp)
            result = self._lint(work)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("no findings", result.stdout)

    def test_all_vibe_verification_warns_and_fails_strict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = self._setup_work(tmp)
            lines = []
            for line in (work / "loop.yaml").read_text(encoding="utf-8").splitlines():
                if "check: [" in line or "expect: exit_zero" in line:
                    continue
                if line.strip() == "type: programmatic":
                    lines.append(line.replace("programmatic", "judge"))
                    lines.append(line.replace("type: programmatic", "rubric: The artifact names an owner."))
                    continue
                lines.append(line)
            (work / "loop.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")

            result = self._lint(work)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("warning[all-vibe-verification]", result.stdout)

            strict = self._lint(work, "--strict")
            self.assertEqual(strict.returncode, 1, strict.stdout + strict.stderr)

    def test_judge_criterion_under_fixed_passes_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = self._setup_work(tmp)
            self._mutate(work, "verdict_policy: revise_until_clean", "verdict_policy: fixed_passes")
            self._mutate(work, "verdict_source: reviewer-1\n", "")
            self._mutate(work, "        criteria:", "    criteria:")
            result = self._lint(work)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("error[judge-criterion-unreachable]", result.stdout)

    def test_cross_vendor_member_without_egress_is_error_and_scoped_egress_clears_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "inputs").mkdir()
            (work / "inputs" / "process-notes.md").write_text("Notes.\n", encoding="utf-8")
            write_loop_yaml(work / "loop.yaml", judge_local=False)

            result = self._lint(work)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("error[unscoped-egress]", result.stdout)

            self._mutate(
                work,
                "privacy:\n  egress: []",
                "privacy:\n  egress:\n    - to: reviewer-1\n      sends: [plan, deliveries]\n"
                "      redact: [\"inputs/**\"]\n      consent: required",
            )
            scoped = self._lint(work)
            self.assertEqual(scoped.returncode, 0, scoped.stdout + scoped.stderr)
            self.assertNotIn("unscoped-egress", scoped.stdout)

            self._mutate(work, "consent: required", "consent: granted")
            granted = self._lint(work)
            self.assertEqual(granted.returncode, 0, granted.stdout + granted.stderr)
            self.assertIn("warning[egress-consent-pregranted]", granted.stdout)

            # A second entry for the same member that still requires consent
            # means the runner will prompt after all; no pre-grant warning.
            self._mutate(
                work,
                "      consent: granted",
                "      consent: granted\n    - to: reviewer-1\n      sends: [reviews]\n"
                "      redact: [\"inputs/**\"]\n      consent: required",
            )
            mixed = self._lint(work)
            self.assertEqual(mixed.returncode, 0, mixed.stdout + mixed.stderr)
            self.assertNotIn("egress-consent-pregranted", mixed.stdout)

    def test_non_local_same_family_member_without_egress_warns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "inputs").mkdir()
            (work / "inputs" / "process-notes.md").write_text("Notes.\n", encoding="utf-8")
            write_loop_yaml(work / "loop.yaml", judge_local=False)
            self._mutate(work, "cli: fake-judge", "cli: fake-host")
            result = self._lint(work)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("warning[non-local-member-without-egress]", result.stdout)
            self.assertIn("warning[same-family-judge]", result.stdout)

    def test_judge_criterion_under_human_verdict_source_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = self._setup_work(tmp)
            self._mutate(work, "verdict_source: reviewer-1", "verdict_source: human")
            result = self._lint(work)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("error[judge-criterion-unreachable]", result.stdout)
            self.assertIn("verdict_source is human", result.stdout)

    def test_unreferenced_cross_vendor_member_warns_instead_of_erroring(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "inputs").mkdir()
            (work / "inputs" / "process-notes.md").write_text("Notes.\n", encoding="utf-8")
            write_loop_yaml(work / "loop.yaml", judge_local=False)
            # Detach the member from both gates: human verdicts, no members,
            # and no judge criteria left anywhere.
            self._mutate(work, "verdict_source: reviewer-1", "verdict_source: human")
            self._mutate(work, "members: [reviewer-1]", "members: []")
            self._mutate(work, "criteria: [covers-goal]", "criteria: []")
            self._mutate(work, "criteria: [has-owner, covers-goal]", "criteria: [has-owner]")
            result = self._lint(work)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotIn("unscoped-egress", result.stdout)
            self.assertIn("warning[unreferenced-council-member]", result.stdout)

    def test_unhonored_human_checkpoint_warns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = self._setup_work(tmp)
            self._mutate(
                work,
                "human_checkpoints: []",
                "human_checkpoints: [after_plan, before cross-vendor send]",
            )
            result = self._lint(work)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("warning[unhonored-human-checkpoint]", result.stdout)
            # Only the unrecognized name is listed; 'after_plan' is honored.
            self.assertIn("['before cross-vendor send']", result.stdout)

    def test_empty_verification_gets_dedicated_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = self._setup_work(tmp)
            lines = []
            skipping = False
            for line in (work / "loop.yaml").read_text(encoding="utf-8").splitlines():
                if line.strip() == "verification:":
                    lines.append(line.replace("verification:", "verification: []"))
                    skipping = True
                    continue
                if skipping and (line.startswith("    ") or not line.strip()):
                    continue
                skipping = False
                lines.append(line)
            text = "\n".join(lines) + "\n"
            text = text.replace("criteria: [covers-goal]", "criteria: []")
            text = text.replace("criteria: [has-owner, covers-goal]", "criteria: []")
            (work / "loop.yaml").write_text(text, encoding="utf-8")
            result = self._lint(work)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("warning[no-verification-criteria]", result.stdout)
            self.assertNotIn("all-vibe-verification", result.stdout)

    def test_egress_to_unknown_member_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = self._setup_work(tmp)
            self._mutate(
                work,
                "privacy:\n  egress: []",
                "privacy:\n  egress:\n    - to: ghost\n      sends: [plan]\n      consent: required",
            )
            result = self._lint(work)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("error[egress-unknown-member]", result.stdout)

    def test_coaching_warnings_for_caps_and_gating(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = self._setup_work(tmp)
            self._mutate(work, "wall_clock_min: 5", "wall_clock_min: null")
            self._mutate(work, "    max_revisions: 2\n", "", 1)
            self._mutate(work, "criteria: [has-owner, covers-goal]", "criteria: [covers-goal]")
            result = self._lint(work)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("warning[no-wall-clock-cap]", result.stdout)
            self.assertIn("warning[missing-max-revisions]", result.stdout)
            self.assertIn("warning[delivery-gate-no-programmatic]", result.stdout)

    def test_json_output_matches_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "inputs").mkdir()
            (work / "inputs" / "process-notes.md").write_text("Notes.\n", encoding="utf-8")
            write_loop_yaml(work / "loop.yaml", judge_local=False)
            result = self._lint(work, "--json")
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["errors"], 1)
            self.assertEqual(
                payload["errors"] + payload["warnings"], len(payload["findings"])
            )
            checks = {item["check"] for item in payload["findings"]}
            self.assertIn("unscoped-egress", checks)

    def test_lint_rejects_uncompilable_spec_with_exit_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = self._setup_work(tmp)
            self._mutate(work, "dir: ./loop-workspace", "dir: ../escape")
            result = self._lint(work)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("looper: error:", result.stderr)

    def test_shipped_templates_and_example_lint_without_errors(self) -> None:
        specs = sorted((ROOT / "templates" / "loops").glob("*/loop.yaml"))
        specs.append(ROOT / "examples" / "ai-workflow-mapping" / "loop.yaml")
        self.assertGreaterEqual(len(specs), 6)
        for spec in specs:
            with self.subTest(spec.parent.name):
                result = run_cmd([sys.executable, str(LOOPER), "lint", str(spec)], ROOT)
                self.assertEqual(result.returncode, 0, f"{spec}: {result.stdout}{result.stderr}")
                self.assertNotIn("error[", result.stdout, spec)


LOOPS = ROOT / "templates" / "loops"


class SecurityScanTemplateTests(unittest.TestCase):
    SCANNER = LOOPS / "security-scan" / "scripts" / "scan-secrets.py"

    def _sweep(self, files: dict[str, str]) -> list[dict]:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            for name, body in files.items():
                (repo / name).write_text(body, encoding="utf-8")
            out = Path(tmp) / "cand.json"
            result = run_cmd(
                [sys.executable, str(self.SCANNER), str(repo), "--out", str(out), "--no-history"],
                ROOT,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(out.read_text(encoding="utf-8"))["candidates"]

    def test_detects_underscore_joined_credential_names(self) -> None:
        cands = self._sweep({"config.py": (
            'SECRET_KEY = "Zq9rT2vXw8LmNp4c"\n'
            "DB_PASSWORD=Corr3ctHorseBat\n"
            "client_secret: NineCharsX\n"
        )})
        self.assertEqual(len(cands), 3, cands)

    def test_value_containing_test_is_not_suppressed(self) -> None:
        cands = self._sweep({"config.py": "password = Contest2024xyz\n"})
        self.assertEqual(len(cands), 1, cands)

    def test_distinct_short_secrets_not_collapsed_by_mask(self) -> None:
        cands = self._sweep({"config.py": "password = hunter22aa\ntoken: swordfish9xy\n"})
        self.assertEqual(len(cands), 2, cands)

    def test_obvious_placeholder_values_are_suppressed(self) -> None:
        cands = self._sweep({"config.py": 'api_key = "your_key_placeholder"\n'})
        self.assertEqual(cands, [])

    def test_masked_excerpt_never_contains_full_value(self) -> None:
        cands = self._sweep({"config.py": "password = hunter22aa\n"})
        self.assertEqual(len(cands), 1)
        self.assertNotIn("hunter22aa", cands[0]["excerpt"])


class TemplateCheckerScriptTests(unittest.TestCase):
    def _run(self, script: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return run_cmd([sys.executable, str(script), *args], ROOT)

    def test_findings_rejects_triple_question_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "SECURITY-FINDINGS.md"
            report.write_text(
                "# Findings\n\nsecret pii vulnerability\nType: bug\nSeverity: high\n"
                "Location: a.py:1\nRemediation: ???\n",
                encoding="utf-8",
            )
            result = self._run(
                LOOPS / "security-scan" / "scripts" / "check-findings.py", str(report)
            )
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn("placeholder", result.stderr)

    def test_findings_incidental_no_secrets_phrase_does_not_waive_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "SECURITY-FINDINGS.md"
            report.write_text(
                "# Report\n\nsecret pii vulnerability scan.\n"
                "No secrets were detected in git history.\n"
                "Critical issue at server.py:42 but no field labels.\n",
                encoding="utf-8",
            )
            result = self._run(
                LOOPS / "security-scan" / "scripts" / "check-findings.py", str(report)
            )
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn("missing required field label", result.stderr)

    def test_findings_genuine_empty_report_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "SECURITY-FINDINGS.md"
            report.write_text(
                "# Report\n\nChecked secret, pii, vulnerability areas.\nNo findings.\n",
                encoding="utf-8",
            )
            result = self._run(
                LOOPS / "security-scan" / "scripts" / "check-findings.py", str(report)
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_citations_reject_path_outside_sources_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loop = Path(tmp)
            (loop / "inputs" / "sources").mkdir(parents=True)
            (loop / "inputs" / "sources" / "a.md").write_text("notes\n", encoding="utf-8")
            (loop / "loop-workspace").mkdir()
            (loop / "loop-workspace" / "report-draft-1.md").write_text("draft\n", encoding="utf-8")
            report = loop / "loop-workspace" / "REPORT.md"
            checker = LOOPS / "research-synthesis" / "scripts" / "check-citations.py"

            report.write_text(
                "# A\n\nClaim supported here indeed. [source: loop-workspace/report-draft-1.md]\n",
                encoding="utf-8",
            )
            outside = run_cmd(
                [sys.executable, str(checker), "loop-workspace/REPORT.md", "--sources", "inputs/sources"],
                loop,
            )
            self.assertEqual(outside.returncode, 1, outside.stdout)

            report.write_text(
                "# A\n\nClaim supported here indeed. [source: inputs/sources/a.md]\n",
                encoding="utf-8",
            )
            inside = run_cmd(
                [sys.executable, str(checker), "loop-workspace/REPORT.md", "--sources", "inputs/sources"],
                loop,
            )
            self.assertEqual(inside.returncode, 0, inside.stderr)


class CrashStateTests(unittest.TestCase):
    def test_unexpected_crash_leaves_terminal_state_not_running(self) -> None:
        # Contract section 5: a crash must not leave state claiming
        # "running". A non-UTF-8 context file used to do exactly that.
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "inputs").mkdir()
            (work / "inputs" / "process-notes.md").write_bytes(
                "caf\xe9 notes\n".encode("latin-1")
            )
            write_loop_yaml(work / "loop.yaml")
            shutil.copyfile(RUNNER_TEMPLATE, work / "run-loop.py")

            compiled = run_cmd(
                [sys.executable, str(LOOPER), "compile", "loop.yaml", "--out", "loop.resolved.json"],
                work,
            )
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = run_cmd([sys.executable, "run-loop.py"], work)
            self.assertNotEqual(result.returncode, 0)
            state = json.loads((work / "loop-workspace" / "state.json").read_text(encoding="utf-8"))
            self.assertNotEqual(state["status"], "running", state)


class ConformanceTests(unittest.TestCase):
    def test_reference_runner_passes_conformance_suite(self) -> None:
        result = run_cmd(
            [
                sys.executable,
                str(ROOT / "conformance" / "check_runner.py"),
                str(RUNNER_TEMPLATE),
            ],
            ROOT,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("FAIL", result.stdout)


if __name__ == "__main__":
    unittest.main()
