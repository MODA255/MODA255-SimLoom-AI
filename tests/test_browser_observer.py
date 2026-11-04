from orchestrator.browser_observer import capture_task_screenshot


def test_capture_task_screenshot_writes_read_only_artifact(tmp_path):
    calls = []
    class Page:
        def screenshot(self, **kwargs):
            calls.append(kwargs)
            open(kwargs['path'], 'wb').write(b'png')
    result = capture_task_screenshot('https://example.test', 'task-1', tmp_path, page_factory=lambda url: Page())
    assert result['status'] == 'collected'
    assert result['path'].endswith('task-task-1.png')
    assert calls and calls[0]['full_page'] is True


def test_capture_task_screenshot_reports_failure_without_raising(tmp_path):
    result = capture_task_screenshot('https://example.test', 'task-1', tmp_path, page_factory=lambda url: (_ for _ in ()).throw(RuntimeError('page failed')))
    assert result['status'] == 'failed'
    assert 'page failed' in result['error']
