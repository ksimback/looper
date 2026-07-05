# research-synthesis

Synthesize a folder of source documents into a cited report that answers a
research question. Citation discipline is enforced programmatically; a judge
spot-checks that citations actually support their claims and that source
disagreements surface instead of being averaged away.

## Use when

You have collected source material (notes, papers, transcripts, exported
docs) and want a synthesis you can trust — every claim traceable to a file,
gaps stated instead of papered over.

## What it emits

- `loop-workspace/synthesis-plan.md` — per-source notes + report structure.
- `loop-workspace/REPORT.md` — the cited synthesis.

## Placeholders

| Token | Replace with |
|-------|--------------|
| `{{RESEARCH_QUESTION}}` | The question the report must answer, in one sentence. |

Also: put the source documents in `inputs/sources/` inside the emitted loop
directory (plain-text formats work best), and create
`inputs/sources/MANIFEST.md` — a one-line-per-file list of what each source
is and where it came from. The manifest is the loop's context anchor; the
host reads the sources themselves from disk.

## Customization notes

- **Citation format** is `[source: inputs/sources/<file>]`, enforced by
  `scripts/check-citations.py`. If you change the format, change it in both
  the goal statement and the script.
- **Web research is out of scope by design** — this loop synthesizes what
  you already collected, which keeps verification honest. Gather first,
  then loop.
- If sources are sensitive, note the judge egress entry: the plan, report,
  and source excerpts go to the judge CLI under the default redaction
  globs, consent-gated on first send.
