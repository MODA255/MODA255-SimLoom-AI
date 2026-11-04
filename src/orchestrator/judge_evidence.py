def build_judge_evidence(data):
    data = data or {}
    nodes = data.get("nodes") or []
    phases = data.get("phases") or []
    diagnostics = data.get("diagnostics") or []
    text_artifacts = data.get("text_artifacts") or []
    lineage = data.get("lineage") or []
    missing = []
    if not nodes: missing.append("nodes")
    if not phases: missing.append("phases")
    if not diagnostics: missing.append("diagnostics")
    if not any(x.get("evidence_status") == "available" for x in text_artifacts): missing.append("transcript_or_script")
    if not lineage: missing.append("lineage")
    phase_summary = []
    for node in nodes:
        phase_summary.append({"key":node.get("key") or node.get("phase_name"),"status":node.get("status"),"input":node.get("input"),"output":node.get("output"),"calls":node.get("calls") or [],"error":node.get("error"),"duration_ms":node.get("duration_ms")})
    result = {"status":"ready" if not missing else "insufficient_evidence","cannot_conclude":bool(missing),"missing_evidence":sorted(set(missing)),"task_context":data.get("task") or {},"phase_summary":phase_summary,"diagnostics":diagnostics,"text_artifacts":text_artifacts,"lineage_summary":lineage,"deterministic_findings":data.get("deterministic_findings") or []}
    return result
