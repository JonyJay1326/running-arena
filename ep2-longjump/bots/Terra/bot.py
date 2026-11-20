import math
START_X=9.0
P=[12.454737836884576, -0.05494786541998927, 0.9573290774701545, 0.43099533579019084, 1.2359931878153214, 2.856446070806885, 0.5024947669478639, -0.39074260999705424, -0.6729008527830577]
J=[1.225949545091589, 0.2280026309072411, -0.3412507242869724, -0.9163391285893643, 1, 0.9980503087883534, -0.7126888516931728, 0.9967441694823538, -0.42714308143039864, -0.7880174874540011]
safe_abort=False
sand_landed=False
def act(o):
    global safe_abort, sand_landed
    t=o['time'];p=J
    if t>1.8 and o['x']>=12 and any(o['contact'].values()): sand_landed=True
    if sand_landed: return [max(-1.0,min(1.0,v)) for v in o['joints']]
    if .8<=t<.81 and o['y']>1.59: safe_abort=True
    if safe_abort: return [max(-1.0,min(1.0,v)) for v in o['joints']]
    if t >= 2.2357564485612835: return [-0.8602226499185103,-0.32573897967070775,-0.8602226499185103,-0.32573897967070775,-0.8315561265095828,-0.8315561265095828,-0.7310277811488702,-0.7310277811488702]
    if t<p[0]:
        f,h,a,k,b,phase,n,arm,tilt=P
        s=math.sin(f*t); s2=math.sin(f*t+phase)
        values=[h+a*s+tilt*o['torso_angle'],k+b*s2,h-a*s+tilt*o['torso_angle'],k-b*s2,arm,arm,n,n]
        return [max(-1.0,min(1.0,v)) for v in values]
    if t<p[0]+p[1]: return [p[2],p[3],p[2],p[3],p[4],p[4],p[5],p[5]]
    return [p[6],p[7],p[6],p[7],p[8],p[8],p[9],p[9]]

