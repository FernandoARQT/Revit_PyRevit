"""Project-scoped JSON proposals with atomic replacement and optimistic concurrency.

Draft edits only write this sidecar store, never a Revit transaction. Both WPF
and the installed MCP dispatcher use the same files and expected_version.
"""
import copy
import contextlib
import hashlib
import json
import os
import re
import tempfile
import time
from . import proposal as model

ROOT=os.path.abspath(os.path.join(os.path.dirname(__file__),'..','..','data','ventilation'))


def _path(document_id,proposal_id):
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}',proposal_id):raise ValueError('Identificador de propuesta no válido.')
    project=hashlib.sha256(document_id.encode()).hexdigest()[:24]
    return os.path.join(ROOT,project,proposal_id+'.json')


@contextlib.contextmanager
def _lock(path):
    os.makedirs(os.path.dirname(path),exist_ok=True);lockpath=path+'.lock';deadline=time.monotonic()+3
    while True:
        try:fd=os.open(lockpath,os.O_CREAT|os.O_EXCL|os.O_WRONLY);break
        except FileExistsError:
            if time.monotonic()>deadline:raise ValueError('La propuesta está ocupada; vuelva a intentarlo. Un bloqueo persistente requiere revisar la sesión anterior.')
            time.sleep(.02)
    try:yield
    finally:os.close(fd);os.unlink(lockpath)


def _write(path,data):
    fd,tmp=tempfile.mkstemp(dir=os.path.dirname(path),prefix='proposal-',suffix='.tmp')
    try:
        with os.fdopen(fd,'w',encoding='utf8') as f:
            json.dump(data,f,ensure_ascii=False,allow_nan=False,separators=(',',':'));f.flush();os.fsync(f.fileno())
        # Windows indexers/sync clients can briefly deny replacement even while
        # our proposal lock is held. Retry without exposing a partially written JSON.
        for attempt in range(20):
            try:os.replace(tmp,path);break
            except PermissionError:
                if attempt==19:raise
                time.sleep(.025)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)


def get(document_id,proposal_id):
    path=_path(document_id,proposal_id)
    with open(path,encoding='utf8') as f:data=json.load(f)
    model.check(data)
    if data['document_id']!=document_id:raise ValueError('Propuesta de otro documento.')
    return data


def create(proposal):
    model.check(proposal);path=_path(proposal['document_id'],proposal['proposal_id'])
    with _lock(path):
        if os.path.exists(path):raise ValueError('La propuesta ya existe.')
        _write(path,proposal)
    return proposal


def update(document_id,proposal_id,expected_version,operation,payload=None):
    path=_path(document_id,proposal_id)
    with _lock(path):
        current=get(document_id,proposal_id)
        if type(expected_version)!=int or current['version']!=expected_version:
            raise ValueError('VERSION_CONFLICT: versión actual {}, recibida {}. Recargue la propuesta.'.format(current['version'],expected_version))
        result=model.edit(current,operation,payload or {},expected_version)
        _write(path,result)
    return result


def replace_after_commit(current,expected_version):
    path=_path(current['document_id'],current['proposal_id'])
    with _lock(path):
        prior=get(current['document_id'],current['proposal_id'])
        if prior['version']!=expected_version:raise ValueError('VERSION_CONFLICT: no se sobrescribe una propuesta editada durante la aplicación.')
        current=copy.deepcopy(current);current['version']=expected_version+1;current=model.seal(current);_write(path,current)
    return current


def list_proposals(document_id,housing_id=None):
    folder=os.path.dirname(_path(document_id,'index'))
    if not os.path.isdir(folder):return []
    result=[]
    for name in os.listdir(folder):
        if not name.endswith('.json'):continue
        data=get(document_id,name[:-5])
        if housing_id is None or data.get('housing_id')==housing_id:result.append(data)
    return sorted(result,key=lambda p:p['updated_utc'],reverse=True)
