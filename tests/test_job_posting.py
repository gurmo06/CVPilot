"""Exact Workday extraction and network boundaries with mocked provider responses."""
import copy
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest import mock
import test_controller as fixtures
from store import Store
import job_posting
import main_commits
import worker

URL='https://example.wd1.myworkdayjobs.com/en-US/External/job/City/Example-Role_REQ123?source=tracking'
DESCRIPTION='<p>Example role working with verified industry skills.</p><ul><li>Relevant experience and qualifications.</li></ul>'*5


class JobPostingTests(unittest.TestCase):
    def response(self):
        return {'jobPostingInfo':{'title':'Example Role','jobPostingId':'Example-Role_REQ123','jobReqId':'REQ123','jobDescription':DESCRIPTION}}

    def test_endpoint_uses_exact_tenant_site_and_job_and_drops_tracking(self):
        host,path,external=job_posting.endpoint(URL)
        self.assertEqual(host,'example.wd1.myworkdayjobs.com')
        self.assertEqual(path,'/wday/cxs/example/External/job/City/Example-Role_REQ123')
        self.assertEqual(external,'/job/City/Example-Role_REQ123')
        self.assertIsNotNone(job_posting.endpoint(URL.replace('/en-US','')))
        self.assertIsNotNone(job_posting.endpoint(URL.replace('/City','')))

    def test_other_providers_are_left_to_existing_agent_fetching(self):
        with mock.patch.object(job_posting,'request_json') as request:
            for url in ('https://jobs.example/job','https://example.wd1.myworkdayjobs.com.evil.test/job'):
                self.assertIsNone(job_posting.fetch(url))
        request.assert_not_called()

    def test_credentials_ports_traversal_and_encoded_separators_are_rejected(self):
        for url in (URL.replace('https:','http:'),URL.replace('https://','https://user:pass@'),
                    URL.replace('.com/','.com:8443/'),URL.replace('/City/','/../'),
                    URL.replace('/City/','/%2e%2e/'),URL.replace('/City/','/City%2fElsewhere/'),
                    URL.replace('/City/','/%252e%252e/'),URL.replace('/job/','/search/')):
            with self.subTest(url=url),self.assertRaises(ValueError):job_posting.endpoint(url)

    def test_matching_posting_id_or_canonical_path_is_required(self):
        value=self.response()
        with mock.patch.object(job_posting,'request_json',return_value=value):posting=job_posting.fetch(URL)
        self.assertEqual(posting['title'],'Example Role');self.assertIn('Relevant experience',posting['text'])
        self.assertNotIn('<p>',posting['text']);self.assertNotIn('tracking',posting['source_url'])
        value['jobPostingInfo'].pop('jobPostingId');value['jobPostingInfo']['externalPath']='/job/City/Example-Role_REQ123'
        with mock.patch.object(job_posting,'request_json',return_value=value):self.assertIsNotNone(job_posting.fetch(URL))
        value['jobPostingInfo']['externalPath']='/job/Different_REQ123'
        with mock.patch.object(job_posting,'request_json',return_value=value),self.assertRaisesRegex(RuntimeError,'different posting'):job_posting.fetch(URL)

    def test_conflicting_requisition_and_incomplete_descriptions_fail_before_agent(self):
        for field,value in (('jobReqId','REQ999'),('jobDescription',''),('jobDescription','x'*40001),('title',None)):
            response=self.response();response['jobPostingInfo'][field]=value
            with self.subTest(field=field),mock.patch.object(job_posting,'request_json',return_value=response),self.assertRaises(RuntimeError):job_posting.fetch(URL)

    def test_description_preserves_list_text_and_removes_script_style(self):
        response=self.response();response['jobPostingInfo']['jobDescription']+='<script>BAD SCRIPT</script><style>BAD STYLE</style><p>A &amp; B</p>'
        with mock.patch.object(job_posting,'request_json',return_value=response):posting=job_posting.fetch(URL)
        self.assertIn('A & B',posting['text']);self.assertNotIn('BAD SCRIPT',posting['text']);self.assertNotIn('BAD STYLE',posting['text'])

    def test_public_address_check_precedes_connection(self):
        records=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))]
        with mock.patch.object(job_posting.socket,'getaddrinfo',return_value=records),mock.patch.object(job_posting.http.client,'HTTPSConnection') as connection:
            with self.assertRaisesRegex(ValueError,'public address'):job_posting.request_json('example.wd1.myworkdayjobs.com','/wday/cxs/example/External/job/x')
        connection.assert_not_called()

    def test_tls_uses_original_host_with_pinned_public_ip_and_bounded_no_redirect_request(self):
        records=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('8.8.8.8',443))]
        response=mock.Mock(status=200);response.getheader.return_value='application/json';response.read.return_value=b'{}'
        context=mock.Mock();connection=mock.Mock();connection.getresponse.return_value=response
        with (mock.patch.object(job_posting.socket,'getaddrinfo',return_value=records),
              mock.patch.object(job_posting.socket,'create_connection',return_value=mock.sentinel.socket) as connect,
              mock.patch.object(job_posting.ssl,'create_default_context',return_value=context),
              mock.patch.object(job_posting.http.client,'HTTPSConnection',return_value=connection)):
            self.assertEqual(job_posting.request_json('example.wd1.myworkdayjobs.com','/exact/path'),{})
            connect.assert_called_once_with(('8.8.8.8',443),timeout=15)
            context.wrap_socket.assert_called_once_with(mock.sentinel.socket,server_hostname='example.wd1.myworkdayjobs.com')
            response.read.assert_called_once_with(job_posting.MAX_BYTES+1)
            self.assertEqual(connection.request.call_args.args,('GET','/exact/path'))
            headers=connection.request.call_args.kwargs['headers'];self.assertNotIn('Authorization',headers)
            for status in (301,302,403,404):
                response.status=status
                with self.assertRaisesRegex(ValueError,'HTTP '+str(status)):job_posting.request_json('example.wd1.myworkdayjobs.com','/exact/path')

    def test_supplied_posting_prompt_uses_exact_text_without_refetch(self):
        job={'discord_id':fixtures.UID,'id':32,'kind':'tailor','payload':json.dumps({'url':URL})}
        with mock.patch.object(job_posting,'request_json',return_value=self.response()):posting=job_posting.fetch(URL)
        prompt=worker.agent_prompt(job,posting,{'documents':[{'path':'master-resumes/general/resume.tex','text':'Verified source'}]})
        self.assertIn('Do not web_fetch or web_search',prompt);self.assertIn(posting['text'].splitlines()[0],prompt)
        self.assertIn('untrusted factual evidence',prompt);self.assertIn('EXACT POSTING DATA',prompt)
        self.assertIn('Match the posting\'s important role-specific keywords',prompt)

    def test_fetch_failure_does_not_run_model_or_publish(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);store=Store(root/'jobs.sqlite');uid=fixtures.UID
            with store.tx() as c:c.execute('INSERT INTO users(discord_id,repo) VALUES(?,?)',(uid,'CV-Pilot/example'))
            jid=store.enqueue(uid,'tailor',{'url':URL});job=store.job(jid)
            task=root/'task';(task/'master-resumes/general').mkdir(parents=True)
            (task/'master-resumes/general/resume.tex').write_text('Source')
            with (mock.patch.object(worker,'ROOT',root),mock.patch.object(worker,'new_worktree',return_value=(task,'main')),
                  mock.patch.object(worker,'stage_agent_task',return_value=task),mock.patch.object(main_commits,'recover',return_value=None),
                  mock.patch.object(main_commits,'begin',return_value={'base':'a'*40}),mock.patch.object(main_commits,'publish') as publish,
                  mock.patch.object(worker,'run_git',return_value='a'*40),
                  mock.patch.object(job_posting,'fetch',side_effect=RuntimeError('Posting unavailable')),
                  mock.patch.object(worker,'run') as agent):
                with self.assertRaisesRegex(RuntimeError,'Posting unavailable'):worker.run_resume_job(job,store)
            agent.assert_not_called();publish.assert_not_called()


if __name__=='__main__':unittest.main()
