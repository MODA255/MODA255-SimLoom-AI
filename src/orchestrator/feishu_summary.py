import json


def build_feishu_summary(report, report_url=''):
    summary = report.get('summary') or {}
    blocked = [c for c in report.get('chains', []) if c.get('verdict', {}).get('test_status') == 'blocked' or c.get('status') == 'blocked']
    issues = []
    for chain in report.get('chains', []):
        issues.extend(i.get('message') or i.get('reason') or str(i) for i in chain.get('verdict', {}).get('issues', []))
    return {
        'msg_type': 'interactive',
        'card': {
            'header': {'title': {'tag': 'plain_text', 'content': 'MatrixGen 每日巡检'}, 'template': 'blue' if not blocked else 'orange'},
            'elements': [
                {'tag': 'div', 'text': {'tag': 'lark_md', 'content': f"**结果**：完成 {summary.get('passed', 0)}，失败 {summary.get('failed', 0)}，阻断 {len(blocked)}，警告 {summary.get('warnings', 0)}"}},
                {'tag': 'div', 'text': {'tag': 'lark_md', 'content': f"**证据**：轮询 {summary.get('poll_event_count', 0)}，节点 {summary.get('node_count', 0)}，诊断 {summary.get('diagnostic_count', 0)}，截图 {summary.get('screenshot_count', 0)}，成片 {summary.get('video_count', 0)}"}},
                {'tag': 'div', 'text': {'tag': 'lark_md', 'content': '**阻断链路**：' + ('、'.join(c.get('chain_id', '') for c in blocked) if blocked else '无')}},
                {'tag': 'action', 'actions': [{'tag': 'button', 'text': {'tag': 'plain_text', 'content': '查看详细报告'}, 'type': 'primary', 'url': report_url}]},
            ],
        },
    }
