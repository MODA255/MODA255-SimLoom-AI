def delivery_status(*sources):
    values = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in ("delivery_enabled", "auto_delivery_enabled"):
            if key in source and source[key] is not None:
                values.append(bool(source[key]))
    if any(values):
        return {"status":"enabled", "verified":True, "source_count":len(values)}
    if values and not any(values):
        return {"status":"disabled", "verified":True, "source_count":len(values)}
    return {"status":"unknown", "verified":False, "source_count":0}
