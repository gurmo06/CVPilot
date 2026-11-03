"""Branding regressions; no real GitHub calls, agents or runtime state."""
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / 'controller'))
from dispatch import process
from store import Store
import github
import worker

UID = '123456789012345678'


class BrandingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.store = Store(self.root / 'jobs.sqlite')
        # Store's existing helpers do not close read connections; close every
        # connection opened by these new tests without changing production scope.
        connect = self.store.connection
        def tracked_connection():
            connection = connect()
            self.addCleanup(connection.close)
            return connection
        patch = mock.patch.object(self.store, 'connection', side_effect=tracked_connection)
        patch.start()
        self.addCleanup(patch.stop)

    def register(self, repo):
        with self.store.tx() as c:
            c.execute('INSERT OR REPLACE INTO users(discord_id,repo,agent) VALUES(?,?,?)',
                      (UID, repo, f'career-{UID}'))

    def test_stored_repository_links_for_both_prefixes_and_custom_names(self):
        for name in (f'career-{UID}', f'cvpilot-{UID}', 'stored-custom-name'):
            repo = f'CV-Pilot/{name}'
            self.register(repo)
            for action, args, suffix in (
                ('repo', {}, ''),
                ('init', {'github_username': 'sampleuser'}, ''),
                ('profile', {'operation': 'show'}, '/blob/main/profile/MASTER_PROFILE.md')):
                with self.subTest(name=name, action=action):
                    result = process({'sender_id': UID, 'action': action, 'args': args}, self.store)
                    self.assertIn(f'https://github.com/{repo}{suffix}', result['text'])

    def test_resume_review_and_unchanged_links_use_stored_name(self):
        for prefix in ('career-', 'cvpilot-', 'custom-'):
            for changed in (False, True):
                with self.subTest(prefix=prefix, changed=changed):
                    repo = f'CV-Pilot/{prefix}{UID}'
                    self.register(repo)
                    task = self.root / 'task'
                    (task / 'profile').mkdir(parents=True, exist_ok=True)
                    (task / 'profile/MASTER_PROFILE.md').write_text('Confirmed facts')
                    trusted = self.root / 'trusted'
                    def fake_git(uid, directory, *args, **kwargs):
                        return 'profile/MASTER_PROFILE.md' if changed and args == ('diff', '--cached', '--name-only') else ''
                    with (mock.patch.object(worker, 'new_worktree', return_value=(task, 'cvpilot/profile-sync/1')),
                          mock.patch.object(worker, 'gitworktree', return_value=trusted),
                          mock.patch.object(worker, 'run', return_value='') as agent,
                          mock.patch.object(worker, 'run_git', side_effect=fake_git) as git):
                        result = worker.run_resume_job({'discord_id': UID, 'id': 1,
                            'kind': 'profile-sync', 'payload': '{}'}, self.store)
                    suffix = '/tree/cvpilot/profile-sync/1' if changed else ''
                    self.assertEqual(result['url'], f'https://github.com/{repo}{suffix}')
                    self.assertEqual(agent.call_args.args[0][3], f'career-{UID}')
                    pushed = [call for call in git.call_args_list if 'push' in call.args]
                    self.assertEqual(len(pushed), int(changed))

    def test_command_messages_use_registered_native_options(self):
        result = process({'sender_id': UID, 'action': 'repo'}, self.store)
        self.assertIn('/cvpilot init github_username:', result['text'])
        self.assertNotIn('/career', result['text'])
        self.register(f'CV-Pilot/career-{UID}')
        with self.assertRaisesRegex(ValueError, '/cvpilot settings master_edit value:on'):
            process({'sender_id': UID, 'action': 'master', 'args': {'operation': 'edit', 'role': 'swe'}}, self.store)

    def test_github_private_creation_default_org_and_ssh_compatibility(self):
        with mock.patch.dict(github.os.environ, {'CVPILOT_GITHUB_TOKEN': 'test-token'}, clear=True):
            gh = github.GitHub()
        self.assertEqual(gh.org, 'CV-Pilot')
        with mock.patch.object(gh, 'api', return_value={'private': True}) as api:
            gh.create_repo('cvpilot-' + UID, 'init-test')
        self.assertIs(api.call_args.args[2]['private'], True)
        self.assertEqual(api.call_args.args[1], '/orgs/CV-Pilot/repos')
        ssh = github.ssh_env('/test/key', '/test/known_hosts')['GIT_SSH_COMMAND']
        self.assertIn('ssh -F /dev/null', ssh)
        self.assertIn('StrictHostKeyChecking=yes', ssh)

    def test_discord_help_only_advertises_registered_commands(self):
        spec = importlib.util.spec_from_file_location('branding_discord', APP.parent / 'discord/cvpilot_discord.py')
        listener = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(listener)
        interaction = mock.Mock()
        interaction.response.send_message = mock.AsyncMock()
        with mock.patch.object(listener, 'public_authorized', new=mock.AsyncMock(return_value=True)):
            asyncio.run(listener.help_command.callback(interaction))
        text = interaction.response.send_message.call_args.args[0]
        import re
        names = {command.qualified_name for command in listener.cvpilot.walk_commands()}
        for command in re.findall(r'`/(cvpilot[^`]*)`', text):
            # Option values follow the registered command's qualified name.
            self.assertTrue(any(command == name or command.startswith(name + ' ') for name in names), command)
        self.assertNotIn('/career', text)
        self.assertEqual(len([cmd for cmd in listener.cvpilot.walk_commands()
                              if not hasattr(cmd, 'commands')]), 13)


if __name__ == '__main__':
    unittest.main()
