# code-review

Review a branch's diff against its base and produce a typed, severity-rated
`REVIEW.md`, with a cross-model second opinion and a judge that rejects
speculation and style-nit padding.

## Use when

You want a repeatable, gated review of a feature branch before merge — every
finding located at file:line in the diff and defended by a rationale, and an
explicit statement of what was examined when the review comes back clean.

## What it emits

- `loop-workspace/review-plan.md` — file/hunk inventory, gated before review.
- `loop-workspace/REVIEW.md` — the final review report.

## Placeholders

| Token | Replace with |
|-------|--------------|
| `{{REPO_DIR}}` | Path to the repository containing the branch. |
| `{{BRANCH}}` | The branch under review. |
| `{{BASE_BRANCH}}` | The base to diff against (usually `main`). |

## Customization notes

- **PR instead of a branch:** point the context commands at
  `git -C <repo> diff <base>...<pr-head>` after fetching the PR head, or
  replace them with `gh pr diff <number>`.
- **Dropping the cross-vendor reviewer:** delete the `second-opinion` council
  member, its gate membership, and its egress entry if the diff must not
  leave the machine.
- **Review focus:** tighten the goal statement to the dimension you care
  about (e.g. security-only) rather than adding more judges.
