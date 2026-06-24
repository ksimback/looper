---
name: looper
description: >
  Scaffold a well-designed agent loop with best-practice coaching and a
  cross-model review council. Use when the user wants to design, build, or set
  up an agent loop, iterative agent workflow, self-review loop, LLM-as-judge
  loop, multi-model council, reviewer/judge gate, or /goal-style looping
  process. Guide goal refinement, typed verification criteria, reviewer and
  judge selection, privacy boundaries, termination guards, no-progress stops,
  and lightweight observability, then emit a RUN_IN_SESSION.md handoff prompt
  plus portable loop.yaml, loop.resolved.json, LOOP.md, and run-loop.py.
tags: [agent-loop, llm-as-judge, multi-model, review-council, planning, hermes-port]
related_skills: [swarm-sprint-execution, kanban-orchestrator, systematic-debugging]
---

# Looper (Hermes port)

> **Fork of [ksimback/looper](https://github.com/ksimback/looper)** with native
> integration for [Hermes Agent](https://hermes-agent.nousresearch.com).
> The contract of `loop.yaml` / `loop.resolved.json` / `LOOP.md` /
> `RUN_IN_SESSION.md` is identical to upstream — loops designed in Claude Code
> can be copied here and vice versa. Only `run-loop.py` gets an opt-in
> alternative (`hermes-runner.py`).

Use Looper as a loop design coach and scaffolder. During design, interview,
critique, validate, and write files. After emission, offer to run the loop in
the current session using `RUN_IN_SESSION.md`; keep `run-loop.py` as the
advanced external runner, or `heres-runner.py` as the Hermes-native runner.

## Invoking this skill (Hermes)

This skill follows the standard Hermes skill pattern. The skill body is loaded
with `skill_view(name='looper')` and then used as the operating instructions
for the rest of this turn. From the user's perspective, the natural triggers
are:

- "design a loop for X"
- "set up a /goal-style loop"
- "I want a multi-model review council for this"
- "scaffold an agent loop"

## Resolving the Looper install root

All `looper.py` invocations need the path to this skill's directory. Set it
once per session, then use `$LOOPER_DIR` everywhere:

```bash
# Adjust to the actual install path. Upstream install puts it at $HOME/.claude/skills/looper
# on Claude Code; the Hermes install (see install.sh) puts it at $HOME/.hermes/skills/looper
export LOOPER_DIR="$HOME/.hermes/skills/looper"
```

If the user is inside Claude Code, the same code path works because the
upstream install is at `$HOME/.claude/skills/looper`. Detect at runtime:

```bash
if [ -d "$HOME/.hermes/skills/looper" ]; then
  export LOOPER_DIR="$HOME/.hermes/skills/looper"
elif [ -d "$HOME/.claude/skills/looper" ]; then
  export LOOPER_DIR="$HOME/.claude/skills/looper"
else
  echo "looper: not installed. Run install.sh first." >&2
  exit 1
fi
```

> **Note for Hermes-native runners**: the `hermes-runner.py` script reads
> `loop.resolved.json` and dispatches steps through `delegate_task` and
> `cronjob` instead of `subprocess`. The spec format is the same; only the
> execution boundary changes. See "Hermes-native execution" below.

## Workflow

1. Resolve the target path from the user. If no target is given, use
   `./looper-output`. If the target contains an existing `loop.yaml`, treat
   the task as an edit/resume instead of a fresh scaffold.
2. Load the relevant rubric only when entering that stage:
   - Goal stage: `references/goal-rubric.md`.
   - Verification stage: `references/verification-rubric.md`.
   - Council stage: `references/council-rubric.md`.
   - Control stage: `references/control-rubric.md`.
   - Model detection or privacy details: `references/model-detection.md`.
3. Interview in seven stages: goal, verification, host model, council,
   gates/control, confirmation flow preview, emit/run option. In the control
   stage, cover execution boundary, isolation, no-progress signals, state, and
   run logging.
4. Critique each stage before accepting it. Prefer concrete alternatives over
   vague warnings. Push weak goals toward outcome, scope, context, and done
   state. Push weak verification toward programmatic checks first, then judge
   rubrics, then human signoff.
5. Keep reviewer and judge roles distinct. A reviewer writes notes. A judge
   returns a structured verdict. `revise_until_clean` must name a judge member
   or `human` as `verdict_source`.
6. Require multiple termination guards: `max_iterations`, a revision cap on
   each gate, a no-progress stop, and either a budget cap or an explicit human
   stop point.
7. Before any cross-vendor council member is selected, state what context will
   leave the user's machine, which CLI receives it, which redaction globs apply,
   and that both execution paths require first-send consent.
8. Show an ASCII flow preview of the planned loop and ask for confirmation
   before final emission. Optimize for terminal readability (the same shape
   works in Claude Code, Hermes, and any CLI).
9. Emit these files into the target:
   - `loop.yaml`
   - `loop.resolved.json`
   - `LOOP.md`
   - `RUN_IN_SESSION.md`
   - `run-loop.py` (upstream runner, subprocess-based) — required
   - `hermes-runner.py` (Hermes-native runner, `delegate_task`/`cronjob` based) — opt-in
   - `loop-workspace/`
   - `README.md`
10. After writing `loop.yaml`, run:
    `LOOPER_PYTHON="${LOOPER_DIR}/.venv/bin/python"; [ -x "$LOOPER_PYTHON" ] || LOOPER_PYTHON=python3; "$LOOPER_PYTHON" ${LOOPER_DIR}/scripts/looper.py compile <target>/loop.yaml --out <target>/loop.resolved.json --render <target>/LOOP.md --session-prompt <target>/RUN_IN_SESSION.md`
    If `python3` is not available, try `python`.
11. Ask whether the user wants to run the loop now in this session. If yes,
    follow `RUN_IN_SESSION.md` directly as the active task. If no, explain that
    the same file is the easy restart path. For durable recurring execution
    across sessions, see "Hermes-native execution" below.

## File Rules

- Write argv arrays, never shell command strings, for all model and check
  invocations.
- Do not write API keys, access tokens, passwords, or CLI auth material into
  `loop.yaml`, `loop.resolved.json`, or model registries.
- Default redaction globs are `.env`, `.env.*`, `secrets/**`, and `**/*.key`.
- Keep `loop.yaml` human-readable and commented where useful. The emitted
  runner reads only `loop.resolved.json`.
- Keep `RUN_IN_SESSION.md` as the default/easy execution handoff. It is meant
  for the current LLM session or a future pasted prompt.
- Copy `templates/run-loop.py` exactly unless the user explicitly asks to edit
  the external runner contract. The Hermes-native runner is a separate file.

## Hermes-native execution

When the user wants the loop to be **durable** (survive a Hermes session
restart), **observable** (findable via `session_search`), or **scheduled**
(fire on a cron cadence), the upstream `run-loop.py` is the wrong tool — it
runs in-process and has no notion of session identity. The Hermes port adds
`templates/hermes-runner.py`, a drop-in alternative that:

- Dispatches each host step via `delegate_task(goal=…, toolsets=[…])`, so the
  work happens in a fresh subagent context with its own model selection.
- Dispatches each judge step via `delegate_task(goal=…, model=<judge-vendor>)`,
  preserving the cross-model review-council property (different model in the
  judge seat).
- Records run state and log via the same `state.json` / `run-log.md` files as
  the upstream runner, so the two runners are observation-compatible.
- Can be scheduled with `cronjob(action='create', script='python3
  hermes-runner.py', schedule='<cron-expr>', notify_on_complete=true)`.

The Hermes runner does not replace `run-loop.py`. It complements it. The
default for in-session/interactive work is `RUN_IN_SESSION.md`; the default for
batch/recurring work is `hermes-runner.py`; the upstream `run-loop.py` stays
for anyone who wants vanilla `subprocess`-based execution (e.g. inside a
container, CI, or `make`).

## Helper Scripts

All assume `LOOPER_DIR` is exported and the venv exists (run `install.sh`
once).

- Detect model CLIs:
  `"$LOOPER_PYTHON" ${LOOPER_DIR}/scripts/looper.py detect-models --write`
- Register a custom CLI:
  `"$LOOPER_PYTHON" ${LOOPER_DIR}/scripts/looper.py register-model <id> --invoke <cmd> [args...]`
- Compile and render:
  `"$LOOPER_PYTHON" ${LOOPER_DIR}/scripts/looper.py compile <target>/loop.yaml --out <target>/loop.resolved.json --render <target>/LOOP.md --session-prompt <target>/RUN_IN_SESSION.md`
- Render only the in-session handoff:
  `"$LOOPER_PYTHON" ${LOOPER_DIR}/scripts/looper.py session-prompt <target>/loop.resolved.json --out <target>/RUN_IN_SESSION.md`
- Run a compiled loop (subprocess runner, upstream):
  `python3 <target>/run-loop.py`
- Run a compiled loop (Hermes-native runner, opt-in):
  `python3 <target>/hermes-runner.py`

## Confirmation Flow Preview

Use this shape and customize labels:

```text
+--------------------------------+
| 1. Goal + context              |
| read sources                   |
+--------------------------------+
               |
               v
+--------------------------------+
| 2. Draft plan.md               |
| state -> state.json            |
+--------------------------------+
               |
               v
+--------------------------------+
| 3. Plan gate                   |
| verdict: reviewer-1            |
+--------------------------------+
               | needs work -> revise <= 3 -> step 2
               | pass
               v
+--------------------------------+
| 4. Write delivery-N.md         |
| log -> run-log.md              |
+--------------------------------+
               |
               v
+--------------------------------+
| 5. Delivery gate               |
| verdict: reviewer-1            |
+--------------------------------+
               | needs work -> revise <= 3 -> step 4
               | pass
               v
+--------------------------------+
| 6. Final output                |
| all gates clean                |
+--------------------------------+

Stops: pass gates | max 12 iterations | no progress x2 | budget 30m, $5.0
```

## Emit Checklist

- The goal has a clear outcome, scope boundary, context sources, and done state.
- Verification criteria are typed as `programmatic`, `judge`, or `human`.
- At least one criterion is not purely vibe-based unless the user explicitly
  accepts that risk.
- Each `revise_until_clean` gate has a valid `verdict_source`.
- Every external invocation is an argv array with a timeout.
- Cross-vendor egress is scoped, redacted, and consent-gated.
- `loop_control` has iteration, revision, no-progress, and wall-clock or budget
  caps.
- Execution boundary and isolation are explicit, even when the choice is the
  current workspace.
- Observability names a `run-log.md` and `state.json` path.
- `loop.resolved.json`, `LOOP.md`, and `RUN_IN_SESSION.md` compile
  successfully before handoff.

## Compatibility with upstream

| Aspect | Upstream Claude Code | This fork (Hermes) |
|---|---|---|
| `loop.yaml` / `loop.resolved.json` / `LOOP.md` / `RUN_IN_SESSION.md` schema | v1 | **v1 (identical)** |
| `scripts/looper.py` (compiler) | unchanged | **unchanged** |
| `references/*.md` (rubrics) | unchanged | **unchanged** |
| `schemas/*.json` | unchanged | **unchanged** |
| `templates/run-loop.py` (subprocess runner) | upstream | **upstream, unmodified** |
| `templates/hermes-runner.py` (native runner) | n/a | **new, opt-in** |
| `commands/looper.md` (slash command) | exists | **dropped (Hermes has no slash-command system)** |
| `install.sh` | `$HOME/.claude/skills/looper` | **`$HOME/.hermes/skills/looper`** |
| Frontmatter `disable-model-invocation`, `argument-hint`, `allowed-tools` | required | **dropped (Hermes frontmatter is `name` + `description`)** |
| Path variable | `${CLAUDE_SKILL_DIR}` | **`${LOOPER_DIR}`** (upstream install paths also detected) |
