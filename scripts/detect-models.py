#!/usr/bin/env python3
"""Compatibility wrapper for the spec-named detect-models helper."""

from __future__ import annotations

import sys

from looper import main


if __name__ == "__main__":
    raise SystemExit(main(["detect-models", *sys.argv[1:]]))
