"""Confirmed repository reset, with atomic remote updates and private recovery data."""
import json
from pathlib import Path
import re
import secrets
import shutil

OID=re.compile(r'(?:[a-f0-9]{40}|[a-f0-9]{64})\Z')


def repository_identity(w, full_name):
    pieces=full_name.split('/')
    if len(pieces)!=2 or any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*',part) for part in pieces):
        raise ValueError('Invalid stored repository identity')
    org,name=pieces;gh=w.GitHub()
    if org.lower()!=gh.org.lower():raise ValueError('Stored repository is outside the configured organization')
    repo=gh.get_repo(name)
    if (not isinstance(repo,dict) or repo.get('private') is not True
            or (repo.get('owner') or {}).get('login','').lower()!=org.lower()
            or repo.get('name','').lower()!=name.lower() or type(repo.get('id')) is not int):
        raise ValueError('Cannot verify the registered private repository; refusing this operation')
    description=repo.get('description') or ''
    if not description.startswith('CVPilot managed workspace [') or not description.endswith(']'):
        raise ValueError('Cannot verify CVPilot repository ownership; refusing this operation')
    if repo.get('default_branch')!='main':
        raise ValueError('Set the repository default branch to main before this operation; no repository settings were changed.')
    return repo['id']


def verified_origin(w, uid, repo, full_name):
    allowed={f'git@github.com:{full_name}.git',f'https://github.com/{full_name}.git',
             f'https://github.com/{full_name}',f'ssh://git@github.com/{full_name}.git'}
    fetch=w.run_git(uid,repo,'remote','get-url','--all','origin').splitlines()
    push=w.run_git(uid,repo,'remote','get-url','--push','--all','origin').splitlines()
    if len(fetch)!=1 or fetch[0] not in allowed or push!=fetch:
        raise ValueError('Local Git origin does not match the registered repository; refusing this operation')
    return f'git@github.com:{full_name}.git'


def remote_heads(w, uid, repo):
    heads={}
    for line in w.run_git(uid,repo,'ls-remote','--heads','origin').splitlines():
        oid,ref=line.split('\t',1)
        if not OID.fullmatch(oid) or not ref.startswith('refs/heads/'):
            raise ValueError('Unexpected remote branch response')
        heads[ref]=oid
    if 'refs/heads/main' not in heads:raise ValueError('Repository main branch is missing; refusing reset')
    return heads


def template_commit(w, uid, repo, old_main, folder, jid):
    w.run_git(uid,repo,'worktree','add','--detach',str(folder),old_main)
    w.run_git(uid,folder,'config','user.name','CVPilot')
    w.run_git(uid,folder,'config','user.email','cvpilot@users.noreply.github.com')
    w.run_git(uid,folder,'rm','-r','-f','--ignore-unmatch','--','.')
    template=w.KIT/'repo-template'
    if not template.is_dir() or template.is_symlink():raise ValueError('Initial repository template is unavailable')
    count=0
    for source in sorted(template.rglob('*')):
        rel=source.relative_to(template)
        if source.is_symlink() or '.git' in rel.parts:raise ValueError('Unsafe repository template')
        if source.is_file():
            destination=folder/rel;destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source,destination);count+=1
    if not count:raise ValueError('Initial repository template is empty')
    w.run_git(uid,folder,'add','-f','-A','--','.')
    w.run_git(uid,folder,'commit','--allow-empty','-m',f'CVPilot reset to initial template (job {jid})')
    return w.run_git(uid,folder,'rev-parse','HEAD')


def reset_repository(job,store):
    import worker as w
    uid=job['discord_id'];jid=job['id'];payload=json.loads(job['payload'])
    if not re.fullmatch(r'[0-9]{16,22}',uid) or type(jid) is not int or jid<=0:
        raise ValueError('Invalid reset job identity')
    user=store.user(uid)
    if not user or not user.get('repo') or payload.get('confirmed') is not True or payload.get('repo')!=user['repo']:
        raise ValueError('Reset requires confirmation for your current registered repository')
    full_name=user['repo'];repo_id=repository_identity(w,full_name)
    directory=w.ROOT/'reset-backups'/uid/str(jid)
    directory.mkdir(parents=True,exist_ok=True,mode=0o700);directory.parent.chmod(0o700)
    journal_path=directory/'reset.json';archived=directory/'checkout'
    repo=w.repopath(uid)
    git_repo=repo if repo.exists() else archived
    if not (git_repo/'.git').is_dir() or git_repo.is_symlink() or (git_repo/'.git').is_symlink():
        raise ValueError('Registered local clone is missing or unsafe; refusing reset')
    origin=verified_origin(w,uid,git_repo,full_name)
    if journal_path.exists():
        journal=json.loads(journal_path.read_text())
        if (journal.get('repo')!=full_name or journal.get('repo_id')!=repo_id or journal.get('uid')!=uid
                or journal.get('job_id')!=jid or not OID.fullmatch(journal.get('new_main',''))):
            raise ValueError('Interrupted reset does not match the current repository; operator review required')
    else:
        heads=remote_heads(w,uid,git_repo)
        w.run_git(uid,git_repo,'fetch','--prune','origin','+refs/heads/*:refs/remotes/origin/*',timeout=120)
        bundle=directory/('before-reset-'+secrets.token_hex(6)+'.bundle')
        w.run_git(uid,git_repo,'bundle','create',str(bundle),'--all',timeout=180);bundle.chmod(0o600)
        w.run_git(uid,git_repo,'bundle','verify',str(bundle))
        folder=directory/('template-'+secrets.token_hex(6))
        new_main=template_commit(w,uid,git_repo,heads['refs/heads/main'],folder,jid)
        journal={'version':1,'uid':uid,'job_id':jid,'repo':full_name,'repo_id':repo_id,
                 'old_heads':heads,'new_main':new_main,'bundle':str(bundle),'complete':False}
        w._write_intent(journal_path,journal)
    new_main=journal['new_main'];before=journal['old_heads'];expected={'refs/heads/main':new_main}
    bundle=Path(journal['bundle'])
    if bundle.parent!=directory or not bundle.is_file():raise ValueError('Reset recovery bundle is missing; operator review required')
    current=remote_heads(w,uid,git_repo)
    if current==before:
        if repository_identity(w,full_name)!=repo_id:raise ValueError('Repository identity changed; refusing reset')
        # The reset commit descends from main. Never rewrite main history or
        # weaken protections. Leases also protect every deleted branch tip.
        w.run_git(uid,git_repo,'merge-base','--is-ancestor',before['refs/heads/main'],new_main)
        leases=[f'--force-with-lease={ref}:{oid}' for ref,oid in sorted(before.items())]
        deletes=[f':{ref}' for ref in sorted(before) if ref!='refs/heads/main']
        w.run_git(uid,git_repo,'push','--atomic',*leases,'origin',f'{new_main}:refs/heads/main',*deletes,timeout=180)
    elif current!=expected:
        raise ValueError('Remote branches changed during reset. No further changes made; review /cvpilot status and start a fresh confirmed reset.')
    if remote_heads(w,uid,git_repo)!=expected:
        raise ValueError('A branch changed during reset; confirm a fresh reset before proceeding')
    staging=repo.parent/f'.reset-{jid}'
    active=False
    if archived.exists() and repo.exists():
        active=(w.run_git(uid,repo,'rev-parse','HEAD')==new_main
                and w.run_git(uid,repo,'for-each-ref','--format=%(refname)','refs/heads')=='refs/heads/main')
        if not active:raise ValueError('Local reset recovery state changed; operator review required')
    if not active:
        if staging.exists():staging.rename(directory/('incomplete-clone-'+secrets.token_hex(6)))
        w.run(['git','clone','--single-branch','--branch','main',origin,str(staging)],env=w.git_env(uid),timeout=120)
        if w.run_git(uid,staging,'rev-parse','HEAD')!=new_main:
            raise ValueError('Main changed before local reset completed; confirm a fresh reset')
        w.run_git(uid,staging,'config','user.name','CVPilot')
        w.run_git(uid,staging,'config','user.email','cvpilot@users.noreply.github.com')
        if repo.exists():
            if archived.exists():raise ValueError('Reset checkout backup already exists')
            repo.rename(archived)
        staging.rename(repo)
    # Preserve old scratch/source state privately instead of mixing it into
    # a future job. The agent workspace mount, keys and bindings remain intact.
    for source,name in [(w.ROOT/'user-workspaces'/uid/'tasks','tasks'),
                        (w.agent_task_root(uid)/'tasks','sandbox-tasks'),
                        (w.ROOT/'git-worktrees'/uid,'worktrees'),
                        (w.ROOT/'resume-inbox'/uid,'resume-inbox')]:
        if source.is_symlink():raise ValueError('Unsafe private reset source directory')
        if source.exists():
            destination=directory/name
            if destination.exists():
                if any(source.iterdir()):raise ValueError('Private reset state changed; operator review required')
            else:source.rename(destination)
    (w.ROOT/'user-workspaces'/uid/'tasks').mkdir(parents=True,exist_ok=True,mode=0o700)
    if remote_heads(w,uid,repo)!=expected:raise ValueError('Remote branches changed before reset verification')
    if w.run_git(uid,repo,'for-each-ref','--format=%(refname)','refs/heads')!='refs/heads/main':
        raise ValueError('Local reset branch verification failed')
    journal['complete']=True;w._write_intent(journal_path,journal)
    print(f'CVPilot reset job {jid} recovery backup: {directory}',flush=True)
    return {'url':f'https://github.com/{full_name}',
            'note':'Repository reset to the initial CVPilot template. Main is the only branch. Upload a new resume to begin again. Your account and job history are preserved.',
            'reset':True,'deleted_branches':len(before)-1,'commit':new_main}
