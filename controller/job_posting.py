"""Retrieve exact public Workday descriptions without JavaScript page extraction."""
from datetime import datetime,timezone
import hashlib
from html.parser import HTMLParser
import http.client
import ipaddress
import json
import re
import socket
import ssl
from urllib.parse import quote,unquote,urlsplit

MAX_BYTES=1024*1024
MAX_TEXT=40000
HOST=re.compile(r'([a-z0-9][a-z0-9-]*)\.wd[0-9]+\.myworkdayjobs\.com\Z')


def endpoint(url):
    u=urlsplit(url);host=(u.hostname or '').lower();match=HOST.fullmatch(host)
    if not match:return None
    if u.scheme!='https' or u.username or u.password or u.port not in (None,443):
        raise ValueError('Workday posting must use a public HTTPS URL without credentials.')
    parts=u.path.strip('/').split('/')
    if parts and re.fullmatch(r'[a-z]{2}-[A-Z]{2}',parts[0]):parts=parts[1:]
    if len(parts)<3 or not re.fullmatch(r'[A-Za-z0-9_-]+',parts[0]) or parts[1]!='job':
        raise ValueError('Supply the full Workday job-listing URL, including its /job/ path.')
    segments=[unquote(part) for part in parts]
    if any(not part or part in ('.','..') or any(c in part for c in '/\\?#%') or any(ord(c)<32 for c in part) for part in segments):
        raise ValueError('Invalid Workday job-listing path.')
    path='/wday/cxs/'+match[1]+'/'+ '/'.join(quote(part,safe='-_.~') for part in segments)
    return host,path,'/'+'/'.join(segments[1:])


def request_json(host,path):
    """Public IP pinning, verified TLS, no redirects/proxies/credentials, bounded body."""
    records=socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)
    addresses=list(dict.fromkeys(item[4][0] for item in records))
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise ValueError('Workday hostname did not resolve to a public address.')
    address=next((item for item in addresses if ':' not in item),addresses[0])
    context=ssl.create_default_context()
    connection=http.client.HTTPSConnection(host,timeout=15,context=context)
    try:
        # Connecting to the validated address prevents a second DNS lookup from
        # changing the destination. The original hostname is still checked by TLS.
        connection.sock=context.wrap_socket(socket.create_connection((address,443),timeout=15),server_hostname=host)
        connection.request('GET',path,headers={'Accept':'application/json','Accept-Encoding':'identity','User-Agent':'CVPilot/1.0'})
        response=connection.getresponse()
        if response.status!=200:
            raise ValueError(f'Workday returned HTTP {response.status}; the posting may be unavailable. Use an accessible public listing URL.')
        if 'application/json' not in response.getheader('Content-Type','').lower():
            raise ValueError('Workday did not return a structured job description.')
        raw=response.read(MAX_BYTES+1)
        if len(raw)>MAX_BYTES:raise ValueError('Workday job response exceeds the size limit.')
        return json.loads(raw)
    finally:connection.close()


class Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True);self.parts=[];self.hidden=0
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style'):self.hidden+=1
        if not self.hidden and tag in ('p','div','li','br','h1','h2','h3','h4'):self.parts.append('\n')
    def handle_endtag(self,tag):
        if tag in ('script','style'):self.hidden=max(0,self.hidden-1)
        if not self.hidden and tag in ('p','div','li'):self.parts.append('\n')
    def handle_data(self,data):
        if not self.hidden:self.parts.append(data)


def fetch(url):
    location=endpoint(url)
    if location is None:return None
    host,path,external_path=location
    try:value=request_json(host,path)
    except (OSError,TimeoutError,http.client.HTTPException,ValueError) as exc:
        raise RuntimeError('Could not retrieve the exact Workday posting: '+str(exc)) from exc
    info=value.get('jobPostingInfo') if isinstance(value,dict) else None
    if not isinstance(info,dict) or not isinstance(info.get('title'),str) or not isinstance(info.get('jobDescription'),str):
        raise RuntimeError('Workday response omitted the job title or description. No tailoring was started.')
    canonical=info.get('externalPath');posting_id=info.get('jobPostingId');req_id=info.get('jobReqId')
    path_matches=isinstance(canonical,str) and unquote(canonical).rstrip('/').casefold()==external_path.rstrip('/').casefold()
    id_matches=isinstance(posting_id,str) and posting_id.casefold()==external_path.rsplit('/',1)[-1].casefold()
    req_matches=not req_id or (isinstance(req_id,str) and external_path.casefold().endswith('_'+req_id.casefold()))
    if not (path_matches or id_matches) or not req_matches:
        raise RuntimeError('Workday returned a different posting. No tailoring was started.')
    parser=Text();parser.feed(info['jobDescription']);parser.close()
    text='\n'.join(re.sub(r'[ \t\r\f\v]+',' ',line).strip() for line in ''.join(parser.parts).splitlines())
    text=re.sub(r'\n{3,}','\n\n',text).strip()
    if len(text)<200:raise RuntimeError('Workday returned no usable job description. No tailoring was started.')
    if len(text)>MAX_TEXT:raise RuntimeError('Workday description exceeds the text limit. No tailoring was started.')
    return {'requested_url':url,'source_url':'https://'+host+path,'title':info['title'][:300],
            'text':text,'sha256':hashlib.sha256(text.encode('utf-8')).hexdigest(),
            'retrieved_at':datetime.now(timezone.utc).isoformat()}
