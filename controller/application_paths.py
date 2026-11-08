"""Worker-owned application names; old job-ID-only folders remain valid."""
import re
import time
import unicodedata
from pathlib import Path

COMPONENT=r'job-[0-9]+(?:-[a-z0-9]+)*'
DIRECTORY=r'applications/[0-9]{4}/'+COMPONENT


def slug(value,limit):
    if not isinstance(value,str):return ''
    value=unicodedata.normalize('NFKD',value).encode('ascii','ignore').decode().lower()
    value=re.sub(r'[^a-z0-9]+','-',value).strip('-')
    if value in ('unknown','n-a','none','not-specified','not-provided','unavailable'):return ''
    return value[:limit].rstrip('-')


def new_directory(jid,notes='',posting=None):
    if type(jid) is not int or jid<1:raise ValueError('Invalid application job ID')
    fields={}
    for line in notes.splitlines():
        plain=line.replace('**','').replace('`','').strip().lstrip('- ')
        match=re.fullmatch(r'(company|position|job title|title)\s*:\s*(.+)',plain,re.I)
        if match:fields.setdefault(match[1].lower(),match[2].strip())
    posting=posting if isinstance(posting,dict) else {}
    company=slug(posting.get('company') or fields.get('company',''),40)
    position=slug(posting.get('title') or fields.get('position') or fields.get('job title') or fields.get('title',''),80)
    name='-'.join([f'job-{jid}']+[part for part in (company,position) if part])
    return Path('applications')/str(time.gmtime().tm_year)/name


def existing_directory(task,jid):
    """Keep one stable folder on retries/recovery, including legacy drafts."""
    root=task/'applications'
    if root.is_symlink():raise RuntimeError('Unsafe tailored resume output directory')
    found=[]
    for year in root.glob('[0-9][0-9][0-9][0-9]'):
        if year.is_symlink():raise RuntimeError('Unsafe tailored resume output directory')
        for folder in year.glob(f'job-{jid}*'):
            if not re.fullmatch(COMPONENT,folder.name) or folder.name.split('-',2)[1]!=str(jid):continue
            if folder.is_symlink():raise RuntimeError('Unsafe tailored resume output directory')
            if folder.is_dir():found.append(folder.relative_to(task))
    if len(found)>1:raise RuntimeError('Multiple application folders match this job; refusing ambiguous output')
    return found[0] if found else None
