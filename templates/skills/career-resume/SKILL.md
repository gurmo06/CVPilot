---
name: career-resume
description: Role-specific accurate TeX resume editing for CVPilot queued jobs
user-invocable: true
---
Read the exact job posting with web_fetch; read master resumes and profile. Wording may be improved but claims must be supported. If a desired tool/skill is unconfirmed, OMIT it and note the question for the user in `notes.md`. Edit only the task worktree path provided in the trusted CVPilot job instructions. Preserve TeX styling, produce `resume.tex` in requested application directory. Do not run Git and do not publish; worker compiles `resume.pdf`, checks one page, and publishes a review branch after validation.