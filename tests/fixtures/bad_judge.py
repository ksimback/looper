#!/usr/bin/env python3
"""Fake judge that emits unparseable output."""

from __future__ import annotations


def main() -> int:
    print("Looks pretty good, but this is not JSON.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

