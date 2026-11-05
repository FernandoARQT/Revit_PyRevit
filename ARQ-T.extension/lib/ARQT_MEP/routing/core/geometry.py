"""3D distances, triangle BVH and conservative swept-volume predicates in mm."""
import math

EPS=1e-7


def add(a,b):return (a[0]+b[0],a[1]+b[1],a[2]+b[2])
def sub(a,b):return (a[0]-b[0],a[1]-b[1],a[2]-b[2])
def mul(a,s):return (a[0]*s,a[1]*s,a[2]*s)
def dot(a,b):return a[0]*b[0]+a[1]*b[1]+a[2]*b[2]
def cross(a,b):return (a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0])
def norm(a):return math.sqrt(dot(a,a))
def distance(a,b):return norm(sub(a,b))
def unit(a):
    n=norm(a)
    if n<EPS:raise ValueError('Vector de conexión nulo.')
    return mul(a,1/n)
def bounds(points):
    xs,ys,zs=zip(*points)
    return (min(xs),min(ys),min(zs),max(xs),max(ys),max(zs))
def expand(b,r):return (b[0]-r,b[1]-r,b[2]-r,b[3]+r,b[4]+r,b[5]+r)
def overlaps(a,b):return (a[0]<=b[3]+EPS and b[0]<=a[3]+EPS and a[1]<=b[4]+EPS and b[1]<=a[4]+EPS and a[2]<=b[5]+EPS and b[2]<=a[5]+EPS)
def contains(b,p):return (b[0]-EPS<=p[0]<=b[3]+EPS and b[1]-EPS<=p[1]<=b[4]+EPS and b[2]-EPS<=p[2]<=b[5]+EPS)


def segment_distance(a,b,c,d):
    u=sub(b,a);v=sub(d,c);w=sub(a,c);aa=dot(u,u);bb=dot(u,v);cc=dot(v,v);dd=dot(u,w);ee=dot(v,w)
    if aa<EPS:return point_segment(c,d,a)
    if cc<EPS:return point_segment(a,b,c)
    den=aa*cc-bb*bb
    s=max(0,min(1,(bb*ee-cc*dd)/den)) if den>EPS else 0
    t=(bb*s+ee)/cc
    if t<0:t=0;s=max(0,min(1,-dd/aa))
    elif t>1:t=1;s=max(0,min(1,(bb-dd)/aa))
    return norm(sub(add(w,mul(u,s)),mul(v,t)))


def point_segment(a,b,p):
    v=sub(b,a);vv=dot(v,v)
    t=max(0,min(1,dot(sub(p,a),v)/vv)) if vv>EPS else 0
    return distance(p,add(a,mul(v,t)))


def point_triangle(p,a,b,c):
    ab=sub(b,a);ac=sub(c,a);ap=sub(p,a);d1=dot(ab,ap);d2=dot(ac,ap)
    if d1<=0 and d2<=0:return norm(ap)
    bp=sub(p,b);d3=dot(ab,bp);d4=dot(ac,bp)
    if d3>=0 and d4<=d3:return norm(bp)
    vc=d1*d4-d3*d2
    if vc<=0 and d1>=0 and d3<=0:return distance(p,add(a,mul(ab,d1/(d1-d3))))
    cp=sub(p,c);d5=dot(ab,cp);d6=dot(ac,cp)
    if d6>=0 and d5<=d6:return norm(cp)
    vb=d5*d2-d1*d6
    if vb<=0 and d2>=0 and d6<=0:return distance(p,add(a,mul(ac,d2/(d2-d6))))
    va=d3*d6-d5*d4
    if va<=0 and d4-d3>=0 and d5-d6>=0:return distance(p,add(b,mul(sub(c,b),(d4-d3)/((d4-d3)+(d5-d6)))))
    den=va+vb+vc
    if abs(den)<EPS:return min(point_segment(a,b,p),point_segment(b,c,p),point_segment(c,a,p))
    return distance(p,add(a,add(mul(ab,vb/den),mul(ac,vc/den))))


def ray_triangle(p,d,a,b,c):
    ab=sub(b,a);ac=sub(c,a);h=cross(d,ac);det=dot(ab,h)
    if abs(det)<1e-12:return None
    s=sub(p,a);u=dot(s,h)/det
    if u<-1e-9 or u>1+1e-9:return None
    q=cross(s,ab);v=dot(d,q)/det
    if v<-1e-9 or u+v>1+1e-9:return None
    t=dot(ac,q)/det
    return t if t>=-EPS else None


def segment_triangle(a,b,triangle):
    v=sub(b,a);t=ray_triangle(a,v,*triangle)
    if t is not None and t<=1+EPS:return 0.0
    return min(point_triangle(a,*triangle),point_triangle(b,*triangle),
               *(segment_distance(a,b,triangle[i],triangle[(i+1)%3]) for i in range(3)))


class BVH:
    def __init__(self,items,box_of,leaf=8):
        self._build([(item,box_of(item)) for item in items],leaf)

    def _build(self,entries,leaf):
        self.items=None;self.left=None;self.right=None
        boxes=[entry[1] for entry in entries]
        self.box=tuple(min(b[i] for b in boxes) for i in range(3))+tuple(max(b[i+3] for b in boxes) for i in range(3))
        if len(entries)<=leaf:self.items=[entry[0] for entry in entries];return
        axis=max(range(3),key=lambda i:self.box[i+3]-self.box[i])
        ordered=sorted(entries,key=lambda entry:(entry[1][axis]+entry[1][axis+3])/2)
        mid=len(ordered)//2
        self.left=object.__new__(BVH);self.left._build(ordered[:mid],leaf)
        self.right=object.__new__(BVH);self.right._build(ordered[mid:],leaf)

    def query(self,box):
        if not overlaps(self.box,box):return
        if self.items is not None:yield from self.items
        else:
            yield from self.left.query(box);yield from self.right.query(box)


class TriangleSolid:
    def __init__(self,vertices,faces):
        self.triangles=[tuple(tuple(vertices[i]) for i in face) for face in faces]
        if not self.triangles:raise ValueError('Sólido sin triangulación.')
        self.bvh=BVH(self.triangles,lambda t:bounds(t))
        self.box=self.bvh.box

    def inside(self,p):
        if not contains(self.box,p):return False
        d=unit((1,.371390676,.529812942))
        end=add(p,mul(d,norm(sub(self.box[3:],self.box[:3]))*3+1))
        hits=[]
        for triangle in self.bvh.query(bounds([p,end])):
            t=ray_triangle(p,d,*triangle)
            if t is not None and t>EPS:hits.append(t)
        hits.sort();distinct=[]
        for t in hits:
            if not distinct or abs(t-distinct[-1])>1e-5:distinct.append(t)
        return len(distinct)%2==1

    def capsule(self,a,b,r):
        query=expand(bounds([a,b]),r)
        if not overlaps(self.box,query):return False
        if self.inside(a):return True
        if a==b:return any(point_triangle(a,*t)<=r+EPS for t in self.bvh.query(query))
        if self.inside(b):return True
        return any(segment_triangle(a,b,t)<=r+EPS for t in self.bvh.query(query))

    def intersects(self,other):
        if not overlaps(self.box,other.box):return False
        if self.inside(other.triangles[0][0]) or other.inside(self.triangles[0][0]):return True
        for triangle in other.triangles:
            for own in self.bvh.query(expand(bounds(triangle),EPS)):
                if any(segment_triangle(triangle[i],triangle[(i+1)%3],own)<=EPS for i in range(3)):return True
                if any(segment_triangle(own[i],own[(i+1)%3],triangle)<=EPS for i in range(3)):return True
        return False


def cylinder_mesh(a,b,r,sides=24):
    """Circumscribed polygonal cylinder, conservative with flat ends."""
    direction=unit(sub(b,a));u=unit(cross(direction,(1,0,0) if abs(direction[0])<.8 else (0,1,0)));v=cross(direction,u)
    radius=r/math.cos(math.pi/sides);vertices=[]
    for p in (a,b):
        for j in range(sides):
            angle=2*math.pi*j/sides
            vertices.append(add(p,add(mul(u,radius*math.cos(angle)),mul(v,radius*math.sin(angle)))))
    vertices.extend([tuple(a),tuple(b)]);faces=[]
    for j in range(sides):
        k=(j+1)%sides
        faces.extend([(j,k,sides+k),(j,sides+k,sides+j),(2*sides,k,j),(2*sides+1,sides+j,sides+k)])
    return vertices,faces


def box_mesh(b):
    vertices=[(x,y,z) for x in (b[0],b[3]) for y in (b[1],b[4]) for z in (b[2],b[5])]
    faces=[(0,1,3),(0,3,2),(4,6,7),(4,7,5),(0,4,5),(0,5,1),(2,3,7),(2,7,6),(0,2,6),(0,6,4),(1,5,7),(1,7,3)]
    return vertices,faces
