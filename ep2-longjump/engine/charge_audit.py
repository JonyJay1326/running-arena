"""Same offline charge thresholds as the previously reviewed demonstration."""
import math

def review(frames,shapes,takeoff_t=None):
    names={s['name']:s['body'] for s in shapes}
    com=[f['com_y'] for f in frames]
    flights=[];i=0
    while i<len(frames):
        if frames[i]['contacts']:i+=1;continue
        start=i
        while i<len(frames) and not frames[i]['contacts']:i+=1
        if i==len(frames) or start==0:continue
        duration=(i-start)/60
        if duration<.1:continue
        grounded=[k for k in range(max(0,start-24),start) if frames[k]['contacts']]
        if not grounded:continue
        low=min(grounded,key=lambda k:com[k])
        drop=max(com[k] for k in grounded if k<=low)-com[low]
        extension=com[start]-com[low]
        vy=(com[min(start+2,len(frames)-1)]-com[start])*30
        bent=0
        for side in ['左','右']:
            knees=[frames[k]['poses'][names[side+'小腿']][2]-frames[k]['poses'][names[side+'大腿']][2] for k in grounded]
            bent=max(bent,max(knees)-min(knees))
        charged=len(grounded)>=5 and (drop>=.06 or extension>=.15) and extension>=.05 and bent>=.25 and vy>=2.2 and duration>=.35
        flights.append(dict(t=frames[start]['obs']['time'],duration=duration,com_vy=vy,crouch_drop=drop,extension=extension,knee_change=bent,charge_candidate=charged))
    cutoff=takeoff_t+.05 if takeoff_t is not None else 20
    before=[f for f in flights if f['t']<=cutoff]
    pairs=[(a['t'],b['t']) for a,b in zip(before,before[1:]) if a['charge_candidate'] and b['charge_candidate'] and 0<=b['t']-a['t']-a['duration']<=.6]
    return {'flights':flights,'charge_pairs':pairs,'charge_flag':bool(pairs)}

def capture(r,frames,shapes):
    if not shapes:
        for i,b in enumerate(r._w_damped):
            for s in b.shapes:
                d={'body':i,'name':r.shape_names[s]}
                if hasattr(s,'get_vertices'):d['vertices']=[list(v) for v in s.get_vertices()]
                else:d.update(radius=s.radius,offset=list(s.offset))
                shapes.append(d)
    frames.append({'obs':r.observe(),'poses':[(b.position.x,b.position.y,b.angle) for b in r._w_damped],'contacts':[(r.shape_names[s],x) for s,x in r.step_contacts],'com_y':sum(b.mass*b.local_to_world(b.center_of_gravity).y for b in r._w_damped)/sum(b.mass for b in r._w_damped)/100})
