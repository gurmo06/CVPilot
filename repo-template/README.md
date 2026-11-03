# CVPilot workspace

One **private GitHub repository per Discord user**.

`main` is manually maintained; branch protection depends on the GitHub plan. The bot reads `main` and pushes only `cvpilot/<job-kind>/<job-id>` branches, which are reviewed as PRs before merging.

- `master-resumes/{swe,ml,systems,it,web}/resume.tex` and compiled resume.pdf
- `profile/MASTER_PROFILE.md` and `profile/profile_sources.json`
- `applications/YYYY/job-<job-id>/{resume.tex,resume.pdf,job-url.txt}`

Missing master resumes are intentionally empty placeholders until populated.
Keep `.tex` and `.pdf` together. PDF build artifacts are ignored by `.gitignore`
except final PDFs deliberately staged for commit.
