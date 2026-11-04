from datetime import datetime

def build_poll_snapshot(task, observed_at):
    return {"observed_at":observed_at,"task_id":task.get("id") or task.get("task_id"),"status":task.get("status"),"current_phase":task.get("current_phase"),"progress_percentage":task.get("progress_percentage"),"progress_message":task.get("progress_message"),"completed_phases":task.get("completed_phases"),"total_phases":task.get("total_phases"),"error_msg":task.get("error_msg")}

def compute_phase_durations(phases):
    result={}
    for phase in phases or []:
        start=phase.get("started_at");finish=phase.get("completed_at");seconds=None
        if start and finish:
            seconds=(datetime.fromisoformat(finish.replace("Z","+00:00"))-datetime.fromisoformat(start.replace("Z","+00:00"))).total_seconds()
        result[phase.get("phase_name") or phase.get("name") or phase.get("phase")]= {"status":phase.get("status"),"duration_seconds":seconds,"error":phase.get("error_msg") or None,"started_at":start,"completed_at":finish,"retry_count":phase.get("retry_count",0)}
    return result
