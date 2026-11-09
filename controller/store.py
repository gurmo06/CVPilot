import sqlite3
from contextlib import contextmanager, closing
from pathlib import Path

SCHEMA = '''
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS users (
  discord_id TEXT PRIMARY KEY, github_user TEXT, repo TEXT, agent TEXT,
  master_edit INTEGER NOT NULL DEFAULT 0, provisioned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, discord_id TEXT NOT NULL,
  kind TEXT NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
  result TEXT, error TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  started_at TEXT, finished_at TEXT
);
CREATE INDEX IF NOT EXISTS jobs_by_state_id ON jobs(state,id);
'''
class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection().close()
    def connection(self):
        c=sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
        c.row_factory=sqlite3.Row
        c.executescript(SCHEMA)
        c.execute('PRAGMA busy_timeout=30000')
        return c
    @contextmanager
    def tx(self):
        c=self.connection()
        try:
            c.execute('BEGIN IMMEDIATE')
            yield c
            c.execute('COMMIT')
        except Exception:
            c.execute('ROLLBACK')
            raise
        finally:
            c.close()
    def enqueue(self, uid, kind, payload):
        import json
        from conversation import get
        contexts=get(self.path.parent)
        snapshot=contexts.snapshot(uid)
        payload=dict(payload)
        payload['_conversation']={'generation':snapshot['generation'],
                                  'through':snapshot['events'][-1]['id'] if snapshot['events'] else 0}
        with self.tx() as c:
            if kind in ('reset','revert','master-delete','application-delete'):
                if c.execute("SELECT 1 FROM jobs WHERE discord_id=? AND state IN ('queued','running') LIMIT 1",(uid,)).fetchone():
                    raise ValueError('Wait for your queued/running jobs to finish before resetting, reverting, or deleting. Use /cvpilot status.')
            if kind!='reset':
                last=c.execute("SELECT state FROM jobs WHERE discord_id=? AND kind='reset' ORDER BY id DESC LIMIT 1",(uid,)).fetchone()
                if last and last['state']!='done':
                    raise ValueError('Repository reset is pending or incomplete. Check /cvpilot status and retry /cvpilot reset if it failed.')
            result=c.execute('INSERT INTO jobs(discord_id,kind,payload) VALUES(?,?,?)', (uid,kind,json.dumps(payload))).lastrowid
        return result
    def claim(self):
        with self.tx() as c:
            j=c.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY id ASC LIMIT 1").fetchone()
            if j: c.execute("UPDATE jobs SET state='running',started_at=CURRENT_TIMESTAMP WHERE id=?", (j['id'],))
        return dict(j) if j else None
    def finish(self, jid, state, result=None, error=None):
        import json
        with self.tx() as c:
            c.execute('UPDATE jobs SET state=?,result=?,error=?,finished_at=CURRENT_TIMESTAMP WHERE id=?',
                (state,json.dumps(result) if result is not None else None,str(error)[:3000] if error else None,jid))
        from conversation.runtime import record_result
        try:record_result(self,self.job(jid),state,result,error)
        except Exception as exc:print(f'CVPilot conversation history warning for job {jid}: {type(exc).__name__}',flush=True)
    def recover(self):
        with self.tx() as c:
            c.execute("UPDATE jobs SET state='queued',started_at=NULL WHERE state='running'")
    def resume_tailor(self,uid,jid,signature):
        import json
        from conversation import get
        snapshot=get(self.path.parent).snapshot(uid)
        with self.tx() as c:
            row=c.execute('SELECT * FROM jobs WHERE id=? AND discord_id=?',(jid,uid)).fetchone()
            if not row or row['kind']!='tailor' or row['state']!='needs-input':
                raise ValueError('Only a tailoring job awaiting answers can be continued.')
            cutoff=c.execute("SELECT COALESCE(MAX(id),0) FROM jobs WHERE discord_id=? AND kind='reset' AND state='done'",(uid,)).fetchone()[0]
            if jid<=cutoff:raise ValueError('This review predates your last reset. Submit a new tailor request.')
            if c.execute("SELECT 1 FROM jobs WHERE discord_id=? AND state IN ('queued','running')",(uid,)).fetchone():
                raise ValueError('Wait for your queued/running jobs, including profile saves, before continuing.')
            data=json.loads(row['payload']);data['accepted_review']=signature
            data['_conversation']={'generation':snapshot['generation'],'through':snapshot['events'][-1]['id'] if snapshot['events'] else 0}
            c.execute("UPDATE jobs SET state='queued',payload=?,started_at=NULL,finished_at=NULL,error=NULL,result=NULL WHERE id=?",(json.dumps(data),jid))
    def user(self, uid):
        with closing(self.connection()) as c:
            x=c.execute('SELECT * FROM users WHERE discord_id=?',(uid,)).fetchone()
        return dict(x) if x else None
    def job(self, jid):
        with closing(self.connection()) as c:
            x=c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
        return dict(x) if x else None
    def recent(self, uid):
        with closing(self.connection()) as c:
            xs=c.execute('SELECT id,kind,state,result,error,created_at FROM jobs WHERE discord_id=? ORDER BY id DESC LIMIT 8',(uid,)).fetchall()
        return [dict(x) for x in xs]

    def queue_snapshot(self, uid, jid=None):
        """One read snapshot: global counts/ranks, but only this user's job details.

        Running work comes first, then waiting jobs in FIFO ID order. Completed
        and failed jobs never occupy a queue position. No scheduling is changed.
        """
        if jid is not None and (type(jid) is not int or jid<1):
            raise ValueError('Invalid job ID')
        with closing(self.connection()) as c:
            c.execute('BEGIN')
            counts=c.execute("""SELECT COUNT(*) AS total,
                COALESCE(SUM(state='running'),0) AS running,
                COALESCE(SUM(state='queued'),0) AS waiting
                FROM jobs WHERE state IN ('queued','running')""").fetchone()
            own_pending=c.execute("SELECT COUNT(*) FROM jobs WHERE discord_id=? AND state IN ('queued','running')",(uid,)).fetchone()[0]
            selection='AND j.id=?' if jid is not None else '''ORDER BY
                CASE j.state WHEN 'running' THEN 0 WHEN 'queued' THEN 1 ELSE 2 END,
                CASE WHEN j.state IN ('queued','running') THEN j.id ELSE -j.id END
                LIMIT 8'''
            rows=c.execute(f"""WITH active AS (
                SELECT id, ROW_NUMBER() OVER (
                    ORDER BY CASE state WHEN 'running' THEN 0 ELSE 1 END, id
                ) AS position FROM jobs WHERE state IN ('queued','running')
            ) SELECT j.id,j.kind,j.state,j.result,j.error,j.created_at,a.position
              FROM jobs j LEFT JOIN active a ON a.id=j.id
              WHERE j.discord_id=? {selection}""",(uid,jid) if jid is not None else (uid,)).fetchall()
        if jid is not None and not rows:raise ValueError('No job found for your account and that job ID.')
        return {**dict(counts),'own_pending':own_pending,'jobs':[dict(row) for row in rows]}

    def latest_uploads(self, uid):
        import json
        with closing(self.connection()) as c:
            rows=c.execute("SELECT kind,result FROM jobs WHERE discord_id=? AND kind IN ('master-upload','master-delete') AND state='done' AND id>(SELECT COALESCE(MAX(id),0) FROM jobs WHERE discord_id=? AND kind='reset' AND state='done') ORDER BY id DESC",(uid,uid)).fetchall()
        uploads={};seen=set()
        for row in rows:
            result=json.loads(row['result'] or '{}')
            name=result.get('label') if row['kind']=='master-upload' else result.get('deleted_directory','').removeprefix('master-resumes/')
            if name and name not in seen:
                seen.add(name)
                if row['kind']=='master-upload':uploads[name]=result
        return uploads

    def status_snapshot(self, uid, jid=None, direction=None):
        """One owned job and ID-based neighbours, from one queue snapshot."""
        if jid is not None and (type(jid) is not int or jid<1):
            raise ValueError('Invalid job ID')
        if direction not in (None,'older','newer') or (direction and jid is None):
            raise ValueError('Invalid status navigation')
        with closing(self.connection()) as c:
            c.execute('BEGIN')
            if jid is not None:
                if not c.execute('SELECT 1 FROM jobs WHERE discord_id=? AND id=?',(uid,jid)).fetchone():
                    raise ValueError('No job found for your account and that job ID.')
                if direction:
                    comparison,order=('<','DESC') if direction=='older' else ('>','ASC')
                    neighbour=c.execute(f'SELECT id FROM jobs WHERE discord_id=? AND id{comparison}? ORDER BY id {order} LIMIT 1',(uid,jid)).fetchone()
                    if neighbour:jid=neighbour[0]
            else:
                latest=c.execute('SELECT MAX(id) FROM jobs WHERE discord_id=?',(uid,)).fetchone()
                jid=latest[0]
            counts=dict(c.execute("""SELECT COUNT(*) AS total,
                COALESCE(SUM(state='running'),0) AS running,
                COALESCE(SUM(state='queued'),0) AS waiting
                FROM jobs WHERE state IN ('queued','running')""").fetchone())
            job=c.execute("""WITH active AS (
                SELECT id,ROW_NUMBER() OVER (ORDER BY CASE state WHEN 'running' THEN 0 ELSE 1 END,id) AS position
                FROM jobs WHERE state IN ('queued','running')
            ) SELECT j.*,a.position FROM jobs j LEFT JOIN active a ON a.id=j.id
              WHERE j.discord_id=? AND j.id=?""",(uid,jid)).fetchone()
            history=c.execute('''SELECT COUNT(*) AS count,1+COALESCE(SUM(id>?),0) AS ordinal,
                MAX(CASE WHEN id<? THEN id END) AS older,
                MIN(CASE WHEN id>? THEN id END) AS newer FROM jobs WHERE discord_id=?''',(jid,jid,jid,uid)).fetchone()
            own_pending=c.execute("SELECT COUNT(*) FROM jobs WHERE discord_id=? AND state IN ('queued','running')",(uid,)).fetchone()[0]
        return {**counts,'own_pending':own_pending,'job':dict(job) if job else None,'history':dict(history)}

    def download_jobs(self, uid):
        with closing(self.connection()) as c:
            rows=c.execute("SELECT id,kind,payload,result FROM jobs WHERE discord_id=? AND state='done' AND id>(SELECT COALESCE(MAX(id),0) FROM jobs WHERE discord_id=? AND kind='reset' AND state='done') ORDER BY id DESC",(uid,uid)).fetchall()
        return [dict(row) for row in rows]

    def deleted_resume_dirs(self, uid):
        import json
        return {json.loads(row['result'] or '{}').get('deleted_directory') for row in self.download_jobs(uid)
                if row['kind'] in ('master-delete','application-delete')} - {None}
    def reset_cutoff(self, uid):
        with closing(self.connection()) as c:
            return c.execute("SELECT COALESCE(MAX(id),0) FROM jobs WHERE discord_id=? AND kind='reset' AND state='done'",(uid,)).fetchone()[0]
    def reset_blocked(self, uid):
        with closing(self.connection()) as c:
            row=c.execute("SELECT state FROM jobs WHERE discord_id=? AND kind='reset' ORDER BY id DESC LIMIT 1",(uid,)).fetchone()
        return bool(row and row['state']!='done')

    def has_pending(self, uid):
        with closing(self.connection()) as c:
            return c.execute("SELECT 1 FROM jobs WHERE discord_id=? AND state IN ('queued','running') LIMIT 1",(uid,)).fetchone() is not None
