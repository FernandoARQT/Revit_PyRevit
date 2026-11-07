"""Deterministic tangent-circle seeds for short oriented routing problems.

These are candidates, never certificates: the normal swept-volume checker
must accept every resulting 3D fillet and both exact connector tangents.
"""
import math
from . import geometry as g


def _arc(center,start,end,sign,radius):
    a=math.atan2(start[1]-center[1],start[0]-center[0])
    b=math.atan2(end[1]-center[1],end[0]-center[0])
    sweep=((b-a)*sign)%(2*math.pi)
    if sweep<1e-7:return [list(start),list(end)]
    count=math.ceil(sweep/(math.pi/3));step=sweep/count
    controls=[list(start)]
    for i in range(count):
        middle=a+sign*(i+.5)*step
        controls.append([center[0]+radius/math.cos(step/2)*math.cos(middle),
                         center[1]+radius/math.cos(step/2)*math.sin(middle),0.])
    controls.append(list(end))
    return controls


def seeds(start,goal,incoming,outgoing,radius):
    """Four CSC turn families, with bounded, explicit elevation interpolation."""
    if any(math.hypot(*v[:2])<.5*g.norm(v) for v in (incoming,outgoing)):return []
    u=g.unit((incoming[0],incoming[1],0));v=g.unit((outgoing[0],outgoing[1],0))
    result=[]
    for first in (-1,1):
        for last in (-1,1):
            c=(start[0]-first*radius*u[1],start[1]+first*radius*u[0])
            d=(goal[0]-last*radius*v[1],goal[1]+last*radius*v[0])
            delta=(d[0]-c[0],d[1]-c[1]);distance=math.hypot(*delta)
            if distance<1e-6:continue
            ratio=(first-last)*radius/distance
            if abs(ratio)>1:continue
            heading=math.atan2(delta[1],delta[0])+math.asin(ratio)
            tangent=(math.cos(heading),math.sin(heading))
            if sum(x*y for x,y in zip(delta,tangent))<1e-5:continue
            a=[c[0]+first*radius*tangent[1],c[1]-first*radius*tangent[0],0.]
            b=[d[0]+last*radius*tangent[1],d[1]-last*radius*tangent[0],0.]
            controls=_arc(c,start,a,first,radius)+_arc(d,b,goal,last,radius)
            clean=[]
            for q in controls:
                if not clean or math.hypot(q[0]-clean[-1][0],q[1]-clean[-1][1])>1e-5:clean.append(q)
            if len(clean)<2:continue
            lengths=[0.]
            for a,b in zip(clean,clean[1:]):lengths.append(lengths[-1]+math.hypot(b[0]-a[0],b[1]-a[1]))
            lo=lengths[min(1,len(clean)-1)];hi=lengths[max(0,len(clean)-2)]
            if abs(goal[2]-start[2])>1e-4 and hi<=lo+1e-4:continue
            z0=start[2]+lo*incoming[2]/math.hypot(*incoming[:2])
            z1=goal[2]-(lengths[-1]-hi)*outgoing[2]/math.hypot(*outgoing[:2])
            for i,q in enumerate(clean):
                t=max(0,min(1,(lengths[i]-lo)/max(1e-5,hi-lo)))
                q[2]=z0+t*(z1-z0)
            clean[0]=list(start);clean[-1]=list(goal)
            result.append(clean)
    return sorted(result,key=lambda qs:sum(g.distance(a,b) for a,b in zip(qs,qs[1:])))
