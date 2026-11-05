"""Conservative rectangular cross-section packing, advisory until 3-D checks."""


def estimate(width_mm,height_mm,radii):
    lanes=[];x=y=row_height=0.;unplaced=[]
    for route_id,radius in sorted(radii.items(),key=lambda item:-item[1]):
        diameter=2*radius
        if diameter>width_mm or diameter>height_mm:
            unplaced.append(route_id);continue
        if x+diameter>width_mm+1e-6:y+=row_height;x=0.;row_height=0.
        if y+diameter>height_mm+1e-6:unplaced.append(route_id);continue
        lanes.append({'route_id':route_id,'center_cross_section_mm':[x+radius,y+radius],
                      'effective_radius_mm':radius})
        x+=diameter;row_height=max(row_height,diameter)
    return {'width_mm':width_mm,'height_mm':height_mm,'lanes':lanes,'unplaced_routes':unplaced,
            'status':'ESTIMATED_CAPACITY_CONFLICT' if unplaced else 'RECTANGULAR_PACKING_CANDIDATE',
            'scope':'Advisory rectangular section only; no obstacle, slope, approach or 3-D route certification'}
