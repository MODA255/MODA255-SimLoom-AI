def attribute_failure(context):
    context = context or {}
    status = context.get('creation_status')
    error = str(context.get('remote_error') or '')
    if status in {400, 422} and any(x in error.lower() for x in ('required', 'invalid', 'asset_ids', 'timbre')):
        category, confidence = 'test_architecture', 'high'
    elif status in {401, 403, 404} or context.get('probe_failed'):
        category, confidence = 'environment_or_dependency', 'high'
    elif context.get('entered_running') and context.get('failure_stage'):
        category, confidence = 'business_logic_or_rule', 'medium'
    elif context.get('timeout'):
        category, confidence = 'unknown', 'low'
    else:
        category, confidence = 'unknown', 'low'
    return {
        'category': category,
        'confidence': confidence,
        'failure_stage': context.get('failure_stage'),
        'failure_code': context.get('failure_code'),
        'message': error,
        'is_test_architecture_failure': category == 'test_architecture',
        'is_environment_failure': category == 'environment_or_dependency',
        'is_business_failure': category == 'business_logic_or_rule',
        'evidence': context.get('evidence') or [],
        'next_action': context.get('next_action'),
    }
