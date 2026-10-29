import json, os, re, socketserver, threading, time
from pathlib import Path
from store import Store

UID_RE=re.compile(r'^[0-9]{16,22}$')
ROLE_RE=re.compile(r'^[a-z][a-z0-9-]{0,24}$')
USERNAME_RE=re.compile(r'^[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,37}[a-zA-Z0-9])?$')
ALLOWED={'init','repo','settings','status','tailor','master','profile','answer'}

def process(req, store):
    uid=str(req.get('sender_id',''))
    if not UID_RE.fullmatch(uid): raise ValueError('Authenticated Discord sender ID required')
    action=req.get('action')
    if action not in ALLOWED: raise ValueError('Unknown /career action')
    args=req.get('args') or {}
    if not isinstance(args,dict): raise ValueError('Invalid arguments')
    user=store.user(uid)
    if action=='status':
        return {'text':'\n'.join([f"#{j['id']} {j['kind']}: {j['state']}" + (f" — {json.loads(j['result']).get('url','')}" if j['result'] else '') + (f" — {j['error'][:120]}" if j['error'] else '') for j in store.recent(uid)]) or 'No jobs yet.'}
    if action=='repo':
        return {'text':f"https://github.com/{user['repo']} (private; requires an accepted invitation)" if user and user.get('repo') else 'Not initialized. Use `/career init YOUR_GITHUB_USERNAME`.'}
    if action=='settings':
        if not user: raise ValueError('Initialize with /career init first')
        if args.get('option')!='master_edit': raise ValueError('Usage: /career settings master_edit on|off')
        if args.get('value') not in ('on','off','show'): raise ValueError('Use on, off, or show')
        if args['value']!='show':
            with store.tx() as c:
                c.execute('UPDATE users SET master_edit=? WHERE discord_id=?',(int(args['value']=='on'),uid))
            return {'text':f"Master editing {'enabled' if args['value']=='on' else 'disabled'} for your account. Every master edit still requires an explicit request."}
        return {'text':f"Master editing: {'on' if user['master_edit'] else 'off'}"}
    if action=='init':
        username=args.get('github_username','')
        if not USERNAME_RE.fullmatch(username): raise ValueError('Usage: /career init YOUR_GITHUB_USERNAME')
        if user and user.get('repo'): return {'text':f"Already initialized: https://github.com/{user['repo']}"}
        # prevents racing initialization while first job is pending
        with store.tx() as c:
            j=c.execute("SELECT id FROM jobs WHERE discord_id=? AND kind='init' AND state IN ('queued','running') LIMIT 1",(uid,)).fetchone()
            if j: return {'text':f'Initialization already queued as job #{j[0]}.'}
        jid=store.enqueue(uid,'init',{'github_username':username})
        return {'text':f'Initialization queued as job #{jid}. `/career status` shows progress.'}
    if not user or not user.get('repo'): raise ValueError('Please initialize with /career init first')
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
        return {'text':f'Tailoring request queued as job #{jid}. Use `/career status` for updates.'}
    if action=='master':
        op=args.get('operation','')
        role=args.get('role','')
        if not ROLE_RE.fullmatch(role): raise ValueError('Supply a role such as swe, ml, systems, it, or web')
        if op=='get':
            return {'text':f"https://github.com/{user['repo']}/tree/main/master-resumes/{role} (private; must have repository access)"}
        if op in ('edit','update'):
            if not user['master_edit']: raise ValueError('Master editing is off. Enable with /career settings master_edit on')
            description=args.get('instructions','')
            if len(description)<8 or len(description)>2500: raise ValueError('Provide specific edit instructions (8-2500 chars)')
            jid=store.enqueue(uid,'master-edit',{'role':role,'instructions':description})
            return {'text':f'Master {role} edit queued as #{jid}. It will create a review branch, not change main.'}
        if op in ('upload','replace'):
            return {'text':'Binary Discord attachment handling is not installed yet. Upload your .tex/.pdf to the private repo’s master-resumes/'+role+'/ on main, then use /career profile sync. The bot will use them on the next task.'}
        raise ValueError('Usage: /career master get ROLE | edit ROLE INSTRUCTIONS | upload ROLE')
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
        raise ValueError('Usage: /career profile show | sync | edit INSTRUCTIONS')
    if action=='answer':
        raise ValueError('Skill confirmation follow-ups are not yet implemented; run a new /career tailor after updating your profile with confirmed facts.')
    raise ValueError('Unsupported operation')

class UnixHandler(socketserver.StreamRequestHandler):
    def handle(self):
        data=self.rfile.readline(128*1024)
        try:
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
