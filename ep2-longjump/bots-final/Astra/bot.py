# 决赛：浅幅步行、接触与姿态触发、唯一一次蹬伸、固定空中及落地保持。
import math
START_X=9.0
GP=[1.0555968414114405, 0.3297133906125388, 0.028892308352304965, 0.9812645544536375, 0.16975197875216752, 0.16726819289675401, 0.8703685857711438, 0, 1.271878766534294, 0.6735121503128549]
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

P=[11.320598885679722, 0.3647082079495067, 0.20243953513981977, 0.815335363791349, 0.41719404847412633, 1, -0.6026189960390115, 0.9997394526997475, -0.1621267257131387, -0.6718427823627726, -0.5247865975856261, 0.5970478762800193, 0.45066445058634674, 0.4491116316153613, -0.5587255615198665, 0.946222973388625, 0.402485859475909, 0.887975667600949, 10.538696133594422, 0.20102676190977203, 100.0, 0.0]
# 状态只向前推进，落地后不会重新蓄力。
phase=0
stamp=0
transition=None
def control(o):
    global phase,stamp,transition
    xgate,ramp,wait,angle,dur,h,k,a,arm,lh,la,ls,fh,fk,fs,fa,ly,ek,early_x,early_angle,ready_vy,min_hold=P
    t=o['time'];ground=o['contact']['foot_L'] or o['contact']['foot_R']
    if phase==0:
        if ground and (o['x']>=xgate or (o['x']>=early_x and o['torso_angle']>=early_angle)):
            phase=1;stamp=t;transition=o['joints'][:]
        else:return gait(o)
    crouch=[h,k,h,k,arm,arm,a,a]
    if phase==1:
        u=min(1,(t-stamp)/ramp)
        if u<1:return [b+(c-b)*u for b,c in zip(transition,crouch)]
        phase=2;stamp=t
    if phase==2:
        if (ground and o['torso_angle']>=angle and o['vy']<=ready_vy and t-stamp>=min_hold) or t-stamp>=wait:
            phase=3;stamp=t
        else:return crouch
    if phase==3:
        if t-stamp<dur:return [lh,ek,lh,ek,ls,ls,la,la]
        phase=4
    if phase==4 and o['vy'] < -1 and o['y']<ly:phase=5
    if phase==5:return [0.6371834859404953, 0.9169588630814187, 0.6371834859404953, 0.9169588630814187, 0.45213587968688224, 0.45213587968688224, 0.48078610748511974, 0.48078610748511974]
    return [fh,fk,fh,fk,fs,fs,fa,fa]

# 接口限幅与引擎一致，覆盖插值起点的关节读数轻微越界。
def act(obs):
    return [max(-1.0, min(1.0, value)) for value in control(obs)]
