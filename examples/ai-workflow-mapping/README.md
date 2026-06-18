# AI Workflow Mapping Example

This example shows the Looper artifact shape for mapping customer process notes
into an agent-ready workflow.

Compile after editing:

```bash
python ../../scripts/looper.py compile loop.yaml --out loop.resolved.json --render LOOP.md
```

Run only after reviewing the model invocations and privacy egress:

```bash
python run-loop.py
```

