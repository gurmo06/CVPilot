"""Private, bounded job diagnostics. Never return raw agent configuration/reports."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit, urlunsplit


def clean(text):
    text=str(text or '')
    for name,value in os.environ.items():
        if re.search(r'TOKEN|PASSWORD|SECRET|API_KEY',name) and len(value)>=8:
            text=text.replace(value,'[redacted]')
    text=re.sub(r'(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|sk-[A-Za-z0-9_-]{16,})','[redacted]',text)
    text=re.sub(r'(?i)(authorization\s*[:=]\s*)(?:bearer|basic)\s+\S+',r'\1[redacted]',text)
    text=re.sub(r'(?i)((?:token|password|secret|api[_-]?key)\s*[=:]\s*)[^\s,;]+',r'\1[redacted]',text)
    text=re.sub(r'https?://[^\s<>`]+',lambda m:safe_url(m[0]),text)
    text=re.sub(r'/home/[^\s\'"<>`]+','[private host path]',text)
    return ''.join(char for char in text if char in '\n\t' or ord(char)>=32)


def safe_url(value):
    try:
        u=urlsplit(value)
        if u.scheme not in ('http','https') or not u.hostname:return '[invalid URL]'
        return urlunsplit((u.scheme,u.hostname+(':'+str(u.port) if u.port else ''),u.path,'',''))
    except ValueError:return '[invalid URL]'


def private_json(root, category, job):
    uid=job['discord_id'];jid=job['id']
    if not re.fullmatch(r'[0-9]{16,22}',uid) or type(jid) is not int or jid<1:return {}
    path=Path(root)/category/uid/f'{jid}.json'
    try:
        if any(p.is_symlink() for p in (path,path.parent,path.parent.parent)):return {}
        if not path.is_file() or path.stat().st_size>2*1024*1024:return {}
        value=json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError,UnicodeError):return {}


def agent_explanation(report):
    payloads=report.get('payloads',[])
    texts=[item['text'] for item in payloads if isinstance(item,dict) and isinstance(item.get('text'),str)] if isinstance(payloads,list) else []
    if not texts and isinstance(report.get('final'),str):texts=[report['final']]
    text='\n\n'.join(texts).strip()
    # Profile JSON contains source documents, not a user-facing explanation.
    if text.startswith(('{','```json')):return 'The agent returned structured data; see the validation error for why it was rejected.'
    return clean(text[:16000])+('\n[Agent explanation exceeds 16,000 characters; remainder omitted.]' if len(text)>16000 else '')


def missing_output(relative, report):
    explanation=agent_explanation(report)
    return ('Tailoring stopped before creating a resume. '+
            ('Agent explanation: '+explanation[:1100]+' ' if explanation else '')+
            f'Expected output: {relative}. No files from this job were published.')


def progress(root, job, stage):
    """A best-effort checkpoint, separate from persistent queue state."""
    uid=job['discord_id'];jid=job['id']
    if not re.fullmatch(r'[0-9]{16,22}',uid) or type(jid) is not int or jid<1:return
    directory=Path(root)/'job-progress'/uid
    try:
        if directory.is_symlink() or directory.parent.is_symlink():return
        directory.mkdir(parents=True,exist_ok=True,mode=0o700);directory.chmod(0o700)
        path=directory/f'{jid}.json'
        if path.is_symlink():return
        record={'job_id':jid,'kind':job['kind'],'stage':stage,
                'updated_at':datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=directory,delete=False) as stream:
            temp=Path(stream.name);json.dump(record,stream);stream.flush()
        try:os.replace(temp,path)
        finally:temp.unlink(missing_ok=True)
    except OSError:
        # Diagnostics must never prevent publication of a validated resume.
        pass


def timestamp(value):
    try:return int(datetime.strptime(value,'%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc).timestamp())
    except (TypeError,ValueError):return None


def duration(seconds):
    seconds=max(0,int(seconds));hours,rest=divmod(seconds,3600);minutes,seconds=divmod(rest,60)
    return (f'{hours}h ' if hours else '')+(f'{minutes}m ' if minutes or hours else '')+f'{seconds}s'


def detail(root, snapshot):
    from queue_status import load_text,position_text
    job=snapshot['job'];jid=job['id'];state=job['state'];history=snapshot['history']
    result=json.loads(job['result'] or '{}');payload=json.loads(job['payload'] or '{}')
    report=private_json(root,'job-reports',job);checkpoint=private_json(root,'job-progress',job)
    if checkpoint.get('job_id')!=jid or checkpoint.get('kind')!=job['kind']:checkpoint={}
    fields=[];report_lines=[];budget=5400
    def add(name,value,limit=1024,record=True):
        nonlocal budget
        value=clean(value)
        if not value:return
        if record:report_lines.extend([name+':',value,''])
        shown=value[:min(limit,budget-len(name)-1)]
        if len(shown)<len(value):shown=shown[:max(0,len(shown)-45)]+'\n[Continued in attached status report.]'
        if shown and budget>len(name)+50:
            fields.append({'name':name,'value':shown,'inline':False});budget-=len(name)+len(shown)
    now=int(datetime.now(timezone.utc).timestamp())
    title=f'CVPilot job #{jid} — {job["kind"]}: {state}'
    timing=[]
    for name,key in (('Queued','created_at'),('Started','started_at'),('Finished','finished_at')):
        value=timestamp(job[key]);timing.append(f'{name}: <t:{value}:F>' if value is not None else f'{name}: —')
    start=timestamp(job['started_at']);end=timestamp(job['finished_at'])
    if start is not None:timing.append(('Run time: ' if end is not None else 'Running for: ')+duration((end or now)-start))
    if state=='queued':timing.append('Waiting for: '+duration(now-(timestamp(job['created_at']) or now)))
    add('Timing','\n'.join(timing))
    add('Queue',load_text(snapshot)+'\n'+(position_text(job,snapshot) if state in ('queued','running') else 'This job no longer occupies the queue.')+f'\nYour pending jobs: {snapshot["own_pending"]}.')
    stage=checkpoint.get('stage')
    if stage:add('Progress' if state in ('queued','running') else 'Last checkpoint',stage+f'\nUpdated: {checkpoint.get("updated_at")} UTC')
    elif state=='queued':add('Progress','Waiting for the worker to claim this job.')
    elif state=='running':add('Progress','Worker is processing this job. No stage checkpoint is available for this older run.')
    fit=private_json(root,'job-refinements',job)
    if fit.get('job_id')==jid and fit.get('kind')=='tailor':
        measurement=fit.get('best_metrics') or fit.get('metrics') or {}
        target=(f'Restore about {measurement["fill_target_lines"]} rendered lines of source-supported context; the page is underfilled.'
                if measurement.get('fill_target_lines') else
                f'Recover {measurement.get("target_lines",0)} rendered wraps through rewording; preserve bullets and context.')
        add('Page fit',f'Draft/refinement round: {fit.get("round",0)}. State: {fit.get("status","unknown")}.\n'
            f'Best PDF: {measurement.get("pages","?")} page(s). Overflow rows: {measurement.get("overflow_lines",0)}.\n'
            f'Current target: {target} Fonts and layout remain fixed.'+
            ('\n'+fit['feedback'] if fit.get('feedback') else ''))
    if job.get('error'):
        error=clean(job['error'])
        report_lines.extend(['Error:',error,''])
        # Preserve the full stored error in the embed, split across Discord fields.
        for offset in range(0,len(error),1000):add('Error' if offset==0 else 'Error (continued)',error[offset:offset+1000],record=False)
        explanation=agent_explanation(report)
        if explanation:add('Agent explanation (reported by the model)',explanation,900)
        if job['kind']=='tailor' and ('did not create' in error or 'before creating a resume' in error):
            add('What happened','The worker received no required resume.tex, so compilation and publication did not run. If the agent reports an inaccessible posting, retry with an accessible public job-listing URL. Status does not retry jobs.')
        elif job['kind'] in ('tailor','master-edit','master-upload') and re.search(r'latex|compil|PDF must',error,re.I):
            add('What happened','Resume compilation or PDF validation failed. Check the compiler error and source line above; a successful model response alone does not mean a PDF was created.')
    if state=='done':
        add('Result',result.get('note','Job completed.'))
        if result.get('url'):add('GitHub',safe_url(result['url']))
        if result.get('commit'):add('Commit',result['commit'])
        if result.get('artifacts'):
            add('Resume files','\n'.join(str(item.get('path','')) for item in result['artifacts'] if isinstance(item,dict))+f'\nRetrieve with /cvpilot download job_id:{jid}. Downloads follow current deletion/reset rules.')
    if payload.get('url'):add('Requested posting',safe_url(payload['url']),500)
    if payload.get('role'):add('Resume label',payload['role'])
    if payload.get('instructions'):add('Requested changes',payload['instructions'],500)
    meta=report.get('meta') or {};agent=meta.get('agentMeta') or {}
    model=agent.get('model');ms=meta.get('durationMs')
    if model or isinstance(ms,(int,float)):
        add('Agent run',(f'Model: {model}\n' if model else '')+(f'Agent time: {duration(ms/1000)}' if isinstance(ms,(int,float)) else ''))
    description=f'Your job {history["ordinal"]} of {history["count"]} (newest first). Updated <t:{now}:R>.'
    fallback=[title,load_text(snapshot)]
    if job['error']:fallback.extend([clean(job['error'])[:700],agent_explanation(report)[:500]])
    elif result.get('url'):fallback.append(safe_url(result['url']))
    elif state in ('queued','running'):fallback.append(position_text(job,snapshot))
    full_report='\n'.join([title,description,'']+report_lines)
    full_report=re.sub(r'<t:([0-9]+):[FR]>',lambda m:datetime.fromtimestamp(int(m[1]),timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC'),full_report)
    return {'text':'\n'.join(filter(None,fallback))[:1900],
            'status_detail':{'title':title,'description':description,'fields':fields,
                             'report':full_report,
                             'state':state},
            'status_view':{'job_id':jid,**history}}
