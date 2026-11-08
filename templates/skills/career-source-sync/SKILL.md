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

For profile-sync jobs, the trusted worker supplies a JSON input bundle inline:
readable TeX (or sandbox-extracted PDF text), exact source hashes, and the current
profile. Return only the requested structured extraction JSON. Do not use tools,
run commands, or edit files for that job kind; the trusted worker writes the two
profile files after parsing and validating the response. Review every supplied
document, merge duplicate facts and cite their matching source paths/hashes.
Preserve existing user-confirmed facts without creating new confirmations.
An empty starter profile is never an unchanged successful result. Use the
specified blocked result when source evidence cannot be processed.

Only a trusted file-editing job without inline inputs may use the task worktree.
Profile edits change only profile/MASTER_PROFILE.md and profile/profile_sources.json;
an explicitly authorized master-edit may also change its stated source label.
