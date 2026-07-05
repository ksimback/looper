# bug-hunt

Reproduce a reported bug, fix the root cause, and prove it: the repro command
is observed failing before the change (plan gate) and passing after it
(delivery gate), with the full test suite still green.

## Use when

You have a bug report and a way to trigger the bug from the command line, and
you want the fix held to a before/after evidence standard instead of "the
error went away".

## What it emits

- `loop-workspace/fix-plan.md` — includes the verbatim failing repro output.
- `loop-workspace/FIX-REPORT.md` — root cause, fix at file:line,
  before/after evidence.
- The fix itself, applied to the target repo's working tree (never
  committed — committing stays your call).

## Placeholders

| Token | Replace with |
|-------|--------------|
| `{{REPO_DIR}}` | Path to the repository containing the bug. |
| `{{REPRO_CMD}}` | Argv array that triggers the bug, e.g. `["python", "-m", "pytest", "tests/test_x.py::test_bug", "-x"]`. Replace the whole one-element list. |
| `{{TEST_CMD}}` | Argv array for the full test suite, e.g. `["python", "-m", "pytest"]`. Replace the whole one-element list. |

Also: put the bug report (the issue text, stack trace, expected vs actual)
in `inputs/bug-report.md` inside the emitted loop directory.

## Customization notes

- **No command-line repro yet?** Write the repro as a small script first and
  point `{{REPRO_CMD}}` at it — a bug you can't trigger deterministically
  can't be gated programmatically, and this template's value collapses to
  judge-only.
- The loop edits the target repo's working tree under side-effect approval;
  run it on a clean branch so the diff is easy to review and revert.
