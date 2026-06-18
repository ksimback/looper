#!/usr/bin/env python3
"""Exit zero when a file contains the requested text."""

from __future__ import annotations

from pathlib import Path
import sys


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: check_contains.py <path> <text>", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    needle = sys.argv[2]
    if needle in path.read_text(encoding="utf-8"):
        return 0
    print(f"{needle!r} not found in {path}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

