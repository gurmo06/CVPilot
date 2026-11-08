"""Preview and confirm deletion of one source or application directory on main."""
import hmac
import json
import re
import secrets
import threading
import time
from urllib.parse import urlsplit, unquote

import main_commits
from application_paths import DIRECTORY
from resumes import label
from repo_history import checked_repo
from queue_status import acknowledgement

CONFIRMATIONS = {}
LOCK = threading.Lock()
TTL = 120
MASTER = re.compile(r'master-resumes/[a-z][a-z0-9-]{0,39}\Z')
APPLICATION = re.compile(DIRECTORY+r'\Z')


def directory(kind, path):
    pattern = MASTER if kind == 'master-delete' else APPLICATION
    if not isinstance(path, str) or not pattern.fullmatch(path):
        raise ValueError('Invalid resume directory')
    return path


def entries(uid, repo, revision, path):
    import worker as w
    raw = w.run_git(uid, repo, 'ls-tree', '-r', '-z', revision, '--', path + '/')
    found = {}
    for entry in raw.split('\0'):
        if not entry:
            continue
        metadata, name = entry.split('\t', 1)
        mode, kind, oid = metadata.split()
        if (mode not in ('100644', '100755') or kind != 'blob'
                or not name.startswith(path + '/') or any(c in name for c in '\r\n\t')
                or not main_commits.OID.fullmatch(oid)):
            raise ValueError('Resume directories must contain regular files, without links.')
        found[name] = oid
    if len(found) > 1000:
        raise ValueError('This directory has too many files for a resume deletion.')
    return found


def revision(uid, repo, value, store):
    import worker as w
    if not isinstance(value, str) or not re.fullmatch(r'[a-fA-F0-9]{7,64}', value):
        raise ValueError('Supply a commit ID with at least 7 hexadecimal characters.')
    try:
        oid = w.run_git(uid, repo, 'rev-parse', '--verify', '--end-of-options', value.lower() + '^{commit}')
    except RuntimeError as exc:
        raise ValueError('Commit ID is missing or ambiguous. Use /cvpilot history.') from exc
    if oid not in w.run_git(uid, repo, 'rev-list', '--first-parent', 'origin/main').splitlines():
        raise ValueError('Choose a commit from your repository main history.')
    reset = store.job(store.reset_cutoff(uid))
    if reset:
        boundary = json.loads(reset['result'] or '{}').get('commit', '')
        if not main_commits.OID.fullmatch(boundary) or oid == boundary or not main_commits.ancestor(w, uid, repo, boundary, oid):
            raise ValueError('Choose a resume created after your last repository reset.')
    return oid


def commit_directory(uid, repo, oid, kind):
    import worker as w
    changed = w.run_git(uid, repo, 'diff-tree', '--root', '--no-commit-id', '--name-only', '-r', oid).splitlines()
    candidates = set()
    for name in changed:
        parts = name.split('/')
        count = 2 if kind == 'master-delete' else 3
        path = '/'.join(parts[:count])
        pattern = MASTER if kind == 'master-delete' else APPLICATION
        if pattern.fullmatch(path) and name.endswith(('.pdf', '.tex')):
            candidates.add(path)
    if len(candidates) != 1:
        raise ValueError('That commit does not identify exactly one resume. Use a master label or an application folder/file link.')
    return candidates.pop()


def link_target(uid, repo, user, value, store):
    if not isinstance(value, str) or len(value) > 1800:
        raise ValueError('Supply a CVPilot application link or commit ID.')
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.port:
        raise ValueError('Supply an HTTPS application link without credentials or a port.')
    if parsed.hostname.lower() != 'github.com':
        # A posting URL may identify a completed application, but is never fetched.
        matches = set()
        for job in store.download_jobs(uid):
            if job['kind'] != 'tailor' or json.loads(job['payload']).get('url') != value:
                continue
            result = json.loads(job['result'] or '{}')
            for item in result.get('artifacts', []):
                path = '/'.join(item.get('path', '').split('/')[:3])
                if APPLICATION.fullmatch(path):
                    matches.add(path)
        if len(matches) != 1:
            raise ValueError('That posting link does not identify one application. Use its CVPilot GitHub link or commit ID.')
        return matches.pop(), None
    parts = unquote(parsed.path).strip('/').split('/')
    if len(parts) < 4 or '/'.join(parts[:2]).lower() != user['repo'].lower():
        raise ValueError('Use an application link from your own CVPilot repository.')
    if parts[2] == 'commit' and len(parts) == 4:
        oid = revision(uid, repo, parts[3], store)
        return commit_directory(uid, repo, oid, 'application-delete'), oid
    if parts[2] in ('tree', 'blob') and len(parts) in (7, 8):
        path = directory('application-delete', '/'.join(parts[4:7]))
        if (parts[2] == 'tree' and len(parts) != 7) or (parts[2] == 'blob' and (len(parts) != 8 or parts[7] not in ('resume.pdf', 'resume.tex', 'notes.md', 'job-url.txt'))):
            raise ValueError('Use an application folder or resume file link.')
        oid = None if parts[3] == 'main' else revision(uid, repo, parts[3], store)
        return path, oid
    raise ValueError('Use an application commit, folder, or resume file link from CVPilot.')


def plan(uid, repo, user, kind, args, store, head):
    supplied = [key for key in ('label', 'commit', 'link') if args.get(key) is not None]
    allowed = ('label', 'commit') if kind == 'master-delete' else ('link', 'commit')
    if len(supplied) != 1 or supplied[0] not in allowed:
        raise ValueError('Supply exactly one of ' + ' or '.join(allowed) + '.')
    oid = None
    if supplied[0] == 'label':
        path = 'master-resumes/' + label(args['label'])
    elif supplied[0] == 'commit':
        oid = revision(uid, repo, args['commit'], store)
        path = commit_directory(uid, repo, oid, kind)
    else:
        path, oid = link_target(uid, repo, user, args['link'], store)
    directory(kind, path)
    files = entries(uid, repo, head, path)
    if not any(name.endswith(('.pdf', '.tex')) for name in files):
        raise ValueError('That resume is not present on main or was already deleted.')
    if oid and entries(uid, repo, oid, path) != files:
        raise ValueError('This resume changed after that commit. Use its current label, current application link, or latest commit to review it.')
    return {'directory': path, 'files': files}


def request(uid, args, user, store, kind):
    key = (uid, kind)
    with LOCK:
        now = time.monotonic()
        for old in list(CONFIRMATIONS):
            if CONFIRMATIONS[old]['expires'] <= now:
                del CONFIRMATIONS[old]
        pending = CONFIRMATIONS.get(key)
        operation = args.get('operation', 'prepare')
        if operation == 'prepare':
            if store.has_pending(uid):
                raise ValueError('Wait for your queued/running jobs to finish before deleting. Use /cvpilot status.')
            repo, repo_id, head = checked_repo(uid, user)
            selection = plan(uid, repo, user, kind, args, store, head)
            token = secrets.token_urlsafe(32)
            CONFIRMATIONS[key] = {'token': token, 'repo': user['repo'], 'repo_id': repo_id,
                                  'head': head, **selection, 'expires': time.monotonic() + TTL}
            names = ', '.join(name.rsplit('/', 1)[-1][:70] for name in list(selection['files'])[:8])
            extra = ' Existing profile facts are kept; use profile edit or sync to review them.' if kind == 'master-delete' else ''
            return {'text': f'Delete {selection["directory"]}?\nFiles ({len(selection["files"])}): {names}\n'
                    'This removes the entire resume folder, including paired PDF/TeX files, from main and CVPilot downloads. '
                    'Git history and previously sent Discord attachments remain. Undo with /cvpilot revert.' + extra +
                    '\nChoose Delete resume, then type DELETE within 2 minutes.', 'confirmation_token': token}
        token = args.get('token', '')
        if not pending or not isinstance(token, str) or not hmac.compare_digest(pending['token'], token) or pending['repo'] != user['repo']:
            raise ValueError('Deletion confirmation expired or no longer matches. Run the delete command again.')
        if operation == 'cancel':
            del CONFIRMATIONS[key]
            return {'text': 'Deletion cancelled. Your repository was not changed.'}
        if operation != 'confirm' or args.get('confirmation') != 'DELETE':
            raise ValueError('Type DELETE exactly to confirm.')
        if store.has_pending(uid):
            raise ValueError('Wait for your queued/running jobs to finish before deleting.')
        _, repo_id, head = checked_repo(uid, user)
        if repo_id != pending['repo_id'] or head != pending['head']:
            raise ValueError('Repository or main changed since the preview. Run the delete command again.')
        if time.monotonic() >= pending['expires']:
            raise ValueError('Deletion confirmation expired. Run the delete command again.')
        payload = {key: pending[key] for key in ('repo', 'repo_id', 'head', 'directory', 'files')}
        jid = store.enqueue(uid, kind, payload | {'confirmed': True})
        del CONFIRMATIONS[key]
        return acknowledgement(store,uid,jid,f'Resume deletion queued as job #{jid}. Check /cvpilot status.')


def delete_resume(job, store):
    import worker as w
    uid = job['discord_id']; data = json.loads(job['payload']); user = store.user(uid)
    if (not user or data.get('confirmed') is not True or data.get('repo') != user.get('repo')
            or not main_commits.OID.fullmatch(data.get('head', ''))):
        raise ValueError('A confirmed deletion for this account and repository is required.')
    path = directory(job['kind'], data.get('directory'))
    repo, repo_id, head = checked_repo(uid, user)
    if repo_id != data.get('repo_id'):
        raise ValueError('Repository identity changed; refusing deletion.')
    recovered = main_commits.recover(w, job, store)
    if recovered is not None:
        return recovered
    if head != data['head']:
        raise ValueError('Main changed after confirmation. Run the delete command again.')
    files = entries(uid, repo, head, path)
    if not files or files != data.get('files'):
        raise ValueError('Resume files no longer match the confirmed preview.')
    w.new_worktree(uid, job['id'], job['kind'])
    record = main_commits.begin(w, job, store)
    if record['base'] != data['head']:
        raise ValueError('Main changed before deletion started. Confirm a fresh deletion.')
    w.run_git(uid, w.gitworktree(uid, job['id']), 'rm', '-r', '--', path)
    return main_commits.publish(w, job, store, {'deleted_directory': path, 'artifacts': [],
        'note': f'Deleted {path} from main and CVPilot downloads. Git history remains. '
                'Use /cvpilot revert with this deletion commit to restore it.' +
                (' Existing profile facts were kept.' if job['kind'] == 'master-delete' else '')})
