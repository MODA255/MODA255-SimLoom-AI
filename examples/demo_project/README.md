# Offline demo project

This fixture does not require CATIA, Ansys, Adams or VI-grade. It exercises the
local object model, STEP/CSV/JSON readers, independent validators, versioning,
diffs and metric evaluation.

```bash
PYTHONPATH=src python3 -m engineering_agent.cli inspect --root examples/demo_project
PYTHONPATH=src python3 -m engineering_agent.cli evaluate examples/demo_project/metrics.json examples/demo_project/rules.json
PYTHONPATH=src python3 -m engineering_agent.cli version baseline examples/demo_project/model.step examples/demo_project/metrics.json --root examples/demo_project
```
