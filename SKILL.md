---
name: looper
description: >
  Scaffold a well-designed agent loop with best-practice coaching and a
  cross-model review council. Use when the user wants to design, build, or set
  up an agent loop, iterative agent workflow, self-review loop, LLM-as-judge
  loop, multi-model council, reviewer/judge gate, or /goal-style looping
  process. Start from a named pattern template (security-scan, code-review,
  bug-hunt, docs-sync, research-synthesis) or from a blank interview. Guide
  goal refinement, typed verification criteria, reviewer and judge selection,
  privacy boundaries, termination guards, no-progress stops, and lightweight
  observability, then emit a RUN_IN_SESSION.md handoff prompt plus portable
  loop.yaml, loop.resolved.json, LOOP.md, and run-loop.py.
disable-model-invocation: true
argument-hint: "[target-dir] [--template <name>]"
allowed-tools: Read, Write, Bash
---

# Looper

Use Looper as a loop design coach and scaffolder. During design, interview,
critique, validate, and write files. After emission, offer to run the loop in
the current session using `RUN_IN_SESSION.md`; keep `run-loop.py` as the
advanced external runner.

## Workflow

1. Resolve the target path and optional `--template <name>` from the
   `/looper` arguments. If no target is given, use `./looper-output`. If the
   target contains an existing `loop.yaml`, treat the task as an edit/resume
   instead of a fresh scaffold. If a template was requested, follow Template
   Mode below instead of the blank-slate interview in step 3.
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
   before final emission. Optimize for Claude Code CLI readability.
9. Emit these files into the target:
   - `loop.yaml`
   - `loop.resolved.json`
   - `LOOP.md`
   - `RUN_IN_SESSION.md`
   - `run-loop.py`
   - `loop-workspace/`
   - `README.md`
10. After writing `loop.yaml`, resolve the helper Python (see Helper Python
   below) and run:
   `"$LOOPER_PYTHON" ${CLAUDE_SKILL_DIR}/scripts/looper.py compile <target>/loop.yaml --out <target>/loop.resolved.json --render <target>/LOOP.md --session-prompt <target>/RUN_IN_SESSION.md`
   Then run
   `"$LOOPER_PYTHON" ${CLAUDE_SKILL_DIR}/scripts/looper.py lint <target>/loop.yaml`
   and relay the findings: fix any `error[...]` before continuing (the spec
   would not behave as written), and surface `warning[...]` lines to the user
   as design coaching they may accept or address.
11. Ask whether the user wants to run the loop now in this session. If yes,
   follow `RUN_IN_SESSION.md` directly as the active task. If no, explain that
   the same file is the easy restart path and `run-loop.py` is available for
   advanced external execution.

## Template Mode

The pattern library lives at `${CLAUDE_SKILL_DIR}/templates/loops/` — one
directory per template containing a complete, compilable `loop.yaml` (with
`{{PLACEHOLDER}}` tokens marking project-specific slots), a `README.md`
(use-when, placeholder table, customization notes), and optionally
`scripts/` with helper checkers. The catalog index is
`templates/loops/README.md`.

A template is a pre-answered interview, not a bypass of design review:

0. If the target directory already contains a `loop.yaml`, the edit/resume
   rule in step 1 wins: do **not** overwrite it with a template. Say the
   directory already has a loop and ask the user to pick an empty target or
   confirm they want it replaced before continuing.
1. If `--template` has no name, an unknown name, or the user asks what is
   available, show the catalog table (template + use-when) and let them pick.
2. Read the template's `loop.yaml` and `README.md`. Use the template as the
   seed instead of a blank spec.
3. Run a compressed interview in place of the seven blank-slate stages:
   ask for each `{{PLACEHOLDER}}` slot named in the template README, run
   the host-model stage against detected CLIs (`detect-models`) and swap
   `host` / `council` invocations to what is actually installed and authed,
   then confirm target and workspace paths.
4. Everything after the interview still applies unchanged: critique each
   pre-filled stage against its rubric (step 4), the structural rules
   (steps 5–8) including the cross-vendor egress statement, the ASCII flow
   preview, confirmation, emission, and compile.
5. Never emit while any `{{` token remains in `loop.yaml`. The compiler
   prints `looper: warning: unresolved template placeholders remain ...`
   for this case — treat that warning as a blocker, not advice.
6. At emission, copy the template's `scripts/` directory (when present)
   into `<target>/scripts/` alongside the standard emitted files, before
   running compile.

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
  the external runner contract.

## Helper Python

The installer creates a private venv inside the skill directory. Its Python
lives at `.venv/bin/python` on macOS/Linux and `.venv/Scripts/python.exe` on
Windows. Shell state does not persist between commands, so prefix every helper
invocation below with this resolution (works in POSIX shells and Git Bash on
Windows):

```bash
LOOPER_PYTHON="${CLAUDE_SKILL_DIR}/.venv/bin/python"; [ -x "$LOOPER_PYTHON" ] || LOOPER_PYTHON="${CLAUDE_SKILL_DIR}/.venv/Scripts/python.exe"; [ -x "$LOOPER_PYTHON" ] || { LOOPER_PYTHON=python3; "$LOOPER_PYTHON" -c "" >/dev/null 2>&1 || LOOPER_PYTHON=python; }
```

The final fallback executes the candidate rather than just locating it: on
Windows, `python3` on PATH is often the Microsoft Store alias stub, which
exists but cannot run scripts. If no candidate can execute `-c ""`, tell the
user to rerun the Looper installer (it creates the venv).

## Helper Scripts

Each command below assumes the Helper Python resolution is prefixed in the
same shell invocation:

- Detect model CLIs:
  `"$LOOPER_PYTHON" ${CLAUDE_SKILL_DIR}/scripts/looper.py detect-models --write`
- Register a custom CLI (quote the whole invocation if it contains flags):
  `"$LOOPER_PYTHON" ${CLAUDE_SKILL_DIR}/scripts/looper.py register-model <id> --invoke "<cmd> [args...]"`
- Compile and render:
  `"$LOOPER_PYTHON" ${CLAUDE_SKILL_DIR}/scripts/looper.py compile <target>/loop.yaml --out <target>/loop.resolved.json --render <target>/LOOP.md --session-prompt <target>/RUN_IN_SESSION.md`
- Lint against the design-rubric anti-patterns (add `--strict` to fail on warnings, `--json` for tooling):
  `"$LOOPER_PYTHON" ${CLAUDE_SKILL_DIR}/scripts/looper.py lint <target>/loop.yaml`
- Render only the in-session handoff:
  `"$LOOPER_PYTHON" ${CLAUDE_SKILL_DIR}/scripts/looper.py session-prompt <target>/loop.resolved.json --out <target>/RUN_IN_SESSION.md`

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

Stops: pass gates | max 12 iterations | no progress x2 | budget 30m, $5.0, 2000000 tokens
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
