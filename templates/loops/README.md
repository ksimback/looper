# Loop pattern library

Named, pre-designed loops the Looper wizard customizes instead of starting
from a blank interview:

```
/looper my-loop-dir --template <name>
```

Each template is a complete, compilable `loop.yaml` plus a README describing
when to use it and which `{{PLACEHOLDER}}` slots to fill. Templates with a
`scripts/` directory ship helper checkers that the wizard copies next to the
emitted loop. A template is a pre-answered interview, not a bypass: the
wizard still critiques every stage, enforces the structural rules, and shows
the flow preview before emitting.

| Template | Use when |
|----------|----------|
| [security-scan](security-scan/) | Read-only sweep of a repo for secrets, PII, and vulnerabilities → triaged `SECURITY-FINDINGS.md`. |
| [code-review](code-review/) | Review a branch's diff against its base → typed, severity-rated `REVIEW.md` grounded in the diff. |
| [bug-hunt](bug-hunt/) | Reproduce a reported bug, fix the root cause, prove it with before/after repro evidence. |
| [docs-sync](docs-sync/) | Find and fix doc/code drift → per-item `DRIFT-REPORT.md`; docs follow code, code untouched. |
| [research-synthesis](research-synthesis/) | Synthesize collected sources into a cited `REPORT.md`; every claim traceable to a file. |

## Conventions

- `{{PLACEHOLDER}}` tokens mark the project-specific slots; the README table
  in each template says what goes where. `looper.py compile` warns if any
  remain, and the wizard refuses to emit while one survives.
- Every template compiles as-is (CI enforces this), so the catalog can't
  drift from the compiler's contract.
- Templates that edit files (`bug-hunt`, `docs-sync`) do so under
  side-effect approval and never commit; read-only templates say so in
  their `execution.side_effects.notes`.
- Programmatic checks invoke the interpreter as `python` (matching the
  repo's example loop). On a machine where only `python3` is on PATH, the
  wizard should swap the interpreter token when it customizes the template.

## Adding a template

Add `templates/loops/<name>/` with `loop.yaml` (compilable, placeholders
only inside string values), `README.md` (use-when, emits, placeholder
table), and optional `scripts/`. Add a row to the table above. The test
suite compiles every template automatically.
