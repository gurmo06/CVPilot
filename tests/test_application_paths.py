"""Descriptive names stay bounded, safe and compatible with existing jobs."""
from pathlib import Path
import tempfile
import unittest
import application_paths as paths
import resumes
import resume_delete
import worker


class ApplicationPathTests(unittest.TestCase):
    def test_company_position_and_id_form_a_readable_folder_name(self):
        name=paths.new_directory(40,'**Company:** T-Mobile\n**Position:** Software Engineering Internship')
        self.assertEqual(name.name,'job-40-t-mobile-software-engineering-internship')
        self.assertTrue(resumes.safe_artifact_path(name.as_posix()+'/resume.pdf'))
        self.assertEqual(resume_delete.directory('application-delete',name.as_posix()),name.as_posix())
        self.assertTrue(worker.allowed_path('tailor',name.as_posix()+'/resume.pdf',{}))

    def test_worker_posting_title_takes_precedence_and_missing_details_do_not_fail(self):
        name=paths.new_directory(40,'Company: Example School\nPosition: Wrong Title',{'title':'Classroom Teacher'})
        self.assertEqual(name.name,'job-40-example-school-classroom-teacher')
        self.assertEqual(paths.new_directory(40,'Company: Unknown\nPosition: Unknown').name,'job-40')
        self.assertEqual(paths.new_directory(40,posting={'title':'Nurse'}).name,'job-40-nurse')

    def test_untrusted_names_are_transliterated_bounded_and_cannot_be_paths(self):
        name=paths.new_directory(40,'Company: ../../Médical & Co.\nPosition: '+('Senior / Nurse -- '*100))
        self.assertTrue(name.name.startswith('job-40-medical-co-senior-nurse'))
        self.assertLessEqual(len(name.name),128)
        self.assertRegex(name.name,r'^job-40-[a-z0-9]+(?:-[a-z0-9]+)*$')
        for invalid in ('applications/2026/job-40-../resume.pdf','applications/2026/job-40-company/../resume.pdf'):
            self.assertFalse(resumes.safe_artifact_path(invalid))

    def test_recovery_keeps_old_and_descriptive_folders_without_confusing_job_prefixes(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'applications/2025/job-400-other-role').mkdir(parents=True)
            self.assertIsNone(paths.existing_directory(root,40))
            legacy=root/'applications/2025/job-40';legacy.mkdir()
            self.assertEqual(paths.existing_directory(root,40),legacy.relative_to(root))
            legacy.rename(legacy.with_name('job-40-example-school-teacher'))
            self.assertEqual(paths.existing_directory(root,40).name,'job-40-example-school-teacher')
            legacy.mkdir()
            with self.assertRaisesRegex(RuntimeError,'Multiple'):paths.existing_directory(root,40)

    def test_symlink_output_directories_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'applications').symlink_to(root,target_is_directory=True)
            with self.assertRaisesRegex(RuntimeError,'Unsafe'):paths.existing_directory(root,40)


if __name__=='__main__':unittest.main()
