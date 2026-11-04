---
name: career-source-sync
description: Sync confirmed resume facts and source provenance in CVPilot task snapshots
user-invocable: true
---
Read all master resume TeX/PDFs in task worktree and update only profile/MASTER_PROFILE.md and profile/profile_sources.json, except when an explicit trusted master-edit job allows modification of one user-defined label's source resume. Merge facts from all sources. Never drop user-confirmed skills just because absent from a resume. Never add unverified facts. Do not commit/push or change main. The trusted controller commits validated changes directly to main.


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
