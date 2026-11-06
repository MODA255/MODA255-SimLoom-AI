"""Local launcher: optional native window, with a browser fallback."""
from __future__ import annotations

import argparse
import importlib
import os
import platform
import threading
import webbrowser
from pathlib import Path

from .agent import EngineeringAgent
from .queue import TaskQueue
from .web import create_server


def default_data_dir() -> Path:
    system = platform.system()
    if system == "Windows":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "SimLoom AI"
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "SimLoom AI"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "simloom-ai"


class LocalApplication:
    def __init__(self, agent: EngineeringAgent, data_dir: str | Path | None = None, port: int = 0):
        self.data_dir = Path(data_dir or default_data_dir()).expanduser().resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.server = create_server(agent, TaskQueue(self.data_dir / "workspace.db"),
                                    host="127.0.0.1", port=port, workspace_root=self.data_dir / "projects")
        # Closing a window waits for in-flight requests to finish their writes.
        self.server.daemon_threads = False
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, name="simloom-http", daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def launch(agent: EngineeringAgent, *, data_dir: str | Path | None = None, mode: str = "auto", port: int = 0) -> int:
    if mode not in {"auto", "native", "browser"}:
        raise ValueError("mode must be auto, native or browser")
    window_module = None
    if mode != "browser":
        try:
            window_module = importlib.import_module("webview")
        except ImportError:
            if mode == "native":
                print("独立窗口依赖尚未安装。请安装 .[desktop]，或使用 --mode browser。")
                return 2
    try:
        with LocalApplication(agent, data_dir, port) as app:
            print(f"SimLoom AI: {app.url}", flush=True)
            print(f"项目和历史记录：{app.data_dir}", flush=True)
            if window_module:
                try:
                    window_module.create_window("SimLoom AI", app.url, width=1440, height=960,
                                                min_size=(900, 650), background_color="#f5f7fb", text_select=True)
                    window_module.start(private_mode=False, storage_path=str(app.data_dir / "browser"))
                    return 0
                except Exception as exc:
                    if mode == "native":
                        print(f"独立窗口启动失败：{exc}")
                        return 2
                    print(f"独立窗口不可用，将打开浏览器：{exc}", flush=True)
            opened = webbrowser.open(app.url)
            if not opened:
                print("未能自动打开浏览器，请手动访问上面的本地地址。", flush=True)
            print("保持此启动窗口运行。退出时按 Ctrl+C；项目文件会保留。", flush=True)
            while app.thread.is_alive():
                app.thread.join(timeout=0.5)
    except KeyboardInterrupt:
        return 0
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="simloom-ai", description="启动 SimLoom AI 本地工作台")
    parser.add_argument("--mode", choices=["auto", "browser", "native"], default="auto")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--config", type=Path)
    args = parser.parse_args(argv)
    config = EngineeringAgent.load_config(args.config) if args.config else {"tools": {}}
    return launch(EngineeringAgent(config), data_dir=args.data_dir, mode=args.mode, port=args.port)


if __name__ == "__main__":
    raise SystemExit(main())
