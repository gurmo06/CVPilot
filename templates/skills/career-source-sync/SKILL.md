---
name: career-source-sync
description: Sync confirmed resume facts and source provenance in CVPilot task snapshots
user-invocable: true
---
This skill applies to explicit profile-sync/profile-edit or authorized
master-edit jobs. A tailoring or page-refinement job must keep the profile and
masters read-only and follow its own resume-output protocol. Merge supported
facts from all supplied sources. Never drop user-confirmed skills just because
absent from a resume. Never add unverified facts. Do not commit/push or change
main; the trusted controller publishes validated changes.


Include evidence for any career or field of study: professional and practical
skills, credentials, work, education, portfolios and community contributions.
Do not assume software or computer-science experience. Labels are user-defined.

For profile-sync jobs, the worker supplies one bounded source excerpt per pass
and requests detailed factual records with exact contiguous evidence quotes.
Follow that pass's JSON schema. Do not use tools, run commands, edit files or
return a rewritten profile summary. Preserve responsibilities, methods, scope,
results/metrics, dates, tools and project/role context. Record documented
actions in full even when they do not name a job keyword; do not invent skills,
products, proficiency or credentials from merely related experience. The controller merges all passes into the durable evidence
database, retains complete source text and preserves existing confirmed and
historical records. There is no page or brevity goal for the master profile.

Only a trusted file-editing job without inline inputs may use the task worktree.
Profile edits change only profile/MASTER_PROFILE.md and profile/profile_sources.json;
an explicitly authorized master-edit may also change its stated source label.
