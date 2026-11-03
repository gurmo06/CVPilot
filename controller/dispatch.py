import json, os, re, socketserver, threading, time
from pathlib import Path
from store import Store
from resumes import label, stage_upload, MAX_PACKET_BYTES

UID_RE=re.compile(r'^[0-9]{16,22}$')
USERNAME_RE=re.compile(r'^[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,37}[a-zA-Z0-9])?$')
ALLOWED={'init','repo','settings','status','tailor','master','profile','answer','download'}

def process(req, store):
    uid=str(req.get('sender_id',''))
    if not UID_RE.fullmatch(uid): raise ValueError('Authenticated Discord sender ID required')
    action=req.get('action')
    if action not in ALLOWED: raise ValueError('Unknown /cvpilot action')
    args=req.get('args') or {}
    if not isinstance(args,dict): raise ValueError('Invalid arguments')
    user=store.user(uid)
    if action=='status':
        return {'text':'\n'.join([f"#{j['id']} {j['kind']}: {j['state']}" + (f" — {json.loads(j['result']).get('url','')}" if j['result'] else '') + (f" — {j['error'][:120]}" if j['error'] else '') for j in store.recent(uid)]) or 'No jobs yet.'}
    if action=='repo':
        return {'text':f"https://github.com/{user['repo']} (private; requires an accepted invitation)" if user and user.get('repo') else 'Not initialized. Use `/cvpilot init github_username:YOUR_GITHUB_USERNAME`.'}
    if action=='settings':
        if not user: raise ValueError('Initialize with /cvpilot init first')
        if args.get('option')!='master_edit': raise ValueError('Usage: /cvpilot settings master_edit value:on|off|show')
        if args.get('value') not in ('on','off','show'): raise ValueError('Use on, off, or show')
        if args['value']!='show':
            with store.tx() as c:
                c.execute('UPDATE users SET master_edit=? WHERE discord_id=?',(int(args['value']=='on'),uid))
            return {'text':f"Master editing {'enabled' if args['value']=='on' else 'disabled'} for your account. Every master edit still requires an explicit request."}
        return {'text':f"Master editing: {'on' if user['master_edit'] else 'off'}"}
    if action=='init':
        username=args.get('github_username','')
        if not USERNAME_RE.fullmatch(username): raise ValueError('Usage: /cvpilot init github_username:YOUR_GITHUB_USERNAME')
        if user and user.get('repo'): return {'text':f"Already initialized: https://github.com/{user['repo']}"}
        # prevents racing initialization while first job is pending
        with store.tx() as c:
            j=c.execute("SELECT id FROM jobs WHERE discord_id=? AND kind='init' AND state IN ('queued','running') LIMIT 1",(uid,)).fetchone()
            if j: return {'text':f'Initialization already queued as job #{j[0]}.'}
        jid=store.enqueue(uid,'init',{'github_username':username})
        return {'text':f'Initialization queued as job #{jid}. `/cvpilot status` shows progress.'}
    if not user or not user.get('repo'): raise ValueError('Please initialize with /cvpilot init first')
    if action=='download':
        from worker import download_job
        return download_job(uid,args.get('job_id'),store)
    if action=='tailor':
        import ipaddress,urllib.parse
        url=args.get('url','')
        u=urllib.parse.urlparse(url)
        host=u.hostname or ''
        if u.scheme!='https' or not host or u.username or u.password or len(url)>1800:
            raise ValueError('Supply an HTTPS job posting URL')
        if host.lower() in ('localhost','localhost.localdomain') or host.endswith('.local'):
            raise ValueError('Local/private job URLs are not supported')
        try:
            if ipaddress.ip_address(host).is_global is False: raise ValueError('Private IP URL is blocked')
        except ValueError as e:
            if 'blocked' in str(e): raise
        jid=store.enqueue(uid,'tailor',{'url':url})
        return {'text':f'Tailoring request queued as job #{jid}. Your PDF and TeX will be sent by DM. Use `/cvpilot status` for updates or `/cvpilot download` if DMs are blocked.'}
    if action=='master':
        op=args.get('operation','')
        role=label(args.get('label',args.get('role','general')))
        if op=='get':
            from worker import download_master
            return download_master(uid,role,store)
        if op in ('edit','update'):
            if not user['master_edit']: raise ValueError('Master editing is off. Enable with /cvpilot settings master_edit value:on')
            description=args.get('instructions','')
            if len(description)<8 or len(description)>2500: raise ValueError('Provide specific edit instructions (8-2500 chars)')
            jid=store.enqueue(uid,'master-edit',{'role':role,'instructions':description})
            return {'text':f'Master {role} edit queued as #{jid}. It will create a review branch, not change main.'}
        if op in ('upload','replace'):
            import shutil
            from resumes import inbox_path
            staged=stage_upload(store.path.parent,uid,args.get('files'))
            try:
                jid=store.enqueue(uid,'master-upload',{'role':role,**staged})
            except BaseException:
                shutil.rmtree(inbox_path(store.path.parent,uid,staged['upload_id']))
                raise
            return {'text':f'Resume upload queued as job #{jid} under label {role}. It will be available for tailoring after completion. Reusing a label explicitly replaces its active uploaded version; earlier versions stay in Git history.'}

        raise ValueError('Usage: /cvpilot master get label:LABEL | edit label:LABEL instructions:TEXT | upload label:LABEL')
    if action=='profile':
        op=args.get('operation','')
        if op=='show': return {'text':f"https://github.com/{user['repo']}/blob/main/profile/MASTER_PROFILE.md"}
        if op=='sync':
            jid=store.enqueue(uid,'profile-sync',{})
            return {'text':f'Profile sync queued as #{jid}; changes go to a review branch.'}
        if op=='edit':
            detail=args.get('instructions','')
            if len(detail)<5 or len(detail)>2500: raise ValueError('Supply specific profile facts')
            jid=store.enqueue(uid,'profile-edit',{'instructions':detail})
            return {'text':f'Profile update queued as #{jid}. Unverified additions will require confirmation.'}
        raise ValueError('Usage: /cvpilot profile show | sync | edit instructions:TEXT')
    if action=='answer':
        raise ValueError('Skill confirmation follow-ups are not yet implemented; run a new /cvpilot tailor after updating your profile with confirmed facts.')
    raise ValueError('Unsupported operation')

class UnixHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(60)
        data=self.rfile.readline(MAX_PACKET_BYTES+1)
        try:
            if len(data)>MAX_PACKET_BYTES or not data.endswith(b'\n'): raise ValueError('Request exceeds the attachment transfer limit')
            req=json.loads(data)
            result=process(req,self.server.store)
            ans={'ok':True,**result}
        except Exception as e:
            ans={'ok':False,'text':str(e)}
        self.wfile.write((json.dumps(ans)+'\n').encode())

class UnixServer(socketserver.ThreadingMixIn,socketserver.UnixStreamServer):
    daemon_threads=True

def serve(socket_path,store,shutdown=None):
    p=Path(socket_path);p.parent.mkdir(parents=True,exist_ok=True)
    try:p.unlink()
    except FileNotFoundError:pass
    s=UnixServer(str(p),UnixHandler);s.store=store
    os.chmod(p,0o600)
    if shutdown: shutdown.append(s)
    s.serve_forever(poll_interval=.3)
