# Career resumes

One **private GitHub repository per Discord user**.

`main` is manually maintained and protected. The bot reads `main` and pushes
only `careeradvisor/*` branches, which are reviewed as PRs before merging.

- `master-resumes/{swe,ml,systems,it}/resume.tex` and compiled resume.pdf
- `profile/MASTER_PROFILE.md` and `profile/profile_sources.json`
- `applications/YYYY/company/role/task-id/{resume.tex,resume.pdf,job-url.txt}`

Missing master resumes are intentionally empty placeholders until populated.
Keep `.tex` and `.pdf` together. PDF build artifacts are ignored by `.gitignore`
except final PDFs deliberately staged for commit.
