"""Read verified historical DXF snapshots without restoring or executing edits."""
from pathlib import Path

from .cad_history import CadHistory
from .cad_model import CadDocument
from .cad_render import native_model_scene
from .cad_viewer import render_cad_viewer
from .transactions import RunTransaction, confined_path


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
