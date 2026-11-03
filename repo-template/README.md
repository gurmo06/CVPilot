# CVPilot workspace

Your private resume workspace for any profession, industry, or field of study.

## Use CVPilot from Discord

Join the CVPilot server and receive the CVPilot User role from an administrator.
You can then use the commands in the designated server channel or in a direct
message to the CVPilot bot. DM commands check your current server membership and
role each time; leaving the server or losing access disables DM use. Group DMs
are not supported. Operator diagnostics stay restricted to the server.

1. Initialize with `/cvpilot init github_username:<your GitHub username>`.
2. Upload with `/cvpilot master upload file:<PDF or TeX>`. The label defaults to
   `general`. Use any label you prefer, such as teaching, healthcare, sales,
   creative, academic, or volunteer. Add `companion:<matching file>` for a PDF/TeX
   pair. PDF and UTF-8 TeX files are supported, up to 8 MiB each; DOCX is not.
   A TeX-only upload automatically compiles to PDF. Both files are saved and
   available through `master get` or `download`. If you upload a matching pair,
   CVPilot keeps your supplied PDF without recompiling it.
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

## Start over with a confirmed reset

Run `/cvpilot reset` in the server channel or in a DM. Read the private summary,
select **Reset repository**, and type **RESET** in the confirmation form within
2 minutes. Only the requesting user can confirm, and permissions are checked
again. Cancel or let the prompt expire to leave the repository unchanged.

A confirmed reset replaces the files on `main` with the current initial CVPilot
template and deletes every other branch, including branches you created. Old
uploads and generated resumes stop being available through CVPilot. Your account,
SSH keys, agent and job history remain, so upload a new resume to begin again.
Git history and a private operator recovery backup remain; reset is not secure
erasure. Tags are unchanged. Branch protection is never bypassed or disabled.

Wait for all your jobs to finish before resetting. Other resume actions are
blocked while a reset is pending or incomplete. If it fails, inspect
`/cvpilot status` and run a fresh confirmed reset after resolving the reported
problem. The remote branch update and deletions are atomic and reject branch
changes made concurrently. No reset happens merely by opening the command.
