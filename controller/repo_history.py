"""Read main history and confirm a non-destructive Git revert of one commit."""
import hmac
import json
import re
import secrets
import threading
import time
import main_commits
from reset_repo import repository_identity, verified_origin

CONFIRMATIONS={}
LOCK=threading.Lock()
TTL=120


def checked_repo(uid, user):
    import worker as w
    repo=w.repopath(uid)
    verified_origin(w,uid,repo,user['repo'])
    identity=repository_identity(w,user['repo'])
    w.run_git(uid,repo,'fetch','origin','main')
    return repo,identity,w.run_git(uid,repo,'rev-parse','origin/main')


def history(uid, user, limit=8):
    import worker as w
    if type(limit) is not int or not 1<=limit<=10:raise ValueError('History limit must be between 1 and 10.')
    repo,_,head=checked_repo(uid,user)
    entries=w.run_git(uid,repo,'log','--first-parent',f'-{limit}','--format=%H%x00%s',head).splitlines()
    lines=['Recent commits on main (newest first):']
    for entry in entries:
        oid,subject=entry.split('\0',1)
        subject=subject.replace('`',"'")[:85]
        lines.append(f'`{oid[:12]}` {subject}')
    lines.append(f'https://github.com/{user["repo"]}/commits/main')
    lines.append('Undo one change with /cvpilot revert commit:<ID>; confirmation is required.')
    return {'text':'\n'.join(lines)}


def target(uid, repo, commit, store):
    import worker as w
    if not isinstance(commit,str) or not re.fullmatch(r'[a-fA-F0-9]{7,64}',commit):
        raise ValueError('Supply a commit ID from /cvpilot history (at least 7 hexadecimal characters).')
    try:oid=w.run_git(uid,repo,'rev-parse','--verify','--end-of-options',commit.lower()+'^{commit}')
    except RuntimeError as exc:raise ValueError('Commit ID is missing or ambiguous. Copy the ID from /cvpilot history.') from exc
    first_parent=w.run_git(uid,repo,'rev-list','--first-parent','origin/main').splitlines()
    if oid not in first_parent:raise ValueError('Choose a commit from your repository main history.')
    parents=w.run_git(uid,repo,'rev-list','--parents','-n','1',oid).split()
    if len(parents)!=2:raise ValueError('Initial commits and merge commits cannot be reverted by CVPilot.')
    reset=store.job(store.reset_cutoff(uid))
    if reset:
        boundary=json.loads(reset['result'] or '{}').get('commit','')
        if not main_commits.OID.fullmatch(boundary) or oid==boundary or not main_commits.ancestor(w,uid,repo,boundary,oid):
            raise ValueError('Choose a commit after your latest repository reset. Reset and earlier commits cannot be reverted.')
    files=w.run_git(uid,repo,'diff-tree','--no-commit-id','--name-only','-r',oid).splitlines()
    if not files:raise ValueError('That commit has no file changes to revert.')
    subject=w.run_git(uid,repo,'show','-s','--format=%s',oid).replace('`',"'")[:160]
    return oid,subject,files


def request(uid, args, user, store):
    operation=args.get('operation','prepare')
    with LOCK:
        now=time.monotonic()
        for key in list(CONFIRMATIONS):
            if CONFIRMATIONS[key]['expires']<=now:del CONFIRMATIONS[key]
        pending=CONFIRMATIONS.get(uid)
        if operation=='prepare':
            if store.has_pending(uid):raise ValueError('Wait for your queued/running jobs to finish before reverting. Use /cvpilot status.')
            repo,repo_id,head=checked_repo(uid,user)
            oid,subject,files=target(uid,repo,args.get('commit'),store)
            token=secrets.token_urlsafe(32)
            CONFIRMATIONS[uid]={'token':token,'repo':user['repo'],'repo_id':repo_id,'head':head,
                                'commit':oid,'expires':time.monotonic()+TTL}
            names=', '.join(name[:80] for name in files[:8])
            if len(files)>8:names+=f', and {len(files)-8} more'
            return {'text':f'Undo commit {oid[:12]} on main?\n{subject}\nFiles: {names}\nThis creates a new commit reversing only this change; other commits and historical downloads remain. Conflicting later edits stop the revert. Choose Revert commit, then type REVERT within 2 minutes.\nhttps://github.com/{user["repo"]}/commit/{oid}',
                    'confirmation_token':token}
        token=args.get('token','')
        if (not pending or not isinstance(token,str) or not hmac.compare_digest(pending['token'],token)
                or pending['repo']!=user['repo']):
            raise ValueError('Revert confirmation expired or no longer matches your repository. Run /cvpilot revert again.')
        if operation=='cancel':
            del CONFIRMATIONS[uid]
            return {'text':'Revert cancelled. Your repository was not changed.'}
        if operation!='confirm' or args.get('confirmation')!='REVERT':raise ValueError('Type REVERT exactly to confirm.')
        if store.has_pending(uid):raise ValueError('Wait for your queued/running jobs to finish before reverting.')
        _,repo_id,head=checked_repo(uid,user)
        if repo_id!=pending['repo_id'] or head!=pending['head']:
            raise ValueError('Repository or main changed since the preview. Run /cvpilot revert again to review the current state.')
        if time.monotonic()>=pending['expires']:raise ValueError('Revert confirmation expired. Run /cvpilot revert again.')
        jid=store.enqueue(uid,'revert',{key:pending[key] for key in ('repo','repo_id','head','commit')}|{'confirmed':True})
        del CONFIRMATIONS[uid]
        return {'text':f'Revert queued as job #{jid}. Check /cvpilot status.'}


def revert_commit(job, store):
    import worker as w
    uid=job['discord_id'];data=json.loads(job['payload']);user=store.user(uid)
    if (not user or data.get('confirmed') is not True or data.get('repo')!=user.get('repo')
            or not main_commits.OID.fullmatch(data.get('head',''))
            or not main_commits.OID.fullmatch(data.get('commit',''))):
        raise ValueError('A confirmed revert for this account and repository is required')
    repo,repo_id,head=checked_repo(uid,user)
    if repo_id!=data.get('repo_id'):raise ValueError('Repository identity changed; refusing revert')
    recovered=main_commits.recover(w,job,store)
    if recovered is not None:return recovered
    if head!=data['head']:raise ValueError('Main changed after confirmation. Run /cvpilot revert again to review the current state.')
    oid,_,_=target(uid,repo,data['commit'],store)
    w.new_worktree(uid,job['id'],'revert')
    record=main_commits.begin(w,job,store)
    if record['base']!=data['head']:raise ValueError('Main changed before revert started; confirm a fresh revert')
    trusted=w.gitworktree(uid,job['id'])
    try:w.run_git(uid,trusted,'revert','--no-commit',oid)
    except RuntimeError as exc:
        # This is a disposable detached checkout, never the user's main branch.
        try:w.run_git(uid,trusted,'revert','--abort')
        except RuntimeError:w.run_git(uid,trusted,'reset','--hard',record['base'])
        raise ValueError('The selected change conflicts with later edits. Main was not changed. Resolve the conflict in GitHub or choose a different commit.') from exc
    if not w.run_git(uid,trusted,'diff','--cached','--name-only'):
        raise ValueError('That change is already undone or has no remaining effect. Main was not changed.')
    return main_commits.publish(w,job,store,{'reverted_commit':oid,'artifacts':[],
        'note':f'Undid commit {oid[:12]} with a new commit on main. Other history and historical downloads remain. Use /cvpilot history to inspect it.'})
