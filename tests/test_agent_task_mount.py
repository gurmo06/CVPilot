"""Job staging stays inside one agent workspace without Git metadata or credentials."""
from pathlib import Path
import unittest
from unittest import mock
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'controller'))
import worker
import test_resume_attachments as fixtures

UID=fixtures.UID
OTHER=fixtures.OTHER


class AgentTaskMountTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.ResumeWorkflowTests('runTest');self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root=self.fixture.root;self.store=self.fixture.store

    def test_stage_only_plain_task_files_under_own_agent_workspace(self):
        original,_=worker.new_worktree(UID,101,'profile-sync')
        staged=worker.stage_agent_task(UID,101,original)
        self.assertTrue(staged.is_relative_to(worker.agent_workspace(UID)))
        self.assertFalse((staged/'.git').exists())
        self.assertFalse(worker.gitworktree(UID,101).is_relative_to(worker.agent_task_root(UID)))
        self.assertFalse(worker.keypath(UID).is_relative_to(worker.agent_task_root(UID)))
        self.assertEqual((staged/'README.md').read_bytes(),(original/'README.md').read_bytes())
        for directory in (worker.agent_task_root(UID),staged.parent,staged):
            self.assertEqual(directory.stat().st_mode&0o777,0o700)
        self.assertNotEqual(staged,worker.agent_taskdir(OTHER,101))
        self.assertFalse(worker.agent_workspace(OTHER).exists())

    def test_restart_reuses_partial_agent_output_and_cleanup_removes_both_copies(self):
        original,_=worker.new_worktree(UID,102,'profile-sync')
        staged=worker.stage_agent_task(UID,102,original)
        (staged/'partial.txt').write_text('Keep interrupted work')
        self.assertEqual(worker.stage_agent_task(UID,102,original),staged)
        self.assertEqual((staged/'partial.txt').read_text(),'Keep interrupted work')
        worker.cleanup_completed(UID,102)
        self.assertFalse(original.exists());self.assertFalse(staged.exists())
        self.assertTrue(worker.agent_workspace(UID).exists())

    def test_symlink_inputs_or_outputs_are_rejected_without_reading_external_files(self):
        original,_=worker.new_worktree(UID,103,'profile-sync')
        outside=self.root/'private';outside.write_text('Private outside content')
        (original/'link').symlink_to(outside)
        with self.assertRaisesRegex(RuntimeError,'Symlinks'):worker.stage_agent_task(UID,103,original)
        self.assertFalse(worker.agent_taskdir(UID,103).exists())
        (original/'link').unlink()
        staged=worker.stage_agent_task(UID,103,original)
        (staged/'link').symlink_to(outside)
        with self.assertRaisesRegex(RuntimeError,'Symlinks'):worker.validate_agent_task(UID,103)

    def test_symlink_task_parent_is_rejected(self):
        original,_=worker.new_worktree(UID,104,'profile-sync')
        root=worker.agent_task_root(UID);root.mkdir(parents=True)
        outside=self.root/'elsewhere';outside.mkdir()
        (root/'tasks').symlink_to(outside,target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError,'Unsafe'):worker.stage_agent_task(UID,104,original)
        self.assertEqual(list(outside.iterdir()),[])

    def test_changed_agent_task_cannot_publish_a_host_symlink(self):
        original_run=worker.run
        jid=self.store.enqueue(UID,'profile-edit',{});job=self.store.claim()
        def agent(argv,**kwargs):
            if argv[0]=='openclaw':
                task=worker.agent_taskdir(UID,jid)
                (task/'profile').mkdir()
                (task/'profile/MASTER_PROFILE.md').symlink_to('/etc/passwd')
                return fixtures.AGENT_OK
            return original_run(argv,**kwargs)
        before=self.fixture.git('-C',str(self.fixture.remote),'rev-parse','main')
        with mock.patch.object(worker,'run',side_effect=agent):
            with self.assertRaisesRegex(RuntimeError,'Symlinks'):worker.work(job,self.store)
        self.assertEqual(self.fixture.git('-C',str(self.fixture.remote),'rev-parse','main'),before)


if __name__=='__main__':unittest.main()
