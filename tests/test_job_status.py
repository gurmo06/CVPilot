"""Detailed single-job diagnostics use temporary state, never real jobs or reports."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import test_controller as fixtures
import dispatch
import job_status
import main_commits
from store import Store
import worker

UID=fixtures.UID;OTHER=fixtures.UID2


class JobStatusTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.store=Store(self.root/'jobs.sqlite')

    def call(self,**args):
        return dispatch.process({'sender_id':UID,'action':'status','args':args},self.store)

    def report(self,jid,data):
        path=self.root/'job-reports'/UID/f'{jid}.json';path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(data));return path

    def failed(self,error='Agent did not create applications/2026/job-1/resume.tex'):
        jid=self.store.enqueue(UID,'tailor',{'url':'https://jobs.example/listing?secret=hidden#fragment'})
        self.store.claim();self.store.finish(jid,'failed',error=error);return jid

    def test_missing_output_exposes_saved_blocker_without_disclosing_agent_configuration(self):
        jid=self.failed()
        self.report(jid,{'payloads':[{'text':'Unable to fetch the job posting; please provide another URL.'}],
            'meta':{'durationMs':7110,'agentMeta':{'model':'gpt-oss:20b','sessionId':'PRIVATE SESSION'},
                    'systemPromptReport':{'systemPrompt':'PRIVATE SYSTEM PROMPT'}}})
        result=self.call();detail=result['status_detail'];text=json.dumps(result)
        self.assertEqual(result['status_view']['job_id'],jid)
        self.assertIn('Unable to fetch the job posting',text)
        self.assertIn('compilation and publication did not run',text)
        self.assertIn('Agent time: 7s',text)
        for private in ('PRIVATE SESSION','PRIVATE SYSTEM PROMPT','secret=hidden','fragment'):self.assertNotIn(private,text)
        self.assertIn('Agent did not create',detail['report'])

    def test_error_longer_than_old_120_character_limit_is_fully_preserved(self):
        error='xelatex failed:\n'+('Specific source-line detail.\n'*100)+'LAST LINE'
        jid=self.failed(error);result=self.call(job_id=jid)
        self.assertIn(error,result['status_detail']['report'])
        fields=result['status_detail']['fields']
        self.assertIn('LAST LINE',''.join(f['value'] for f in fields if f['name'].startswith('Error')))
        self.assertTrue(all(len(f['value'])<=1024 for f in fields))

    def test_navigation_skips_other_users_without_skipping_old_owned_jobs(self):
        mine=[]
        for _ in range(15):
            mine.append(self.store.enqueue(UID,'profile-sync',{}))
            self.store.enqueue(OTHER,'profile-sync',{})
        page=self.call();self.assertEqual(page['status_view']['count'],15)
        for index,jid in enumerate(reversed(mine),1):
            self.assertEqual(page['status_view']['job_id'],jid)
            self.assertEqual(page['status_view']['ordinal'],index)
            page=self.call(job_id=jid,direction='older')
        self.assertIsNone(page['status_view']['older'])
        page=self.call(job_id=mine[0],direction='newer')
        self.assertEqual(page['status_view']['job_id'],mine[1])
        foreign=mine[0]+1
        for direction in (None,'older','newer'):
            with self.assertRaisesRegex(ValueError,'your account'):self.call(job_id=foreign,direction=direction)

    def test_refresh_same_job_and_newer_navigation_survive_new_arrivals(self):
        first=self.store.enqueue(UID,'profile-sync',{});self.store.claim()
        page=self.call();second=self.store.enqueue(UID,'profile-sync',{})
        self.store.finish(first,'done',{'note':'Finished','changed':False})
        updated=self.call(job_id=first)
        self.assertEqual(updated['status_view']['job_id'],first)
        self.assertEqual(updated['status_view']['newer'],second)
        self.assertIn('done',updated['status_detail']['title'])
        self.assertNotEqual(page['status_detail']['title'],updated['status_detail']['title'])

    def test_invalid_navigation_and_identifiers_are_rejected(self):
        for args in ({'job_id':True},{'job_id':'1'},{'job_id':0},{'direction':'older'},
                     {'job_id':1,'direction':'sideways'}):
            with self.subTest(args=args),self.assertRaises(ValueError):self.call(**args)

    def test_stage_checkpoint_and_duration_are_visible_on_failed_job(self):
        jid=self.store.enqueue(UID,'tailor',{});self.store.claim();job=self.store.job(jid)
        job_status.progress(self.root,job,'Compiling the tailored TeX and validating the one-page PDF')
        self.store.finish(jid,'failed',error='xelatex: Undefined control sequence at line 42')
        result=self.call();text=json.dumps(result)
        self.assertIn('Last checkpoint',text);self.assertIn('Compiling the tailored TeX',text)
        self.assertIn('Run time:',text);self.assertIn('line 42',text)
        path=self.root/'job-progress'/UID/f'{jid}.json'
        self.assertEqual(path.stat().st_mode&0o777,0o600)

    def test_private_report_symlinks_corruption_and_oversize_do_not_break_status(self):
        jid=self.failed();secret=self.root/'secret.json';secret.write_text('{"payloads":[{"text":"MUST NOT READ"}]}')
        path=self.report(jid,{});path.unlink();path.symlink_to(secret)
        self.assertNotIn('MUST NOT READ',json.dumps(self.call()))
        path.unlink();path.write_text('not json');self.assertIn('failed',self.call()['text'])
        path.write_bytes(b'x'*(2*1024*1024+1));self.assertIn('failed',self.call()['text'])

    def test_secrets_host_paths_and_url_credentials_do_not_cross_status_socket(self):
        secret='example-test-token-123456789'
        self.failed('Read /home/someone/.config/private.json failed. Authorization: Bearer abcdefg\n'
                    'token=hidden-value https://user:password@jobs.example/listing?auth=secret#private '+secret)
        with mock.patch.dict(job_status.os.environ,{'CVPILOT_GITHUB_TOKEN':secret}):text=json.dumps(self.call())
        for private in (secret,'abcdefg','hidden-value','user:password','auth=secret','/home/someone'):self.assertNotIn(private,text)

    def test_worst_case_report_remains_readable_beyond_embed_budget(self):
        jid=self.failed('E'*3000)
        explanation='Agent explanation '+('word '*4000)+'END OF EXPLANATION'
        self.report(jid,{'payloads':[{'text':explanation}]})
        page=self.call();detail=page['status_detail']
        self.assertIn('[Agent explanation exceeds 16,000 characters',detail['report'])
        self.assertLess(sum(len(f['name'])+len(f['value']) for f in detail['fields'])+len(detail['title'])+len(detail['description'])+200,6000)
        self.assertLessEqual(len(page['text']),1900)
        self.assertIn('Continued in attached status report',json.dumps(detail['fields']))

    def test_all_counts_and_selected_job_share_a_read_snapshot(self):
        first=self.store.enqueue(OTHER,'tailor',{});mine=self.store.enqueue(UID,'profile-sync',{})
        writer=Store(self.store.path);original=self.store.connection;changed=[]
        class Connection:
            def __init__(self):self.real=original()
            def __getattr__(self,name):return getattr(self.real,name)
            def execute(self,sql,*args):
                cursor=self.real.execute(sql,*args)
                if 'COUNT(*) AS total' in sql and not changed:
                    changed.append(True);writer.finish(first,'done',{})
                return cursor
        with mock.patch.object(self.store,'connection',side_effect=Connection):page=self.call(job_id=mine)
        self.assertEqual(page['queue']['total'],2)
        self.assertIn('Queue position 2 of 2',page['text'])
        self.assertEqual(self.call(job_id=mine)['queue']['total'],1)

    def test_structured_profile_output_is_not_dumped_into_diagnostics(self):
        jid=self.failed('Profile sync invalid JSON')
        self.report(jid,{'payloads':[{'text':'{"profile_sources":"PRIVATE RAW PROFILE"}'}]})
        self.assertNotIn('PRIVATE RAW PROFILE',json.dumps(self.call()))

    def test_missing_output_error_includes_agent_reason_and_cannot_publish(self):
        with self.store.tx() as c:c.execute('INSERT INTO users(discord_id,repo) VALUES(?,?)',(UID,'CV-Pilot/example'))
        jid=self.store.enqueue(UID,'tailor',{'url':'https://jobs.example/listing'});job=self.store.job(jid)
        task=self.root/'task';(task/'master-resumes/general').mkdir(parents=True)
        (task/'master-resumes/general/resume.tex').write_text('Master source')
        response={'payloads':[{'text':'Unable to fetch this Workday posting.'}], 'meta':{}}
        with (mock.patch.object(worker,'ROOT',self.root),
              mock.patch.object(worker,'new_worktree',return_value=(task,'main')),
              mock.patch.object(worker,'stage_agent_task',return_value=task),
              mock.patch.object(worker,'validate_agent_task'),
              mock.patch.object(worker,'run',return_value=json.dumps(response)) as agent,
              mock.patch.object(main_commits,'recover',return_value=None),
              mock.patch.object(main_commits,'begin',return_value={'base':'a'*40}),
              mock.patch.object(main_commits,'publish') as publish,
              mock.patch.object(worker,'compile_pdf') as compile_pdf):
            with self.assertRaisesRegex(RuntimeError,'Unable to fetch this Workday posting') as failure:
                worker.run_resume_job(job,self.store)
            self.assertIn('No files from this job were published',str(failure.exception))
            publish.assert_not_called();compile_pdf.assert_not_called();agent.assert_called_once()
        self.assertIn('Checking required agent output',job_status.private_json(self.root,'job-progress',job)['stage'])


if __name__=='__main__':unittest.main()
