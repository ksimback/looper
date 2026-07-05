#!/usr/bin/env python3
"""Structural gate check for FIX-REPORT.md.

Verifies the fix report is well-formed before the judge spends tokens on it:
  - the file exists and is non-empty,
  - it states a root cause, the fix location, and before/after evidence,
  - the fix location includes at least one file:line reference,
  - there are no leftover TBD / TODO / FIXME placeholders.

Exit 0 = structurally complete. Exit 1 = revise. This is deliberately
format-tolerant: it accepts either sections or labeled blocks, so the host
is not forced into one exact layout.

Usage:
    python check-fix-report.py <path-to-FIX-REPORT.md>
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REQUIRED_LABELS = ["root cause", "fix", "before", "after"]
FILE_LINE = re.compile(r"\S+:\d+")
# `?` is not a word char, so `\b\?\?\?\b` can never match; keep `???` as its
# own alternative outside the word-boundary group.
PLACEHOLDERS = re.compile(r"\b(?:TBD|TODO|FIXME|XXX)\b|\?\?\?", re.IGNORECASE)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: check-fix-report.py <file>", file=sys.stderr)
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

    for label in REQUIRED_LABELS:
        if label not in lower:
            problems.append(f"missing required label: '{label}'")

    if not FILE_LINE.search(text):
        problems.append("no file:line reference for the fix location")

    placeholder_hits = PLACEHOLDERS.findall(text)
    if placeholder_hits:
        problems.append(f"contains {len(placeholder_hits)} placeholder token(s): "
                        f"{sorted(set(h.upper() for h in placeholder_hits))}")

    if problems:
        print("FAIL: FIX-REPORT.md is not structurally complete:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1

    print("PASS: FIX-REPORT.md structurally complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
