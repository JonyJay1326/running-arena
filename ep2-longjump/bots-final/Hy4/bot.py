"""EP2 跳远：决赛最终提交版。

方案（继承资格赛最终版）：approach 技能包助跑 -> 相位锁定接管 -> 一次深蹲蓄力+蹬伸大跳 ->
空中前伸腿 -> 双脚先落沙坑。单次正式大跳，无连续蓄力大跳；只导入 math。

决赛改动：保留资格赛全部参数，仅把 FLIGHT（空中前伸腿）阶段的肩指令由 0.5753 下调 0.20
至 0.3753（左右对称）。180 个独立风种子同参数配对复核：均值 4.662 -> 4.732m，
三跳取最好估计 4.925 -> 5.041m，0 犯规、0 无效、96.7% 后侧印记为脚。
决赛新选技能包 landing 已用同种子配对测试（见 log.md）：以 landing() 或融合姿态在下降段
接管会拉低中位距离，故弃用，保留本控制器自有 FLIGHT/LAND 姿态。
"""
import math

START_X = 9.0

STRIDE_PERIOD = 0.9427686943729606
HIP_SWING = 0.4612432499188154
KNEE_SWING = 0.46037503371745675
HIP_OFFSET = 0.16081091205893727
LEG_OFFSET = -0.11176635736923067
KNEE_PHASE = -0.6848588702859837
BALANCE_GAIN = -0.8549383067209367
BALANCE_DAMPING = -0.019411040799691966

TRIG_T = 2.87802817781073
CROUCH_DT = 0.2863572581763171

CROUCH = [1, -0.6296905681, 1, -0.6296905681, -0.0940774413, -0.0940774413, 0.9878674544, 0.9878674544]
LAUNCH = [-0.4525570803, 0.6343395808, -0.4525570803, 0.6343395808, 0.5840407272, 0.5840407272, -0.6668782716, -0.6668782716]
FLIGHT = [0.4383035532, -0.1191530492, 0.4383035532, -0.1191530492, 0.3753427954, 0.3753427954, 0.418166844, 0.418166844]
LAND = [0.0121250771, 0.3955047867, 0.0121250771, 0.3955047867, 0.6165352029, 0.6165352029, -0.2688450193, -0.2688450193]

_st = {}


def _gait(obs, ramp):
    phase = obs['time'] * math.tau / STRIDE_PERIOD
    legs, ankles = [], []
    for side in (0, 1):
        leg_phase = phase + side * math.pi
        swing = ramp * HIP_SWING * math.cos(leg_phase) + LEG_OFFSET
        knee = -ramp * KNEE_SWING * max(0, -math.sin(leg_phase + KNEE_PHASE))
        hip = swing + HIP_OFFSET + BALANCE_GAIN * obs['torso_angle'] + BALANCE_DAMPING * obs['torso_omega']
        ankle = -swing - knee
        legs.extend([(hip * 180 / math.pi - 7.5) / 67.5, 1 + knee * 180 / math.pi / 55])
        ankles.append((ankle * 180 / math.pi - 5) / 55)
    return legs + [-0.39, -0.39] + ankles


def act(obs):
    ph = _st.get('ph', 'approach')
    t = obs['time']
    c = obs['contact']
    down = c['foot_L'] or c['foot_R']
    anydown = c['foot_L'] or c['foot_R'] or c['torso'] or c['hand_L'] or c['hand_R']
    if ph == 'approach':
        a = _gait(obs, min(1, t / 0.5))
        if t >= TRIG_T:
            _st['ph'] = 'crouch'
            _st['t0'] = t
        return a
    if ph == 'crouch':
        if t - _st['t0'] >= CROUCH_DT:
            _st['ph'] = 'launch'
            _st['t1'] = t
        else:
            return CROUCH
    if ph == 'launch':
        if not down or t - _st['t1'] > 0.5:
            _st['ph'] = 'flight'
            _st['t2'] = t
        else:
            return LAUNCH
    if ph == 'flight':
        if anydown or t - _st['t2'] > 1.5:
            _st['ph'] = 'land'
            _st['t3'] = t
        else:
            return FLIGHT
    return LAND
