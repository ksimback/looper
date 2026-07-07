# Changelog

All notable changes to Looper are documented here. Versions follow
[Semantic Versioning](https://semver.org/); the loop spec format is versioned
separately via `version:` in `loop.yaml` (currently `1`).

## Unreleased

### Added — `looper lint`
- `looper.py lint <loop.yaml>` — the design rubrics as a static checker, no
  wizard required. Compiles the spec first (compile rejections exit 2), then
  reports findings in two severities: **errors** for specs that will not
  behave the way they read at runtime (`judge-criterion-unreachable` — judge
  criteria on a `fixed_passes` gate are never evaluated; `unscoped-egress` —
  a cross-vendor member with no `privacy.egress` declaration;
  `egress-unknown-member` — an egress entry naming nobody) and **warnings**
  for rubric coaching (`all-vibe-verification`, `same-family-judge`,
  `delivery-gate-no-programmatic`, `non-local-member-without-egress`,
  `egress-consent-pregranted`, `cross-vendor-send-without-checkpoint`,
  `missing-max-revisions`, `no-wall-clock-cap`, `no-stop-conditions`,
  `shell-string-check`, `unresolved-placeholders`). Exit 1 on errors, or on
  any finding with `--strict`; `--json` emits machine-readable findings for
  CI.
- The wizard now runs `lint` after every compile and treats errors as
  blockers, warnings as coaching to relay (SKILL.md step 10).
- 10 new tests (37 total), including a sweep asserting all five shipped
  templates and the example lint with zero errors.

## 0.3.0 — 2026-07-05

### Added — loop pattern library
- `templates/loops/` — five named, pre-designed loops the wizard customizes
  instead of starting blank: `security-scan` (promoted from the real run
  that produced hermes-ecosystem's security fixes), `code-review`,
  `bug-hunt`, `docs-sync`, and `research-synthesis`. Each is a complete,
  compiler-validated `loop.yaml` with `{{PLACEHOLDER}}` slots, a README
  (use-when, placeholder table, customization notes), and helper check
  scripts where the pattern needs them.
- `/looper [target-dir] --template <name>` — Template Mode in the wizard:
  a compressed interview that asks only for the placeholder slots, model
  selection, and paths, while keeping the full critique, structural-rule,
  privacy, and preview flow.
- `looper.py compile` warns when unresolved `{{PLACEHOLDER}}` tokens remain
  in the resolved spec; the wizard treats the warning as an emit blocker.
- `scan-secrets.py` (security-scan template): deterministic secret/PII
  candidate sweep over working tree + full git history — streaming reads,
  directory-pruned walk, masked excerpts only, placeholder-value
  suppression. Detects underscore-joined credential names (`SECRET_KEY`,
  `DB_PASSWORD`, `client_secret`), does not suppress real secrets whose
  value merely contains `test`, and dedupes on a hash of the raw value so
  distinct secrets that mask alike are never dropped.
- Template checker scripts reject `???` placeholders, only waive
  required-field validation for a genuinely empty report (standalone
  no-findings line with no finding signal), and validate citations resolve
  to a file under the sources directory.
- 2 new tests (18 total): every template must compile (with the expected
  placeholder warning) and be listed in the catalog; the warning must
  disappear after substitution.

## 0.2.1 — 2026-07-05

### Fixed
- `install.ps1` crashed on Windows PowerShell 5.1 with "Argument expected for
  the -c option": the Python probe passed an empty string (which PowerShell
  drops for native executables) and its stderr redirect became a terminating
  error under the script's `Stop` preference. The probe now runs `--version`
  with stderr tolerated and validates the exit code (#14).

## 0.2.0 — 2026-07-05

Remediation release from a full audit of the runner, compiler, installers,
and docs. No loop spec format changes: `version: 1` loop.yaml files compile
unchanged (compiled output gains default `timeout_sec` fields on programmatic
checks and cmd context sources).

### Fixed — runner (`templates/run-loop.py`)
- `fixed_passes` gates no longer trip the no-progress detector with their own
  synthetic marker; reviewer-only gates with `max_revisions >= 2` complete
  cleanly (#11).
- Judge verdicts containing nested JSON parse correctly; trailing prose after
  the JSON block is tolerated (#11).
- A no-progress stop ends the whole run instead of only the current delivery
  attempt (#11).
- Resume works at gate boundaries: iteration restored from `state.json`,
  existing artifacts kept, wall-clock budget accrues across resumes (#11).
- Runner failures mark `state.json` as `failed` instead of leaving `running`;
  malformed specs produce friendly errors, not tracebacks (#11).
- `loop_control.human_checkpoints` (`after_plan`) is honored (#11).
- Subprocess I/O pinned to UTF-8 — fixes crashes/corruption on Windows with
  non-ASCII model output (#11).
- Example wrapper runs against its own `loop.resolved.json` (#11).
- Python 3.9/3.10 compatibility: `datetime.timezone.utc` instead of the
  3.11-only `datetime.UTC` (#9, fixes #8).

### Security
- Consent fails closed: any non-local council member requires explicit
  first-send consent even without a `privacy.egress` entry (#11).
- Context gathering honors configured privacy redactions; redaction globs
  match at any path depth (bare `.env` now covers `config/.env`); defaults
  always apply and configured globs extend them (#11).
- Workspace and context paths must stay inside the loop directory — enforced
  at compile time and again at runtime (#11, #12).
- `register-model` refuses invoke/notes values that look like credentials;
  the registry stores invocation metadata only (#12).

### Fixed — compiler (`scripts/looper.py`)
- Programmatic checks and `cmd` context sources compile with validated
  timeouts (defaults 300s / 60s) (#12).
- Empty argv arrays, negative/typed-wrong budgets, duplicate council ids,
  gate verdict sources not listed in the gate's members, and context sources
  with both/neither of `file`/`cmd` are rejected with clean errors (#12).
- Null `context_sources:` / `criteria:` / `members:` keys no longer crash
  with `TypeError` (#12).
- UTF-8 BOM tolerated on JSON/YAML reads; all generated artifacts written
  with LF newlines on every platform (#12).
- `register-model --invoke "claude -p"` (quoted, flag-bearing invocations)
  now registrable (#12).
- JSON schemas reconciled with the actual compile contract (#12).

### Added
- GitHub Actions CI: unittest + example compile smoke test across
  ubuntu/windows/macos and Python 3.9–3.13 (#10).
- 9 new regression tests (16 total).
- CHANGELOG, CONTRIBUTING, uninstall docs, and a "what Looper writes on your
  machine" section documenting `~/.looper/models.json`.

### Docs
- SKILL.md helper commands resolve the venv Python correctly on Windows
  (`.venv/Scripts/python.exe`) with POSIX and Git Bash fallbacks.
- `allowed-tools` aligned across SKILL.md and the slash command.
- Clarified token/USD budget limits as operator-advisory (#5).

## 0.1.0 — 2026-06-19

Initial public scaffold: `/looper` skill and slash command, seven-stage
design interview with rubrics, `loop.yaml` → `loop.resolved.json` compiler,
LOOP.md + RUN_IN_SESSION.md rendering, external Python runner, model
detection/registry, installers, and the `ai-workflow-mapping` example.
Early fixes: `fixed_passes` hard failure handling (#2), prompt redaction
before reviewer sends (#3), PyYAML installed with the skill venv (#4),
CLI error handling (#6).
