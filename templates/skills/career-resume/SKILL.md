---
name: career-resume
description: Accurate TeX resume editing for any profession for CVPilot queued jobs
user-invocable: true
---
For queued jobs with inline SOURCE DATA, all master resumes, original TeX layouts,
profile facts and provenance are already supplied by the trusted worker. Do not
browse sandbox directories or ask the user to supply those files again. Use
supplied posting text when present; otherwise fetch the exact posting URL.
For the initial draft return cvpilot-resume-tex (complete document) and
cvpilot-notes. For a later refinement return cvpilot-resume-body (complete body
only) and cvpilot-notes, as requested by that stage. During tailoring the
profile and masters are read-only; do not
return profile JSON. Use current master evidence directly even if the profile
is empty or outdated. Use profile facts only when user-confirmed or cited to
current source hashes; corroborate stale facts in current masters. Profile
updates belong to /cvpilot profile sync. Do not use filesystem tools or run Git.
The worker saves files at fixed job paths, verifies read-only inputs, compiles
the one-page PDF and commits validated changes to main. For other tasks, follow
their explicit task instructions and use complete absolute sandbox paths.

Start initial notes with `Company: <employer>` and `Position: <exact title>`
copied from the posting; use Unknown if unavailable. Keep these headers in
later notes. The worker names the folder `job-<id>-<company>-<position>` using
safe, bounded slugs. Do not invent names or choose output paths yourself.

Resume labels are user-defined and default to `general`; they are not a fixed
set of professions. Adapt to the real industry and job, including nontechnical
careers. Consider relevant qualifications, practical experience, licenses,
certifications, creative work, research and volunteering as supported by sources.
PDF-only inputs can use a clean new TeX layout; never claim exact reproduction
of their formatting. Discord attachments are handled by the trusted controller.

Write raw TeX with actual line breaks, not literal JSON escape sequences. Close
macro bodies before percent comments. Escape text ampersands; use unescaped
ampersands only for table alignment. The controller may retry compilation once
after narrowly correcting generated syntax. The final output must be a readable,
well-filled single page; do not aggressively cut the first draft to guarantee it.

Follow the worker's shared RESUME QUALITY POLICY in both stages. Start with the
closest master and preserve its unaffected wording, entries and formatting.
Improve relevance using evidence, not a blanket rewrite. Use the whole usable
page with supported meaningful detail and normal bottom breathing room. Keep
action, scope/method and outcome/metrics together so bullets remain useful.
Target short spillovers in wrapped bullets first: reword two lines into one or
three into two when the meaning survives. Keep already concise bullets intact.
Do not force every bullet onto one line, replace context with cryptic fragments,
or infer qualifications from adjacent technologies. Explain initial substantive
omissions/replacements and name the baseline in notes.

For page-fit refinements, follow the worker's measured line-reduction target.
It reports overflow rows, available first-page space and the net number of body
line-equivalents to recover. This is a wrap-reduction budget, not permission to
delete bullets. Reword only enough to meet it, retaining all current bullets,
sections, entries, dates, numeric achievements and important supported keywords.
For an underfilled draft the worker instead gives a restoration budget: add back
relevant source-supported context or omitted supporting bullets, without filler,
invented claims or changing typography. Return the requested
cvpilot-resume-body and cvpilot-notes blocks. The worker keeps the original
preamble, font sizes, margins and spacing, recompiles, and measures again.
Deleted bullets/identities/metrics, excessive cuts and non-improving proposals
are rejected. Refinement stops at a well-filled single page or an explicit
stalled-progress/time/round-budget error; it does not shrink typography or
publish a gutted resume merely because the page count is one. Sparse source
resumes must not be padded with unrelated facts to fill a quota.
