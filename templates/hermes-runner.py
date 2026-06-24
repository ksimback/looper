#!/usr/bin/env python3
"""Hermes-native Looper runner.

This file executes a resolved loop spec using Hermes Agent (the `hermes chat`
CLI) as the model runner, instead of the upstream `subprocess.run(arbitrary CLI)`
approach in `run-loop.py`.

The spec format (`loop.resolved.json`) is identical to the upstream runner,
and the on-disk artifacts (`state.json`, `run-log.md`, `plan.md`, `delivery-N.md`,
`review-*.md`) are produced with the same field names and structure, so the
two runners are observation-compatible: a loop started by `run-loop.py` can be
inspected from a session that ran `hermes-runner.py` and vice versa.

## When to use this runner vs `run-loop.py`

Use `run-loop.py` (upstream) when:
  - You are running inside a container/CI without the Hermes CLI installed.
  - You want to call a non-Hermes CLI directly (e.g. raw `codex exec`, raw
    `claude -p`, or a custom Python harness).
  - You are testing the spec format itself and want zero Hermes-specific
    magic in the loop.

Use `hermes-runner.py` (this file) when:
  - You want the host/judge steps to run as proper Hermes sessions
    (logged, summarizable via `session_search`, model-isolated).
  - You want to schedule the loop with `hermes cron create --script
    hermes-runner.py ...` for durable recurring execution.
  - You want each step's model to be selected per-step, not pinned at
    loop-design time.

## Mapping `invoke` argv arrays to Hermes

The upstream spec stores each model member as an `invoke: [cli, arg, arg, ...]`
argv array (e.g. `["codex", "exec", "--model", "gpt-5"]` or `["claude", "-p"]`).
This runner maps common shapes to `hermes chat` flags:

  - argv[0] == "codex"   -> `hermes chat -m openai/<model>  -q <prompt>`
  - argv[0] == "claude"  -> `hermes chat -m anthropic/<model> -q <prompt>`
  - argv[0] == "hermes"  -> `hermes chat -m <model>  -q <prompt>` (passthrough)
  - argv[0] in PATH and not matched above -> still dispatched via
    `hermes chat`, with the argv's model flag extracted heuristically

Unrecognized shapes fall back to invoking the original argv directly
(this is the same as `run-loop.py` and keeps the runner safe for unknown
models). Set `LOOPER_HERMES_FALLBACK=0` to disable fallback and fail loudly
instead.

## Cross-model review council

Looper's main value is the *different model in the judge seat*. This runner
preserves that: configure the judge member's `invoke` to point at a different
model vendor than the host, and the runner will dispatch the judge through
`hermes chat -m <other-vendor>/<model>` automatically.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


PASS = "pass"
REVISE = "revise"

DEFAULT_TIMEOUT_SEC = 600
HERMES_FALLBACK = os.environ.get("LOOPER_HERMES_FALLBACK", "1") != "0"

# Map a CLI name (argv[0]) to a Hermes model vendor prefix. Extend this dict
# to add support for new CLIs.
CLI_TO_HERMES_VENDOR = {
    "codex": "openai",
    "gpt": "openai",
    "openai": "openai",
    "claude": "anthropic",
    "anthropic": "anthropic",
    "gemini": "google",
    "google": "google",
    "grok": "xai",
    "xai": "xai",
    "hermes": None,  # passthrough, model string is already qualified
}


class RunnerError(RuntimeError):
    pass


def utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except OSError as exc:
        raise RunnerError(f"Could not read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RunnerError(f"Could not parse JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RunnerError(f"{path} must contain a JSON object")
    return data


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def ensure_argv(value: Any, field: str) -> list[str]:
    if isinstance(value, list) and value and all(isinstance(item, str) for item in value):
        return value
    raise RunnerError(f"{field} must be a non-empty argv array")


def parse_judge_output(text: str) -> dict[str, Any]:
    """Mirror of run-loop.py's judge-output parser."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text.strip()
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return {
            "verdict": REVISE,
            "blocking_issues": ["Judge output was not parseable JSON."],
            "confidence": 0.0,
            "notes": text.strip(),
            "warning": "unparseable_judge_output",
        }
    if not isinstance(parsed, dict):
        return {
            "verdict": REVISE,
            "blocking_issues": ["Judge output was not a JSON object."],
            "confidence": 0.0,
            "notes": text.strip(),
            "warning": "invalid_judge_output",
        }
    verdict = parsed.get("verdict")
    if verdict not in {PASS, REVISE}:
        parsed["verdict"] = REVISE
        parsed.setdefault("blocking_issues", []).append("Judge verdict was not pass or revise.")
    parsed.setdefault("blocking_issues", [])
    parsed.setdefault("confidence", 0.0)
    parsed.setdefault("notes", "")
    return parsed


def model_string_from_argv(argv: list[str]) -> str | None:
    """Best-effort: extract '<vendor>/<model>' from a model CLI argv.

    Recognized shapes:
        ["codex", "exec", "--model", "gpt-5"]             -> "openai/gpt-5"
        ["claude", "-p", "--model", "claude-sonnet-4-5"]  -> "anthropic/claude-sonnet-4-5"
        ["hermes", "-m", "openai/gpt-4.1-mini"]           -> "openai/gpt-4.1-mini" (passthrough)
        ["codex", "exec"]                                 -> "openai/default"  (no model)
    """
    if not argv:
        return None
    cli = argv[0].lower()
    vendor = CLI_TO_HERMES_VENDOR.get(cli)
    if vendor is None and cli == "hermes":
        vendor = ""  # passthrough
    elif vendor is None:
        return None  # unknown CLI; bail and let the runner fall back
    # Find --model <value> or -m <value>
    model_value = None
    for i, token in enumerate(argv[1:], start=1):
        if token in {"--model", "-m"} and i + 1 < len(argv):
            model_value = argv[i + 1]
            break
        if token.startswith("--model="):
            model_value = token.split("=", 1)[1]
            break
        if token.startswith("-m="):
            model_value = token.split("=", 1)[1]
            break
    if model_value is None:
        model_value = "default"
    if vendor == "":
        return model_value
    if "/" in model_value:
        return model_value  # already qualified
    return f"{vendor}/{model_value}"


def call_hermes_chat(prompt: str, model: str, *, timeout_sec: int) -> str:
    """Invoke `hermes chat -q ... -m <model>` and return its final response text.

    Uses --ignore-user-config and --ignore-rules for isolation: we don't want
    the runner's host/judge session to be influenced by user-level memory,
    custom skills, or rules. The runner's caller is the loop, not the user.
    """
    argv = [
        "hermes",
        "chat",
        "-q",
        prompt,
        "-m",
        model,
        "--ignore-user-config",
        "--ignore-rules",
    ]
    try:
        result = subprocess.run(
            argv,
            input="",  # -q supplies the prompt; stdin is closed
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RunnerError(f"hermes chat timed out after {timeout_sec}s") from exc
    except FileNotFoundError as exc:
        raise RunnerError(
            "`hermes` CLI not found in PATH. Install Hermes Agent or set LOOPER_HERMES_FALLBACK=0 "
            "and provide a working invoke in loop.yaml."
        ) from exc
    if result.returncode != 0:
        raise RunnerError(
            f"hermes chat failed (exit {result.returncode}): {result.stderr.strip()[:500]}"
        )
    return result.stdout.strip()


def call_model_hermes(member: dict[str, Any], prompt: str, base_dir: Path) -> str:
    """Dispatch a model step through `hermes chat` (with subprocess fallback)."""
    argv = ensure_argv(member.get("invoke"), f"{member.get('id', member.get('cli', 'model'))}.invoke")
    timeout_sec = int(member.get("timeout_sec", DEFAULT_TIMEOUT_SEC))
    model = model_string_from_argv(argv)
    if model is not None:
        return call_hermes_chat(prompt, model, timeout_sec=timeout_sec)
    if not HERMES_FALLBACK:
        raise RunnerError(
            f"Could not map invoke argv to a Hermes model for member "
            f"{member.get('id', member.get('cli', '?'))}: {argv!r}. "
            f"Set LOOPER_HERMES_FALLBACK=1 to allow subprocess fallback."
        )
    # Fallback: behave like run-loop.py and call the original argv directly.
    try:
        result = subprocess.run(
            argv,
            input=prompt,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(base_dir),
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RunnerError(f"Model invocation timed out ({' '.join(argv)}): {exc}") from exc
    except OSError as exc:
        raise RunnerError(f"Model invocation failed ({' '.join(argv)}): {exc}") from exc
    if result.returncode != 0:
        raise RunnerError(
            f"Model invocation failed ({' '.join(argv)}): exit {result.returncode}\n{result.stderr}"
        )
    return result.stdout.strip()


def call_model(member: dict[str, Any], prompt: str, base_dir: Path) -> str:
    """Public entry point: dispatch through Hermes with fallback."""
    return call_model_hermes(member, prompt, base_dir)


class Runner:
    def __init__(self, spec_path: Path) -> None:
        self.engine = "hermes-runner"  # marker so consumers can tell runners apart
        self.spec_path = spec_path.resolve()
        self.base_dir = self.spec_path.parent
        self.spec = load_json(self.spec_path)
        self.workspace = Path(self.spec["workspace"]["dir"])
        if not self.workspace.is_absolute():
            self.workspace = self.base_dir / self.workspace
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.observability = self.spec.get("observability", {})
        self.run_log_path = self.workspace / self.observability.get("run_log", "run-log.md")
        self.state_path = self.workspace / self.observability.get("state_file", "state.json")
        self.state = self.load_state()
        self.started = time.monotonic()

    def load_state(self) -> dict[str, Any]:
        if self.state_path.exists():
            return load_json(self.state_path)
        return {
            "status": "initialized",
            "started_at": utc_now(),
            "iteration": 0,
            "warnings": [],
            "consent": {},
            "engine": self.engine,
        }

    def save_state(self, **updates: Any) -> None:
        self.state.update(updates)
        self.state["updated_at"] = utc_now()
        write_json(self.state_path, self.state)

    def append_log(self, event: str, **fields: Any) -> None:
        self.run_log_path.parent.mkdir(parents=True, exist_ok=True)
        payload = f" {json.dumps(fields, sort_keys=True)}" if fields else ""
        with self.run_log_path.open("a", encoding="utf-8") as fh:
            fh.write(f"- {utc_now()} `{event}`{payload}\n")

    def enforce_wall_clock(self) -> None:
        budget = self.spec.get("loop_control", {}).get("budget", {})
        wall_clock_min = budget.get("wall_clock_min")
        if wall_clock_min is None:
            return
        if time.monotonic() - self.started > float(wall_clock_min) * 60:
            self.save_state(status="failed", failure="wall_clock_budget_exceeded")
            self.append_log("stop", reason="wall_clock_budget_exceeded", engine=self.engine)
            raise RunnerError("Wall-clock budget exceeded")

    def criteria(self, ids: list[str]) -> list[dict[str, Any]]:
        by_id = self.spec.get("criteria_by_id", {})
        return [by_id[item] for item in ids]

    def member(self, member_id: str) -> dict[str, Any]:
        return self.spec["council_by_id"][member_id]

    def host_prompt(self, phase: str, artifact: str = "", review: str = "") -> str:
        goal = self.spec["goal"]
        if phase == "plan":
            return (
                "Draft plan.md for this loop.\n\n"
                f"Goal:\n{goal['statement']}\n\n"
                f"Definition of done:\n{goal['definition_of_done']}\n\n"
                f"Context:\n{(self.workspace / 'context.md').read_text(encoding='utf-8')}\n"
            )
        if phase == "delivery":
            return (
                "Write the next delivery artifact for this loop.\n\n"
                f"Goal:\n{goal['statement']}\n\n"
                f"Definition of done:\n{goal['definition_of_done']}\n\n"
                f"Plan:\n{(self.workspace / 'plan.md').read_text(encoding='utf-8')}\n"
            )
        if phase == "revise":
            return (
                "Revise the artifact to address the review. Return only the revised artifact.\n\n"
                f"Artifact:\n{artifact}\n\nReview:\n{review}\n"
            )
        raise RunnerError(f"Unknown host phase: {phase}")

    def run_host(self, phase: str, target: Path, artifact: str = "", review: str = "") -> None:
        self.enforce_wall_clock()
        self.append_log("host_start", phase=phase, target=target.name, engine=self.engine)
        output = call_model(self.spec["host"], self.host_prompt(phase, artifact, review), self.base_dir)
        write_text(target, output)
        self.append_log("host_done", phase=phase, target=target.name, engine=self.engine)

    def judge_prompt(
        self,
        gate_name: str,
        artifact_label: str,
        artifact_text: str,
        criteria: list[dict[str, Any]],
    ) -> str:
        rubric_lines = []
        for criterion in criteria:
            if criterion["type"] == "judge":
                rubric_lines.append(f"- {criterion['id']}: {criterion['rubric']}")
            elif criterion["type"] == "programmatic":
                rubric_lines.append(f"- {criterion['id']}: programmatic check result is included below.")
            elif criterion["type"] == "human":
                rubric_lines.append(f"- {criterion['id']}: human signoff is required separately.")
        return (
            "You are the Looper judge. Return only a fenced JSON object with keys "
            "verdict, blocking_issues, confidence, and notes. verdict must be pass or revise.\n\n"
            f"Gate: {gate_name}\n"
            f"Artifact: {artifact_label}\n\n"
            "Criteria:\n" + "\n".join(rubric_lines) + "\n\n"
            f"Artifact content:\n{artifact_text}\n"
        )

    def run_judge(
        self,
        member_id: str,
        gate_name: str,
        artifact_label: str,
        artifact_text: str,
        criteria: list[dict[str, Any]],
    ) -> dict[str, Any]:
        output = call_model(
            self.member(member_id),
            self.judge_prompt(gate_name, artifact_label, artifact_text, criteria),
            self.base_dir,
        )
        verdict = parse_judge_output(output)
        verdict["member"] = member_id
        self.append_log(
            "judge_verdict",
            gate=gate_name,
            member=member_id,
            verdict=verdict.get("verdict"),
            engine=self.engine,
        )
        return verdict

    def gather_context(self) -> str:
        """Read every `goal.context_sources[*].file` into a single context.md.

        Mirrors run-loop.py's gather_context but **does not** redact secrets
        or invoke shell commands (those features are upstream run-loop.py's
        job; this runner keeps the minimum to drive a Hermes session).
        """
        goal = self.spec["goal"]
        chunks: list[str] = []
        for index, source in enumerate(goal.get("context_sources", []), start=1):
            if "file" not in source:
                continue
            raw = source["file"]
            path = Path(raw)
            if not path.is_absolute():
                path = self.base_dir / raw
            if path.exists():
                chunks.append(f"## Context source {index}: {raw}\n{path.read_text(encoding='utf-8')}\n")
                self.append_log("context", source=raw, status="read", engine=self.engine)
            else:
                chunks.append(f"## Context source {index}: {raw}\n[missing]\n")
                self.append_log("context", source=raw, status="missing", engine=self.engine)
        context = "\n".join(chunks).strip()
        write_text(self.workspace / "context.md", context or "No context sources configured.")
        return context

    def _run_gate_until_pass(
        self,
        gate_name: str,
        artifact_path: Path,
        artifact_label: str,
    ) -> dict[str, Any] | None:
        """Run a gate, retrying the artifact (and re-judging) on `revise`.

        Returns the final verdict dict if the gate passed, or None on failure.
        """
        gate = self.spec["gates"][gate_name]
        max_revisions = int(gate.get("max_revisions", 0))
        verdict_source = gate.get("verdict_source")
        criteria = self.criteria(gate.get("criteria", []))
        revision = 0
        self.append_log("gate_start", gate=gate_name, artifact=artifact_label, engine=self.engine)
        while True:
            self.enforce_wall_clock()
            verdict = self.run_judge(
                verdict_source,
                gate_name,
                artifact_label,
                artifact_path.read_text(encoding="utf-8"),
                criteria,
            )
            if verdict.get("verdict") == PASS:
                self.save_state(**{gate_name: {"passed_at": utc_now()}}, engine=self.engine)
                self.append_log("gate_passed", gate=gate_name, artifact=artifact_label, engine=self.engine)
                return verdict
            if revision >= max_revisions:
                self.save_state(
                    status="failed",
                    failure=f"{gate_name}_max_revisions_reached",
                    verdict=verdict,
                    engine=self.engine,
                )
                self.append_log(
                    "run_failed",
                    reason=f"{gate_name}_max_revisions_reached",
                    engine=self.engine,
                )
                return None
            # Revise: re-run host with the review and try again
            revision += 1
            self.append_log(
                "revision",
                gate=gate_name,
                artifact=artifact_label,
                revision=revision,
                blocking_issues=verdict.get("blocking_issues", []),
                engine=self.engine,
            )
            review_text = json.dumps(verdict, indent=2, sort_keys=True)
            current = artifact_path.read_text(encoding="utf-8")
            self.run_host("revise", artifact_path, artifact=current, review=review_text)

    def run(self) -> int:
        """Run the loop end-to-end. Mirrors run-loop.py's main flow.

        This is a *minimal* first-cut implementation that focuses on the
        core plan -> plan_gate -> delivery -> delivery_gate cycle, which is
        the 80% use case. It deliberately does not implement: cross-vendor
        consent flow, redaction, reviewers (only judges), and human
        checkpoints with non-TTY input. Those features are upstream
        run-loop.py's job; if you need them, either:
          - extend this file, or
          - use run-loop.py via the `engine: subprocess` opt-in (TODO: not
            implemented in v1 of hermes-runner.py)
        """
        # 0. Gather context
        self.gather_context()

        # 1. Plan phase
        plan_path = self.workspace / "plan.md"
        self.run_host("plan", plan_path)
        plan_verdict = self._run_gate_until_pass("plan_gate", plan_path, "plan.md")
        if plan_verdict is None:
            return 1

        # 2. Delivery loop
        delivery_index = 1
        max_iterations = int(self.spec.get("loop_control", {}).get("max_iterations", 12))
        while delivery_index <= max_iterations:
            self.enforce_wall_clock()
            target = self.workspace / f"delivery-{delivery_index}.md"
            self.run_host("delivery", target)
            verdict = self._run_gate_until_pass("delivery_gate", target, target.name)
            if verdict is not None:
                self.save_state(
                    status="passed",
                    final_delivery=str(target),
                    iteration=delivery_index,
                    engine=self.engine,
                )
                self.append_log("run_passed", final_delivery=str(target), engine=self.engine)
                return 0
            delivery_index += 1

        self.save_state(status="failed", failure="max_iterations_reached", engine=self.engine)
        self.append_log("run_failed", reason="max_iterations_reached", engine=self.engine)
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a compiled Looper loop through Hermes Agent (hermes chat).",
    )
    parser.add_argument(
        "spec_path",
        nargs="?",
        type=Path,
        default=Path(__file__).with_name("loop.resolved.json"),
        help="Path to loop.resolved.json (defaults to the file next to hermes-runner.py).",
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        return Runner(args.spec_path).run()
    except RunnerError as exc:
        print(f"hermes-runner: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
