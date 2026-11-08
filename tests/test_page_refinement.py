"""Measured page-fit budgets, preserved typography, rollback and private rounds."""
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock
import test_controller as fixtures
import test_resume_attachments as workflow
from test_tailoring import response,updated
import generated_tex
import page_refinement as fit
import worker

UID=fixtures.UID
TEX='\\documentclass[10pt]{article}\n\\begin{document}\nOriginal resume content.\n\\end{document}\n'


def metrics(pages=2,footprint=780,target=5):
    return {'pages':pages,'lines_per_page':[60,5] if pages>1 else [60],
            'overflow_lines':5 if pages>1 else 0,'line_pitch_pt':12.0,'first_page_spare_pt':0,
            'net_overflow_pt':max(0,footprint-720),'target_lines':target if pages>1 else 0,
            'footprint_pt':footprint,'text_height_pt':720.0}


def revision(body='Concise resume content.',notes='Recovered the measured wrapped lines.'):
    return {'payloads':[{'text':f'```cvpilot-resume-body\n{body}\n```\n```cvpilot-notes\n{notes}\n```'}]}


def bbox(pages):
    return '<html><body>'+''.join('<page width="612" height="792">'+''.join(
        f'<flow><block><line xMin="{x}" xMax="500" yMin="{y}" yMax="{y+10}"><word>{text}</word></line></block></flow>'
        for x,y,text in lines)+'</page>' for lines in pages)+'</body></html>'


class MeasurementTests(unittest.TestCase):
    def test_measurement_wrapper_does_not_enter_master_resume_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);calls=[]
            def run(argv,**kwargs):
                calls.append(argv)
                return 'Pages: 1' if 'pdfinfo' in argv else ''
            for relative,expected in (('master-resumes/general/resume.tex',False),('applications/2026/job-1/resume.tex',True)):
                tex=root/relative;tex.parent.mkdir(parents=True);tex.write_text(TEX);tex.with_suffix('.pdf').write_bytes(workflow.PDF)
                with mock.patch.object(worker,'run',side_effect=run):worker.compile_pdf(root,tex)
                self.assertEqual(tex.with_name('.cvpilot-layout-resume.tex').exists(),expected)

    def test_overflow_goal_credits_first_page_slack_and_merges_aligned_columns(self):
        xml=bbox([[(20,20,'Name'),(20,680,'First'),(300,680,'Aligned')],
                  [(20,20,'One'),(20,32,'Two'),(20,44,'Three'),(20,56,'Four'),(20,68,'Five')]])
        m=fit.measure_xml(xml,'CVPILOTLAYOUT:20.075pt|702.625pt') # PDF text block: y=20..720
        self.assertEqual(m['lines_per_page'],[2,5]);self.assertEqual(m['overflow_lines'],5)
        self.assertAlmostEqual(m['first_page_spare_pt'],30)
        self.assertEqual(m['line_pitch_pt'],12);self.assertEqual(m['target_lines'],3)
        self.assertAlmostEqual(m['net_overflow_pt'],28)

    def test_multiple_overflow_pages_and_footer_numbers(self):
        m=fit.measure_xml(bbox([[(20,700,'End')],[(20,20,'One'),(20,32,'Two'),(20,770,'2')],
                              [(20,20,'Three'),(20,32,'Four'),(20,770,'3')]]),'CVPILOTLAYOUT:20.075pt|702.625pt')
        self.assertEqual(m['pages'],3);self.assertEqual(m['overflow_lines'],4)
        self.assertEqual(m['lines_per_page'],[1,2,2]);self.assertGreater(m['target_lines'],0)

    def test_missing_geometry_or_unreadable_pdf_never_becomes_a_generic_shorten_request(self):
        for xml,log in ((bbox([[(20,30,'text')]]),''),('<html/>','CVPILOTLAYOUT:20pt|700pt')):
            with self.assertRaises(ValueError):fit.measure_xml(xml,log)

    def test_preamble_and_identity_header_are_retained_exactly(self):
        old='\\documentclass[10pt]{article}\n% \\begin{document} in a comment\n\\begin{document}\nName and contacts\\\\\n\\ressection{Experience}\nLong bullet.\n\\end{document}'
        proposed='Changed name\n\\ressection{Experience}\nShort bullet.'
        new,_=fit.parse_revision(revision(proposed),old)
        self.assertTrue(new.startswith(old.split('Long bullet.')[0]))
        self.assertIn('Short bullet.',new);self.assertNotIn('Changed name',new)

    def test_uniform_body_escaping_is_corrected_without_damaging_real_row_breaks(self):
        body=r'\begin{center}Name\\[2pt]Contact\end{center}'+'\n'+r'\ressection{Experience}\begin{itemize}\item Original words.\end{itemize}'
        old=TEX.replace('Original resume content.',body)
        desired=body.replace('Original words.','Concise words.')
        new,_=fit.parse_revision(revision(desired.replace('\\','\\\\')),old)
        self.assertIn(r'Name\\[2pt]Contact',new)
        self.assertIn(r'\item Concise words.',new)
        self.assertEqual(new,old.replace('Original words.','Concise words.'))

    def test_font_shrinking_spacing_scaling_and_new_columns_are_rejected(self):
        for body in (r'\tiny Content',r'\fontsize{7}{7}\selectfont Content',r'\vspace{-20pt} Content',
                     r'\resizebox{!}{10pt}{Content}',r'\begin{multicols}{2}Content\end{multicols}',
                     r'\begin{tiny}Content\end{tiny}',r'\begin{itemize}[nosep]\item Content\end{itemize}',
                     r'\renewenvironment{resitems}{}{} Content',r'\documentclass{other}'):
            with self.subTest(body=body),self.assertRaises(fit.RevisionRejected):fit.parse_revision(revision(body),TEX)
        old=TEX.replace('Original resume content.',r'{\fontsize{11}{12}\selectfont Original content.}')
        with self.assertRaises(fit.RevisionRejected):fit.parse_revision(revision('Removed font controls.'),old)

    def test_prompt_has_exact_physical_goal_and_no_blanket_shortening(self):
        prompt=fit.revision_prompt({'id':35},{'documents':[{'path':'resume.tex','text':TEX}]},None,TEX,'Notes',metrics(),1,'')
        self.assertIn('recover 5 rendered line-equivalents',prompt)
        self.assertIn('60.00 pt of net overflow',prompt);self.assertIn('12.00 pt per body line',prompt)
        self.assertIn('already credited',prompt);self.assertIn('Do not generically shorten',prompt)
        self.assertIn('cvpilot-resume-body',prompt);self.assertNotIn('cvpilot-profile-json',prompt)


class RefinementLoopTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.task=self.root/'task';self.task.mkdir();self.tex=self.task/'resume.tex';self.tex.write_text(TEX)
        (self.task/'notes.md').write_text('Original notes')
        self.job={'id':35,'discord_id':UID,'kind':'tailor','payload':'{}'}
        self.store=SimpleNamespace(path=self.root/'jobs.sqlite');self.prompts=[]
        self.worker=SimpleNamespace(ROOT=self.root,progress=mock.Mock(),validate_agent_task=mock.Mock(),
            _write_intent=worker._write_intent,agent_completion=mock.Mock(),compile_pdf=mock.Mock())
        self.initial={'payloads':[{'text':'Original complete draft'}]}
        worker._write_intent(self.root/'job-reports'/UID/'35.json',self.initial)
        self.inputs={'documents':[{'path':'master-resumes/general/resume.tex','text':TEX}]}

    def run_fit(self,compiles,measurements,replies,**kwargs):
        pending=list(compiles)
        def compile_pdf(task,tex):
            tex.with_suffix('.pdf').write_bytes(b'%PDF-1.4 mock')
            tex.with_suffix('.log').write_text('Current compile log')
            error=pending.pop(0)
            if error:raise error
            return tex.with_suffix('.pdf')
        self.worker.compile_pdf.side_effect=compile_pdf
        self.worker.agent_completion.side_effect=replies
        with mock.patch.object(fit,'measure',side_effect=measurements):
            return fit.fit(self.worker,self.job,self.store,self.task,self.tex,self.inputs,None,self.initial,
                           kwargs.pop('deadline',time.monotonic()+900),**kwargs)

    def test_remeasures_each_round_and_stops_immediately_on_adequate_one_page(self):
        result=self.run_fit([generated_tex.PageCountError(self.tex.with_suffix('.pdf'),2),
                            generated_tex.PageCountError(self.tex.with_suffix('.pdf'),2),None],
                           [metrics(),metrics(2,744,2),metrics(1,720,0)],
                           [revision('First tighter draft.'),revision('Final concise draft.')])
        self.assertEqual(result['rounds'],2);self.assertEqual(self.worker.agent_completion.call_count,2)
        prompts=[call.args[1] for call in self.worker.agent_completion.call_args_list]
        self.assertIn('recover 5 rendered',prompts[0]);self.assertIn('recover 2 rendered',prompts[1])
        self.assertIn('Final concise draft',self.tex.read_text())
        report=json.loads((self.root/'job-reports'/UID/'35.json').read_text())
        self.assertIn('cvpilot-resume-tex',report['payloads'][0]['text'])
        self.assertIn('"status":"unchanged"',report['payloads'][0]['text'])
        for path in (self.root/'job-drafts').rglob('*'):
            if path.is_file():self.assertEqual(path.stat().st_mode&0o777,0o600)

    def test_excessive_cut_is_rolled_back_before_next_attempt(self):
        result=self.run_fit([generated_tex.PageCountError(self.tex.with_suffix('.pdf'),2),None,None],
                           [metrics(),metrics(1,600,0),metrics(1,720,0)],
                           [revision('Over-cut content.'),revision('Minimal sufficient change.')])
        second=self.worker.agent_completion.call_args_list[1].args[1]
        self.assertIn('rejected as over-shortened',second);self.assertIn(TEX,second)
        self.assertNotIn('Over-cut content.',second);self.assertEqual(result['rounds'],2)
        records=[json.loads(p.read_text()) for p in (self.root/'job-drafts').rglob('measurement.json')]
        rejected=next(r for r in records if r['status']=='over_shortened')
        self.assertEqual(rejected['best_metrics']['pages'],2)

    def test_repeated_nonprogress_stops_and_retains_fuller_draft(self):
        overflow=generated_tex.PageCountError(self.tex.with_suffix('.pdf'),2)
        with self.assertRaisesRegex(RuntimeError,'stalled after 3'):
            self.run_fit([overflow]*4,[metrics()]*4,[revision()]*3)
        self.assertEqual(self.tex.read_text(),TEX);self.assertEqual(self.worker.agent_completion.call_count,3)

    def test_bad_font_change_gets_specific_feedback_then_can_converge(self):
        result=self.run_fit([generated_tex.PageCountError(self.tex.with_suffix('.pdf'),2),None],
                           [metrics(),metrics(1,720,0)],[revision(r'\tiny Bad'),revision('Proper content edit.')])
        self.assertEqual(result['rounds'],2)
        self.assertIn('changed fonts',self.worker.agent_completion.call_args_list[1].args[1])
        self.assertNotIn(r'\tiny',self.tex.read_text())

    def test_revision_compile_error_is_repaired_from_the_last_valid_fuller_draft(self):
        result=self.run_fit([generated_tex.PageCountError(self.tex.with_suffix('.pdf'),2),
                            generated_tex.TexCompileError('Undefined control sequence l.18'),None],
                           [metrics(),metrics(1,720,0)],[revision('Broken proposal.'),revision('Corrected proposal.')])
        self.assertEqual(result['rounds'],2)
        prompt=self.worker.agent_completion.call_args_list[1].args[1]
        self.assertIn('Undefined control sequence',prompt);self.assertIn('recover 5 rendered',prompt)

    def test_time_budget_and_saved_response_recovery_cannot_invoke_another_model(self):
        for options,pattern in (({'deadline':0},'time budget'),({'allow_model':False},'cannot run additional model')):
            with self.subTest(options=options),self.assertRaisesRegex(RuntimeError,pattern):
                self.run_fit([generated_tex.PageCountError(self.tex.with_suffix('.pdf'),2)],[metrics()],[],**options)
            self.worker.agent_completion.assert_not_called()

    def test_infrastructure_errors_are_not_mistaken_for_bad_tex(self):
        with self.assertRaisesRegex(RuntimeError,'Docker unavailable'):
            self.run_fit([RuntimeError('Docker unavailable')],[],[])
        self.worker.agent_completion.assert_not_called()

    def test_layout_extraction_cannot_override_the_compilers_actual_page_count(self):
        overflow=generated_tex.PageCountError(self.tex.with_suffix('.pdf'),2)
        with self.assertRaisesRegex(ValueError,'page count and layout measurement disagree'):
            self.run_fit([overflow,overflow],[metrics(),metrics(1,720,0)],[revision()])
        self.assertEqual(self.tex.read_text(),TEX)
        self.assertEqual(self.worker.agent_completion.call_count,1)

    def test_already_one_page_needs_no_measurement_or_model_round(self):
        result=self.run_fit([None],[],[])
        self.assertEqual(result,{'rounds':0,'pages':1});self.worker.agent_completion.assert_not_called()


class RefinementWorkflowTests(unittest.TestCase):
    def test_only_the_final_fitting_draft_is_published_via_normal_main_workflow(self):
        fixture=workflow.ResumeWorkflowTests('runTest');fixture.setUp();self.addCleanup(fixture.doCleanups)
        upload,_=fixture.upload();worker.cleanup_completed(UID,upload['id'])
        jid=fixture.store.enqueue(UID,'tailor',{'url':'https://jobs.example/teaching'});job=fixture.store.claim()
        original=worker.run;calls=[];compiles=[]
        def agent(argv,**kwargs):
            if argv[0]!='openclaw':return original(argv,**kwargs)
            prompt=Path(argv[argv.index('--message-file')+1]).read_text();calls.append(prompt)
            self.assertGreater(int(argv[argv.index('--timeout')+1]),0)
            self.assertLess(int(argv[argv.index('--timeout')+1]),kwargs['timeout'])
            if len(calls)==1:
                pack=json.loads((worker.taskdir(UID,jid)/'.cvpilot-sync-inputs.json').read_text())
                return json.dumps(response(profile=updated(pack)))
            self.assertIn('recover 1 rendered line-equivalents',prompt)
            return json.dumps(revision('Verified teaching experience in concise wording.'))
        def compile_pdf(task,tex):
            compiles.append(tex.read_text());tex.with_suffix('.pdf').write_bytes(workflow.PDF)
            tex.with_suffix('.log').write_text('Current log')
            if len(compiles)==1:raise generated_tex.PageCountError(tex.with_suffix('.pdf'),2)
            return tex.with_suffix('.pdf')
        with (mock.patch.object(worker,'run',side_effect=agent),mock.patch.object(worker,'compile_pdf',side_effect=compile_pdf),
              mock.patch.object(fit,'measure',side_effect=[metrics(2,732,1),metrics(1,720,0)])):
            result=worker.work(job,fixture.store)
        self.assertEqual(len(calls),2);self.assertEqual(len(result['artifacts']),2)
        self.assertIn('after 1 measured refinement',result['note'])
        path=f'applications/{time.gmtime().tm_year}/job-{jid}/resume.tex'
        published=fixture.git('-C',str(fixture.remote),'show','main:'+path)
        self.assertIn('concise wording',published);self.assertNotEqual(published,compiles[0])
        self.assertFalse(fixture.git('-C',str(fixture.remote),'ls-tree','main','--','job-drafts'))
        self.assertEqual(fixture.git('-C',str(fixture.remote),'show','main:master-resumes/general/resume.tex'),workflow.TEX.decode())


if __name__=='__main__':unittest.main()
