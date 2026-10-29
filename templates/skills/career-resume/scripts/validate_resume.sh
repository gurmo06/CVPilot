#!/usr/bin/env bash
set -euo pipefail
pdf="${1:?Usage: validate_resume.sh <pdf>}"
[[ -s "$pdf" ]] || { echo "Missing or empty PDF: $pdf" >&2; exit 1; }
command -v pdfinfo >/dev/null || { echo 'Install poppler-utils (pdfinfo)' >&2; exit 1; }
pages=$(pdfinfo "$pdf" | awk '/^Pages:/ {print $2}')
if [[ "$pages" != 1 ]]; then
  echo "FAIL: expected exactly one page, got ${pages:-unknown}: $pdf" >&2
  exit 1
fi
if ! pdftotext "$pdf" - | grep -q '[[:alnum:]]'; then
  echo "FAIL: no extractable text in PDF: $pdf" >&2
  exit 1
fi
echo "PASS: exactly one PDF page and extractable text: $pdf"
