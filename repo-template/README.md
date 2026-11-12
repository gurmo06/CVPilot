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

Tailoring selects, reorders and rewrites relevant evidence from all source
resumes and confirmed profile facts. Existing TeX provides a layout, while the
content is adapted to the posting. The goal is a readable, well-filled page with supported
accomplishments and their context intact. When text overflows, CVPilot measures
the excess and first rewords wrapped bullets (for example, a short second-line
spillover back onto one line). Refinement preserves bullets, entries, dates and
metrics; it does not delete sections or shrink typography to force a fit.
If a first draft leaves substantial unused space and sources contain omitted
evidence, it requests relevant context back. Sparse sources are not padded with
filler. A one-page count alone is not treated as proof of a good resume.
If only a small amount of optional page filling remains and repeated proposals
would damage facts or layout, CVPilot retains the substantially filled, validated
one-page draft with an explicit warning. It still refuses multi-page output or
large losses of useful context.

CVPilot drafts autonomously from all source resumes and confirmed profile facts.
It chooses relevant evidence and truthful wording without a qualification
interview or approval of routine editorial decisions. Notes explain the
selected evidence, keyword coverage and unsupported requirements. Documented
actions can establish a capability without the exact posting keyword; related
experience alone cannot establish an unmentioned tool or qualification.

Tailoring and page refinement do not ask qualification questions or request
additional experience. Unsupported claims stay out; notes describe gaps.
To add experience separately, use `/cvpilot profile edit instructions:<facts>`,
review the exact statement, and type **CONFIRM**. Only your statement becomes a
profile fact; source resumes remain unchanged. After the save completes,
`/cvpilot continue job_id:<id>` queues a new version using current evidence and
saved facts, keeping the original application and job history.
Older jobs paused as `needs-input` can continue without answers and retain their
original ID. `/cvpilot answer` remains available for archived questions from the
older workflow. The same permission checks apply in server commands and DMs;
a normal revert can undo a profile commit.

Each request keeps its permanent job ID (for example, `#20`) and also shows
its current position among all pending CVPilot jobs, the number ahead of it,
and total running/waiting work. Running jobs count ahead of waiting jobs;
finished and failed jobs are excluded. Queue positions are shared across users,
but only aggregate counts are shown for other users. `/cvpilot status` refreshes
the selected job's position and defaults to your newest job. Supply `job_id` to
select a job directly, or use the older/newer arrows to browse your history.
Refresh updates that one job; its view includes timing, checkpoints, errors and
an attached full report. Earlier confirmation messages show the queue snapshot
at the time they were sent. No duration estimate is implied.

There is no fixed list of career categories. Labels are user-defined lowercase
names (letters, numbers, hyphens; start with a letter; up to 40 characters).
Reusing a label explicitly replaces its active uploaded version; earlier
versions remain in Git history. Upload a matching PDF/TeX pair together so the
bot does not combine files from different versions.
Uploading under a different label removes the untouched `general` starter
folder; uploaded resumes and customized files in `general` are preserved.

## Files and commit history

- `master-resumes/<label>/resume.pdf` and/or `resume.tex`: source resumes.
- `profile/MASTER_PROFILE.md` and `profile/profile_sources.json`: verified facts
  and their sources, including work, education, certifications and volunteering.
- `applications/YYYY/job-<id>-<company>-<position>/{resume.tex,resume.pdf,job-url.txt}`:
  tailored output, for example `job-40-t-mobile-software-engineering-internship`.
  Names use bounded filename-safe slugs from the posting, keeping the unique job
  ID. Unavailable details are omitted. Earlier `job-<id>` folders remain usable.

Successful uploads, resume edits, tailored applications and profile changes
commit directly to `main` after validation. CVPilot creates no per-job branches.
The operator can configure an owned GitHub bot and optional co-authors in private
runtime configuration. Commits use that bot's display name and ID-based GitHub
noreply address; co-author credit is limited to explicitly configured users.
Without this configuration, the fallback is `CVPilot <cvpilot@cvpilot.invalid>`,
an unlinked automation label that does not provide GitHub contribution credit.
Never invent an address under `users.noreply.github.com` or assume that setting
a Git author name creates an account. Registration email addresses stay private.
Changing the identity for future commits leaves existing commit IDs unchanged.
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
  posting URL if it identifies exactly one current application.
  Deleted folders are excluded when matching a posting URL. If several current
  applications use that URL, CVPilot shows their GitHub folder links so you can
  choose one explicitly. Reverting a deletion makes that folder eligible again.

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

## Conversation and detailed profile

DM CVPilot or mention it in the designated server channel. Chat and slash
commands share one private conversation per user; file operations use the same
validated controller and commits to main. Chat responses and files arrive by DM.
Commands keep their existing confirmation previews. In chat, type the exact
confirmation word after the preview; the model cannot confirm for you.

`/cvpilot context operation:show` displays usage. Each user starts with a 24,000
conservative token budget (UTF-8 byte upper bound for conversation history,
separate from system/source prompt overhead). At the limit, choose
`operation:compact` or `operation:wipe`. Compaction may omit detail and reduce
accuracy; original transcripts remain archived privately. A wipe starts a fresh
active generation. Both preserve the master profile, resumes and Git history.
Idle conversation working sets leave CVPilot's RAM cache after five minutes and
reload from private SQLite on activity. This does not promise eviction of model
weights, provider KV caches or the operating system's disk cache.

Owner diagnostics in the configured admin channel include `/openclaw contexts`
with optional `user_id` and `limit`. It lists residency, limits, usage and pending
chat turns; an explicit target and limit update only that user's context budget.

The master profile is a detailed evidence database, not a shortened resume.
Profile sync extracts all source documents in bounded passes, keeps verbatim
evidence and full extracted source text, and preserves historical and confirmed
records. It never reduces the profile to satisfy a resume page limit. User facts
from chat must be reviewed and confirmed before being saved. Tailoring retrieves
relevant records and keeps the profile read-only. Source text and older context
archives remain private; the public sync script copies source code only.
