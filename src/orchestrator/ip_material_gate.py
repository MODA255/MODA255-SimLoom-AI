from collections import Counter


def _has_asr(asset):
    value = asset.get('asr')
    return bool(value and (not isinstance(value, dict) or value.get('text') or value.get('status') in {'ready', 'completed'}))


def _has_evidence(asset):
    return bool(asset.get('ip_evidence') or asset.get('ip_name') or asset.get('tags'))


def validate_ip_materials(assets, automated=False):
    assets = list(assets or [])
    profile = 'auto_ip_promo' if automated else 'manual_ip_promo'
    minimum = {'min_assets': 9, 'min_duration': 120, 'min_clips': 30} if automated else {'min_assets': 3, 'min_duration': 60, 'min_clips': 2}
    reasons = []
    if len(assets) < minimum['min_assets']: reasons.append('ASSET_COUNT_INSUFFICIENT')
    total_duration = sum(float(a.get('duration') or a.get('duration_seconds') or 0) for a in assets)
    total_clips = sum(int(a.get('clip_count') or a.get('clips') or 0) for a in assets)
    if total_duration < minimum['min_duration']: reasons.append('DURATION_INSUFFICIENT')
    if total_clips < minimum['min_clips']: reasons.append('CLIP_COUNT_INSUFFICIENT')
    if any(str(a.get('status', '')).lower() not in {'active', 'ready'} for a in assets): reasons.append('MATERIAL_NOT_READY')
    ips = {str(a.get('ip_name')).strip() for a in assets if a.get('ip_name')}
    if len(ips) > 1: reasons.append('IP_EVIDENCE_MISMATCH')
    if any(not _has_asr(a) for a in assets): reasons.append('ASR_MISSING')
    if any(not _has_evidence(a) for a in assets): reasons.append('IP_EVIDENCE_MISSING')
    valid = not reasons
    return {'valid': valid, 'code': None if valid else ('IP_EVIDENCE_MISMATCH' if 'IP_EVIDENCE_MISMATCH' in reasons else 'IP_MATERIAL_INSUFFICIENT'), 'profile': profile, 'reasons': reasons, 'evidence': {'asset_count': len(assets), 'duration_seconds': total_duration, 'clip_count': total_clips, 'ip_names': sorted(ips)}}
