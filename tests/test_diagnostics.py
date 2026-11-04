from orchestrator.diagnostics import diagnostic_names_for, classify_diagnostics


def test_diagnostic_matrix_is_recipe_specific():
    assert 'auto_theme_audit.json' not in diagnostic_names_for('ecom_remix')
    assert 'auto_theme_audit.json' in diagnostic_names_for('auto_theme_discovery')


def test_diagnostics_distinguish_empty_and_missing():
    result = classify_diagnostics({'a': {'status': 200, 'content': {}}, 'b': {'status': 404}, 'c': {'status': 500, 'error': 'x'}})
    assert [x['status'] for x in result] == ['available_empty', 'missing_404', 'request_error']
