"""Optional binary master with lazy geometric conflict cuts (SciPy/HiGHS)."""
import time
import warnings


def solve(admitted,order,conflict,deadline,cancelled=None,known_conflicts=()):
    try:
        from ...runtime import scientific_context
        with scientific_context():
            import numpy as np
            from scipy.optimize import milp,Bounds,LinearConstraint
    except ImportError:return None,{'backend':'PYTHON_BRANCH_AND_BOUND','available':False}
    columns=[(rid,i) for rid in order for i in range(len(admitted.get(rid,[])))]
    if not columns:return {},{'backend':'SCIPY_HIGHS_MILP','cuts':0}
    rows=[];upper=[];cuts=set();calls=0;best=None;primary_optimal=False;primary_connections=None
    for rid in order:
        rows.append([int(key==rid) for key,_ in columns]);upper.append(1)
    column_index={key:i for i,key in enumerate(columns)}
    for a,b in known_conflicts:
        if a not in column_index or b not in column_index:continue
        pair=tuple(sorted((column_index[a],column_index[b])))
        if pair in cuts:continue
        cuts.add(pair);rows.append([int(i in pair) for i in range(len(columns))]);upper.append(1)
    initial_cuts=len(cuts)
    condition=np.array([int(admitted[rid][i]['status']=='CONDITIONAL') for rid,i in columns],dtype=float)
    cost=np.array([admitted[rid][i].get('cost',{}).get('total',0) for rid,i in columns],dtype=float)
    pressure=[admitted[rid][i].get('pressure_assessment',{}) for rid,i in columns]
    pressure_known=all(p.get('status')=='ESTIMATED_FROM_REFERENCED_DATA' and admitted[rid][i].get('pressure_objective_enabled',False)
                       for (rid,i),p in zip(columns,pressure))
    objectives=[-np.ones(len(columns)),condition]
    if pressure_known:objectives.append(np.array([p['pressure_loss_pa'] for p in pressure]))
    objectives.append(cost)
    while time.monotonic()<deadline and not (cancelled and cancelled()):
        matrix=list(rows);low=[-np.inf]*len(rows);high=list(upper);selection=None;restart=False
        # Lexicographic objectives, without arbitrary giant penalty constants.
        for objective in objectives:
            remaining=deadline-time.monotonic()
            if remaining<=0:break
            with warnings.catch_warnings():
                warnings.filterwarnings('ignore',message='Unrecognized options detected.*threads.*')
                answer=milp(objective,integrality=np.ones(len(columns)),bounds=Bounds(0,1),
                    constraints=LinearConstraint(np.array(matrix,dtype=float),low,high),
                    options={'time_limit':min(remaining,2.),'mip_rel_gap':0.0,'threads':1})
            calls+=1
            if answer.x is None:break
            selected=[i for i,value in enumerate(answer.x) if value>.5]
            new=[]
            for pos,a in enumerate(selected):
                for b in selected[pos+1:]:
                    ra,ia=columns[a];rb,ib=columns[b]
                    if conflict(ra,ia,rb,ib):new.append(tuple(sorted((a,b))))
            if new:
                for pair in new:
                    if pair in cuts:continue
                    cuts.add(pair);rows.append([int(i in pair) for i in range(len(columns))]);upper.append(1)
                restart=True;break
            selection={columns[i][0]:columns[i][1] for i in selected};best=selection
            # A time-limited incumbent is useful, but cannot certify the
            # primary optimum for subsequent lexicographic stages.
            if answer.status!=0:break
            if objective is objectives[0]:primary_optimal=True;primary_connections=len(selected)
            value=float(objective@answer.x);matrix.append(objective);low.append(value-1e-7);high.append(value+1e-7)
        if not restart:break
    return best,{'backend':'SCIPY_HIGHS_MILP','cuts':len(cuts),'initial_cuts':initial_cuts,'solver_calls':calls,'primary_optimal':primary_optimal,'primary_connections':primary_connections,'limit_reached':time.monotonic()>=deadline,'verified_pressure_objective':pressure_known,
                 'scope':'supplied candidates; exact swept-volume conflict separation'}
