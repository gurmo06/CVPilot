"""Bounded syntax cleanup for failed agent-generated TeX, never uploaded masters."""
import re


def readable_text(text):
    # Carlito lacks the nonbreaking-hyphen glyph used by some generated drafts.
    # A normal hyphen keeps words readable/extractable without missing-glyph boxes.
    text=text.replace('\u2011','-')
    before,separator,body=text.partition(r'\begin{document}')
    if separator:
        # Numeric percentages in resume prose must not start TeX comments. Leave
        # preamble parameter comments, escaped percentages and URL encodings alone.
        body=re.sub(r'(?<=\d)%(?=\s|$|[+),.;:!?])',lambda _:r'\%',body)
        return before+separator+body
    return text


def _line_syntax(line):
    """Return live code, comment and brace depth, respecting escaped TeX symbols."""
    i=0;depth=0
    while i<len(line):
        if line[i]=='\\':
            i+=2
            continue
        if line[i]=='%':return line[:i],line[i+1:],depth
        if line[i]=='{':depth+=1
        elif line[i]=='}':depth-=1
        i+=1
    return line,'',depth


def normalize(text):
    text=readable_text(text)
    # A generated single-slash \[<length>] after a closing group starts math;
    # the intended row break is \\[<length>]. Require a literal TeX length
    # and end-of-line so genuine display-math delimiters remain untouched.
    text=re.sub(r'(?<=})\\\[(-?(?:\d+(?:\.\d*)?|\.\d+)(?:pt|em|ex|in|cm|mm))\](?=\s*(?:\n|$))',
                lambda match:r'\\['+match[1]+']',text)
    # A literal JSON newline is distinct from real TeX commands such as
    # \newcommand, \newline and \nu. Preserve a deliberately defined \n.
    if not re.search(r'\\(?:newcommand|renewcommand|providecommand)\s*\{\\n\}|\\def\s*\\n\b',text):
        text=re.sub(r'(?<!\\)\\n(?=\s|[}\\])','\n',text)
    # Restore known JSON-escaped table operators. An ampersand in ordinary
    # text must remain TeX-escaped; only parameter-to-parameter table separators
    # are unescaped alignment markers.
    lines=[];in_table=False
    for line in text.splitlines(keepends=True):
        if re.search(r'\\begin\{(?:tabularx|tabular|array)\}',line):in_table=True
        line=re.sub(r'(?<!\\)\\u003[eE]', '>',line)
        if in_table:
            line=re.sub(r'(?<=[0-9}])\s+\\u0026\s+(?=#[1-9])',' & ',line)
        line=re.sub(r'(?<!\\)\\u0026',lambda _:r'\&',line)
        if re.search(r'\\end\{(?:tabularx|tabular|array)\}',line):in_table=False
        lines.append(line)
    text=''.join(lines)
    # A generated "%}" at the end of a macro comments out its closing brace.
    # Restore it only for a recognized top-level command whose sole remaining
    # open group is that command body. Reject an inconsistent overall structure.
    repaired=[];depth=0;command=False;changed=False
    for line in text.splitlines(keepends=True):
        ending='\n' if line.endswith('\n') else ''
        body=line[:-1] if ending else line
        code,comment,delta=_line_syntax(body)
        if depth==0:command=bool(re.match(r'\s*\\(?:newcommand|renewcommand|providecommand)\*?\s*\{',code))
        depth+=delta
        if command and depth==1 and comment.strip()=='}':
            body=code+'}%';depth=0;changed=True
        if depth<0:return text
        if depth==0:command=False
        repaired.append(body+ending)
    return ''.join(repaired) if changed and depth==0 else text


def compile_generated(compile_pdf, task, tex):
    """Try the compiler, then at most one deterministic cleanup/recompile."""
    if tex.is_symlink() or not tex.is_file():raise ValueError('Generated TeX must be a regular file')
    initial=tex.read_text(encoding='utf-8');visible=readable_text(initial)
    if visible!=initial:tex.write_text(visible,encoding='utf-8')
    def checked():
        pdf=compile_pdf(task,tex)
        log=tex.with_suffix('.log')
        if log.is_symlink():raise RuntimeError('Unsafe generated compiler log')
        if log.is_file():
            with log.open(encoding='utf-8',errors='replace') as stream:
                if 'Missing character:' in stream.read(2*1024*1024):
                    raise RuntimeError('Generated PDF has missing font glyphs; the draft was retained and no changes were published.')
        return pdf
    try:return checked()
    except RuntimeError as original_error:
        if tex.is_symlink() or not tex.is_file():raise
        original=tex.read_text(encoding='utf-8')
        repaired=normalize(original)
        if repaired==original:raise
        tex.write_text(repaired,encoding='utf-8')
        try:return checked()
        except RuntimeError as failed:
            raise RuntimeError('Generated TeX still failed validation after one syntax cleanup. '
                'The draft is retained; no changes were published. '+str(failed)) from original_error
