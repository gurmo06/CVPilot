"""Private job acknowledgements and status with current shared-queue positions."""
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


def status(store, uid, jid=None, direction=None):
    from job_status import detail
    snapshot=store.status_snapshot(uid,jid,direction)
    response={'text':load_text(snapshot)+'\nNo jobs yet.',
              'queue':{key:snapshot[key] for key in ('running','waiting','total')}}
    if snapshot['job']:
        response.update(detail(store.path.parent,snapshot))
    return response
