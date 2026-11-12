"""Regression tests for retryable, ownership-checked CVPilot initialization."""
import contextlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'controller'))
try:
    import store
except ImportError:
    sys.modules['store'] = types.SimpleNamespace(Store=object)
import github
import worker
ORIGINAL_PROVISION_AGENT=worker.provision_agent

UID="012345678901234567" # <-- Actual UID here


class FakeStore:
    def user(self, uid):
        return None

    @contextlib.contextmanager
    def tx(self):
        class Cursor:
            def execute(self, *args):
                return None
        yield Cursor()


class FakeGitHub:
    def __init__(self, *, existing=None, deny_create=False):
        self.org='CV-Pilot'
        self.repo=existing
        self.deny_create=deny_create
        self.created=0
        self.keys=[]
        self.invited=0
        self.used_keys=set()

    def get_repo(self, name):
        return self.repo

    def create_repo(self, name, marker):
        self.created+=1
        if self.deny_create:
            raise github.GitHubAPIError(403,'POST',f'/orgs/{self.org}/repos','Forbidden')
        self.repo={
            'id': 42+self.created,
            'name': name,
            'owner': {'login': self.org},
            'private': True,
            'description': f'CVPilot managed workspace [{marker}]',
        }
        return self.repo

    def api(self, method, path, data=None):
        if method=='GET' and path.endswith('/keys'):
            return self.keys
        raise AssertionError((method,path,data))

    def deploy_key(self, name, public_key, uid):
        if tuple(public_key.split()[:2]) in self.used_keys:
            raise github.GitHubAPIError(422, 'POST', f'/repos/{self.org}/{name}/keys', 'key is already in use')
        self.keys.append({'title': f'cvpilot-{uid}', 'key': public_key, 'read_only': False})

    def protect_main(self, name):
        return None

    def invite(self, name, github_user):
        self.invited+=1


class InitRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.state=self.root/'state'
        self.kit=self.root/'app'
        (self.kit/'repo-template').mkdir(parents=True)
        (self.kit/'repo-template'/'README.md').write_text('Test template')
        (self.root/'.ssh').mkdir()
        (self.root/'.ssh'/'known_hosts').write_text('github.com ssh-ed25519 AAAATEST')
        self.gh=FakeGitHub()
        self.store=FakeStore()
        self.fake_git_calls=[]
        self.patch(worker,'ROOT',self.state)
        self.patch(worker,'KIT',self.kit)
        self.patch(worker,'ORG','CV-Pilot')
        self.patch(worker,'GitHub',lambda:self.gh)
        self.patch(worker.Path,'home',lambda:self.root)
        self.patch(worker,'run',self.fake_run)
        self.patch(worker,'run_git',self.fake_run_git)
        self.patch(worker,'provision_agent',lambda uid,home:None)

    def patch(self, target, key, value):
        p=mock.patch.object(target,key,value)
        p.start()
        self.addCleanup(p.stop)

    def fake_run(self, args, **kwargs):
        self.fake_git_calls.append(args)
        if args[0]=='ssh-keygen':
            p=Path(args[args.index('-f')+1])
            p.parent.mkdir(parents=True,exist_ok=True)
            p.write_text('TEST PRIVATE KEY ' + str(p))
            value='AAAANEWKEY' if 'pending' in p.parts else 'AAAATEST'
            p.with_suffix('.pub').write_text(f'ssh-ed25519 {value} CVPilot')
        elif args[:2]==['git','clone']:
            repo=Path(args[-1])
            repo.mkdir(parents=True)
            (repo/'.git').mkdir()
        return ''

    def fake_run_git(self, uid, dir, *args, **kwargs):
        if args==('status','--porcelain'):
            return ''
        return ''

    def job(self, username='sampleuser'):
        return {'discord_id':UID,'payload':json.dumps({'github_username':username})}

    def intent(self, content):
        path=self.state/'intents'/f'{UID}.json'
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(content))
        return path

    def test_new_repo_creates_and_finishes(self):
        result=worker.repo_init(self.job(),self.store)
        self.assertEqual(self.gh.created,1)
        self.assertEqual(self.gh.invited,1)
        self.assertEqual(result['url'],f'https://github.com/CV-Pilot/cvpilot-{UID}')
        self.assertFalse((self.state/'intents'/f'{UID}.json').exists())
        self.assertTrue((worker.repopath(UID)/'master-resumes/general').is_dir())
        self.assertFalse((worker.repopath(UID)/'master-resumes/swe').exists())

    def test_success_message_has_no_gateway_or_protection_warning(self):
        result=worker.repo_init(self.job(),self.store)
        self.assertEqual(result['note'],
            'Private CVPilot workspace is ready for sampleuser. Accept the GitHub invitation if prompted.')
        self.assertNotIn('gateway',result['note'].lower())
        self.assertNotIn('protection',result['note'].lower())
        self.assertEqual(result['main_protection'],'enabled')
        self.assertIsNone(result['main_protection_error'])

    def test_init_configures_a_local_automation_identity_without_borrowing_a_github_account(self):
        with mock.patch.object(worker,'run_git',side_effect=self.fake_run_git) as git:
            worker.repo_init(self.job(),self.store)
        git.assert_any_call(UID,worker.repopath(UID),'config','user.name','CVPilot')
        git.assert_any_call(UID,worker.repopath(UID),'config','user.email','cvpilot@cvpilot.invalid')
        self.assertNotIn('cvpilot@users.noreply.github.com',str(git.call_args_list))

    def test_plan_limitation_is_logged_and_saved_without_warning_in_success_message(self):
        error=github.GitHubAPIError(403,'PUT','/branches/main/protection',
                                  'Upgrade to GitHub Pro or make this repository public to enable this feature.')
        with (mock.patch.object(self.gh,'protect_main',side_effect=error),
              mock.patch('builtins.print') as log):
            result=worker.repo_init(self.job(),self.store)
        self.assertEqual(result['main_protection'],'unavailable_on_plan')
        self.assertEqual(result['main_protection_error'],str(error))
        self.assertTrue(any(str(error) in str(call) for call in log.call_args_list))
        self.assertNotIn('Upgrade',result['note'])
        self.assertNotIn('protection',result['note'])
        self.assertNotIn('gateway',result['note'].lower())
        self.assertEqual(self.gh.invited,1)
        self.assertTrue(self.gh.repo['private'])

    def test_other_protection_errors_keep_operator_diagnostics(self):
        error=github.GitHubAPIError(403,'PUT','/branches/main/protection','Resource not accessible by token')
        with (mock.patch.object(self.gh,'protect_main',side_effect=error),
              mock.patch('builtins.print') as log):
            result=worker.repo_init(self.job(),self.store)
        self.assertEqual(result['main_protection'],'failed')
        self.assertEqual(result['main_protection_error'],str(error))
        self.assertTrue(any(str(error) in str(call) for call in log.call_args_list))
        self.assertNotIn('Resource not accessible',result['note'])

    def test_invitation_failure_is_still_a_real_initialization_failure(self):
        error=github.GitHubAPIError(403,'PUT','/collaborators/sampleuser','Forbidden')
        with mock.patch.object(self.gh,'invite',side_effect=error):
            with self.assertRaises(github.GitHubAPIError):
                worker.repo_init(self.job(),self.store)
        self.assertTrue((self.state/'intents'/f'{UID}.json').exists())

    def test_new_prefix_is_used_for_all_provisioning_operations(self):
        with (mock.patch.object(self.gh, 'get_repo', wraps=self.gh.get_repo) as lookup,
              mock.patch.object(self.gh, 'deploy_key', wraps=self.gh.deploy_key) as deploy,
              mock.patch.object(self.gh, 'invite', wraps=self.gh.invite) as invite,
              mock.patch.object(self.gh, 'protect_main', wraps=self.gh.protect_main) as protect):
            worker.repo_init(self.job(),self.store)
        name=f'cvpilot-{UID}'
        lookup.assert_called_with(name)
        self.assertEqual(deploy.call_args.args[0],name)
        invite.assert_called_with(name,'sampleuser')
        protect.assert_called_with(name)
        clones=[args for args in self.fake_git_calls if args[:2]==['git','clone']]
        self.assertEqual(clones[0][2],f'git@github.com:CV-Pilot/{name}.git')

    def test_new_prefix_marked_intent_recovers(self):
        name=f'cvpilot-{UID}'
        self.intent({'name':name,'github_user':'sampleuser','org':'CV-Pilot',
                     'marker':'init-test','repo_id':33})
        self.gh.repo={'id':33,'name':name,'private':True,
                      'owner':{'login':'CV-Pilot'},
                      'description':'CVPilot managed workspace [init-test]'}
        result=worker.repo_init(self.job(),self.store)
        self.assertEqual(self.gh.created,0)
        self.assertTrue(result['url'].endswith(name))

    def test_intent_for_another_user_or_repository_is_rejected(self):
        for name in ('unrelated', 'career-223456789012345678', 'cvpilot-223456789012345678'):
            self.intent({'name':name,'github_user':'sampleuser','marker':'init-test'})
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError,'different parameters'):
                worker.repo_init(self.job(),self.store)
        self.assertEqual(self.gh.created,0)

    def test_legacy_intent_and_missing_repo_recovers(self):
        self.intent({'name':f'career-{UID}','github_user':'sampleuser'})
        worker.repo_init(self.job(),self.store)
        self.assertEqual(self.gh.created,1)

    def test_legacy_intent_with_existing_repo_requires_review(self):
        self.intent({'name':f'career-{UID}','github_user':'sampleuser'})
        self.gh.repo={'id':2,'name':f'career-{UID}','owner':{'login':'CV-Pilot'},'private':True}
        with self.assertRaisesRegex(RuntimeError,'Legacy init intent'):
            worker.repo_init(self.job(),self.store)
        self.assertEqual(self.gh.created,0)

    def test_no_intent_never_adopts_existing_repo(self):
        self.gh.repo={'id':2,'name':f'career-{UID}','owner':{'login':'CV-Pilot'},'private':True}
        with self.assertRaisesRegex(RuntimeError,'refusing takeover'):
            worker.repo_init(self.job(),self.store)
        self.assertEqual(self.gh.created,0)

    def test_matching_marked_repo_resumes(self):
        marker='init-test'
        self.intent({'name':f'career-{UID}','github_user':'sampleuser',
                     'org':'CV-Pilot','marker':marker,'repo_id':33})
        self.gh.repo={'id':33,'name':f'career-{UID}', 'private':True,
                      'owner':{'login':'CV-Pilot'},
                      'description':f'CVPilot managed workspace [{marker}]'}
        worker.repo_init(self.job(),self.store)
        self.assertEqual(self.gh.created,0)

    def test_wrong_marker_refuses_existing_repo(self):
        self.intent({'name':f'career-{UID}','github_user':'sampleuser',
                     'marker':'init-ours'})
        self.gh.repo={'id':3,'name':f'career-{UID}', 'private':True,
                      'owner':{'login':'CV-Pilot'},
                      'description':'Somebody else created this'}
        with self.assertRaisesRegex(RuntimeError,'cannot verify CVPilot ownership'):
            worker.repo_init(self.job(),self.store)

    def test_missing_repo_with_old_id_archives_local_clone(self):
        self.intent({'name':f'career-{UID}','github_user':'sampleuser',
                     'marker':'init-ours','repo_id':11})
        local=self.state/'git-repos'/UID/'repo'
        (local/'.git').mkdir(parents=True)
        (local/'my-notes.txt').write_text('PRESERVE THIS')
        worker.repo_init(self.job(),self.store)
        copies=list(local.parent.glob('repo.recovery-*'))
        self.assertEqual(len(copies),1)
        self.assertEqual((copies[0]/'my-notes.txt').read_text(),'PRESERVE THIS')
        self.assertTrue((local/'.git').exists())
        self.assertEqual(self.gh.created,1)

    def test_different_username_rejected(self):
        self.intent({'name':f'career-{UID}','github_user':'anotheruser'})
        with self.assertRaisesRegex(RuntimeError,'different parameters'):
            worker.repo_init(self.job(),self.store)

    def test_changed_repo_id_is_rejected(self):
        marker='init-test'
        self.intent({'name':f'career-{UID}','github_user':'sampleuser',
                     'marker':marker,'repo_id':33})
        self.gh.repo={'id':44,'name':f'career-{UID}','private':True,
                      'owner':{'login':'CV-Pilot'},
                      'description':f'CVPilot managed workspace [{marker}]'}
        with self.assertRaisesRegex(RuntimeError,'identity changed unexpectedly'):
            worker.repo_init(self.job(),self.store)

    def test_provision_agent_reads_templates_skills(self):
        template=self.kit/'templates'
        template.mkdir(parents=True)
        (template/'AGENTS.md').write_text('Agent instructions')
        for name in ('career-resume','career-source-sync'):
            skill=template/'skills'/name
            skill.mkdir(parents=True)
            (skill/'SKILL.md').write_text(name)
        commands=[]
        def fake_agent_run(args,**kwargs):
            commands.append(args)
            if args[:3]==['openclaw','agents','list']:
                return '[]'
            if args[:4]==['openclaw','config','get','bindings']:
                return '[]'
            return ''
        with (mock.patch.object(worker,'run',side_effect=fake_agent_run),
              mock.patch.object(worker,'provision_agent',ORIGINAL_PROVISION_AGENT)):
            worker.provision_agent(UID,self.state/'user-workspaces'/UID)
        target=self.root/'.openclaw'/f'workspace-career-{UID}'/'skills'
        self.assertEqual((target/'career-resume'/'SKILL.md').read_text(),'career-resume')
        self.assertEqual((target/'career-source-sync'/'SKILL.md').read_text(),'career-source-sync')
        binds=[call for call in commands if '.sandbox.docker.binds' in ' '.join(map(str,call))]
        self.assertEqual(json.loads(binds[0][4]),[f'{self.root}/.openclaw/workspace-career-{UID}/cvpilot-jobs:/career:rw'])
        self.assertIn(['openclaw','config','set',f'agents.entries.career-{UID}.tools.exec.host','sandbox'],commands)
        self.assertFalse(any('dangerouslyAllow' in ' '.join(call) for call in commands))

    def test_create_permission_failure_keeps_intent(self):
        self.gh.deny_create=True
        with self.assertRaises(github.GitHubAPIError):
            worker.repo_init(self.job(),self.store)
        data=json.loads((self.state/'intents'/f'{UID}.json').read_text())
        self.assertTrue(data['marker'].startswith('init-'))

    def test_deploy_key_collision_is_not_silently_accepted(self):
        self.gh.keys=[{'title':f'cvpilot-{UID}', 'key':'ssh-ed25519 WRONG',
                       'read_only':False}]
        with self.assertRaisesRegex(RuntimeError,'Deploy key mismatch'):
            worker.repo_init(self.job(),self.store)

    def test_existing_key_already_in_use_rotates_and_preserves_old(self):
        self.gh.used_keys.add(('ssh-ed25519', 'AAAATEST'))
        result=worker.repo_init(self.job(),self.store)
        self.assertIn('cvpilot-',result['url'])
        self.assertEqual(len(self.gh.keys),1)
        self.assertEqual(self.gh.keys[0]['key'].split()[1], 'AAAANEWKEY')
        active=worker.keypath(UID)
        self.assertEqual(active.with_suffix('.pub').read_text().split()[1], 'AAAANEWKEY')
        archives=list((active.parent/'previous-keys').glob('id_ed25519-*'))
        self.assertEqual(len([p for p in archives if p.suffix != '.pub']),1)
        self.assertTrue(all(p.stat().st_mode & 0o077 == 0 for p in archives if p.suffix != '.pub'))
        self.assertFalse((active.parent/'pending').exists())

    def test_retry_recovers_after_key_registered_but_not_activated(self):
        marker='init-test'
        self.intent({'name':f'career-{UID}','github_user':'sampleuser',
                     'org':'CV-Pilot','marker':marker,'repo_id':33})
        self.gh.repo={'id':33,'name':f'career-{UID}','private':True,
                      'owner':{'login':'CV-Pilot'},
                      'description':f'CVPilot managed workspace [{marker}]'}
        active=worker.keypath(UID)
        active.parent.mkdir(parents=True)
        active.write_text('ORIGINAL PRIVATE')
        active.with_suffix('.pub').write_text('ssh-ed25519 AAAATEST')
        pending=worker._pending_key_path(active)
        pending.parent.mkdir(parents=True)
        pending.write_text('NEW PRIVATE')
        pending.with_suffix('.pub').write_text('ssh-ed25519 AAAANEWKEY')
        self.gh.keys=[{'title':f'cvpilot-{UID}',
                       'key':'ssh-ed25519 AAAANEWKEY', 'read_only':False}]
        worker.repo_init(self.job(),self.store)
        self.assertEqual(active.read_text(),'NEW PRIVATE')
        self.assertFalse(pending.exists())

    def test_collision_only_rotates_for_specific_422(self):
        class UnrelatedFailure(Exception):
            pass
        def deny(*args):
            raise github.GitHubAPIError(422,'POST','/keys','deploy keys disabled')
        self.gh.deploy_key=deny
        with self.assertRaisesRegex(github.GitHubAPIError,'deploy keys disabled'):
            worker.repo_init(self.job(),self.store)
        active=worker.keypath(UID)
        self.assertEqual(active.with_suffix('.pub').read_text().split()[1],'AAAATEST')
        self.assertFalse(worker._pending_key_path(active).exists())

    def test_existing_remote_mismatch_does_not_rotate(self):
        self.gh.keys=[{'title':f'cvpilot-{UID}', 'key':'ssh-ed25519 WRONG',
                       'read_only':False}]
        with self.assertRaisesRegex(RuntimeError,'Deploy key mismatch'):
            worker.repo_init(self.job(),self.store)
        self.assertFalse(worker._pending_key_path(worker.keypath(UID)).exists())

    def test_public_repo_cannot_be_adopted(self):
        marker='init-test'
        self.intent({'name':f'career-{UID}','github_user':'sampleuser','marker':marker})
        self.gh.repo={'id':3,'name':f'career-{UID}','private':False,
                      'owner':{'login':'CV-Pilot'},
                      'description':f'CVPilot managed workspace [{marker}]'}
        with self.assertRaisesRegex(RuntimeError,'non-private'):
            worker.repo_init(self.job(),self.store)


class GitHubErrorTests(unittest.TestCase):
    def test_get_repo_only_converts_404(self):
        gh=object.__new__(github.GitHub)
        gh.org='CV-Pilot'
        for status in (403, 422):
            with mock.patch.object(gh,'api',side_effect=github.GitHubAPIError(status,'GET','/repos/foo','error')):
                with self.assertRaises(github.GitHubAPIError):
                    gh.get_repo('career-123')
        with mock.patch.object(gh,'api',side_effect=github.GitHubAPIError(404,'GET','/repos/foo','Not Found')):
            self.assertIsNone(gh.get_repo('career-123'))


if __name__=='__main__':
    unittest.main()
