# Source resumes for every field

Use any resume labels you find useful. `general` is the default, not a required
profession. Labels use lowercase letters, numbers and hyphens (up to 40
characters), starting with a letter. Existing labels remain usable.

Upload from Discord:
`/cvpilot master upload file:<PDF or TeX> label:<your-label> companion:<optional matching file>`

Download a source resume:
`/cvpilot master get label:<your-label>`

Reuse a label to explicitly replace its active uploaded version. Upload a PDF
and its matching TeX together with `companion`; a new PDF-only upload does not
reuse an older TeX file. Earlier versions are retained in Git history.

GitHub uploads are also supported at `master-resumes/<label>/resume.pdf` and
`resume.tex` on `main`. Resume sources may be multiple pages. Tailored output is
validated as a one-page PDF. PDF-only sources are supported with a clean new
layout; a matching TeX source is best for preserving an existing layout.
