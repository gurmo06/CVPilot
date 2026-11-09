"""Quality means preserved evidence and useful page coverage, not page count alone."""
import unittest
from resume_quality import POLICY,fill_target,fuller_sources,lost_content
import page_refinement as fit
from test_page_refinement import revision,TEX


class ResumeQualityTests(unittest.TestCase):
    def test_normal_bottom_breathing_room_is_not_treated_as_underfill(self):
        metrics={'pages':1,'line_pitch_pt':12,'text_height_pt':720,'first_page_spare_pt':36}
        self.assertEqual(fill_target(metrics),0)
        metrics['first_page_spare_pt']=120;self.assertEqual(fill_target(metrics),7)
        metrics['pages']=2;self.assertEqual(fill_target(metrics),0)

    def test_sparse_sources_do_not_create_an_obligation_to_add_filler(self):
        self.assertFalse(fuller_sources({'documents':[{'text':TEX}]},TEX))
        richer=TEX.replace('Original resume content.',r'\item Built supported systems. \item Improved verified outcomes.')
        self.assertTrue(fuller_sources({'documents':[{'text':richer}]},TEX))
        self.assertTrue(fuller_sources({'documents':[{'text':'Supported detailed teaching experience '*40}]},TEX))

    def test_rewording_keeps_bullets_identities_and_metrics(self):
        before=r'\ressection{Experience}\jobline{Clinic}{Nurse}{2024 -- Present}\begin{itemize}\item Delivered care to 40 patients.\item Reduced delays by 20\%.\end{itemize}'
        after=before.replace('Delivered care to 40 patients.','Cared for 40 patients.').replace('Reduced delays by','Cut delays by')
        self.assertEqual(lost_content(before,after),'')
        reference=TEX.replace('Original resume content.',before)
        document,_=fit.parse_revision(revision(after),reference)
        self.assertIn('Cared for 40 patients.',document)

    def test_page_fit_cannot_delete_a_bullet_section_entry_or_metric(self):
        before=r'\ressection{Experience}\jobline{Clinic}{Nurse}{2024 -- Present}\begin{itemize}\item Delivered care to 40 patients.\item Reduced delays by 20\%.\end{itemize}'
        reference=TEX.replace('Original resume content.',before)
        for after in (before.replace(r'\item Reduced delays by 20\%.',''),
                      before.replace('Experience','Overview'),before.replace('Clinic','Another employer'),
                      before.replace('2024','2025'),before.replace('40 patients','patients')):
            with self.subTest(after=after),self.assertRaises(fit.RevisionRejected):
                fit.parse_revision(revision(after),reference)

    def test_restoration_may_repeat_an_existing_list_style_but_not_introduce_spacing(self):
        body=r'\ressection{Experience}\begin{itemize}\item Delivered care to 40 patients.\end{itemize}'
        reference=TEX.replace('Original resume content.',body)
        restored=body+r'\begin{itemize}\item Documented verified care plans.\end{itemize}'
        document,_=fit.parse_revision(revision(restored),reference,allow_additions=True)
        self.assertIn('verified care plans',document)
        with self.assertRaisesRegex(fit.RevisionRejected,'spacing'):
            fit.parse_revision(revision(restored.replace('\\begin{itemize}','\\begin{itemize}[nosep]',1)),reference,allow_additions=True)

    def test_shared_policy_prioritizes_wrapped_wording_context_and_source_support(self):
        for phrase in ('select, reorder, combine and rewrite','two-line bullet','action, scope/method/tools',
                       'Use the whole usable page','not delete that many source lines or bullets',
                       'Never infer a new qualification solely from related experience'):
            self.assertIn(phrase,POLICY)
        self.assertNotIn('smallest necessary edits',POLICY)


if __name__=='__main__':unittest.main()
