def automation_preflight(chain, capability_probe=None):
    payload=chain.get('payload') or {}; kind=chain.get('chain_id')
    if kind in {'auto_ip', 'A-IP'} or chain.get('scene_type') == 'ip_promo':
        required=('name','brief','topic','num_scripts','auto_confirm_scripts','material_asset_ids','mix_config_snapshot')
        missing=[k for k in required if k not in payload]
        mix=payload.get('mix_config_snapshot') or {}
        missing += [k for k in ('timbre_voice_ids','delivery_enabled') if k not in mix]
        return {'status':'passed' if not missing else 'blocked','missing_fields':missing,'chain_id':kind}
    return None
