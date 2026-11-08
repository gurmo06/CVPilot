"""Private Discord resume transfers. No credentials or host paths cross the socket."""
import base64
import binascii
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
from application_paths import DIRECTORY

MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_PACKET_BYTES = 24 * 1024 * 1024
LABEL_RE = re.compile(r'[a-z][a-z0-9-]{0,39}\Z')
COMMIT_RE = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})\Z')


def label(value):
    if not isinstance(value, str) or not LABEL_RE.fullmatch(value):
        raise ValueError('Use a resume label such as general, teaching, healthcare, or your-own-label (lowercase letters, numbers, hyphens; up to 40 characters).')
    return value


def validate_files(files):
    if not isinstance(files, list) or not 1 <= len(files) <= 2:
        raise ValueError('Attach one PDF or TeX file, optionally with its matching companion file.')
    decoded = {}
    for item in files:
        if not isinstance(item, dict):
            raise ValueError('Invalid resume attachment')
        name = item.get('filename', '')
        if not isinstance(name, str) or not name or len(name) > 160 or any(c in name for c in ('/', '\\', '\x00', '\r', '\n')):
            raise ValueError('Invalid resume filename')
        suffix = Path(name).suffix.lower()
        if suffix not in ('.pdf', '.tex'):
            raise ValueError('Resume attachments must be PDF or TeX files.')
        target = 'resume' + suffix
        if target in decoded:
            raise ValueError('Attach at most one PDF and one TeX file per upload.')
        encoded = item.get('content_base64', '')
        if not isinstance(encoded, str) or not encoded or len(encoded) > ((MAX_FILE_BYTES + 2) // 3) * 4:
            raise ValueError('Each resume attachment must be nonempty and at most 8 MiB.')
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError('Invalid attachment encoding') from exc
        if not data or len(data) > MAX_FILE_BYTES:
            raise ValueError('Each resume attachment must be nonempty and at most 8 MiB.')
        if suffix == '.pdf' and not data.startswith(b'%PDF-'):
            raise ValueError('The PDF attachment does not have a valid PDF header.')
        if suffix == '.tex':
            try:
                data.decode('utf-8')
            except UnicodeDecodeError as exc:
                raise ValueError('TeX attachments must use UTF-8 text.') from exc
            if b'\x00' in data:
                raise ValueError('TeX attachments cannot contain binary data.')
        decoded[target] = data
    return decoded


def inbox_path(root, uid, upload_id):
    if not re.fullmatch(r'[0-9]{16,22}', uid) or not re.fullmatch(r'[a-f0-9]{32}', upload_id):
        raise ValueError('Invalid private upload identifier')
    return Path(root) / 'resume-inbox' / uid / upload_id


def stage_upload(root, uid, files):
    decoded = validate_files(files)  # Validate every file before writing any data.
    upload_id = secrets.token_hex(16)
    path = inbox_path(root, uid, upload_id)
    path.mkdir(mode=0o700, parents=True)
    path.parent.chmod(0o700)
    metadata = []
    try:
        for name, data in decoded.items():
            fd = os.open(path / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            metadata.append({'filename': name, 'sha256': hashlib.sha256(data).hexdigest()})
        directory = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        shutil.rmtree(path)
        raise
    return {'upload_id': upload_id, 'files': metadata}


def staged_files(root, uid, payload):
    path = inbox_path(root, uid, payload['upload_id'])
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError('Invalid private upload directory')
    result = {}
    for item in payload['files']:
        name = item.get('filename')
        if name not in ('resume.pdf', 'resume.tex') or name in result:
            raise ValueError('Invalid staged resume filename')
        file = path / name
        if file.is_symlink() or not file.is_file() or file.stat().st_size > MAX_FILE_BYTES:
            raise ValueError('Staged resume is missing or unsafe; upload it again.')
        data = file.read_bytes()
        if hashlib.sha256(data).hexdigest() != item.get('sha256'):
            raise ValueError('Staged resume changed; upload it again.')
        result[name] = data
    if not 1 <= len(result) <= 2:
        raise ValueError('Invalid staged resume files')
    return result


def safe_artifact_path(path):
    return isinstance(path, str) and (re.fullmatch(
        DIRECTORY+r'/resume\.(pdf|tex)', path) or re.fullmatch(
        r'master-resumes/[a-z][a-z0-9-]{0,39}/[A-Za-z0-9][A-Za-z0-9._-]*\.(pdf|tex)', path))


def read_blob(repo, commit, path):
    if commit != 'origin/main' and (not isinstance(commit, str) or not COMMIT_RE.fullmatch(commit)):
        raise ValueError('Invalid resume revision')
    if not safe_artifact_path(path):
        raise ValueError('Invalid resume artifact path')
    def git(*args, binary=False):
        result = subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', '-c',
            'protocol.ext.allow=never', '-C', str(repo), *args],
            check=True, capture_output=True, timeout=20)
        return result.stdout if binary else result.stdout.decode().strip()
    entry = git('ls-tree', commit, '--', path)
    if not entry:
        return None
    if not entry.startswith(('100644 blob ', '100755 blob ')):
        raise ValueError('Resume artifacts must be regular files, not links.')
    spec = f'{commit}:{path}'
    size = int(git('cat-file', '-s', spec))
    if not 0 < size <= MAX_FILE_BYTES:
        raise ValueError('This file exceeds the 8 MiB Discord transfer limit. Use its private GitHub link.')
    return git('show', spec, binary=True)


def encode_attachments(files):
    return [{'filename': name, 'content_base64': base64.b64encode(data).decode('ascii')}
            for name, data in files]


def artifact_files(repo, result):
    commit = result.get('commit', '')
    files = []
    artifacts = result.get('artifacts', [])
    if not isinstance(artifacts, list) or len(artifacts) > 2:
        raise ValueError('Invalid resume artifact list')
    for item in artifacts:
        path = item.get('path', '')
        data = read_blob(repo, commit, path)
        if data is None:
            raise ValueError('The requested resume file is no longer available.')
        # Filenames are derived from validated Git paths, never from user metadata.
        files.append((Path(path).name, data))
    return files


def active_upload_files(repo, result, current_files):
    """Use uploads immediately; defer to main once its source files change.

    The upload records hashes of the source on main at upload time. A later
    merge/edit on main wins, preventing an old Discord upload from overwriting it.
    """
    if result.get('on_main') is True:return None
    role = label(result['label'])
    base = result.get('base_files', {})
    if set(base) != {'resume.pdf', 'resume.tex'}:
        raise ValueError('Invalid uploaded-resume baseline')
    for name in base:
        current = current_files.get(name)
        digest = hashlib.sha256(current).hexdigest() if current is not None else None
        if digest != base[name]:
            return None
    files = artifact_files(repo, result)
    if not files or any(item.get('path', '').split('/')[:2] != ['master-resumes', role]
                        for item in result.get('artifacts', [])):
        raise ValueError('Invalid uploaded-resume ownership metadata')
    return dict(files)
