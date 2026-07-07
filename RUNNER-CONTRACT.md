# Looper Runner Contract (v1)

This document is the normative contract for **runners**: programs that execute
a compiled Looper spec (`loop.resolved.json`, spec `version: 1`). The bundled
`templates/run-loop.py` is the reference implementation; third-party runners
(other languages, other host platforms) that satisfy every MUST below — and
pass the conformance suite in `conformance/` — may honestly claim to run
Looper loops.

"MUST" / "MUST NOT" / "SHOULD" are used as in RFC 2119. The contract is
versioned with the spec format: this file describes runners for spec
`version: 1` and the resolved-spec schema `schemas/loop.resolved.v1.schema.json`.

## 1. Inputs and invocation

- A runner MUST take a single `loop.resolved.json` as its input and MUST NOT
  read or re-interpret `loop.yaml`. Compilation is the compiler's job; the
  resolved spec is the whole interface.
- Invocation convention (what the conformance suite exercises): the resolved
  spec path is the runner's sole positional argument, and the runner operates
  with the *loop directory* (the spec file's parent) as its working
  directory. A runner MAY offer additional interfaces, but MUST support this
  one to claim conformance.
- A runner MUST reject a spec whose required fields are missing with a clear
  error rather than guessing defaults for: `goal.statement`, `gates.*`,
  `loop_control.max_iterations`, `workspace.dir`, `host.invoke`.

## 2. Paths and workspace

- `workspace.dir` MUST resolve inside the directory containing the spec (the
  *loop directory*). A workspace path that escapes it (absolute, `..`,
  symlink tricks) MUST abort the run before any filesystem writes.
- A context-source `file` path that escapes the loop directory MUST NOT be
  read. The runner MAY abort, or MAY continue with a blocked-path marker in
  place of the content (the reference runner does the latter); what it MUST
  NOT do is read or send the file.
- Artifacts live in the workspace: `plan.md`, `delivery-<n>.md`,
  `review-<gate>-<n>.md`, plus the configured `observability.state_file` and
  `observability.run_log`.

## 3. Model invocations

- Every model call MUST use the member's `invoke` argv array verbatim — no
  shell string interpolation — with the prompt on stdin, and MUST apply the
  member's `timeout_sec`.
- Programmatic checks MUST run their `check` argv with `timeout_sec` and
  evaluate `expect` as: `exit_zero` (return code 0), `exit_nonzero`
  (non-zero), `stdout_contains` (the `contains` string appears in stdout).
- Programmatic-check and `cmd` context-source subprocesses MUST run with the
  loop directory as their working directory — specs are written with
  loop-directory-relative paths (`loop-workspace/delivery-1.md`,
  `inputs/...`).
- Model and check subprocess output MUST be decoded as UTF-8 (with
  replacement on errors), not the platform locale codepage.

## 4. Gates and verdicts

- The plan gate MUST pass on `plan.md` before any delivery is produced; the
  delivery gate MUST run against each `delivery-<n>.md`.
- Criterion types:
  - `programmatic` and `human` criteria are evaluated on every gate round.
  - `judge` criteria are evaluated **only** by the gate's `verdict_source`
    when `verdict_policy: revise_until_clean` names a judge member. A runner
    MUST NOT silently skip judge criteria in other configurations — if a
    gate's configuration makes a judge criterion unreachable, the runner
    SHOULD surface that (the compiler's `lint` flags it statically).
- `revise_until_clean`: the gate passes only when no programmatic/human
  failures remain AND the verdict source returns `pass`. A `human` verdict
  source is an interactive approval.
- `fixed_passes`: the gate runs reviewer passes until `max_revisions`
  review rounds have completed with no programmatic/human failures. The
  synthetic "one more reviewer pass" marker MUST NOT feed the no-progress
  detector or be reported as a real failure.
- Judge output MUST be parsed as a JSON object with `verdict` of `pass` or
  `revise`. Unparseable or malformed output MUST degrade to `revise` with a
  recorded warning — never to `pass`.
- On a failed round the runner MUST write the review artifact, request a
  host revision, and stop the gate at `max_revisions`, marking the run
  failed.

## 5. Caps and termination

- `loop_control.max_iterations` MUST bound delivery iterations; exhausting it
  fails the run.
- The wall-clock budget (`loop_control.budget.wall_clock_min`) MUST be
  enforced at least at step boundaries, and elapsed time MUST accumulate
  across resumed runs (persisted in state).
- Token / USD budgets are advisory unless the runner has real accounting; a
  runner MUST NOT claim to enforce them when it cannot measure them.
- No-progress: the runner MUST derive a signature from a gate round's
  failures; when the same gate yields the same signature
  `max_stalled_iterations` times consecutively, the configured action fires:
  `stop` fails the **entire run** (not just the current artifact);
  `human_checkpoint` asks for an interactive override.
- A run MUST end in exactly one terminal state: `passed`, `failed`, or
  `blocked`. A crash or model failure MUST NOT leave state claiming
  `running`.

## 6. Consent (fail closed)

- Before the first send to any council member not flagged `local: true`, the
  runner MUST obtain interactive consent — unless every `privacy.egress`
  entry targeting that member declares `consent: granted`.
- No matching egress entry means consent is REQUIRED, not waived.
- Granted consent MUST be recorded in state (timestamp, what is sent, active
  redactions) so later runs and audits can see it. Refused consent MUST
  block the run (`blocked`, non-zero exit) without invoking the member.
- The prompt content MUST be scrubbed (section 7) before consent is
  requested, and the consent prompt SHOULD display any leak warnings
  already recorded for that member — the consent decision is made with the
  leak signal visible, not before it exists.

## 7. Redaction (two layers)

- The **default redaction globs** are normative and MUST always apply, even
  when `privacy.egress` is empty or absent (the resolved spec does not carry
  them): `.env`, `.env.*`, `secrets/**`, `**/*.key`. Every
  `privacy.egress[].redact` pattern extends this set.
- **Layer 1 — path-based non-send.** Files matching the redaction globs
  (matching must cover nested paths, e.g. bare `.env` also matches
  `config/.env`) MUST NOT be read into any prompt. A context source naming a
  flagged file yields a `[redacted]` marker instead of content.
- **Layer 2 — content scrub with surfacing.** Prompts to **any** model —
  the host as much as council members — and the output of `cmd` context
  sources MUST be scrubbed against the content of flagged files
  (full-content and per-line). When a scrub catches anything, the runner
  MUST log a redaction event and record a state warning naming **every**
  source file whose content matched and the destination. Scrubbing is
  best-effort and errs toward over-redaction; surfacing it is what keeps
  the contract honest.
- A flagged file the runner cannot content-scrub (too large, undecodable)
  MUST be surfaced as a blind spot (log event + state warning) rather than
  silently skipped — the operator must know the scrub cannot see it.

## 8. State and run log

- The state file MUST track at minimum: `status`, `iteration`, `consent`,
  `warnings`, accumulated wall-clock, and timestamps; it is the resumable
  snapshot.
- The run log MUST be append-only and record at minimum: run start, context
  reads (with redaction status), host calls, programmatic check results,
  judge verdicts, gate transitions, revisions, no-progress detections, and
  every stop decision with its reason.

## 9. Exit codes

- `0` MUST mean exactly one thing: the loop passed all gates. Any other
  outcome MUST exit non-zero.
- The split among non-zero codes is a SHOULD: `1` for a loop that ran and
  failed (gate exhaustion, iteration cap, no-progress), `2` for a runner
  refusal or error (bad spec, bad paths, consent refused, interactive input
  unavailable). The reference runner maps a wall-clock budget abort to `2`
  (it is raised as a runner error); tooling that needs to distinguish
  failure classes should read the state file's `status`/`failure`, which is
  the normative record, rather than the 1-vs-2 split.

## 10. Conformance

Run the suite against your runner:

```bash
python conformance/check_runner.py path/to/your-runner
```

The harness scaffolds fixture loops (deterministic fake host/judge scripts —
no real model CLIs needed), invokes your runner in each, and asserts the
observable obligations above: happy path, judge-degrade (with real revision
rounds), consent fail-closed, prompt redaction, host-prompt scrub, default
redaction globs, context non-send, cmd-output scrub, workspace escape
refusal, and revision-cap failure (with a stall-proof judge, so a no-progress
detector cannot mask a missing cap). Every scenario must pass for a
conformance claim. The reference runner is checked in CI against this same
suite.
