from pathlib import Path
from typing import Any


def capture_task_screenshot(origin: str, task_id: str, output_dir: str | Path, *, storage_state_path: str | Path | None = None, route_template: str = "/create/vfc-library?task_id={task_id}", page_factory: Any = None) -> dict:
    """只读打开任务页面并截图；失败返回明确状态，不改变任务结果。"""
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"task-{task_id}.png"
    url = origin.rstrip('/') + route_template.format(task_id=task_id)
    try:
        if page_factory is not None:
            page = page_factory(url)
            page.screenshot(path=str(path), full_page=True)
        else:
            from playwright.sync_api import sync_playwright
            state = None
            if storage_state_path and Path(storage_state_path).exists():
                state = str(storage_state_path)
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True, args=['--no-sandbox'])
                context = browser.new_context(storage_state=state) if state else browser.new_context()
                page = context.new_page()
                page.goto(url, wait_until='networkidle', timeout=60_000)
                page.screenshot(path=str(path), full_page=True)
                browser.close()
        return {'status': 'collected', 'path': str(path), 'url': url}
    except Exception as exc:
        return {'status': 'failed', 'path': None, 'url': url, 'error': f'{type(exc).__name__}: {exc}'}
