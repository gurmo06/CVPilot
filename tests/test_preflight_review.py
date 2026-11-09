"""Review, release the queue for answers, then draft from fresh confirmed facts."""
import json
import threading
import unittest
from unittest import mock
import test_main_commits as fixtures
import test_resume_attachments as attachments
from test_tailoring import response,TEX
import main_commits
import qualifications as q
import worker

UID=fixtures.UID;OTHER=fixtures.OTHER
QUESTIONS=[{'term':'First aid','question':'Do you have first-aid training or practical experience? Describe it.'}]


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.MainCommitTests('runTest');self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.store=self.fixture.store;self.call=self.fixture.call;q.PENDING.clear()
        upload,_=self.fixture.fixture.upload();worker.cleanup_completed(UID,upload['id'])
        self.jid=self.store.enqueue(UID,'tailor',{'url':'https://jobs.example/teaching'})
        self.before=self.fixture.main()
        patch=mock.patch.object(worker,'review_qualifications',side_effect=lambda job,store,pack,posting:q.review(worker,job,store,pack,posting))
        patch.start();self.addCleanup(patch.stop)

    def pause(self,questions=QUESTIONS):
        stop=threading.Event();sent=[]
        def notify(uid,text,**kwargs):sent.append(text);stop.set()
        with (mock.patch.object(worker,'agent_completion',return_value={'final':json.dumps({'questions':questions})}) as agent,
              mock.patch.object(worker,'compile_pdf') as compile_pdf,
              mock.patch.object(worker,'notify',side_effect=notify),mock.patch.object(main_commits,'publish') as publish):
            worker.forever(self.store,stop)
        self.assertEqual(agent.call_count,1);compile_pdf.assert_not_called();publish.assert_not_called()
        return sent

    def complete(self):
        job=self.store.claim();self.assertEqual(job['id'],self.jid)
        def compile_pdf(task,tex):tex.with_suffix('.pdf').write_bytes(attachments.PDF)
        with (mock.patch.object(worker,'agent_completion',return_value=response()) as agent,
              mock.patch.object(worker,'compile_pdf',side_effect=compile_pdf)):
            result=worker.work(job,self.store)
        self.store.finish(self.jid,'done',result);self.assertEqual(agent.call_count,1)
        return result,agent.call_args.args[1]

    def test_questions_pause_before_drafting_or_publication_and_release_queue(self):
        sent=self.pause();job=self.store.job(self.jid)
        self.assertEqual(job['state'],'needs-input');self.assertIsNone(job['error'])
        self.assertEqual(self.store.queue_snapshot(UID)['total'],0);self.assertFalse(self.store.has_pending(UID))
        self.assertEqual(self.fixture.main(),self.before)
        self.assertFalse(main_commits.journal_path(worker,job).exists())
        self.assertIn('/cvpilot answer',sent[0]);self.assertIn('/cvpilot continue',sent[0])
        page=self.call('status',{'job_id':self.jid});self.assertIn('Drafting and publication have not started',page['status_detail']['report'])
        self.store.recover();self.assertEqual(self.store.job(self.jid)['state'],'needs-input')

    def test_confirmed_answer_is_used_when_the_same_job_continues(self):
        self.pause()
        preview=self.call('answer',{'job_id':self.jid,'question':1,'details':'I completed first-aid training during supervised school placements.'})
        self.call('answer',{'operation':'confirm','token':preview['confirmation_token'],'confirmation':'CONFIRM'})
        save=self.store.claim();self.assertEqual(save['kind'],'profile-confirm')
        self.fixture.finish(save)
        self.call('continue',{'job_id':self.jid})
        result,prompt=self.complete()
        self.assertIn('I completed first-aid training',prompt)
        self.assertTrue(result['changed']);self.assertEqual(self.store.job(self.jid)['state'],'done')

    def test_explicit_continue_can_leave_unanswered_skills_unconfirmed(self):
        self.pause();self.call('continue',{'job_id':self.jid})
        result,prompt=self.complete()
        self.assertIn('Unanswered qualifications remain unconfirmed',prompt)
        self.assertFalse(self.fixture.git('-C',str(self.fixture.remote),'ls-tree','main','--','profile/profile_sources.json'))

    def test_changed_sources_force_a_fresh_review_instead_of_using_old_approval(self):
        self.pause();self.fixture.external_edit('master-resumes/general/resume.tex',TEX+'\nNew supported source evidence.')
        self.call('continue',{'job_id':self.jid});self.pause([{'term':'Credential','question':'Do you hold the credential?'}])
        self.assertEqual(self.store.job(self.jid)['state'],'needs-input')
        self.assertEqual(q.questions_for(self.store.job(self.jid),self.store.path.parent)[0]['term'],'Credential')

    def test_foreign_pending_and_reset_jobs_cannot_continue_a_review(self):
        self.pause()
        with self.assertRaisesRegex(ValueError,'own tailoring'):
            self.call('continue',{'job_id':self.jid},uid=OTHER)
        queued=self.store.enqueue(UID,'profile-sync',{})
        with self.assertRaisesRegex(ValueError,'queued/running'):self.call('continue',{'job_id':self.jid})
        job=self.store.claim();self.store.finish(job['id'],'done',{})
        reset=self.store.enqueue(UID,'reset',{});self.store.finish(reset,'done',{})
        with self.assertRaisesRegex(ValueError,'last reset'):self.call('continue',{'job_id':self.jid})

    def test_a_review_without_gaps_proceeds_directly_to_drafting(self):
        job=self.store.claim()
        def compile_pdf(task,tex):tex.with_suffix('.pdf').write_bytes(attachments.PDF)
        with (mock.patch.object(worker,'agent_completion',side_effect=[{'final':'{"questions":[]}'},response()]) as agent,
              mock.patch.object(worker,'compile_pdf',side_effect=compile_pdf)):
            result=worker.work(job,self.store)
        self.assertEqual(agent.call_count,2);self.assertEqual(result['questions'],[])


if __name__=='__main__':unittest.main()
