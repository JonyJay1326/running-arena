"""单次蹬伸片段；不含助跑、空间触发或空中收腿。来自实验控制器的起跳段。
需要自行决定何时调用 takeoff(obs)，将函数复制进自己的 bot.py。
本文件 act 仅用于板前练习，从正式起点调用不保证得分。
"""
START_X = 7.0
begin = None
extension = None
def pose(h,k,s,a): return [h,k,h,k,s,s,a,a]
def takeoff(obs):
    global begin, extension
    if begin is None: begin = obs['time']
    elapsed = obs['time'] - begin
    if extension is None:
        ready = elapsed >= .331 and obs['torso_angle'] >= .710 and obs['vy'] <= 1.829
        if ready or elapsed >= .9: extension = obs['time']
        else: return pose(1., -.874, .051, 1.)
    return pose(-.391, .639, .688, -.342)
def act(obs): return takeoff(obs)
