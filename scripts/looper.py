#!/usr/bin/env python3
"""Looper helper CLI.

This script belongs to the scaffolding side of Looper. It may detect installed
CLIs, register invocation metadata, compile loop.yaml to loop.resolved.json, and
render LOOP.md. It must not invoke model CLIs to do loop work.
"""

from __future__ import annotations

import argparse
import copy
import datetime as _dt
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
from typing import Any


DEFAULT_REDACTIONS = [".env", ".env.*", "secrets/**", "**/*.key"]
REGISTRY_PATH = Path.home() / ".looper" / "models.json"

# The registry stores invocation metadata only, never credentials. Refuse
# values that look like secrets so a stray key can't land on disk.
SECRET_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}"),
    re.compile(r"\bxox[a-z]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\b(api[-_]?key|token|secret|password)\s*[=:]\s*\S{8,}"),
]


# Loop templates under templates/loops/ mark project-specific slots with
# {{TOKEN}} placeholders. They compile as ordinary strings; the warning below
# keeps a half-customized template from being run by accident.
PLACEHOLDER_PATTERN = re.compile(r"\{\{[A-Za-z0-9_]+\}\}")


def find_placeholders(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, str):
        found.update(PLACEHOLDER_PATTERN.findall(value))
    elif isinstance(value, list):
        for item in value:
            found.update(find_placeholders(item))
    elif isinstance(value, dict):
        for key, item in value.items():
            found.update(find_placeholders(key))
            found.update(find_placeholders(item))
    return found


def reject_secret_material(values: list[str], context: str) -> None:
    for value in values:
        for pattern in SECRET_PATTERNS:
            if pattern.search(value):
                raise LooperError(
                    f"{context} looks like it contains a credential; the model registry "
                    "must never store secrets. Configure auth in the CLI's own config or "
                    "keychain instead."
                )

MODEL_PROBES: dict[str, dict[str, Any]] = {
    "claude": {
        "invoke": ["claude", "-p"],
        "probe": ["claude", "--version"],
        "local": False,
        "install": "Install and authenticate the Claude CLI.",
    },
    "codex": {
        "invoke": ["codex", "exec"],
        "probe": ["codex", "--version"],
        "local": False,
        "install": "Install and authenticate the Codex CLI.",
    },
    "gemini": {
        "invoke": ["gemini", "-p"],
        "probe": ["gemini", "--version"],
        "local": False,
        "install": "Install and authenticate the Gemini CLI.",
    },
    "llm": {
        "invoke": ["llm"],
        "probe": ["llm", "--version"],
        "local": False,
        "install": "Install llm and configure a model/provider.",
    },
    "ollama": {
        "invoke": ["ollama", "run"],
        "probe": ["ollama", "--version"],
        "local": True,
        "install": "Install Ollama and pull a local model.",
    },
}


class LooperError(RuntimeError):
    pass


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore
    except ImportError as exc:
        raise LooperError(
            "PyYAML is required to compile loop.yaml. Install with: python -m pip install PyYAML"
        ) from exc

    try:
        with path.open("r", encoding="utf-8-sig") as fh:
            data = yaml.safe_load(fh)
    except OSError as exc:
        raise LooperError(f"Could not read {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise LooperError(f"Could not parse YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise LooperError(f"{path} must contain a YAML mapping at the top level")
    return data


def load_json(path: Path) -> dict[str, Any]:
    try:
        # utf-8-sig tolerates a BOM left by Windows editors.
        with path.open("r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except OSError as exc:
        raise LooperError(f"Could not read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise LooperError(f"Could not parse JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise LooperError(f"{path} must contain a JSON object")
    return data


def write_text_lf(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Explicit LF keeps generated artifacts byte-identical across platforms.
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def write_json(path: Path, data: Any) -> None:
    write_text_lf(path, json.dumps(to_jsonable(data), indent=2, sort_keys=True) + "\n")


def to_jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    if isinstance(value, (_dt.date, _dt.datetime)):
        return value.isoformat()
    return value


def read_registry(path: Path = REGISTRY_PATH) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise LooperError(f"Registry {path} must contain a JSON object")
    return data


def write_registry(data: dict[str, Any], path: Path = REGISTRY_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, data)


def run_probe(argv: list[str], timeout_sec: int = 5) -> tuple[bool, str]:
    probe_argv = list(argv)
    if os.name == "nt":
        resolved = shutil.which(argv[0])
        if resolved and Path(resolved).suffix.lower() in {".cmd", ".bat"}:
            probe_argv = ["cmd", "/d", "/c", *argv]
    try:
        completed = subprocess.run(
            probe_argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)

    output = (completed.stdout or completed.stderr or "").strip()
    return completed.returncode == 0, output.splitlines()[0] if output else ""


def detect_models() -> dict[str, Any]:
    registry: dict[str, Any] = {}
    for model_id, meta in MODEL_PROBES.items():
        cli = meta["invoke"][0]
        path = shutil.which(cli)
        available = path is not None
        authed = False
        version = ""
        if available:
            authed, version = run_probe(meta["probe"])
        registry[model_id] = {
            "cli": cli,
            "path": path,
            "invoke": meta["invoke"],
            "available": available,
            "authed": authed,
            "local": meta["local"],
            "probe": meta["probe"],
            "version": version,
            "install": meta["install"],
        }
    return registry


def normalize_argv(value: Any, field: str) -> list[str]:
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        argv = value
    elif isinstance(value, str):
        argv = shlex.split(value, posix=os.name != "nt")
    else:
        raise LooperError(f"{field} must be an argv array or string")
    if not argv or not argv[0].strip():
        raise LooperError(f"{field} must not be empty")
    return argv


def validate_timeout(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise LooperError(f"{field} must be a positive integer")
    return value


def validate_relative_path(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LooperError(f"{field} must be a non-empty string")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise LooperError(
            f"{field} must be a relative path inside the loop directory (got {value!r})"
        )
    return value


def criteria_by_id(spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    criteria = spec.get("goal", {}).get("verification") or []
    if not isinstance(criteria, list):
        raise LooperError("goal.verification must be a list")
    result: dict[str, dict[str, Any]] = {}
    for item in criteria:
        if not isinstance(item, dict):
            raise LooperError("Each verification criterion must be an object")
        cid = item.get("id")
        ctype = item.get("type")
        if not isinstance(cid, str) or not cid:
            raise LooperError("Each verification criterion needs a non-empty id")
        if cid in result:
            raise LooperError(f"Duplicate verification criterion id: {cid}")
        if ctype not in {"programmatic", "judge", "human"}:
            raise LooperError(f"Criterion {cid} has invalid type: {ctype}")
        if ctype == "programmatic":
            item["check"] = normalize_argv(item.get("check"), f"criterion {cid}.check")
            item["timeout_sec"] = validate_timeout(
                item.setdefault("timeout_sec", 300), f"criterion {cid}.timeout_sec"
            )
            if item.get("expect") not in {"exit_zero", "exit_nonzero", "stdout_contains"}:
                raise LooperError(
                    f"Criterion {cid}.expect must be exit_zero, exit_nonzero, or stdout_contains"
                )
            if item.get("expect") == "stdout_contains" and not isinstance(item.get("contains"), str):
                raise LooperError(f"Criterion {cid} with stdout_contains needs contains")
        elif ctype == "judge" and not isinstance(item.get("rubric"), str):
            raise LooperError(f"Criterion {cid} needs a judge rubric")
        elif ctype == "human" and not isinstance(item.get("prompt"), str):
            raise LooperError(f"Criterion {cid} needs a human prompt")
        result[cid] = item
    return result


def validate_member(member: dict[str, Any]) -> None:
    mid = member.get("id")
    role = member.get("role")
    if not isinstance(mid, str) or not mid:
        raise LooperError("Each council member needs a non-empty id")
    if role not in {"reviewer", "judge"}:
        raise LooperError(f"Council member {mid} role must be reviewer or judge")
    if not isinstance(member.get("cli"), str) or not member["cli"].strip():
        raise LooperError(f"Council member {mid} needs a cli name")
    member["invoke"] = normalize_argv(member.get("invoke"), f"council.{mid}.invoke")
    member["timeout_sec"] = validate_timeout(
        member.setdefault("timeout_sec", 600), f"council.{mid}.timeout_sec"
    )
    member.setdefault("scope", ["plan", "delivery"])
    member.setdefault("local", member.get("cli") == "ollama")


def validate_gate(
    name: str,
    gate: dict[str, Any],
    criteria: dict[str, dict[str, Any]],
    members: dict[str, dict[str, Any]],
) -> None:
    if not isinstance(gate, dict):
        raise LooperError(f"{name} must be an object")
    policy = gate.get("verdict_policy")
    if policy not in {"revise_until_clean", "fixed_passes"}:
        raise LooperError(f"{name}.verdict_policy must be revise_until_clean or fixed_passes")
    max_revisions = gate.get("max_revisions", 1)
    if not isinstance(max_revisions, int) or max_revisions < 0:
        raise LooperError(f"{name}.max_revisions must be a non-negative integer")
    gate_criteria = gate.get("criteria") or []
    if not isinstance(gate_criteria, list):
        raise LooperError(f"{name}.criteria must be a list")
    gate["criteria"] = gate_criteria
    for cid in gate_criteria:
        if cid not in criteria:
            raise LooperError(f"{name} references unknown criterion: {cid}")
    gate_members = gate.get("members") or []
    if not isinstance(gate_members, list):
        raise LooperError(f"{name}.members must be a list")
    gate["members"] = gate_members
    for mid in gate_members:
        if mid not in members:
            raise LooperError(f"{name} references unknown council member: {mid}")
    if policy == "revise_until_clean":
        source = gate.get("verdict_source")
        if source == "human":
            return
        if source not in members:
            raise LooperError(f"{name}.verdict_source must be a judge member or human")
        if members[source].get("role") != "judge":
            raise LooperError(f"{name}.verdict_source must name a judge, not a reviewer")
        if source not in gate_members:
            raise LooperError(
                f"{name}.verdict_source {source!r} must also be listed in {name}.members"
            )


def normalize_spec(spec: dict[str, Any], source_path: Path) -> dict[str, Any]:
    if spec.get("version") != 1:
        raise LooperError("Only loop.yaml version: 1 is supported")

    goal = spec.get("goal")
    if not isinstance(goal, dict):
        raise LooperError("goal must be an object")
    if not isinstance(goal.get("statement"), str) or not goal["statement"].strip():
        raise LooperError("goal.statement is required")
    if not isinstance(goal.get("definition_of_done"), str) or not goal["definition_of_done"].strip():
        raise LooperError("goal.definition_of_done is required")

    context_sources = goal.get("context_sources") or []
    if not isinstance(context_sources, list):
        raise LooperError("goal.context_sources must be a list")
    goal["context_sources"] = context_sources
    for index, source in enumerate(context_sources):
        if not isinstance(source, dict):
            raise LooperError("goal.context_sources entries must be objects")
        has_file = "file" in source
        has_cmd = "cmd" in source
        if has_file == has_cmd:
            raise LooperError(
                f"context_sources[{index}] must have exactly one of file or cmd"
            )
        if has_file:
            validate_relative_path(source["file"], f"context_sources[{index}].file")
        if has_cmd:
            source["cmd"] = normalize_argv(source["cmd"], f"context_sources[{index}].cmd")
            source["timeout_sec"] = validate_timeout(
                source.setdefault("timeout_sec", 60), f"context_sources[{index}].timeout_sec"
            )

    criteria = criteria_by_id(spec)

    host = spec.get("host")
    if not isinstance(host, dict):
        raise LooperError("host must be an object")
    if not isinstance(host.get("cli"), str) or not host["cli"].strip():
        raise LooperError("host.cli is required")
    host["invoke"] = normalize_argv(host.get("invoke"), "host.invoke")
    host["timeout_sec"] = validate_timeout(host.setdefault("timeout_sec", 600), "host.timeout_sec")

    council_list = spec.get("council") or []
    if not isinstance(council_list, list):
        raise LooperError("council must be a list")
    members: dict[str, dict[str, Any]] = {}
    for member in council_list:
        if not isinstance(member, dict):
            raise LooperError("council entries must be objects")
        validate_member(member)
        if member["id"] in members:
            raise LooperError(f"Duplicate council member id: {member['id']}")
        members[member["id"]] = member

    gates = spec.get("gates")
    if not isinstance(gates, dict):
        raise LooperError("gates must be an object")
    for gate_name in ("plan_gate", "delivery_gate"):
        validate_gate(gate_name, gates.get(gate_name), criteria, members)

    control = spec.get("loop_control")
    if not isinstance(control, dict):
        raise LooperError("loop_control must be an object")
    max_iterations = control.get("max_iterations")
    if not isinstance(max_iterations, int) or max_iterations <= 0:
        raise LooperError("loop_control.max_iterations must be a positive integer")
    budget = control.setdefault("budget", {})
    if not isinstance(budget, dict):
        raise LooperError("loop_control.budget must be an object")
    if "wall_clock_min" not in budget:
        budget["wall_clock_min"] = 30
    for key in ("usd", "tokens", "wall_clock_min"):
        if key in budget and budget[key] is not None:
            value = budget[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                raise LooperError(f"loop_control.budget.{key} must be a positive number")
    no_progress = control.setdefault(
        "no_progress",
        {
            "max_stalled_iterations": 2,
            "signals": [
                "same blocking issue repeats",
                "delivery artifact has no material change",
                "verifier output is unchanged",
            ],
            "action": "stop",
        },
    )
    if not isinstance(no_progress, dict):
        raise LooperError("loop_control.no_progress must be an object")
    stalled = no_progress.setdefault("max_stalled_iterations", 2)
    if not isinstance(stalled, int) or stalled <= 0:
        raise LooperError("loop_control.no_progress.max_stalled_iterations must be a positive integer")
    signals = no_progress.setdefault("signals", ["same blocking issue repeats"])
    if not isinstance(signals, list) or not all(isinstance(item, str) for item in signals):
        raise LooperError("loop_control.no_progress.signals must be a list of strings")
    action = no_progress.setdefault("action", "stop")
    if action not in {"stop", "human_checkpoint"}:
        raise LooperError("loop_control.no_progress.action must be stop or human_checkpoint")

    execution = spec.setdefault(
        "execution",
        {
            "mode": "in_session",
            "isolation": "current_workspace",
            "side_effects": {"requires_approval": True, "duplicate_action_check": True},
        },
    )
    if not isinstance(execution, dict):
        raise LooperError("execution must be an object")
    execution.setdefault("mode", "in_session")
    execution.setdefault("isolation", "current_workspace")
    if execution["mode"] not in {"in_session", "external_runner", "orchestrated"}:
        raise LooperError("execution.mode must be in_session, external_runner, or orchestrated")
    if execution["isolation"] not in {"current_workspace", "branch", "worktree", "sandbox"}:
        raise LooperError("execution.isolation must be current_workspace, branch, worktree, or sandbox")
    side_effects = execution.setdefault("side_effects", {})
    if not isinstance(side_effects, dict):
        raise LooperError("execution.side_effects must be an object")
    side_effects.setdefault("requires_approval", True)
    side_effects.setdefault("duplicate_action_check", True)

    observability = spec.setdefault(
        "observability",
        {"state_file": "state.json", "run_log": "run-log.md", "checkpoint_granularity": "gate"},
    )
    if not isinstance(observability, dict):
        raise LooperError("observability must be an object")
    observability.setdefault("state_file", "state.json")
    observability.setdefault("run_log", "run-log.md")
    observability.setdefault("checkpoint_granularity", "gate")
    if not isinstance(observability["state_file"], str) or not observability["state_file"]:
        raise LooperError("observability.state_file must be a non-empty string")
    if not isinstance(observability["run_log"], str) or not observability["run_log"]:
        raise LooperError("observability.run_log must be a non-empty string")
    if observability["checkpoint_granularity"] not in {"gate", "step"}:
        raise LooperError("observability.checkpoint_granularity must be gate or step")

    workspace = spec.setdefault("workspace", {})
    if not isinstance(workspace, dict):
        raise LooperError("workspace must be an object")
    workspace.setdefault("dir", "./loop-workspace")
    validate_relative_path(workspace["dir"], "workspace.dir")
    layout = workspace.setdefault("layout", ["plan.md", "delivery-{n}.md", "review-{n}.md", "state.json", "run-log.md"])
    if not isinstance(layout, list) or not all(isinstance(item, str) for item in layout):
        raise LooperError("workspace.layout must be a list of strings")
    for required_file in (observability["state_file"], observability["run_log"]):
        if required_file not in layout:
            layout.append(required_file)

    privacy = spec.setdefault("privacy", {})
    if not isinstance(privacy, dict):
        raise LooperError("privacy must be an object")
    egress = privacy.setdefault("egress", [])
    if not isinstance(egress, list):
        raise LooperError("privacy.egress must be a list")
    for entry in egress:
        if not isinstance(entry, dict):
            raise LooperError("privacy.egress entries must be objects")
        entry.setdefault("redact", DEFAULT_REDACTIONS)
        entry.setdefault("consent", "required")

    resolved = {
        "$schema": "https://github.com/ksimback/looper/schema/loop.resolved.v1.json",
        "compiled_at": _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat(),
        "source": str(source_path),
        **spec,
        "criteria_by_id": criteria,
        "council_by_id": members,
    }
    return to_jsonable(resolved)


# ---------------------------------------------------------------------------
# Lint: the design rubrics as a static checker.
#
# Severity contract:
#   error   - the spec will not behave the way it reads at runtime.
#   warning - rubric coaching; the loop runs, but the design has a known trap.
# Compile validation always runs first, so anything normalize_spec rejects
# (reviewer verdict_source, missing caps enforced there, bad paths) surfaces
# as `looper: error:` before lint findings are evaluated.

LINT_ERROR = "error"
LINT_WARNING = "warning"


def lint_spec(raw_spec: dict[str, Any], resolved: dict[str, Any]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []

    def add(check: str, severity: str, message: str) -> None:
        findings.append({"check": check, "severity": severity, "message": message})

    criteria = resolved.get("criteria_by_id", {})
    members = resolved.get("council_by_id", {})
    host_cli = resolved.get("host", {}).get("cli", "")
    gates = resolved.get("gates", {})
    control = resolved.get("loop_control", {})
    egress = resolved.get("privacy", {}).get("egress", [])
    egress_targets = {entry.get("to") for entry in egress if isinstance(entry, dict)}

    programmatic_ids = {
        cid for cid, item in criteria.items() if item.get("type") == "programmatic"
    }
    if not programmatic_ids:
        add(
            "all-vibe-verification",
            LINT_WARNING,
            "every verification criterion is judge or human ('all-vibe'); add at least "
            "one programmatic check so something objective can block the loop "
            "(verification rubric)",
        )

    raw_gates = raw_spec.get("gates") or {}
    for gate_name in ("plan_gate", "delivery_gate"):
        gate = gates.get(gate_name, {})
        policy = gate.get("verdict_policy")
        gate_criteria = gate.get("criteria", []) or []

        if policy == "fixed_passes":
            dead = [
                cid for cid in gate_criteria if criteria.get(cid, {}).get("type") == "judge"
            ]
            if dead:
                add(
                    "judge-criterion-unreachable",
                    LINT_ERROR,
                    f"{gate_name} uses fixed_passes but lists judge criteria "
                    f"({', '.join(dead)}); runners only consult a judge under "
                    "revise_until_clean, so these rubrics are never evaluated",
                )

        source = gate.get("verdict_source")
        if policy == "revise_until_clean" and source and source != "human":
            source_cli = members.get(source, {}).get("cli", "")
            if source_cli and source_cli == host_cli:
                add(
                    "same-family-judge",
                    LINT_WARNING,
                    f"{gate_name} verdict_source {source!r} runs on the same CLI family "
                    f"as the host ({host_cli}); a different model family closes "
                    "self-grading blind spots (council rubric)",
                )

        raw_gate = raw_gates.get(gate_name) or {}
        if isinstance(raw_gate, dict) and "max_revisions" not in raw_gate:
            add(
                "missing-max-revisions",
                LINT_WARNING,
                f"{gate_name} does not set max_revisions; make the revision cap "
                "explicit (control rubric requires one per gate)",
            )

    if programmatic_ids:
        delivery_criteria = gates.get("delivery_gate", {}).get("criteria", []) or []
        if not any(cid in programmatic_ids for cid in delivery_criteria):
            add(
                "delivery-gate-no-programmatic",
                LINT_WARNING,
                "delivery_gate applies no programmatic criteria even though some are "
                "defined; the shipped artifact is gated by judgment alone",
            )

    for member_id, member in members.items():
        if member.get("local"):
            continue
        if member_id in egress_targets:
            continue
        if member.get("cli") != host_cli:
            add(
                "unscoped-egress",
                LINT_ERROR,
                f"council member {member_id!r} is non-local and cross-vendor "
                f"({member.get('cli')}) but has no privacy.egress entry; declare what "
                "it receives, the redaction globs, and consent (privacy contract)",
            )
        else:
            add(
                "non-local-member-without-egress",
                LINT_WARNING,
                f"council member {member_id!r} is non-local but has no privacy.egress "
                "entry; project content leaves this machine undeclared - name the "
                "sends and redactions even for a same-vendor CLI",
            )

    for entry in egress:
        if not isinstance(entry, dict):
            continue
        target = entry.get("to")
        if target not in members:
            add(
                "egress-unknown-member",
                LINT_ERROR,
                f"privacy.egress entry targets unknown council member {target!r}; its "
                "redactions and consent will never apply to anyone",
            )
        if entry.get("consent") == "granted":
            add(
                "egress-consent-pregranted",
                LINT_WARNING,
                f"privacy.egress to {target!r} pre-grants consent; the runner will "
                "skip the first-send prompt for it",
            )

    has_cross_vendor = any(
        not member.get("local") and member.get("cli") != host_cli
        for member in members.values()
    )
    if has_cross_vendor and not (control.get("human_checkpoints") or []):
        add(
            "cross-vendor-send-without-checkpoint",
            LINT_WARNING,
            "a cross-vendor council member is configured but "
            "loop_control.human_checkpoints is empty; add a checkpoint before the "
            "first cross-vendor send (control rubric)",
        )

    budget = control.get("budget", {}) or {}
    if budget.get("wall_clock_min") is None:
        add(
            "no-wall-clock-cap",
            LINT_WARNING,
            "loop_control.budget.wall_clock_min is null; the runner will not enforce "
            "any wall-clock cap",
        )

    if not (control.get("stop_conditions") or []):
        add(
            "no-stop-conditions",
            LINT_WARNING,
            "loop_control.stop_conditions is empty; state the success stop and the "
            "failure/no-progress stops explicitly (control rubric)",
        )

    for item in (raw_spec.get("goal") or {}).get("verification") or []:
        if (
            isinstance(item, dict)
            and item.get("type") == "programmatic"
            and isinstance(item.get("check"), str)
        ):
            add(
                "shell-string-check",
                LINT_WARNING,
                f"criterion {item.get('id')!r} writes its check as a shell string; "
                "prefer an argv array (verification rubric) unless a placeholder must "
                "be shlex-split after substitution",
            )

    placeholders = sorted(find_placeholders(resolved))
    if placeholders:
        add(
            "unresolved-placeholders",
            LINT_WARNING,
            f"unresolved template placeholders remain ({', '.join(placeholders)}); "
            "fill them in before running this loop",
        )

    return findings


def clip(text: Any, width: int) -> str:
    value = str(text or "")
    return value if len(value) <= width else value[: width - 1] + "~"


def ascii_box(*rows: str, width: int = 30) -> list[str]:
    border = "+" + "-" * (width + 2) + "+"
    body = [f"| {clip(row, width):<{width}} |" for row in rows if row is not None]
    return [border, *body, border]


def render_ascii_diagram(resolved: dict[str, Any]) -> str:
    gates = resolved.get("gates", {})
    control = resolved.get("loop_control", {})
    observability = resolved.get("observability", {})
    plan_gate = gates.get("plan_gate", {})
    delivery_gate = gates.get("delivery_gate", {})
    plan_revisions = plan_gate.get("max_revisions", 0)
    delivery_revisions = delivery_gate.get("max_revisions", 0)
    plan_source = plan_gate.get("verdict_source", "human")
    delivery_source = delivery_gate.get("verdict_source", "human")
    no_progress = control.get("no_progress", {})
    stalled = no_progress.get("max_stalled_iterations", 2)
    budget = control.get("budget", {})
    budget_bits = []
    if budget.get("wall_clock_min") is not None:
        budget_bits.append(f"{budget.get('wall_clock_min')}m")
    if budget.get("usd") is not None:
        budget_bits.append(f"${budget.get('usd')}")
    if budget.get("tokens") is not None:
        budget_bits.append(f"{budget.get('tokens')} tokens")
    budget_text = ", ".join(budget_bits) or "configured caps"

    lines: list[str] = []
    lines.extend(ascii_box("1. Goal + context", "read sources"))
    lines.extend(["               |", "               v"])
    lines.extend(ascii_box("2. Draft plan.md", f"state -> {observability.get('state_file', 'state.json')}"))
    lines.extend(["               |", "               v"])
    lines.extend(ascii_box("3. Plan gate", f"verdict: {plan_source}"))
    lines.extend([f"               | needs work -> revise <= {plan_revisions} -> step 2", "               | pass", "               v"])
    lines.extend(ascii_box("4. Write delivery-N.md", f"log -> {observability.get('run_log', 'run-log.md')}"))
    lines.extend(["               |", "               v"])
    lines.extend(ascii_box("5. Delivery gate", f"verdict: {delivery_source}"))
    lines.extend([f"               | needs work -> revise <= {delivery_revisions} -> step 4", "               | pass", "               v"])
    lines.extend(ascii_box("6. Final output", "all gates clean"))
    lines.extend(
        [
            "",
            f"Stops: pass gates | max {control.get('max_iterations')} iterations | "
            f"no progress x{stalled} | budget {budget_text}",
        ]
    )
    return "\n".join(lines)


def render_loop(resolved: dict[str, Any]) -> str:
    meta = resolved.get("meta", {})
    goal = resolved.get("goal", {})
    gates = resolved.get("gates", {})
    control = resolved.get("loop_control", {})
    execution = resolved.get("execution", {})
    observability = resolved.get("observability", {})
    title = meta.get("name") or "Looper Generated Loop"
    criteria = goal.get("verification", [])
    council = resolved.get("council", [])

    lines = [
        f"# {title}",
        "",
        meta.get("description", "").strip(),
        "",
        "## Goal",
        "",
        goal.get("statement", "").strip(),
        "",
        "## Definition of Done",
        "",
        goal.get("definition_of_done", "").strip(),
        "",
        "## Verification",
        "",
    ]
    for item in criteria:
        lines.append(f"- `{item['id']}` ({item['type']})")
    lines.extend(["", "## Council", ""])
    if council:
        for member in council:
            lines.append(
                f"- `{member['id']}`: {member.get('role')} via {member.get('cli')} "
                f"({member.get('model', 'default')})"
            )
    else:
        lines.append("- No council members configured.")
    lines.extend(
        [
            "",
            "## Gates",
            "",
            f"- Plan gate: {gates.get('plan_gate', {}).get('verdict_policy')}",
            f"- Delivery gate: {gates.get('delivery_gate', {}).get('verdict_policy')}",
            "",
            "## Loop Control",
            "",
            f"- Max iterations: {control.get('max_iterations')}",
            f"- Budget: `{json.dumps(control.get('budget', {}), sort_keys=True)}`",
            f"- No-progress: `{json.dumps(control.get('no_progress', {}), sort_keys=True)}`",
            "",
            "## Execution Boundary",
            "",
            f"- Mode: `{execution.get('mode', 'in_session')}`",
            f"- Isolation: `{execution.get('isolation', 'current_workspace')}`",
            f"- Side effects: `{json.dumps(execution.get('side_effects', {}), sort_keys=True)}`",
            "",
            "## Observability",
            "",
            f"- State file: `{observability.get('state_file', 'state.json')}`",
            f"- Run log: `{observability.get('run_log', 'run-log.md')}`",
            f"- Checkpoint granularity: `{observability.get('checkpoint_granularity', 'gate')}`",
            "",
            "## Flow Preview",
            "",
            "```text",
            render_ascii_diagram(resolved),
            "```",
            "",
        ]
    )
    return "\n".join(line for line in lines if line is not None)


def render_session_prompt(resolved: dict[str, Any]) -> str:
    meta = resolved.get("meta", {})
    goal = resolved.get("goal", {})
    gates = resolved.get("gates", {})
    control = resolved.get("loop_control", {})
    workspace = resolved.get("workspace", {})
    execution = resolved.get("execution", {})
    observability = resolved.get("observability", {})
    criteria = goal.get("verification", [])
    council = resolved.get("council", [])
    title = meta.get("name") or "Looper Generated Loop"

    lines = [
        f"# Run `{title}` In This Session",
        "",
        "Use this prompt when the user wants to run the Looper-designed loop in the current LLM session.",
        "This is the default/easy execution path. The Python runner is the advanced path for running later or outside the session.",
        "",
        "## Operator Instructions",
        "",
        "You are executing a Looper-designed loop in this current session.",
        "Follow the resolved spec below, write handoff files into the workspace, and enforce the caps manually.",
        "Do not use `run-loop.py` unless the user explicitly asks for the advanced external runner.",
        "",
        "1. Create the workspace directory if it does not exist.",
        "2. Read the context sources before drafting the plan.",
        "3. Draft `plan.md` in the workspace.",
        "4. Run the plan gate. Apply programmatic checks when available. For judge criteria, use the configured judge only after consent for any non-local egress; otherwise ask the user to approve a human/current-session substitute.",
        "5. Revise until the gate passes or `max_revisions` is reached.",
        "6. Produce `delivery-N.md` in the workspace.",
        "7. Run the delivery gate after each delivery.",
        "8. Stop when all delivery criteria pass, a cap is reached, or the user stops the loop.",
        "9. Keep `state.json` current with status, iteration, last gate, consent, and blockers.",
        "10. Append a compact entry to `run-log.md` after every context read, model call, check, gate verdict, revision, blocker, and stop decision.",
        "11. Compare each blocker against the previous blocker. If the same blocker repeats for the configured no-progress window, stop or ask for the configured human checkpoint instead of revising again.",
        "12. Treat token and USD budgets as operator limits in this session: if exact accounting is unavailable, stop and ask before continuing when the loop appears likely to exceed them.",
        "",
        "## Files",
        "",
        f"- Source spec: `{Path(resolved.get('source', 'loop.yaml')).name}`",
        "- Human summary: `LOOP.md`",
        "- Resolved spec: `loop.resolved.json`",
        f"- Workspace: `{workspace.get('dir', './loop-workspace')}`",
        f"- State file: `{observability.get('state_file', 'state.json')}`",
        f"- Run log: `{observability.get('run_log', 'run-log.md')}`",
        "",
        "## Goal",
        "",
        goal.get("statement", "").strip(),
        "",
        "## Definition Of Done",
        "",
        goal.get("definition_of_done", "").strip(),
        "",
        "## Context Sources",
        "",
    ]

    context_sources = goal.get("context_sources", [])
    if context_sources:
        for source in context_sources:
            if "file" in source:
                lines.append(f"- Read file `{source['file']}`")
            elif "cmd" in source:
                lines.append(f"- Run command `{json.dumps(source['cmd'])}`")
    else:
        lines.append("- No context sources configured.")

    lines.extend(["", "## Verification Criteria", ""])
    for item in criteria:
        if item["type"] == "programmatic":
            lines.append(
                f"- `{item['id']}` programmatic: run `{json.dumps(item['check'])}` and expect `{item['expect']}`"
            )
        elif item["type"] == "judge":
            lines.append(f"- `{item['id']}` judge rubric: {item['rubric']}")
        elif item["type"] == "human":
            lines.append(f"- `{item['id']}` human signoff: {item['prompt']}")

    lines.extend(["", "## Council", ""])
    if council:
        for member in council:
            locality = "local" if member.get("local") else "non-local"
            lines.append(
                f"- `{member['id']}` {member.get('role')} via `{json.dumps(member.get('invoke', []))}` "
                f"({locality}; timeout {member.get('timeout_sec', 600)}s)"
            )
    else:
        lines.append("- No council members configured.")

    lines.extend(["", "## Gates", ""])
    for gate_name in ("plan_gate", "delivery_gate"):
        gate = gates.get(gate_name, {})
        lines.extend(
            [
                f"### {gate_name}",
                "",
                f"- When: `{gate.get('when')}`",
                f"- Policy: `{gate.get('verdict_policy')}`",
                f"- Verdict source: `{gate.get('verdict_source', 'none')}`",
                f"- Criteria: `{', '.join(gate.get('criteria', []))}`",
                f"- Max revisions: `{gate.get('max_revisions')}`",
                "",
            ]
        )

    lines.extend(
        [
            "## Loop Control",
            "",
            f"- Max iterations: `{control.get('max_iterations')}`",
            f"- Budget: `{json.dumps(control.get('budget', {}), sort_keys=True)}`",
            f"- No-progress: `{json.dumps(control.get('no_progress', {}), sort_keys=True)}`",
            f"- Human checkpoints: `{', '.join(control.get('human_checkpoints', [])) or 'none'}`",
            "- Stop conditions:",
        ]
    )
    for condition in control.get("stop_conditions", []):
        lines.append(f"  - {condition}")

    lines.extend(
        [
            "",
            "## Execution Boundary",
            "",
            f"- Mode: `{execution.get('mode', 'in_session')}`",
            f"- Isolation: `{execution.get('isolation', 'current_workspace')}`",
            f"- Side effects: `{json.dumps(execution.get('side_effects', {}), sort_keys=True)}`",
            "",
            "If the loop needs scheduled runs, child-agent lifecycle management, concurrency control, or restart-safe step retries, stop and tell the user this Looper spec should be handed to a durable orchestrator.",
            "",
            "## Observability",
            "",
            f"- State file: `{observability.get('state_file', 'state.json')}`",
            f"- Run log: `{observability.get('run_log', 'run-log.md')}`",
            f"- Checkpoint granularity: `{observability.get('checkpoint_granularity', 'gate')}`",
            "",
            "Use `state.json` for the latest resumable status and `run-log.md` for the append-only history of what happened.",
        ]
    )

    lines.extend(["", "## Privacy", ""])
    egress = resolved.get("privacy", {}).get("egress", [])
    if egress:
        for entry in egress:
            lines.append(
                f"- Before sending `{', '.join(entry.get('sends', []))}` to `{entry.get('to')}`, "
                f"confirm consent and apply redactions `{', '.join(entry.get('redact', []))}`."
            )
    else:
        lines.append("- No cross-vendor egress configured.")

    lines.extend(
        [
            "",
            "## Start Now",
            "",
            "If the user asked to run now, begin at step 1 under Operator Instructions and keep going until a stop condition is reached.",
            "",
        ]
    )
    return "\n".join(lines)


def cmd_detect(args: argparse.Namespace) -> int:
    registry = detect_models()
    if args.write:
        existing = read_registry(args.registry)
        existing.update(registry)
        write_registry(existing, args.registry)
    print(json.dumps(registry, indent=2, sort_keys=True))
    return 0


def cmd_register(args: argparse.Namespace) -> int:
    if not args.invoke:
        raise LooperError("--invoke needs at least one command token")
    # argparse cannot accept flag-like tokens after --invoke, so a single
    # quoted string (e.g. --invoke "claude -p") is split shell-style.
    invoke = list(args.invoke)
    if len(invoke) == 1:
        invoke = normalize_argv(invoke[0], "--invoke")
    reject_secret_material(invoke, "--invoke")
    if args.notes:
        reject_secret_material([args.notes], "--notes")
    registry = read_registry(args.registry)
    registry[args.model_id] = {
        "cli": invoke[0],
        "invoke": invoke,
        "available": shutil.which(invoke[0]) is not None,
        "authed": args.authed,
        "local": args.local,
        "model": args.model,
        "notes": args.notes or "",
    }
    write_registry(registry, args.registry)
    print(f"Registered {args.model_id} in {args.registry}")
    return 0


def cmd_compile(args: argparse.Namespace) -> int:
    source = args.loop_yaml.resolve()
    spec = load_yaml(source)
    resolved = normalize_spec(spec, source)
    placeholders = sorted(find_placeholders(resolved))
    if placeholders:
        print(
            "looper: warning: unresolved template placeholders remain "
            f"({', '.join(placeholders)}); fill them in before running this loop",
            file=sys.stderr,
        )
    out = args.out or source.with_name("loop.resolved.json")
    write_json(out, resolved)
    if args.render:
        write_text_lf(args.render, render_loop(resolved))
    if args.session_prompt:
        write_text_lf(args.session_prompt, render_session_prompt(resolved))
    print(f"Wrote {out}")
    if args.render:
        print(f"Wrote {args.render}")
    if args.session_prompt:
        print(f"Wrote {args.session_prompt}")
    return 0


def cmd_lint(args: argparse.Namespace) -> int:
    source = args.loop_yaml.resolve()
    raw = load_yaml(source)
    raw_copy = copy.deepcopy(raw)
    resolved = normalize_spec(raw, source)
    findings = lint_spec(raw_copy, resolved)
    errors = sum(1 for item in findings if item["severity"] == LINT_ERROR)
    warnings = len(findings) - errors

    if args.json:
        print(
            json.dumps(
                {"findings": findings, "errors": errors, "warnings": warnings},
                indent=2,
                sort_keys=True,
            )
        )
    else:
        name = args.loop_yaml
        for item in findings:
            print(f"{name}: {item['severity']}[{item['check']}]: {item['message']}")
        if findings:
            print(f"looper lint: {errors} error(s), {warnings} warning(s)")
        else:
            print("looper lint: no findings")

    if errors or (args.strict and findings):
        return 1
    return 0


def cmd_session_prompt(args: argparse.Namespace) -> int:
    resolved = load_json(args.resolved_json)
    prompt = render_session_prompt(resolved)
    if args.out:
        write_text_lf(args.out, prompt)
        print(f"Wrote {args.out}")
    else:
        print(prompt)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="looper", description="Looper scaffolding helpers")
    sub = parser.add_subparsers(dest="command", required=True)

    detect = sub.add_parser("detect-models", help="Detect model CLIs and print registry JSON")
    detect.add_argument("--write", action="store_true", help="Merge results into the model registry")
    detect.add_argument("--registry", type=Path, default=REGISTRY_PATH)
    detect.set_defaults(func=cmd_detect)

    register = sub.add_parser("register-model", help="Register custom model CLI invocation metadata")
    register.add_argument("model_id")
    register.add_argument("--invoke", nargs="+", required=True)
    register.add_argument("--model", default="")
    register.add_argument("--local", action="store_true")
    register.add_argument("--authed", action="store_true")
    register.add_argument("--notes", default="")
    register.add_argument("--registry", type=Path, default=REGISTRY_PATH)
    register.set_defaults(func=cmd_register)

    compile_cmd = sub.add_parser("compile", help="Compile loop.yaml to loop.resolved.json")
    compile_cmd.add_argument("loop_yaml", type=Path)
    compile_cmd.add_argument("--out", type=Path)
    compile_cmd.add_argument("--render", type=Path)
    compile_cmd.add_argument("--session-prompt", type=Path)
    compile_cmd.set_defaults(func=cmd_compile)

    lint = sub.add_parser(
        "lint",
        help="Check a loop.yaml against the design-rubric anti-patterns",
        description=(
            "Static anti-pattern checks over a compilable loop.yaml. "
            "Errors mean the spec will not behave the way it reads at runtime; "
            "warnings are rubric coaching. Exit 1 on errors (or on any finding "
            "with --strict), 2 if the spec does not compile."
        ),
    )
    lint.add_argument("loop_yaml", type=Path)
    lint.add_argument("--json", action="store_true", help="Emit findings as JSON")
    lint.add_argument(
        "--strict", action="store_true", help="Treat warnings as failures (exit 1)"
    )
    lint.set_defaults(func=cmd_lint)

    session_prompt = sub.add_parser(
        "session-prompt", help="Render the in-session execution prompt from loop.resolved.json"
    )
    session_prompt.add_argument("resolved_json", type=Path)
    session_prompt.add_argument("--out", type=Path)
    session_prompt.set_defaults(func=cmd_session_prompt)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except LooperError as exc:
        print(f"looper: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
