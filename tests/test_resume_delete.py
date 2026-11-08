"""Confirmed resume deletion using only temporary SQLite and local Git remotes."""
import json
from unittest import mock
import unittest

import test_main_commits as fixtures
import test_resume_attachments as attachments
import main_commits
import resume_delete
import worker

UID = fixtures.UID
OTHER = fixtures.OTHER


class ResumeDeleteTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.MainCommitTests('runTest'); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.store = self.fixture.store; self.git = self.fixture.git
        self.call = self.fixture.call; self.main = self.fixture.main
        resume_delete.CONFIRMATIONS.clear()

    def upload(self, label='general', files=None):
        job, result = self.fixture.fixture.upload(label, files)
        worker.cleanup_completed(UID, job['id'])
        return job, result

    def app(self, url='https://example.invalid/jobs/teaching',suffix=''):
        jid = self.store.enqueue(UID, 'tailor', {'url': url})
        path = f'applications/2026/job-{jid}'+suffix
        seed = self.fixture.fixture.seed
        self.git('-C', str(seed), 'pull', '--ff-only', 'origin', 'main')
        folder = seed / path; folder.mkdir(parents=True)
        for name, data in [('resume.pdf', attachments.PDF), ('resume.tex', attachments.TEX),
                           ('job-url.txt', url.encode()), ('notes.md', b'Verified experience')]:
            (folder / name).write_bytes(data)
        self.git('-C', str(seed), 'add', '-f', '--', path)
        self.git('-C', str(seed), 'commit', '-m', f'CVPilot tailor job {jid}')
        self.git('-C', str(seed), 'push', 'origin', 'main')
        result = {'commit': self.main(), 'url': f'https://github.com/CV-Pilot/cvpilot-{UID}/commit/{self.main()}',
                  'artifacts': [{'path': path + '/' + name} for name in ('resume.pdf', 'resume.tex')]}
        self.store.finish(jid, 'done', result)
        return jid, path, result

    def confirmed(self, kind, **selector):
        preview = self.call(kind, selector)
        self.assertIsNone(self.store.claim())
        self.call(kind, {'operation': 'confirm', 'token': preview['confirmation_token'], 'confirmation': 'DELETE'})
        return self.store.claim()

    def finish(self, job):
        result = worker.work(job, self.store)
        self.store.finish(job['id'], 'done', result)
        worker.cleanup_completed(UID, job['id'])
        return result

    def tree(self, path):
        return self.git('-C', str(self.fixture.remote), 'ls-tree', '-r', 'main', '--', path)

    def test_label_deletes_pair_and_companions_only_with_one_main_commit_and_no_agent(self):
        job, original = self.upload('teaching'); self.upload('healthcare')
        self.fixture.external_edit('master-resumes/teaching/layout.cls', 'Companion')
        self.fixture.external_edit('profile/MASTER_PROFILE.md', 'Keep verified facts')
        before = self.main(); user = self.store.user(UID)
        deletion = self.confirmed('master-delete', label='teaching')
        with mock.patch.object(worker, 'run_resume_job', side_effect=AssertionError('No AI for deletion')):
            result = self.finish(deletion)
        self.assertTrue(result['changed']); self.assertEqual(self.main(), result['commit'])
        self.assertEqual(self.tree('master-resumes/teaching'), '')
        self.assertIn('healthcare/resume.pdf', self.tree('master-resumes/healthcare'))
        self.assertIn('MASTER_PROFILE.md', self.tree('profile'))
        self.assertEqual(self.store.user(UID), user)
        self.assertEqual(self.store.job(job['id'])['state'], 'done')
        self.assertEqual(self.git('-C', str(self.fixture.remote), 'rev-list', '--count', before + '..main'), '1')
        changed = self.git('-C', str(self.fixture.remote), 'diff', '--name-only', before, 'main').splitlines()
        self.assertEqual(set(changed), {'master-resumes/teaching/' + name for name in ('resume.pdf', 'resume.tex', 'layout.cls')})
        self.assertEqual(self.git('-C', str(self.fixture.remote), 'for-each-ref', '--format=%(refname)', 'refs/heads'), 'refs/heads/main')
        self.assertIn('Existing profile facts', result['note'])
        with self.assertRaisesRegex(ValueError, 'deleted'):
            self.call('download', {'job_id': job['id']})
        self.assertNotIn('attachments', self.call('master', {'operation': 'get', 'label': 'teaching'}))

    def test_master_commit_selector_and_normal_revert_restore_files_and_downloads(self):
        job, source = self.upload()
        result = self.finish(self.confirmed('master-delete', commit=source['commit'][:12].upper()))
        self.assertNotIn('general', self.store.latest_uploads(UID))
        self.fixture.finish(self.fixture.revert_job(result['commit']))
        self.assertEqual(len(self.call('master', {'operation': 'get'})['attachments']), 2)
        self.assertEqual(len(self.call('download', {'job_id': job['id']})['attachments']), 2)

    def test_reupload_after_deletion_uses_new_source(self):
        self.upload(); self.finish(self.confirmed('master-delete', label='general'))
        self.upload(files=attachments.attachments(('new.pdf', attachments.PDF + b'new')))
        actual = attachments.resumes.validate_files(self.call('master', {'operation': 'get'})['attachments'])
        self.assertEqual(actual, {'resume.pdf': attachments.PDF + b'new'})

    def test_application_github_commit_link_removes_entire_folder_and_blocks_old_download(self):
        master, _ = self.upload(); jid, path, app = self.app()
        result = self.finish(self.confirmed('application-delete', link=app['url']))
        self.assertEqual(self.tree(path), '')
        self.assertTrue(self.tree('master-resumes/general'))
        with self.assertRaisesRegex(ValueError, 'deleted'): self.call('download', {'job_id': jid})
        self.assertIn(f'job #{master["id"]}', self.call('download')['text'])
        self.fixture.finish(self.fixture.revert_job(result['commit']))
        self.assertEqual(len(self.call('download', {'job_id': jid})['attachments']), 2)

    def test_application_commit_folder_and_file_links_select_only_that_application(self):
        jid, path, app = self.app(); self.app('https://example.invalid/jobs/nurse')
        base = f'https://github.com/CV-Pilot/cvpilot-{UID}'
        for selector in ({'commit': app['commit'][:7]}, {'link': base + '/tree/main/' + path},
                         {'link': base + '/blob/' + app['commit'] + '/' + path + '/resume.pdf'}):
            with self.subTest(selector=selector):
                preview = self.call('application-delete', selector)
                self.assertIn(path, preview['text'])
                self.call('application-delete', {'operation': 'cancel', 'token': preview['confirmation_token']})

    def test_descriptive_folder_download_link_delete_and_revert_remain_compatible(self):
        jid,path,app=self.app(suffix='-example-school-classroom-teacher')
        # This fixture writes through a separate seed clone, unlike the real
        # worker's publication worktree, which shares the local object store.
        worker.run_git(UID,worker.repopath(UID),'fetch','origin','main')
        self.assertEqual(len(self.call('download',{'job_id':jid})['attachments']),2)
        link=f'https://github.com/CV-Pilot/cvpilot-{UID}/tree/main/'+path
        preview=self.call('application-delete',{'link':link})
        self.assertIn(path,preview['text'])
        self.call('application-delete',{'operation':'cancel','token':preview['confirmation_token']})
        result=self.finish(self.confirmed('application-delete',commit=app['commit'][:12]))
        self.assertEqual(self.tree(path),'')
        with self.assertRaisesRegex(ValueError,'deleted'):self.call('download',{'job_id':jid})
        self.fixture.finish(self.fixture.revert_job(result['commit']))
        self.assertEqual(len(self.call('download',{'job_id':jid})['attachments']),2)

    def test_original_posting_link_requires_one_owned_application(self):
        url = 'https://example.invalid/jobs/social-work'
        jid, path, _ = self.app(url)
        self.assertIn(path, self.call('application-delete', {'link': url})['text'])
        self.app(url)
        with self.assertRaisesRegex(ValueError, 'one application'):
            self.call('application-delete', {'link': url})
        with self.assertRaises(ValueError): self.call('application-delete', {'link': url}, uid=OTHER)

    def test_posting_link_ignores_deleted_jobs_and_copy_paste_whitespace(self):
        url='https://example.invalid/jobs/social-work'
        old_id,old_path,old=self.app(url)
        self.finish(self.confirmed('application-delete',commit=old['commit'][:12]))
        jid,path,app=self.app(url,suffix='-example-community-social-worker')
        preview=self.call('application-delete',{'link':' \n'+url+'\n'})
        self.assertIn(path,preview['text']);self.assertNotIn(old_path+'?',preview['text'])
        self.assertEqual(self.store.job(old_id)['state'],'done')
        self.assertIsNone(self.store.claim())

    def test_posting_link_with_only_deleted_matches_reports_no_current_application(self):
        url='https://example.invalid/jobs/social-work';jid,path,app=self.app(url)
        self.finish(self.confirmed('application-delete',commit=app['commit'][:12]))
        with self.assertRaisesRegex(ValueError,'No current application matches'):
            self.call('application-delete',{'link':url})

    def test_real_posting_ambiguity_lists_only_current_owned_folder_links(self):
        url='https://example.invalid/jobs/social-work'
        _,removed_path,removed=self.app(url)
        deletion=self.finish(self.confirmed('application-delete',commit=removed['commit'][:12]))
        _,first,_=self.app(url);_,second,_=self.app(url,suffix='-example-social-worker')
        with self.assertRaises(ValueError) as caught:self.call('application-delete',{'link':url})
        message=str(caught.exception)
        self.assertIn('matches 2 current applications',message)
        for path in (first,second):self.assertIn(f'https://github.com/CV-Pilot/cvpilot-{UID}/tree/main/'+path,message)
        self.assertNotIn(removed_path+'\n',message)
        self.fixture.finish(self.fixture.revert_job(deletion['commit']))
        with self.assertRaisesRegex(ValueError,'matches 3 current applications'):
            self.call('application-delete',{'link':url})

    def test_posting_link_ignores_folders_removed_directly_on_main(self):
        url='https://example.invalid/jobs/social-work';_,path,app=self.app(url)
        seed=self.fixture.fixture.seed
        self.git('-C',str(seed),'rm','-r','--',path)
        self.git('-C',str(seed),'commit','-m','Remove application outside CVPilot')
        self.git('-C',str(seed),'push','origin','main')
        _,current,_=self.app(url)
        self.assertIn(current,self.call('application-delete',{'link':url})['text'])

    def test_exactly_one_valid_selector_required_and_no_side_effects(self):
        self.upload(); head = self.main()
        for kind, values in [('master-delete', [{}, {'label': 'general', 'commit': head}, {'label': '../general'}, {'link': 'https://example.invalid'}]),
                             ('application-delete', [{}, {'link': 'x', 'commit': head}, {'label': 'general'}, {'commit': '--all'}])]:
            for args in values:
                with self.subTest(kind=kind, args=args), self.assertRaises(ValueError): self.call(kind, args)
        self.assertIsNone(self.store.claim()); self.assertEqual(self.main(), head)

    def test_missing_or_already_deleted_source_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'not present'): self.call('master-delete', {'label': 'general'})
        self.upload(); self.finish(self.confirmed('master-delete', label='general'))
        with self.assertRaisesRegex(ValueError, 'already deleted'): self.call('master-delete', {'label': 'general'})
        with self.assertRaisesRegex(ValueError, 'No downloadable'): self.call('download')

    def test_stale_commit_does_not_delete_newer_source_or_application(self):
        _, old = self.upload(); self.upload(files=attachments.attachments(('new.pdf', attachments.PDF + b'new')))
        with self.assertRaisesRegex(ValueError, 'changed after'): self.call('master-delete', {'commit': old['commit']})
        _, path, app = self.app(); self.fixture.external_edit(path + '/notes.md', 'Newer note')
        with self.assertRaisesRegex(ValueError, 'changed after'): self.call('application-delete', {'link': app['url']})

    def test_ambiguous_commit_and_unrelated_or_off_main_commit_are_refused(self):
        self.upload('general'); self.upload('nursing')
        seed = self.fixture.fixture.seed
        self.git('-C', str(seed), 'pull', '--ff-only', 'origin', 'main')
        for name in ('general', 'nursing'): (seed / 'master-resumes' / name / 'resume.tex').write_text('Changed')
        self.git('-C', str(seed), 'add', '-A'); self.git('-C', str(seed), 'commit', '-m', 'Two resumes')
        self.git('-C', str(seed), 'push', 'origin', 'main')
        with self.assertRaisesRegex(ValueError, 'exactly one'): self.call('master-delete', {'commit': self.main()})
        with self.assertRaisesRegex(ValueError, 'exactly one'): self.call('application-delete', {'commit': self.main()})
        with self.assertRaises(ValueError): self.call('master-delete', {'commit': 'f' * 40})
        self.git('-C', str(seed), 'commit', '--allow-empty', '-m', 'Off main')
        oid = self.git('-C', str(seed), 'rev-parse', 'HEAD')
        self.git('-C', str(seed), 'push', 'origin', 'HEAD:refs/heads/side')
        worker.run_git(UID, worker.repopath(UID), 'fetch', 'origin', 'side')
        with self.assertRaisesRegex(ValueError, 'main history'): self.call('master-delete', {'commit': oid})

    def test_cross_repository_unsafe_and_non_application_links_are_refused(self):
        _, path, result = self.app()
        base = f'https://github.com/CV-Pilot/cvpilot-{UID}'
        for link in [result['url'].replace(UID, OTHER), base + '/tree/main/master-resumes/general',
                     base + '/blob/main/' + path + '/../../README.md', base + '/tree/HEAD/' + path,
                     base.replace('https:', 'http:') + '/tree/main/' + path,
                     base.replace('github.com', 'user@github.com') + '/tree/main/' + path,
                     base.replace('github.com', 'github.com:443') + '/tree/main/' + path,
                     base + '/blob/main/' + path + '/resume.pdf/extra']:
            with self.subTest(link=link), self.assertRaises(ValueError): self.call('application-delete', {'link': link})

    def test_symlink_companion_is_refused(self):
        self.upload(); seed = self.fixture.fixture.seed
        self.git('-C', str(seed), 'pull', '--ff-only', 'origin', 'main')
        (seed / 'master-resumes/general/link.cls').symlink_to('../../../README.md')
        self.git('-C', str(seed), 'add', '-A'); self.git('-C', str(seed), 'commit', '-m', 'Unsafe link')
        self.git('-C', str(seed), 'push', 'origin', 'main')
        with self.assertRaisesRegex(ValueError, 'regular files'): self.call('master-delete', {'label': 'general'})

    def test_confirmation_bound_to_user_kind_phrase_single_use_and_cancel(self):
        self.upload(); preview = self.call('master-delete', {'label': 'general'})
        token = preview['confirmation_token']
        for uid, kind, nonce, phrase in [(OTHER, 'master-delete', token, 'DELETE'),
                (UID, 'application-delete', token, 'DELETE'), (UID, 'master-delete', 'wrong', 'DELETE'),
                (UID, 'master-delete', token, 'delete')]:
            with self.assertRaises(ValueError):
                self.call(kind, {'operation': 'confirm', 'token': nonce, 'confirmation': phrase}, uid=uid)
        self.call('master-delete', {'operation': 'cancel', 'token': token})
        with self.assertRaises(ValueError): self.call('master-delete', {'operation': 'confirm', 'token': token, 'confirmation': 'DELETE'})
        preview = self.call('master-delete', {'label': 'general'})
        args = {'operation': 'confirm', 'token': preview['confirmation_token'], 'confirmation': 'DELETE'}
        self.call('master-delete', args)
        with self.assertRaises(ValueError): self.call('master-delete', args)

    def test_expired_confirmation_and_busy_queue_are_refused(self):
        self.upload()
        with mock.patch.object(resume_delete.time, 'monotonic', return_value=1):
            preview = self.call('master-delete', {'label': 'general'})
        args = {'operation': 'confirm', 'token': preview['confirmation_token'], 'confirmation': 'DELETE'}
        with mock.patch.object(resume_delete.time, 'monotonic', return_value=122):
            with self.assertRaisesRegex(ValueError, 'expired'): self.call('master-delete', args)
        preview = self.call('master-delete', {'label': 'general'}); args['token'] = preview['confirmation_token']
        self.store.enqueue(UID, 'profile-sync', {})
        with self.assertRaisesRegex(ValueError, 'Wait'): self.call('master-delete', {'label': 'general'})
        with self.assertRaisesRegex(ValueError, 'Wait'): self.call('master-delete', args)
        with self.assertRaisesRegex(ValueError, 'Wait'): self.store.enqueue(UID, 'master-delete', {})

    def test_changed_main_at_confirmation_or_execution_and_recreated_repo_are_refused(self):
        self.upload(); preview = self.call('master-delete', {'label': 'general'})
        self.fixture.external_edit('note.txt', 'External change')
        with self.assertRaisesRegex(ValueError, 'changed since'):
            self.call('master-delete', {'operation': 'confirm', 'token': preview['confirmation_token'], 'confirmation': 'DELETE'})
        job = self.confirmed('master-delete', label='general')
        self.fixture.external_edit('note.txt', 'Second change')
        with self.assertRaisesRegex(ValueError, 'Main changed after'): worker.work(job, self.store)
        self.fixture.info['id'] = 43
        with self.assertRaisesRegex(ValueError, 'identity changed'): worker.work(job, self.store)

    def test_unconfirmed_or_wrong_scope_payload_refused(self):
        self.upload(); job = self.confirmed('master-delete', label='general')
        data = json.loads(job['payload']); data['confirmed'] = False; job['payload'] = json.dumps(data)
        with self.assertRaisesRegex(ValueError, 'confirmed deletion'): worker.work(job, self.store)
        data['confirmed'] = True; data['directory'] = 'profile'; job['payload'] = json.dumps(data)
        with self.assertRaisesRegex(ValueError, 'Invalid resume directory'): worker.work(job, self.store)

    def test_reset_boundary_excludes_old_commit_links_and_jobs(self):
        _, source = self.upload(); jid, path, app = self.app()
        reset = self.store.enqueue(UID, 'reset', {})
        boundary = self.fixture.external_edit('reset-note.txt', 'Reset boundary fixture')
        self.store.finish(reset, 'done', {'commit': boundary})
        with self.assertRaisesRegex(ValueError, 'last repository reset'):
            self.call('master-delete', {'commit': source['commit']})
        with self.assertRaisesRegex(ValueError, 'last repository reset'):
            self.call('application-delete', {'link': app['url']})
        with self.assertRaisesRegex(ValueError, 'No current application matches'):
            self.call('application-delete', {'link': 'https://example.invalid/jobs/teaching'})

    def test_concurrent_push_during_deletion_is_preserved(self):
        self.upload(); job = self.confirmed('master-delete', label='general')
        original = worker.run_git; newer = []
        def run(uid, repo, *args, **kwargs):
            if args[0] == 'push' and not newer:
                newer.append(self.fixture.external_edit('other.txt', 'Concurrent change'))
            return original(uid, repo, *args, **kwargs)
        with mock.patch.object(worker, 'run_git', side_effect=run):
            with self.assertRaises(RuntimeError): worker.work(job, self.store)
        self.assertEqual(self.main(), newer[0])
        self.assertIn('resume.pdf', self.tree('master-resumes/general'))
        self.assertIn('other.txt', self.tree('other.txt'))

    def test_lost_push_reply_recovers_exact_commit_without_duplicate(self):
        self.upload(); job = self.confirmed('master-delete', label='general')
        original = worker.run_git; lost = []
        def run(uid, repo, *args, **kwargs):
            result = original(uid, repo, *args, **kwargs)
            if args[0] == 'push' and not lost:
                lost.append(True); raise RuntimeError('Lost reply')
            return result
        with mock.patch.object(worker, 'run_git', side_effect=run): result = worker.work(job, self.store)
        recovered = worker.work(job, self.store)
        self.assertEqual(result['commit'], recovered['commit']); self.assertEqual(self.main(), result['commit'])
        self.assertEqual(self.git('-C', str(self.fixture.remote), 'rev-list', '--count', 'main'), '3')

    def test_push_rejection_keeps_main_and_downloads(self):
        source, _ = self.upload(); before = self.main(); job = self.confirmed('master-delete', label='general')
        original = worker.run_git
        def run(uid, repo, *args, **kwargs):
            if args[0] == 'push': raise RuntimeError('Protected main')
            return original(uid, repo, *args, **kwargs)
        with mock.patch.object(worker, 'run_git', side_effect=run):
            with self.assertRaisesRegex(RuntimeError, 'Protected main'): worker.work(job, self.store)
        self.assertEqual(self.main(), before)
        self.assertEqual(len(self.call('download', {'job_id': source['id']})['attachments']), 2)


if __name__ == '__main__': unittest.main()
