"""Private process entry point: JSON in, JSON out, no Revit/WPF imports."""
import json
import os
import sys
import traceback
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))


def main():
    from ARQT_MEP.routing.design.model import RoutingDesignProposal
    from ARQT_MEP.routing.design.service import calculate_graph
    request=Path(sys.argv[1]);response=Path(sys.argv[2]);stop=Path(sys.argv[3])
    try:
        data=json.loads(request.read_text(encoding='utf8'))
        p=RoutingDesignProposal.from_dict(data['proposal'])
        result=calculate_graph(p,data.get('options'),stop.exists)
        reply={'proposal':result.to_dict(),'worker_pid':os.getpid()}
    except Exception:reply={'error':traceback.format_exc(limit=8)}
    temporary=response.with_suffix('.tmp')
    temporary.write_text(json.dumps(reply,allow_nan=False),encoding='utf8');os.replace(temporary,response)


if __name__=='__main__':main()
