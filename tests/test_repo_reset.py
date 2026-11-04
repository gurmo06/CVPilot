"""Reset confirmation and atomic branch tests, using temporary SQLite/local Git."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

APP=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(APP/'controller'))
import dispatch
import reset_repo
VERIFIED_ORIGIN=reset_repo.verified_origin
from store import Store
import worker
import test_resume_attachments as fixtures

UID=fixtures.UID
OTHER=fixtures.OTHER


class ConfirmationTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.store=Store(Path(temporary.name)/'jobs.sqlite')
        dispatch._RESET_CONFIRMATIONS.clear()
        with self.store.tx() as c:
            for uid in (UID,OTHER):
                c.execute('INSERT INTO users(discord_id,repo,agent) VALUES(?,?,?)',
                          (uid,'CV-Pilot/cvpilot-'+uid,'career-'+uid))

    def call(self,operation='prepare',uid=UID,**args):
        return dispatch.process({'sender_id':uid,'action':'reset','args':{'operation':operation,**args}},self.store)

    def test_prepare_warns_without_queuing_or_mutating_user_data(self):
        result=self.call()
        self.assertIn('EVERY other branch',result['text'])
        self.assertIn('type RESET',result['text'])
        self.assertIsNone(self.store.claim())
        self.assertEqual(self.store.user(UID)['repo'],'CV-Pilot/cvpilot-'+UID)

    def test_typed_confirmation_requires_own_token_and_cannot_replay(self):
        token=self.call()['confirmation_token']
        for uid,phrase,nonce in [(OTHER,'RESET',token),(UID,'reset',token),(UID,'RESET','wrong')]:
            with self.subTest(uid=uid,phrase=phrase,nonce=nonce),self.assertRaises(ValueError):
                self.call('confirm',uid=uid,token=nonce,confirmation=phrase)
        self.assertIsNone(self.store.claim())
        result=self.call('confirm',token=token,confirmation='RESET')
        self.assertIn('queued as job #1',result['text'])
        job=self.store.claim()
        self.assertEqual(job['kind'],'reset')
        self.assertEqual(json.loads(job['payload']),{'repo':'CV-Pilot/cvpilot-'+UID,'confirmed':True})
        with self.assertRaises(ValueError):self.call('confirm',token=token,confirmation='RESET')

    def test_cancel_and_expiry_never_queue_reset(self):
        token=self.call()['confirmation_token']
        self.assertIn('cancelled',self.call('cancel',token=token)['text'])
        with self.assertRaises(ValueError):self.call('confirm',token=token,confirmation='RESET')
        with mock.patch.object(dispatch.time,'monotonic',return_value=10):token=self.call()['confirmation_token']
        with mock.patch.object(dispatch.time,'monotonic',return_value=131):
            with self.assertRaisesRegex(ValueError,'expired'):self.call('confirm',token=token,confirmation='RESET')
        self.assertIsNone(self.store.claim())

    def test_repository_change_invalidates_confirmation(self):
        token=self.call()['confirmation_token']
        with self.store.tx() as c:c.execute('UPDATE users SET repo=? WHERE discord_id=?',('CV-Pilot/another',UID))
        with self.assertRaisesRegex(ValueError,'no longer matches'):self.call('confirm',token=token,confirmation='RESET')
        self.assertIsNone(self.store.claim())

    def test_busy_user_refused_and_enqueue_rechecks_after_prepare(self):
        token=self.call()['confirmation_token']
        jid=self.store.enqueue(UID,'tailor',{})
        with self.assertRaisesRegex(ValueError,'Wait'):self.call()
        with self.assertRaisesRegex(ValueError,'Wait'):self.call('confirm',token=token,confirmation='RESET')
        self.store.claim()
        with self.assertRaisesRegex(ValueError,'Wait'):self.call()
        self.store.finish(jid,'done',{})
        self.call('confirm',token=token,confirmation='RESET')

    def test_pending_failed_reset_blocks_user_jobs_but_not_other_accounts(self):
        token=self.call()['confirmation_token'];self.call('confirm',token=token,confirmation='RESET')
        with self.assertRaisesRegex(ValueError,'reset is pending'):self.store.enqueue(UID,'profile-sync',{})
        self.assertIsInstance(self.store.enqueue(OTHER,'profile-sync',{}),int)
        self.assertTrue(dispatch.process({'sender_id':UID,'action':'status'},self.store)['text'])
        self.store.finish(1,'failed',error='simulated reset interruption')
        with self.assertRaises(ValueError):self.store.enqueue(UID,'tailor',{})

    def test_reset_cutoff_keeps_history_but_invalidates_earlier_sources_and_downloads(self):
        old=self.store.enqueue(UID,'master-upload',{})
        self.store.finish(old,'done',{'label':'general','artifacts':[{'path':'master-resumes/general/resume.pdf'}]})
        other=self.store.enqueue(OTHER,'master-upload',{})
        self.store.finish(other,'done',{'label':'other','artifacts':[{'path':'master-resumes/other/resume.pdf'}]})
        token=self.call()['confirmation_token'];self.call('confirm',token=token,confirmation='RESET')
        reset=self.store.claim();self.store.finish(reset['id'],'done',{'reset':True})
        self.assertEqual(self.store.latest_uploads(UID),{})
        self.assertIsNone(self.store.latest_download(UID))
        self.assertEqual(self.store.latest_download(OTHER),other)
        self.assertEqual(self.store.job(old)['state'],'done')
        with self.assertRaisesRegex(ValueError,'before your last'):
            worker.download_job(UID,old,self.store)
        fresh=self.store.enqueue(UID,'master-upload',{})
        self.store.finish(fresh,'done',{'label':'general','artifacts':[{'path':'master-resumes/general/resume.pdf'}]})
        self.assertEqual(self.store.latest_download(UID),fresh)


class ResetWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.ResumeWorkflowTests('runTest');self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.state=self.fixture.state;self.store=self.fixture.store
        self.remote=self.fixture.remote;self.git=self.fixture.git
        self.root=self.fixture.root
        dispatch._RESET_CONFIRMATIONS.clear()
        self.info={'id':42,'name':'cvpilot-'+UID,'owner':{'login':'CV-Pilot'},'private':True,
                   'description':'CVPilot managed workspace [init-test]','default_branch':'main'}
        gh=mock.Mock(org='CV-Pilot');gh.get_repo.side_effect=lambda name:dict(self.info)
        self.gh=gh
        self.patch(worker,'GitHub',lambda:gh)
        self.patch(reset_repo,'verified_origin',lambda w,uid,repo,name:str(self.remote))
        self.original=self.git('-C',str(self.remote),'rev-parse','main')
        self.git('-C',str(self.remote),'update-ref','refs/heads/user-work',self.original)
        self.git('-C',str(self.remote),'update-ref','refs/heads/cvpilot/tailor/old',self.original)
        self.git('-C',str(self.remote),'tag','keep-tag',self.original)
        key=self.state/'keys'/UID/'id_ed25519';key.parent.mkdir(parents=True);key.write_text('PRESERVE PRIVATE KEY')
        self.key=key
        foreign=self.state/'user-workspaces'/OTHER/'tasks/77';foreign.mkdir(parents=True)
        (foreign/'private.txt').write_text('OTHER USER DATA')
        self.foreign=foreign

    def patch(self,target,name,value):
        patch=mock.patch.object(target,name,value);patch.start();self.addCleanup(patch.stop)

    def heads(self):
        return self.git('-C',str(self.remote),'for-each-ref','--format=%(refname):%(objectname)','refs/heads')

    def claim_reset(self):
        result=self.fixture.call('reset',{'operation':'prepare'})
        self.fixture.call('reset',{'operation':'confirm','token':result['confirmation_token'],'confirmation':'RESET'})
        return self.store.claim()

    def finish_reset(self,job):
        result=worker.work(job,self.store);self.store.finish(job['id'],'done',result);return result

    def test_confirmed_reset_restores_template_deletes_all_other_branches_and_preserves_account(self):
        uploaded,_=self.fixture.upload('teaching')
        original_task,_=worker.new_worktree(UID,901,'profile-sync')
        staged=worker.stage_agent_task(UID,901,original_task)
        user=self.store.user(UID)
        job=self.claim_reset();result=self.finish_reset(job)
        self.assertTrue(result['reset'])
        self.assertEqual(self.git('-C',str(self.remote),'for-each-ref','--format=%(refname)','refs/heads'),'refs/heads/main')
        self.assertEqual(worker.run_git(UID,worker.repopath(UID),'for-each-ref','--format=%(refname)','refs/heads'),'refs/heads/main')
        actual=set(self.git('-C',str(self.remote),'ls-tree','-r','--name-only','main').splitlines())
        template={p.relative_to(APP/'repo-template').as_posix() for p in (APP/'repo-template').rglob('*') if p.is_file()}
        self.assertEqual(actual,template)
        for rel in template:self.assertEqual((worker.repopath(UID)/rel).read_bytes(),(APP/'repo-template'/rel).read_bytes())
        self.git('-C',str(self.remote),'merge-base','--is-ancestor',self.original,'main')
        self.assertEqual(self.git('-C',str(self.remote),'tag'),'keep-tag')
        self.assertEqual(self.store.user(UID),user)
        self.assertEqual(self.key.read_text(),'PRESERVE PRIVATE KEY')
        self.assertEqual((self.foreign/'private.txt').read_text(),'OTHER USER DATA')
        self.assertEqual(self.store.latest_uploads(UID),{})
        self.assertIsNone(self.store.latest_download(UID))
        self.assertEqual(self.store.job(uploaded['id'])['state'],'done')
        backups=self.state/'reset-backups'/UID/str(job['id'])
        self.assertTrue((backups/'sandbox-tasks/901/README.md').is_file())
        self.assertFalse(staged.exists())
        self.assertTrue((backups/'checkout/.git').is_dir())
        journal=json.loads((backups/'reset.json').read_text())
        self.assertTrue(journal['complete'])
        self.assertEqual(Path(journal['bundle']).stat().st_mode&0o777,0o600)
        self.assertTrue((backups/'resume-inbox').is_dir())

    def test_public_unowned_wrong_owner_or_wrong_default_branch_refuses_without_remote_changes(self):
        before=self.heads()
        job=self.claim_reset()
        for fields in ({'private':False},{'owner':{'login':'other'}},{'description':'unrelated'},
                       {'default_branch':'user-work'},{'id':None}):
            original=dict(self.info);self.info.update(fields)
            with self.subTest(fields=fields),self.assertRaises(ValueError):worker.work(job,self.store)
            self.info=original
            self.assertEqual(self.heads(),before)

    def test_unconfirmed_or_wrong_account_payload_refuses_before_github(self):
        for payload in ({'repo':'CV-Pilot/cvpilot-'+UID,'confirmed':False},
                        {'repo':'CV-Pilot/cvpilot-'+OTHER,'confirmed':True}):
            with self.assertRaises(ValueError):
                reset_repo.reset_repository({'discord_id':UID,'id':1,'payload':json.dumps(payload)},self.store)
        self.gh.get_repo.assert_not_called()

    def test_protected_deletions_fail_atomically_without_updating_main(self):
        self.git('-C',str(self.remote),'config','receive.denyDeletes','true')
        before=self.heads();job=self.claim_reset()
        with self.assertRaises(RuntimeError):worker.work(job,self.store)
        self.assertEqual(self.heads(),before)
        self.assertEqual(self.git('-C',str(self.remote),'rev-parse','main'),self.original)
        self.assertFalse((self.state/'reset-backups'/UID/str(job['id'])/'checkout').exists())

    def test_remote_without_atomic_support_is_not_reset_partially(self):
        self.git('-C',str(self.remote),'config','receive.advertiseAtomic','false')
        before=self.heads();job=self.claim_reset()
        with self.assertRaises(RuntimeError):worker.work(job,self.store)
        self.assertEqual(self.heads(),before)

    def test_concurrent_branch_update_is_protected_by_explicit_leases(self):
        job=self.claim_reset();run=worker.run_git;changed=[]
        def racing(uid,repo,*args,**kwargs):
            if args[:2]==('push','--atomic') and not changed:
                self.git('-C',str(self.fixture.seed),'commit','--allow-empty','-m','concurrent branch update')
                oid=self.git('-C',str(self.fixture.seed),'rev-parse','HEAD');changed.append(oid)
                self.git('-C',str(self.fixture.seed),'push','origin','HEAD:refs/heads/user-work')
            return run(uid,repo,*args,**kwargs)
        with mock.patch.object(worker,'run_git',side_effect=racing):
            with self.assertRaises(RuntimeError):worker.work(job,self.store)
        self.assertEqual(self.git('-C',str(self.remote),'rev-parse','user-work'),changed[0])
        self.assertEqual(self.git('-C',str(self.remote),'rev-parse','main'),self.original)

    def test_interruption_after_remote_push_resumes_without_another_push(self):
        job=self.claim_reset();run=worker.run_git;pushes=[]
        def interrupted(uid,repo,*args,**kwargs):
            result=run(uid,repo,*args,**kwargs)
            if args[:2]==('push','--atomic'):
                pushes.append(args);raise RuntimeError('connection lost after completed atomic push')
            return result
        with mock.patch.object(worker,'run_git',side_effect=interrupted):
            with self.assertRaisesRegex(RuntimeError,'connection lost'):worker.work(job,self.store)
        new_main=self.git('-C',str(self.remote),'rev-parse','main')
        self.store.recover();recovered=self.store.claim();self.assertEqual(recovered['id'],job['id'])
        result=self.finish_reset(recovered)
        self.assertEqual(result['commit'],new_main);self.assertEqual(len(pushes),1)

    def test_interruption_between_clone_swap_steps_recovers_without_data_loss(self):
        job=self.claim_reset();rename=Path.rename
        def interrupted(path,target):
            if path.name==f'.reset-{job["id"]}':raise OSError('simulated interrupted clone activation')
            return rename(path,target)
        with mock.patch.object(Path,'rename',interrupted):
            with self.assertRaisesRegex(OSError,'clone activation'):worker.work(job,self.store)
        self.assertFalse(worker.repopath(UID).exists())
        self.store.recover();self.finish_reset(self.store.claim())
        self.assertTrue((worker.repopath(UID)/'.git').is_dir())
        self.assertEqual(self.key.read_text(),'PRESERVE PRIVATE KEY')

    def test_completed_reset_retry_is_idempotent_before_job_finish(self):
        job=self.claim_reset();first=worker.work(job,self.store)
        self.store.recover();second=worker.work(self.store.claim(),self.store)
        self.assertEqual(first['commit'],second['commit'])
        self.store.finish(job['id'],'done',second)

    def test_new_unseen_branch_is_not_silently_deleted_and_requires_fresh_confirmation(self):
        job=self.claim_reset();run=worker.run_git;created=[]
        def racing(uid,repo,*args,**kwargs):
            if args[:2]==('push','--atomic') and not created:
                self.git('-C',str(self.remote),'update-ref','refs/heads/new-work',self.original)
                created.append(True)
            return run(uid,repo,*args,**kwargs)
        with mock.patch.object(worker,'run_git',side_effect=racing):
            with self.assertRaisesRegex(ValueError,'branch changed'):worker.work(job,self.store)
        self.assertEqual(self.git('-C',str(self.remote),'rev-parse','new-work'),self.original)
        self.store.finish(job['id'],'failed',error='Concurrent new branch detected')
        self.assertTrue(self.store.reset_blocked(UID))
        self.finish_reset(self.claim_reset())
        self.assertFalse(self.store.reset_blocked(UID))
        self.assertEqual(self.git('-C',str(self.remote),'for-each-ref','--format=%(refname)','refs/heads'),'refs/heads/main')

    def test_origin_validation_refuses_other_repositories_and_multiple_push_destinations(self):
        full='CV-Pilot/cvpilot-'+UID
        canonical=f'git@github.com:{full}.git'
        for fetch,push in [(canonical,'git@github.com:someone/other.git'),
                           ('git@github.com:someone/other.git','git@github.com:someone/other.git'),
                           (canonical+'\n'+canonical,canonical)]:
            fake=mock.Mock()
            fake.run_git.side_effect=[fetch,push]
            with self.assertRaisesRegex(ValueError,'origin does not match'):
                # Call the original helper; integration tests otherwise patch
                # this check to deliberately use an isolated local bare remote.
                VERIFIED_ORIGIN(fake,UID,Path('/not-used'),full)

    def test_old_repository_identity_cannot_be_replaced_during_recovery(self):
        job=self.claim_reset();run=worker.run_git
        def interrupted(uid,repo,*args,**kwargs):
            result=run(uid,repo,*args,**kwargs)
            if args[:2]==('push','--atomic'):raise RuntimeError('interrupted')
            return result
        with mock.patch.object(worker,'run_git',side_effect=interrupted):
            with self.assertRaises(RuntimeError):worker.work(job,self.store)
        self.info['id']=999
        before=self.heads()
        with self.assertRaisesRegex(ValueError,'does not match'):worker.work(job,self.store)
        self.assertEqual(self.heads(),before)


if __name__=='__main__':unittest.main()
