def _parse(raw):
    if not raw: return None
    if isinstance(raw, (list, tuple)):
        return {'method':str(raw[0]).upper(),'path':str(raw[1])} if len(raw) == 2 else None
    try:
        method, path = str(raw).split(' ', 1)
        return {'method':method.upper(),'path':path}
    except ValueError: return None


def probe_phase_routes(config, adapter=None):
    routes = (config or {}).get('automation_routes', {})
    result = {'status':'ready','routes':{}}
    for label, key in (('automation','get_automation_stages'),('ip_promo','get_ip_promo_phases')):
        parsed = _parse(routes.get(key))
        if not parsed:
            result['routes'][label] = 'unconfigured'
        else:
            result['routes'][label] = parsed
    if any(value == 'unconfigured' for value in result['routes'].values()):
        result['status'] = 'blocked'; result['reason'] = 'PHASE_ROUTE_UNCONFIGURED'
    return result
