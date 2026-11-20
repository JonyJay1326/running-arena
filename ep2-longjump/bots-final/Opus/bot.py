import math
START_X=9.0
P=[0.5047183838104851, 0.09637826365737456, 0.9533336560186134, 0.9061220513365416, 2.325229312860239, 0.13938326819806768, 0.09062169305950885, 12.909425560638828, -0.8818224455851817, 0.5743545504357369, 0.6, 11.311939929244922, 1, -0.5261390193582184, -0.19642924278324078, 0.2952527178506194, 0.2302773998511328, -0.7463833783207691, -1, -0.40322463794365265, 0.27210900242255637, -0.9917352236780722, -0.31311443213980217, 0.8792381835839405, 0.7389157448500452, 0.616618402504609, -0.23554450661719686, 0.7242728581087443, -0.9307249310171838, -0.9080748236137453]
S={'ph':0,'t0':0}
def cl(v):return -1 if v<-1 else (1 if v>1 else v)
def act(obs):
    t=obs['time'];c=obs['contact'];air=not any(c.values())
    h0,ha,k0,ka,dk,a0_,aa,w,s0,g,ta,Xj,h1,k1,a1,s1,T1,h2,a2,s2,T2,lh,lk,la,ls,lv,h3,k3,s3,a3=P
    fb=g*(obs['torso_angle']-ta)
    if S['ph']==0:
        if obs['x']>=Xj:S['ph']=1;S['t0']=t
        else:
            ramp=min(1,t/0.5)
            o=[]
            for p in (0,math.pi):
                ph=w*t+p
                o.append((cl(h0+ramp*ha*math.sin(ph)+fb),cl(k0+ramp*ka*math.sin(ph+dk)),cl(a0_+ramp*aa*math.sin(ph+dk))))
            return [o[0][0],o[0][1],o[1][0],o[1][1],s0,s0,o[0][2],o[1][2]]
    if S['ph']==1:
        if t-S['t0']>=T1:S['ph']=2;S['t0']=t
        return [cl(h1+fb),k1,cl(h1+fb),k1,s1,s1,a1,a1]
    if S['ph']==2:
        if t-S['t0']>T2 and air:S['ph']=3
        return [h2,1,h2,1,s2,s2,a2,a2]
    if S['ph']==3:
        if obs['vy']<lv:S['ph']=4
        return [h3,k3,h3,k3,s3,s3,a3,a3]
    return [lh,lk,lh,lk,ls,ls,la,la]
