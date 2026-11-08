"""CVPilot's OpenClaw process contract, independent of the shell's Node PATH."""
import json
import os
from pathlib import Path
import shutil
from job_status import clean


class OpenClawError(RuntimeError):
    def __init__(self,message,response=None):
        super().__init__(message)
        self.response=response


def command(argv):
    binary=Path(os.environ.get('CVPILOT_OPENCLAW_BIN') or shutil.which('openclaw') or
                str(Path.home()/'.npm-global/bin/openclaw')).expanduser()
    if not binary.is_file() or not os.access(binary,os.X_OK):
        raise RuntimeError('OpenClaw executable not found. Set CVPILOT_OPENCLAW_BIN to its executable path.')
    entry=binary.resolve()
    if entry.suffix in ('.mjs','.js'):
        node=Path(os.environ.get('CVPILOT_NODE_BIN','/usr/bin/node')).expanduser()
        if not node.is_file() or not os.access(node,os.X_OK):
            raise RuntimeError('OpenClaw needs a supported Node runtime. Set CVPILOT_NODE_BIN to its executable path.')
        # The same Node 24 runtime as the managed Gateway, rather than env node
        # selecting Node 22 and making OpenClaw respawn itself on every job.
        return [str(node),str(entry),*argv[1:]]
    # An explicit shell/native wrapper still owns its own invocation contract.
    return [str(binary),*argv[1:]]


def json_response(stdout):
    try:
        value=json.loads(stdout)
        return value if isinstance(value,dict) else None
    except (ValueError,TypeError):return None


def retained_response(response):
    result=response.get('result')
    value=dict(result) if isinstance(result,dict) else dict(response)
    for name in ('status','error','summary'):
        if name in response:value[name]=response[name]
    return value


def failure(returncode,argv,stdout,stderr):
    response=json_response(stdout)
    details=[]
    if response:
        result=response.get('result')
        result=result if isinstance(result,dict) else response
        meta=result.get('meta');meta=meta if isinstance(meta,dict) else {}
        for obj in (response,result,meta):
            error=obj.get('error')
            text=error.get('message') if isinstance(error,dict) else error
            text=text or obj.get('errorMessage')
            if isinstance(text,str) and text.strip() and text not in details:details.append(text)
        payloads=result.get('payloads')
        if isinstance(payloads,list):
            for item in payloads:
                if (isinstance(item,dict) and (item.get('isError') or response.get('status') in ('error','timeout','aborted','cancelled'))
                        and not item.get('isReasoning') and not item.get('isCommentary') and isinstance(item.get('text'),str)):
                    if item['text'] not in details:details.append(item['text'])
        summary=response.get('summary')
        if isinstance(summary,str) and summary.strip() and summary not in ('error','failed') and summary not in details:details.append(summary)
        if not details and response.get('status'):details.append('Agent ended with status '+str(response['status']))
    if not details:
        # Preserve the actual diagnostic, rather than selecting stderr warnings
        # over a structured status=error result written to stdout.
        details=[stdout.strip() or stderr.strip() or 'No diagnostic was returned.']
    text=clean('\n'.join(details))
    return OpenClawError(f'OpenClaw {argv[1] if len(argv)>1 else "command"} failed ({returncode}): {text[:2700]}',response)
