import math
from functools import reduce
from candidates import empty_candidate,add,best_unique,ordered_widths
from settings import validate_settings


def optimize_margins(remainder,left,right):
    low=max(left[0],remainder-right[2])
    high=min(left[2],remainder-right[0])
    if low>high+1e-7:
        return None
    l=min(high,max(low,(remainder+left[1]-right[1])/2))
    return l,remainder-l


def calculate_dp_limit(width,margins,options,g,near=False,k=3):
    limit=width-sum(m[0] for m in margins)
    return max(0,math.floor((limit+(k*max(o['width'] for o in options) if near else 0))/g))


def solve_k_best(width_mm,s,k=3):
    validate_settings(s)
    if not math.isfinite(width_mm) or width_mm<=0:
        raise ValueError('Anchura del paño inválida.')
    options=sorted([dict(o) for o in s['widths'] if o['enabled'] and o['width']>=s['minimum_exceptional']],key=lambda o:-o['width'])
    if not options:
        raise ValueError('No hay anchuras activas que respeten el mínimo excepcional.')
    g=reduce(math.gcd,(o['width'] for o in options))
    hmin=s['left'][0]+s['right'][0]
    hmax=s['left'][2]+s['right'][2]
    lower=math.ceil((width_mm-hmax)/g)
    upper=math.floor((width_mm-hmin)/g)
    def run(limit):
        if limit>500000:
            raise ValueError('Más de 500.000 estados discretos. Reduzca el paño o el catálogo.')
        previous={0:[empty_candidate(len(options))]}
        for i,o in enumerate(options):
            step=o['width']//g
            used={}
            current={}
            for total in range(limit+1):
                values=[]
                if total>=step:
                    values=[add(c,i,o,s,True) for c in previous.get(total-step,())]
                    values.extend(add(c,i,o,s,False) for c in used.get(total-step,()))
                    if values:
                        used[total]=best_unique(values,k)
                merged=best_unique(list(previous.get(total,()))+used.get(total,[]),k)
                if merged:
                    current[total]=merged
            previous=current
        return previous
    def finalize(states,admissible):
        results=[]
        for units,cs in states.items():
            total=units*g
            if total==0:
                continue
            remainder=width_mm-total
            margins=optimize_margins(remainder,s['left'],s['right'])
            if bool(margins is not None)!=admissible:
                continue
            for c in cs:
                excess=max(0,remainder-hmax)
                deficit=max(0,hmin-remainder)
                l,r=margins if margins else (None,None)
                deviation=(l-s['left'][1])**2+(r-s['right'][1])**2 if margins else 0
                order=ordered_widths(c.counts,options,s['reverse'])
                tol=sum(s['tolerance_standard'] if w==1200 else s['tolerance_other'] for w in order)
                robust=optimize_margins(remainder-tol,s['left'],s['right']) is not None and optimize_margins(remainder+tol,s['left'],s['right']) is not None
                key=c.score+(deviation,)+c.counts if admissible else (excess+deficit,)+c.score+c.counts
                results.append({'counts':c.counts,'score':c.score,'total_width':total,'plate_count':len(order),'left':l,'right':r,
                                'excess':excess,'deficit':deficit,'valid':admissible,'order':order,'options':options,
                                'key':key,'tolerance_sum':tol,'tolerance_extremes_fit':robust,
                                'warning':'Despiece geométricamente válido. Montaje pendiente de validación dimensional.' if admissible else 'No admisible: ajustar catálogo o holguras.'})
        return sorted(results,key=lambda r:r['key'])[:k]
    states=run(calculate_dp_limit(width_mm,[s['left'],s['right']],options,g)) if lower<=upper else {}
    results=finalize(states,True)
    if results:
        return results
    return finalize(run(calculate_dp_limit(width_mm,[s['left'],s['right']],options,g,True,k)),False)
