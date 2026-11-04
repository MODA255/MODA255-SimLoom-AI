# Engineering Agent

Offline-first AgentCAD-style runtime for CATIA, Ansys, Adams and VI-grade workflows.

The project can be used without any commercial solver installed. It provides a
canonical engineering object model, STEP/CSV/JSON/DXF/OBJ/SVG/PDF local formats,
independent validators, transaction journals, snapshots and diffs, MCP tools,
a Web API/viewer, and a Windows bridge for later vendor integration.

## Quick start

```bash
./install.sh
. .venv/bin/activate
PYTHONPATH=src python -m engineering_agent.cli inspect --root examples/demo_project
PYTHONPATH=src python -m engineering_agent.cli evaluate examples/demo_project/metrics.json examples/demo_project/rules.json
PYTHONPATH=src python -m engineering_agent.cli serve --port 8787
```

The commercial software adapters remain optional. Configure them only on a
licensed Windows workstation; the offline runtime and tests do not require them.
