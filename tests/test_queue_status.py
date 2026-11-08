"""Relative FIFO positions, global load and private user details in temporary SQLite."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import test_controller as fixtures
from store import Store
import dispatch
import queue_status
import test_resume_attachments as attachments
import test_resume_delete as deletion_fixtures

UID=fixtures.UID
OTHER=fixtures.UID2


class QueueStatusTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.store=Store(Path(temp.name)/'jobs.sqlite')
        with self.store.tx() as c:
            for uid in (UID,OTHER):
                c.execute('INSERT INTO users(discord_id,repo,agent,master_edit) VALUES(?,?,?,1)',
                          (uid,'CV-Pilot/cvpilot-'+uid,'career-'+uid))

    def call(self,action,args=None,uid=UID):
        return dispatch.process({'sender_id':uid,'action':action,'args':args or {}},self.store)

    def test_permanent_id_and_current_position_are_independent_of_finished_history(self):
        for _ in range(17):
            jid=self.store.enqueue(UID,'profile-sync',{});self.store.finish(jid,'done',{})
        first=self.store.enqueue(OTHER,'tailor',{'url':'https://example.invalid/private-posting'})
        self.assertEqual(self.store.claim()['id'],first)
        self.store.enqueue(OTHER,'profile-edit',{'instructions':'OTHER PRIVATE FACTS'})
        response=self.call('profile',{'operation':'sync'})
        self.assertEqual(response['queue'],{'job_id':20,'state':'queued','position':3,'ahead':2,'running':1,'waiting':2,'total':3})
        self.assertIn('#20',response['text']);self.assertIn('Queue position 3 of 3 pending; 2 ahead.',response['text'])
        self.assertIn('1 running, 2 waiting',response['text'])
        self.assertNotIn('PRIVATE FACTS',json.dumps(response));self.assertNotIn('private-posting',json.dumps(response))

    def test_positions_advance_after_completion_failure_and_claim(self):
        first=self.store.enqueue(OTHER,'tailor',{});self.store.claim()
        second=self.store.enqueue(OTHER,'tailor',{})
        mine=self.store.enqueue(UID,'profile-sync',{})
        self.assertEqual(self.store.queue_snapshot(UID,mine)['jobs'][0]['position'],3)
        self.store.finish(first,'failed',error='private other-user error')
        snap=self.store.queue_snapshot(UID,mine);self.assertEqual(snap['jobs'][0]['position'],2)
        self.assertEqual((snap['running'],snap['waiting'],snap['total']),(0,2,2))
        self.assertEqual(self.store.claim()['id'],second);self.store.finish(second,'done',{})
        self.assertEqual(self.store.queue_snapshot(UID,mine)['jobs'][0]['position'],1)
        self.assertEqual(self.store.claim()['id'],mine)
        response=self.call('status');self.assertIn(f'#{mine} — profile-sync: running',response['text']);self.assertIn('Running now.',response['text'])
        self.assertNotRegex(response['text'],r'Queue position [0-9]')
        self.store.finish(mine,'done',{})
        self.assertEqual(self.store.queue_snapshot(UID,mine)['jobs'][0]['position'],None)
        self.assertIn('0 running, 0 waiting (0 pending)',self.call('status')['text'])

    def test_running_work_counts_ahead_even_if_its_id_is_newer(self):
        mine=self.store.enqueue(UID,'profile-sync',{});other=self.store.enqueue(OTHER,'tailor',{})
        with self.store.tx() as c:c.execute("UPDATE jobs SET state='running' WHERE id=?",(other,))
        snap=self.store.queue_snapshot(UID,mine)
        self.assertEqual(snap['jobs'][0]['position'],2)
        self.assertEqual(snap['running'],1)

    def test_status_exposes_only_global_counts_and_callers_job_details(self):
        foreign=self.store.enqueue(OTHER,'tailor',{'url':'https://other-private.invalid'})
        self.store.finish(foreign,'failed',{'url':'https://private-result.invalid'},error='PRIVATE OTHER ERROR')
        self.store.enqueue(OTHER,'tailor',{})
        own=self.store.enqueue(UID,'profile-sync',{})
        response=self.call('status');text=json.dumps(response)
        self.assertIn('2 pending',text);self.assertIn(f'#{own}',text);self.assertIn('profile-sync: queued',text)
        for secret in ('other-private','private-result','PRIVATE OTHER ERROR',OTHER):self.assertNotIn(secret,text)
        with self.assertRaisesRegex(ValueError,'your account'):self.store.queue_snapshot(UID,foreign)

    def test_user_without_jobs_can_see_aggregate_load_without_other_jobs(self):
        self.store.enqueue(OTHER,'tailor',{})
        response=self.call('status')
        self.assertIn('No jobs yet.',response['text']);self.assertIn('1 waiting (1 pending)',response['text'])
        self.assertNotIn('tailor',response['text']);self.assertNotIn('#1',response['text'])

    def test_latest_job_is_shown_and_active_load_remains_visible(self):
        first=self.store.enqueue(UID,'profile-sync',{})
        for _ in range(12):
            jid=self.store.enqueue(UID,'profile-edit',{});self.store.finish(jid,'done',{})
        response=self.call('status')
        self.assertEqual(response['status_view']['job_id'],jid)
        self.assertIn('1 waiting',response['text'])
        self.assertNotIn(f'#{first} ',response['text'])
        selected=self.call('status',{'job_id':first})
        self.assertIn('Queue position 1 of 1',selected['text'])

    def test_long_own_queue_shows_one_job_and_load(self):
        for _ in range(15):jid=self.store.enqueue(UID,'profile-sync',{})
        response=self.call('status')
        self.assertEqual(response['status_view']['job_id'],jid)
        self.assertEqual(response['status_view']['count'],15)
        self.assertEqual(response['status_view']['older'],14)
        self.assertIsNone(response['status_view']['newer'])
        self.assertIn('15 waiting',response['text']);self.assertLessEqual(len(response['text']),1900)

    def test_completed_result_and_error_can_be_opened_individually(self):
        complete=self.store.enqueue(UID,'profile-sync',{})
        self.store.finish(complete,'done',{'url':'https://github.com/example/repo/commit/abc'})
        failed=self.store.enqueue(UID,'master-edit',{})
        self.store.finish(failed,'failed',error='Compilation failed')
        text=self.call('status')['text']
        self.assertIn('Compilation failed',text);self.assertNotRegex(text,r'Queue position [0-9]')
        older=self.call('status',{'job_id':failed,'direction':'older'})
        self.assertIn('https://github.com/example/repo/commit/abc',older['text'])
        self.assertEqual(older['status_view']['job_id'],complete)

    def test_long_link_remains_whole_in_full_report(self):
        url='https://github.com/example/repository/commit/'+'a'*600
        for _ in range(8):
            jid=self.store.enqueue(UID,'tailor',{});self.store.finish(jid,'done',{'url':url})
        response=self.call('status')
        self.assertLessEqual(len(response['text']),1900)
        self.assertIn(url,response['status_detail']['report'])
        self.assertEqual(response['status_view']['job_id'],jid)

    def test_acknowledgement_handles_worker_start_or_finish_before_response(self):
        jid=self.store.enqueue(UID,'profile-sync',{});self.store.claim()
        started=queue_status.acknowledgement(self.store,UID,jid,f'Queued as #{jid}.')
        self.assertIn('Running now.',started['text']);self.assertEqual(started['queue']['state'],'running')
        self.store.finish(jid,'done',{})
        finished=queue_status.acknowledgement(self.store,UID,jid,f'Queued as #{jid}.')
        self.assertIn('Already completed',finished['text']);self.assertIsNone(finished['queue']['position'])

    def test_counts_and_positions_use_one_snapshot_when_another_connection_finishes_work(self):
        first=self.store.enqueue(OTHER,'tailor',{});mine=self.store.enqueue(UID,'profile-sync',{})
        writer=Store(self.store.path);original=self.store.connection;changed=[]
        class Connection:
            def __init__(self):self.real=original()
            def __getattr__(self,name):return getattr(self.real,name)
            def execute(self,sql,*args):
                cursor=self.real.execute(sql,*args)
                if 'COUNT(*) AS total' in sql and not changed:
                    changed.append(True);writer.finish(first,'done',{})
                return cursor
        with mock.patch.object(self.store,'connection',side_effect=Connection):
            snapshot=self.store.queue_snapshot(UID,mine)
        self.assertEqual(snapshot['total'],2);self.assertEqual(snapshot['jobs'][0]['position'],2)
        self.assertEqual(self.store.queue_snapshot(UID,mine)['total'],1)

    def test_recovery_preserves_fifo_position_and_failed_jobs_do_not_count(self):
        first=self.store.enqueue(OTHER,'tailor',{});self.store.claim()
        mine=self.store.enqueue(UID,'profile-sync',{})
        self.store.recover()
        snapshot=self.store.queue_snapshot(UID,mine)
        self.assertEqual(snapshot['jobs'][0]['position'],2)
        self.assertEqual((snapshot['running'],snapshot['waiting']),(0,2))
        self.assertEqual(self.store.claim()['id'],first)

    def test_all_normal_enqueue_paths_and_existing_init_include_relative_queue(self):
        cases=[('tailor',{'url':'https://example.org/job'}),
               ('master',{'operation':'edit','label':'general','instructions':'Clarify verified experience'}),
               ('master',{'operation':'upload','label':'general','files':attachments.attachments(('cv.pdf',attachments.PDF))}),
               ('profile',{'operation':'sync'}),('profile',{'operation':'edit','instructions':'Verified new facts'})]
        for action,args in cases:
            result=self.call(action,args);self.assertIn('Queue position',result['text']);self.assertIn('Queue load:',result['text'])
        new='323456789012345678'
        first=self.call('init',{'github_username':'new-user'},uid=new)
        again=self.call('init',{'github_username':'new-user'},uid=new)
        self.assertEqual(first['queue']['job_id'],again['queue']['job_id'])
        self.assertIn('already queued',again['text']);self.assertIn('Queue position',again['text'])

    def test_invalid_job_id_cannot_query_another_queue_item(self):
        for jid in (True,0,-1,'1'):
            with self.assertRaises(ValueError):self.store.queue_snapshot(UID,jid)


class ConfirmedQueueTests(unittest.TestCase):
    def setUp(self):
        self.flow=deletion_fixtures.ResumeDeleteTests('runTest');self.flow.setUp()
        self.addCleanup(self.flow.doCleanups)

    def test_delete_and_revert_confirmations_report_global_position(self):
        _,uploaded=self.flow.upload()
        store=self.flow.store
        store.enqueue(deletion_fixtures.OTHER,'profile-sync',{})
        store.claim()
        preview=self.flow.call('master-delete',{'label':'general'})
        response=self.flow.call('master-delete',{'operation':'confirm','token':preview['confirmation_token'],'confirmation':'DELETE'})
        self.assertIn('Queue position 2 of 2 pending; 1 ahead.',response['text'])
        self.assertIn('1 running, 1 waiting',response['text'])

    def test_revert_and_reset_confirmations_include_relative_queue(self):
        _,uploaded=self.flow.upload();store=self.flow.store
        store.enqueue(deletion_fixtures.OTHER,'profile-sync',{})
        preview=self.flow.call('revert',{'commit':uploaded['commit']})
        response=self.flow.call('revert',{'operation':'confirm','token':preview['confirmation_token'],'confirmation':'REVERT'})
        self.assertIn('Queue position 2 of 2 pending',response['text'])
        store.finish(response['queue']['job_id'],'failed',error='fixture only; no actual revert')
        preview=self.flow.call('reset')
        response=self.flow.call('reset',{'operation':'confirm','token':preview['confirmation_token'],'confirmation':'RESET'})
        self.assertIn('Queue position 2 of 2 pending',response['text'])

    def test_application_delete_confirmation_includes_position_without_running_delete(self):
        _,path,application=self.flow.app();store=self.flow.store
        store.enqueue(deletion_fixtures.OTHER,'profile-sync',{})
        preview=self.flow.call('application-delete',{'link':application['url']})
        response=self.flow.call('application-delete',{'operation':'confirm','token':preview['confirmation_token'],'confirmation':'DELETE'})
        self.assertIn('Queue position 2 of 2 pending',response['text'])
        self.assertTrue(self.flow.tree(path))


if __name__=='__main__':unittest.main()
