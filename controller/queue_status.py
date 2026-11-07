"""Private job acknowledgements and status with current shared-queue positions."""
import json


def load_text(snapshot):
    return f'Queue load: {snapshot["running"]} running, {snapshot["waiting"]} waiting ({snapshot["total"]} pending).'


def position_text(job, snapshot):
    if job['state']=='queued':
        return f'Queue position {job["position"]} of {snapshot["total"]} pending; {job["position"]-1} ahead.'
    if job['state']=='running':return 'Running now.'
    if job['state']=='done':return 'Already completed; check /cvpilot status for the result.'
    return f'Current state: {job["state"]}. Check /cvpilot status.'


def acknowledgement(store, uid, jid, text):
    snapshot=store.queue_snapshot(uid,jid);job=snapshot['jobs'][0]
    return {'text':text+'\n'+position_text(job,snapshot)+'\n'+load_text(snapshot),
            'queue':{'job_id':jid,'state':job['state'],'position':job['position'],
                     'ahead':job['position']-1 if job['state']=='queued' else 0,
                     **{key:snapshot[key] for key in ('running','waiting','total')}}}


def status(store, uid):
    snapshot=store.queue_snapshot(uid)
    lines=[load_text(snapshot)]
    if not snapshot['jobs']:lines.append('No jobs yet.')
    shown_active=0
    for job in snapshot['jobs']:
        line=f'#{job["id"]} {job["kind"]}: {job["state"]}'
        if job['state'] in ('queued','running'):
            line+=' — '+position_text(job,snapshot)
        else:
            if job['result']:
                url=json.loads(job['result']).get('url','')
                if url:line+=' — '+url
            if job['error']:line+=' — '+job['error'][:120].replace('\n',' ')
        # Leave space for a summary instead of letting Discord cut a job/link.
        if len('\n'.join(lines+[line]))>1640:break
        lines.append(line)
        if job['state'] in ('queued','running'):shown_active+=1
    hidden=snapshot['own_pending']-shown_active
    if hidden>0:lines.append(f'{hidden} more of your jobs are waiting; showing your next {shown_active} active jobs.')
    elif len(lines)-1<len(snapshot['jobs']):lines.append('More recent jobs omitted to fit this message.')
    lines.append('Queue positions include running work and update when you run /cvpilot status.')
    return {'text':'\n'.join(lines),
            'queue':{key:snapshot[key] for key in ('running','waiting','total')}}
