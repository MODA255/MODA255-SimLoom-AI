import json
from pathlib import Path
from .rubric import evaluate_chain
from .diagnosis import diagnose_chain
from .delivery import delivery_status


def _video_metadata(video):
    video = video or {}
    return {"video_id": video.get("id") or video.get("video_id"), "status": video.get("status"), "remote_url": video.get("oss_url") or video.get("remote_url"), "duration": video.get("duration"), "resolution": video.get("resolution"), "file_size": video.get("file_size"), "transcript": video.get("transcript_json") or video.get("transcript") or None, "local_file": None}


def _judge(judge):
    if not judge:
        return {"status":"not_run","overall_score":None,"confidence":None,"failure_modes":[],"reason":""}
    return {"status":judge.get("status","completed"),"overall_score":judge.get("overall_score"),"confidence":judge.get("confidence"),"failure_modes":list(judge.get("failure_modes") or []),"reason":judge.get("reason",""),"evidence":list(judge.get("evidence") or []),"model":judge.get("model"),"prompt_version":judge.get("prompt_version"),"input_sha256":judge.get("input_sha256"),"missing_evidence":list(judge.get("missing_evidence") or []),"error_code":judge.get("error_code"),"error":judge.get("error"),"debug":judge.get("debug")}


def build_chain_report(data):
    data=dict(data or {}); rubric=evaluate_chain(data); videos=[_video_metadata(v) for v in data.get("videos") or []]; judge=_judge(data.get("judge")); hard=rubric["overall"]=="failed"; final="failed" if hard else ("passed_with_warning" if rubric["failure_modes"] else "passed")
    return {"chain_id":data.get("chain_id"),"execution_mode":data.get("execution_mode"),"scene_type":data.get("scene_type"),"timing":{"started_at":data.get("started_at") or data.get("poll_started_at"),"finished_at":data.get("finished_at") or data.get("poll_finished_at"),"poll_status":data.get("poll_status","not_run"),"poll_error":data.get("poll_error"),"poll_events":data.get("poll_events") or [],"phase_durations":data.get("phase_durations") or {}},"evidence":{"nodes":data.get("nodes") or [],"diagnostics":data.get("diagnostics") or [],"files":data.get("evidence_files") or [],"screenshot_status":data.get("screenshot_status","not_collected"),"screenshots":data.get("screenshots") or []},"task":{"task_id":(data.get("task") or {}).get("id"),"parent_task_id":(data.get("parent_task") or {}).get("id"),"status":(data.get("task") or {}).get("status"),"parent_status":(data.get("parent_task") or {}).get("status"),"current_phase":(data.get("task") or {}).get("current_phase"),"completed_phases":(data.get("task") or {}).get("completed_phases"),"total_phases":(data.get("task") or {}).get("total_phases")},"deterministic_rubric":rubric,"phase_rubric":data.get("phase_rubric") or {"status":"not_run","phases":{},"failure_modes":[]},"llm_judge":judge,"execution_status":"failed" if rubric["overall"]=="failed" else "passed" if rubric["passed"] else "warning","content_evaluation_status":"insufficient_evidence" if judge.get("status")=="insufficient_evidence" else "failed" if judge.get("failure_modes") and judge.get("status") != "error" else "error" if judge.get("status")=="error" else "completed" if judge.get("status")=="completed" else "not_run","delivery_status":delivery_status(data.get("submission_payload"),data.get("task"),data.get("delivery_config_snapshot")),"parent_task_status":(data.get("parent_task") or {}).get("status"),"cleanup_status":(data.get("cleanup") or {}).get("status","not_started"),"diagnosis":diagnose_chain({"phase_rubric":data.get("phase_rubric"),"deterministic_rubric":rubric,"evidence_completeness":data.get("evidence_completeness"),"cleanup":data.get("cleanup")}),"videos":{"requested":data.get("expected_video_count",0),"created":len(videos),"items":videos},"delivery":{"enabled":(data.get("task") or {}).get("delivery_enabled"),"auto_enabled":(data.get("task") or {}).get("auto_delivery_enabled")},"transcripts":[v["transcript"] for v in videos if v["transcript"] is not None],"failure_modes":list(dict.fromkeys(rubric["failure_modes"]+judge["failure_modes"])),"final_status":final,"local_media_files":[],"evidence_api_error":data.get("evidence_api_error")}


def build_six_chain_report(chain_inputs):
    chains=[build_chain_report(i) for i in (chain_inputs or [])]; counts={}
    for c in chains: counts[c["final_status"]]=counts.get(c["final_status"],0)+1
    return {"chain_count":len(chains),"status_counts":counts,"chains":chains,"local_media_files":[]}


def _markdown(report):
    lines=["# MatrixGen 六链路结构化日报","",f"链路数量：{report['chain_count']}","","| 链路 | 最终状态 | 执行 | 内容评测 | 投放 | 清理 | Judge |","|---|---|---|---|---|---|---:|"]
    for c in report["chains"]:
        j=c["llm_judge"]; lines.append(f"| {c.get('chain_id')} | {c['final_status']} | {c['execution_status']} | {c['content_evaluation_status']} | {c['delivery_status'].get('status')} | {c['cleanup_status']} | {j.get('overall_score') if j.get('overall_score') is not None else '-'} |")
    return "\n".join(lines)+"\n"


def write_structured_report(report,directory):
    root=Path(directory);root.mkdir(parents=True,exist_ok=True);jp=root/"six-chain-report.json";mp=root/"six-chain-report.md";jp.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8");mp.write_text(_markdown(report),encoding="utf-8");return {"json":str(jp),"markdown":str(mp)}
