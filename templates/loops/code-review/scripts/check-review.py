#!/usr/bin/env python3
"""Structural gate check for REVIEW.md.

Verifies the review report is well-formed before the judge spends tokens on it:
  - the file exists and is non-empty,
  - it names the reviewed range (base and branch),
  - every finding carries Type, Severity, Location (file:line), and Rationale,
  - severities use the agreed scale (critical / major / minor),
  - there are no leftover TBD / TODO / FIXME placeholders.

Exit 0 = structurally complete. Exit 1 = revise. This is deliberately
format-tolerant: it accepts either a Markdown table or repeated labeled
blocks, so the host is not forced into one exact layout. A review that
explicitly declares zero findings passes without finding fields, as long as
it states what was checked.

Usage:
    python check-review.py <path-to-REVIEW.md>
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REQUIRED_FIELDS = ["type", "severity", "location", "rationale"]
SEVERITY_SCALE = re.compile(r"\b(critical|major|minor)\b", re.IGNORECASE)
FILE_LINE = re.compile(r"\S+:\d+")
# `?` is not a word char, so `\b\?\?\?\b` can never match; keep `???` as its
# own alternative outside the word-boundary group.
PLACEHOLDERS = re.compile(r"\b(?:TBD|TODO|FIXME|XXX)\b|\?\?\?", re.IGNORECASE)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: check-review.py <file>", file=sys.stderr)
        return 1

    path = Path(sys.argv[1])
    if not path.is_file():
        print(f"FAIL: report not found: {path}", file=sys.stderr)
        return 1

    text = path.read_text(encoding="utf-8", errors="ignore")
    if len(text.strip()) < 50:
        print("FAIL: report is empty or trivially short", file=sys.stderr)
        return 1

    lower = text.lower()
    problems = []

    # 1. The reviewed range is named.
    if not re.search(r"\b(diff|branch|range|against|\.\.\.?)\b", lower):
        problems.append("report never names the reviewed range (branch/base/diff)")

    # 2. A report with findings must expose all required fields somewhere.
    #    If the report explicitly declares no findings, fields are not required —
    #    but only when it's a genuine empty report: a standalone declaration line
    #    AND no finding signal (a severity term or a file:line). Otherwise an
    #    incidental "no issues in module X" sentence would waive validation.
    declares_empty = bool(re.search(
        r"(?m)^[\s>*#-]*no (?:confirmed |material )?(?:findings|issues|defects|problems)\b",
        lower))
    has_finding_signal = bool(SEVERITY_SCALE.search(text) or FILE_LINE.search(text))
    declares_no_findings = declares_empty and not has_finding_signal
    if not declares_no_findings:
        for field in REQUIRED_FIELDS:
            if field not in lower:
                problems.append(f"missing required field label: '{field}'")
        if not SEVERITY_SCALE.search(text):
            problems.append("no severity from the scale critical/major/minor")
        if not FILE_LINE.search(text):
            problems.append("no file:line location anywhere in the report")

    # 3. No placeholder text.
    placeholder_hits = PLACEHOLDERS.findall(text)
    if placeholder_hits:
        problems.append(f"contains {len(placeholder_hits)} placeholder token(s): "
                        f"{sorted(set(h.upper() for h in placeholder_hits))}")

    if problems:
        print("FAIL: REVIEW.md is not structurally complete:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1

    status = "no-findings report" if declares_no_findings else "findings report"
    print(f"PASS: REVIEW.md structurally complete ({status}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
