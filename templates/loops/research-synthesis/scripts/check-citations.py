#!/usr/bin/env python3
"""Structural gate check for REPORT.md citations.

Verifies the synthesis report's citation discipline before the judge spends
tokens on it:
  - the file exists and is non-empty,
  - it contains at least one [source: <path>] citation,
  - every cited path exists under the sources directory,
  - every substantive paragraph (prose of ~40+ words outside headings and
    code blocks) carries at least one citation,
  - there are no leftover TBD / TODO / FIXME placeholders.

Exit 0 = structurally complete. Exit 1 = revise. Whether the citations
actually SUPPORT the claims is the judge's job, not this script's.

Usage:
    python check-citations.py <path-to-REPORT.md> --sources <sources-dir>
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

CITATION = re.compile(r"\[source:\s*([^\]]+?)\s*\]")
# `?` is not a word char, so `\b\?\?\?\b` can never match; keep `???` as its
# own alternative outside the word-boundary group.
PLACEHOLDERS = re.compile(r"\b(?:TBD|TODO|FIXME|XXX)\b|\?\?\?", re.IGNORECASE)
SUBSTANTIVE_WORDS = 40


def resolves_under_sources(raw: str, sources: Path) -> bool:
    """True only when the cited path is a real file located under `sources`.

    Guards against citing a file outside the sources tree (e.g. the model's own
    draft, or an unrelated same-basename file elsewhere): a citation of
    `inputs/sources/foo.md` (loop-dir-relative) or a bare `foo.md` must both
    land inside the sources directory to count.
    """
    sources_root = sources.resolve()
    for candidate in (Path(raw), sources / Path(raw).name):
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved.is_file() and (
            resolved.parent == sources_root or sources_root in resolved.parents
        ):
            return True
    return False


def paragraphs(text: str) -> list[str]:
    """Prose paragraphs, with fenced code blocks and headings removed."""
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    parts = re.split(r"\n\s*\n", text)
    return [p.strip() for p in parts
            if p.strip() and not p.lstrip().startswith("#")]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("--sources", type=Path, required=True)
    args = parser.parse_args()

    if not args.report.is_file():
        print(f"FAIL: report not found: {args.report}", file=sys.stderr)
        return 1

    text = args.report.read_text(encoding="utf-8", errors="ignore")
    if len(text.strip()) < 50:
        print("FAIL: report is empty or trivially short", file=sys.stderr)
        return 1

    problems = []
    cited = CITATION.findall(text)
    if not cited:
        problems.append("no [source: <path>] citations anywhere in the report")

    # 1. Every cited path must resolve to a real file UNDER the sources dir.
    #    A path that exists elsewhere (a draft, an unrelated same-name file) is
    #    not a valid citation.
    for raw in sorted(set(cited)):
        if not resolves_under_sources(raw, args.sources):
            problems.append(f"cited source is not a file under {args.sources}: '{raw}'")

    # 2. Substantive paragraphs must be cited.
    for para in paragraphs(text):
        if len(para.split()) >= SUBSTANTIVE_WORDS and not CITATION.search(para):
            problems.append(
                f"uncited substantive paragraph starting: '{para[:60]}...'")

    # 3. No placeholder text.
    placeholder_hits = PLACEHOLDERS.findall(text)
    if placeholder_hits:
        problems.append(f"contains {len(placeholder_hits)} placeholder token(s): "
                        f"{sorted(set(h.upper() for h in placeholder_hits))}")

    if problems:
        print("FAIL: REPORT.md citation discipline is incomplete:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1

    print(f"PASS: REPORT.md cites {len(set(cited))} source(s); "
          "all paths resolve and substantive paragraphs are cited.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
