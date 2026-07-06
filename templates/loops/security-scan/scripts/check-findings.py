#!/usr/bin/env python3
"""Structural gate check for SECURITY-FINDINGS.md.

Verifies the report is well-formed before the judge spends tokens on it:
  - the file exists and is non-empty,
  - it covers all three required areas (secrets, PII, vulnerabilities),
  - every finding row carries Type, Severity, Location, and Remediation,
  - there are no leftover TBD / TODO / FIXME placeholders.

Exit 0 = structurally complete. Exit 1 = revise. This is deliberately format-tolerant:
it accepts either a Markdown table or repeated labeled blocks, so the host is not forced
into one exact layout.

Usage:
    python check-findings.py <path-to-SECURITY-FINDINGS.md>
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REQUIRED_AREAS = ["secret", "pii", "vulnerab"]  # substrings, case-insensitive
REQUIRED_FIELDS = ["type", "severity", "location", "remediation"]
SEVERITY_SCALE = re.compile(r"\b(critical|high|medium|low|major|minor)\b", re.IGNORECASE)
FILE_LINE = re.compile(r"\S+:\d+")
# `?` is not a word char, so `\b\?\?\?\b` can never match; keep `???` as its
# own alternative outside the word-boundary group.
PLACEHOLDERS = re.compile(r"\b(?:TBD|TODO|FIXME|XXX)\b|\?\?\?", re.IGNORECASE)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: check-findings.py <file>", file=sys.stderr)
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

    # 1. All three security areas mentioned.
    for area in REQUIRED_AREAS:
        if area not in lower:
            problems.append(f"missing required area keyword: '{area}'")

    # 2. A report with confirmed findings must expose all required fields
    #    somewhere. Fields are waived only for a genuine empty report: a
    #    standalone no-findings declaration line AND no finding signal (a
    #    severity term or a file:line). A prose "no secrets in git history"
    #    inside a report full of findings must not waive validation.
    declares_empty = bool(re.search(
        r"(?m)^[\s>*#-]*no (?:confirmed |material )?(?:findings|secrets|vulnerabilities|issues)\b",
        lower))
    has_finding_signal = bool(SEVERITY_SCALE.search(text) or FILE_LINE.search(text))
    declares_no_findings = declares_empty and not has_finding_signal
    if not declares_no_findings:
        for field in REQUIRED_FIELDS:
            if field not in lower:
                problems.append(f"missing required field label: '{field}'")

    # 3. No placeholder text.
    placeholder_hits = PLACEHOLDERS.findall(text)
    if placeholder_hits:
        problems.append(f"contains {len(placeholder_hits)} placeholder token(s): "
                        f"{sorted(set(h.upper() for h in placeholder_hits))}")

    if problems:
        print("FAIL: SECURITY-FINDINGS.md is not structurally complete:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1

    status = "no-findings report" if declares_no_findings else "findings report"
    print(f"PASS: SECURITY-FINDINGS.md structurally complete ({status}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
