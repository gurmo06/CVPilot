"""Narrow generated-source cleanup and retained-tailor recovery; no live state."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import test_resume_attachments as fixtures
import generated_tex
import worker
import main_commits

UID=fixtures.UID


class GeneratedTexTests(unittest.TestCase):
    def test_commented_macro_closers_and_json_table_symbols_are_repaired(self):
        source='\n'.join([r'\newcommand{\row}[2]{%',
            r'\begin{tabularx}{\textwidth}{X \u003e{\raggedleft}X}',
            r'#1 \u0026 #2',r'\end{tabularx}%}',r'\begin{document}',
            r'Algorithms \u0026 Complexity',r'\end{document}'])
        text=generated_tex.normalize(source)
        self.assertIn(r'\begin{tabularx}{\textwidth}{X >{\raggedleft}X}',text)
        self.assertIn('#1 & #2',text);self.assertIn(r'\end{tabularx}}%',text)
        self.assertIn(r'Algorithms \& Complexity',text)
        self.assertEqual(generated_tex.normalize(text),text)

    def test_literal_json_newlines_are_distinct_from_real_tex_commands(self):
        source=r'\newcommand{\example}{text}'+ '\n'+r'One\n  Two\n\textbf{Three} \newline \nu'
        fixed=generated_tex.normalize(source)
        self.assertIn(r'\newcommand{\example}{text}',fixed)
        self.assertIn('One\n  Two\n'+r'\textbf{Three}',fixed)
        self.assertIn(r'\newline \nu',fixed)

    def test_deliberately_defined_n_command_is_preserved(self):
        source=r'\newcommand{\n}{N}'+ '\n'+r'\n text'
        self.assertEqual(generated_tex.normalize(source),source)

    def test_header_row_break_with_length_does_not_open_display_math(self):
        source=r'{Name}\[-0.1pt]'+'\n'+r'\[x^2\]'+'\n'+r'{Already valid}\\[2pt]'+'\n'
        expected=r'{Name}\\[-0.1pt]'+'\n'+r'\[x^2\]'+'\n'+r'{Already valid}\\[2pt]'+'\n'
        self.assertEqual(generated_tex.normalize(source),expected)

    def test_readable_hyphens_and_percentages_preserve_whole_resume_claims(self):
        text=r'\newcommand{\sample}[1]{#1%'+ '\n'+r'}\begin{document}'+ '\n'+'Self\u2011hosted work improved results by 50% while keeping 200%+ growth. '+r'Already 30\% and https://example.invalid/20%20encoded.'
        fixed=generated_tex.readable_text(text)
        self.assertIn('Self-hosted work',fixed)
        self.assertIn(r'50\% while keeping 200\%+ growth.',fixed)
        self.assertIn(r'Already 30\%',fixed);self.assertIn('20%20encoded',fixed)
        self.assertIn('#1%\n',fixed)

    def test_escaped_percent_and_literal_braces_do_not_change_group_balance(self):
        source='\n'.join([r'\newcommand{\labelled}[1]{%',r'\{#1\} costs 20\% \textbf{value}%}',r'\begin{document}x\end{document}'])
        fixed=generated_tex.normalize(source)
        self.assertIn(r'\{#1\} costs 20\% \textbf{value}}%',fixed)

    def test_legitimate_commented_brace_and_unrelated_syntax_are_not_guessed(self):
        for source in ['\n'.join([r'\newcommand{\ok}{text%}',r'}']),
                       r'\newcommand{\broken}{still missing a close',
                       r'\input{missing-file} \newpage ordinary text']:
            self.assertEqual(generated_tex.normalize(source),source)

    def test_successful_sources_are_not_changed_or_recompiled(self):
        with tempfile.TemporaryDirectory() as directory:
            task=Path(directory);tex=task/'resume.tex';tex.write_text(r'\newcommand{\ok}{text%}'+'\n}')
            original=tex.read_bytes();compiler=mock.Mock(return_value=task/'resume.pdf')
            self.assertEqual(generated_tex.compile_generated(compiler,task,tex),task/'resume.pdf')
            compiler.assert_called_once();self.assertEqual(tex.read_bytes(),original)

    def test_failure_triggers_at_most_one_retry_and_still_requires_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            task=Path(directory);tex=task/'resume.tex';tex.write_text(r'\newcommand{\sample}{text%}')
            compiler=mock.Mock(side_effect=[RuntimeError('Runaway argument'),RuntimeError('PDF must be exactly one page')])
            with self.assertRaisesRegex(RuntimeError,'after one syntax cleanup.*exactly one page'):
                generated_tex.compile_generated(compiler,task,tex)
            self.assertEqual(compiler.call_count,2)

    def test_unrelated_compiler_errors_are_not_retried_without_a_specific_correction(self):
        with tempfile.TemporaryDirectory() as directory:
            task=Path(directory);tex=task/'resume.tex';tex.write_text(r'\documentclass{missing}')
            compiler=mock.Mock(side_effect=RuntimeError('Missing class'))
            with self.assertRaisesRegex(RuntimeError,'Missing class'):generated_tex.compile_generated(compiler,task,tex)
            compiler.assert_called_once()

    def test_missing_font_glyphs_prevent_success_even_when_compiler_returns_pdf(self):
        with tempfile.TemporaryDirectory() as directory:
            task=Path(directory);tex=task/'resume.tex';tex.write_text(r'\begin{document}A readable word\end{document}')
            def compile_pdf(task,path):
                path.with_suffix('.log').write_text('Missing character: unsupported character in font')
                return path.with_suffix('.pdf')
            with self.assertRaisesRegex(RuntimeError,'missing font glyphs'):
                generated_tex.compile_generated(compile_pdf,task,tex)


class RetainedTailorTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.ResumeWorkflowTests('runTest');self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.store=self.fixture.store
        job,_=self.fixture.upload();worker.cleanup_completed(UID,job['id'])

    def failed(self):
        jid=self.store.enqueue(UID,'tailor',{'url':'https://example.org/jobs/teaching'});job=self.store.claim()
        task,_=worker.new_worktree(UID,jid,'tailor');main_commits.begin(worker,job,self.store)
        staged=worker.stage_agent_task(UID,jid,task)
        folder=staged/f'applications/2026/job-{jid}';folder.mkdir(parents=True)
        (folder/'resume.tex').write_text(r'\documentclass{article}\begin{document}Verified teaching experience\end{document}')
        (folder/'job-url.txt').write_text('https://example.org/jobs/teaching')
        worker._write_intent(worker.ROOT/'job-reports'/UID/f'{jid}.json',{'payloads':[{'text':'Draft complete.'}]})
        self.store.finish(jid,'failed',error='Generated TeX did not compile')
        return job,folder

    def test_recovery_reuses_exact_retained_draft_without_model_or_new_job(self):
        job,folder=self.failed();original=worker.run
        def run(argv,**kwargs):
            if Path(str(argv[0])).name=='openclaw':raise AssertionError('Model must not rerun')
            return original(argv,**kwargs)
        def compile_pdf(task,tex):tex.with_suffix('.pdf').write_bytes(fixtures.PDF)
        with mock.patch.object(worker,'run',side_effect=run),mock.patch.object(worker,'compile_pdf',side_effect=compile_pdf):
            result=worker.run_resume_job(job,self.store,reuse_saved_response=True)
        self.assertTrue(result['changed']);self.assertEqual(len(result['artifacts']),2)
        self.assertEqual(self.fixture.git('-C',str(self.fixture.remote),'show','main:'+str((folder/'resume.tex').relative_to(worker.agent_taskdir(UID,job['id'])))),(folder/'resume.tex').read_text())
        self.store.finish(job['id'],'done',result)
        self.assertEqual(self.store.recent(UID)[0]['id'],job['id'])
        self.assertIsNone(self.store.claim())

    def test_recovery_rejects_missing_retained_draft_or_changed_main(self):
        job,folder=self.failed()
        seed=self.fixture.seed
        self.fixture.git('-C',str(seed),'pull','--ff-only','origin','main')
        (seed/'external.txt').write_text('New work')
        self.fixture.git('-C',str(seed),'add','external.txt');self.fixture.git('-C',str(seed),'commit','-m','External change')
        self.fixture.git('-C',str(seed),'push','origin','main')
        with self.assertRaisesRegex(ValueError,'Main changed'):
            worker.run_resume_job(job,self.store,reuse_saved_response=True)
        import shutil
        shutil.rmtree(worker.agent_taskdir(UID,job['id']))
        with self.assertRaisesRegex(ValueError,'original tailored draft is missing'):
            worker.run_resume_job(job,self.store,reuse_saved_response=True)


if __name__=='__main__':unittest.main()
