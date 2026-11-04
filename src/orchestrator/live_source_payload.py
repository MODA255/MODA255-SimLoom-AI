from copy import deepcopy


def select_and_apply_source(payload, selection):
    payload=deepcopy(payload or {}); url=selection.get('selected_url')
    if url:
        payload['source_urls']=[url]
        payload.setdefault('ingest_config_snapshot',{})['live_urls']=[url]
    return payload
