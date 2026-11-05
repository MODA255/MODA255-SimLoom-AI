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

Optional geometry capabilities are also isolated: install the `cadquery-ocp`
package (it provides the `OCP` Python module) or `pythonocc-core` to enable true
STEP B-Rep inspection, primitive creation, and fuse/cut/common operations:

```bash
./.venv/bin/python -m pip install -e '.[brep]'
```

Without that dependency, the agent reports the backend as unavailable and keeps
the conservative STEP entity parser.
