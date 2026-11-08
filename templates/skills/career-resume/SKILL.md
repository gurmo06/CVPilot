---
name: career-resume
description: Accurate TeX resume editing for any profession for CVPilot queued jobs
user-invocable: true
---
For queued jobs with inline SOURCE DATA, all master resumes, original TeX layouts,
profile facts and provenance are already supplied by the trusted worker. Do not
browse sandbox directories or ask the user to supply those files again. Use
supplied posting text when present; otherwise fetch the exact posting URL.
Return the three fenced blocks requested by the worker: cvpilot-resume-tex,
cvpilot-notes, and cvpilot-profile-json. Do not use filesystem tools or run Git.
The worker saves files at fixed job paths, validates profile provenance, compiles
the one-page PDF and commits validated changes to main. For other tasks, follow
their explicit task instructions and use complete absolute sandbox paths.

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

For page-fit refinements, follow the worker's measured line-reduction target.
It reports overflow rows, available first-page space and the net number of body
line-equivalents to recover. Tighten only enough content to meet that target;
preserve important facts and supported job keywords. Return the requested
cvpilot-resume-body and cvpilot-notes blocks. The worker keeps the original
preamble, font sizes, margins and spacing, recompiles, and measures again.
Over-shortened or non-improving proposals are rejected. Refinement stops at one
page or with an explicit stalled-progress/time/round-budget error; it does not
silently shrink typography to force a fit.
