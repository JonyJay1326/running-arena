import math

START_X = 9.0

# 小幅助跑；髋反馈保持躯干姿态，膝屈曲幅度限制在走跑范围内。
PERIOD = 0.28
HIP_BIAS = -0.24652694427765234
HIP_AMPLITUDE = 0.5474834503923997
KNEE_SWING = 0.35062502146214114
ANKLE_BIAS = 0.4594244826672302
ANKLE_AMPLITUDE = 0.8164333094843682
ANKLE_PHASE = 0.03641619004874251
LEAN_TARGET = 0.4253892380359314
LEAN_KP = 1.1562011604142748
LEAN_KD = 0.07728946859079115
WALK_SHOULDER = 0.41160824496817056
WALK_RAMP = 0.5812338354575931

# 一次准备、一次蹬伸。下降后锁定落地阶段，不再次蓄力。
PREP_DURATION = 0.18841802620916487
PREP_HIP = 0.9102080326424462
PREP_KNEE = -0.902874348298643
PREP_ANKLE = 1
PREP_SHOULDER = 0.5002723695861423
PUSH_DURATION = 0.4422154310664231
PUSH_HIP = 0.20611828271170823
PUSH_KNEE = 0.8463889442752416
PUSH_ANKLE = -0.6340623800250057
PUSH_SHOULDER = 0.4447758790325304
AIR_HIP = -0.4324353721090784
AIR_KNEE = 0.969865983382385
AIR_SHOULDER = -0.875716090901487
AIR_ANKLE = -0.14997163356227672
LAND_HEIGHT = 0.9941760584008517
JUMP_TIME = 0.9952408312927461
LAND_HIP = 0.8394975479447492
LAND_KNEE = 0.36377980491337725
LAND_SHOULDER = 1
LAND_ANKLE = -0.36324631869008245
AIR_HIP_KP = 0
AIR_HIP_KD = 0.06821199899087338
AIR_LEAN_TARGET = 0.4405841349704297
AIR_SHOULDER_KP = 0.1867395816256488
WIND_HIP_GAIN = -0.0035293645183268984
WIND_PHASE_GAIN = -0.020523504389813766
CONTACT_WAIT = 0.15558965473166234

PREP_LEAN_TARGET = 0.6459158044933391
PREP_ANGLE_KP = -0.4614435876554539
PREP_ANGLE_KD = -0.008507174867430384
PREP_ANKLE_KP = 0.16543031960727686

prepare_time = None
push_time = None
initial_joints = None
landing = False


def clamp(value):
    return max(-1, min(1, value))


def gait(obs):
    t = obs['time']
    phase = 2 * math.pi * t / PERIOD + WIND_PHASE_GAIN * obs['wind']
    ramp = min(1, t / WALK_RAMP)
    cosine = math.cos(phase)
    sine = math.sin(phase)
    feedback = ((LEAN_TARGET - obs['torso_angle']) * LEAN_KP
                - obs['torso_omega'] * LEAN_KD + WIND_HIP_GAIN * obs['wind'])
    hip_left = HIP_BIAS + ramp * HIP_AMPLITUDE * cosine + feedback
    hip_right = HIP_BIAS - ramp * HIP_AMPLITUDE * cosine + feedback
    knee_left = 1 - KNEE_SWING * ramp * max(0, -sine)
    knee_right = 1 - KNEE_SWING * ramp * max(0, sine)
    ankle_left = ANKLE_BIAS + ramp * ANKLE_AMPLITUDE * math.cos(phase + ANKLE_PHASE * math.pi)
    ankle_right = ANKLE_BIAS - ramp * ANKLE_AMPLITUDE * math.cos(phase + ANKLE_PHASE * math.pi)
    return [clamp(hip_left), knee_left, clamp(hip_right), knee_right,
            WALK_SHOULDER, WALK_SHOULDER, clamp(ankle_left), clamp(ankle_right)]


def act(obs):
    global prepare_time, push_time, initial_joints, landing
    if prepare_time is None:
        if obs['time'] >= JUMP_TIME:
            prepare_time = obs['time']
            initial_joints = obs['joints'][:]
        else:
            return gait(obs)

    elapsed = obs['time'] - prepare_time
    preparation = [PREP_HIP, PREP_KNEE, PREP_HIP, PREP_KNEE,
                   PREP_SHOULDER, PREP_SHOULDER, PREP_ANKLE, PREP_ANKLE]
    prep_error = PREP_LEAN_TARGET - obs['torso_angle']
    prep_correction = PREP_ANGLE_KP * prep_error - PREP_ANGLE_KD * obs['torso_omega']
    preparation[0] = preparation[2] = clamp(PREP_HIP + prep_correction)
    preparation[6] = preparation[7] = clamp(PREP_ANKLE + PREP_ANKLE_KP * prep_error)
    if elapsed < PREP_DURATION:
        weight = elapsed / PREP_DURATION
        return [initial_joints[i] * (1 - weight) + preparation[i] * weight
                for i in range(8)]

    if push_time is None:
        if (obs['contact']['foot_L'] or obs['contact']['foot_R']
                or elapsed >= PREP_DURATION + CONTACT_WAIT):
            push_time = obs['time']
        else:
            return preparation

    if obs['time'] - push_time < PUSH_DURATION:
        hip, knee, ankle, shoulder = PUSH_HIP, PUSH_KNEE, PUSH_ANKLE, PUSH_SHOULDER
    else:
        if obs['vy'] < -1 and obs['y'] < LAND_HEIGHT:
            landing = True
        if landing:
            hip, knee, shoulder, ankle = LAND_HIP, LAND_KNEE, LAND_SHOULDER, LAND_ANKLE
        else:
            hip, knee, shoulder, ankle = AIR_HIP, AIR_KNEE, AIR_SHOULDER, AIR_ANKLE
        if not any(obs['contact'].values()):
            error = AIR_LEAN_TARGET - obs['torso_angle']
            hip += AIR_HIP_KP * error - AIR_HIP_KD * obs['torso_omega']
            shoulder += AIR_SHOULDER_KP * error
    return [clamp(hip), knee, clamp(hip), knee,
            clamp(shoulder), clamp(shoulder), ankle, ankle]
