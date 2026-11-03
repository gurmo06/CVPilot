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

## Files and commit history

- `master-resumes/<label>/resume.pdf` and/or `resume.tex`: source resumes.
- `profile/MASTER_PROFILE.md` and `profile/profile_sources.json`: verified facts
  and their sources, including work, education, certifications and volunteering.
- `applications/YYYY/job-<id>/{resume.tex,resume.pdf,job-url.txt}`: tailored output.

Successful uploads, resume edits, tailored applications and profile changes
commit directly to `main` after validation. CVPilot creates no per-job branches.
Each job links to its exact commit; saved downloads continue to retrieve the
files from that commit even after later edits. Unchanged jobs create no extra
commit. GitHub edits to `main` are fetched before work begins. Concurrent edits
are preserved: a job refuses to overwrite a changed `main`, and you can submit
it again against the current files. Interrupted pushes recover from a private
publication journal without rerunning the agent or duplicating the commit.
Existing GitHub protections are respected; direct pushes must be permitted by
the repository's settings. New repositories retain force-push/deletion protection
where supported, without requiring pull-request reviews.

### Undo a change

1. Run `/cvpilot history` (optionally `limit:<1–10>`) for recent main commit IDs.
2. Run `/cvpilot revert commit:<ID>` using at least 7 characters of the ID.
3. Review the private commit summary and changed files, select **Revert commit**,
   and type **REVERT** within 2 minutes. Only the requesting user can confirm;
   current server permissions are checked again.

Revert adds a new commit reversing one selected commit. It preserves history,
later nonconflicting changes and old downloadable files. It does not restore
the whole repository to that point in time. Conflicts or a changed main stop the
operation and require a fresh request. Initial commits, merge commits, the last
reset commit and commits before that reset are excluded. Wait for your jobs to
finish before requesting a revert. Cancel or let the prompt expire to make no
changes. The command works in the server channel and DMs.

Older job branches remain available as history. Older branch-based source
uploads remain usable until that label is replaced or its main version changes;
new source uploads are saved directly to main.

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
