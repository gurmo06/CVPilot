import base64, json, os, re, urllib.request, urllib.error

ORG='CV-Pilot'

class GitHubAPIError(RuntimeError):
    """GitHub API error with a structured HTTP status."""

    def __init__(self, status, method, path, detail):
        self.status = status
        self.method = method
        self.path = path
        super().__init__(f'GitHub HTTP {status} on {method} {path}: {detail}')


class GitHub:
    def __init__(self):
        self.token=os.environ.get('CVPILOT_GITHUB_TOKEN','')
        self.org=os.environ.get('CVPILOT_GITHUB_ORG',ORG)
        if not self.token: raise RuntimeError('Missing CVPILOT_GITHUB_TOKEN in worker environment')
    def api(self, method, path, data=None):
        if not path.startswith('/'): raise ValueError('API path must be absolute')
        url='https://api.github.com'+path
        headers={'Accept':'application/vnd.github+json','Authorization':f'Bearer {self.token}',
                 'X-GitHub-Api-Version':'2022-11-28','User-Agent':'CVPilot-controller'}
        body=json.dumps(data).encode() if data is not None else None
        if body is not None: headers['Content-Type']='application/json'
        req=urllib.request.Request(url, data=body, method=method,headers=headers)
        try:
            with urllib.request.urlopen(req,timeout=30) as rsp:
                b=rsp.read()
                return json.loads(b) if b else {}
        except urllib.error.HTTPError as e:
            body=e.read(500).decode(errors='replace')
            raise GitHubAPIError(e.code, method, path, body) from e
    def get_repo(self, name):
        try:
            return self.api('GET', f'/repos/{self.org}/{name}')
        except GitHubAPIError as exc:
            if exc.status == 404:
                return None
            raise

    def create_repo(self, name, marker):
        data=self.api('POST',f'/orgs/{self.org}/repos',
                      {'name':name,'private':True,'auto_init':True,
                       'description':f'CVPilot managed workspace [{marker}]',
                       'has_issues':False,'has_wiki':False})
        if data.get('private') is not True: raise RuntimeError('Created repo is not private')
        return data
    def deploy_key(self, name, public_key, uid):
        return self.api('POST',f'/repos/{self.org}/{name}/keys',
                    {'title':f'cvpilot-{uid}','key':public_key,'read_only':False})
    def invite(self, name, github_user):
        return self.api('PUT',f'/repos/{self.org}/{name}/collaborators/{github_user}',{'permission':'push'})
    def protect_main(self,name):
        # GitHub plan/settings may reject this request; report failure clearly.
        return self.api('PUT',f'/repos/{self.org}/{name}/branches/main/protection',{
             'required_status_checks':None,'enforce_admins':True,
             'required_pull_request_reviews':None,
             'restrictions':None,'required_linear_history':False,
             'allow_force_pushes':False,'allow_deletions':False,
        })

def ssh_env(keypath,known_hosts):
    # Never put shell interp data from the model into this config.
    return {'GIT_SSH_COMMAND':f'ssh -F /dev/null -i {keypath} -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile={known_hosts}'}


def https_git_env(full_name, token, base_env):
    """Invocation-only HTTPS auth for one verified GitHub repository; never persist credentials."""
    if (not isinstance(full_name,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*',full_name)
            or not token or '\n' in token or '\r' in token):
        raise ValueError('Invalid repository-scoped GitHub HTTPS credentials')
    env=base_env.copy();url=f'https://github.com/{full_name}.git'
    header='Authorization: Basic '+base64.b64encode(('x-access-token:'+token).encode()).decode()
    settings=[(f'url.{url}.insteadOf',f'git@github.com:{full_name}.git'),
              (f'url.{url}.insteadOf',f'ssh://git@github.com/{full_name}.git'),
              (f'http.{url}/.extraHeader',''),(f'http.{url}/.extraHeader',header),
              (f'http.https://github.com/{full_name}/.extraHeader',''),
              (f'http.https://github.com/{full_name}/.extraHeader',header),
              ('credential.helper',''),('core.askPass','/bin/false'),
              ('http.followRedirects','false'),('http.sslVerify','true')]
    count=int(env.get('GIT_CONFIG_COUNT','0'))
    if not 0<=count<=100:raise ValueError('Unexpected inherited Git configuration')
    for index,(key,value) in enumerate(settings,count):
        env[f'GIT_CONFIG_KEY_{index}']=key;env[f'GIT_CONFIG_VALUE_{index}']=value
    env['GIT_CONFIG_COUNT']=str(count+len(settings));env['GIT_TERMINAL_PROMPT']='0'
    for key in list(env):
        if key.startswith('GIT_TRACE') or key in ('GIT_CURL_VERBOSE','GIT_ASKPASS'):env.pop(key)
    return env
