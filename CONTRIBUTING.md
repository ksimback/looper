# Contributing to Looper

Thanks for helping — external bug reports and PRs have already made this
project better.

## Ground rules

- **Looper is a scaffolder, not an orchestrator.** Its own process never
  invokes a model to do loop work; it reads input, coaches, and writes files.
  PRs that blur that boundary (scheduling, durable orchestration, hidden model
  calls) will be redirected to the spec's "hand off to an orchestrator" story.
- **The loop spec is the contract.** `loop.yaml` (authoring) compiles to
  `loop.resolved.json` (execution). Changes to either format need a schema
  update in `schemas/`, compiler validation in `scripts/looper.py`, and runner
  support in `templates/run-loop.py` — in the same PR.
- **Safety invariants are non-negotiable:** argv arrays (never shell strings),
  a timeout on every external invocation, no secrets in generated files or the
  model registry, consent before any cross-vendor send, redaction defaults
  always applied.

## Dev setup

```bash
git clone https://github.com/ksimback/looper
cd looper
python -m pip install "PyYAML>=6.0"
python -m unittest discover -s tests
```

Supported floor is **Python 3.9** on Windows, macOS, and Linux. CI runs the
suite plus an example compile smoke test across that matrix; please keep both
green. If you fix a bug, add a regression test that fails without your fix.

## Pull requests

- Keep PRs focused; one concern per PR.
- Describe the failure scenario, not just the change.
- Update `CHANGELOG.md` under an "Unreleased" heading if your change is
  user-visible.
- Ports to other agent hosts are welcome as long as the spec format stays
  schema-identical — open an issue first so we can talk contract.

## Reporting bugs

Open a GitHub issue with the repro steps, your OS and Python version, and the
relevant `state.json` / `run-log.md` snippets (redact anything sensitive).
