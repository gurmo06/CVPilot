"""Inline tailoring needs no model filesystem calls; all state/Git is temporary."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock
import test_controller as fixtures
import test_resume_attachments as workflow
import profile_sync
import tailoring
import worker
from resume_quality import POLICY

TEX=r'\documentclass{article}\begin{document}Verified teaching experience\end{document}'
URL='https://jobs.example/teaching'


def response(tex=TEX,notes='Teaching experience and supported classroom keywords.',profile=None):
    text=f'```cvpilot-resume-tex\n{tex}\n```\n```cvpilot-notes\n{notes}\n```'
    if profile is not None:text+='\n```cvpilot-profile-json\n'+json.dumps(profile)+'\n```'
    return {'payloads':[{'text':text}]}


def updated(pack):
    path=pack['documents'][0]['path']
    return {'status':'updated','profile_markdown':'# Career profile\n\nVerified teaching experience.\n',
            'profile_sources':{'schema_version':1,'source_manifest':pack['source_manifest'],
                'facts':{'teaching':{'text':'Verified teaching experience',
                    'sources':[{'path':path,'sha256':pack['source_manifest'][path]}]}}}}


class TailoringTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.task=self.root/'task';self.task.mkdir()
        self.template=self.root/'template.md';self.template.write_text('# Empty profile template')
        source=self.task/'master-resumes/teaching/resume.tex';source.parent.mkdir(parents=True);source.write_text(TEX)
        self.pack=tailoring.prepare(self.task,'a'*40,mock.Mock())
        profile_sync.apply_response(self.task,{'payloads':[{'text':json.dumps(updated(self.pack))}]})
        self.pack=tailoring.prepare(self.task,'a'*40,mock.Mock())
        self.folder=self.task/'applications'/str(time.gmtime().tm_year)/'job-33'

    def apply(self,report=None,pack=None):
        return tailoring.apply(self.task,report or response(),33,URL,pack or self.pack)

    def test_all_nested_labels_are_inline_and_paired_pdf_does_not_replace_tex(self):
        pdf=self.task/'master-resumes/teaching/resume.pdf';pdf.write_bytes(workflow.PDF)
        other=self.task/'master-resumes/healthcare/resume.tex';other.parent.mkdir();other.write_text('Verified healthcare experience')
        pdfonly=self.task/'master-resumes/arts/resume.pdf';pdfonly.parent.mkdir();pdfonly.write_bytes(workflow.PDF)
        extract=mock.Mock(return_value='Verified arts experience')
        pack=tailoring.prepare(self.task,'a'*40,extract)
        self.assertEqual(len(pack['source_manifest']),4);self.assertEqual(len(pack['documents']),3)
        self.assertEqual({item['path'] for item in pack['documents']},{'master-resumes/teaching/resume.tex','master-resumes/healthcare/resume.tex','master-resumes/arts/resume.pdf'})
        self.assertIn(TEX,[item['text'] for item in pack['documents']]);extract.assert_called_once_with(self.task,pdfonly)
        self.assertEqual((self.task/'.cvpilot-sync-inputs.json').stat().st_mode&0o777,0o600)

    def test_prompt_has_full_source_text_profile_hashes_and_no_filesystem_dependency(self):
        job={'id':33,'discord_id':fixtures.UID,'kind':'tailor','payload':json.dumps({'url':URL})}
        prompt=worker.agent_prompt(job,{'text':'Exact public teaching posting'},self.pack)
        self.assertIn('DO NOT use ls, read, write, edit, exec',prompt)
        self.assertIn('Verified teaching experience',prompt);self.assertIn('source_manifest',prompt)
        self.assertIn('current_profile_sources',prompt);self.assertIn('cvpilot-resume-tex',prompt)
        self.assertNotIn('ONLY work inside',prompt);self.assertNotIn('/career/tasks',prompt)
        self.assertIn('ATS-friendly',prompt);self.assertIn('any profession',prompt)
        self.assertIn('profile is read-only',prompt);self.assertIn('two fenced blocks',prompt)
        self.assertIn('citations match the supplied current source hashes',prompt)
        self.assertNotIn('cvpilot-profile-json',prompt)
        self.assertIn(POLICY,prompt)
        self.assertIn('actively selecting and adapting content',prompt)
        self.assertIn('Do not merely shorten a pre-existing resume',prompt)
        self.assertIn('Draft autonomously from available evidence',prompt)
        self.assertIn('Do not ask qualification questions',prompt)
        self.assertIn('no questions or requests for user input',prompt)
        self.assertIn('Company: <employer name stated in the posting',prompt)
        self.assertIn('Position: <exact position title stated in the posting',prompt)
        with self.assertRaisesRegex(ValueError,'readable resume text'):tailoring.prompt(job,None,None)

    def test_template_text_is_literal_tex_instead_of_json_escaped(self):
        rendered=tailoring.source_text(self.pack)
        self.assertIn(TEX,rendered)
        self.assertIn('```tex\n'+TEX+'\n```',rendered)
        self.assertNotIn(TEX.replace('\\','\\\\'),rendered)
        self.assertIn('"source_manifest"',rendered)
        self.assertNotIn('"documents"',rendered)

    def test_source_fences_cannot_be_closed_by_fences_inside_a_resume(self):
        pack=copy.deepcopy(self.pack);pack['documents'][0]['text']+='\n```\nuntrusted example\n```'
        rendered=tailoring.source_text(pack)
        self.assertIn('````tex\n',rendered)
        self.assertIn(pack['documents'][0]['text'],rendered)
        self.assertTrue(rendered.endswith('\n````'))

    def test_raw_tex_is_written_without_json_decoding_or_model_chosen_paths(self):
        tex=TEX.replace('Verified',r'\textbf{Verified}')
        self.assertTrue(self.apply(response(tex=tex)))
        self.assertEqual((self.folder/'resume.tex').read_text(),tex+'\n')
        self.assertEqual((self.folder/'job-url.txt').read_text(),URL+'\n')
        self.assertIn('Teaching experience',(self.folder/'notes.md').read_text())
        for file in self.folder.iterdir():self.assertEqual(file.stat().st_mode&0o777,0o600)
        self.assertEqual((self.task/'master-resumes/teaching/resume.tex').read_text(),TEX)

    def test_missing_truncated_duplicate_or_oversized_blocks_fail_before_outputs(self):
        original=response()['payloads'][0]['text']
        invalid=['```cvpilot-resume-tex\n'+TEX,original.replace('```cvpilot-resume-tex','```wrong-tex'),
                 original+'\n```cvpilot-resume-tex\n'+TEX+'\n```',response(notes='x'*16001)['payloads'][0]['text'],
                 response(tex='x'*250001)['payloads'][0]['text']]
        for text in invalid:
            with self.subTest(size=len(text)),self.assertRaises(RuntimeError):self.apply({'payloads':[{'text':text}]})
            self.assertFalse(self.folder.exists())

    def test_incomplete_tex_cannot_be_published(self):
        for report in (response(tex=TEX.replace(r'\end{document}','')),
                       response(tex=TEX+'\x08')):
            with self.subTest(report=str(report)[:40]),self.assertRaises((RuntimeError,ValueError)):self.apply(report)
            self.assertFalse(self.folder.exists())

    def test_unsolicited_profile_is_ignored_and_confirmed_facts_are_byte_identical(self):
        prior={'text':'User-confirmed teaching credential','user_confirmed':True}
        path=self.task/'profile/profile_sources.json';saved=json.loads(path.read_text())
        saved['facts']['confirmed']=prior;path.write_text(json.dumps(saved))
        self.pack=tailoring.prepare(self.task,'a'*40,mock.Mock())
        original={name:(self.task/name).read_bytes() for name in tailoring.PROFILE_FILES}
        # Job #37 flattened facts/manifest and repeated profile entries. Even a
        # syntactically valid or truncated unsolicited update must never apply.
        bad={'status':'updated','profile_markdown':'Repeated entry\n'*50,'facts':{},'source_manifest':{}}
        for suffix in ('\n```cvpilot-profile-json\n'+json.dumps(bad)+'\n```',
                       '\n```cvpilot-profile-json\n{"status":"updated",',
                       '\n**cvpilot-profile-json**\n'+json.dumps(updated(self.pack))):
            self.assertTrue(self.apply({'final':response()['payloads'][0]['text']+suffix}))
            self.assertEqual({name:(self.task/name).read_bytes() for name in original},original)
        saved=json.loads(path.read_text())
        self.assertEqual(saved['facts']['confirmed'],prior)

    def test_current_source_change_is_rejected(self):
        (self.task/'master-resumes/teaching/resume.tex').write_text(TEX+'\nNew source fact')
        with self.assertRaisesRegex(ValueError,'changed source resumes'):self.apply()
        self.assertFalse(self.folder.exists())

    def test_profile_is_optional_and_stale_profile_is_left_unchanged(self):
        for name in tailoring.PROFILE_FILES:(self.task/name).unlink()
        self.pack=tailoring.prepare(self.task,'a'*40,mock.Mock())
        self.assertTrue(self.apply())
        self.assertTrue(all(not (self.task/name).exists() for name in tailoring.PROFILE_FILES))
        (self.task/'profile/MASTER_PROFILE.md').write_text('# Old profile')
        (self.task/'profile/profile_sources.json').write_text('{"schema_version":1,"source_manifest":{},"facts":{}}')
        self.pack=tailoring.prepare(self.task,'a'*40,mock.Mock())
        original=tailoring.profile_snapshot(self.task)
        self.assertTrue(self.apply());self.assertEqual(tailoring.profile_snapshot(self.task),original)

    def test_profile_modification_deletion_and_creation_are_rejected(self):
        for name in tailoring.PROFILE_FILES:
            path=self.task/name;original=path.read_bytes()
            for change in ('modify','delete'):
                if change=='modify':path.write_text('Unexpected model edit')
                else:path.unlink()
                with self.subTest(name=name,change=change),self.assertRaisesRegex(ValueError,'Read-only profile changed'):
                    self.apply()
                self.assertFalse(self.folder.exists());path.write_bytes(original)
        path.unlink();self.pack=tailoring.prepare(self.task,'a'*40,mock.Mock());path.write_text('{}')
        with self.assertRaisesRegex(ValueError,'Read-only profile changed'):self.apply()

    def test_heading_tex_fence_and_optional_notes_are_supported(self):
        for suffix in ('','\n**cvpilot-notes**\nPlain rationale.',
                       '\n```cvpilot-notes\nUnclosed optional notes.'):
            report={'final':'## CVPilot-Resume-Tex\n~~~latex\n'+TEX+'\n~~~'+suffix}
            self.assertTrue(self.apply(report));self.assertEqual((self.folder/'resume.tex').read_text(),TEX+'\n')

    def test_unclosed_notes_stop_before_unwanted_profile_metadata(self):
        text='```cvpilot-resume-tex\n'+TEX+'\n```\n```cvpilot-notes\nSafe rationale.\n'
        for metadata in ('```cvpilot-profile-json\n','**cvpilot-profile-json**\n'):
            self.assertTrue(self.apply({'final':text+metadata+'Unwanted repeated profile data '*1000}))
            self.assertEqual((self.folder/'notes.md').read_text(),'Safe rationale.\n')

    def test_tailoring_allowlist_cannot_publish_or_delete_profile_files(self):
        for name in tailoring.PROFILE_FILES:
            self.assertFalse(worker.allowed_path('tailor',name,{}))
            self.assertTrue(worker.allowed_path('profile-sync',name,{}))

    def test_reasoning_and_commentary_are_not_saved_as_resume_output(self):
        report={'payloads':[{'text':response()['payloads'][0]['text'],'isReasoning':True}]}
        self.assertFalse(self.apply(report));self.assertFalse(self.folder.exists())
        report={'final':response()['payloads'][0]['text']}
        self.assertTrue(self.apply(report))

    def test_legacy_retained_file_draft_remains_usable_without_blocks(self):
        self.assertFalse(self.apply({'payloads':[{'text':'Completed file-based draft.'}]}))

    def test_source_and_output_symlinks_are_rejected_without_external_writes(self):
        external=self.root/'external';external.mkdir()
        (self.task/'applications').symlink_to(external,target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError,'Unsafe'):self.apply()
        self.assertEqual(list(external.iterdir()),[])
        link=self.task/'master-resumes/teaching/unsafe.tex';link.symlink_to(self.template)
        with self.assertRaisesRegex(ValueError,'symlinks'):tailoring.prepare(self.task,'a'*40,mock.Mock())

    def test_output_hardlink_is_replaced_without_changing_source(self):
        self.folder.mkdir(parents=True)
        source=self.task/'master-resumes/teaching/resume.tex'
        (self.folder/'resume.tex').hardlink_to(source)
        tex=TEX.replace('Verified teaching','Verified classroom')
        self.assertTrue(self.apply(response(tex=tex)))
        self.assertEqual(source.read_text(),TEX)

    def test_profile_symlink_is_rejected_before_extraction(self):
        path=self.task/'profile/MASTER_PROFILE.md';path.unlink();path.symlink_to(self.template)
        with self.assertRaisesRegex(ValueError,'Unsafe read-only profile'):
            tailoring.prepare(self.task,'a'*40,mock.Mock())


class TailoringWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.fixture=workflow.ResumeWorkflowTests('runTest');self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.store=self.fixture.store;self.uid=workflow.UID
        directory=self.fixture.seed/'profile';directory.mkdir()
        (directory/'MASTER_PROFILE.md').write_text('# Prior confirmed facts\nTeaching credential')
        (directory/'profile_sources.json').write_text(json.dumps({'schema_version':1,'source_manifest':{},
            'facts':{'credential':{'text':'Teaching credential','user_confirmed':True}}}))
        self.fixture.git('-C',str(self.fixture.seed),'add','profile')
        self.fixture.git('-C',str(self.fixture.seed),'commit','-m','Seed read-only profile')
        self.fixture.git('-C',str(self.fixture.seed),'push','origin','main')
        job,_=self.fixture.upload(role='teaching');worker.cleanup_completed(self.uid,job['id'])

    def test_one_model_text_response_is_compiled_and_published_with_both_files(self):
        jid=self.store.enqueue(self.uid,'tailor',{'url':URL});job=self.store.claim();original=worker.run;calls=[]
        before={name:self.fixture.git('-C',str(self.fixture.remote),'show','main:'+name) for name in tailoring.PROFILE_FILES}
        def agent(argv,**kwargs):
            if argv[0]!='openclaw':return original(argv,**kwargs)
            calls.append(argv)
            prompt=Path(argv[argv.index('--message-file')+1]).read_text()
            self.assertIn('Verified teaching experience',prompt)
            self.assertIn('DO NOT use ls, read, write, edit, exec',prompt)
            # No model writes or reads the sandbox task. The worker owns output.
            canonical=worker.taskdir(self.uid,jid)
            pack=json.loads((canonical/'.cvpilot-sync-inputs.json').read_text())
            return json.dumps(response(notes='Company: Example School\nPosition: Classroom Teacher\nVerified classroom keywords.\nQuestion: First aid | Do you have first-aid experience or training?',profile=updated(pack)))
        def compile_pdf(task,tex):
            self.assertIn('Verified teaching experience',tex.read_text())
            tex.with_suffix('.pdf').write_bytes(workflow.PDF)
        with mock.patch.object(worker,'run',side_effect=agent),mock.patch.object(worker,'compile_pdf',side_effect=compile_pdf):
            result=worker.work(job,self.store)
        self.assertEqual(len(calls),1);self.assertTrue(result['on_main']);self.assertTrue(result['changed'])
        self.assertEqual(len(result['artifacts']),2)
        self.assertEqual(result['questions'],[])
        self.assertNotIn('/cvpilot answer job_id:',result['note'])
        folder=f'applications/{time.gmtime().tm_year}/job-{jid}-example-school-classroom-teacher'
        for name in ('resume.pdf','resume.tex','job-url.txt','notes.md'):
            self.assertTrue(self.fixture.git('-C',str(self.fixture.remote),'ls-tree','main','--',folder+'/'+name))
        self.assertEqual(self.fixture.git('-C',str(self.fixture.remote),'show','main:master-resumes/teaching/resume.tex'),workflow.TEX.decode())
        self.assertFalse(self.fixture.git('-C',str(self.fixture.remote),'ls-tree','main','--','.cvpilot-sync-inputs.json'))
        self.assertEqual({name:self.fixture.git('-C',str(self.fixture.remote),'show','main:'+name) for name in before},before)
        self.assertEqual(set(self.fixture.git('-C',str(self.fixture.remote),'diff-tree','--no-commit-id','--name-only','-r','main').splitlines()),
                         {folder+'/'+name for name in ('resume.pdf','resume.tex','job-url.txt','notes.md')})
        self.assertEqual(self.fixture.git('-C',str(self.fixture.remote),'for-each-ref','--format=%(refname)','refs/heads'),'refs/heads/main')
        self.store.finish(jid,'done',result)
        downloaded=self.fixture.call('download',{'job_id':jid})
        self.assertEqual(set(workflow.resumes.validate_files(downloaded['attachments'])),{'resume.pdf','resume.tex'})

    def test_profile_change_during_compilation_prevents_publication(self):
        jid=self.store.enqueue(self.uid,'tailor',{'url':URL});job=self.store.claim()
        before=self.fixture.git('-C',str(self.fixture.remote),'rev-parse','main');original=worker.run
        def agent(argv,**kwargs):
            return json.dumps(response()) if argv[0]=='openclaw' else original(argv,**kwargs)
        def compiler(task,tex):
            tex.with_suffix('.pdf').write_bytes(workflow.PDF)
            (task/'profile/MASTER_PROFILE.md').write_text('Unexpected change after response parsing')
        with mock.patch.object(worker,'run',side_effect=agent),mock.patch.object(worker,'compile_pdf',side_effect=compiler):
            with self.assertRaisesRegex(ValueError,'Read-only profile changed'):worker.work(job,self.store)
        self.assertEqual(self.fixture.git('-C',str(self.fixture.remote),'rev-parse','main'),before)

    def test_truncated_agent_response_leaves_main_unchanged(self):
        jid=self.store.enqueue(self.uid,'tailor',{'url':URL});job=self.store.claim();before=self.fixture.git('-C',str(self.fixture.remote),'rev-parse','main')
        original=worker.run
        def agent(argv,**kwargs):
            if argv[0]=='openclaw':return json.dumps({'payloads':[{'text':'```cvpilot-resume-tex\n'+TEX}]})
            return original(argv,**kwargs)
        with mock.patch.object(worker,'run',side_effect=agent),mock.patch.object(worker,'compile_pdf') as compiler:
            with self.assertRaisesRegex(RuntimeError,'incomplete output blocks'):worker.work(job,self.store)
        compiler.assert_not_called()
        self.assertEqual(self.fixture.git('-C',str(self.fixture.remote),'rev-parse','main'),before)


if __name__=='__main__':unittest.main()
