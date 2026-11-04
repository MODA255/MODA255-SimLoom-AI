import json
from pathlib import Path
from orchestrator.daily_report import render_daily_report, write_daily_report


def sample():
    return {'run_id':'r1','daily_report':{'overall':'partial_success','status_counts':{'passed':1,'failed':1},'failure_count':1,'root_cause_counts':{'PHASE_STALLED':1},'slowest_phase':{'name':'cut','duration_seconds':3600},'failures':[{'branch_id':'auto_theme','root_cause':'PHASE_STALLED','next_action':'INSPECT_TASK_LOGS'}]}}


def test_render_daily_report_is_human_readable():
    text = render_daily_report(sample())
    assert 'r1' in text
    assert 'partial_success' in text
    assert 'PHASE_STALLED' in text
    assert 'auto_theme' in text


def test_write_daily_report_writes_json_and_markdown(tmp_path):
    paths = write_daily_report(sample(), tmp_path)
    assert Path(paths['json']).is_file()
    assert Path(paths['markdown']).is_file()
    assert json.loads(Path(paths['json']).read_text())['run_id'] == 'r1'


def test_write_daily_report_also_writes_detailed_html(tmp_path):
    paths = write_daily_report(sample(), tmp_path)
    assert Path(paths['html']).is_file()
    content = Path(paths['html']).read_text()
    assert '失败明细' in content
    assert '运行时间线' in content
