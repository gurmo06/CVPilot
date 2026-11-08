---
name: career-resume
description: Accurate TeX resume editing for any profession for CVPilot queued jobs
user-invocable: true
---
Read the exact job posting with web_fetch; read master resumes and profile. Wording may be improved but claims must be supported. If a desired tool/skill is unconfirmed, OMIT it and note the question for the user in `notes.md`. Edit only the task worktree path provided in the trusted CVPilot job instructions. Preserve TeX styling, produce `resume.tex` in requested application directory. Do not run Git and do not publish; worker compiles `resume.pdf`, checks one page, and commits validated changes directly to main.

Resume labels are user-defined and default to `general`; they are not a fixed
set of professions. Adapt to the real industry and job, including nontechnical
careers. Consider relevant qualifications, practical experience, licenses,
certifications, creative work, research and volunteering as supported by sources.
PDF-only inputs can use a clean new TeX layout; never claim exact reproduction
of their formatting. Discord attachments are handled by the trusted controller.

Write raw TeX with actual line breaks, not literal JSON escape sequences. Close
macro bodies before percent comments. Escape text ampersands; use unescaped
ampersands only for table alignment. The controller may retry compilation once
after narrowly correcting generated syntax; validation still requires one page.
