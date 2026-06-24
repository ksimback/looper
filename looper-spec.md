# Looper — Design Spec (v0.4)

*An open-source Claude Code skill that scaffolds well-designed agent loops.*

Author: Kevin Simback ([@ksimback](https://x.com/ksimback) · github.com/ksimback)
Status: draft v0.4 · License target: MIT
Changes since v0.3: loop-control now includes no-progress detection, execution-boundary metadata, and lightweight observability through `state.json` plus `run-log.md`.

---

## 1. What it is

`Looper` is a Claude Code skill, invoked with `/looper`, that walks an agentic engineer through designing a multi-step agent loop — and **coaches them toward a good one** as they go. It interviews the user, critiques their goal and verification design against built-in best-practice rubrics, lets them wire in a cross-model reviewer/judge, renders a terminal-friendly ASCII flow preview for confirmation, then **emits an in-session run prompt plus a portable loop spec and external runner**.

The wedge is **design discipline + a cross-model council**, at the layer *before* execution. Claude Code already ships `/goal` (a persistent objective the model self-checks) and `/loop` (an interval scheduler). Neither helps you design a *good* loop, force a checkable definition of done, or get a second, different model's eyes — which is exactly where loops fail. Looper fills that gap and produces artifacts the current session, existing tools, or an external runner can follow. See the README's comparison section.

---

## 2. The scaffolder/runtime boundary (read this first)

Looper is a **scaffolder and session handoff, not an orchestration framework**, and the boundary is a hard rule, not a vibe:

> **Looper's own process never invokes a model to do loop work. It only reads input, coaches, and writes files.**

The default "run now" path is `RUN_IN_SESSION.md`: a structured prompt/handoff that the current Claude Code session can follow immediately after design. The advanced external path is `run-loop.py`, which the user executes in their own environment. Both are generated from the resolved spec and make no design decisions: every choice is already baked into the loop artifacts. This keeps Looper from drifting into being a hidden orchestration framework by accident.

Looper provides file-based state and run logs, but it does **not** provide durable orchestration. It does not schedule work, checkpoint every model/tool step across process restarts, manage child-agent lifecycles, enforce concurrency, or store a production run history. If the loop needs those guarantees, Looper should name that boundary and hand the spec to a real orchestrator.

| Layer | Who | What |
| :-- | :-- | :-- |
| Design | Looper (`/looper`) | interview, coach, validate, emit |
| Immediate execution | current Claude Code session + `RUN_IN_SESSION.md` | follow the resolved loop, write known files, honor gates and caps |
| External execution | emitted `run-loop.py` | parse resolved spec → invoke CLIs → read/write known files → enforce caps → stop |

---

## 3. Design principles

1. **Scaffold, don't run.** Output is files. No long-lived Looper process, no framework to learn.
2. **Coaching over collection.** The wizard's value is the critique it returns at each stage, driven by the rubrics (§5).
3. **Model-agnostic council.** The reviewer/judge can be any installed CLI model, chosen in the wizard. Cross-model is the recommended default.
4. **File-based handoff.** Emitted loops pass state through a shared workspace of files; no fragile context piping.
5. **Two execution surfaces, one resolved spec.** Author in YAML; generate `RUN_IN_SESSION.md` for easy in-session execution and `run-loop.py` for advanced external execution from the normalized `loop.resolved.json`. No shell-parsing of YAML.
6. **Honest durability.** Looper records state, logs decisions, and models resume boundaries, but it does not pretend to be a durable orchestration engine.
7. **Safe by construction.** argv arrays not shell strings; timeouts on every external call; no secrets stored by Looper; explicit consent before any cross-vendor context egress.

---

## 4. The Loop Spec (the core IP)

Authoring format is YAML (`loop.yaml`) — human-friendly, comment-friendly. Looper compiles it to a normalized, validated `loop.resolved.json` (refs expanded, model invocations resolved to argv arrays, rubrics inlined). `RUN_IN_SESSION.md` is rendered from that resolved spec. **The external runner only ever reads `loop.resolved.json`.**

```yaml
version: 1
meta:
  name: ai-workflow-mapping
  description: Map a client's manual workflow into an agent design
  author: ksimback
  created: 2026-06-18

goal:
  statement: >
    Produce an agent workflow map for <client> that converts their current
    manual process into a stepwise agent design with tool calls and handoffs.
  context_sources:
    - file: ./inputs/process-notes.md
    - cmd: "ls ./inputs/transcripts"      # argv-resolved at compile time
  definition_of_done: >
    A LOOP.md + flow preview the client can execute, every step mapped to a
    tool or human action, nothing left as "TBD".

  # Verification taxonomy is first-class structure, not prose.
  verification:
    - id: build-ok
      type: programmatic          # programmatic | judge | human
      check: ["npm", "run", "build"]
      expect: exit_zero
    - id: covers-goal
      type: judge
      rubric: >
        Every part of the goal statement is addressed; each step has an owner
        (tool / model / human); no step depends on info the loop never gathers.
    - id: client-signoff
      type: human
      prompt: "Confirm the map matches the client's real process."

host:
  cli: codex
  model: gpt-5.5-xhigh
  invoke: ["codex", "exec", "--model", "gpt-5.5-xhigh"]   # argv array
  timeout_sec: 600

council:
  - id: reviewer-1
    role: judge                  # reviewer (notes only) | judge (verdict)
    cli: claude
    model: opus-4.8-high
    invoke: ["claude", "-p"]
    timeout_sec: 600
    scope: [plan, delivery]

gates:
  plan_gate:
    when: after_plan
    members: [reviewer-1]
    verdict_policy: revise_until_clean   # requires a verdict_source
    verdict_source: reviewer-1           # a judge member, or "human"
    criteria: [covers-goal]
    max_revisions: 3
  delivery_gate:
    when: after_each_delivery
    members: [reviewer-1]
    verdict_policy: revise_until_clean
    verdict_source: reviewer-1
    criteria: [build-ok, covers-goal]
    max_revisions: 3

loop_control:
  max_iterations: 12
  budget: { usd: 5.00, tokens: 2_000_000, wall_clock_min: 30 }
  no_progress:
    max_stalled_iterations: 2
    signals:
      - same blocking issue repeats
      - delivery artifact has no material change
      - verifier output is unchanged
    action: stop
  human_checkpoints: [after_plan]
  stop_conditions:
    - "all deliveries pass their gate clean"
    - "max_iterations reached"
    - "same blocker repeats for 2 iterations"
    - "any budget cap exceeded"

execution:
  mode: in_session              # in_session | external_runner | orchestrated
  isolation: current_workspace  # current_workspace | branch | worktree | sandbox
  side_effects:
    requires_approval: true
    duplicate_action_check: true

observability:
  state_file: state.json
  run_log: run-log.md
  checkpoint_granularity: gate   # gate | step; Looper itself only guarantees gate-level handoff

privacy:
  egress:
    - to: reviewer-1               # this member's CLI/vendor
      sends: [plan, deliveries]
      redact: [".env", "secrets/**", "**/*.key"]
      consent: required           # wizard must confirm before first send

workspace:
  dir: ./loop-workspace
  layout: [plan.md, "delivery-{n}.md", "review-{n}.md", state.json, run-log.md]
```

### Verification criteria (the value, encoded)

Each criterion is a typed object, never a bare string:

- **programmatic** — `check` is an argv array; `expect` is a pass condition (`exit_zero`, `stdout_contains`, etc.). Deterministic, free to run.
- **judge** — a `rubric` scored by a council member; produces a structured verdict (§ below).
- **human** — a `prompt` shown at a human checkpoint.

The wizard pushes hard to convert vague criteria into `programmatic` where possible, and warns loudly if every criterion is a `judge`/`human` vibe.

### Reviewer vs. judge, and the reviewer-only rule

- **reviewer** → emits notes; host revises against them. No verdict.
- **judge** → emits a structured verdict that gates progression.

**Rule:** `verdict_policy: revise_until_clean` **requires a `verdict_source`** — either a judge member or `human`. A gate with only reviewers (no verdict source) may use `verdict_policy: fixed_passes` (apply notes N times, then proceed) but **not** `revise_until_clean`, because nothing can declare "clean." This removes the ambiguity in v0.1.

### Structured judge output (default)

Judges must return a fenced JSON block; free text is a fallback the runner tolerates but warns on.

```json
{
  "verdict": "revise",
  "blocking_issues": ["Step 4 has no owner", "No fallback if OCR fails"],
  "confidence": 0.82,
  "notes": "Plan is mostly sound; two gaps block sign-off."
}
```

---

## 5. Best-practice intelligence (the "good" part)

Rubric reference files under `references/`, loaded only when their wizard stage runs (progressive disclosure). These are the differentiator and double as the project's public-facing value.

- **`goal-rubric.md`** — outcome vs. activity framing, scope boundaries, explicit "done" state, gather-vs-assume context. Before/after examples.
- **`verification-rubric.md`** — *the most important file.* Turning "make it good" into checkable criteria; the programmatic/judge/human taxonomy and when each applies; anti-patterns (criteria only the author model can grade; "success = no errors thrown"; all-vibe rubrics).
- **`council-rubric.md`** — reviewer vs. judge selection; why cross-model beats same-family; writing judge rubrics that yield stable verdicts; scope guidance.
- **`control-rubric.md`** — termination design: iteration/revision/no-progress/budget caps, where to insert human checkpoints, execution boundaries, failing safe.

---

## 6. Cross-model judge: detection & selection

Dumb, transparent, overridable.

On install / first run, `detect-models` probes `PATH` and known locations for a small allowlist of CLIs (`claude`, `codex`, `gemini`, `llm`, `ollama`, …), checks which are authed, and writes a registry to `~/.looper/models.json`:

```json
{
  "claude":  { "invoke": ["claude", "-p"], "authed": true },
  "codex":   { "invoke": ["codex", "exec"], "authed": true }
}
```

`models.json` stores **invocation metadata only — never API keys or secrets.** Auth stays in each CLI's own config/keychain. In the wizard's host and council stages, Looper offers detected, authed models as choices; for anything missing it prints the install/auth command and offers to re-probe. Unknown CLIs are added via `looper register-model` or by hand-editing the registry.

At compile time, Looper resolves each chosen model's `invoke` into the spec as an argv array. The in-session prompt tells the current session exactly which argv array is configured and when consent is needed; the emitted runner shells out with argv (no string interpolation) and a per-call `timeout_sec`. This is the "codex → claude / claude → codex" bridge from the source post, captured as generated config rather than hand-written.

---

## 7. Execution contracts

### Default: in-session handoff

`RUN_IN_SESSION.md` is the easy path. It is a prompt the current Claude Code session can follow immediately after `/looper` finishes designing the loop:

1. Read `loop.resolved.json` / `LOOP.md` and the listed context sources.
2. Draft `plan.md`.
3. Run the plan gate, including programmatic checks and judge/human criteria.
4. Revise up to `max_revisions`.
5. Write `delivery-N.md`.
6. Run the delivery gate.
7. Keep `state.json` current and append decisions/checks/blockers to `run-log.md`.
8. Stop on pass, cap breach, repeated no-progress, or user stop.

This path is easy and conversational, but caps are enforced by the current agent following instructions, not by a separate process.

### Advanced: external Python runner

`run-loop.py` is the strict external runner, and its contract is fixed and small:

1. Load and validate `loop.resolved.json`.
2. Gather `context_sources` into the workspace.
3. Host drafts `plan.md`.
4. **plan_gate:** run `programmatic` checks; invoke the `verdict_source` member (argv + timeout) for `judge` criteria; parse the structured verdict. On `revise`, write `review-N.md`, host revises, repeat up to `max_revisions`. Honor `human_checkpoints`.
5. On clean, iterate deliveries: host writes `delivery-N.md`; **delivery_gate** runs the same way.
6. Enforce `loop_control` on every cycle. The runner enforces `max_iterations`, `no_progress`, and `budget.wall_clock_min`; `budget.usd` and `budget.tokens` are rendered into prompts/logs as advisory operator limits unless a wrapper adds provider-specific accounting.
7. Append a compact run log after each context, model, check, gate, and revision step.
8. Stop on any `stop_condition`; write final output.

The runner never decides *what* to do — only executes the resolved spec. Single language (Python: near-universal on the ICP's machines; argv, timeouts, and JSON parsing are all native). No dual-shell, no YAML parsing at runtime.

---

## 8. Scaffolding output

```
<target>/
├── loop.yaml             # human-authored source
├── loop.resolved.json    # compiled, validated; the runner reads this
├── LOOP.md               # human-readable rendering + ASCII flow preview
├── RUN_IN_SESSION.md     # default handoff prompt for current-session execution
├── run-loop.py           # advanced external runner (contract above)
├── loop-workspace/       # empty handoff dir with the file layout
│   ├── state.json        # current status, iteration, blockers, consent
│   └── run-log.md        # append-only step/decision/check log
└── README.md             # how to run; attribution
```

Cross-platform note: the in-session handoff is the default path because it keeps the loop in the same Claude Code conversation. A single Python runner replaces v0.1's dual `.ps1`/`.sh` plan for users who want external execution, sidestepping the shell-YAML trap and the Windows/POSIX split (the ICP spans both via Claude Code).

---

## 9. Privacy & security (cross-model means data leaves)

A council sends your project context to *another* vendor's CLI. The wizard must make that explicit and consented:

- **Name the destination.** When a non-local judge is selected, state which vendor/CLI the plan and deliveries will be sent to.
- **Scope egress.** The `privacy.egress` block declares exactly what each member receives (`plan`, `deliveries`, specific paths).
- **Redact.** Default redaction globs (`.env`, `secrets/**`, `**/*.key`) are applied before any send; the user can extend them.
- **Consent.** `consent: required` makes the session handoff ask before the first cross-vendor send and makes the runner refuse the send until the user confirms.
- **Local-only path.** If a member's CLI is local (e.g. `ollama`), flag it as no-egress so privacy-sensitive users can keep the council in-house.

---

## 10. Packaging as a Claude Code skill

Standard skill anatomy (progressive disclosure). Confirmed against current Claude Code docs:

```
looper/
├── SKILL.md
├── references/            # the four rubrics + model-detection notes
├── templates/            # loop.yaml, README, run-loop.py
├── scripts/
│   ├── detect-models.py  # detection wrapper
│   └── looper.py         # detect/register/compile/render/session-prompt
├── examples/
│   └── ai-workflow-mapping/
├── LICENSE
└── README.md
```

`SKILL.md` frontmatter:

```yaml
---
name: looper
description: >
  Scaffold a well-designed agent loop with best-practice coaching and a
  cross-model review council. Use whenever the user wants to build, design,
  or set up an agent loop, an iterative agent workflow, a self-review or
  LLM-as-judge loop, a multi-model "council," or a /goal-style looping
  process — even if they don't say "loop." Guides goal refinement,
  verification criteria, reviewer/judge selection (including non-Claude
  models), and termination guards, then emits RUN_IN_SESSION.md plus a portable
  loop.yaml + runner.
disable-model-invocation: true     # deliberate wizard with file writes; user-triggered only
argument-hint: "[target-dir]"
allowed-tools: Write Bash(python3 *)
---
```

Notes on the frontmatter choices:
- `disable-model-invocation: true` — Looper writes files and runs an interactive interview; it should fire only when the user types `/looper`, never auto-trigger.
- `argument-hint` — autocomplete hint for the target directory (or an existing `loop.yaml` to edit).
- `allowed-tools` — pre-approve file writes and the Python detection script so the wizard doesn't prompt mid-flow.
- `${CLAUDE_SKILL_DIR}` — use it in the body to reference `scripts/detect-models.py` regardless of cwd.
- **Name collision:** `/loop` is a bundled skill; `/looper` avoids it. (The README explains why Looper is a different layer, not a competitor.)

`SKILL.md` body holds the 7-stage wizard procedure and points to the right rubric at each stage; keep it under ~500 lines and let the rubrics carry the depth.

---

## 11. Build order (revised)

1. **Decide the verification taxonomy on paper.** The criteria schema is downstream of it, so settle programmatic/judge/human first.
2. **Freeze the artifact contract via a fake-model spike (≈1 day).** Dummy `host`/`judge` scripts that emit canned `plan.md`, `delivery-N.md`, and structured verdicts. Use them to prove loop control, max_revisions, resume, and verdict parsing — without paying model costs. Keep these as test fixtures.
3. **Define and freeze `loop.yaml` v1 + `loop.resolved.json` schema** against the proven flow.
4. **Write the four rubrics** (verification first — highest leverage).
5. **Build `detect-models` + the registry.**
6. **Write `SKILL.md`** (the wizard) against the frozen schema.
7. **Ship the `ai-workflow-mapping` example end-to-end** as the README demo, including `RUN_IN_SESSION.md` and the ASCII flow preview.

---

## 12. Open decisions

1. **Judge JSON enforcement.** Structured-by-default with a text fallback (chosen). Decide whether to hard-fail on unparseable judge output or degrade to "treat as revise + warn." Lean: degrade + warn.
2. **Spec schema versioning / deprecation policy** before v1.0 so shared specs don't rot.
3. **Local-model UX.** How prominently to surface `ollama`/local judges as the privacy-preserving default.
4. **Resume granularity.** Resume at gate boundaries only, or mid-revision. Lean: gate boundaries (simpler, matches the fixture tests).
