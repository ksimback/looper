# docs-sync

Detect drift between documentation and the code it describes, fix the docs,
and account for every drift item — fixed, or deferred with a reason. Docs
follow code; the code is never modified.

## Use when

Documentation has fallen behind the code — commands, flags, defaults, paths,
or behavior claims no longer match — and you want the cleanup done with an
auditable per-item report instead of a silent rewrite.

## What it emits

- `loop-workspace/sync-plan.md` — doc inventory + likely drift hot spots.
- `loop-workspace/DRIFT-REPORT.md` — every drift item with doc location,
  code location, description, and status.
- Doc fixes, applied to the target repo's working tree (never committed).

## Placeholders

| Token | Replace with |
|-------|--------------|
| `{{REPO_DIR}}` | Path to the repository. |
| `{{DOCS_DIR}}` | Docs path relative to the repo root, e.g. `docs` or `README.md`. |
| `{{SRC_DIR}}` | Code path relative to the repo root, e.g. `src`. |

## Customization notes

- **Multiple doc surfaces** (README + docs/ + CLI help text): list them all
  in the `ls-files` context command and name them in the goal statement.
- The judge verifies fixed items against the code itself, not the report's
  claims — keep `{{SRC_DIR}}` scoped to what the docs actually describe so
  verification stays cheap.
- Run on a clean branch; doc edits go through side-effect approval and stay
  uncommitted for your review.
