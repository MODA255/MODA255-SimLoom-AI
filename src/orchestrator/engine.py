import hashlib, json
from .policy import SourcePolicy

class SubmissionUnknown(RuntimeError): pass

class Orchestrator:
 def __init__(self, config, repo): self.config=config; self.repo=repo
 def doctor(self):
  missing=[]
  if self.config.mode=='remote_live' and not self.config.data.get('target',{}).get('base_url'): missing.append('target.base_url')
  if not self.config.data.get('providers',{}).get('search'): missing.append('provider.search')
  if not self.config.data.get('providers',{}).get('model'): missing.append('provider.model')
  if not self.config.data.get('profiles',{}).get('folder_id'): missing.append('resources.folder_id')
  if self.config.mode=='remote_live' and not any(self.config.data.get('auth',{}).get(k) for k in ('token_env','secret_file')): missing.append('auth.reference')
  return {'status':'blocked' if missing else 'ready','missing':missing,'mode':self.config.mode}
 def operation_key(self,branch,op,payload): return hashlib.sha256(json.dumps([self.config.data.get('target',{}).get('environment'),branch,op,payload],sort_keys=True).encode()).hexdigest()
 def submit_with_unknown(self,branch,key):
  self.repo.operation(key,'submission_unknown'); raise SubmissionUnknown('SUBMISSION_UNKNOWN')
 def run(self, inputs=None, execute=False):
  return self.daily_run(inputs, execute=execute)
 def _daily(self):
  from .daily import DailyRunner
  return DailyRunner(self, **getattr(self, 'daily_dependencies', {}))
 def daily_plan(self, inputs=None): return self._daily().plan(inputs)
 def daily_run(self, inputs=None, execute=False): return self._daily().run(inputs, execute)
 def daily_status(self, rid): return self.repo.get_refs(rid)
 def daily_report(self, rid): return self._daily().report(rid)
 def cancel(self,rid): self.repo.set_result(rid,'cancelled','cancelled'); return self.repo.get_run(rid)
