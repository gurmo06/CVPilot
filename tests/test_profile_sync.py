"""Profile sync must write evidence, not report success after tool/refusal failures."""
import json
from pathlib import Path
import sys
import unittest
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'controller'))
import profile_sync
import worker
import test_resume_attachments as fixtures

UID=fixtures.UID


class ProfileSyncTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.ResumeWorkflowTests('runTest');self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.store=self.fixture.store;self.root=self.fixture.root
        uploaded,_=self.fixture.upload();worker.cleanup_completed(UID,uploaded['id'])

    def job(self):
        self.store.enqueue(UID,'profile-sync',{});return self.store.claim()

    def write_profile(self,task,*,manifest=None,facts=None):
        manifest=manifest if manifest is not None else profile_sync.source_manifest(task)
        path='master-resumes/general/resume.tex'
        facts=facts if facts is not None else {'teaching':{'text':'Verified teaching experience','sources':[{'path':path,'sha256':manifest[path]}]}}
        directory=task/'profile';directory.mkdir(exist_ok=True)
        (directory/'MASTER_PROFILE.md').write_text('# Profile\n\nVerified teaching experience\n')
        (directory/'profile_sources.json').write_text(json.dumps({'schema_version':1,'facts':facts,'source_manifest':manifest},indent=2))

    def execute(self,job,write=None,raw=None):
        original=worker.run
        def agent(argv,**kwargs):
            if argv[0]=='openclaw':
                self.assertIn('--json',argv)
                self.assertIn('--message-file',argv)
                self.assertIn(f'agent:career-{UID}:cvpilot:job:{job["id"]}',argv)
                task=worker.agent_taskdir(UID,job['id'])
                provided=json.loads((task/'.cvpilot-sync-inputs.json').read_text())['source_manifest']
                self.assertEqual(provided,profile_sync.source_manifest(task))
                if raw is not None:return raw
                if write:
                    write(task)
                    value={'status':'updated','profile_markdown':(task/'profile/MASTER_PROFILE.md').read_text(),
                           'profile_sources':json.loads((task/'profile/profile_sources.json').read_text())}
                    # Simulate a model response, not model filesystem writes.
                    (task/'profile/MASTER_PROFILE.md').unlink();(task/'profile/profile_sources.json').unlink()
                else:value={'status':'unchanged'}
                return json.dumps({'status':'ok','result':{'payloads':[{'text':json.dumps(value)}]}})
            return original(argv,**kwargs)
        with mock.patch.object(worker,'run',side_effect=agent):return worker.work(job,self.store)

    def finish(self,job,result):
        self.store.finish(job['id'],'done',result);worker.cleanup_completed(UID,job['id'])

    def test_first_sync_commits_populated_profile_and_exact_source_hashes(self):
        job=self.job();before=self.fixture.git('-C',str(self.fixture.remote),'rev-parse','main')
        result=self.execute(job,self.write_profile);self.finish(job,result)
        self.assertTrue(result['changed']);self.assertNotEqual(result['commit'],before)
        self.assertIn('Verified teaching experience',self.fixture.git('-C',str(self.fixture.remote),'show','main:profile/MASTER_PROFILE.md'))
        self.assertEqual(len(json.loads(self.fixture.git('-C',str(self.fixture.remote),'show','main:profile/profile_sources.json'))['source_manifest']),2)
        self.assertEqual(self.fixture.git('-C',str(self.fixture.remote),'ls-tree','main','--','.cvpilot-sync-inputs.json'),'')
        self.assertTrue((worker.ROOT/'job-reports'/UID/f'{job["id"]}.json').is_file())

    def test_genuine_noop_keeps_commit_and_links_main_with_explicit_message(self):
        first=self.job();result=self.execute(first,self.write_profile);self.finish(first,result)
        second=self.job();same=self.execute(second);self.finish(second,same)
        self.assertFalse(same['changed']);self.assertEqual(same['commit'],result['commit'])
        self.assertTrue(same['url'].endswith('/tree/main'))
        self.assertIn('No new commit was created',same['note'])

    def test_refusal_or_false_done_without_output_cannot_be_a_successful_noop(self):
        job=self.job();before=self.fixture.git('-C',str(self.fixture.remote),'rev-parse','main')
        refused=json.dumps({'status':'ok','result':{'payloads':[{'text':'I cannot access the source files; please send them again.'}]}})
        with self.assertRaisesRegex(ValueError,'incomplete or invalid JSON'):self.execute(job,raw=refused)
        self.assertEqual(self.fixture.git('-C',str(self.fixture.remote),'rev-parse','main'),before)
        self.assertNotIn('draft',json.loads((worker.ROOT/'publications'/UID/f'{job["id"]}.json').read_text()))

    def test_stale_manifest_missing_facts_and_false_confirmation_are_rejected(self):
        for case in ('manifest','facts','confirmation','source_ref'):
            with self.subTest(case=case):
                task,_=worker.new_worktree(UID,100+len(case),'profile-sync')
                expected=profile_sync.prepare(task,'a'*40);self.write_profile(task)
                file=task/'profile/profile_sources.json';metadata=json.loads(file.read_text())
                if case=='manifest':metadata['source_manifest']={}
                if case=='facts':metadata['facts']={}
                if case=='confirmation':metadata['facts']['teaching']['user_confirmed']=True
                if case=='source_ref':metadata['facts']['teaching']['sources'][0]['sha256']='0'*64
                file.write_text(json.dumps(metadata))
                with self.assertRaises(ValueError):profile_sync.validate(task,expected,worker.KIT/'repo-template/profile/MASTER_PROFILE.md')

    def test_blank_profile_and_master_edits_are_rejected(self):
        task,_=worker.new_worktree(UID,210,'profile-sync');expected=profile_sync.prepare(task,'a'*40)
        self.write_profile(task)
        template=worker.KIT/'repo-template/profile/MASTER_PROFILE.md'
        (task/'profile/MASTER_PROFILE.md').write_text(template.read_text())
        with self.assertRaisesRegex(ValueError,'starter template'):profile_sync.validate(task,expected,template)
        self.write_profile(task);(task/'master-resumes/general/resume.tex').write_text('Changed source')
        with self.assertRaisesRegex(ValueError,'changed source resumes'):profile_sync.validate(task,expected,template)

    def test_existing_user_confirmed_facts_must_be_preserved(self):
        task,_=worker.new_worktree(UID,220,'profile-sync');expected=profile_sync.prepare(task,'a'*40)
        self.write_profile(task);confirmed={'confirmed':{'text':'User supplied fact','user_confirmed':True}}
        file=task/'profile/profile_sources.json';metadata=json.loads(file.read_text());metadata['facts'].update(confirmed);file.write_text(json.dumps(metadata))
        profile_sync.validate(task,expected,worker.KIT/'repo-template/profile/MASTER_PROFILE.md',confirmed)
        metadata['facts'].pop('confirmed');file.write_text(json.dumps(metadata))
        with self.assertRaisesRegex(ValueError,'preserve user-confirmed'):profile_sync.validate(task,expected,worker.KIT/'repo-template/profile/MASTER_PROFILE.md',confirmed)

    def test_prompt_supplies_text_and_hashes_without_filesystem_dependence(self):
        task,_=worker.new_worktree(UID,42,'profile-sync');manifest=profile_sync.prepare(task,'a'*40)
        prompt=worker.agent_prompt({'discord_id':UID,'id':42,'kind':'profile-sync','payload':'{}'},task)
        self.assertIn('Verified teaching experience',prompt)
        self.assertIn(manifest['master-resumes/general/resume.tex'],prompt)
        self.assertIn('DO NOT use filesystem tools',prompt)

    def test_false_unchanged_with_empty_profile_is_rejected(self):
        job=self.job()
        with self.assertRaisesRegex(ValueError,'did not produce'):self.execute(job)

    def test_pdf_only_inputs_are_extracted_and_matching_pdf_tex_pairs_prefer_text(self):
        task,_=worker.new_worktree(UID,230,'profile-sync')
        extract=mock.Mock(return_value='Verified PDF-only experience')
        profile_sync.prepare(task,'a'*40,extract);extract.assert_not_called()
        (task/'master-resumes/general/resume.tex').unlink()
        profile_sync.prepare(task,'a'*40,extract);extract.assert_called_once()
        pack=json.loads((task/'.cvpilot-sync-inputs.json').read_text())
        self.assertEqual(pack['documents'][0]['text'],'Verified PDF-only experience')

    def test_failed_extraction_can_publish_its_retained_response_without_another_agent_run(self):
        import main_commits
        job=self.job();task,_=worker.new_worktree(UID,job['id'],'profile-sync')
        main_commits.begin(worker,job,self.store);profile_sync.prepare(task,'a'*40)
        staged=worker.stage_agent_task(UID,job['id'],task);self.write_profile(staged)
        value={'status':'updated','profile_markdown':(staged/'profile/MASTER_PROFILE.md').read_text()+r'F\*',
               'profile_sources':json.loads((staged/'profile/profile_sources.json').read_text())}
        text=json.dumps(value).replace('F\\\\*','F\\*')
        worker._write_intent(worker.ROOT/'job-reports'/UID/f'{job["id"]}.json',{'payloads':[{'text':text}]})
        self.store.finish(job['id'],'failed',error='Old parser rejected escaped Markdown')
        original=worker.run
        def no_model(argv,**kwargs):
            if argv[0]=='openclaw':raise AssertionError('Recovery must not rerun the model')
            return original(argv,**kwargs)
        with mock.patch.object(worker,'run',side_effect=no_model):
            result=worker.run_resume_job(job,self.store,reuse_saved_response=True)
        self.finish(job,result)
        self.assertTrue(result['changed']);self.assertEqual(self.store.job(job['id'])['state'],'done')
        self.assertIn(r'F\*',self.fixture.git('-C',str(self.fixture.remote),'show','main:profile/MASTER_PROFILE.md'))
        with self.assertRaisesRegex(ValueError,'failed profile-sync'):
            worker.run_resume_job(job,self.store,reuse_saved_response=True)

    def test_saved_response_recovery_refuses_nonfailed_jobs(self):
        job=self.job()
        with self.assertRaisesRegex(ValueError,'failed profile-sync'):
            worker.run_resume_job(job,self.store,reuse_saved_response=True)


class AgentCompletionTests(unittest.TestCase):
    def test_failed_payload_or_envelope_is_not_completion(self):
        cases=['',json.dumps({}),json.dumps({'status':'error','error':'Tool failed'}),
               json.dumps({'status':'ok','result':{'payloads':[{'text':'Cannot access files','isError':True}]}}),
               json.dumps({'status':'ok','result':{'meta':{'aborted':True}}})]
        for raw in cases:
            with self.subTest(raw=raw),self.assertRaises(RuntimeError):profile_sync.agent_response(raw)

    def test_gateway_and_direct_completion_envelopes_are_supported(self):
        for value in ({'status':'ok','result':{'payloads':[{'text':'Done'}]}},{'payloads':[{'text':'Done'}],'meta':{}}):
            self.assertEqual(profile_sync.agent_response(json.dumps(value))['payloads'][0]['text'],'Done')


class ProfileJsonEscapeTests(unittest.TestCase):
    def apply(self,text):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            task=Path(directory)
            profile_sync.apply_response(task,{'payloads':[{'text':text}]})
            return (task/'profile/MASTER_PROFILE.md').read_text(),json.loads((task/'profile/profile_sources.json').read_text())

    def test_markdown_escapes_inside_fenced_json_preserve_literal_characters(self):
        text='```json\n'+r'{"status":"updated","profile_markdown":"F\* and A\_B, \(x\), 50\%","profile_sources":{"facts":{}}}'+'\n```'
        md,_=self.apply(text)
        self.assertEqual(md,r'F\* and A\_B, \(x\), 50\%')

    def test_repair_does_not_change_valid_escapes_escaped_backslashes_or_unicode(self):
        expected='Line one\n"quoted"\\q\t'+'\u03b1'+r' F\*'
        encoded=json.dumps({'status':'updated','profile_markdown':expected,'profile_sources':{}})
        invalid=encoded.replace('F\\\\*','F\\*')
        self.assertNotEqual(invalid,encoded)
        md,_=self.apply(invalid)
        self.assertEqual(md,expected)
        self.assertEqual(self.apply(encoded)[0],expected)

    def test_tex_command_starting_with_u_is_preserved_when_not_a_unicode_escape(self):
        md,_=self.apply(r'{"status":"updated","profile_markdown":"Use \unknownmacro safely","profile_sources":{}}')
        self.assertEqual(md,r'Use \unknownmacro safely')

    def test_truncated_or_other_malformed_json_is_still_rejected(self):
        for text in (r'{"status":"updated","profile_markdown":"F\*"',
                     r'{"status":"updated","profile_markdown":"F\*",}',
                     'Not a profile at all'):
            with self.subTest(text=text),self.assertRaisesRegex(ValueError,'incomplete or invalid JSON'):
                self.apply(text)


if __name__=='__main__':unittest.main()
