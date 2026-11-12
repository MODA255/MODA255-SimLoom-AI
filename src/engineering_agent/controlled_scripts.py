"""Controlled execution for project-local CAD helper scripts.

The scripts supported here are preparation helpers around the native CAD
document. They are deliberately executed as a child process and their only
published artifact is an explicitly declared DXF output. This keeps the
operation useful for repeatable CAD automation while preserving the same
transaction, validation and history guarantees as built-in operations.

This is a policy guard, not a replacement for an OS sandbox. On Windows a
deployment that runs untrusted scripts should still use a worker account or a
container/job isolation boundary.
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any


MAX_SCRIPT_BYTES = 256 * 1024
DEFAULT_TIMEOUT_SECONDS = 30
MAX_TIMEOUT_SECONDS = 300
DEFAULT_OUTPUT_BYTES = 16 * 1024 * 1024
MAX_LOG_BYTES = 256 * 1024

_ALLOWED_IMPORTS = {
    "json", "math", "pathlib", "sys", "typing", "ezdxf",
}
_FORBIDDEN_MODULES = {
    "subprocess", "socket", "ssl", "urllib", "requests", "httpx",
    "ctypes", "multiprocessing", "threading", "asyncio", "importlib",
    "pickle", "marshal", "pty", "ftplib", "telnetlib",
}
_FORBIDDEN_CALLS = {
    "eval", "exec", "compile", "__import__", "breakpoint",
}


class ScriptPolicyError(ValueError):
    """A script was rejected before it could modify a transaction workspace."""

    code = "CAD_SCRIPT_POLICY_REJECTED"

    def __init__(self, code: str, message: str = ""):
        self.code = code
        super().__init__(f"{code}:{message}" if message else code)


def _module_name(node: ast.AST) -> str:
    if isinstance(node, ast.Import):
        return node.names[0].name.split(".", 1)[0]
    if isinstance(node, ast.ImportFrom):
        return (node.module or "").split(".", 1)[0]
    return ""


def validate_script_source(source: str, *, filename: str = "script.py") -> dict[str, Any]:
    """Parse and apply a small, explainable static policy to a Python script."""
    if len(source.encode("utf-8")) > MAX_SCRIPT_BYTES:
        raise ScriptPolicyError("CAD_SCRIPT_TOO_LARGE", f"script exceeds {MAX_SCRIPT_BYTES} bytes")
    try:
        tree = ast.parse(source, filename=filename, mode="exec")
    except SyntaxError as exc:
        raise ScriptPolicyError("CAD_SCRIPT_SYNTAX_ERROR", str(exc)) from exc
    violations: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module = _module_name(node)
            if module in _FORBIDDEN_MODULES or (module and module not in _ALLOWED_IMPORTS):
                violations.append({"code": "IMPORT_NOT_ALLOWED", "module": module, "line": node.lineno})
        if isinstance(node, ast.Call):
            called = node.func.id if isinstance(node.func, ast.Name) else (
                f"{node.func.value.id}.{node.func.attr}"
                if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
                else ""
            )
            if called in _FORBIDDEN_CALLS or called in {"os.system", "os.popen", "os.spawn", "os.execv", "shutil.rmtree"}:
                violations.append({"code": "CALL_NOT_ALLOWED", "call": called, "line": node.lineno})
    if violations:
        raise ScriptPolicyError("CAD_SCRIPT_POLICY_REJECTED", json.dumps(violations, ensure_ascii=False))
    return {"valid": True, "bytes": len(source.encode("utf-8")), "imports": sorted({
        _module_name(node) for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom)) and _module_name(node)
    })}


def _confined(root: Path, value: str | os.PathLike[str]) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        resolved = candidate.resolve(strict=False)
    else:
        resolved = (root / candidate).resolve(strict=False)
    if not resolved.is_relative_to(root.resolve()):
        raise PermissionError("CAD_SCRIPT_PATH_OUTSIDE_WORKSPACE")
    current = resolved
    while current != root.resolve():
        if current.is_symlink():
            raise PermissionError("CAD_SCRIPT_SYMLINK_PATH")
        current = current.parent
    return resolved


def _launcher_source() -> str:
    """Return a tiny launcher that installs an audit-hook path guard."""
    return textwrap.dedent(
        r'''
        import builtins
        import json
        import os
        import runpy
        import site
        import sys
        from pathlib import Path

        script, request, root = map(Path, sys.argv[1:4])
        root = root.resolve()
        runtime_roots = {Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve(),
                         Path(sys.executable).resolve().parent}
        for value in site.getsitepackages() if hasattr(site, "getsitepackages") else []:
            runtime_roots.add(Path(value).resolve())
        if hasattr(site, "getusersitepackages"):
            runtime_roots.add(Path(site.getusersitepackages()).resolve())
        for value in os.environ.get("PYTHONPATH", "").split(os.pathsep):
            if value:
                runtime_roots.add(Path(value).resolve())

        def workspace_path(value):
            if isinstance(value, int) or value is None:
                return False
            try:
                path = Path(value).resolve(strict=False)
            except (TypeError, ValueError, OSError):
                return False
            return path.is_relative_to(root)

        def readable_path(value):
            if isinstance(value, int) or value is None:
                return True
            try:
                path = Path(value).resolve(strict=False)
            except (TypeError, ValueError, OSError):
                return False
            return workspace_path(path) or any(path.is_relative_to(item) for item in runtime_roots)

        def audit(event, args):
            # ``runpy`` itself emits the generic ``exec`` audit event while
            # loading the approved script, so high-risk Python calls are
            # handled by the static policy above and the audit hook focuses on
            # process, network and filesystem escape attempts.
            if event in {"subprocess.Popen", "socket.connect", "socket.bind", "os.system", "os.popen",
                         "pty.spawn", "code.__new__"}:
                raise PermissionError("CAD_SCRIPT_OPERATION_BLOCKED:" + event)
            if event == "open":
                path = args[0] if args else None
                mode = args[1] if len(args) > 1 and isinstance(args[1], str) else "r"
                if not readable_path(path) or (any(flag in mode for flag in ("w", "a", "+", "x")) and not workspace_path(path)):
                    raise PermissionError("CAD_SCRIPT_PATH_OUTSIDE_WORKSPACE")
            if event in {"os.remove", "os.rmdir", "os.rename", "os.replace", "os.symlink", "os.link",
                         "os.mkdir", "os.makedirs", "os.chmod", "os.utime"}:
                for value in args[:2]:
                    if not workspace_path(value):
                        raise PermissionError("CAD_SCRIPT_PATH_OUTSIDE_WORKSPACE")
            if event == "os.chdir" and not workspace_path(args[0]):
                raise PermissionError("CAD_SCRIPT_PATH_OUTSIDE_WORKSPACE")

        sys.addaudithook(audit)
        os.chdir(root)
        sys.argv = [str(script), str(request)]
        runpy.run_path(str(script), run_name="__main__")
        '''
    )


def _files(root: Path) -> list[Path]:
    result: list[Path] = []
    for directory, dirs, names in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        for name in [*dirs, *names]:
            path = directory_path / name
            if path.is_symlink():
                raise ScriptPolicyError("CAD_SCRIPT_SYMLINK_PATH", str(path.relative_to(root)))
            if path.is_file():
                result.append(path)
    return result


def run_controlled_script(*, transaction_dir: Path, input_file: Path, output_name: str,
                          source: str, parameters: dict[str, Any] | None = None,
                          timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
                          max_output_bytes: int = DEFAULT_OUTPUT_BYTES) -> dict[str, Any]:
    """Run a project-local script in a transaction-owned workspace.

    The returned ``output_file`` is still inside ``transaction_dir`` and can
    be passed directly to :meth:`RunTransaction.commit`.
    """
    policy = validate_script_source(source)
    timeout = max(1, min(int(timeout_seconds), MAX_TIMEOUT_SECONDS))
    max_output_bytes = max(1024, min(int(max_output_bytes), 128 * 1024 * 1024))
    workspace = transaction_dir / "controlled-cad-script"
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    input_copy = workspace / "input.dxf"
    script_copy = workspace / "script.py"
    request_path = workspace / "request.json"
    launcher_path = workspace / "launcher.py"
    output_path = _confined(workspace, output_name)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(input_file, input_copy)
    script_copy.write_text(source, encoding="utf-8")
    launcher_path.write_text(_launcher_source(), encoding="utf-8")
    request = {
        "schema_version": "simloom-ai/cad-script/v1",
        "input_file": "input.dxf",
        "output_file": output_name,
        "allowed_outputs": [output_name],
        "parameters": parameters or {},
    }
    request_path.write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
    stdout_path, stderr_path = workspace / "stdout.log", workspace / "stderr.log"
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONNOUSERSITE": "1",
    }
    # Carry only installed dependency directories into the child.  This keeps
    # the project checkout and the caller's arbitrary module search path out
    # of the script environment while still working when SimLoom is launched
    # from a source checkout with an explicit site-packages path.
    dependency_paths = [value for value in sys.path
                        if value and "site-packages" in value and Path(value).is_dir()]
    if dependency_paths:
        env["PYTHONPATH"] = os.pathsep.join(dependency_paths)
    command = [sys.executable, str(launcher_path), str(script_copy), str(request_path), str(workspace)]
    try:
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            completed = subprocess.run(command, cwd=workspace, env=env, stdout=stdout, stderr=stderr,
                                       timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise ScriptPolicyError("CAD_SCRIPT_TIMEOUT", f"script exceeded {timeout} seconds") from exc
    stdout = stdout_path.read_bytes()[:MAX_LOG_BYTES].decode("utf-8", errors="replace")
    stderr = stderr_path.read_bytes()[:MAX_LOG_BYTES].decode("utf-8", errors="replace")
    if completed.returncode != 0:
        code = "CAD_SCRIPT_BLOCKED" if "CAD_SCRIPT_" in stderr else "CAD_SCRIPT_FAILED"
        raise ScriptPolicyError(code, stderr.strip() or stdout.strip() or f"script exited {completed.returncode}")
    files = _files(workspace)
    reserved = {input_copy, script_copy, request_path, launcher_path, stdout_path, stderr_path}
    undeclared = [str(path.relative_to(workspace)) for path in files if path not in reserved and path != output_path]
    if undeclared:
        raise ScriptPolicyError("CAD_SCRIPT_UNDECLARED_OUTPUT", json.dumps(sorted(undeclared)))
    if not output_path.is_file():
        raise ScriptPolicyError("CAD_SCRIPT_OUTPUT_MISSING", output_name)
    if output_path.stat().st_size > max_output_bytes:
        raise ScriptPolicyError("CAD_SCRIPT_OUTPUT_TOO_LARGE", output_name)
    return {"workspace": str(workspace), "output_file": str(output_path), "request_file": str(request_path),
            "stdout": stdout, "stderr": stderr, "returncode": completed.returncode, "policy": policy,
            "output_bytes": output_path.stat().st_size, "command": command}
