"""Draft first, optional follow-ups, and compatibility with previously held jobs."""
import json
from contextlib import closing
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
QUESTIONS=[{'term':'First aid','question':'Have you completed first-aid training?'}]
NOTES='Company: School\nPosition: Teacher\nQuestion: First aid | Have you completed first-aid training?'


class AutonomousTailoringTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.MainCommitTests('runTest');self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.store=self.fixture.store;self.call=self.fixture.call;q.PENDING.clear()
        upload,_=self.fixture.fixture.upload();worker.cleanup_completed(UID,upload['id'])
        self.jid=self.store.enqueue(UID,'tailor',{'url':'https://jobs.example/teaching'})
        self.before=self.fixture.main()

    @staticmethod
    def compile_pdf(task,tex):tex.with_suffix('.pdf').write_bytes(attachments.PDF)

    def legacy_pause(self):
        """Seed the persisted format of a job held by the former implementation."""
        self.store.claim()
        worker._write_intent(self.store.path.parent/'job-reviews'/UID/f'{self.jid}.json',
                             {'job_id':self.jid,'signature':'a'*64,'questions':QUESTIONS})
        self.store.finish(self.jid,'needs-input',{'questions':QUESTIONS})

    def complete(self,notes=NOTES):
        job=self.store.claim();self.assertEqual(job['id'],self.jid)
        with (mock.patch.object(worker,'agent_completion',return_value=response(notes=notes)) as agent,
              mock.patch.object(worker,'compile_pdf',side_effect=self.compile_pdf)):
            result=worker.work(job,self.store)
        self.store.finish(self.jid,'done',result);self.assertEqual(agent.call_count,1)
        return result,agent.call_args.args[1]

    def test_optional_questions_accompany_published_files_without_pausing_or_saving_facts(self):
        stop=threading.Event();sent=[]
        def notify(uid,text,**kwargs):sent.append((text,kwargs));stop.set()
        with (mock.patch.object(worker,'agent_completion',return_value=response(notes=NOTES)) as agent,
              mock.patch.object(worker,'compile_pdf',side_effect=self.compile_pdf),
              mock.patch.object(worker,'notify',side_effect=notify)):
            worker.forever(self.store,stop)
        job=self.store.job(self.jid);result=json.loads(job['result'])
        self.assertEqual(job['state'],'done');self.assertEqual(agent.call_count,1)
        self.assertEqual(result['questions'],QUESTIONS);self.assertEqual(self.fixture.main(),result['commit'])
        self.assertNotEqual(self.fixture.main(),self.before)
        self.assertEqual({item['path'].rsplit('/',1)[-1] for item in result['artifacts']},{'resume.pdf','resume.tex'})
        self.assertEqual({name for name,_ in sent[0][1]['files']},{'resume.pdf','resume.tex'})
        self.assertIn('Optional follow-ups',sent[0][0]);self.assertIn('/cvpilot answer',sent[0][0])
        self.assertFalse(self.store.has_pending(UID))
        changes=self.fixture.git('-C',str(self.fixture.remote),'diff','--name-only',self.before,'main').splitlines()
        self.assertTrue(all(name.startswith('applications/') for name in changes))
        self.assertFalse(self.fixture.git('-C',str(self.fixture.remote),'ls-tree','main','--','profile/profile_sources.json'))
        page=self.call('status',{'job_id':self.jid})
        self.assertIn('Optional follow-ups',page['status_detail']['report'])
        self.assertNotIn('Awaiting your input',page['status_detail']['report'])

    def test_answer_then_continue_creates_a_new_draft_from_fresh_facts_preserving_original(self):
        original,_=self.complete();old_id=self.jid;old_row=self.store.job(old_id)
        preview=self.call('answer',{'job_id':old_id,'question':1,'details':'I completed first-aid training during supervised school placements.'})
        self.call('answer',{'operation':'confirm','token':preview['confirmation_token'],'confirmation':'CONFIRM'})
        with self.assertRaisesRegex(ValueError,'queued/running'):self.call('continue',{'job_id':old_id})
        save=self.store.claim();self.assertEqual(save['kind'],'profile-confirm');self.fixture.finish(save)
        queued=self.call('continue',{'job_id':old_id});self.assertIn('Updated draft requested',queued['text'])
        with closing(self.store.connection()) as c:
            row=c.execute("SELECT id FROM jobs WHERE state='queued'").fetchone()
        self.jid=row['id'];self.assertNotEqual(self.jid,old_id)
        result,prompt=self.complete(notes='Company: School\nPosition: Teacher\nUpdated from confirmed evidence.')
        self.assertIn('I completed first-aid training',prompt)
        self.assertEqual(self.store.job(old_id),old_row)
        self.assertEqual(result['questions'],[])
        files=worker.artifact_files(worker.repopath(UID),original)
        self.assertEqual({name for name,_ in files},{'resume.pdf','resume.tex'})

    def test_legacy_job_can_continue_without_answers_and_without_another_review(self):
        self.legacy_pause();self.assertEqual(self.store.queue_snapshot(UID)['total'],0)
        page=self.call('status',{'job_id':self.jid});self.assertIn('No answers are required',page['status_detail']['report'])
        self.call('continue',{'job_id':self.jid});result,prompt=self.complete(notes='No additional questions.')
        self.assertIn('Unanswered qualifications remain unconfirmed',prompt)
        self.assertEqual(result['questions'],[])
        self.assertNotIn('First aid',result['note'])

    def test_changed_sources_are_used_when_a_legacy_job_continues(self):
        self.legacy_pause();self.fixture.external_edit('master-resumes/general/resume.tex',TEX+'\nNew supported source evidence.')
        self.call('continue',{'job_id':self.jid});result,prompt=self.complete()
        self.assertIn('New supported source evidence',prompt);self.assertTrue(result['changed'])

    def test_foreign_pending_and_reset_jobs_cannot_continue(self):
        self.complete()
        with self.assertRaisesRegex(ValueError,'own tailoring'):self.call('continue',{'job_id':self.jid},uid=OTHER)
        self.store.enqueue(UID,'profile-sync',{})
        with self.assertRaisesRegex(ValueError,'queued/running'):self.call('continue',{'job_id':self.jid})
        job=self.store.claim();self.store.finish(job['id'],'done',{})
        reset=self.store.enqueue(UID,'reset',{});self.store.finish(reset,'done',{})
        with self.assertRaisesRegex(ValueError,'last reset'):self.call('continue',{'job_id':self.jid})

    def test_no_gaps_drafts_in_one_call_without_questions(self):
        result,_=self.complete(notes='Supported classroom evidence.');self.assertEqual(result['questions'],[])
        self.assertNotIn('/cvpilot answer',result['note'])

    def test_new_followups_are_limited_to_two_while_legacy_questions_remain_readable(self):
        notes='\n'.join(f'Question: Topic {n} | Have you done task {n}?' for n in range(1,5))
        result,_=self.complete(notes=notes);self.assertEqual(len(result['questions']),2)
        legacy=self.store.enqueue(UID,'tailor',{'url':'https://jobs.example/legacy'})
        self.store.claim();self.store.finish(legacy,'needs-input',{'questions':q.extract(notes)})
        self.assertEqual(len(q.questions_for(self.store.job(legacy),self.store.path.parent)),3)

    def test_compiler_failure_keeps_optional_questions_but_does_not_publish(self):
        job=self.store.claim()
        with (mock.patch.object(worker,'agent_completion',return_value=response(notes=NOTES)),
              mock.patch.object(worker,'compile_pdf',side_effect=RuntimeError('Compiler unavailable')),
              mock.patch.object(main_commits,'publish') as publish):
            with self.assertRaisesRegex(RuntimeError,'Compiler unavailable'):worker.work(job,self.store)
        self.store.finish(self.jid,'failed',error='Compiler unavailable')
        publish.assert_not_called();self.assertEqual(self.fixture.main(),self.before)
        self.assertEqual(q.questions_for(self.store.job(self.jid),self.store.path.parent),QUESTIONS)
        result=self.call('continue',{'job_id':self.jid});self.assertIn('Updated draft requested',result['text'])

    def test_continuation_reuses_posting_url_validation(self):
        self.store.claim()
        self.store.finish(self.jid,'failed',error='Old job')
        with self.store.tx() as c:c.execute('UPDATE jobs SET payload=? WHERE id=?',(json.dumps({'url':'http://127.0.0.1/private'}),self.jid))
        with self.assertRaisesRegex(ValueError,'HTTPS'):self.call('continue',{'job_id':self.jid})
        self.assertFalse(self.store.has_pending(UID))


if __name__=='__main__':unittest.main()
