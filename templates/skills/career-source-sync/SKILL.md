---
name: career-source-sync
description: Sync confirmed resume facts and source provenance in CVPilot review worktrees
user-invocable: true
---
Read all master resume TeX/PDFs in task worktree and update only profile/MASTER_PROFILE.md and profile/profile_sources.json, except when an explicit trusted master-edit job allows modification of one role's master. Merge facts from all sources. Never drop user-confirmed skills just because absent from a resume. Never add unverified facts. Do not commit/push or change main. The trusted controller publishes reviewed changes.
