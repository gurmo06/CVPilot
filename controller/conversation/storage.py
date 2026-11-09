"""Private disk-first history with a bounded, five-minute working-set cache.

The budget is a conservative UTF-8 byte upper bound on transcript tokens,
not a claim to measure the provider's KV cache or system prompt. No history is
silently trimmed. A full generation waits for the user's compact/wipe choice.
"""
from contextlib import contextmanager
from pathlib import Path
import json, sqlite3, threading, time

IDLE_SECONDS=300
SCHEMA='''
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS contexts (
 uid TEXT PRIMARY KEY, generation INTEGER NOT NULL DEFAULT 1,
 token_limit INTEGER NOT NULL DEFAULT 24000, used INTEGER NOT NULL DEFAULT 0,
 last_user REAL NOT NULL, summary TEXT NOT NULL DEFAULT '', pending TEXT,
 compacted INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT NOT NULL, generation INTEGER NOT NULL,
 role TEXT NOT NULL, text TEXT NOT NULL, cost INTEGER NOT NULL, created REAL NOT NULL,
 event_key TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS events_owner ON events(uid,generation,id);
CREATE TABLE IF NOT EXISTS uploads (uid TEXT PRIMARY KEY, files TEXT NOT NULL, expires REAL NOT NULL);
CREATE TABLE IF NOT EXISTS turns (
 id INTEGER PRIMARY KEY AUTOINCREMENT, uid TEXT NOT NULL, generation INTEGER NOT NULL,
 event_id INTEGER NOT NULL, text TEXT NOT NULL, files TEXT,
 kind TEXT NOT NULL DEFAULT 'chat', state TEXT NOT NULL DEFAULT 'queued', error TEXT,
 message_id TEXT UNIQUE, created REAL NOT NULL
);
'''


def cost(text):return len(text.encode('utf-8'))+12


class Contexts:
    def __init__(self,path,clock=time.time):
        self.path=Path(path);self.clock=clock;self.lock=threading.RLock();self.cache={}
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.db() as c:c.executescript(SCHEMA)
        self.path.chmod(0o600)
    @contextmanager
    def db(self):
        c=sqlite3.connect(str(self.path),timeout=30,isolation_level=None);c.row_factory=sqlite3.Row
        try:yield c
        finally:c.close()
    @contextmanager
    def tx(self):
        with self.lock,self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            try:yield c;c.execute('COMMIT')
            except Exception:c.execute('ROLLBACK');raise
    def ensure(self,c,uid):
        c.execute('INSERT OR IGNORE INTO contexts(uid,last_user) VALUES(?,?)',(uid,self.clock()))
        return dict(c.execute('SELECT * FROM contexts WHERE uid=?',(uid,)).fetchone())
    def expire(self):
        with self.lock,self.db() as c:
            c.execute('DELETE FROM uploads WHERE expires<=?',(self.clock(),))
            for uid,entry in list(self.cache.items()):
                if self.clock()-entry['last_user']>=IDLE_SECONDS:del self.cache[uid]
    def info(self,uid):
        self.expire()
        with self.tx() as c:
            row=self.ensure(c,uid)
            active=c.execute("SELECT COUNT(*) FROM turns WHERE uid=? AND state IN ('queued','running')",(uid,)).fetchone()[0]
            last=c.execute('SELECT id,kind,state,error FROM turns WHERE uid=? ORDER BY id DESC LIMIT 1',(uid,)).fetchone()
        row.pop('pending',None);row.pop('summary',None)
        return row|{'residency':'resident' if uid in self.cache else 'on-disk','blocked':row['used']>=row['token_limit'],'pending_turns':active,'compacted':bool(row['compacted']),'last_turn':dict(last) if last else None}
    def append(self,uid,role,text,*,key=None,user_activity=False):
        with self.tx() as c:
            row=self.ensure(c,uid)
            cursor=c.execute('INSERT OR IGNORE INTO events(uid,generation,role,text,cost,created,event_key) VALUES(?,?,?,?,?,?,?)',
                (uid,row['generation'],role,text,cost(text),self.clock(),key))
            if cursor.rowcount:
                c.execute('UPDATE contexts SET used=used+? WHERE uid=?',(cost(text),uid))
            if user_activity:c.execute('UPDATE contexts SET last_user=? WHERE uid=?',(self.clock(),uid))
            eid=cursor.lastrowid if cursor.rowcount else c.execute('SELECT id FROM events WHERE event_key=?',(key,)).fetchone()[0]
            entry=self.cache.get(uid)
            if entry and entry['generation']==row['generation']:
                if cursor.rowcount:entry['events'].append({'id':eid,'role':role,'text':text})
                if user_activity:entry['last_user']=self.clock()
            elif entry:self.cache.pop(uid,None)
        return eid
    def snapshot(self,uid,*,through=None,generation=None):
        self.expire()
        with self.lock,self.db() as c:
            row=c.execute('SELECT * FROM contexts WHERE uid=?',(uid,)).fetchone()
            if not row:return {'generation':0,'summary':'','events':[]}
            row=dict(row)
            if generation is not None and generation!=row['generation']:
                raise ValueError('Conversation changed. Submit the request again in your current context.')
            entry=self.cache.get(uid)
            if not entry or entry['generation']!=row['generation']:
                events=[dict(x) for x in c.execute('SELECT id,role,text FROM events WHERE uid=? AND generation=? ORDER BY id',(uid,row['generation']))]
                entry={'generation':row['generation'],'summary':row['summary'],'events':events,'last_user':row['last_user']}
                # Idle background work reads disk without retaining a working set.
                if self.clock()-row['last_user']<IDLE_SECONDS:self.cache[uid]=entry
            return {'generation':entry['generation'],'summary':entry['summary'],
                'events':[dict(x) for x in entry['events'] if through is None or x['id']<=through]}
    def queue(self,uid,text,*,message_id=None,files=None):
        with self.tx() as c:
            row=self.ensure(c,uid)
            if message_id:
                prior=c.execute('SELECT id FROM turns WHERE message_id=? AND uid=?',(message_id,uid)).fetchone()
                if prior:return prior[0]
            if c.execute("SELECT COUNT(*) FROM turns WHERE uid=? AND state IN ('queued','running')",(uid,)).fetchone()[0]>=3:
                raise ValueError('You already have three chat turns pending. Wait for a reply.')
            if row['used']+cost(text)>=row['token_limit']:raise ValueError(choice())
            eid=c.execute('INSERT INTO events(uid,generation,role,text,cost,created) VALUES(?,?,?,?,?,?)',
                (uid,row['generation'],'user',text,cost(text),self.clock())).lastrowid
            c.execute('UPDATE contexts SET used=used+?,last_user=? WHERE uid=?',(cost(text),self.clock(),uid))
            tid=c.execute('INSERT INTO turns(uid,generation,event_id,text,files,message_id,created) VALUES(?,?,?,?,?,?,?)',
                (uid,row['generation'],eid,text,json.dumps(files) if files else None,message_id,self.clock())).lastrowid
            entry=self.cache.get(uid)
            if entry and entry['generation']==row['generation']:
                entry['events'].append({'id':eid,'role':'user','text':text});entry['last_user']=self.clock()
            elif entry:self.cache.pop(uid,None)
        return tid
    def upload(self,uid,files=...):
        with self.tx() as c:
            c.execute('DELETE FROM uploads WHERE expires<=?',(self.clock(),))
            if files is ...:
                row=c.execute('SELECT files FROM uploads WHERE uid=?',(uid,)).fetchone()
                return json.loads(row[0]) if row else None
            if files:c.execute('INSERT OR REPLACE INTO uploads(uid,files,expires) VALUES(?,?,?)',(uid,json.dumps(files),self.clock()+600))
            else:c.execute('DELETE FROM uploads WHERE uid=?',(uid,))
    def compact_turn(self,uid):
        with self.tx() as c:
            row=self.ensure(c,uid)
            if c.execute("SELECT 1 FROM turns WHERE uid=? AND state IN ('queued','running')",(uid,)).fetchone():raise ValueError('Wait for your pending chat replies before compacting.')
            return c.execute("INSERT INTO turns(uid,generation,event_id,text,kind,created) VALUES(?,?,0,'','compact',?)",(uid,row['generation'],self.clock())).lastrowid
    def claim(self):
        with self.tx() as c:
            row=c.execute("SELECT * FROM turns WHERE state='queued' ORDER BY id LIMIT 1").fetchone()
            if row:c.execute("UPDATE turns SET state='running' WHERE id=?",(row['id'],))
        return dict(row) if row else None
    def finish(self,tid,error=None):
        with self.tx() as c:c.execute("UPDATE turns SET state=?,error=?,files=NULL WHERE id=?",('failed' if error else 'done',str(error)[:1000] if error else None,tid))
    def recover(self):
        # Never replay an admitted action after a process crash: a repository job
        # may already have been queued. The user can inspect status and retry.
        with self.tx() as c:c.execute("UPDATE turns SET state='failed',error='Interrupted; inspect job status before retrying',files=NULL WHERE state='running'")
    def pending(self,uid,value=...):
        with self.tx() as c:
            self.ensure(c,uid)
            if value is ...:
                raw=c.execute('SELECT pending FROM contexts WHERE uid=?',(uid,)).fetchone()[0]
                item=json.loads(raw) if raw else None
                return item if item and item['expires']>self.clock() else None
            c.execute('UPDATE contexts SET pending=? WHERE uid=?',(json.dumps(value) if value else None,uid))
    def replace(self,uid,generation,summary='',*,wipe=False,turn_id=-1,through=None):
        with self.tx() as c:
            row=self.ensure(c,uid)
            if row['generation']!=generation:raise ValueError('Context changed during compaction; try again.')
            if c.execute("SELECT 1 FROM turns WHERE uid=? AND state='running' AND id!=?",(uid,turn_id)).fetchone():raise ValueError('Wait for your current chat reply before changing context.')
            if through is not None and c.execute('SELECT COALESCE(MAX(id),0) FROM events WHERE uid=? AND generation=?',(uid,generation)).fetchone()[0]!=through:raise ValueError('New conversation activity arrived during compaction. Your original context was kept; try again.')
            if cost(summary)>=row['token_limit']//2:raise ValueError('Summary is too large; context was preserved.')
            c.execute('UPDATE contexts SET generation=generation+1,summary=?,used=?,pending=NULL,compacted=?,last_user=? WHERE uid=?',
                (summary,cost(summary) if summary else 0,0 if wipe else 1,self.clock(),uid))
            c.execute("UPDATE turns SET state='cancelled',files=NULL WHERE uid=? AND state='queued'",(uid,))
            c.execute('DELETE FROM uploads WHERE uid=?',(uid,))
            self.cache.pop(uid,None)
    def set_limit(self,uid,limit):
        if type(limit) is not int or not 2048<=limit<=64000:raise ValueError('Use a context limit between 2048 and 64000 tokens.')
        with self.tx() as c:self.ensure(c,uid);c.execute('UPDATE contexts SET token_limit=? WHERE uid=?',(limit,uid))
    def inventory(self):
        self.expire()
        with self.db() as c:uids=[r[0] for r in c.execute('SELECT uid FROM contexts ORDER BY last_user DESC')]
        return [self.info(uid) for uid in uids]


def choice():
    return ('Your conversation context is full. Model work is paused. Choose `/cvpilot context operation:compact` '
            '(summaries can lose details and reduce accuracy), or `/cvpilot context operation:wipe` '
            '(starts fresh; your master profile, resumes and Git history stay).')

_INSTANCES={};_LOCK=threading.Lock()
def get(root):
    path=Path(root).resolve()/'contexts.sqlite'
    with _LOCK:
        if path not in _INSTANCES:_INSTANCES[path]=Contexts(path)
        return _INSTANCES[path]


def housekeeping(root,stop):
    contexts=get(root)
    while not stop.wait(5):contexts.expire()
