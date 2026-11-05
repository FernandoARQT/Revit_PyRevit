from dataclasses import dataclass


@dataclass(frozen=True)
class Candidate:
    counts: tuple
    score: tuple


def empty_candidate(n):
    return Candidate((0,)*n,(0,0,0,0,0,0))


def add(c,i,o,s,first):
    counts=list(c.counts)
    counts[i]+=1
    delta=(o['penalty']+s['plate_cost']+(s['activation_cost'] if first and o['width']!=1200 else 0),
           int(o['category']==3),int(o['category']==2),int(o['width']!=1200),1,-int(o['width']==1200))
    return Candidate(tuple(counts),tuple(a+b for a,b in zip(c.score,delta)))


def best_unique(items,k):
    return sorted({c.counts:c for c in items}.values(),key=lambda c:(c.score,c.counts))[:k]


def ordered_widths(counts,options,reverse=False):
    standard=[]
    special=[]
    for count,o in zip(counts,options):
        (standard if o['width']==1200 else special).extend([o['width']]*count)
    special.sort()
    left=[]
    right=[]
    for i,w in enumerate(special):
        (right if i%2==0 else left).append(w)
    result=left+standard+list(reversed(right))
    return list(reversed(result)) if reverse else result
