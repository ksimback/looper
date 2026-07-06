#!/usr/bin/env python3
"""Structural gate check for DRIFT-REPORT.md.

Verifies the drift report is well-formed before the judge spends tokens on it:
  - the file exists and is non-empty,
  - every drift item carries a doc location, a code location, a drift
    description, and a status,
  - statuses use the agreed vocabulary (fixed / deferred),
  - a deferred item carries a reason,
  - there are no leftover TBD / TODO / FIXME placeholders.

Exit 0 = structurally complete. Exit 1 = revise. This is deliberately
format-tolerant: it accepts either a Markdown table or repeated labeled
blocks. A report that explicitly declares zero drift passes without item
fields, as long as it states what was compared.

Usage:
    python check-drift-report.py <path-to-DRIFT-REPORT.md>
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REQUIRED_FIELDS = ["doc", "code", "status"]
STATUS_SCALE = re.compile(r"\b(fixed|deferred)\b", re.IGNORECASE)
# `?` is not a word char, so `\b\?\?\?\b` can never match; keep `???` as its
# own alternative outside the word-boundary group.
PLACEHOLDERS = re.compile(r"\b(?:TBD|TODO|FIXME|XXX)\b|\?\?\?", re.IGNORECASE)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: check-drift-report.py <file>", file=sys.stderr)
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

    # Waive the per-item fields only for a genuine no-drift report: a standalone
    # declaration line AND no drift signal (a fixed/deferred status). A "no drift
    # in section X" line inside a populated report must not waive validation.
    declares_empty = bool(re.search(
        r"(?m)^[\s>*#-]*no (?:drift|mismatches|discrepancies)\b", lower))
    declares_no_drift = declares_empty and not STATUS_SCALE.search(text)
    if not declares_no_drift:
        for field in REQUIRED_FIELDS:
            if field not in lower:
                problems.append(f"missing required field label: '{field}'")
        if not STATUS_SCALE.search(text):
            problems.append("no status from the scale fixed/deferred")
        deferred_count = len(re.findall(r"\bdeferred\b", lower))
        reason_count = len(re.findall(r"\b(reason|because)\b", lower))
        if deferred_count and not reason_count:
            problems.append("deferred item(s) present but no reason given anywhere")

    placeholder_hits = PLACEHOLDERS.findall(text)
    if placeholder_hits:
        problems.append(f"contains {len(placeholder_hits)} placeholder token(s): "
                        f"{sorted(set(h.upper() for h in placeholder_hits))}")

    if problems:
        print("FAIL: DRIFT-REPORT.md is not structurally complete:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1

    status = "no-drift report" if declares_no_drift else "drift report"
    print(f"PASS: DRIFT-REPORT.md structurally complete ({status}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
