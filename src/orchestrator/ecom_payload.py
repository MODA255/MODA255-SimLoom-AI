"""Read-only candidate builder, based on saved VFC frontend request construction."""
from copy import deepcopy

class EcomPayloadError(ValueError):
    pass


def build_ecom_payload(asset, topic, fixed_config):
    asset = deepcopy(asset)
    asset_id = asset.get('asset_id') or asset.get('id')
    validation = []
    if not asset_id:
        validation.append({'reason': 'asset_id_missing'})
    if asset.get('status') not in ('active', 'ready'):
        validation.append({'reason': 'asset_not_ready'})
    if not topic.get('topic'):
        validation.append({'reason': 'topic_missing'})
    asset['asset_id'] = asset_id
    business = deepcopy(fixed_config.get('business', {}))
    for key in ('brand_id', 'brand_name', 'product_id', 'product_name', 'mechanism_id', 'mechanism_name', 'ip_id', 'ip_name'):
        if key in fixed_config:
            business[key] = fixed_config[key]
    count = fixed_config.get('num_versions', 1)
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        validation.append({'reason': 'invalid_num_versions'})
    candidate = {
        'recipe': 'ecom_remix', 'num_versions': count,
        'master_asset_id': asset_id, 'master_asset_ids': [asset_id] if asset_id else [],
        'query': topic.get('topic', ''), 'brief': topic.get('brief', ''),
        'bgm_mode': fixed_config.get('bgm_mode', 'none'),
        'auto_delivery_enabled': False, 'auto_delivery_products': [],
        'auto_delivery_product_id': '', 'auto_delivery_product_name': '', 'auto_delivery_product_image': '',
    }
    mapping = {'subtitle_enabled': 'enable_subtitle', 'word_effect_enabled': 'enable_fancy_text',
               'smart_original_audio': 'enable_smart_native_insertion', 'smart_sound_effect': 'enable_sfx',
               'force_vertical': 'force_portrait', 'dynamic_crop': 'enable_dynamic_crop'}
    for local, remote in mapping.items():
        candidate[remote] = fixed_config.get(local, False)
    for key in ('template_id', 'bgm_asset_id', 'bgm_track_id'):
        if fixed_config.get(key):
            candidate[key] = fixed_config[key]
    if fixed_config.get('template_id') and fixed_config.get('template_version'):
        candidate['template_version_id'] = fixed_config['template_version']
    for key in ('ip_name', 'brand_name'):
        if business.get(key):
            candidate[key] = business[key]
    for key in ('product_id', 'brand_id', 'mechanism_id', 'ip_id', 'product_name', 'mechanism_name'):
        if business.get(key):
            validation.append({'reason': 'remote_business_mapping_unconfirmed', 'field': key})
    if fixed_config.get('subtitle_style'):
        validation.append({'reason': 'subtitle_style_mapping_unconfirmed'})
    # These delivery fields are policy metadata: the saved VFC store only confirms
    # auto_delivery_* on its wire request. Do not guess a second remote schema.
    delivery = {'delivery_enabled': False, 'advertiser': [], 'awemes': [], 'products': [], 'promotion_goal': '', 'adlab_scene': ''}
    validation.append({'reason': 'remote_delivery_schema_unconfirmed', 'fields': list(delivery)})
    return {**delivery, 'recipe': 'ecom_remix', 'num_versions': count, 'assets': [asset],
            'business': business, 'candidate': candidate, 'status': 'blocked',
            'submission_allowed': False, 'validation': validation,
            'evidence': [{'source': 'artifacts/frontend-analysis/assets_vfc-CqEYngwX.js',
                          'confirmed': ['master_asset_ids', 'query', 'enable_subtitle', 'auto_delivery_enabled']}]}
