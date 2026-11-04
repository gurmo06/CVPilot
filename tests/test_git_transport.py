"""Git authentication fallback preserves repository/key authorization and secrets."""
import base64
import os
from pathlib import Path
import sys
import unittest
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'controller'))
import github
import reset_repo
import worker
import test_resume_attachments as fixtures

UID=fixtures.UID


class HttpsEnvironmentTests(unittest.TestCase):
    def test_credentials_are_scoped_ephemeral_and_tls_checked(self):
        original={'PATH':'/usr/bin','GIT_TRACE':'1','GIT_CURL_VERBOSE':'1','GIT_ASKPASS':'untrusted'}
        result=github.https_git_env('CV-Pilot/cvpilot-test','test-secret',original)
        self.assertNotIn('GIT_TRACE',result);self.assertNotIn('GIT_CURL_VERBOSE',result)
        self.assertNotIn('GIT_ASKPASS',result);self.assertEqual(original['GIT_TRACE'],'1')
        pairs=[(result[f'GIT_CONFIG_KEY_{i}'],result[f'GIT_CONFIG_VALUE_{i}']) for i in range(int(result['GIT_CONFIG_COUNT']))]
        self.assertIn(('http.sslVerify','true'),pairs)
        self.assertIn(('http.followRedirects','false'),pairs)
        self.assertIn(('credential.helper',''),pairs)
        self.assertIn(('core.askPass','/bin/false'),pairs)
        for key,value in pairs:
            if value.startswith('Authorization:'):
                self.assertTrue(key.startswith('http.https://github.com/CV-Pilot/cvpilot-test'))
                self.assertEqual(base64.b64decode(value.split()[-1]).decode(),'x-access-token:test-secret')
            else:self.assertNotIn('test-secret',value)
        self.assertNotIn('test-secret',' '.join(result.keys()))

    def test_invalid_repository_or_credentials_are_rejected(self):
        for repo,token in [('other/../secret','key'),('https://attacker.invalid/repo','key'),('org/repo',''),('org/repo','key\nInjected')]:
            with self.subTest(repo=repo),self.assertRaises(ValueError):github.https_git_env(repo,token,{})

    def test_existing_process_configuration_is_preserved_before_security_overrides(self):
        env=github.https_git_env('org/repo','token',{'GIT_CONFIG_COUNT':'1','GIT_CONFIG_KEY_0':'safe.setting','GIT_CONFIG_VALUE_0':'yes'})
        self.assertEqual(env['GIT_CONFIG_KEY_0'],'safe.setting')
        self.assertEqual(env['GIT_CONFIG_KEY_1'],'url.https://github.com/org/repo.git.insteadOf')


class GitFallbackTests(unittest.TestCase):
    def setUp(self):
        worker._HTTPS_USERS.clear();self.addCleanup(worker._HTTPS_USERS.clear)

    def test_ssh_publickey_failure_retries_exact_operation_once_over_https(self):
        with (mock.patch.object(worker,'run',side_effect=[RuntimeError('Permission denied (publickey).'),'refs']) as run,
              mock.patch.object(worker,'_registered_https_env',return_value={'transport':'https'}) as env):
            self.assertEqual(worker.run_git(UID,Path('/repo'),'fetch','origin','main'),'refs')
        self.assertEqual(run.call_count,2);env.assert_called_once_with(UID,Path('/repo'))
        self.assertEqual(run.call_args_list[0].args,run.call_args_list[1].args)
        self.assertEqual(run.call_args.kwargs['env'],{'transport':'https'})
        self.assertIn(UID,worker._HTTPS_USERS)

    def test_verified_fallback_route_is_reused_for_later_network_operations(self):
        worker._HTTPS_USERS.add(UID)
        with (mock.patch.object(worker,'run',return_value='done') as run,
              mock.patch.object(worker,'_registered_https_env',return_value={'transport':'https'}) as env):
            worker.run_git(UID,Path('/repo'),'push','origin','commit:refs/heads/main')
        self.assertEqual(run.call_count,1);env.assert_called_once()
        self.assertFalse(any('force' in value for value in run.call_args.args[0]))

    def test_host_key_errors_and_non_network_commands_never_fall_back(self):
        for args,error in [(('fetch','origin','main'),'Host key verification failed.'),(('commit','-m','test'),'Permission denied (publickey).')]:
            with (self.subTest(args=args),mock.patch.object(worker,'run',side_effect=RuntimeError(error)) as run,
                  mock.patch.object(worker,'_registered_https_env') as env):
                with self.assertRaises(RuntimeError):worker.run_git(UID,Path('/repo'),*args)
                self.assertEqual(run.call_count,1);env.assert_not_called()

    def test_failed_https_auth_is_not_retried_indefinitely_or_cached(self):
        with (mock.patch.object(worker,'run',side_effect=RuntimeError('Permission denied (publickey).')) as run,
              mock.patch.object(worker,'_registered_https_env',return_value={})):
            with self.assertRaises(RuntimeError):worker.run_git(UID,Path('/repo'),'fetch','origin','main')
        self.assertEqual(run.call_count,2);self.assertNotIn(UID,worker._HTTPS_USERS)


class FallbackAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.ResumeWorkflowTests('runTest');self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        worker._HTTPS_USERS.clear();self.addCleanup(worker._HTTPS_USERS.clear)
        key=worker.keypath(UID);key.parent.mkdir(parents=True);key.write_text('test private placeholder');key.with_suffix('.pub').write_text('ssh-ed25519 TESTKEY')
        self.key=key
        self.info={'id':42,'private':True,'owner':{'login':'CV-Pilot'},'name':'cvpilot-'+UID,
                   'description':'CVPilot managed workspace [test]','default_branch':'main'}
        self.keys=[{'title':'cvpilot-'+UID,'key':'ssh-ed25519 TESTKEY','read_only':False,'enabled':True}]
        gh=mock.Mock(org='CV-Pilot',token='test-token');gh.get_repo.side_effect=lambda name:self.info;gh.api.side_effect=lambda *args:self.keys
        p=mock.patch.object(worker,'GitHub',return_value=gh);p.start();self.addCleanup(p.stop)
        p=mock.patch.object(reset_repo,'verified_origin',return_value='git@github.com:CV-Pilot/cvpilot-'+UID+'.git');p.start();self.addCleanup(p.stop)

    def test_private_owned_repository_with_matching_enabled_writable_key_is_accepted(self):
        env=worker._registered_https_env(UID,worker.repopath(UID))
        self.assertGreater(int(env['GIT_CONFIG_COUNT']),0)

    def test_revoked_readonly_disabled_or_mismatched_keys_are_not_bypassed(self):
        for fields in ({'read_only':True},{'enabled':False},{'key':'ssh-ed25519 DIFFERENT'},{'title':'someone-else'}):
            original=dict(self.keys[0]);self.keys[0].update(fields)
            with self.subTest(fields=fields),self.assertRaises(ValueError):worker._registered_https_env(UID,worker.repopath(UID))
            self.keys[0]=original
        self.keys=[]
        with self.assertRaises(ValueError):worker._registered_https_env(UID,worker.repopath(UID))

    def test_public_or_unowned_repository_and_missing_local_key_are_refused(self):
        for fields in ({'private':False},{'owner':{'login':'other-org'}},{'description':'Unrelated repository'}):
            old=dict(self.info);self.info.update(fields)
            with self.subTest(fields=fields),self.assertRaises(ValueError):worker._registered_https_env(UID,worker.repopath(UID))
            self.info=old
        self.key.unlink()
        with self.assertRaisesRegex(ValueError,'key is missing'):worker._registered_https_env(UID,worker.repopath(UID))

    def test_mismatched_origin_is_refused_before_https_credentials_are_built(self):
        with (mock.patch.object(reset_repo,'verified_origin',side_effect=ValueError('Wrong origin')),
              mock.patch.object(worker,'https_git_env') as credentials):
            with self.assertRaisesRegex(ValueError,'Wrong origin'):worker._registered_https_env(UID,worker.repopath(UID))
        credentials.assert_not_called()


if __name__=='__main__':unittest.main()
