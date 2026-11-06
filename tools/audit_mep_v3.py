"""Reproducible module inventory, hashes and V3 architectural disposition."""
import ast
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
LIB=ROOT/'ARQ-T.extension/lib/ARQT_MEP'
ADAPT={
    'plan_canvas.py':'Reutilizar PlanCanvas/draw_scene mediante la capa de corredores, sin sustituir navegación.',
    'routing/service.py':'Separar la estrategia global V3 de la búsqueda individual; conservar la entrada anterior.',
    'routing/gui.py':'Alojar aquí todo V3, reutilizando la planta de Distribuir ventilación sin modificar aquel botón.',
    'routing/mcp.py':'Extender las operaciones de este despachador con el dominio V3; sin segundo servidor.',
    'routing/settings.py':'No heredar la conversión forzada de principales a flexibles; V3 necesita contrato propio.',
    'routing/core/global_planner.py':'Conservar prepare/solve para baseline; extraer la selección conjunta previa al detalle.',
    'routing/core/corridor_assignment.py':'La asignación actual es un beam de tomas/estaciones, no selección global de corredores.',
    'routing/core/corridor_capacity.py':'La estimación existente no acredita secciones variables ni curvas de haces.',
    'routing/core/multi_route.py':'Conservar el trazado anterior; invocar por diagnóstico desde el futuro selector V3.',
    'routing/core/portfolio.py':'Evitar ejecutar sistemáticamente todo el portfolio desde el camino V3.',
    'routing/core/integer_master.py':'Reutilizar SciPy/HiGHS y cortes; añadir ocupación de corredor y capacidades en H3.',
    'routing/core/candidate_repository.py':'Conservar repositorio y añadir revisión de propuesta/contexto V3 en su adaptador.',
    'routing/revit/extractor.py':'Reutilizar sólidos/planos; normalizar identidad techo-sólido y nivel en la frontera V3.',
    'routing/revit/proposals.py':'Separar prueba geométrica nativa, aprobación y certificación de red completa.',
    'routing/revit/network_model.py':'Reutilizar auditoría/conectores; adaptar MAIN rígida y plan explícito V3 en H9.',
    'routing/systems/heat_recovery_topology.py':'Reutilizar identificación de tomas; posponer asignación final hasta la selección global.',
}


def main():
    records=[]
    for path in sorted(LIB.rglob('*.py')):
        if '__pycache__' in path.parts or '/design/' in path.as_posix():continue
        relative=path.relative_to(LIB).as_posix();content=path.read_bytes()
        tree=ast.parse(content.decode('utf-8-sig'))
        reason=ADAPT.get(relative)
        records.append({'module':relative,'disposition':'Adaptar' if reason else 'Conservar',
            'reason':reason or 'Conservar su responsabilidad y sus pruebas; integrar mediante adaptadores, sin reescritura de estilo.',
            'responsibility':(ast.get_docstring(tree) or '').split('\n')[0],
            'symbols':[n.name for n in tree.body if isinstance(n,(ast.FunctionDef,ast.ClassDef))],
            'sha256':hashlib.sha256(content).hexdigest()})
    output=ROOT/'tests/artifacts/routing-v3-module-audit.json'
    output.write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'modules':len(records),'adapt':sum(r['disposition']=='Adaptar' for r in records),'output':str(output)},ensure_ascii=False))


if __name__=='__main__':main()
