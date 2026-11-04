from .evidence_adapter import collect_diagnostic_summaries

DIAGNOSTIC_MATRIX = {
    'ecom_remix': ['pipeline.log', 'arrange_report.json', 'timeline_review_report.json'],
    'ip_promo': ['pipeline.log', 'refine_slicer_report.json', 'arrange_report.json'],
    'auto_theme_discovery': ['pipeline.log', 'auto_theme_audit.json', 'arrange_report.json'],
}


def diagnostic_names_for(recipe):
    return DIAGNOSTIC_MATRIX.get(recipe, ['pipeline.log'])


def classify_diagnostics(responses):
    summaries = collect_diagnostic_summaries(responses)
    for item in summaries:
        if item['status'] == 'available':
            item['status'] = 'available_non_empty' if item.get('content') else 'available_empty'
        elif item['status'] == 'missing':
            item['status'] = 'missing_404'
        elif item['status'] == 'unavailable':
            item['status'] = 'request_error'
    return summaries
