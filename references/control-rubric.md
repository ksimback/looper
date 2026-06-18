# Control Rubric

Use this when setting gates, iteration caps, budgets, and stop conditions.

## Required Guards

- `loop_control.max_iterations`
- `gates.*.max_revisions`
- At least one wall-clock, token, or USD budget cap when external models run.
- A stop condition that describes success.

## Good Gate Design

- Plan gate runs before delivery work.
- Delivery gate runs after each delivery artifact.
- Programmatic checks run before judge calls when possible.
- Human checkpoints sit at high-leverage points, usually after plan approval or
  before external egress.
- Resume happens at gate boundaries unless the user explicitly needs finer
  granularity.

## Failure Behavior

- Stop immediately when a hard cap is reached.
- Write the latest state to `loop-workspace/state.json`.
- Preserve review notes even when the gate fails.
- Do not let the host keep revising forever.

## Anti-Patterns

- No maximum iteration count.
- A judge gate with no judge.
- A budget cap in prose but not in `loop_control`.
- Human signoff required but no checkpoint.
- Stop conditions that require subjective self-satisfaction.

