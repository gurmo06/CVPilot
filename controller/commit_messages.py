"""Describe trusted staged changes without asking a model or reading resume content."""
import json
from pathlib import PurePosixPath
import re
import tempfile
from urllib.parse import urlsplit, urlunsplit


def github_identity(record):
    """Use a verified account ID/login pair, never a guessed username email."""
    if not isinstance(record,dict):raise ValueError('Invalid private Git identity record')
    name=record.get('display_name');login=record.get('github_username');account_id=record.get('github_id')
    if (not isinstance(name,str) or not name.strip() or len(name)>160
            or any(ord(c)<32 or ord(c)==127 or c in '<>' for c in name)
            or not isinstance(login,str) or not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?',login)
            or type(account_id) is not int or account_id<=0):
        raise ValueError('Private Git identity needs a display name and verified GitHub username/account ID')
    return {'name':name.strip(),'email':f'{account_id}+{login}@users.noreply.github.com'}


def identities(w):
    """Private operator configuration stays outside source and model input."""
    path=w.ROOT/'git-identity.json'
    if path.is_symlink():raise ValueError('Private Git identity configuration must be a regular file')
    if not path.exists():return {'name':'CVPilot','email':w.GIT_IDENTITY_EMAIL},{}
    if not path.is_file() or path.stat().st_size>1024*1024:
        raise ValueError('Private Git identity configuration is invalid')
    try:config=json.loads(path.read_text(encoding='utf-8'))
    except (ValueError,UnicodeError) as exc:raise ValueError('Private Git identity configuration contains invalid JSON') from exc
    if not isinstance(config,dict) or config.get('schema_version')!=1:
        raise ValueError('Unsupported private Git identity configuration')
    bot=github_identity(config.get('bot'));configured=config.get('coauthors',{})
    if (not isinstance(configured,dict) or any(not re.fullmatch(r'[0-9]{16,22}',uid) for uid in configured)):
        raise ValueError('Private Git co-authors must be bound to explicit Discord user IDs')
    return bot,{uid:github_identity(record) for uid,record in configured.items()}


def configure_identity(w, uid, repo):
    bot,_=identities(w)
    w.run_git(uid,repo,'config','user.name',bot['name'])
    w.run_git(uid,repo,'config','user.email',bot['email'])


def coauthor(w, uid):
    _,users=identities(w)
    record=users.get(uid)
    return f'{record["name"]} <{record["email"]}>' if record else None


def commit(w, uid, repo, message, *, allow_empty=False):
    # Long file lists can exceed the operating system's single-argument limit.
    # Keep metadata outside the checkout and remove the private file afterward.
    with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',prefix='cvpilot-commit-',suffix='.txt') as file:
        file.write(message);file.flush()
        return w.run_git(uid,repo,'commit',*(['--allow-empty'] if allow_empty else []),
                         '--cleanup=verbatim','--file',file.name)


def single_line(value, limit=180):
    text=re.sub(r'[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]', ' ', str(value))
    text=' '.join(text.split())
    return text if len(text)<=limit else text[:limit-3]+'...'


def staged_changes(w, uid, repo):
    # Disable rename inference so every path is explicitly added, updated or deleted.
    fields=w.run_git(uid,repo,'diff','--cached','--name-status','--no-renames','-z').split('\0')
    if fields and not fields[-1]:fields.pop()
    if len(fields)%2:raise ValueError('Invalid staged change listing')
    return [(fields[i],fields[i+1]) for i in range(0,len(fields),2)]


def display_path(path):
    # Escape unusual filenames rather than letting them forge body lines/trailers.
    return path.encode('unicode_escape').decode('ascii') if any(ord(c)<32 or ord(c)==127 for c in path) else path


def posting_url(value):
    try:
        url=urlsplit(value)
        if url.scheme!='https' or not url.hostname:return ''
        # Tracking/query strings, fragments and URL credentials do not belong in metadata.
        return single_line(urlunsplit(('https',url.hostname,url.path,'','')),350)
    except (TypeError,ValueError):return ''


def resume_formats(changes):
    extensions={PurePosixPath(path).suffix.lower() for _,path in changes}
    return ' + '.join(name for extension,name in (('.pdf','PDF'),('.tex','TeX')) if extension in extensions)


def inverse_summary(changes):
    groups={}
    for status,path in changes:
        parts=path.split('/')
        if len(parts)>=3 and parts[0]=='master-resumes':name=f'master {parts[1]}'
        elif len(parts)>=4 and parts[0]=='applications':name=f'application {parts[2]}'
        elif parts[0]=='profile':name='profile'
        else:name='file '+single_line(path,65)
        groups.setdefault(name,set()).add(status)
    phrases=[]
    for name,statuses in groups.items():
        verb='restore' if statuses=={'A'} else 'remove' if statuses=={'D'} else 'restore previous' if statuses=={'M'} else 'update'
        phrases.append(f'{verb} {name}')
    return ', '.join(phrases[:2])+(f' and {len(phrases)-2} more areas' if len(phrases)>2 else '')


def build(w, uid, repo, kind, jid, payload=None, draft=None, *, coauthor=None):
    payload=payload or {};draft=draft or {};changes=staged_changes(w,uid,repo)
    formats=resume_formats(changes)
    label=single_line(draft.get('label') or payload.get('role') or payload.get('label') or 'general',40)
    details=[]
    if kind=='master-upload':
        # A PDF-only replacement may also delete an old TeX file. Describe the
        # uploaded formats in the title; list the TeX removal in the body.
        formats=resume_formats([('',item.get('path','')) for item in draft.get('artifacts',[])]) or formats
        replaced=any(status!='A' and path.startswith(f'master-resumes/{label}/') for status,path in changes)
        summary=f'{"Replace" if replaced else "Upload"} master {label}'
        if formats:summary+=f' ({formats})'
    elif kind=='master-edit':summary=f'Edit master {label}'+(f' ({formats})' if formats else '')
    elif kind=='master-delete':
        label=single_line(draft.get('deleted_directory',payload.get('directory','')).rsplit('/',1)[-1],40)
        summary=f'Delete master {label}'+(f' ({formats})' if formats else '')
    elif kind in ('tailor','application-delete'):
        folders=sorted({path.rsplit('/',1)[0] for _,path in changes if path.startswith('applications/')})
        folder=draft.get('deleted_directory') or payload.get('directory') or (folders[0] if folders else f'job-{jid}')
        name=single_line(folder.rsplit('/',1)[-1],40)
        summary=f'{"Tailor" if kind=="tailor" else "Delete"} application {name}'+(f' ({formats})' if formats else '')
        url=posting_url(payload.get('url',''))
        if url:details.append(f'Job posting: {url}')
    elif kind=='profile-sync':summary='Sync profile and source records from master resumes'
    elif kind=='profile-edit':summary='Update verified profile facts'
    elif kind=='profile-confirm':
        summary='Save user-confirmed '+single_line(payload.get('term','experience'),65)
        details.extend([(f'Follow-up to job {payload.get("source_job")}, question {payload.get("question")}.' if payload.get('source_job') is not None else 'User explicitly reviewed and confirmed this profile statement.'),
                        'Confirmed statement: '+single_line(payload.get('statement',''),1200)])
    elif kind=='init':summary='Initialize CVPilot workspace'
    elif kind=='reset':
        summary='Reset workspace to initial template'
        details.append('Replace main files with the current initial CVPilot template.')
    elif kind=='revert':
        oid=draft.get('reverted_commit','')
        if not re.fullmatch(r'(?:[a-f0-9]{40}|[a-f0-9]{64})',oid):raise ValueError('Invalid reverted commit identity')
        effect=inverse_summary(changes)
        summary=f'Revert {oid[:12]}: {effect}'
        original=w.run_git(uid,repo,'show','-s','--format=%s',oid)
        details.extend([f'Reverts commit: {oid}',f'Original change: {single_line(original,240)}',f'Result: {single_line(effect,240)}.'])
    else:summary=single_line(kind.replace('-',' ').capitalize())
    subject=f'CVPilot: {single_line(summary,120)} (job {jid})'
    instructions=payload.get('instructions')
    if instructions:details.append('Requested change: '+single_line(instructions,350))
    counts={code:sum(status==code for status,_ in changes) for code in ('A','D')}
    updated=len(changes)-counts['A']-counts['D']
    details.append(f'Files: {counts["A"]} added, {updated} updated, {counts["D"]} deleted.')
    for status,path in changes:
        action={'A':'Added','D':'Deleted'}.get(status,'Updated')
        details.append(f'{action}: {display_path(path)}')
    message=subject+'\n\n'+'\n'.join(details)
    return message+('\n\nCo-authored-by: '+coauthor if coauthor else '')
