"""Direct main publication and confirmed inverse commits; all state/Git are temporary."""
import json
import shutil
from pathlib import Path
import sys
from unittest import mock
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'controller'))
import main_commits
import repo_history
import worker
import test_resume_attachments as fixtures

UID=fixtures.UID
OTHER=fixtures.OTHER


class MainCommitTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.ResumeWorkflowTests('runTest');self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root=self.fixture.root;self.store=self.fixture.store
        self.git=self.fixture.git;self.remote=self.fixture.remote
        self.call=self.fixture.call
        repo_history.CONFIRMATIONS.clear()
        self.info={'id':42,'name':'cvpilot-'+UID,'owner':{'login':'CV-Pilot'},'private':True,
                   'description':'CVPilot managed workspace [test]','default_branch':'main'}
        gh=mock.Mock(org='CV-Pilot');gh.get_repo.side_effect=lambda name:dict(self.info)
        self.patch(worker,'GitHub',lambda:gh)
        self.patch(repo_history,'verified_origin',lambda w,uid,repo,name:str(self.remote))

    def patch(self,target,name,value):
        p=mock.patch.object(target,name,value);p.start();self.addCleanup(p.stop)

    def main(self):return self.git('-C',str(self.remote),'rev-parse','main')

    def external_edit(self,name,contents):
        seed=self.fixture.seed
        self.git('-C',str(seed),'pull','--ff-only','origin','main')
        file=seed/name;file.parent.mkdir(parents=True,exist_ok=True);file.write_text(contents)
        self.git('-C',str(seed),'add','-A')
        self.git('-C',str(seed),'commit','-m','External update')
        self.git('-C',str(seed),'push','origin','main')
        return self.main()

    def profile_agent(self,contents):
        original=worker.run
        def agent(argv,**kwargs):
            if argv[0]=='openclaw':
                task=next((worker.ROOT/'user-workspaces'/UID/'tasks').iterdir())
                path=task/'profile/MASTER_PROFILE.md';path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text(contents);return ''
            return original(argv,**kwargs)
        return agent

    def profile(self,contents):
        jid=self.store.enqueue(UID,'profile-sync',{});job=self.store.claim()
        with mock.patch.object(worker,'run',side_effect=self.profile_agent(contents)):
            result=worker.work(job,self.store)
        self.store.finish(jid,'done',result);worker.cleanup_completed(UID,jid)
        return job,result

    def revert_job(self,commit):
        preview=self.call('revert',{'commit':commit})
        self.call('revert',{'operation':'confirm','token':preview['confirmation_token'],'confirmation':'REVERT'})
        return self.store.claim()

    def finish(self,job):
        result=worker.work(job,self.store);self.store.finish(job['id'],'done',result)
        worker.cleanup_completed(UID,job['id']);return result

    def test_profile_changes_commit_to_main_and_unchanged_jobs_do_not_add_commits(self):
        _,result=self.profile('Verified work')
        self.assertEqual(self.main(),result['commit'])
        self.assertEqual(self.git('-C',str(self.remote),'show','main:profile/MASTER_PROFILE.md'),'Verified work')
        self.assertEqual(self.git('-C',str(self.remote),'for-each-ref','--format=%(refname)','refs/heads'),'refs/heads/main')
        _,unchanged=self.profile('Verified work')
        self.assertEqual(unchanged['commit'],result['commit'])
        self.assertIn('No material change',unchanged['note'])
        self.assertEqual(self.git('-C',str(self.remote),'rev-list','--count','main'),'2')

    def test_allowlisted_deletions_commit_to_main_without_publishing_other_agent_changes(self):
        self.profile('Old profile')
        jid=self.store.enqueue(UID,'profile-edit',{'instructions':'Remove outdated profile file'})
        job=self.store.claim();original=worker.run
        def agent(argv,**kwargs):
            if argv[0]=='openclaw':
                task=worker.taskdir(UID,jid)
                (task/'profile/MASTER_PROFILE.md').unlink()
                (task/'README.md').write_text('Unexpected change must not publish')
                return ''
            return original(argv,**kwargs)
        with mock.patch.object(worker,'run',side_effect=agent):result=self.finish(job)
        self.assertEqual(self.main(),result['commit'])
        self.assertEqual(self.git('-C',str(self.remote),'ls-tree','main','--','profile/MASTER_PROFILE.md'),'')
        self.assertEqual(self.git('-C',str(self.remote),'show','main:README.md'),'Private test workspace')

    def test_master_edit_updates_main_and_keeps_downloads_pinned_to_original_commits(self):
        uploaded,old=self.fixture.upload()
        worker.cleanup_completed(UID,uploaded['id'])
        with self.store.tx() as c:c.execute('UPDATE users SET master_edit=1 WHERE discord_id=?',(UID,))
        jid=self.store.enqueue(UID,'master-edit',{'role':'general','instructions':'Update verified experience'})
        job=self.store.claim();original=worker.run;new_pdf=fixtures.PDF+b'new';new_tex=fixtures.TEX+b'new'
        def agent(argv,**kwargs):
            if argv[0]=='openclaw':
                (worker.taskdir(UID,jid)/'master-resumes/general/resume.tex').write_bytes(new_tex);return ''
            return original(argv,**kwargs)
        def compile_pdf(task,tex):tex.with_suffix('.pdf').write_bytes(new_pdf)
        with mock.patch.object(worker,'run',side_effect=agent),mock.patch.object(worker,'compile_pdf',side_effect=compile_pdf):
            result=self.finish(job)
        self.assertEqual(self.main(),result['commit'])
        self.assertEqual(fixtures.resumes.validate_files(self.call('master',{'operation':'get'})['attachments']),{'resume.pdf':new_pdf,'resume.tex':new_tex})
        self.assertEqual(dict(fixtures.resumes.artifact_files(worker.repopath(UID),old)),{'resume.pdf':fixtures.PDF,'resume.tex':fixtures.TEX})

    def test_lost_push_reply_is_verified_without_duplicate_commit(self):
        original=worker.run_git;pushes=[]
        def lost_reply(uid,repo,*args,**kwargs):
            result=original(uid,repo,*args,**kwargs)
            if args[0]=='push':pushes.append(args);raise RuntimeError('lost push reply')
            return result
        with mock.patch.object(worker,'run_git',side_effect=lost_reply):
            _,result=self.fixture.upload()
        self.assertEqual(len(pushes),1);self.assertEqual(self.main(),result['commit'])
        self.assertFalse(any('force' in word for word in pushes[0]))
        self.assertEqual(self.git('-C',str(self.remote),'rev-list','--count','main'),'2')

    def test_publication_recovers_crash_before_commit_or_after_commit_without_rerunning_agent(self):
        for phase in ('draft','result'):
            with self.subTest(phase=phase):
                jid=self.store.enqueue(UID,'profile-sync',{});job=self.store.claim();original=worker._write_intent
                def interrupt(path,record):
                    original(path,record)
                    if phase in record:raise SystemExit('simulated process crash')
                with (mock.patch.object(worker,'_write_intent',side_effect=interrupt),
                      mock.patch.object(worker,'run',side_effect=self.profile_agent('Facts '+phase))):
                    with self.assertRaises(SystemExit):worker.work(job,self.store)
                self.store.recover();again=self.store.claim();original_run=worker.run
                def forbid_agent(argv,**kwargs):
                    if argv[0]=='openclaw':raise AssertionError('agent reran')
                    return original_run(argv,**kwargs)
                with mock.patch.object(worker,'run',side_effect=forbid_agent):result=self.finish(again)
                self.assertEqual(self.main(),result['commit'])
        self.assertEqual(self.git('-C',str(self.remote),'rev-list','--count','main'),'3')

    def test_retry_after_completed_publication_preserves_newer_external_commit(self):
        job,result=self.fixture.upload()
        worker.cleanup_completed(UID,job['id'])
        payload=json.loads(job['payload']);shutil.rmtree(fixtures.resumes.inbox_path(worker.ROOT,UID,payload['upload_id']))
        newer=self.external_edit('notes.txt','Keep newer work')
        with mock.patch.object(worker,'compile_pdf',side_effect=AssertionError('should not rebuild')):
            recovered=worker.run_upload_job(job,self.store)
        self.assertEqual(recovered['commit'],result['commit']);self.assertEqual(self.main(),newer)

    def test_concurrent_external_push_is_preserved_without_creating_job_branches(self):
        original=worker.run_git;newer=[]
        def racing(uid,repo,*args,**kwargs):
            if args[0]=='push' and not newer:newer.append(self.external_edit('notes.txt','Concurrent work'))
            return original(uid,repo,*args,**kwargs)
        with mock.patch.object(worker,'run_git',side_effect=racing):
            with self.assertRaises(RuntimeError):self.fixture.upload()
        self.assertEqual(self.main(),newer[0])
        self.assertEqual(self.git('-C',str(self.remote),'for-each-ref','--format=%(refname)','refs/heads'),'refs/heads/main')

    def test_protected_main_rejection_does_not_fall_back_to_a_branch_or_force_push(self):
        original=worker.run_git;before=self.main();pushes=[]
        def protected(uid,repo,*args,**kwargs):
            if args[0]=='push':pushes.append(args);raise RuntimeError('protected main rejects direct pushes')
            return original(uid,repo,*args,**kwargs)
        with mock.patch.object(worker,'run_git',side_effect=protected):
            with self.assertRaisesRegex(RuntimeError,'protected main'):self.fixture.upload()
        self.assertEqual(self.main(),before);self.assertEqual(len(pushes),1)
        self.assertFalse(any('force' in arg for arg in pushes[0]))

    def test_history_lists_main_ids_and_fits_discord_at_maximum_limit(self):
        for n in range(10):self.external_edit('history.txt','version '+str(n))
        history=self.call('history',{'limit':10})['text']
        self.assertIn(self.main()[:12],history);self.assertIn('/cvpilot revert',history)
        self.assertLessEqual(len(history),1900)
        for limit in (0,11,True,'8'):
            with self.assertRaises(ValueError):self.call('history',{'limit':limit})

    def test_revert_latest_upload_restores_previous_pair_without_stale_overlay(self):
        original,_=self.fixture.upload()
        changed,result=self.fixture.upload(files=fixtures.attachments(('new.pdf',fixtures.PDF+b'new')))
        reverted=self.finish(self.revert_job(result['commit'][:12]))
        self.assertEqual(self.main(),reverted['commit'])
        self.assertNotEqual(self.main(),result['commit'])
        self.assertEqual(fixtures.resumes.validate_files(self.call('master',{'operation':'get'})['attachments']),{'resume.pdf':fixtures.PDF,'resume.tex':fixtures.TEX})
        self.assertEqual(fixtures.resumes.validate_files(self.call('download',{'job_id':changed['id']})['attachments']),{'resume.pdf':fixtures.PDF+b'new'})
        self.git('-C',str(self.remote),'merge-base','--is-ancestor',result['commit'],self.main())
        self.assertEqual(self.store.job(original['id'])['state'],'done')

    def test_revert_nonhead_preserves_later_changes_and_can_revert_the_revert(self):
        _,upload=self.fixture.upload()
        upload_job=self.store.job(1);worker.cleanup_completed(UID,upload_job['id'])
        _,profile=self.profile('Keep these verified facts')
        reverted=self.finish(self.revert_job(upload['commit']))
        self.assertIn('No resume uploaded',self.call('master',{'operation':'get'})['text'])
        self.assertEqual(self.git('-C',str(self.remote),'show','main:profile/MASTER_PROFILE.md'),'Keep these verified facts')
        self.git('-C',str(self.remote),'merge-base','--is-ancestor',profile['commit'],self.main())
        self.finish(self.revert_job(reverted['commit']))
        self.assertEqual(len(self.call('master',{'operation':'get'})['attachments']),2)

    def test_revert_conflict_leaves_main_unchanged(self):
        _,first=self.fixture.upload()
        self.fixture.upload(files=fixtures.attachments(('new.pdf',fixtures.PDF+b'newer')))
        before=self.main();job=self.revert_job(first['commit'])
        with self.assertRaisesRegex(ValueError,'conflicts with later edits'):worker.work(job,self.store)
        self.assertEqual(self.main(),before)

    def test_confirmation_is_bound_to_user_phrase_repository_and_single_use(self):
        _,result=self.fixture.upload();preview=self.call('revert',{'commit':result['commit']})
        token=preview['confirmation_token'];self.assertIsNone(self.store.claim())
        self.assertIn('type REVERT',preview['text'])
        for uid,phrase,nonce in ((OTHER,'REVERT',token),(UID,'revert',token),(UID,'REVERT','wrong')):
            with self.assertRaises(ValueError):self.call('revert',{'operation':'confirm','token':nonce,'confirmation':phrase},uid=uid)
        self.call('revert',{'operation':'confirm','token':token,'confirmation':'REVERT'})
        self.assertEqual(self.store.claim()['kind'],'revert')
        with self.assertRaises(ValueError):self.call('revert',{'operation':'confirm','token':token,'confirmation':'REVERT'})

    def test_cancellation_and_expiration_queue_nothing(self):
        _,result=self.fixture.upload()
        for cancel in (True,False):
            with mock.patch.object(repo_history.time,'monotonic',return_value=10):
                preview=self.call('revert',{'commit':result['commit']})
            token=preview['confirmation_token']
            if cancel:
                with mock.patch.object(repo_history.time,'monotonic',return_value=11):
                    self.call('revert',{'operation':'cancel','token':token})
            with mock.patch.object(repo_history.time,'monotonic',return_value=131):
                with self.assertRaisesRegex(ValueError,'expired'):self.call('revert',{'operation':'confirm','token':token,'confirmation':'REVERT'})
            self.assertIsNone(self.store.claim())

    def test_busy_preview_and_confirmation_refuse_pending_jobs(self):
        _,result=self.fixture.upload();preview=self.call('revert',{'commit':result['commit']})
        self.store.enqueue(UID,'profile-sync',{})
        with self.assertRaisesRegex(ValueError,'Wait'):self.call('revert',{'commit':result['commit']})
        with self.assertRaisesRegex(ValueError,'Wait'):self.call('revert',{'operation':'confirm','token':preview['confirmation_token'],'confirmation':'REVERT'})

    def test_changed_main_before_confirmation_or_execution_requires_fresh_confirmation(self):
        _,result=self.fixture.upload();preview=self.call('revert',{'commit':result['commit']})
        self.external_edit('notes.txt','First edit')
        with self.assertRaisesRegex(ValueError,'changed since the preview'):
            self.call('revert',{'operation':'confirm','token':preview['confirmation_token'],'confirmation':'REVERT'})
        job=self.revert_job(result['commit']);newer=self.external_edit('notes.txt','Second edit')
        with self.assertRaisesRegex(ValueError,'Main changed after confirmation'):worker.work(job,self.store)
        self.assertEqual(self.main(),newer)

    def test_recreated_repository_or_unconfirmed_payload_is_refused(self):
        _,result=self.fixture.upload();job=self.revert_job(result['commit']);self.info['id']=43
        with self.assertRaisesRegex(ValueError,'identity changed'):worker.work(job,self.store)
        payload=json.loads(job['payload']);payload['confirmed']=False;job['payload']=json.dumps(payload)
        with self.assertRaisesRegex(ValueError,'confirmed revert'):worker.work(job,self.store)

    def test_root_off_main_and_nonhex_targets_are_refused(self):
        for commit in (self.fixture.original_main,'HEAD','main','--all','a'*40):
            with self.subTest(commit=commit),self.assertRaises(ValueError):self.call('revert',{'commit':commit})
        self.git('-C',str(self.fixture.seed),'commit','--allow-empty','-m','Side branch only')
        side=self.git('-C',str(self.fixture.seed),'rev-parse','HEAD')
        self.git('-C',str(self.fixture.seed),'push','origin','HEAD:refs/heads/side')
        worker.run_git(UID,worker.repopath(UID),'fetch','origin','side')
        with self.assertRaisesRegex(ValueError,'main history'):self.call('revert',{'commit':side})

    def test_merge_commit_is_refused_without_guessing_a_parent(self):
        seed=self.fixture.seed
        self.git('-C',str(seed),'switch','-c','feature')
        (seed/'feature.txt').write_text('Feature')
        self.git('-C',str(seed),'add','.')
        self.git('-C',str(seed),'commit','-m','Feature')
        self.git('-C',str(seed),'switch','main')
        self.git('-C',str(seed),'merge','--no-ff','feature','-m','Merge feature')
        self.git('-C',str(seed),'push','origin','main')
        with self.assertRaisesRegex(ValueError,'merge commits'):self.call('revert',{'commit':self.main()})

    def test_reset_and_earlier_commits_cannot_be_reverted(self):
        import reset_repo
        self.patch(reset_repo,'verified_origin',lambda w,uid,repo,name:str(self.remote))
        _,before=self.fixture.upload()
        preview=self.call('reset',{})
        self.call('reset',{'operation':'confirm','token':preview['confirmation_token'],'confirmation':'RESET'})
        reset=self.store.claim();reset_result=self.finish(reset)
        self.fixture.upload()
        for oid in (before['commit'],reset_result['commit']):
            with self.subTest(oid=oid),self.assertRaisesRegex(ValueError,'latest repository reset'):
                self.call('revert',{'commit':oid})


if __name__=='__main__':unittest.main()
