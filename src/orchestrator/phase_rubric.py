def _item(status, evidence=None, failure_modes=None, next_action=None):
    return {"status": status, "evidence": evidence or [], "failure_modes": failure_modes or [], "next_action": next_action}


def _timeline_overlaps(timeline):
    values = sorted((x.get("start"), x.get("end")) for x in timeline if x.get("start") is not None and x.get("end") is not None)
    return any(end > start for (_, end), (start, _) in zip(values, values[1:]))


def evaluate_phases(phases, expected_video_count=1):
    result = {"status": "passed", "phases": {}, "failure_modes": []}
    for phase in phases or []:
        name = phase.get("phase_name") or phase.get("name") or phase.get("phase")
        status = phase.get("status")
        metrics = phase.get("metrics") or {}
        output = phase.get("output_data") or {}
        failures = []
        evidence = [f"status={status}"]
        phase_status = "passed" if status == "completed" else ("skipped" if status == "skipped" else "failed" if status in {"failed", "error", "cancelled"} else "warning")
        if name == "material_download":
            if metrics.get("bytes_done") is None or metrics.get("bytes_total") is None: phase_status = "insufficient_evidence"; failures.append("DOWNLOAD_METRICS_MISSING")
            elif metrics.get("bytes_done") < metrics.get("bytes_total"): phase_status = "failed"; failures.append("DOWNLOAD_INCOMPLETE")
        elif name == "segment":
            if metrics.get("total_clips") is None: phase_status = "insufficient_evidence"; failures.append("SEGMENT_METRICS_MISSING")
            elif not metrics.get("total_clips"): phase_status = "failed"; failures.append("NO_CLIPS_CREATED")
            elif float(metrics.get("unlabeled_ratio", 0)) > 0.2: phase_status = "warning"; failures.append("UNLABELED_CLIPS_HIGH")
        elif name == "viral_learning":
            requested, final = metrics.get("requested"), metrics.get("final")
            if requested is None or final is None: phase_status = "insufficient_evidence"; failures.append("VIRAL_LEARNING_METRICS_MISSING")
            elif metrics.get("shortfall") or final < requested: phase_status = "warning"; failures.append("VIRAL_LEARNING_SHORTFALL")
        elif name in {"refine_slicer", "timeline_review"} and not metrics and not output: phase_status = "insufficient_evidence"; failures.append(f"{name.upper()}_OUTPUT_MISSING")
        elif name == "subtitle" and status == "completed" and not (output or metrics): phase_status = "insufficient_evidence"; failures.append("SUBTITLE_OUTPUT_MISSING")
        elif name in {"bgm", "sfx"} and status == "completed" and not (output or metrics): phase_status = "insufficient_evidence"; failures.append(f"{name.upper()}_OUTPUT_MISSING")
        elif name == "transcript" and not output.get("text_length") and not metrics.get("text_length"): phase_status = "insufficient_evidence"; failures.append("TRANSCRIPT_OUTPUT_MISSING")
        elif name == "select":
            if metrics.get("selected_count") is None and not output.get("selected_clip_ids"): phase_status = "insufficient_evidence"; failures.append("SELECT_OUTPUT_MISSING")
            elif metrics.get("shortfall") is True: phase_status = "warning"; failures.append("SELECT_SHORTFALL")
        elif name == "arrange":
            timeline = output.get("timeline") or []
            if not timeline: phase_status = "insufficient_evidence"; failures.append("TIMELINE_OUTPUT_MISSING")
            elif _timeline_overlaps(timeline): phase_status = "warning"; failures.append("TIMELINE_OVERLAP")
        elif name == "compose":
            if metrics.get("done") is None: phase_status = "insufficient_evidence"; failures.append("COMPOSE_OUTPUT_MISSING")
            elif metrics.get("done") < expected_video_count: phase_status = "failed"; failures.append("COMPOSE_COUNT_SHORTFALL")
        elif name == "upload" and not phase.get("output_files") and not output.get("oss_url"): phase_status = "insufficient_evidence"; failures.append("UPLOAD_OUTPUT_MISSING")
        result["phases"][name] = _item(phase_status, evidence, failures)
        result["failure_modes"].extend(failures)
    if any(v["status"] == "failed" for v in result["phases"].values()): result["status"] = "failed"
    elif any(v["status"] in {"warning", "insufficient_evidence"} for v in result["phases"].values()): result["status"] = "warning"
    return result
