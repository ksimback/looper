# Model Detection and Privacy Notes

Looper detection is intentionally dumb and transparent. It stores invocation
metadata only, never credentials.

## Registry

Default registry path:

```text
~/.looper/models.json
```

Registry entries should look like:

```json
{
  "claude": {
    "cli": "claude",
    "invoke": ["claude", "-p"],
    "available": true,
    "authed": true,
    "local": false
  }
}
```

`authed` means the basic probe command exited cleanly. It is a convenience
signal, not a guarantee that a future paid model call will succeed.

## Default Redactions

- `.env`
- `.env.*`
- `secrets/**`
- `**/*.key`

Add project-specific globs for customer data, private transcripts, or internal
design docs before sending anything to a non-local council member.

## Local Model UX

Surface `ollama` as the privacy-preserving option when present. It may be lower
quality than frontier hosted models, but it keeps council review in-house.

