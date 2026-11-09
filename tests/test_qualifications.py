"""Missing evidence is a question; only the owner's confirmed answer is a fact."""
import json
from unittest import mock
import unittest
import test_main_commits as fixtures
import tailoring
from test_tailoring import TEX
import qualifications as q
import worker

UID=fixtures.UID;OTHER=fixtures.OTHER


class QualificationTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.MainCommitTests('runTest');self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.store=self.fixture.store;self.call=self.fixture.call;q.PENDING.clear()
        self.jid=self.store.enqueue(UID,'tailor',{'url':'https://jobs.example/nursing'})
        self.store.claim();self.store.finish(self.jid,'done',{'questions':[{'term':'Critical care','question':'Do you have direct critical-care experience? Describe your role and setting.'}]})

    def preview(self,**overrides):
        return self.call('answer',{'job_id':self.jid,'question':1,'details':'I cared for patients in a supervised critical-care placement.',**overrides})

    def confirm(self,preview):
        self.call('answer',{'operation':'confirm','token':preview['confirmation_token'],'confirmation':'CONFIRM'})
        return self.store.claim()

    def test_question_extraction_is_bounded_optional_and_not_a_fact(self):
        notes='Company: A hospital\nQuestion: Critical care | Have you worked in critical care?\n**Question 2:** License | Do you hold the requested license?\nQuestion: Critical care | Duplicate?'
        questions=q.extract(notes);self.assertEqual(len(questions),2)
        self.assertEqual(q.extract('A supported rationale without questions.'),[])
        self.assertIn('/cvpilot answer job_id:7',q.message(7,questions))
        self.assertTrue(all('user_confirmed' not in item for item in questions))

    def test_numbered_questions_from_older_saved_notes_are_not_discarded(self):
        notes='Unconfirmed requirements (questions for the user):\n1. Have you worked in a classroom? Describe your role.\n2. Do you hold the requested credential?'
        self.assertEqual(len(q.extract(notes)),2)
        path=self.store.path.parent/'job-reports'/UID/f'{self.jid}.json'
        worker._write_intent(path,{'payloads':[{'text':'```cvpilot-notes\n'+notes+'\n```'}]})
        self.store.finish(self.jid,'failed',error='A later stage failed')
        self.assertEqual(len(q.questions_for(self.store.job(self.jid),self.store.path.parent)),2)

    def test_preview_cancel_and_wrong_confirmation_do_not_queue_or_commit(self):
        before=self.fixture.main();preview=self.preview()
        self.assertIn('Your statement: I cared',preview['text']);self.assertIsNone(self.store.claim())
        with self.assertRaisesRegex(ValueError,'CONFIRM'):
            self.call('answer',{'operation':'confirm','token':preview['confirmation_token'],'confirmation':'yes'})
        self.call('answer',{'operation':'cancel','token':preview['confirmation_token']})
        self.assertIsNone(self.store.claim());self.assertEqual(self.fixture.main(),before)

    def test_confirmed_statement_is_committed_exactly_without_a_model_or_master_edits(self):
        self.fixture.external_edit('master-resumes/general/resume.tex',TEX)
        before=self.fixture.main();preview=self.preview();job=self.confirm(preview)
        self.assertEqual(job['kind'],'profile-confirm')
        with mock.patch.object(worker,'agent_completion',side_effect=AssertionError('No model for confirmation')):
            result=self.fixture.finish(job)
        git=self.fixture.git;repo=str(self.fixture.remote)
        metadata=json.loads(git('-C',repo,'show','main:profile/profile_sources.json'))
        fact=next(iter(metadata['facts'].values()))
        self.assertEqual(fact['text'],'I cared for patients in a supervised critical-care placement.')
        self.assertIs(fact['user_confirmed'],True)
        self.assertEqual(fact['confirmation'],{'job_id':self.jid,'question':1})
        self.assertEqual(set(git('-C',repo,'diff','--name-only',before,'main').splitlines()),
                         {'profile/MASTER_PROFILE.md','profile/profile_sources.json'})
        self.assertIn('user-confirmed Critical care',git('-C',repo,'show','-s','--format=%s','main'))
        with self.assertRaisesRegex(ValueError,'already saved'):self.preview()
        task,_=worker.new_worktree(UID,999,'tailor')
        pack=tailoring.prepare(task,result['commit'],mock.Mock())
        self.assertIn(fact['text'],tailoring.source_text(pack))
        self.fixture.finish(self.fixture.revert_job(result['commit']))
        self.assertFalse(git('-C',repo,'ls-tree','main','--','profile/profile_sources.json'))

    def test_existing_provenance_and_user_confirmations_are_preserved(self):
        prior={'text':'Previously confirmed credential','user_confirmed':True}
        self.fixture.external_edit('profile/MASTER_PROFILE.md','# Profile\nExisting facts.')
        metadata={'schema_version':1,'facts':{'prior':prior},'source_manifest':{'master-resumes/general/resume.tex':'a'*64}}
        self.fixture.external_edit('profile/profile_sources.json',json.dumps(metadata))
        result=self.fixture.finish(self.confirm(self.preview()))
        saved=json.loads(self.fixture.git('-C',str(self.fixture.remote),'show','main:profile/profile_sources.json'))
        self.assertEqual(saved['facts']['prior'],prior);self.assertEqual(saved['source_manifest'],metadata['source_manifest'])

    def test_foreign_jobs_tokens_bad_answers_and_bad_question_indices_are_refused(self):
        with self.assertRaisesRegex(ValueError,'own finished'):
            self.call('answer',{'job_id':self.jid,'question':1,'details':'Foreign statement'},uid=OTHER)
        preview=self.preview()
        with self.assertRaisesRegex(ValueError,'expired'):
            self.call('answer',{'operation':'confirm','token':preview['confirmation_token'],'confirmation':'CONFIRM'},uid=OTHER)
        for value in ('yes','no',''):
            with self.assertRaisesRegex(ValueError,'own words'):self.preview(details=value)
        with self.assertRaisesRegex(ValueError,'question number'):self.preview(question=2)

    def test_expired_or_changed_repository_preview_cannot_save(self):
        preview=self.preview();q.PENDING[UID]['expires']=0
        with self.assertRaisesRegex(ValueError,'expired'):self.confirm(preview)
        preview=self.preview();self.fixture.external_edit('new.txt','Concurrent work')
        with self.assertRaisesRegex(ValueError,'Repository changed'):self.confirm(preview)

    def test_reset_and_pending_work_block_old_answers(self):
        preview=self.preview();self.store.enqueue(UID,'profile-sync',{})
        with self.assertRaisesRegex(ValueError,'queued/running'):self.confirm(preview)
        job=self.store.claim();self.store.finish(job['id'],'done',{})
        reset=self.store.enqueue(UID,'reset',{});self.store.finish(reset,'done',{})
        with self.assertRaisesRegex(ValueError,'last reset'):self.preview()

    def test_questions_survive_a_failed_tailoring_job_and_appear_in_status(self):
        record={'job_id':self.jid,'kind':'tailor','questions':[{'term':'Credential','question':'Do you hold this credential?'}]}
        path=self.store.path.parent/'job-questions'/UID/f'{self.jid}.json';worker._write_intent(path,record)
        self.store.finish(self.jid,'failed',error='Compiler failed')
        page=self.call('status',{'job_id':self.jid})
        self.assertIn('Do you hold this credential?',page['status_detail']['report'])
        self.assertIn('Credential',self.preview()['text'])

    def test_interrupted_save_recovers_without_duplicating_commits_or_facts(self):
        job=self.confirm(self.preview());before=self.fixture.main()
        first=worker.work(job,self.store);second=worker.work(job,self.store)
        self.assertEqual(first['commit'],second['commit'])
        self.assertEqual(self.fixture.git('-C',str(self.fixture.remote),'rev-list','--count',before+'..main'),'1')
        self.assertEqual(len(json.loads(self.fixture.git('-C',str(self.fixture.remote),'show','main:profile/profile_sources.json'))['facts']),1)

    def test_repository_identity_change_invalidates_preview(self):
        preview=self.preview();self.fixture.info['id']=43
        with self.assertRaisesRegex(ValueError,'Repository changed'):self.confirm(preview)


if __name__=='__main__':unittest.main()
