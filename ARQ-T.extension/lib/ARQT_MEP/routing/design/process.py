"""Bounded external calculation; scientific libraries never run inside Revit."""
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from ..parallel import executable
from .model import RoutingDesignProposal


def calculate(proposal,options,cancelled=None):
    with tempfile.TemporaryDirectory(prefix='arqt-v3-graph-') as folder:
        folder=Path(folder);request=folder/'request.json';response=folder/'response.json';stop=folder/'cancel'
        request.write_text(json.dumps({'proposal':proposal.to_dict(),'options':options},allow_nan=False),encoding='utf8')
        env=dict(os.environ,OMP_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1',
            NUMEXPR_NUM_THREADS='1',VECLIB_MAXIMUM_THREADS='1',PYTHONUTF8='1')
        deadline=time.monotonic()+float(options.get('seconds',30))+30;cancelled_at=None
        with open(folder/'worker.log','w',encoding='utf8') as log:
            process=subprocess.Popen([executable(),'-u',str(Path(__file__).with_name('worker.py')),str(request),str(response),str(stop)],
                stdout=log,stderr=log,env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:
                while process.poll() is None:
                    if cancelled and cancelled() and cancelled_at is None:stop.touch();cancelled_at=time.monotonic()
                    if time.monotonic()>deadline or cancelled_at is not None and time.monotonic()-cancelled_at>3:
                        raise InterruptedError('Cálculo cancelado o fuera del presupuesto; se conserva la propuesta anterior.')
                    time.sleep(.05)
                if process.returncode or not response.exists():
                    log.flush();detail=(folder/'worker.log').read_text(encoding='utf8')[-2000:]
                    raise RuntimeError('El proceso de cálculo terminó sin un resultado publicable. '+detail)
                reply=json.loads(response.read_text(encoding='utf8'))
                if 'error' in reply:raise ValueError(reply['error'])
                return RoutingDesignProposal.from_dict(reply['proposal'])
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:process.wait(timeout=2)
                    except subprocess.TimeoutExpired:process.kill();process.wait(timeout=2)
