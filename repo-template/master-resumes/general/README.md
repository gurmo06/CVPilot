# General resume

Upload your source resume with `/cvpilot master upload file:<PDF or TeX>`.
The default label is `general`. Add `companion:<matching PDF or TeX>` to keep a
matching pair together. Each file may be up to 8 MiB.
A TeX-only Discord upload automatically generates a PDF. Retrieve both files
with `/cvpilot master get`; multi-page masters are supported. Use self-contained
TeX source; `fontspec` sources automatically use XeLaTeX. Supply a matching PDF
using `companion` to skip compilation.

You can also place `resume.pdf` and/or UTF-8 `resume.tex` here on `main`.
CVPilot supports every profession and field of study. For additional versions,
choose your own lowercase label, such as `teaching`, `healthcare`, `sales`,
`creative`, `academic`, or `volunteer`; none of these labels is required.
