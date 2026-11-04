import json, sqlite3, uuid
from pathlib import Path
from .config import snapshot_hash, ConfigError


RESOURCE_COLUMNS = {
    'case_run_id': 'TEXT', 'terminal_status': 'TEXT', 'cleanup_status': "TEXT NOT NULL DEFAULT 'pending'",
    'operation_key': 'TEXT', 'payload_hash': 'TEXT', 'cleanup_retry_count': 'INTEGER NOT NULL DEFAULT 0',
    'cleanup_last_error': 'TEXT', 'cleanup_next_retry_at': 'TEXT',
}

class Repository:
 def __init__(self, path):
  self.path=str(path)
  if self.path != ':memory:': Path(path).parent.mkdir(parents=True, exist_ok=True)
  self._conn = sqlite3.connect(self.path, check_same_thread=False)
  with self._conn as c:
   c.executescript('''CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,status TEXT,snapshot TEXT,snapshot_hash TEXT,result TEXT,lease_owner TEXT); CREATE TABLE IF NOT EXISTS operations(key TEXT PRIMARY KEY,status TEXT); CREATE TABLE IF NOT EXISTS remote_tasks(run_id TEXT,remote_id TEXT,status TEXT); CREATE TABLE IF NOT EXISTS run_refs(run_id TEXT PRIMARY KEY, refs TEXT); CREATE TABLE IF NOT EXISTS resources(id TEXT PRIMARY KEY, run_id TEXT NOT NULL, case_id TEXT NOT NULL, resource_type TEXT NOT NULL, resource_id TEXT, status TEXT NOT NULL, created_at TEXT NOT NULL, cleanup_after TEXT NOT NULL, protected INTEGER NOT NULL, protection_reason TEXT, metadata TEXT NOT NULL, UNIQUE(run_id,case_id,resource_type));''')
   for table, column, definition in [('runs','lease_owner','TEXT')]:
    if column not in {row[1] for row in c.execute(f'PRAGMA table_info({table})')}: c.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')
   columns={row[1] for row in c.execute('PRAGMA table_info(resources)')}
   for column, definition in RESOURCE_COLUMNS.items():
    if column not in columns: c.execute(f'ALTER TABLE resources ADD COLUMN {column} {definition}')
 def create_run(self,snapshot):
  rid='run-'+uuid.uuid4().hex[:12]
  with self._conn as c: c.execute('INSERT INTO runs VALUES(?,?,?,?,?,?)',(rid,'created',json.dumps(snapshot),snapshot_hash(snapshot),None,None))
  return rid
 def get_run(self,rid):
  with self._conn as c: r=c.execute('SELECT id,status,snapshot,snapshot_hash,result FROM runs WHERE id=?',(rid,)).fetchone()
  if not r: raise KeyError(rid)
  return {'id':r[0],'status':r[1],'snapshot':json.loads(r[2]),'snapshot_hash':r[3],'result':r[4]}
 def assert_snapshot(self,rid,snapshot):
  if self.get_run(rid)['snapshot_hash'] != snapshot_hash(snapshot): raise ConfigError('CONFIG_MUTATED')
 def set_result(self,rid,status,result):
  with self._conn as c: c.execute('UPDATE runs SET status=?,result=? WHERE id=?',(status,result,rid))
 def operation(self,key,status):
  with self._conn as c: c.execute('INSERT OR REPLACE INTO operations VALUES(?,?)',(key,status))
 def operation_status(self,key):
  with self._conn as c: r=c.execute('SELECT status FROM operations WHERE key=?',(key,)).fetchone()
  return r[0] if r else None
 def get_operation(self,key):
  raw=self.operation_status(key)
  if not raw:
   return None
  try:
   return json.loads(raw)
  except (TypeError, json.JSONDecodeError):
   # Older repositories stored sentinel states as bare strings.
   return raw
 def reserve_operation(self,key,record):
  encoded=json.dumps(record, ensure_ascii=False, sort_keys=True)
  with self._conn as c:
   inserted=c.execute('INSERT OR IGNORE INTO operations VALUES(?,?)',(key,encoded)).rowcount == 1
   row=c.execute('SELECT status FROM operations WHERE key=?',(key,)).fetchone()
  if not row:
   return inserted, None
  try:
   record = json.loads(row[0])
  except (TypeError, json.JSONDecodeError):
   record = row[0]
  return inserted, record
 def acquire_lease(self,rid,owner):
  with self._conn as c: return c.execute("UPDATE runs SET lease_owner=? WHERE id=? AND (lease_owner IS NULL OR lease_owner=?)",(owner,rid,owner)).rowcount == 1
 def record_remote_task(self,rid,remote_id,status):
  with self._conn as c: c.execute('INSERT INTO remote_tasks VALUES(?,?,?)',(rid,remote_id,status))
 def get_remote_tasks(self,rid):
  with self._conn as c: rows=c.execute('SELECT run_id,remote_id,status FROM remote_tasks WHERE run_id=?',(rid,)).fetchall()
  return [{'run_id':a,'remote_id':b,'status':d} for a,b,d in rows]
 def save_refs(self,rid,refs):
  with self._conn as c: c.execute('INSERT OR REPLACE INTO run_refs VALUES(?,?)',(rid,json.dumps(refs)))
 def get_refs(self,rid):
  with self._conn as c: row=c.execute('SELECT refs FROM run_refs WHERE run_id=?',(rid,)).fetchone()
  return json.loads(row[0]) if row else {}
 def claim_operation(self,key):
  with self._conn as c: return c.execute('INSERT OR IGNORE INTO operations VALUES(?,?)',(key,'submission_unknown')).rowcount == 1
 def _select(self, where='', args=()):
  fields='id,run_id,case_id,case_run_id,resource_type,resource_id,status,created_at,cleanup_after,protected,protection_reason,terminal_status,cleanup_status,operation_key,payload_hash,cleanup_retry_count,cleanup_last_error,cleanup_next_retry_at,metadata'
  with self._conn as c: return c.execute('SELECT '+fields+' FROM resources'+where,args).fetchall()
 def get_resource_by_key(self,run_id,case_id,resource_type):
  rows=self._select(' WHERE run_id=? AND case_id=? AND resource_type=?',(run_id,case_id,resource_type)); return self._resource(rows[0]) if rows else None
 def get_resource(self,resource_id):
  rows=self._select(' WHERE id=?',(resource_id,))
  if not rows: raise KeyError(resource_id)
  return self._resource(rows[0])
 def insert_resource(self, resource):
  if not resource.get('case_run_id'):
   raise ValueError('case_run_id is required')
  fields=['id','run_id','case_id','case_run_id','resource_type','resource_id','status','created_at','cleanup_after','protected','protection_reason','terminal_status','cleanup_status','operation_key','payload_hash','cleanup_retry_count','cleanup_last_error','cleanup_next_retry_at','metadata']
  values=[resource.get(k) for k in fields]
  values[9]=int(bool(values[9])); values[12]=values[12] or 'pending'; values[15]=values[15] or 0; values[18]=json.dumps(values[18] or {})
  with self._conn as c:
   c.execute('INSERT OR IGNORE INTO resources ('+','.join(fields)+') VALUES ('+','.join('?' for _ in fields)+')',values)
  return self.get_resource_by_key(resource['run_id'],resource['case_id'],resource['resource_type'])
 def update_resource(self,resource_id,updates):
  allowed=(set(RESOURCE_COLUMNS)-{'case_run_id'})|{'resource_id','status','protected','protection_reason','created_at','cleanup_after','metadata'}
  unknown=set(updates)-allowed
  if unknown: raise KeyError(next(iter(unknown)))
  if not updates:
   rows=self._select(' WHERE id=?',(resource_id,))
   if not rows: raise KeyError(resource_id)
   raise KeyError('updates')
  assignments=', '.join(f'{key}=?' for key in updates)
  values=[int(v) if k=='protected' else (json.dumps(v or {}) if k=='metadata' else v) for k,v in updates.items()]
  with self._conn as c: c.execute(f'UPDATE resources SET {assignments} WHERE id=?',values+[resource_id])
  rows=self._select(' WHERE id=?',(resource_id,))
  if not rows: raise KeyError(resource_id)
  return self._resource(rows[0])
 def list_resources(self,run_id=None):
  rows=self._select(' WHERE run_id=? ORDER BY created_at,id' if run_id is not None else ' ORDER BY created_at,id', (run_id,) if run_id is not None else ())
  return [self._resource(row) for row in rows]
 @staticmethod
 def _resource(row):
  keys=('id','run_id','case_id','case_run_id','resource_type','resource_id','status','created_at','cleanup_after','protected','protection_reason','terminal_status','cleanup_status','operation_key','payload_hash','cleanup_retry_count','cleanup_last_error','cleanup_next_retry_at','metadata')
  item=dict(zip(keys,row)); item['protected']=bool(item['protected']); item['metadata']=json.loads(item['metadata'] or '{}'); return item
 def release_lease(self,rid,owner):
  with self._conn as c: c.execute('UPDATE runs SET lease_owner=NULL WHERE id=? AND lease_owner=?',(rid,owner))
