from .live_source_probe import probe_live_candidates


def choose_live_source(pool, *, search=None, max_attempts=5, probe=probe_live_candidates):
    candidates=list(pool or [])
    discovery=[]
    if not candidates and search:
        found=search() or []
        discovery=[x.get('url') if isinstance(x,dict) else x for x in found]
        candidates.extend([x for x in discovery if x])
    result=probe(candidates[:max_attempts]) if candidates else {'status':'failed','attempts':[],'failure_code':'LIVE_SOURCE_POOL_EMPTY'}
    result['candidate_count']=len(candidates);result['discovery_count']=len(discovery);result['max_attempts']=max_attempts
    return result
