import json, os, subprocess, urllib.request, urllib.error
from pathlib import Path

ORG='CVPilot'
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
            raise RuntimeError(f'GitHub HTTP {e.code} on {method} {path}: {body}') from e
    def create_repo(self, name):
        data=self.api('POST',f'/orgs/{self.org}/repos',
                      {'name':name,'private':True,'auto_init':True,'has_issues':False,'has_wiki':False})
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
             'required_pull_request_reviews':{'required_approving_review_count':1,'dismiss_stale_reviews':True},
             'restrictions':None,'required_linear_history':False,
             'allow_force_pushes':False,'allow_deletions':False,
        })

def cmd(args, **kwargs):
    return subprocess.run(args,check=True,text=True,capture_output=True,**kwargs).stdout.strip()

def ssh_env(keypath,known_hosts):
    # Never put shell interp data from the model into this config.
    return {'GIT_SSH_COMMAND':f'ssh -F /dev/null -i {keypath} -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile={known_hosts}'}