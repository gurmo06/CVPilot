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

TEX=r'\documentclass{article}\begin{document}Verified teaching experience\end{document}'
URL='https://jobs.example/teaching'


def response(tex=TEX,notes='Teaching experience and supported classroom keywords.',profile=None):
    profile=profile or {'status':'unchanged'}
    return {'payloads':[{'text':f'```cvpilot-resume-tex\n{tex}\n```\n```cvpilot-notes\n{notes}\n```\n```cvpilot-profile-json\n'+json.dumps(profile)+'\n```'}]}


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
        return tailoring.apply(self.task,report or response(),33,URL,pack or self.pack,self.template)

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
        prompt=worker.agent_prompt(job,self.task,{'text':'Exact public teaching posting'},self.pack)
        self.assertIn('DO NOT use ls, read, write, edit, exec',prompt)
        self.assertIn('Verified teaching experience',prompt);self.assertIn('source_manifest',prompt)
        self.assertIn('current_profile_sources',prompt);self.assertIn('cvpilot-resume-tex',prompt)
        self.assertNotIn('ONLY work inside',prompt);self.assertNotIn('/career/tasks',prompt)
        self.assertIn('ATS-friendly',prompt);self.assertIn('any profession',prompt)
        with self.assertRaisesRegex(ValueError,'readable resume text'):tailoring.prompt(job,None,None)

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
        invalid=[original.rsplit('```',1)[0],original.replace('```cvpilot-notes','```wrong-notes'),
                 original+'\n```cvpilot-resume-tex\n'+TEX+'\n```',response(notes='x'*16001)['payloads'][0]['text'],
                 response(tex='x'*250001)['payloads'][0]['text']]
        for text in invalid:
            with self.subTest(size=len(text)),self.assertRaises(RuntimeError):self.apply({'payloads':[{'text':text}]})
            self.assertFalse(self.folder.exists())

    def test_incomplete_tex_or_invalid_profile_cannot_be_published(self):
        for report in (response(tex=TEX.replace(r'\end{document}','')),
                       response(profile={'status':'updated','profile_markdown':'Invented profile','profile_sources':{}}),
                       response(tex=TEX+'\x08')):
            with self.subTest(report=str(report)[:40]),self.assertRaises((RuntimeError,ValueError)):self.apply(report)
            self.assertFalse(self.folder.exists())

    def test_new_profile_uses_source_hashes_and_preserves_confirmations(self):
        prior={'text':'User-confirmed teaching credential','user_confirmed':True}
        pack=copy.deepcopy(self.pack);pack['current_profile_sources']['facts']['confirmed']=prior
        result=updated(pack)
        with self.assertRaisesRegex(ValueError,'user-confirmed'):self.apply(response(profile=result),pack)
        result['profile_sources']['facts']['confirmed']=prior
        self.assertTrue(self.apply(response(profile=result),pack))
        saved=json.loads((self.task/'profile/profile_sources.json').read_text())
        self.assertEqual(saved['facts']['confirmed'],prior)

    def test_unchanged_decision_is_rejected_when_a_source_was_replaced(self):
        (self.task/'master-resumes/teaching/resume.tex').write_text(TEX+'\nNew source fact')
        with self.assertRaisesRegex(ValueError,'changed source resumes'):self.apply()
        self.assertFalse(self.folder.exists())

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


class TailoringWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.fixture=workflow.ResumeWorkflowTests('runTest');self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.store=self.fixture.store;self.uid=workflow.UID
        job,_=self.fixture.upload(role='teaching');worker.cleanup_completed(self.uid,job['id'])

    def test_one_model_text_response_is_compiled_and_published_with_both_files(self):
        jid=self.store.enqueue(self.uid,'tailor',{'url':URL});job=self.store.claim();original=worker.run;calls=[]
        def agent(argv,**kwargs):
            if argv[0]!='openclaw':return original(argv,**kwargs)
            calls.append(argv)
            prompt=Path(argv[argv.index('--message-file')+1]).read_text()
            self.assertIn('Verified teaching experience',prompt)
            self.assertIn('DO NOT use ls, read, write, edit, exec',prompt)
            # No model writes or reads the sandbox task. The worker owns output.
            canonical=worker.taskdir(self.uid,jid)
            pack=json.loads((canonical/'.cvpilot-sync-inputs.json').read_text())
            return json.dumps(response(profile=updated(pack)))
        def compile_pdf(task,tex):
            self.assertIn('Verified teaching experience',tex.read_text())
            tex.with_suffix('.pdf').write_bytes(workflow.PDF)
        with mock.patch.object(worker,'run',side_effect=agent),mock.patch.object(worker,'compile_pdf',side_effect=compile_pdf):
            result=worker.work(job,self.store)
        self.assertEqual(len(calls),1);self.assertTrue(result['on_main']);self.assertTrue(result['changed'])
        self.assertEqual(len(result['artifacts']),2)
        folder=f'applications/{time.gmtime().tm_year}/job-{jid}'
        for name in ('resume.pdf','resume.tex','job-url.txt','notes.md'):
            self.assertTrue(self.fixture.git('-C',str(self.fixture.remote),'ls-tree','main','--',folder+'/'+name))
        self.assertEqual(self.fixture.git('-C',str(self.fixture.remote),'show','main:master-resumes/teaching/resume.tex'),workflow.TEX.decode())
        self.assertFalse(self.fixture.git('-C',str(self.fixture.remote),'ls-tree','main','--','.cvpilot-sync-inputs.json'))
        self.assertEqual(self.fixture.git('-C',str(self.fixture.remote),'for-each-ref','--format=%(refname)','refs/heads'),'refs/heads/main')

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
