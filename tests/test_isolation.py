import os, subprocess, sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'controller'))
import worker

UID='123456789012345678'
class WorktreeIsolationTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.oldroot=worker.ROOT;self.old_git_env=worker.git_env
        worker.ROOT=Path(self.temp.name)/'state'
        worker.git_env=lambda uid:os.environ.copy()
        base=Path(self.temp.name)
        self.bare=base/'remote.git'
        def run(*args,cwd=None):return subprocess.run(args,cwd=cwd,check=True,capture_output=True,text=True).stdout
        run('git','init','--bare','-b','main',str(self.bare))
        seed=base/'seed';run('git','init','-b','main',str(seed))
        run('git','config','user.email','test@example.invalid',cwd=seed)
        run('git','config','user.name','Test',cwd=seed)
        (seed/'master-resumes'/'swe').mkdir(parents=True)
        (seed/'master-resumes'/'swe'/'resume.tex').write_text('seed')
        run('git','add','.',cwd=seed);run('git','commit','-m','seed',cwd=seed)
        run('git','remote','add','origin',str(self.bare),cwd=seed);run('git','push','origin','main',cwd=seed)
        worker.repopath(UID).parent.mkdir(parents=True)
        run('git','clone',str(self.bare),str(worker.repopath(UID)))
    def tearDown(self):
        worker.ROOT=self.oldroot;worker.git_env=self.old_git_env
        self.temp.cleanup()
    def test_model_receives_only_plain_files(self):
        work,branch=worker.new_worktree(UID,101,'tailor')
        self.assertTrue((work/'master-resumes/swe/resume.tex').exists())
        self.assertFalse((work/'.git').exists())
        self.assertTrue((worker.gitworktree(UID,101)/'.git').exists())
        self.assertFalse(worker.gitworktree(UID,101).is_relative_to(work))
        self.assertTrue(worker.safe_output_filename('tailor','applications/2026/job-101/resume.pdf',{}))
        self.assertFalse(worker.safe_output_filename('tailor','applications/2026/job-101/.git/config',{}))
    def test_other_user_path_distinct(self):
        self.assertNotEqual(worker.taskdir(UID,5),worker.taskdir('223456789012345678',5))
        self.assertNotEqual(worker.repopath(UID),worker.repopath('223456789012345678'))
if __name__=='__main__':unittest.main()
