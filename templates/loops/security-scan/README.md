# security-scan

Read-only security sweep of a repository: secrets, PII, and exploitable
vulnerabilities, triaged into a findings report. This is the pattern Looper's
own proof-of-concept run used against a real repo.

## Use when

You want a bounded, evidence-first security review of a repo — every candidate
from a deterministic sweep either confirmed (severity + location + remediation)
or dismissed with a reason, and every claimed vulnerability defended as
actually exploitable.

## What it emits

- `loop-workspace/candidates.json` — deterministic secret/PII sweep output
  (working tree + git history, masked excerpts).
- `loop-workspace/SECURITY-FINDINGS.md` — the triaged final report.

## Placeholders

| Token | Replace with |
|-------|--------------|
| `{{TARGET_REPO}}` | Clone URL (or local path) of the repository to scan. |

## Customization notes

- **Local repo instead of a URL:** replace the `git clone` context command
  with a clone from a local path, or point the scan at an existing checkout —
  keep it inside `loop-workspace/` so the loop stays read-only toward the
  original.
- **Dropping the cross-vendor reviewer:** delete the `vuln-reviewer` council
  member, its gate membership, and its egress entry if the code must not
  leave the machine. The judge alone still gates all four criteria.
- The gate criteria assume the two helper scripts in `scripts/` are copied
  next to the emitted loop (the wizard does this automatically).
