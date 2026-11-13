"""Read verified historical DXF and batch snapshots without changing a project."""
import hashlib
import json
import re
from pathlib import Path

from .cad_history import CadHistory
from .cad_model import CadDocument
from .cad_render import native_model_scene
from .cad_viewer import render_cad_viewer
from .transactions import RunTransaction, confined_path


def batch_step_view(project_dir, run_id, step_index, side="after"):
    """Read and render one immutable checkpoint from a CAD operation batch."""
    root = Path(project_dir).expanduser().resolve()
    if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", run_id):
        raise ValueError("CAD_BATCH_INVALID_RUN_ID")
    if side not in {"before", "after"}:
        raise ValueError("CAD_BATCH_INVALID_SIDE")
    try:
        index = int(step_index)
    except (TypeError, ValueError) as exc:
        raise ValueError("CAD_BATCH_INVALID_STEP") from exc
    if index < 0:
        raise ValueError("CAD_BATCH_INVALID_STEP")
    run_dir = confined_path(root, f"artifacts/runs/{run_id}")
    checkpoint = confined_path(run_dir, f"checkpoints/step-{index:03d}-{side}/state.json")
    if not checkpoint.is_file():
        raise FileNotFoundError(checkpoint)
    try:
        payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("CAD_BATCH_CHECKPOINT_CORRUPT") from exc
    if payload.get("kind") != "cad-batch-step" or payload.get("run_id") != run_id or payload.get("step") != index or payload.get("stage") != side:
        raise RuntimeError("CAD_BATCH_CHECKPOINT_INVALID")
    document_payload = payload.get("document")
    document = CadDocument.from_dict(document_payload)
    geometry = document.to_dxf()
    try:
        scene = native_model_scene(geometry)
    except (RuntimeError, TypeError, ValueError) as exc:
        # A canonical checkpoint can contain vendor/native entities that the
        # editable ezdxf builder cannot recreate without the original file.
        # Keep replay read-only and useful with the semantic fallback renderer
        # instead of making the whole checkpoint unavailable.
        from .viewer import render_dxf_svg
        scene = {
            "svg": render_dxf_svg(geometry),
            "layers": {str(name): {"visible": bool(item.visible), "locked": bool(item.locked)}
                       for name, item in document.layers.items()},
            "warnings": [{"code": "CHECKPOINT_NATIVE_ENTITY_FALLBACK", "message": str(exc)}],
            "entity_count": len(geometry.get("entities", [])),
            "rendered_entities": len([item for item in geometry.get("entities", [])
                                       if str(item.get("type", "")).upper() in {"LINE", "RAY", "XLINE", "POINT", "CIRCLE", "ARC", "LWPOLYLINE", "POLYLINE", "ELLIPSE", "SPLINE", "TEXT", "MTEXT"}]),
        }
    return {
        "read_only": True,
        "run_id": run_id,
        "step_index": index,
        "side": side,
        "operation": payload.get("operation"),
        "checkpoint": str(checkpoint.relative_to(root)),
        "checkpoint_sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
        "document": document.to_dict(),
        "diff": payload.get("diff"),
        "validation": payload.get("validation"),
        "scene": scene,
        "viewer": render_cad_viewer(geometry, scene=scene, read_only=True),
    }


def history_view(project_dir, input_file, revision_id, side="after"):
    root = Path(project_dir).expanduser().resolve()
    path = Path(input_file).expanduser()
    if path.is_absolute():
        if not path.is_relative_to(root):
            raise ValueError("PATH_OUTSIDE_PROJECT")
        path = path.relative_to(root)
    drawing = confined_path(root, str(path))
    if drawing.suffix.lower() != ".dxf":
        raise ValueError("CAD_HISTORY_REQUIRES_DXF")
    with RunTransaction.read_lock(root) as transaction:
        history = CadHistory(transaction, drawing)
        snapshot = history.snapshot(revision_id, side)
        document = CadDocument.open(snapshot["path"]) if snapshot["exists"] else None
        geometry = document.to_dxf() if document else {"entities": [], "units": "mm"}
        native_path = snapshot["path"] if document and document._source_path else None
        scene = native_model_scene(geometry, source_path=native_path)
        description = ("历史图纸 · 只读；查看不会执行操作或改变撤销位置。" if document
                       else "此时文件尚不存在 · 只读；当前项目文件未改变。")
        return {"path": str(path), "read_only": True,
                "snapshot": {key: value for key, value in snapshot.items() if key != "path"},
                "document": document.to_dict() if document else None,
                "history": history.summary(), "scene": scene, "preview_note": description,
                "viewer": render_cad_viewer(geometry, scene=scene, read_only=True)}
