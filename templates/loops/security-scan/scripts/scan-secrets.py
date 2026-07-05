#!/usr/bin/env python3
"""Deterministic secret / PII candidate sweep for the security-scan loop.

This is a *sweep*, not a gate. It streams the working tree (and, by default, the
git history) of a repository line by line, applies a fixed battery of regexes for
common credential shapes and PII, suppresses obvious placeholders, and writes a
JSON file of MASKED candidates for a downstream reviewer to triage.

Everything it emits is a *candidate*: a lead worth a human/agent look, not a
confirmed leak. Matched values are always masked before they touch disk or the
terminal — the full secret is never written out.

Usage:
    python scan-secrets.py <repo-dir> [--out <path>.json] [--no-history]

Exit codes:
    0 = the sweep completed. This does NOT mean "no secrets" — candidates may or
        may not have been found. Zero and non-zero candidate counts both exit 0.
    2 = bad usage / missing repo dir.

Stdlib only, Python 3.9+, cross-platform.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Pattern, Tuple

# --- Tunable skip lists ------------------------------------------------------
# Directory names that, anywhere in a path, exclude a file from the worktree scan.
SKIP_DIRS = {
    ".git",
    "node_modules",
    "dist",
    "build",
    "out",
    "vendor",
    "third_party",
    "__pycache__",
    ".venv",
    "venv",
    ".tox",
    ".mypy_cache",
    ".ipynb_checkpoints",
    "coverage",
    "target",
}

# Filename glob patterns that exclude a file from the worktree scan.
SKIP_FILE_GLOBS = [
    "*.min.js",
    "*.min.css",
    "*.map",
    "*.lock",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "*.svg",
]

MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MB
BINARY_SNIFF_BYTES = 8 * 1024  # 8 KB

# Case-insensitive substrings that mark a captured value as an obvious placeholder.
# Kept deliberately specific: a marker that also appears inside real credentials
# (e.g. "test", which suppresses "Contest2024!" or "prodtest-…") causes false
# negatives, which for a secret sweep is the worst failure mode.
PLACEHOLDER_MARKERS = [
    "example",
    "placeholder",
    "changeme",
    "your_key",
    "your-key",
    "yourkey",
    "xxxx",
    "<",
    "{{",
]

# --- Detection patterns ------------------------------------------------------
# Each entry is (pattern_id, compiled regex). Group 1, when present, is the
# specific value to mask/dedupe on; otherwise the whole match is used.
PATTERNS: List[Tuple[str, Pattern[str]]] = [
    ("aws_access_key_id", re.compile(r"\b(AKIA[0-9A-Z]{16})\b")),
    (
        "aws_secret_access_key",
        re.compile(
            r"(?i)aws.{0,20}?(?:secret|key).{0,20}?[=:]\s*['\"]?([A-Za-z0-9/+]{40})['\"]?"
        ),
    ),
    (
        "generic_secret_assignment",
        # The keyword may be embedded in a larger identifier: SECRET_KEY,
        # DB_PASSWORD, client_secret, POSTGRES_PASSWORD. Underscore is a word
        # char, so a trailing \b after the keyword would reject exactly those
        # common names; instead allow identifier chars on either side.
        re.compile(
            r"(?i)[A-Za-z0-9_]*(?:api[_-]?key|apikey|token|secret|passwd|password)"
            r"[A-Za-z0-9_]*\s*[=:]\s*['\"]?([^\s'\"]{8,})['\"]?"
        ),
    ),
    ("bearer_token", re.compile(r"(?i)bearer\s+([A-Za-z0-9._\-+/=]{8,})")),
    ("private_key_pem", re.compile(r"(-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----)")),
    (
        "github_token",
        re.compile(r"\b((?:ghp|gho|ghs|github_pat)_[A-Za-z0-9_]{20,})\b"),
    ),
    ("slack_token", re.compile(r"\b(xox[baprs]-[A-Za-z0-9-]{10,})\b")),
    ("google_api_key", re.compile(r"\b(AIza[0-9A-Za-z_\-]{35})\b")),
    (
        "jwt",
        re.compile(r"\b(eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)\b"),
    ),
    (
        "connection_string_credentials",
        re.compile(r"\b([a-zA-Z][a-zA-Z0-9+.\-]*://[^\s:/@]+:[^\s:/@]+@[^\s/]+)"),
    ),
    (
        "email_address",
        re.compile(r"\b([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})\b"),
    ),
    # Phone-like sequences are only reported when the line also mentions a
    # phone-ish keyword (checked separately) to keep noise down.
    (
        "phone_number",
        re.compile(r"(\+?\d[\d\s().\-]{7,}\d)"),
    ),
]

_PHONE_KEYWORD_RE = re.compile(r"(?i)\b(phone|tel|mobile|cell)\b")


def is_placeholder(value: str) -> bool:
    """True when the captured value looks like an obvious placeholder/example."""
    low = value.lower()
    return any(marker in low for marker in PLACEHOLDER_MARKERS)


def mask(value: str) -> str:
    """Mask a secret value for display: keep a few edge chars, elide the middle.

    The exact length is never revealed (a fixed elision, not one dot per char),
    and the mask is display-only — dedupe keys off a hash of the raw value, not
    this string, so two distinct secrets never collapse just because they mask
    the same way.
    """
    if len(value) >= 12:
        return f"{value[:4]}…{value[-4:]}"
    if len(value) >= 6:
        return f"{value[:2]}…{value[-2:]}"
    return "…"


def value_fingerprint(value: str) -> str:
    """Stable, non-reversible fingerprint of a raw value for deduping."""
    return hashlib.sha256(value.encode("utf-8", "ignore")).hexdigest()[:16]


def _skip_by_glob(name: str) -> bool:
    return any(Path(name).match(pat) for pat in SKIP_FILE_GLOBS)


def iter_worktree_files(repo: Path) -> Iterable[Path]:
    """Yield candidate files under repo, honoring the skip lists.

    Prunes skipped directories in place so the walk never descends into
    .git / node_modules / .venv / etc. (rglob would stat every entry first).
    """
    for dirpath, dirnames, filenames in os.walk(repo):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        base = Path(dirpath)
        for name in filenames:
            if _skip_by_glob(name):
                continue
            path = base / name
            if path.is_file():
                yield path


def looks_binary(path: Path) -> bool:
    """Sniff the first 8 KB for a NUL byte."""
    try:
        with path.open("rb") as fh:
            chunk = fh.read(BINARY_SNIFF_BYTES)
    except OSError:
        return True
    return b"\x00" in chunk


def scan_line(line: str) -> Iterable[Tuple[str, str]]:
    """Yield (pattern_id, captured_value) for every match in a single line."""
    for pattern_id, regex in PATTERNS:
        for m in regex.finditer(line):
            value = m.group(1) if m.groups() else m.group(0)
            if not value:
                continue
            if pattern_id == "phone_number" and not _PHONE_KEYWORD_RE.search(line):
                continue
            if is_placeholder(value):
                continue
            yield pattern_id, value


class Sweeper:
    """Accumulates deduped, masked candidates from worktree and history."""

    def __init__(self) -> None:
        self._seen: set = set()
        self.candidates: List[Dict[str, object]] = []
        self._counter = 0

    def add(
        self,
        pattern_id: str,
        value: str,
        source: str,
        path: str,
        line: Optional[int] = None,
        commit: Optional[str] = None,
    ) -> None:
        # Dedupe on a hash of the RAW value, not the masked form — masking is
        # lossy (short values elide to the same string), so masked-keyed dedupe
        # silently drops distinct secrets that happen to mask alike.
        key = (pattern_id, path, value_fingerprint(value))
        if key in self._seen:
            return
        self._seen.add(key)
        self._counter += 1
        entry: Dict[str, object] = {
            "id": f"C{self._counter}",
            "pattern": pattern_id,
            "source": source,
            "path": path,
            "excerpt": mask(value),
        }
        if source == "worktree":
            entry["line"] = line
        else:
            entry["commit"] = commit
        self.candidates.append(entry)


def sweep_worktree(repo: Path, sweeper: Sweeper) -> None:
    for path in iter_worktree_files(repo):
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        if looks_binary(path):
            continue
        rel = path.relative_to(repo).as_posix()
        try:
            with path.open("r", encoding="utf-8", errors="ignore") as fh:
                for lineno, line in enumerate(fh, start=1):
                    for pattern_id, value in scan_line(line):
                        sweeper.add(pattern_id, value, "worktree", rel, line=lineno)
        except OSError:
            continue


def sweep_history(repo: Path, sweeper: Sweeper) -> bool:
    """Stream `git log --all -p` and scan added lines. Returns True if scanned."""
    if not (repo / ".git").exists():
        return False
    if shutil.which("git") is None:
        return False

    cmd = ["git", "-C", str(repo), "log", "--all", "-p", "--unified=0", "--no-color"]
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="ignore",
            shell=False,
        )
    except OSError:
        return False

    commit = ""
    cur_path = ""
    assert proc.stdout is not None
    try:
        for raw in proc.stdout:
            line = raw.rstrip("\n")
            if line.startswith("commit "):
                commit = line.split(" ", 1)[1].strip()[:12]
                continue
            if line.startswith("+++ "):
                # +++ b/path/to/file  (or /dev/null on deletion)
                target = line[4:].strip()
                if target == "/dev/null":
                    cur_path = ""
                elif target.startswith("b/"):
                    cur_path = target[2:]
                else:
                    cur_path = target
                continue
            if line.startswith("+") and not line.startswith("+++"):
                added = line[1:]
                for pattern_id, value in scan_line(added):
                    sweeper.add(
                        pattern_id, value, "history", cur_path, commit=commit
                    )
    finally:
        if proc.stdout is not None:
            proc.stdout.close()
        proc.wait()
    return True


def summarize(sweeper: Sweeper, history_scanned: bool) -> str:
    by_source: Dict[str, int] = {}
    by_pattern: Dict[str, int] = {}
    for c in sweeper.candidates:
        by_source[str(c["source"])] = by_source.get(str(c["source"]), 0) + 1
        by_pattern[str(c["pattern"])] = by_pattern.get(str(c["pattern"]), 0) + 1
    total = len(sweeper.candidates)
    src = ", ".join(f"{k}={v}" for k, v in sorted(by_source.items())) or "none"
    pat = ", ".join(f"{k}={v}" for k, v in sorted(by_pattern.items())) or "none"
    hist = "on" if history_scanned else "off"
    return (
        f"sweep complete: {total} candidate(s) [history={hist}] | "
        f"by source: {src} | by pattern: {pat}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="scan-secrets.py",
        description="Deterministic secret / PII candidate sweep (sweep, not a gate).",
    )
    parser.add_argument("repo", help="path to the repository to sweep")
    parser.add_argument(
        "--out",
        default="candidates.json",
        help="output JSON path (default: candidates.json in CWD)",
    )
    parser.add_argument(
        "--no-history",
        action="store_true",
        help="skip the git-history scan (worktree only)",
    )
    args = parser.parse_args()

    repo = Path(args.repo)
    if not repo.is_dir():
        print(f"error: repo dir not found: {repo}", file=sys.stderr)
        return 2

    sweeper = Sweeper()
    sweep_worktree(repo, sweeper)

    history_scanned = False
    if not args.no_history:
        history_scanned = sweep_history(repo, sweeper)

    out_path = Path(args.out)
    payload = {
        "version": 1,
        "repo": str(repo),
        "history_scanned": history_scanned,
        "candidates": sweeper.candidates,
    }
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
            fh.write("\n")
    except OSError as exc:
        print(f"error: could not write {out_path}: {exc}", file=sys.stderr)
        return 2

    print(summarize(sweeper, history_scanned))
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
