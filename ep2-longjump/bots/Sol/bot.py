import math

START_X = 9.0

# 小幅助跑；髋反馈保持躯干姿态，膝屈曲幅度限制在走跑范围内。
WALK = [0.28, -0.24652694427765234, 0.5474834503923997, 0.3537940459157208, 0.4594244826672302, 0.8164333094843682, 0.03641619004874251, 0.4253892380359314, 1.1562011604142748, 0.0774220998104782, 0.41160824496817056, 0.5812338354575931]
(PERIOD, HIP_BIAS, HIP_AMPLITUDE, KNEE_SWING, ANKLE_BIAS,
 ANKLE_AMPLITUDE, ANKLE_PHASE, LEAN_TARGET, LEAN_KP, LEAN_KD,
 WALK_SHOULDER, WALK_RAMP) = WALK

# 一次准备、一次蹬伸。下降后锁定落地阶段，不再次蓄力。
JUMP = [0.1833327154725205, 0.9102080326424462, -0.923879116431474, 1, 0.5002723695861423, 0.4222929911081867, 0.20611828271170823, 0.8455182085119327, -0.6840324056339068, 0.895451458175268, -0.4324353721090784, 1, -0.7719296835891076, -0.6512512218080666, 0.9941760584008517, 0.9917339522902442, 0.7408059361927126, 0.41495451894617497, 0.9512191072371172, 0.08500910890416767, 0.2084530165685537, 0, 0.4501053707694087, 0, -0.0035293645183268984, -0.020523504389813766, 0.12458027716160354]
(PREP_DURATION, PREP_HIP, PREP_KNEE, PREP_ANKLE, PREP_SHOULDER,
 PUSH_DURATION, PUSH_HIP, PUSH_KNEE, PUSH_ANKLE, PUSH_SHOULDER,
 AIR_HIP, AIR_KNEE, AIR_SHOULDER, AIR_ANKLE, LAND_HEIGHT,
 JUMP_TIME, LAND_HIP, LAND_KNEE, LAND_SHOULDER, LAND_ANKLE,
 AIR_HIP_KP, AIR_HIP_KD, AIR_LEAN_TARGET, AIR_SHOULDER_KP,
 WIND_HIP_GAIN, WIND_PHASE_GAIN, CONTACT_WAIT) = JUMP

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
            WALK_SHOULDER, WALK_SHOULDER, ankle_left, ankle_right]


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
