"""EP2 跳远资格赛 bot：低强度蹬伸接近 + takeoff 技能包的一次正式大跳。

只用 math/random 允许的接口；状态保存在模块级变量（每跳模块重新加载）。
输出 8 项：[髋L, 膝L, 髋R, 膝R, 肩L, 肩R, 踝L, 踝R]。

动作分三段：
  1) 接近：技能包预屈姿态与蹬伸姿态按周期交替，但蹬伸只走到 30% 幅度，
     属于小幅短腾空接近（不构成连续蓄力大跳）；走满 2 个周期或到达触发位置即起跳。
  2) 起跳：沿用 takeoff 技能包自身的触发条件（躯干前倾 ≥0.710 rad 且竖直速度 ≤1.829），
     触发后执行技能包的蹬伸动作，这是全场唯一一次正式大跳。
  3) 腾空/落地：双腿前伸（髋 1.0、膝 1.0、肩 0.2、踝 -0.5），让脚先落地，尽量把落点推远。
"""
import math

START_X = 9.0

# ---- 接近段参数 ----
CYCLE_T = 0.55        # 接近周期（秒）
LUNGE_FRAC = 0.65     # 周期内预屈段占比
PUSH_GAIN = 0.30      # 蹬伸幅度（0~1，越大蹬得越狠；保持小值避免被判蓄力连跳）
N_CYCLE = 2           # 走几个接近周期后起跳
X_TRIG = 10.6         # 躯干到达该位置即可提前起跳（需满足状态门控）
X_LAST = 11.75        # 超过该位置强制起跳，避免越过踏板判犯规
T_RUN_MAX = 9.0       # 接近段最长时长
GATE_VX = 0.8         # 提前触发门控：水平速度下限
GATE_VY = 0.6         # 提前触发门控：竖直速度上限

# ---- 腾空/落地姿态 ----
FL_HIP, FL_KNEE, FL_SHO, FL_ANK = 1.0, 1.0, 0.2, -0.5

# 技能包的两个姿态（髋, 膝, 肩, 踝）
LUNGE = (1.0, -0.874, 0.051, 1.0)
PUSH = (-0.391, 0.639, 0.688, -0.342)

_phase = -1
_t0 = 0.0
_cyc = 0
_jbegin = -1.0
_jext = -1.0
_ext_t = -1.0
_try = 0


def _pose(p):
    return [p[0], p[1], p[0], p[1], p[2], p[2], p[3], p[3]]


def _mix(a, b, u):
    return (a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u,
            a[2] + (b[2] - a[2]) * u, a[3] + (b[3] - a[3]) * u)


def _fly():
    return [FL_HIP, FL_KNEE, FL_HIP, FL_KNEE, FL_SHO, FL_SHO, FL_ANK, FL_ANK]


def act(obs):
    global _phase, _t0, _cyc, _jbegin, _jext, _ext_t, _try
    t = obs['time']
    c = obs['contact']
    gnd = c['foot_L'] or c['foot_R']
    if _phase < 0:
        _phase = 0
        _t0 = t
    el = t - _t0

    if _phase == 0:                                   # 接近段
        cyc = (el / CYCLE_T) % 1.0
        if cyc < LUNGE_FRAC:
            pose = LUNGE
        else:
            u = (cyc - LUNGE_FRAC) / (1.0 - LUNGE_FRAC)
            pose = _mix(LUNGE, PUSH, PUSH_GAIN * u)
        done = _cyc >= N_CYCLE or obs['x'] >= X_TRIG or obs['x'] > X_LAST or el > T_RUN_MAX
        gate = gnd and obs['vx'] > GATE_VX and obs['vy'] < GATE_VY
        if done and (_cyc >= N_CYCLE or (obs['x'] >= X_TRIG and gate) or obs['x'] > X_LAST):
            _phase = 1
            _t0 = t
            _jbegin = -1.0
            _jext = -1.0
            _ext_t = -1.0
            _try += 1
        elif cyc < LUNGE_FRAC and int(el / CYCLE_T) > _cyc:
            _cyc = int(el / CYCLE_T)
        return _pose(pose)

    if _phase == 1:                                   # 正式大跳（技能包起跳）
        if _jbegin < 0:
            _jbegin = t
        elj = t - _jbegin
        if _jext < 0:
            ready = elj >= 0.331 and obs['torso_angle'] >= 0.710 and obs['vy'] <= 1.829
            if ready or elj >= 0.9 or obs['x'] > X_LAST:
                _jext = t
            else:
                return _pose(LUNGE)
        if _ext_t < 0:
            _ext_t = t
        if not gnd and obs['vy'] > 0.0:                # 离地
            _phase = 2
            _t0 = t
            return _pose(PUSH)
        if t - _ext_t > 0.45 and _try < 3:            # 没蹬起来：回到接近段重试
            _phase = 0
            _t0 = t
            _cyc = N_CYCLE
            return _pose(LUNGE)
        return _pose(PUSH)

    if _phase == 2:                                   # 腾空：前伸双腿
        if gnd:
            _phase = 3
            _t0 = t
        return _fly()

    return _fly()
