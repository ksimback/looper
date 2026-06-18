#!/usr/bin/env python3
"""Fake host model for Looper runner tests."""

from __future__ import annotations

import sys


def main() -> int:
    prompt = sys.stdin.read()
    if "Revise the artifact" in prompt:
        print("Revised artifact")
        print()
        print("Owner: host")
        print("Resolved reviewer issue.")
        print("No TBD")
    elif "Draft plan.md" in prompt:
        print("# Plan")
        print()
        print("Owner: host")
        print("1. Read the context.")
        print("2. Produce the delivery.")
        print("No TBD")
    else:
        print("# LOOP.md")
        print()
        print("Owner: host")
        print("Diagram: included")
        print("No TBD")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

