# 资格赛：浅幅步行、接触与姿态触发、唯一一次蹬伸、固定空中及落地保持。
import math
START_X=9.0
GP=[1.0555968414114405, 0.3297133906125388, 0.02443813186067961, 0.9858896652348953, 0.17101020549169338, 0.19914476536352926, 0.7558932750394904, 0, 1.2591617406975912, 0.6735121503128549]
def gait(o):
    freq,amp,bias,kbase,kamp,pitch,kp,kd,phase,ramp=GP
    t=o['time'];s=min(1,t/ramp);actions=[];ankles=[]
    for side in [0,1]:
        ph=t*freq*2*math.pi+side*math.pi+phase
        h=-1/9+s*(bias+amp*math.cos(ph)+1/9)
        k=1-s*((1-kbase)+kamp*max(0,-math.sin(ph)))
        ha=math.radians(-60+(h+1)*67.5)
        ka=math.radians(-110+(k+1)*55)
        aa=pitch*s+kp*(pitch*s-o['torso_angle'])-kd*o['torso_omega']-ha-ka
        a=(math.degrees(aa)+50)/55-1
        actions.extend([h,k]);ankles.append(max(-1,min(1,a)))
    return actions+[-.39,-.39]+ankles

P=[11.335851968830665, 0.36474969793798967, 0.2625123353296303, 0.8049688346074602, 0.6151081726157323, 0.99899313192267, -0.6146671118980471, 0.990489596525304, -0.16473974980260508, -0.6814783106242037, -0.45689940011866287, 0.625943975991487, 0.6566460380031879, 0.5468700672809871, 0.028850163475164656, 0.8040810682340388, 0.0]
# 状态只向前推进，落地后不会重新蓄力。
phase=0
stamp=0
transition=None
def act(o):
    global phase,stamp,transition
    xgate,ramp,wait,angle,dur,h,k,a,arm,lh,la,ls,fh,fk,fs,fa,ly=P
    t=o['time'];ground=o['contact']['foot_L'] or o['contact']['foot_R']
    if phase==0:
        if ground and (o['x']>=xgate or (o['x']>=10.6 and o['torso_angle']>=.2)):
            phase=1;stamp=t;transition=o['joints'][:]
        else:return gait(o)
    crouch=[h,k,h,k,arm,arm,a,a]
    if phase==1:
        u=min(1,(t-stamp)/ramp)
        if u<1:return [b+(c-b)*u for b,c in zip(transition,crouch)]
        phase=2;stamp=t
    if phase==2:
        if (ground and o['torso_angle']>=angle) or t-stamp>=wait:
            phase=3;stamp=t
        else:return crouch
    if phase==3:
        if t-stamp<dur:return [lh,1,lh,1,ls,ls,la,la]
        phase=4
    if phase==4 and o['vy'] < -1 and o['y']<ly:phase=5
    if phase==5:return [0.6371834859404953, 0.9169588630814187, 0.6371834859404953, 0.9169588630814187, 0.45213587968688224, 0.45213587968688224, 0.48078610748511974, 0.48078610748511974]
    return [fh,fk,fh,fk,fs,fs,fa,fa]
