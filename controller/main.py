import os,signal,threading
from pathlib import Path
from dispatch import serve
from store import Store
from worker import forever

"""
Use 'python3 -m py_compile   ~/.local/share/cvpilot/app/controller/main.py' to recompile
"""

path=Path(os.environ.get('CVPILOT_STATE_DIR',str(Path.home()/'.local/state/cvpilot')))
path.mkdir(parents=True,exist_ok=True)
store=Store(path/'jobs.sqlite')
stop=threading.Event();servers=[]
def handle(*a):
    stop.set()
    for s in servers:
        threading.Thread(target=s.shutdown, daemon=True).start()
signal.signal(signal.SIGTERM,handle)
signal.signal(signal.SIGINT,handle)
threading.Thread(target=forever,args=(store,stop),daemon=True).start()
print('CVPilot controller online',flush=True)
serve(path/'controller.sock',store,servers)
