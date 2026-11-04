from .evidence import collect_text_artifacts, build_lineage


def merge_phase_node_evidence(phases, nodes):
    nodes_by_key = {item.get("key"): item for item in nodes or []}
    result = []
    for phase in phases or []:
        name = phase.get("phase_name") or phase.get("name")
        node = nodes_by_key.get(name, {})
        result.append({"key":name,"status":node.get("status") or phase.get("status"),"metrics":phase.get("metrics") or {},"output_data":phase.get("output_data") or {},"input":node.get("input"),"output":node.get("output"),"calls":node.get("calls") or [],"error":node.get("error") or phase.get("error_msg"),"duration_ms":node.get("duration_ms") or phase.get("duration_ms"),"evidence_status":"available" if node else "partial"})
    for node in nodes or []:
        if node.get("key") not in {x["key"] for x in result}:
            result.append({"key":node.get("key"),"status":node.get("status"),"metrics":{},"output_data":{},"input":node.get("input"),"output":node.get("output"),"calls":node.get("calls") or [],"error":node.get("error"),"duration_ms":node.get("duration_ms"),"evidence_status":"node_only"})
    return result


def collect_diagnostic_summaries(responses):
    result=[]
    for name,response in (responses or {}).items():
        response = response or {}
        status=response.get("status")
        result.append({"name":name,"status":"available" if status == 200 else "missing" if status in {404,410} else "unavailable","content":(response.get("content") if response.get("content") is not None else response.get("text")) if status == 200 else None,"error":response.get("error")})
    return result


def aggregate_text_and_lineage(task, videos):
    artifacts=[]
    for item in collect_text_artifacts(videos[0] if videos else {}, task or {}):
        if item.get("evidence_status") != "missing": artifacts.append(item)
    return {"text_artifacts":artifacts,"lineage":build_lineage(videos[0].get("id"), videos[0].get("clip_ids", [])) if videos else []}
