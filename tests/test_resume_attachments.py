"""Transfers use temporary state and local Git only; no live users or services."""
import base64
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'controller'))
import dispatch
import resumes
from store import Store
import worker

UID='123456789012345678'
OTHER='223456789012345678'
PDF=b'%PDF-1.4\nTEST RESUME CONTENT\n%%EOF\n'
TEX=b'\\documentclass{article}\\begin{document}Verified teaching experience\\end{document}'
AGENT_OK=json.dumps({'status':'ok','result':{'payloads':[{'text':'Completed requested edits.'}],'meta':{}}})


def attachments(*files):
    return [{'filename':name,'content_base64':base64.b64encode(data).decode()} for name,data in files]


class FileValidationTests(unittest.TestCase):
    def test_pair_and_profession_independent_labels(self):
        result=resumes.validate_files(attachments(('my-cv.PDF',PDF),('my-cv.tex',TEX)))
        self.assertEqual(result,{'resume.pdf':PDF,'resume.tex':TEX})
        for label in ('general','teaching','nursing','arts','social-work','swe','x'*40):
            self.assertEqual(resumes.label(label),label)
        for label in ('','../secrets','../general','a/b','Upper','two words','x'*41):
            with self.subTest(label=label),self.assertRaises(ValueError):resumes.label(label)

    def test_rejects_invalid_content_encoding_types_and_duplicate_files(self):
        invalid=[[],attachments(('cv.docx',PDF)),attachments(('../cv.pdf',PDF)),
                 attachments(('cv.pdf',b'not a PDF')),attachments(('cv.tex',b'\x00binary')),
                 attachments(('cv.tex',b'\xff')),attachments(('a.pdf',PDF),('b.pdf',PDF)),
                 [{'filename':'cv.pdf','content_base64':'!!!'}]]
        for files in invalid:
            with self.subTest(files=str(files)[:100]),self.assertRaises(ValueError):
                resumes.validate_files(files)

    def test_size_limit_before_staging(self):
        with mock.patch.object(resumes,'MAX_FILE_BYTES',16):
            with self.assertRaisesRegex(ValueError,'8 MiB'):
                resumes.validate_files(attachments(('cv.pdf',PDF*2)))

    def test_staged_data_is_private_durable_and_integrity_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            staged=resumes.stage_upload(directory,UID,attachments(('cv.pdf',PDF)))
            path=resumes.inbox_path(directory,UID,staged['upload_id'])
            self.assertEqual(path.stat().st_mode&0o777,0o700)
            self.assertEqual((path/'resume.pdf').stat().st_mode&0o777,0o600)
            self.assertEqual(resumes.staged_files(directory,UID,staged),{'resume.pdf':PDF})
            (path/'resume.pdf').write_bytes(PDF+b'tampered')
            with self.assertRaisesRegex(ValueError,'changed'):
                resumes.staged_files(directory,UID,staged)


class ResumeWorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.state=self.root/'state'
        self.store=Store(self.state/'jobs.sqlite')
        self.patch(worker,'ROOT',self.state)
        self.patch(worker,'agent_workspace',lambda uid:self.root/'.openclaw'/f'workspace-career-{uid}')
        self.patch(worker,'git_env',lambda uid:os.environ.copy())
        self.remote=self.root/'remote.git';self.seed=self.root/'seed'
        self.git('init','--bare','-b','main',str(self.remote))
        self.git('init','-b','main',str(self.seed))
        self.git('-C',str(self.seed),'config','user.name','Test')
        self.git('-C',str(self.seed),'config','user.email','test@example.invalid')
        (self.seed/'README.md').write_text('Private test workspace')
        (self.seed/'.gitignore').write_text('*.pdf\n')
        self.git('-C',str(self.seed),'add','.')
        self.git('-C',str(self.seed),'commit','-m','seed')
        self.git('-C',str(self.seed),'remote','add','origin',str(self.remote))
        self.git('-C',str(self.seed),'push','origin','main')
        self.original_main=self.git('-C',str(self.seed),'rev-parse','HEAD')
        worker.repopath(UID).parent.mkdir(parents=True)
        self.git('clone',str(self.remote),str(worker.repopath(UID)))
        with self.store.tx() as c:
            for uid in (UID,OTHER):
                c.execute('INSERT INTO users(discord_id,repo,agent) VALUES(?,?,?)',
                          (uid,'CV-Pilot/cvpilot-'+uid,'career-'+uid))

    def patch(self,target,name,value):
        p=mock.patch.object(target,name,value);self.addCleanup(p.stop);return p.start()

    def git(self,*args):
        return subprocess.run(['git',*args],check=True,capture_output=True,text=True).stdout.strip()

    def call(self,action,args=None,uid=UID):
        return dispatch.process({'sender_id':uid,'action':action,'args':args or {}},self.store)

    def upload(self,role='general',files=None):
        files=files if files is not None else attachments(('cv.pdf',PDF),('cv.tex',TEX))
        result=self.call('master',{'operation':'upload','role':role,'files':files})
        self.assertIn('queued',result['text'])
        job=self.store.claim()
        outcome=worker.work(job,self.store)
        self.store.finish(job['id'],'done',outcome)
        return job,outcome

    def legacy_upload(self,role):
        """Seed a pre-main-publication upload to retain compatibility coverage."""
        jid=self.store.enqueue(UID,'master-upload',{})
        branch=f'cvpilot/master-upload/{jid}'
        self.git('-C',str(self.seed),'switch','-c',branch)
        directory=self.seed/'master-resumes'/role;directory.mkdir(parents=True)
        for name,data in (('resume.pdf',PDF),('resume.tex',TEX)):(directory/name).write_bytes(data)
        self.git('-C',str(self.seed),'add','-f','.')
        self.git('-C',str(self.seed),'commit','-m','Legacy upload')
        commit=self.git('-C',str(self.seed),'rev-parse','HEAD')
        self.git('-C',str(self.seed),'push','origin',branch)
        self.git('-C',str(self.seed),'switch','main')
        worker.run_git(UID,worker.repopath(UID),'fetch','origin',branch)
        result={'label':role,'commit':commit,'base_files':{'resume.pdf':None,'resume.tex':None},
                'artifacts':[{'path':f'master-resumes/{role}/{name}'} for name in ('resume.pdf','resume.tex')]}
        self.store.finish(jid,'done',result)
        return self.store.job(jid),result

    def test_upload_commits_to_main_immediately_usable_and_downloadable(self):
        job,result=self.upload('healthcare')
        self.assertEqual(self.git('-C',str(self.remote),'rev-parse','main'),result['commit'])
        self.assertEqual(self.git('-C',str(self.remote),'for-each-ref','--format=%(refname)','refs/heads'),'refs/heads/main')
        self.assertEqual(result['label'],'healthcare')
        self.assertIn('/commit/'+result['commit'],result['url'])
        saved=self.call('master',{'operation':'get','label':'healthcare'})
        self.assertEqual(resumes.validate_files(saved['attachments']),{'resume.pdf':PDF,'resume.tex':TEX})
        self.assertIn('/tree/main/master-resumes/healthcare',saved['text'])
        task,_=worker.new_worktree(UID,100,'profile-sync')
        worker.apply_uploaded_sources(UID,self.store,task)
        self.assertEqual((task/'master-resumes/healthcare/resume.pdf').read_bytes(),PDF)
        worker.cleanup_completed(UID,job['id'])
        downloaded=self.call('download',{'job_id':job['id']})
        self.assertEqual(resumes.validate_files(downloaded['attachments']),{'resume.pdf':PDF,'resume.tex':TEX})
        self.assertIn('CV-Pilot/cvpilot-'+UID,downloaded['text'])

    def test_latest_pdf_only_upload_does_not_reuse_old_tex(self):
        first,old=self.upload('arts')
        second,new=self.upload('arts',attachments(('new.pdf',PDF+b'new version')))
        task,_=worker.new_worktree(UID,101,'profile-sync')
        worker.apply_uploaded_sources(UID,self.store,task)
        self.assertEqual((task/'master-resumes/arts/resume.pdf').read_bytes(),PDF+b'new version')
        self.assertFalse((task/'master-resumes/arts/resume.tex').exists())
        self.assertEqual(resumes.validate_files(self.call('download')['attachments']),
                         {'resume.pdf':PDF+b'new version'})
        self.assertEqual(len(self.call('download',{'job_id':first['id']})['attachments']),2)

    def test_tex_only_upload_compiles_in_isolation_and_downloads_both_files(self):
        builds=[]
        def compile_master(task,tex,**kwargs):
            self.assertFalse(kwargs['require_one_page'])
            self.assertEqual(list(task.iterdir()),[tex])
            self.assertEqual(tex.read_bytes(),TEX)
            builds.append(task)
            pdf=tex.with_suffix('.pdf');pdf.write_bytes(PDF);return pdf
        with mock.patch.object(worker,'compile_pdf',side_effect=compile_master) as compile_mock:
            job,result=self.upload('teaching',attachments(('my-master.tex',TEX)))
        compile_mock.assert_called_once()
        self.assertFalse(builds[0].exists())
        self.assertIn('TeX compiled to PDF',result['note'])
        expected={'resume.pdf':PDF,'resume.tex':TEX}
        self.assertEqual(resumes.validate_files(self.call('master',{'operation':'get','role':'teaching'})['attachments']),expected)
        worker.cleanup_completed(UID,job['id'])
        self.assertEqual(resumes.validate_files(self.call('download',{'job_id':job['id']})['attachments']),expected)
        task,_=worker.new_worktree(UID,103,'profile-sync')
        worker.apply_uploaded_sources(UID,self.store,task)
        self.assertEqual((task/'master-resumes/teaching/resume.pdf').read_bytes(),PDF)
        self.assertEqual(self.git('-C',str(self.remote),'rev-parse','main'),result['commit'])

    def test_supplied_pdf_only_and_pdf_tex_pair_skip_compilation(self):
        with mock.patch.object(worker,'compile_pdf',side_effect=AssertionError('unexpected compilation')):
            self.upload('general',attachments(('resume.pdf',PDF)))
            _,result=self.upload('general')
        self.assertNotIn('TeX compiled',result['note'])

    def test_failed_tex_compilation_keeps_previous_active_resume_and_no_remote_branch(self):
        previous,_=self.upload('general',attachments(('resume.pdf',PDF)))
        self.call('master',{'operation':'upload','files':attachments(('resume.tex',TEX))})
        job=self.store.claim()
        with mock.patch.object(worker,'compile_pdf',side_effect=RuntimeError('Missing package example.sty')):
            with self.assertRaisesRegex(RuntimeError,'Master TeX compilation failed.*Missing package') as raised:
                worker.run_upload_job(job,self.store)
        self.store.finish(job['id'],'failed',error=str(raised.exception))
        self.assertEqual(self.store.latest_uploads(UID)['general']['commit'],json.loads(self.store.job(previous['id'])['result'])['commit'])
        self.assertEqual(resumes.validate_files(self.call('master',{'operation':'get'})['attachments']),{'resume.pdf':PDF})
        self.assertEqual(self.git('-C',str(self.remote),'branch','--list',f'cvpilot/master-upload/{job["id"]}'),'')
        self.assertFalse(list(worker.taskdir(UID,job['id']).glob('master-build-*')))

    def test_tex_upload_retry_reuses_cached_pdf_and_commit_after_push_failure(self):
        self.call('master',{'operation':'upload','files':attachments(('resume.tex',TEX))})
        job=self.store.claim();original=worker.run_git;attempts=[]
        def push_fails_once(uid,directory,*args,**kwargs):
            if args[0]=='push' and not attempts:
                attempts.append(True);raise RuntimeError('temporary push failure')
            return original(uid,directory,*args,**kwargs)
        def compile_master(task,tex,**kwargs):
            pdf=tex.with_suffix('.pdf');pdf.write_bytes(PDF);return pdf
        with (mock.patch.object(worker,'compile_pdf',side_effect=compile_master) as compile_mock,
              mock.patch.object(worker,'run_git',side_effect=push_fails_once)):
            with self.assertRaisesRegex(RuntimeError,'push failure'):worker.run_upload_job(job,self.store)
            commit=self.git('-C',str(worker.gitworktree(UID,job['id'])),'rev-parse','HEAD')
            self.store.recover();again=self.store.claim()
            result=worker.run_upload_job(again,self.store)
        compile_mock.assert_called_once()
        self.assertEqual(result['commit'],commit)
        cache=resumes.inbox_path(self.state,UID,json.loads(job['payload'])['upload_id'])/'compiled.json'
        self.assertEqual(cache.stat().st_mode&0o777,0o600)

    def test_default_label_is_general_and_other_accounts_cannot_download(self):
        self.call('master',{'operation':'upload','files':attachments(('cv.pdf',PDF))})
        job=self.store.claim()
        self.assertEqual(json.loads(job['payload'])['role'],'general')
        result=worker.work(job,self.store);self.store.finish(job['id'],'done',result)
        with self.assertRaisesRegex(ValueError,'your account'):
            self.call('download',{'job_id':job['id']},OTHER)
        with self.assertRaisesRegex(ValueError,'No downloadable'):
            self.call('download',uid=OTHER)

    def test_pending_failed_unknown_and_noninteger_downloads_are_rejected(self):
        pending=self.store.enqueue(UID,'tailor',{})
        for value in (pending,999,'1',True,-1):
            with self.subTest(value=value),self.assertRaises(ValueError):
                self.call('download',{'job_id':value})
        self.store.finish(pending,'failed',error='test')
        with self.assertRaisesRegex(ValueError,'not complete'):
            self.call('download',{'job_id':pending})

    def test_main_updates_take_precedence_over_earlier_uploads(self):
        self.legacy_upload('teaching')
        directory=self.seed/'master-resumes/teaching';directory.mkdir(parents=True)
        newer=TEX+b' newer verified main source'
        (directory/'resume.tex').write_bytes(newer)
        self.git('-C',str(self.seed),'add','.')
        self.git('-C',str(self.seed),'commit','-m','new source on main')
        self.git('-C',str(self.seed),'push','origin','main')
        task,_=worker.new_worktree(UID,102,'profile-sync')
        worker.apply_uploaded_sources(UID,self.store,task)
        self.assertEqual((task/'master-resumes/teaching/resume.tex').read_bytes(),newer)
        self.assertFalse((task/'master-resumes/teaching/resume.pdf').exists())
        downloaded=self.call('master',{'operation':'get','role':'teaching'})
        self.assertEqual(resumes.validate_files(downloaded['attachments']),{'resume.tex':newer})

    def test_merged_upload_allows_later_master_edits_on_main(self):
        job,result=self.legacy_upload('general')
        self.git('-C',str(self.seed),'fetch','origin',f'cvpilot/master-upload/{job["id"]}')
        self.git('-C',str(self.seed),'merge','--ff-only','FETCH_HEAD')
        (self.seed/'master-resumes/general/resume.tex').write_bytes(TEX+b' corrected main version')
        self.git('-C',str(self.seed),'add','.')
        self.git('-C',str(self.seed),'commit','-m','reviewed update')
        self.git('-C',str(self.seed),'push','origin','main')
        downloaded=self.call('master',{'operation':'get'})
        self.assertEqual(resumes.validate_files(downloaded['attachments'])['resume.tex'],TEX+b' corrected main version')

    def test_stale_clone_check_is_after_fetch_and_supports_pdf_only_source(self):
        directory=self.seed/'master-resumes/general';directory.mkdir(parents=True)
        (directory/'resume.pdf').write_bytes(PDF)
        self.git('-C',str(self.seed),'add','-f','master-resumes/general/resume.pdf')
        self.git('-C',str(self.seed),'commit','-m','GitHub-equivalent upload')
        self.git('-C',str(self.seed),'push','origin','main')
        self.assertFalse((worker.repopath(UID)/'master-resumes/general/resume.pdf').exists())
        def agent(args,**kwargs):
            if args[0]=='openclaw':
                task=worker.agent_taskdir(UID,200)
                self.assertEqual((task/'master-resumes/general/resume.pdf').read_bytes(),PDF)
                destination=task/'applications/2026/job-200';destination.mkdir(parents=True)
                (destination/'resume.tex').write_bytes(TEX)
                (destination/'job-url.txt').write_text('https://example.org/job')
                return AGENT_OK
            return original_run(args,**kwargs)
        original_run=worker.run
        def compile_pdf(task,target):
            target.with_suffix('.pdf').write_bytes(PDF)
        with (mock.patch.object(worker,'run',side_effect=agent),
              mock.patch.object(worker,'compile_pdf',side_effect=compile_pdf)):
            job={'discord_id':UID,'id':200,'kind':'tailor','payload':json.dumps({'url':'https://example.org/job'})}
            result=worker.run_resume_job(job,self.store)
        self.assertEqual(len(result['artifacts']),2)
        self.assertIn('PDF is available',worker.agent_prompt(job,Path('/unused')))
        self.assertEqual(dict(resumes.artifact_files(worker.repopath(UID),result)),{'resume.pdf':PDF,'resume.tex':TEX})
        worker.cleanup_completed(UID,200)
        self.assertEqual(len(resumes.artifact_files(worker.repopath(UID),result)),2)
        self.assertEqual(self.git('-C',str(self.remote),'rev-parse','main'),result['commit'])
        self.git('-C',str(self.remote),'merge-base','--is-ancestor',
                 self.git('-C',str(self.seed),'rev-parse','HEAD'),result['commit'])

    def test_queue_payload_contains_no_file_bytes_and_enqueue_failure_cleans_staging(self):
        self.call('master',{'operation':'upload','files':attachments(('cv.pdf',PDF))})
        payload=json.loads(self.store.claim()['payload'])
        self.assertNotIn('content_base64',json.dumps(payload))
        self.assertNotIn(PDF.decode(),json.dumps(payload))
        before=set((self.state/'resume-inbox'/UID).iterdir())
        with mock.patch.object(self.store,'enqueue',side_effect=RuntimeError('queue failure')):
            with self.assertRaisesRegex(RuntimeError,'queue failure'):
                self.call('master',{'operation':'upload','files':attachments(('cv.pdf',PDF))})
        self.assertEqual(set((self.state/'resume-inbox'/UID).iterdir()),before)

    def test_upload_retry_reuses_main_commit_without_an_extra_commit(self):
        self.call('master',{'operation':'upload','files':attachments(('cv.pdf',PDF))})
        job=self.store.claim()
        first=worker.run_upload_job(job,self.store)
        self.store.recover()
        again=self.store.claim()
        second=worker.run_upload_job(again,self.store)
        self.assertEqual(first['commit'],second['commit'])
        self.assertTrue(first['on_main']);self.assertTrue(second['on_main'])

    def test_download_rejects_symlinks_and_arbitrary_artifact_paths(self):
        (self.seed/'master-resumes/general').mkdir(parents=True)
        (self.seed/'master-resumes/general/resume.pdf').symlink_to('/etc/passwd')
        self.git('-C',str(self.seed),'add','-f','master-resumes/general/resume.pdf')
        self.git('-C',str(self.seed),'commit','-m','untrusted symlink')
        self.git('-C',str(self.seed),'push','origin','main')
        worker.run_git(UID,worker.repopath(UID),'fetch','origin','main')
        with self.assertRaisesRegex(ValueError,'regular files'):
            resumes.read_blob(worker.repopath(UID),'origin/main','master-resumes/general/resume.pdf')
        for path in ('../../keys/id_ed25519','profile/MASTER_PROFILE.md','.git/config','master-resumes/general/../../key.pdf'):
            with self.subTest(path=path),self.assertRaises(ValueError):
                resumes.read_blob(worker.repopath(UID),'origin/main',path)

    def test_untrusted_source_symlink_is_never_dereferenced_into_sandbox(self):
        secret=self.root/'outside-secret';secret.write_text('PRIVATE OUTSIDE CONTENT')
        (self.seed/'source-link').symlink_to(secret)
        self.git('-C',str(self.seed),'add','source-link')
        self.git('-C',str(self.seed),'commit','-m','untrusted source link')
        self.git('-C',str(self.seed),'push','origin','main')
        with self.assertRaisesRegex(RuntimeError,'Symlinks'):
            worker.new_worktree(UID,300,'tailor')
        self.assertTrue((worker.taskdir(UID,300)/'source-link').is_symlink())

    def test_finished_jobs_deliver_attachments_after_successful_publication(self):
        _,result=self.upload()
        jid=self.store.enqueue(UID,'tailor',{'url':'https://example.org/job'})
        stop=threading.Event();sent=[]
        def notify(uid,text,files=None):
            sent.append((uid,text,files));stop.set()
        with (mock.patch.object(worker,'work',return_value=result),
              mock.patch.object(worker,'notify',side_effect=notify)):
            worker.forever(self.store,stop)
        self.assertEqual(self.store.job(jid)['state'],'done')
        self.assertEqual(sent[0][0],UID)
        self.assertEqual(dict(sent[0][2]),{'resume.pdf':PDF,'resume.tex':TEX})

    def test_controller_socket_transfers_attachments_larger_than_old_limit(self):
        path=self.root/'test.sock'
        server=dispatch.UnixServer(str(path),dispatch.UnixHandler);server.store=self.store
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        data=PDF+b'x'*150000
        try:
            packet=json.dumps({'sender_id':UID,'action':'master','args':{'operation':'upload',
                    'files':attachments(('cv.pdf',data))}}).encode()+b'\n'
            with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
                sock.settimeout(5);sock.connect(str(path));sock.sendall(packet)
                with sock.makefile('r') as stream:response=json.loads(stream.readline())
            self.assertTrue(response['ok'])
            job=self.store.claim();payload=json.loads(job['payload'])
            self.assertEqual(resumes.staged_files(self.state,UID,payload)['resume.pdf'],data)
        finally:
            server.shutdown();server.server_close();thread.join()


class CompilationTests(unittest.TestCase):
    def test_engine_selection_preserves_standard_tex_and_supports_fontspec_and_directives(self):
        cases=[(TEX,'pdflatex'),
               (b'\\usepackage{fontspec}\n\\setmainfont{Carlito}','xelatex'),
               (b'\\usepackage[option]{xcolor, unicode-math}','xelatex'),
               (b'\\setsansfont{Carlito}','xelatex'),
               (b'% \\usepackage{fontspec}\n'+TEX,'pdflatex'),
               (b'% !TeX program = XeLaTeX\n'+TEX,'xelatex'),
               (b'% !TEX TS-program = lualatex\n'+TEX,'lualatex'),
               (b'% !TeX program = pdflatex\n\\usepackage{fontspec}','pdflatex'),
               (b'% !TeX program = arbitrary-command\n'+TEX,'pdflatex')]
        with tempfile.TemporaryDirectory() as directory:
            tex=Path(directory)/'resume.tex'
            for source,engine in cases:
                with self.subTest(source=source):
                    tex.write_bytes(source)
                    self.assertEqual(worker.latex_engine(tex),engine)

    def test_fontspec_uses_xelatex_and_reports_actual_error_instead_of_latexmk_footer(self):
        with tempfile.TemporaryDirectory() as directory:
            task=Path(directory);tex=task/'resume.tex';tex.write_bytes(b'\\usepackage{fontspec}')
            def run(args,**kwargs):
                self.assertIn('-xelatex',args)
                self.assertIn('-xelatex=xelatex -no-shell-escape %O %S',args)
                tex.with_suffix('.log').write_text('Build output\n! Package fontspec Error: The font "MissingFont" cannot be found.\n\nFor help\nl.45 \\setmainfont{MissingFont}\n')
                raise RuntimeError('Latexmk: If appropriate, the -f option can be used')
            with mock.patch.object(worker,'run',side_effect=run):
                with self.assertRaisesRegex(RuntimeError,'xelatex: Package fontspec Error:.*MissingFont.*l.45') as error:
                    worker.compile_pdf(task,tex)
            self.assertNotIn('the -f option',str(error.exception))

    def test_compiler_runtime_error_without_tex_log_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            task=Path(directory);tex=task/'resume.tex';tex.write_bytes(TEX)
            with mock.patch.object(worker,'run',side_effect=RuntimeError('Docker image unavailable')):
                with self.assertRaisesRegex(RuntimeError,'Docker image unavailable'):
                    worker.compile_pdf(task,tex)

    def test_page_validation_runs_in_networkless_compiler_and_sources_can_be_multipage(self):
        with tempfile.TemporaryDirectory() as directory:
            task=Path(directory);tex=task/'resume.tex';tex.write_bytes(TEX)
            calls=[]
            def run(args,**kwargs):
                calls.append(args)
                if 'latexmk' in args:
                    tex.with_suffix('.pdf').write_bytes(PDF);return ''
                return 'Pages:          2'
            with mock.patch.object(worker,'run',side_effect=run):
                worker.compile_pdf(task,tex,require_one_page=False)
                with self.assertRaisesRegex(RuntimeError,'exactly one page'):worker.compile_pdf(task,tex)
            self.assertIn('-norc',calls[0])
            self.assertIn('-pdflatex=pdflatex -no-shell-escape %O %S',calls[0])
            for args in calls:
                self.assertEqual(args[:3],['docker','run','--rm'])
                self.assertEqual(args[args.index('--network')+1],'none')
                self.assertIn('no-new-privileges',args)
            self.assertEqual(calls[1][-2:],['pdfinfo','resume.pdf'])

    def test_missing_empty_and_oversized_generated_pdf_fail_before_caching(self):
        with tempfile.TemporaryDirectory() as directory:
            task=Path(directory);cache=task/'compiled.json'
            for contents in (b'',b'invalid',PDF*2):
                def compile_master(build,tex,**kwargs):
                    pdf=tex.with_suffix('.pdf');pdf.write_bytes(contents);return pdf
                with (self.subTest(contents=contents),
                      mock.patch.object(worker,'compile_pdf',side_effect=compile_master),
                      mock.patch.object(worker,'MAX_FILE_BYTES',len(PDF))):
                    with self.assertRaisesRegex(RuntimeError,'Master TeX compilation failed'):
                        worker.compile_uploaded_master(task,{'resume.tex':TEX},cache)
                self.assertFalse(cache.exists())
                self.assertEqual(list(task.iterdir()),[])

    def test_cache_is_bound_to_uploaded_tex(self):
        with tempfile.TemporaryDirectory() as directory:
            task=Path(directory);cache=task/'compiled.json'
            cache.write_text(json.dumps({'source_sha256':'different-source','attachment':attachments(('resume.pdf',PDF))[0]}))
            with mock.patch.object(worker,'compile_pdf') as compile_mock:
                with self.assertRaisesRegex(ValueError,'source changed'):
                    worker.compile_uploaded_master(task,{'resume.tex':TEX},cache)
            compile_mock.assert_not_called()


class NotificationTests(unittest.TestCase):
    def test_completion_dm_uses_private_multipart_attachments_without_mentions(self):
        requests=[]
        def respond(request,**kwargs):
            requests.append(request)
            response=mock.MagicMock()
            response.__enter__.return_value.read.return_value=(b'{"id":"private-channel"}' if len(requests)==1 else b'{}')
            return response
        with (mock.patch.dict(os.environ,{'CVPILOT_DISCORD_TOKEN':'test-token'}),
              mock.patch('urllib.request.urlopen',side_effect=respond)):
            worker.notify(UID,'Complete. /cvpilot download job_id:1',files=[('resume.pdf',PDF),('resume.tex',TEX)])
        self.assertTrue(requests[0].full_url.endswith('/users/@me/channels'))
        self.assertEqual(json.loads(requests[0].data)['recipient_id'],UID)
        self.assertTrue(requests[1].full_url.endswith('/channels/private-channel/messages'))
        self.assertIn('multipart/form-data',requests[1].get_header('Content-type'))
        self.assertIn(PDF,requests[1].data)
        self.assertIn(TEX,requests[1].data)
        self.assertIn(b'"allowed_mentions": {"parse": []}',requests[1].data)

    def test_failed_attachment_delivery_falls_back_to_text_without_failing_job(self):
        requests=[]
        def respond(request,**kwargs):
            requests.append(request)
            if len(requests)==2:raise OSError('attachment refused')
            response=mock.MagicMock()
            response.__enter__.return_value.read.return_value=b'{"id":"private-channel"}'
            return response
        with (mock.patch.dict(os.environ,{'CVPILOT_DISCORD_TOKEN':'test-token'}),
              mock.patch('urllib.request.urlopen',side_effect=respond)):
            worker.notify(UID,'Complete. /cvpilot download job_id:1',files=[('resume.pdf',PDF)])
        self.assertEqual(len(requests),3)
        self.assertEqual(requests[2].get_header('Content-type'),'application/json')
        self.assertNotIn('attachments',json.loads(requests[2].data))


if __name__=='__main__':unittest.main()
