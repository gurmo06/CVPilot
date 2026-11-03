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
        with self.tx() as c:
            if kind in ('reset','revert'):
                if c.execute("SELECT 1 FROM jobs WHERE discord_id=? AND state IN ('queued','running') LIMIT 1",(uid,)).fetchone():
                    raise ValueError('Wait for your queued/running jobs to finish before resetting or reverting. Use /cvpilot status.')
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
    def recover(self):
        with self.tx() as c:
            c.execute("UPDATE jobs SET state='queued',started_at=NULL WHERE state='running'")
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

    def latest_uploads(self, uid):
        import json
        with closing(self.connection()) as c:
            rows=c.execute("SELECT result FROM jobs WHERE discord_id=? AND kind='master-upload' AND state='done' AND id>(SELECT COALESCE(MAX(id),0) FROM jobs WHERE discord_id=? AND kind='reset' AND state='done') ORDER BY id DESC",(uid,uid)).fetchall()
        uploads={}
        for row in rows:
            result=json.loads(row['result'] or '{}')
            name=result.get('label')
            if name and name not in uploads: uploads[name]=result
        return uploads
    def latest_download(self, uid):
        import json
        with closing(self.connection()) as c:
            rows=c.execute("SELECT id,result FROM jobs WHERE discord_id=? AND state='done' AND id>(SELECT COALESCE(MAX(id),0) FROM jobs WHERE discord_id=? AND kind='reset' AND state='done') ORDER BY id DESC",(uid,uid)).fetchall()
        return next((row['id'] for row in rows if json.loads(row['result'] or '{}').get('artifacts')),None)

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
