import tempfile,unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'controller'))
from store import Store
from dispatch import process

UID='123456789012345678'
UID2='223456789012345678'
class ControllerTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.tmp.name)/'jobs.sqlite')
    def tearDown(self):self.tmp.cleanup()
    def call(self,uid,action,args=None):return process({'sender_id':uid,'action':action,'args':args or {}},self.store)
    def register(self,uid=UID):
        with self.store.tx() as c:
            c.execute('INSERT INTO users(discord_id,repo,agent) VALUES(?,?,?)', (uid,'CVPilot/career-'+uid,'career-'+uid))
    def test_init_idempotent_fifo(self):
        a=self.call(UID,'init',{'github_username':'user-one'})
        b=self.call(UID,'init',{'github_username':'user-one'})
        self.assertIn('job #1',a['text'])
        self.assertIn('already queued',b['text'])
        c=self.call(UID2,'init',{'github_username':'user-two'})
        self.assertIn('job #2',c['text'])
        self.assertEqual(self.store.claim()['id'],1)
        self.store.finish(1,'done',{})
        self.assertEqual(self.store.claim()['id'],2)
    def test_user_scope(self):
        self.register();self.register(UID2)
        self.call(UID,'tailor',{'url':'https://example.org/jobs/123'})
        self.assertIn('No jobs',self.call(UID2,'status')['text'])
        self.assertIn('career-'+UID2,self.call(UID2,'repo')['text'])
    def test_master_lock(self):
        self.register()
        with self.assertRaisesRegex(ValueError,'off'):
            self.call(UID,'master',{'operation':'edit','role':'swe','instructions':'Expand confirmed Pintos bullets'})
        self.call(UID,'settings',{'option':'master_edit','value':'on'})
        txt=self.call(UID,'master',{'operation':'edit','role':'swe','instructions':'Expand confirmed Pintos bullets'})['text']
        self.assertIn('#1',txt)
        self.call(UID,'settings',{'option':'master_edit','value':'off'})
        self.assertFalse(self.store.user(UID)['master_edit'])
    def test_identity_and_urls(self):
        with self.assertRaisesRegex(ValueError,'Authenticated'):
            self.call('not-an-id','init',{'github_username':'foo'})
        self.register()
        for url in ('http://example.org','https://localhost/x','https://127.0.0.1/x'):
            with self.assertRaises(ValueError):self.call(UID,'tailor',{'url':url})
    def test_crash_recovery(self):
        self.register();self.call(UID,'profile',{'operation':'sync'})
        self.assertEqual(self.store.claim()['id'],1)
        self.store.recover()
        self.assertEqual(self.store.claim()['id'],1)
if __name__=='__main__':unittest.main()
