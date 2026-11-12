"""Temporary workbench for the browser regression; never uses a user's project."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cad_view_fixture import create_view_fixture

from engineering_agent import EngineeringAgent
from engineering_agent.formats import write_dxf
from engineering_agent.queue import TaskQueue
from engineering_agent.web import create_server


def main():
    root = Path(sys.argv[1]).resolve()
    project = root / "drawing"
    project.mkdir()
    write_dxf(project / "车架 test.dxf",
              lines=[{"x1": 0, "y1": 0, "x2": 10, "y2": 0, "layer": "FRAME"}],
              circles=[{"x": 5, "y": 5, "radius": 2, "layer": "WHEEL"}],
              texts=[{"x": 0, "y": 10, "text": "Bracket A", "height": 1}])
    write_dxf(project / "other.dxf", lines=[{"x1": 0, "y1": 0, "x2": 20, "y2": 0}])
    fixture = create_view_fixture(project / "curves.dxf")
    agent = EngineeringAgent({"tools": {}})
    agent.write_asset(str(project / "new.dxf"), "dxf", {"lines": [
        {"x1": 0, "y1": 0, "x2": 10, "y2": 0}]}, project_dir=project)
    queue = TaskQueue(root / "workspace.db")
    project_id = queue.create_project("CAD browser regression", str(project), {"template": "custom"})
    server = create_server(agent, queue, port=0)
    print(json.dumps({"url": f"http://127.0.0.1:{server.server_port}",
                      "project": project_id, "root": str(project), "fixture": fixture}, ensure_ascii=False), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
