"""Black-box stdio coverage for an external MCP client round trip."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from engineering_agent.formats import write_dxf


def _request(process: subprocess.Popen[str], payload: dict) -> dict:
    assert process.stdin is not None and process.stdout is not None
    process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
    process.stdin.flush()
    line = process.stdout.readline()
    assert line, process.stderr.read() if process.stderr is not None else "MCP server exited"
    return json.loads(line)


def test_external_stdio_client_discovers_and_calls_agentcad_alias(tmp_path: Path):
    source = tmp_path / "source.dxf"
    output = tmp_path / "alias-output.dxf"
    write_dxf(source, lines=[{"x1": 0, "y1": 0, "x2": 1, "y2": 0}])
    process = subprocess.Popen(
        [sys.executable, "-m", "engineering_agent.cli", "--project-dir", str(tmp_path), "mcp", "--profile", "full"],
        cwd=tmp_path,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        initialized = _request(process, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                        "clientInfo": {"name": "external-fixture", "version": "1"}},
        })
        assert initialized["result"]["protocolVersion"] == "2025-03-26"
        listed = _request(process, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {"limit": 500}})
        alias = next(item for item in listed["result"]["tools"] if item["name"] == "operation.add_line")
        assert alias["inputSchema"]["x-operation-schema-version"] == "1.0"
        called = _request(process, {
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "operation.add_line", "arguments": {
                "project_dir": str(tmp_path), "input_file": str(source), "output_file": str(output),
                "p1": {"x": 2, "y": 0}, "p2": {"x": 3, "y": 1},
            }},
        })
        assert called["result"].get("isError", False) is False
        assert called["result"]["structuredContent"]["operation"] == "cad.line_create"
        assert output.is_file()
    finally:
        if process.stdin is not None:
            process.stdin.close()
        process.terminate()
        process.wait(timeout=10)
