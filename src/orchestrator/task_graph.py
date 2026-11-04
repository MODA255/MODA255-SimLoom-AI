from datetime import datetime, timezone


def _id(item):
    return (item or {}).get('id') or (item or {}).get('task_id') or (item or {}).get('video_id')


def _parse(value):
    if not value: return None
    return datetime.fromisoformat(str(value).replace('Z', '+00:00'))


def build_task_graph(parent, now=None):
    parent = parent or {}; root_id = _id(parent); links=[]; video_ids=[]
    def connect(source, values, kind):
        for value in values or []:
            target = _id(value)
            if target: links.append({'from':source,'to':target,'type':kind})
    for session in parent.get('sessions', []):
        sid=_id(session); connect(root_id,[session],'session')
        for segment in session.get('segments', []):
            gid=_id(segment); connect(sid,[segment],'segment')
            for child in segment.get('child_tasks', segment.get('children', [])):
                cid=_id(child); connect(gid,[child],'child_task')
                for video in child.get('videos', []):
                    vid=_id(video); connect(cid,[video],'video')
                    if vid: video_ids.append(vid)
    for child in parent.get('children', parent.get('tasks', [])):
        cid=_id(child); connect(root_id,[child],'child_task')
        for video in child.get('videos', []):
            vid=_id(video); connect(cid,[video],'video')
            if vid: video_ids.append(vid)
    metrics=[]; stalled=[]; reference=_parse(now) if now else datetime.now(timezone.utc)
    for phase in parent.get('phases', []):
        start=_parse(phase.get('started_at')); end=_parse(phase.get('finished_at')) or (reference if str(phase.get('status','')).lower() in {'processing','running'} else None)
        duration=(end-start).total_seconds() if start and end else None
        metrics.append({'name':phase.get('name') or phase.get('phase'),'status':phase.get('status'),'started_at':phase.get('started_at'),'finished_at':phase.get('finished_at'),'duration_seconds':duration})
        if str(phase.get('status','')).lower() in {'processing','running'}: stalled.append(phase.get('name') or phase.get('phase'))
    return {'root_id':root_id,'links':links,'video_ids':video_ids,'phase_metrics':metrics,'stalled_phases':stalled}


def classify_task_graph(parent, children):
    children=list(children or []); successful=[_id(c) for c in children if str(c.get('status','')).lower() in {'completed','success','succeeded','done'}]; failed=[_id(c) for c in children if str(c.get('status','')).lower() in {'failed','error','cancelled'}]
    if failed and successful: status='partial_success'
    elif failed: status='failed'
    elif successful and len(successful)==len(children): status='passed'
    else: status='waiting'
    return {'status':status,'successful_child_task_ids':successful,'failed_child_task_ids':failed,'child_task_count':len(children)}
