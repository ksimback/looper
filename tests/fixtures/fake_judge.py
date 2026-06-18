#!/usr/bin/env python3
"""Fake judge model for Looper runner tests."""

from __future__ import annotations

import json
from pathlib import Path
import sys


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: fake_judge.py <state-file> [revise-count]", file=sys.stderr)
        return 2
    state_path = Path(sys.argv[1])
    revise_count = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state = {"count": 0}
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
    count = int(state.get("count", 0))
    state["count"] = count + 1
    state_path.write_text(json.dumps(state), encoding="utf-8")

    if count < revise_count:
        verdict = {
            "verdict": "revise",
            "blocking_issues": ["Add an explicit owner."],
            "confidence": 0.91,
            "notes": "The artifact is close but needs one concrete owner.",
        }
    else:
        verdict = {
            "verdict": "pass",
            "blocking_issues": [],
            "confidence": 0.94,
            "notes": "The artifact satisfies the rubric.",
        }
    print("```json")
    print(json.dumps(verdict))
    print("```")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

