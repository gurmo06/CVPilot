# CVPilot workspace

Your private resume workspace for any profession, industry, or field of study.

## Use CVPilot from Discord

1. Initialize with `/cvpilot init github_username:<your GitHub username>`.
2. Upload with `/cvpilot master upload file:<PDF or TeX>`. The label defaults to
   `general`. Use any label you prefer, such as teaching, healthcare, sales,
   creative, academic, or volunteer. Add `companion:<matching file>` for a PDF/TeX
   pair. PDF and UTF-8 TeX files are supported, up to 8 MiB each; DOCX is not.
3. Wait for the upload job to complete, then request
   `/cvpilot tailor url:<HTTPS job posting URL>`.
4. Receive the one-page PDF and editable TeX as private Discord attachments.
   If DMs are blocked, use `/cvpilot download job_id:<id>` in the designated
   channel for a private response. Omit the job ID for your latest saved files.
5. Retrieve sources with `/cvpilot master get label:<label>` and inspect progress
   with `/cvpilot status`.

There is no fixed list of career categories. Labels are user-defined lowercase
names (letters, numbers, hyphens; start with a letter; up to 40 characters).
Reusing a label explicitly replaces its active uploaded version; earlier
versions remain in Git history. Upload a matching PDF/TeX pair together so the
bot does not combine files from different versions.

## Files and review history

- `master-resumes/<label>/resume.pdf` and/or `resume.tex`: source resumes.
- `profile/MASTER_PROFILE.md` and `profile/profile_sources.json`: verified facts
  and their sources, including work, education, certifications and volunteering.
- `applications/YYYY/job-<id>/{resume.tex,resume.pdf,job-url.txt}`: tailored output.

CVPilot saves source uploads and generated changes on `cvpilot/<job-kind>/<id>`
review branches. Successful Discord uploads are immediately available to later
jobs; you do not need to merge a branch to begin tailoring. GitHub also keeps
source history and provides a fallback download link. Once a source is updated
or merged on `main`, that main version takes precedence over its earlier upload.
The bot does not silently merge into `main`; branch protection depends on the
GitHub plan. GitHub uploads to `main` are also supported and fetched before use.

The bot reads all available source resumes and verified profile facts. It
adapts to the actual job and industry, keeps claims grounded in source evidence,
and preserves existing formatting when TeX is available. With PDF-only sources,
it creates a clean new layout instead of claiming an exact formatting match.
AI edits to source resumes require both account permission and an explicit edit
request. User-supplied uploads are themselves explicit source updates.
