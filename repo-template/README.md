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

Tailoring uses all current uploaded resumes and supported profile facts. It
changes only the application files; masters and the profile remain unchanged.
An empty or outdated profile does not block tailoring. Use `/cvpilot profile sync`
to rebuild the profile separately from the current masters.

Tailoring starts from the closest existing master and makes minimal necessary
edits for the posting. The goal is a readable, well-filled page with supported
accomplishments and their context intact. When text overflows, CVPilot measures
the excess and first rewords wrapped bullets (for example, a short second-line
spillover back onto one line). Refinement preserves bullets, entries, dates and
metrics; it does not delete sections or shrink typography to force a fit.
If a first draft leaves substantial unused space and sources contain omitted
evidence, it requests relevant context back. Sparse sources are not padded with
filler. A one-page count alone is not treated as proof of a good resume.

Each request keeps its permanent job ID (for example, `#20`) and also shows
its current position among all pending CVPilot jobs, the number ahead of it,
and total running/waiting work. Running jobs count ahead of waiting jobs;
finished and failed jobs are excluded. Queue positions are shared across users,
but only aggregate counts are shown for other users. `/cvpilot status` refreshes
these positions and prioritizes your active jobs. Earlier confirmation messages
show the queue snapshot at the time they were sent. No duration estimate is implied.

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
New commit titles identify the operation, master label or application, and
resume formats when relevant. Each commit body lists the exact added, updated
and deleted files. Revert titles keep the original commit ID and explain the
inverse change; their bodies also include the original commit title and full ID.
Each job links to its exact commit; saved downloads continue to retrieve the
files from that commit even after later edits, unless the resume is explicitly
deleted using a delete command below. Unchanged jobs create no extra
commit. GitHub edits to `main` are fetched before work begins. Concurrent edits
are preserved: a job refuses to overwrite a changed `main`, and you can submit
it again against the current files. Interrupted pushes recover from a private
publication journal without rerunning the agent or duplicating the commit.
Existing GitHub protections are respected; direct pushes must be permitted by
the repository's settings. New repositories retain force-push/deletion protection
where supported, without requiring pull-request reviews.

### Delete one resume

- `/cvpilot master delete label:<label>` or `/cvpilot master delete commit:<ID>`
  removes one master source folder, including its paired PDF/TeX and companion files.
- `/cvpilot application delete link:<URL>` or `/cvpilot application delete commit:<ID>`
  removes one tailored application's folder, including PDF, TeX, notes and posting link.
  Use the CVPilot GitHub result link, an application folder/file link, or the original
  posting URL if it identifies exactly one completed application.

Supply exactly one selector. Commit IDs need at least 7 characters and must
identify one resume in your main history after the latest reset. A commit for
an older version cannot delete a newer version: use its current label, link,
or latest commit instead. Ambiguous selections are refused.

Review the private file summary, choose **Delete resume**, and type **DELETE**
within 2 minutes. Only the requesting user with current server access can
confirm. Cancel or let the prompt expire to make no changes. Wait for queued
jobs to finish first; a changed repository or main requires a new preview.
Both commands work in the server channel and DMs, without running an AI job.

Deletion creates one normal commit on main. The files stop being available
through CVPilot downloads or as master inputs. Other resumes and existing
profile facts are kept; use profile edit or sync to review those facts. Git
history and previously sent Discord attachments remain: this is not secure
erasure. Restore a deletion with `/cvpilot revert commit:<deletion-commit-ID>`;
restored files become downloadable again. Re-uploading a master label is also
supported. No account, key, branch, or whole repository is deleted.

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

## Sync verified profile facts

Run `/cvpilot profile sync` after uploading sources. CVPilot reads the source
resumes and updates both the profile and its source records on main. Source
records include the exact SHA-256 of every current PDF/TeX input; verified facts
cite those sources. Existing user-confirmed facts are preserved.

If the profile and sources are already current, the result says no new commit
was created and links to main. An agent that cannot read or update the files,
returns an error, or leaves only the starter profile cannot report a successful
sync. Fix the reported blocker and submit a fresh sync.
