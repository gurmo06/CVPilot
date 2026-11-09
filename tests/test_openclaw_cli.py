"""Supported-runtime launching and genuine CLI errors; no agent or Gateway calls."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock
import test_controller as fixtures
from store import Store
import main_commits
import openclaw_cli
import worker


class OpenClawCLITests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.entry=self.root/'openclaw.mjs';self.entry.write_text('#!/usr/bin/env node\n');self.entry.chmod(0o755)
        self.binary=self.root/'openclaw';self.binary.symlink_to(self.entry)
        self.node=self.root/'node';self.node.write_text('#!/bin/sh\n');self.node.chmod(0o755)
        patch=mock.patch.dict(os.environ,{'CVPILOT_OPENCLAW_BIN':str(self.binary),'CVPILOT_NODE_BIN':str(self.node)})
        patch.start();self.addCleanup(patch.stop)

    def test_npm_link_is_launched_with_supported_explicit_node(self):
        result=subprocess.CompletedProcess([],0,'{"status":"ok"}','')
        with mock.patch.object(worker.subprocess,'run',return_value=result) as run:
            self.assertEqual(worker.run(['openclaw','agent','--json']),'{"status":"ok"}')
        self.assertEqual(run.call_args.args[0],[str(self.node),str(self.entry),'agent','--json'])

    def test_explicit_shell_wrapper_is_preserved(self):
        shell=self.root/'custom-wrapper';shell.write_text('#!/bin/sh\n');shell.chmod(0o755)
        with mock.patch.dict(os.environ,{'CVPILOT_OPENCLAW_BIN':str(shell)}):
            self.assertEqual(openclaw_cli.command(['openclaw','status']),[str(shell),'status'])

    def test_missing_binary_and_runtime_are_clear_errors(self):
        self.entry.unlink()
        with self.assertRaisesRegex(RuntimeError,'CVPILOT_OPENCLAW_BIN'):openclaw_cli.command(['openclaw'])
        self.entry.write_text('');self.entry.chmod(0o755);self.node.unlink()
        with self.assertRaisesRegex(RuntimeError,'CVPILOT_NODE_BIN'):openclaw_cli.command(['openclaw'])

    def test_status_error_stdout_wins_over_runtime_warnings_on_stderr(self):
        response={'status':'error','summary':'Model returned malformed tool arguments',
                  'result':{'payloads':[{'text':'Could not finish the reply','isError':True}],'meta':{'aborted':False}}}
        proc=subprocess.CompletedProcess([],1,json.dumps(response),'ExperimentalWarning: SQLite\nRetrying with /usr/bin/node')
        with mock.patch.object(worker.subprocess,'run',return_value=proc):
            with self.assertRaises(openclaw_cli.OpenClawError) as failure:worker.run(['openclaw','agent','--json'])
        self.assertIn('malformed tool arguments',str(failure.exception));self.assertNotIn('ExperimentalWarning',str(failure.exception))
        self.assertEqual(failure.exception.response,response)

    def test_error_string_dict_nested_meta_and_plain_stderr_are_supported(self):
        for response in ({'ok':False,'error':'Actual failure'},
                         {'status':'error','error':{'message':'Actual failure'}},
                         {'status':'error','result':{'meta':{'error':{'message':'Actual failure'}}}},
                         {'status':'error','result':{'payloads':[{'text':'Actual failure'}]}},
                         {'status':'error','result':{'meta':{'errorMessage':'Actual failure'}}}):
            with self.subTest(response=response):
                error=openclaw_cli.failure(1,['openclaw','agent'],json.dumps(response),'Warning')
                self.assertIn('Actual failure',str(error));self.assertNotIn('Warning',str(error))
        self.assertIn('Socket unavailable',str(openclaw_cli.failure(1,['openclaw','agent'],'','Socket unavailable')))

    def test_non_openclaw_commands_keep_existing_contract(self):
        proc=subprocess.CompletedProcess([],1,'','git diagnostic')
        with mock.patch.object(worker.subprocess,'run',return_value=proc) as run:
            with self.assertRaisesRegex(RuntimeError,'git diagnostic'):worker.run(['git','status'])
        self.assertEqual(run.call_args.args[0],['git','status'])

    def test_failed_json_response_is_retained_privately_without_publication(self):
        uid=fixtures.UID;store=Store(self.root/'jobs.sqlite')
        with store.tx() as c:c.execute('INSERT INTO users(discord_id,repo) VALUES(?,?)',(uid,'CV-Pilot/example'))
        jid=store.enqueue(uid,'tailor',{'url':'https://jobs.example/listing'});job=store.job(jid)
        task=self.root/'task';(task/'master-resumes/general').mkdir(parents=True)
        (task/'master-resumes/general/resume.tex').write_text('Source resume')
        response={'status':'error','result':{'payloads':[{'text':'Actual model failure','isError':True}],'meta':{}}}
        with (mock.patch.object(worker,'ROOT',self.root),mock.patch.object(worker,'new_worktree',return_value=(task,'main')),
              mock.patch.object(worker,'stage_agent_task',return_value=task),mock.patch.object(worker,'validate_agent_task'),
              mock.patch.object(worker,'review_qualifications',return_value=[]),
              mock.patch.object(worker,'run_git',return_value='a'*40),
              mock.patch.object(main_commits,'recover',return_value=None),mock.patch.object(main_commits,'begin',return_value={'base':'a'*40}),
              mock.patch.object(main_commits,'publish') as publish,
              mock.patch.object(worker,'run',side_effect=openclaw_cli.failure(1,['openclaw','agent'],json.dumps(response),'warning'))):
            with self.assertRaisesRegex(RuntimeError,'Actual model failure'):worker.run_resume_job(job,store)
        saved=self.root/'job-reports'/uid/f'{jid}.json';record=json.loads(saved.read_text())
        self.assertEqual(record['status'],'error');self.assertIn('Actual model failure',record['payloads'][0]['text'])
        self.assertEqual(saved.stat().st_mode&0o777,0o600);publish.assert_not_called()
        self.assertEqual(list((self.root/'job-prompts'/uid).iterdir()),[])

    def test_zero_exit_with_failed_response_is_also_retained_and_rejected(self):
        response={'status':'timeout','result':{'meta':{'timedOut':True},'payloads':[]}}
        retained=openclaw_cli.retained_response(response)
        self.assertEqual(retained['status'],'timeout')
        import profile_sync
        with self.assertRaisesRegex(RuntimeError,'did not complete'):profile_sync.agent_response(json.dumps(response))


if __name__=='__main__':unittest.main()
