"""Descriptions reflect actual staged changes and remain stable across retries."""
import json
from pathlib import Path
from unittest import mock
import unittest

import test_resume_delete as fixtures
import test_resume_attachments as attachments
import commit_messages
import main_commits
import worker

UID=fixtures.UID


class CommitMessageTests(unittest.TestCase):
    def setUp(self):
        self.flow=fixtures.ResumeDeleteTests('runTest');self.flow.setUp()
        self.addCleanup(self.flow.doCleanups)
        self.fixture=self.flow.fixture;self.store=self.flow.store

    def message(self,oid):
        return self.flow.git('-C',str(self.fixture.remote),'show','-s','--format=%B',oid)

    def test_upload_identifies_label_formats_and_added_files(self):
        _,result=self.flow.upload('teaching');message=self.message(result['commit'])
        self.assertIn('Upload master teaching (PDF + TeX)',message.splitlines()[0])
        self.assertIn('(job 1)',message.splitlines()[0])
        self.assertIn('Files: 2 added, 0 updated, 0 deleted.',message)
        self.assertIn('Added: master-resumes/teaching/resume.pdf',message)
        self.assertIn('Added: master-resumes/teaching/resume.tex',message)
        self.assertNotIn(attachments.TEX.decode(),message)

    def test_pdf_only_replacement_explains_update_and_tex_removal(self):
        self.flow.upload('general')
        _,result=self.flow.upload('general',attachments.attachments(('new.pdf',attachments.PDF+b'new')))
        message=self.message(result['commit'])
        self.assertIn('Replace master general (PDF)',message.splitlines()[0])
        self.assertNotIn('TeX',message.splitlines()[0])
        self.assertIn('Updated: master-resumes/general/resume.pdf',message)
        self.assertIn('Deleted: master-resumes/general/resume.tex',message)
        self.assertIn('Files: 0 added, 1 updated, 1 deleted.',message)

    def test_deleted_master_is_named_in_title_and_every_removed_path_is_listed(self):
        self.flow.upload('nursing')
        result=self.flow.finish(self.flow.confirmed('master-delete',label='nursing'))
        message=self.message(result['commit'])
        self.assertIn('Delete master nursing (PDF + TeX)',message.splitlines()[0])
        self.assertIn('Deleted: master-resumes/nursing/resume.pdf',message)
        self.assertIn('Deleted: master-resumes/nursing/resume.tex',message)

    def test_application_deletion_names_original_application_and_companions(self):
        jid,path,app=self.flow.app()
        result=self.flow.finish(self.flow.confirmed('application-delete',commit=app['commit']))
        message=self.message(result['commit'])
        self.assertIn(f'Delete application job-{jid} (PDF + TeX)',message.splitlines()[0])
        for name in ('resume.pdf','resume.tex','notes.md','job-url.txt'):
            self.assertIn('Deleted: '+path+'/'+name,message)

    def test_revert_of_deletion_keeps_id_and_explains_restored_master(self):
        self.flow.upload('general')
        deleted=self.flow.finish(self.flow.confirmed('master-delete',label='general'))
        result=self.fixture.finish(self.fixture.revert_job(deleted['commit']))
        message=self.message(result['commit'])
        self.assertIn(f'Revert {deleted["commit"][:12]}: restore master general',message.splitlines()[0])
        self.assertIn('Reverts commit: '+deleted['commit'],message)
        self.assertIn('Original change: '+self.message(deleted['commit']).splitlines()[0],message)
        self.assertIn('Added: master-resumes/general/resume.pdf',message)
        self.assertIn('Added: master-resumes/general/resume.tex',message)

    def test_revert_of_upload_explains_removal_and_revert_of_revert_explains_restoration(self):
        _,uploaded=self.flow.upload('arts')
        reverted=self.fixture.finish(self.fixture.revert_job(uploaded['commit']))
        message=self.message(reverted['commit'])
        self.assertIn('remove master arts',message.splitlines()[0])
        self.assertIn('Original change: CVPilot: Upload master arts',message)
        restored=self.fixture.finish(self.fixture.revert_job(reverted['commit']))
        self.assertIn('restore master arts',self.message(restored['commit']).splitlines()[0])

    def test_profile_edit_includes_requested_change_and_only_actual_modified_files(self):
        _,result=self.fixture.profile('Verified professional experience')
        message=self.message(result['commit'])
        self.assertIn('Update verified profile facts',message.splitlines()[0])
        self.assertIn('Requested change: Update profile with verified facts',message)
        self.assertIn('Added: profile/MASTER_PROFILE.md',message)
        self.assertNotIn('profile_sources.json',message)

    def test_tailoring_metadata_uses_actual_directory_and_strips_url_credentials_and_tracking(self):
        w=mock.Mock();w.run_git.return_value='A\0applications/2026/job-8/resume.pdf\0A\0applications/2026/job-8/resume.tex\0'
        message=commit_messages.build(w,UID,'repo','tailor',8,{'url':'https://name:secret@example.invalid/role?token=secret#fragment'})
        self.assertIn('Tailor application job-8 (PDF + TeX)',message.splitlines()[0])
        self.assertIn('Job posting: https://example.invalid/role',message)
        self.assertNotIn('secret',message);self.assertNotIn('fragment',message)

    def test_noop_upload_does_not_create_an_empty_metadata_commit(self):
        _,first=self.flow.upload();_,same=self.flow.upload()
        self.assertEqual(first['commit'],same['commit']);self.assertFalse(same['changed'])
        self.assertEqual(self.flow.git('-C',str(self.fixture.remote),'rev-list','--count','main'),'2')

    def test_recovery_after_message_journal_or_commit_preserves_full_message(self):
        for phase in ('message','commit'):
            with self.subTest(phase=phase):
                self.store.enqueue(UID,'profile-edit',{'instructions':'Add verified '+phase});job=self.store.claim()
                original_write=worker._write_intent;original_git=worker.run_git
                def write(path,record):
                    original_write(path,record)
                    if phase=='message' and 'commit_message' in record:raise SystemExit('After message journal')
                def git(uid,repo,*args,**kwargs):
                    result=original_git(uid,repo,*args,**kwargs)
                    if phase=='commit' and args[0]=='commit':raise SystemExit('After commit')
                    return result
                with (mock.patch.object(worker,'_write_intent',side_effect=write),
                      mock.patch.object(worker,'run_git',side_effect=git),
                      mock.patch.object(worker,'run',side_effect=self.fixture.profile_agent('Facts '+phase))):
                    with self.assertRaises(SystemExit):worker.work(job,self.store)
                record=main_commits.load(worker,job,self.store)
                expected=record['commit_message']
                with mock.patch.object(worker,'run_resume_job',side_effect=AssertionError('No model rerun')):
                    recovered=main_commits.recover(worker,job,self.store)
                self.assertEqual(self.message(recovered['commit']),expected)
                self.store.finish(job['id'],'done',recovered);worker.cleanup_completed(UID,job['id'])

    def test_legacy_interrupted_commit_remains_recoverable_with_original_message(self):
        jid=self.store.enqueue(UID,'profile-edit',{});job=self.store.claim()
        worker.new_worktree(UID,jid,'profile-edit');record=main_commits.begin(worker,job,self.store)
        tree=worker.gitworktree(UID,jid);path=tree/'profile/MASTER_PROFILE.md';path.parent.mkdir(parents=True,exist_ok=True);path.write_text('Legacy facts')
        worker.run_git(UID,tree,'add','-A')
        record['draft']={'note':'legacy','artifacts':[]};worker._write_intent(main_commits.journal_path(worker,job),record)
        old=f'CVPilot profile-edit job {jid}';worker.run_git(UID,tree,'commit','-m',old)
        recovered=main_commits.recover(worker,job,self.store)
        self.assertEqual(self.message(recovered['commit']),old)

    def test_recovery_rejects_changed_body_even_if_subject_and_parent_match(self):
        jid=self.store.enqueue(UID,'profile-edit',{});job=self.store.claim()
        worker.new_worktree(UID,jid,'profile-edit');record=main_commits.begin(worker,job,self.store)
        tree=worker.gitworktree(UID,jid);(tree/'note.txt').write_text('Validated output')
        worker.run_git(UID,tree,'add','-A')
        record.update(draft={'note':'done','artifacts':[]},commit_message='CVPilot: Update profile (job 1)\n\nExpected body')
        worker._write_intent(main_commits.journal_path(worker,job),record)
        worker.run_git(UID,tree,'commit','-m','CVPilot: Update profile (job 1)\n\nDifferent body')
        before=self.flow.main()
        with self.assertRaisesRegex(ValueError,'checkout changed'):main_commits.recover(worker,job,self.store)
        self.assertEqual(self.flow.main(),before)

    def test_newlines_in_paths_and_request_cannot_forge_metadata_lines(self):
        w=mock.Mock();w.run_git.return_value='A\0profile/file\nForged: yes.md\0'
        message=commit_messages.build(w,UID,'repo','profile-edit',1,{'instructions':'Change facts\n\nForged: yes'})
        self.assertIn('file\\nForged: yes.md',message)
        self.assertNotIn('\nForged:',message)

    def test_large_messages_use_private_temporary_file_and_cleanup_on_success_or_failure(self):
        message='CVPilot: Detailed change\n\n'+('Added: example/path.tex\n'*10000)
        for fail in (False,True):
            w=mock.Mock();seen=[]
            def run(uid,repo,*args):
                self.assertNotIn('-m',args);self.assertIn('--allow-empty',args)
                path=Path(args[args.index('--file')+1]);seen.append(path)
                self.assertEqual(path.stat().st_mode&0o777,0o600)
                self.assertEqual(path.read_text(),message)
                if fail:raise RuntimeError('commit rejected')
            w.run_git.side_effect=run
            if fail:
                with self.assertRaises(RuntimeError):commit_messages.commit(w,UID,'repo',message,allow_empty=True)
            else:commit_messages.commit(w,UID,'repo',message,allow_empty=True)
            self.assertFalse(seen[0].exists())


if __name__=='__main__':unittest.main()
