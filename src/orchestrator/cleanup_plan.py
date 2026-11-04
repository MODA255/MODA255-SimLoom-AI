def build_cleanup_plan(report):
    actions = []
    for chain in report.get("chains") or []:
        cid = chain.get("chain_id")
        parent_status = chain.get("parent_task_status")
        videos_created = (chain.get("videos") or {}).get("created", 0)
        task = chain.get("task") or {}
        if parent_status == "waiting_live":
            actions.append({"chain_id": cid, "action":"cancel_parent", "resource_id": task.get("parent_task_id"), "reason":"PARENT_WAITING_LIVE"})
        if task.get("status") == "completed" and videos_created:
            actions.append({"chain_id": cid, "action":"retain", "resource_id": task.get("task_id"), "reason":"COMPLETED_WITH_OUTPUT"})
        elif chain.get("cleanup_status") not in {"passed", "retained"}:
            actions.append({"chain_id": cid, "action":"inspect", "resource_id": task.get("task_id"), "reason":"CLEANUP_REQUIRES_REVIEW"})
    return {"status":"planned", "dry_run":bool(report.get("dry_run", True)), "remote_writes":False, "actions":actions}
