# Run `ai-workflow-mapping` In This Session

Use this prompt when the user wants to run the Looper-designed loop in the current LLM session.
This is the default/easy execution path. The Python runner is the advanced path for running later or outside the session.

## Operator Instructions

You are executing a Looper-designed loop in this current session.
Follow the resolved spec below, write handoff files into the workspace, and enforce the caps manually.
Do not use `run-loop.py` unless the user explicitly asks for the advanced external runner.

1. Create the workspace directory if it does not exist.
2. Read the context sources before drafting the plan.
3. Draft `plan.md` in the workspace.
4. Run the plan gate. Apply programmatic checks when available. For judge criteria, use the configured judge only after consent for any non-local egress; otherwise ask the user to approve a human/current-session substitute.
5. Revise until the gate passes or `max_revisions` is reached.
6. Produce `delivery-N.md` in the workspace.
7. Run the delivery gate after each delivery.
8. Stop when all delivery criteria pass, a cap is reached, or the user stops the loop.
9. Keep `state.json` current with status, iteration, last gate, and any blockers.

## Files

- Source spec: `loop.yaml`
- Human summary: `LOOP.md`
- Resolved spec: `loop.resolved.json`
- Workspace: `./loop-workspace`

## Goal

Produce an agent workflow map that converts the process notes into a stepwise design with tool calls, model responsibilities, and human checkpoints.

## Definition Of Done

A LOOP.md-style workflow map exists, every step has an owner, input, output, and checkpoint decision where needed, and there are no TBDs.

## Context Sources

- Read file `./inputs/process-notes.md`

## Verification Criteria

- `required-sections` programmatic: run `["python", "scripts/check-loop-doc.py", "loop-workspace/delivery-1.md"]` and expect `exit_zero`
- `covers-goal` judge rubric: Every part of the goal statement is addressed. Each workflow step has an owner, required input, output artifact, and human checkpoint where business judgment is needed. No step depends on information the loop never gathers. There are no unresolved TBDs.


## Council

- `reviewer-1` judge via `["claude", "-p"]` (non-local; timeout 600s)

## Gates

### plan_gate

- When: `after_plan`
- Policy: `revise_until_clean`
- Verdict source: `reviewer-1`
- Criteria: `covers-goal`
- Max revisions: `3`

### delivery_gate

- When: `after_each_delivery`
- Policy: `revise_until_clean`
- Verdict source: `reviewer-1`
- Criteria: `required-sections, covers-goal`
- Max revisions: `3`

## Loop Control

- Max iterations: `12`
- Budget: `{"tokens": 2000000, "usd": 5.0, "wall_clock_min": 30}`
- Human checkpoints: `none`
- Stop conditions:
  - all deliveries pass their gate clean
  - max_iterations reached
  - any budget cap exceeded

## Privacy

- Before sending `plan, deliveries` to `reviewer-1`, confirm consent and apply redactions `.env, .env.*, secrets/**, **/*.key`.

## Start Now

If the user asked to run now, begin at step 1 under Operator Instructions and keep going until a stop condition is reached.
