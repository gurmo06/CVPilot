"""Shared history, limits, disk lifecycle, chat dispatch and consent boundaries."""
import json, os, tempfile, unittest
from pathlib import Path
from unittest import mock
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'controller'))
from conversation.storage import Contexts,get,cost
from conversation import runtime
from store import Store
from dispatch import process
import worker
UID='123456789012345678';OTHER='223456789012345678'


def report(value):return {'payloads':[{'text':json.dumps(value)}]}


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.now=1000.;self.root=Path(self.temp.name)
        self.c=Contexts(self.root/'contexts.sqlite',clock=lambda:self.now)
    def test_disk_restart_and_cross_user_isolation(self):
        self.c.append(UID,'user','my detailed work',user_activity=True)
        self.c.append(OTHER,'user','private other experience',user_activity=True)
        restarted=Contexts(self.c.path,clock=lambda:self.now)
        self.assertEqual(restarted.snapshot(UID)['events'][0]['text'],'my detailed work')
        self.assertNotIn('private other',json.dumps(restarted.snapshot(UID)))
        self.assertEqual(self.c.path.stat().st_mode&0o777,0o600)
    def test_idle_exactly_five_minutes_evicts_and_activity_reloads(self):
        self.c.append(UID,'user','kept on disk',user_activity=True);self.c.snapshot(UID)
        self.assertEqual(self.c.info(UID)['residency'],'resident')
        self.now+=299;self.c.expire();self.assertIn(UID,self.c.cache)
        self.now+=1;self.c.expire();self.assertNotIn(UID,self.c.cache)
        self.assertEqual(self.c.snapshot(UID)['events'][0]['text'],'kept on disk');self.assertNotIn(UID,self.c.cache)
        self.c.append(UID,'user','back',user_activity=True);self.c.snapshot(UID);self.assertIn(UID,self.c.cache)
    def test_background_result_does_not_activate_idle_context(self):
        self.c.append(UID,'user','old',user_activity=True);self.now+=301
        self.c.append(UID,'job result','finished');self.c.snapshot(UID);self.assertNotIn(UID,self.c.cache)
    def test_budget_blocks_incoming_chat_without_silent_history_trim(self):
        self.c.set_limit(UID,2048);self.c.append(UID,'user','a'*2000)
        with self.assertRaisesRegex(ValueError,'compacted|compact'):self.c.queue(UID,'b'*100)
        self.assertEqual(len(self.c.snapshot(UID)['events']),1)
    def test_duplicate_message_and_result_events_are_idempotent(self):
        a=self.c.queue(UID,'hello',message_id='323456789012345678')
        b=self.c.queue(UID,'hello',message_id='323456789012345678');self.assertEqual(a,b)
        for _ in range(2):self.c.append(UID,'job result','done',key='unique')
        self.assertEqual(len(self.c.snapshot(UID)['events']),2)
    def test_context_wipe_keeps_archive_and_other_user(self):
        self.c.append(UID,'user','old');self.c.append(OTHER,'user','other')
        self.c.replace(UID,1,wipe=True);self.assertEqual(self.c.snapshot(UID)['events'],[])
        with self.c.db() as c:self.assertEqual(c.execute('SELECT text FROM events WHERE uid=?',(UID,)).fetchone()[0],'old')
        self.assertEqual(self.c.snapshot(OTHER)['events'][0]['text'],'other')
    def test_summary_preserved_and_racing_activity_rejects_compaction(self):
        eid=self.c.append(UID,'user','important')
        self.c.append(UID,'user','arrived during compaction')
        with self.assertRaisesRegex(ValueError,'activity'):self.c.replace(UID,1,'summary',through=eid)
        self.c.replace(UID,1,'summary');self.assertEqual(self.c.snapshot(UID)['summary'],'summary')
    def test_huge_or_stale_summary_preserves_current_context(self):
        self.c.set_limit(UID,2048)
        with self.assertRaises(ValueError):self.c.replace(UID,1,'x'*2000)
        with self.assertRaises(ValueError):self.c.replace(UID,99,'small')
        self.assertEqual(self.c.info(UID)['generation'],1)
    def test_pending_chat_limit_and_crash_recovery_do_not_replay_actions(self):
        for i in range(3):self.c.queue(UID,str(i))
        with self.assertRaisesRegex(ValueError,'three'):self.c.queue(UID,'four')
        turn=self.c.claim();self.c.recover()
        with self.c.db() as c:self.assertEqual(c.execute('SELECT state FROM turns WHERE id=?',(turn['id'],)).fetchone()[0],'failed')
    def test_pending_upload_expires_and_wipe_clears_it(self):
        self.c.upload(UID,[{'file':'private'}]);self.assertTrue(self.c.upload(UID))
        self.now+=601;self.assertIsNone(self.c.upload(UID))
        self.c.upload(UID,[{'file':'private'}]);self.c.replace(UID,1,wipe=True);self.assertIsNone(self.c.upload(UID))
    def test_model_does_not_get_future_events_in_queued_snapshot(self):
        first=self.c.append(UID,'user','first');self.c.append(UID,'user','later')
        self.assertEqual(len(self.c.snapshot(UID,through=first)['events']),1)
    def test_admin_limit_bounds(self):
        for value in (0,2047,64001,True,'8192'):
            with self.assertRaises(ValueError):self.c.set_limit(UID,value)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=Store(self.root/'jobs.sqlite');self.c=get(self.root)
        with self.store.tx() as c:c.execute('INSERT INTO users(discord_id,repo,agent) VALUES(?,?,?)',(UID,'CV-Pilot/cvpilot-'+UID,'career-'+UID))
        self.notify=mock.patch.object(worker,'notify').start();self.addCleanup(mock.patch.stopall)
        mock.patch.object(worker,'repopath',return_value=self.root/'absent').start()
    def call(self,action,args=None,uid=UID):return process({'sender_id':uid,'action':action,'args':args or {}},self.store)
    def turn(self,text='hello'):
        self.call('chat',{'text':text});return self.c.claim()
    def test_commands_jobs_and_chat_share_one_history(self):
        self.call('tailor',{'url':'https://example.org/jobs/1'})
        job=self.store.claim();self.store.finish(job['id'],'failed',error='compiler failed')
        turn=self.turn('why did that fail?')
        with mock.patch.object(runtime,'model',return_value=report({'reply':'I can inspect the failure.','action':None})) as model:
            runtime.handle_turn(turn,self.store,worker)
        prompt=model.call_args.args[3]
        self.assertIn('compiler failed',prompt);self.assertIn('tailor',prompt)
        self.assertIn('why did that fail?',prompt)
        projection=runtime.job_context(job,self.root)
        self.assertIn('https://example.org/jobs/1',projection);self.assertNotIn('why did that fail?',projection)
    def test_history_failure_cannot_turn_published_job_into_failure(self):
        jid=self.store.enqueue(UID,'tailor',{'url':'https://example.org/job'})
        with mock.patch.object(runtime,'record_result',side_effect=OSError('disk problem')),mock.patch('builtins.print'):
            self.store.finish(jid,'done',{'url':'https://github.com/example/commit/abc'})
        self.assertEqual(self.store.job(jid)['state'],'done')

    def test_completed_active_chat_stays_resident_until_idle_deadline(self):
        turn=self.turn('help me understand my profile')
        with mock.patch.object(runtime,'model',return_value=report({'reply':'What part of your experience would you like to discuss?','action':None})):
            runtime.handle_turn(turn,self.store,worker)
        self.assertEqual(self.c.info(UID)['residency'],'resident')
        self.assertEqual(self.c.snapshot(UID)['events'][-1]['role'],'assistant')
        last=self.c.info(UID)['last_user']
        with mock.patch.object(self.c,'clock',return_value=last+301):
            self.c.expire();self.assertEqual(self.c.info(UID)['residency'],'on-disk')
        self.assertIn('experience',self.c.snapshot(UID)['events'][-1]['text'])

    def test_context_wipe_invalidates_old_job_conversation_projection(self):
        self.call('tailor',{'url':'https://example.org/jobs/1'});job=self.store.claim()
        self.call('context',{'operation':'wipe'})
        self.assertEqual(runtime.job_context(job,self.root),'');self.assertEqual(self.store.job(job['id'])['state'],'running')
    def test_chat_plan_uses_same_typed_controller_operation(self):
        turn=self.turn('tailor my resume for https://example.org/jobs/1')
        value={'action':{'name':'tailor','args':{'url':'https://example.org/jobs/1'}}}
        with mock.patch.object(runtime,'model',return_value=report(value)):
            result=runtime.handle_turn(turn,self.store,worker)
        job=self.store.claim();self.assertEqual(job['kind'],'tailor');self.assertIn('queued',result['text'])
    def test_model_cannot_supply_confirmation_or_another_identity(self):
        for args in ({'operation':'confirm'},{'token':'fake'},{'sender_id':OTHER},{'files':[]}):
            with self.subTest(args=args):
                turn=self.turn()
                with mock.patch.object(runtime,'model',return_value=report({'action':{'name':'reset','args':args}})):
                    with self.assertRaisesRegex(ValueError,'rejected'):runtime.handle_turn(turn,self.store,worker)
                self.c.finish(turn['id'])
        self.assertIsNone(self.store.claim())
    def test_model_cannot_access_admin_control(self):
        turn=self.turn()
        with mock.patch.object(runtime,'model',return_value=report({'action':{'name':'contexts-admin','args':{}}})):
            with self.assertRaisesRegex(ValueError,'Unsupported'):runtime.handle_turn(turn,self.store,worker)
    def test_chat_reset_requires_human_word_and_queues_once(self):
        turn=self.turn('reset my repository')
        with mock.patch.object(runtime,'model',return_value=report({'action':{'name':'reset','args':{}}})):
            result=runtime.handle_turn(turn,self.store,worker)
        self.assertIn('type RESET',result['text']);self.assertIsNone(self.store.claim())
        self.c.finish(turn['id'])
        with mock.patch.object(runtime,'model') as model:
            response=self.call('chat',{'text':'RESET'});model.assert_not_called()
        self.assertTrue(response['chat_immediate']);self.assertEqual(self.store.claim()['kind'],'reset')
        self.assertIsNone(self.store.claim())
    def test_budget_read_commands_still_work_and_new_model_jobs_pause(self):
        self.c.set_limit(UID,2048);self.c.append(UID,'assistant','x'*2048)
        self.assertIn('github.com',self.call('repo')['text'])
        with self.assertRaisesRegex(ValueError,'full'):self.call('tailor',{'url':'https://example.org/x'})
        self.assertIsNone(self.store.claim())
    def test_compaction_is_explicit_queued_and_preserves_original_archive(self):
        self.c.append(UID,'user','my detailed work',user_activity=True)
        result=self.call('context',{'operation':'compact'});self.assertIn('accuracy',result['text'])
        turn=self.c.claim();self.assertEqual(turn['kind'],'compact')
        with mock.patch.object(runtime,'model',return_value=report({'summary':'Detailed work was discussed; not yet confirmed.'})):
            runtime.handle_turn(turn,self.store,worker)
        self.assertEqual(self.c.info(UID)['generation'],2);self.assertIn('not yet confirmed',self.c.snapshot(UID)['summary'])
        with self.c.db() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM events WHERE generation=1').fetchone()[0],1)
    def test_admin_inventory_and_scoped_limit_require_owner(self):
        self.c.info(UID);self.c.info(OTHER)
        with mock.patch.dict(os.environ,{'CVPILOT_OWNER_DISCORD_ID':UID}):
            with self.assertRaisesRegex(ValueError,'Administrator'):self.call('contexts-admin',uid=OTHER)
            result=self.call('contexts-admin',{'user_id':OTHER,'limit':4096})
            self.assertEqual(result['contexts'][0]['token_limit'],4096)
        self.assertEqual(self.c.info(UID)['token_limit'],24000)
    def test_invalid_model_json_does_not_queue_operation(self):
        turn=self.turn()
        with mock.patch.object(runtime,'model',return_value={'payloads':[{'text':'Done!'}]}):
            with self.assertRaises(ValueError):runtime.handle_turn(turn,self.store,worker)
        self.assertIsNone(self.store.claim())
    def test_upload_bytes_are_not_logged_in_shared_context(self):
        files=[{'filename':'source.tex','content_base64':'XGRvY3VtZW50Y2xhc3N7YXJ0aWNsZX0='}]
        self.call('chat',{'text':'save this as my general resume','files':files})
        turn=self.c.claim()
        with mock.patch.object(runtime,'model',return_value=report({'action':{'name':'master','args':{'operation':'upload','label':'general'}}})):
            runtime.handle_turn(turn,self.store,worker)
        self.assertEqual(self.store.claim()['kind'],'master-upload')
        self.assertNotIn(files[0]['content_base64'],json.dumps(self.c.snapshot(UID)))
        self.assertIsNone(self.c.upload(UID))

    def test_retained_upload_accepts_observed_placeholder_and_uses_discord_bytes(self):
        import resumes
        files=[{'filename':'source.tex','content_base64':'XGRvY3VtZW50Y2xhc3N7YXJ0aWNsZX0='}]
        self.call('chat',{'text':'Can you upload this resume under the label it','files':files})
        first=self.c.claim()
        with mock.patch.object(runtime,'model',return_value=report({'reply':'Which label?','action':None})):
            runtime.handle_turn(first,self.store,worker)
        self.c.finish(first['id'])
        turn=self.turn('it')
        observed={'reply':'I have queued it.','action':{'name':'master','args':{'operation':'upload','label':'it','files':'[attachments omitted]'}}}
        with mock.patch.object(runtime,'model',return_value=report(observed)) as model:
            result=runtime.handle_turn(turn,self.store,worker)
        self.assertIn('available_resume_attachments',model.call_args.args[3])
        self.assertIn('source.tex',model.call_args.args[3])
        job=self.store.claim();data=json.loads(job['payload'])
        self.assertEqual(job['kind'],'master-upload');self.assertEqual(data['role'],'it')
        self.assertEqual(resumes.staged_files(self.root,UID,data),resumes.validate_files(files))
        self.assertIn('queued as job',result['text']);self.assertIsNone(self.c.upload(UID))
        self.assertIsNone(self.store.claim())

    def test_model_attachment_bytes_cannot_replace_the_authenticated_upload(self):
        import resumes
        files=[{'filename':'source.tex','content_base64':'XGRvY3VtZW50Y2xhc3N7YXJ0aWNsZX0='}]
        self.call('chat',{'text':'Upload this under the label sales','files':files});turn=self.c.claim()
        value={'action':{'name':'master','args':{'operation':'upload','label':'sales',
               'files':[{'filename':'fake.tex','content_base64':'not trusted bytes'}]}}}
        with mock.patch.object(runtime,'model',return_value=report(value)):
            runtime.handle_turn(turn,self.store,worker)
        job=self.store.claim()
        self.assertEqual(resumes.staged_files(self.root,UID,json.loads(job['payload'])),resumes.validate_files(files))

    def test_model_files_without_a_real_discord_attachment_do_not_queue_upload(self):
        turn=self.turn('Upload my resume under the label sales')
        value={'action':{'name':'master','args':{'operation':'upload','label':'sales','files':'[attachments omitted]'}}}
        with mock.patch.object(runtime,'model',return_value=report(value)):
            with self.assertRaisesRegex(ValueError,'Attach the PDF/TeX'):runtime.handle_turn(turn,self.store,worker)
        self.assertIsNone(self.store.claim())

    def test_another_users_pending_attachment_cannot_be_bound_to_upload(self):
        files=[{'filename':'source.tex','content_base64':'XGRvY3VtZW50Y2xhc3N7YXJ0aWNsZX0='}]
        self.c.upload(UID,files)
        with self.store.tx() as c:c.execute('INSERT INTO users(discord_id,repo,agent) VALUES(?,?,?)',(OTHER,'CV-Pilot/cvpilot-'+OTHER,'career-'+OTHER))
        self.call('chat',{'text':'Upload the resume under the label sales'},uid=OTHER);turn=self.c.claim()
        with mock.patch.object(runtime,'model',return_value=report({'action':{'name':'master','args':{'operation':'upload','label':'sales','files':'[attachments omitted]'}}})):
            with self.assertRaisesRegex(ValueError,'Attach the PDF/TeX'):runtime.handle_turn(turn,self.store,worker)
        self.assertIsNone(self.store.claim());self.assertEqual(self.c.upload(UID),files)

    def test_upload_binding_still_rejects_model_identity_and_confirmation(self):
        for extra in ({'sender_id':OTHER},{'token':'fake'},{'confirmation':'CONFIRM'},{'operation':'confirm'}):
            with self.subTest(extra=extra):
                self.c.upload(UID,[{'filename':'source.tex','content_base64':'XGRvY3VtZW50Y2xhc3N7YXJ0aWNsZX0='}])
                turn=self.turn('Upload under the label general')
                args={'operation':'upload','label':'general','files':'[attachments omitted]'}|extra
                with mock.patch.object(runtime,'model',return_value=report({'action':{'name':'master','args':args}})):
                    with self.assertRaisesRegex(ValueError,'rejected'):runtime.handle_turn(turn,self.store,worker)
                self.c.finish(turn['id'])
        self.assertIsNone(self.store.claim())

    def test_model_files_are_rejected_before_profile_edit_argument_conversion(self):
        turn=self.turn('Save my confirmed experience')
        value={'action':{'name':'profile','args':{'operation':'edit','instructions':'My experience','files':'[attachments omitted]'}}}
        with mock.patch.object(runtime,'model',return_value=report(value)):
            with self.assertRaisesRegex(ValueError,'attachments were rejected'):runtime.handle_turn(turn,self.store,worker)
        self.assertIsNone(self.store.claim())

    def test_explicit_label_and_attachment_metadata_are_supplied_to_planner(self):
        files=[{'filename':'source.tex','content_base64':'XGRvY3VtZW50Y2xhc3N7YXJ0aWNsZX0='}]
        self.call('chat',{'text':'Can you upload this resume under the label it','files':files});turn=self.c.claim()
        with mock.patch.object(runtime,'model',return_value=report({'action':{'name':'master','args':{'operation':'upload','label':'it'}}})) as model:
            runtime.handle_turn(turn,self.store,worker)
        prompt=model.call_args.args[3]
        self.assertIn('"explicit_upload_label": "it"',prompt)
        self.assertIn('use arguments the user has already supplied without asking for them again',prompt)
        self.assertNotIn(files[0]['content_base64'],prompt)

    def test_short_labels_and_named_argument_syntax_are_general(self):
        for text,wanted in [('Can you upload this under the label it','it'),
                ('Upload with label "sales-2027".','sales-2027'),('Upload label:healthcare','healthcare'),
                ('Use this under the label IT.','it'),('Upload using the label `and`.','and'),
                ('Can you label it?',None),('Do I need a label?',None),('Upload label:invalid_label',None)]:
            with self.subTest(text=text):self.assertEqual(runtime.explicit_upload_label(text),wanted)

    def test_history_does_not_advertise_attachment_placeholders_as_action_args(self):
        args={'operation':'upload','label':'it','files':[{'content_base64':'private'}],'token':'secret','confirmation':'CONFIRM'}
        self.assertEqual(runtime.safe_args(args),{'operation':'upload','label':'it'})


if __name__=='__main__':unittest.main()

class OnboardingTests(unittest.TestCase):
    setUp=RuntimeTests.setUp
    call=RuntimeTests.call
    # A separate method exercises onboarding without exposing the operator's
    # main workspace to a user who has no agent/repository yet.
    def test_chat_onboarding_does_not_use_operator_model_context(self):
        turn_id=self.call('chat',{'text':'initialize sample-user'},uid=OTHER)['chat_turn']
        turn=self.c.claim()
        with mock.patch.object(runtime,'model',side_effect=AssertionError('No shared main agent')):
            runtime.handle_turn(turn,self.store,worker)
        job=self.store.claim();self.assertEqual(job['discord_id'],OTHER);self.assertEqual(job['kind'],'init')
