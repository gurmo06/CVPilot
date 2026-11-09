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

Delete a source with `/cvpilot master delete label:<your-label>` or
`/cvpilot master delete commit:<ID>` (supply one selector). Review the files,
choose Delete resume and type DELETE within 2 minutes. This removes that label's
entire current folder from main and CVPilot downloads, including its PDF/TeX
pair and companion files. A commit must identify one current, unchanged resume;
use the label or latest commit if the source has since changed. Existing profile
facts remain; review them with profile edit or sync. Git history and previously
sent Discord attachments remain. Restore with `/cvpilot revert` using the
deletion commit, or upload a new source under that label.

A Discord TeX-only upload automatically compiles to PDF in an isolated compiler;
both files become available through `master get` and `download` after the upload
job completes. Sources may have multiple pages. Use self-contained UTF-8 TeX
with packages available in the compiler; external assets and custom
class/style files cannot be attached. On compilation failure the upload fails
and your previous active resume stays unchanged. Correct the source and upload
again, or provide its matching PDF using `companion`. A supplied PDF/TeX pair
is saved as provided without recompilation. Generated PDFs must fit 8 MiB.
CVPilot selects XeLaTeX for `fontspec` or `unicode-math` sources and pdfLaTeX
otherwise. An explicit `% !TeX program = pdflatex`, `xelatex`, or `lualatex`
directive selects that engine. Carlito is included in the compiler image.

GitHub uploads are also supported at `master-resumes/<label>/resume.pdf` and
`resume.tex` on `main`. Resume sources may be multiple pages. Tailored output is
validated as a one-page PDF. PDF-only sources are supported with a clean new
layout; a matching TeX source is best for preserving an existing layout.
Initial tailoring selects and adapts relevant evidence across all masters and
confirmed profile facts while preserving useful context. Later page-fit revisions target wrapped lines through rewording,
retaining bullets, sections and achievements. Underfilled drafts may restore
relevant source details; the bot must not pad sparse sources or delete large
amounts of context just to obtain a one-page count.
Automatic upload compilation applies to the Discord upload command; editing
files directly on GitHub does not trigger an upload job.
