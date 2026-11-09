"""Detailed extraction preserves evidence and records; publication stays transactional."""
import json,tempfile,unittest,sys
from pathlib import Path
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'controller'))
import profile_sync,worker
import test_profile_sync as syncfixtures
UID=syncfixtures.UID


def response(facts):return {'payloads':[{'text':json.dumps({'facts':facts})}]}


class DetailedProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.task=self.root/'task';(self.task/'master-resumes/general').mkdir(parents=True)
        self.text='Project Meridian: designed an accessible scheduling workflow, interviewed 12 clients, and reduced missed appointments by 18%.\n'
        (self.task/'master-resumes/general/resume.tex').write_text(self.text)
        (self.task/'profile').mkdir()
        self.prior={'schema_version':1,'source_manifest':{},'facts':{
            'confirmed':{'text':'I trained staff in UNIX shell workflows for two years.','user_confirmed':True},
            'historical':{'text':'Coordinated an older community project with 30 volunteers.','sources':[{'path':'old-source.pdf','sha256':'a'*64}]}}}
        (self.task/'profile/profile_sources.json').write_text(json.dumps(self.prior))
        (self.task/'profile/MASTER_PROFILE.md').write_text('# Existing detailed profile\nContext not shortened.\n')
        self.job={'id':99,'discord_id':UID,'kind':'profile-sync','payload':'{}'}
        self.patch=mock.patch.object(worker,'ROOT',self.root/'state');self.patch.start();self.addCleanup(self.patch.stop)
        profile_sync.prepare(self.task,'b'*40,max_text=None)
    def fact(self):return {'category':'Projects','subject':'Project Meridian','text':self.text.strip(),'evidence':self.text.strip()}
    def run_sync(self,output=None):
        with mock.patch.object(worker,'agent_completion',return_value=output or response([self.fact()])) as model:
            count=profile_sync.sync_in_depth(worker,self.job,self.task)
        return count,model
    def test_details_evidence_and_all_prior_records_preserved(self):
        count,model=self.run_sync();self.assertEqual(count,3)
        metadata=json.loads((self.task/'profile/profile_sources.json').read_text())
        for key,fact in self.prior['facts'].items():self.assertEqual(metadata['facts'][key],fact)
        self.assertIn(self.text.strip(),(self.task/'profile/MASTER_PROFILE.md').read_text())
        self.assertEqual(next(iter(metadata['source_texts'].values()))['text'],self.text)
        manifest=profile_sync.source_manifest(self.task)
        self.assertEqual(profile_sync.validate(self.task,manifest,worker.KIT/'repo-template/profile/MASTER_PROFILE.md',{'confirmed':self.prior['facts']['confirmed']}),3)
        self.assertIn('not a resume-writing',model.call_args.args[1])
    def test_invented_evidence_or_confirmation_is_rejected_before_writing(self):
        original=(self.task/'profile/profile_sources.json').read_bytes()
        for record in (self.fact()|{'evidence':'invented quote'},self.fact()|{'user_confirmed':True}):
            with self.assertRaisesRegex(ValueError,'evidence'):self.run_sync(response([record]))
            self.assertEqual((self.task/'profile/profile_sources.json').read_bytes(),original)
    def test_incomplete_json_preserves_profile(self):
        original=(self.task/'profile/MASTER_PROFILE.md').read_text()
        with self.assertRaisesRegex(ValueError,'invalid JSON'):self.run_sync({'payloads':[{'text':'{"facts":['}]})
        self.assertEqual((self.task/'profile/MASTER_PROFILE.md').read_text(),original)
    def test_current_detailed_profile_does_not_call_model_again(self):
        self.run_sync();profile_sync.prepare(self.task,'c'*40,max_text=None)
        _,model=self.run_sync();model.assert_not_called()
    def test_large_input_chunks_are_bounded_and_cover_every_character(self):
        text=('A substantive record with responsibilities and results.\n'*2000)
        parts=list(profile_sync.chunks(text));self.assertGreater(len(parts),10)
        self.assertTrue(all(len(part)<=10000 for part in parts))
        # The overlap permits facts spanning a chunk boundary to be extracted.
        for line in text.splitlines():self.assertTrue(any(line in part for part in parts))
        (self.task/'master-resumes/general/resume.tex').write_text(text)
        profile_sync.prepare(self.task,'d'*40,max_text=None)
        pack=json.loads((self.task/'.cvpilot-sync-inputs.json').read_text());self.assertEqual(pack['documents'][0]['text'],text)


class DeepPublicationTests(unittest.TestCase):
    def test_real_git_publication_and_second_sync_noop(self):
        fixture=syncfixtures.ProfileSyncTests('runTest');fixture.setUp();self.addCleanup(fixture.doCleanups)
        job=fixture.job();original=worker.agent_completion
        def extract(job,prompt,path,**kwargs):
            self.assertIn('EXCERPT:',prompt)
            excerpt=prompt.split('EXCERPT:\n',1)[1]
            result=response([{'category':'Experience','subject':'Teaching','text':excerpt.strip(),'evidence':excerpt.strip()}])
            worker._write_intent(path,result);return result
        with mock.patch.object(worker,'agent_completion',side_effect=extract):
            result=worker.work(job,fixture.store)
        self.assertTrue(result['changed']);fixture.finish(job,result)
        metadata=json.loads(fixture.fixture.git('-C',str(fixture.fixture.remote),'show','main:profile/profile_sources.json'))
        self.assertEqual(metadata['profile_depth'],'detailed-records-v1');self.assertTrue(metadata['source_texts'])
        second=fixture.job()
        with mock.patch.object(worker,'agent_completion') as model:
            unchanged=worker.work(second,fixture.store);model.assert_not_called()
        self.assertFalse(unchanged['changed'])


if __name__=='__main__':unittest.main()

class LargeProfileProjectionTests(unittest.TestCase):
    def test_retrieval_keeps_relevant_full_records_and_layout_without_changing_database(self):
        import tailoring,copy
        facts={f'f{i}':{'text':('Other extensive career detail. '*30),'sources':[]} for i in range(200)}
        facts['unix']={'text':'Used UNIX shell pipelines to validate hospital appointment records.','user_confirmed':True}
        layout='\\documentclass{article}\n\\begin{document}Original layout\\end{document}'
        pack={'current_profile':'Long full master profile. '*10000,'current_profile_sources':{'facts':facts},
              'source_manifest':{'master-resumes/a/resume.tex':'a'*64},
              'documents':[{'path':'master-resumes/a/resume.tex','text':layout},
                           {'path':'master-resumes/b/resume.tex','text':'Other source material. '*10000}]}
        original=copy.deepcopy(pack);tailoring.project_evidence(pack,'UNIX shell hospital appointment records')
        self.assertIn('unix',pack['current_profile_sources']['facts'])
        self.assertEqual(pack['current_profile_sources']['facts']['unix'],original['current_profile_sources']['facts']['unix'])
        self.assertEqual(pack['source_manifest'],original['source_manifest'])
        self.assertEqual(pack['documents'][0]['text'],layout)
        self.assertLess(len(json.dumps(pack)),160000)
        self.assertEqual(len(original['current_profile_sources']['facts']),201)
        self.assertIn('not a shortened database',pack['retrieval']['instruction'])
