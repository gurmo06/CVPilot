"""Publish validated jobs to main without branches, history rewrites, or duplicate retries."""
import json
import re
import commit_messages

OID=re.compile(r'(?:[a-f0-9]{40}|[a-f0-9]{64})\Z')


def journal_path(w, job):
    uid=job['discord_id'];jid=job['id']
    if not re.fullmatch(r'[0-9]{16,22}',uid) or type(jid) is not int or jid<1:
        raise ValueError('Invalid publication job identity')
    directory=w.ROOT/'publications'/uid
    if directory.is_symlink() or directory.parent.is_symlink():raise ValueError('Unsafe publication directory')
    directory.mkdir(parents=True,exist_ok=True,mode=0o700);directory.chmod(0o700)
    path=directory/f'{jid}.json'
    if path.is_symlink():raise ValueError('Unsafe publication journal')
    return path


def load(w, job, store):
    path=journal_path(w,job)
    if not path.exists():return None
    record=json.loads(path.read_text())
    user=store.user(job['discord_id'])
    if (not user or record.get('repo')!=user['repo'] or record.get('uid')!=job['discord_id']
            or record.get('job_id')!=job['id'] or record.get('kind')!=job['kind']
            or not OID.fullmatch(record.get('base','')) or job['id']<=store.reset_cutoff(job['discord_id'])):
        raise ValueError('Publication no longer matches this account, job, or repository')
    return record


def begin(w, job, store):
    record=load(w,job,store)
    if record:return record
    uid=job['discord_id'];user=store.user(uid)
    if not user or not user.get('repo'):raise ValueError('Initialize CVPilot first')
    base=w.run_git(uid,w.gitworktree(uid,job['id']),'rev-parse','HEAD')
    if not OID.fullmatch(base):raise ValueError('Invalid main snapshot')
    record={'uid':uid,'job_id':job['id'],'kind':job['kind'],'repo':user['repo'],'base':base}
    w._write_intent(journal_path(w,job),record)
    return record


def ancestor(w, uid, repo, older, newer):
    try:w.run_git(uid,repo,'merge-base','--is-ancestor',older,newer)
    except RuntimeError:return False
    return True


def publish(w, job, store, draft=None):
    uid=job['discord_id'];jid=job['id'];repo=w.repopath(uid);trusted=w.gitworktree(uid,jid)
    record=load(w,job,store)
    if not record:raise ValueError('Missing main publication journal')
    if 'draft' not in record:
        if draft is None:raise ValueError('Missing validated publication result')
        record['draft']=draft
        # Persist output metadata before committing. Recovery never re-runs the
        # agent once validated output is ready, including a lost push response.
        w._write_intent(journal_path(w,job),record)
    if 'result' not in record:
        head=w.run_git(uid,trusted,'rev-parse','HEAD')
        if head==record['base']:
            if w.run_git(uid,trusted,'diff','--cached','--name-only'):
                if 'commit_message' not in record:
                    record['commit_message']=commit_messages.build(w,uid,trusted,job['kind'],jid,
                        json.loads(job['payload']),record['draft'],coauthor=commit_messages.coauthor(w,uid))
                    # Recovery checks the exact persisted message after a crash;
                    # it never regenerates metadata from a different snapshot.
                    w._write_intent(journal_path(w,job),record)
                # Recovery can reach a previously prepared checkout without
                # new_worktree; apply the configured identity to new commits.
                commit_messages.configure_identity(w,uid,trusted)
                commit_messages.commit(w,uid,trusted,record['commit_message'])
                head=w.run_git(uid,trusted,'rev-parse','HEAD')
        else:
            parents=w.run_git(uid,trusted,'rev-list','--parents','-n','1',head).split()
            message=record.get('commit_message')
            if message is None:
                # Journals made before detailed messages may have committed
                # successfully before recording their result. Preserve recovery.
                message=f'CVPilot {job["kind"]} job {jid}'
                if job['kind']=='revert':message=f'CVPilot revert {record["draft"]["reverted_commit"][:12]} job {jid}'
            if parents!=[head,record['base']] or w.run_git(uid,trusted,'show','-s','--format=%B',head)!=message:
                raise ValueError('Interrupted job checkout changed; refusing main publication')
        if not OID.fullmatch(head):raise ValueError('Invalid publication commit')
        changed=head!=record['base']
        record['result']={**record['draft'],'commit':head,'on_main':True,'changed':changed,
                          'url':f'https://github.com/{record["repo"]}/'+(f'commit/{head}' if changed else 'tree/main')}
        w._write_intent(journal_path(w,job),record)
    result=record['result'];commit=result.get('commit','')
    if not OID.fullmatch(commit):raise ValueError('Invalid recorded publication commit')
    w.run_git(uid,repo,'fetch','origin','main')
    current=w.run_git(uid,repo,'rev-parse','origin/main')
    if ancestor(w,uid,repo,commit,current):return result
    if current!=record['base']:
        raise ValueError('Main changed while this job was running. No main changes were published; submit a new job using the current files.')
    if not ancestor(w,uid,repo,current,commit):raise ValueError('Publication would rewrite main history; refusing')
    # A plain fast-forward push respects GitHub protection and rejects competing
    # commits. No job creates a remote branch or force-pushes main.
    try:w.run_git(uid,repo,'push','origin',f'{commit}:refs/heads/main',timeout=90)
    except RuntimeError:
        w.run_git(uid,repo,'fetch','origin','main')
        if not ancestor(w,uid,repo,commit,w.run_git(uid,repo,'rev-parse','origin/main')):raise
        return result
    w.run_git(uid,repo,'fetch','origin','main')
    if not ancestor(w,uid,repo,commit,w.run_git(uid,repo,'rev-parse','origin/main')):
        raise ValueError('Could not verify the published main commit; inspect /cvpilot history')
    return result


def recover(w, job, store):
    record=load(w,job,store)
    if record and 'draft' in record:return publish(w,job,store)
    return None
