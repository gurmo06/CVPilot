"""Require observable source review and a populated profile before sync succeeds."""
import hashlib
import json
from pathlib import Path
import re


def source_manifest(task):
    result={}
    for path in sorted((task/'master-resumes').rglob('*')):
        if path.suffix.lower() not in ('.tex','.pdf') or not path.is_file():continue
        if path.is_symlink():raise ValueError('Resume sources cannot be symlinks')
        result[path.relative_to(task).as_posix()]=hashlib.sha256(path.read_bytes()).hexdigest()
    if not result:raise ValueError('No master resumes to sync. Upload one with /cvpilot master upload first.')
    return result


def prepare(task, base, extract_pdf=None):
    manifest=source_manifest(task)
    documents=[];size=0
    for name in manifest:
        path=task/name
        if path.suffix.lower()=='.pdf':
            if path.with_suffix('.tex').relative_to(task).as_posix() in manifest:continue
            if extract_pdf is None:raise ValueError('PDF text extraction is required for this master resume.')
            text=extract_pdf(task,path)
        else:text=path.read_text(encoding='utf-8')
        if not text.strip():raise ValueError(f'No readable text in {name}; upload a text-based PDF or matching TeX.')
        size+=len(text)
        if size>80000:raise ValueError('Resume sources exceed the profile-sync text limit. Use shorter source versions before syncing.')
        documents.append({'path':name,'text':text})
    directory=task/'profile'
    try:current=json.loads((directory/'profile_sources.json').read_text())
    except (ValueError,OSError):current={}
    pack={'source_manifest':manifest,'base_commit':base,'documents':documents,
          'current_profile':(directory/'MASTER_PROFILE.md').read_text() if (directory/'MASTER_PROFILE.md').is_file() else '',
          'current_profile_sources':current}
    (task/'.cvpilot-sync-inputs.json').write_text(json.dumps(pack,ensure_ascii=False,indent=2))
    return manifest


def complete_json(text):
    try:return json.loads(text)
    except json.JSONDecodeError as exc:
        if exc.msg=='Extra data':
            value,end=json.JSONDecoder().raw_decode(text)
            # Accept exactly one surplus closing root brace after an already
            # complete object. Never fill missing syntax, join objects, or keep
            # a prefix of a truncated response. Full profile validation follows.
            if isinstance(value,dict) and text[end:].strip()=='}':return value
        raise


def readable_typography(text):
    # Fix observed double-encoded typography only, without re-decoding JSON,
    # TeX commands, other Unicode, or literal examples inside Markdown code.
    pattern=r'(?P<code>```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`)|(?<!\\)\\u(?P<point>00[aA]0|201[013])'
    return re.sub(pattern,lambda match:match[0] if match['code'] is not None else chr(int(match['point'],16)),text)


def clean_profile_typography(task):
    path=task/'profile/MASTER_PROFILE.md'
    if path.parent.is_symlink() or path.is_symlink():raise ValueError('Unsafe profile output path')
    if not path.is_file():return False
    original=path.read_text(encoding='utf-8');clean=readable_typography(original)
    if clean==original:return False
    path.write_text(clean,encoding='utf-8')
    return True


def apply_response(task, report):
    text='\n'.join(item.get('text','') for item in report.get('payloads',[]) if isinstance(item,dict))
    if not text:text=report.get('final','')
    text=text.strip()
    if text.startswith('```') and text.endswith('```'):
        text='\n'.join(text.splitlines()[1:-1])
    try:value=complete_json(text)
    except json.JSONDecodeError as exc:
        # Models sometimes use Markdown/TeX escapes such as \* or \_ inside
        # JSON strings. Preserve those literal backslashes; never guess missing
        # syntax, truncate content, or alter valid JSON escape sequences.
        if exc.msg not in ('Invalid \\escape','Invalid \\uXXXX escape'):
            raise ValueError('Profile sync returned incomplete or invalid JSON. No changes were published.') from exc
        escaped=re.sub(r'\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4})|\\',
                       lambda match:match[0] if len(match[0])>1 else '\\\\',text)
        try:value=complete_json(escaped)
        except json.JSONDecodeError as failed:
            raise ValueError('Profile sync returned incomplete or invalid JSON. No changes were published.') from failed
    if not isinstance(value,dict):raise ValueError('Invalid structured profile response; no changes were published.')
    if value.get('status')=='blocked':raise ValueError('Profile sync was blocked: '+str(value.get('reason','source extraction failed'))[:500])
    if value.get('status')=='unchanged':
        clean_profile_typography(task)
        return
    if (value.get('status')!='updated' or not isinstance(value.get('profile_markdown'),str)
            or not isinstance(value.get('profile_sources'),dict)):
        raise ValueError('Profile sync omitted its output files; no changes were published.')
    if len(value['profile_markdown'])>250000 or len(json.dumps(value['profile_sources']))>1000000:
        raise ValueError('Profile response is too large; no changes were published.')
    directory=task/'profile';directory.mkdir(exist_ok=True)
    for path in (directory,directory/'MASTER_PROFILE.md',directory/'profile_sources.json'):
        if path.is_symlink():raise ValueError('Unsafe profile output path')
    (directory/'MASTER_PROFILE.md').write_text(readable_typography(value['profile_markdown']),encoding='utf-8')
    (directory/'profile_sources.json').write_text(json.dumps(value['profile_sources'],ensure_ascii=False,indent=2)+'\n')


def confirmed_facts(task):
    try:metadata=json.loads((task/'profile/profile_sources.json').read_text())
    except (ValueError,OSError):return {}
    facts=metadata.get('facts',{}) if isinstance(metadata,dict) else {}
    return {key:fact for key,fact in facts.items() if isinstance(fact,dict) and fact.get('user_confirmed') is True} if isinstance(facts,dict) else {}


def validate(task, expected, template, prior_confirmed=None):
    if source_manifest(task)!=expected:raise ValueError('Profile sync changed source resumes; no changes were published.')
    md=task/'profile/MASTER_PROFILE.md';provenance=task/'profile/profile_sources.json'
    if any(path.is_symlink() or not path.is_file() for path in (md,provenance)):
        raise ValueError('Profile sync did not produce both profile files; no changes were published.')
    try:metadata=json.loads(provenance.read_text())
    except (ValueError,UnicodeError) as exc:raise ValueError('Profile source records are invalid JSON; no changes were published.') from exc
    if not isinstance(metadata,dict) or metadata.get('schema_version')!=1 or metadata.get('source_manifest')!=expected:
        raise ValueError('Profile sync did not review all current resumes: source hashes are missing or outdated. No changes were published.')
    facts=metadata.get('facts')
    if not isinstance(facts,dict) or not facts:
        raise ValueError('Profile sync did not extract any verified facts. No changes were published.')
    prior_confirmed=prior_confirmed or {}
    if any(facts.get(key)!=value for key,value in prior_confirmed.items()):
        raise ValueError('Profile sync must preserve user-confirmed facts; no changes were published.')
    for key,fact in facts.items():
        if not isinstance(fact,dict) or not isinstance(fact.get('text'),str) or not fact['text'].strip():
            raise ValueError('Profile facts need text and source references; no changes were published.')
        if fact.get('user_confirmed') is True:
            if prior_confirmed.get(key)!=fact:raise ValueError('Sync cannot invent a user confirmation; no changes were published.')
            continue
        refs=fact.get('sources')
        if (not isinstance(refs,list) or not refs or any(not isinstance(ref,dict)
                or not isinstance(ref.get('path'),str) or ref.get('path') not in expected
                or expected[ref['path']]!=ref.get('sha256') for ref in refs)):
            raise ValueError('Profile facts lack matching resume source references; no changes were published.')
    def body(text):return re.sub(r'\s+',' ',re.sub(r'<!--.*?-->','',text,flags=re.S)).strip()
    text=md.read_text()
    if not body(text) or body(text)==body(Path(template).read_text()):
        raise ValueError('Profile is still empty or the starter template. No changes were published.')
    return len(facts)


def agent_response(raw):
    try:outer=json.loads(raw)
    except (ValueError,TypeError) as exc:raise RuntimeError('Agent returned no valid structured completion result.') from exc
    if not isinstance(outer,dict):raise RuntimeError('Agent returned an invalid completion result.')
    result=outer.get('result',outer)
    if not isinstance(result,dict):raise RuntimeError('Agent returned an invalid completion result.')
    meta=result.get('meta') or {};payloads=result.get('payloads') or []
    error=outer.get('error') or result.get('error') or meta.get('error')
    bad=(outer.get('ok') is False or outer.get('status') in ('error','timeout','cancelled','aborted')
         or meta.get('aborted') or meta.get('timedOut') or error)
    for item in payloads:
        if isinstance(item,dict) and item.get('isError'):bad=True;error=error or item.get('text')
    if bad:raise RuntimeError('Agent did not complete the job: '+str(error or outer.get('status','failed'))[:650])
    if not payloads and not outer.get('status') and not meta:raise RuntimeError('Agent returned an empty completion result.')
    return result
