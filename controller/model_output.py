"""Read named model output without treating optional prose as executable content."""
import re

FENCE=re.compile(r'^(`{3,}|~{3,})[ \t]*([^\r\n]*?)\s*$')


class OutputBlocksError(ValueError):pass


def label(line,labels):
    if FENCE.fullmatch(line.strip()):return None
    value=re.sub(r'^#{1,6}\s*','',line.strip()).strip('*_` \t:').casefold()
    return value if value in labels else None


def has_blocks(text,labels):
    for line in text.splitlines():
        opening=FENCE.fullmatch(line.strip())
        if label(line,labels) or (opening and opening[2].casefold() in labels):return True
    return False


def named_blocks(text,primary,notes='cvpilot-notes',ignored=()):
    """Require a closed named TeX fence; accept notes as Markdown or at EOF.

    Headings followed by tex/latex fences are equivalent to named fences.
    Ignored legacy metadata is never parsed, required, or included in notes.
    """
    labels={primary,notes,*ignored};lines=text.splitlines(keepends=True)
    blocks={};pending=None;i=0
    def put(name,value):
        if name in ignored:return
        if name in blocks:raise OutputBlocksError('Duplicate output blocks')
        blocks[name]=value
    while i<len(lines):
        line=lines[i].strip();heading=label(line,labels)
        if heading:
            pending=heading;i+=1;continue
        opening=FENCE.fullmatch(line)
        if opening:
            fence,info=opening.groups();name=info.casefold() if info.casefold() in labels else None
            if name is None and pending:
                languages=('', 'tex','latex') if pending==primary else ('','text','md','markdown','json')
                if info.lower() in languages:name=pending
            pending=None;i+=1;start=i;closed=False
            while i<len(lines):
                candidate=lines[i].strip()
                if re.fullmatch(re.escape(fence[0])+'{'+str(len(fence))+',}[ \t]*',candidate):
                    closed=True;break
                other=FENCE.fullmatch(candidate)
                next_label=other[2].casefold() if other else label(candidate,labels)
                if name and next_label in labels:
                    if name==primary:raise OutputBlocksError('Resume fence is not closed before the next block')
                    break
                i+=1
            if name==primary and not closed:
                raise OutputBlocksError('Return a complete, closed '+primary+' TeX block')
            if name:put(name,''.join(lines[start:i]))
            if closed:i+=1
            continue
        if pending in labels-{primary} and line:
            start=i
            while i<len(lines):
                candidate=lines[i].strip();other=FENCE.fullmatch(candidate)
                if label(candidate,labels) or (other and other[2].casefold() in labels):break
                i+=1
            put(pending,''.join(lines[start:i]));pending=None;continue
        if line:pending=None
        i+=1
    if not blocks.get(primary,'').strip():
        raise OutputBlocksError('Return one complete, labelled '+primary+' TeX block')
    return blocks
