# Offline four-tool demo

This fixture runs the CATIA → Ansys → Adams → VI-grade workflow without
commercial software. The checked-in files provide one shared model spec and
one cross-tool metric rule set; generated artifacts are written under
`artifacts/generated/` and are ignored by Git.

```bash
PYTHONPATH=src python -m engineering_agent.cli \
  --project-dir ./examples/offline_demo offline-run \
  --model-spec "$(cat examples/offline_demo/model_spec.json)" \
  --rules examples/offline_demo/rules.json
```

The output is a deterministic runtime and artifact-contract check. It is not a
replacement for a licensed CATIA, Ansys, Adams or VI-grade solve.
