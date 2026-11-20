import math

START_X = 9.0
WALK = [0.24146487030544386, 0.6544161252423055, 0.30719405453875676, 0.7007016767788979, 0.28607322584085204, 0.4992659719877419, 0.35671490435480196, -0.32689536940148434, -1.9458819011377901, -0.32457188118044517, 0.8277049943218111, -0.21102311527675205]
SMOOTH = [0.14279113587439463, 0.10036824585427276, 0.06479813054959481]
TRANSFER = [0.33302798278286416, 0.03626179347014169, 0.06899808857618506, -0.42705934095337517, -0.09875104282492275, 0.15177582344877716, 0.02798766002521924, 0.022513046660821046]
rate = 0.0


def clamp(value):
    return max(-1.0,min(1.0,value))


def walk(obs):
    global rate
    period,hip,stride,knee,lift,offset,ankle,roll,kp,kd,arms,phase = WALK
    smooth,ramp_time,lean = SMOOTH
    rate += smooth*(obs["torso_omega"]-rate)
    ramp = min(1.0,(obs["time"]+.012)/ramp_time)
    a = 2*math.pi*(obs["time"]/period+phase)
    advance = math.sin(a)
    swing = math.sin(a+2*math.pi*offset)
    pelvis = -.11111 + ramp*(hip+.11111 + kp*(obs["torso_angle"]-lean)+kd*rate)
    knee_base = 1.0 + ramp*(knee-1)
    # 在最后几次换脚时调整躯干和足落点，左右腿仍按步态交替。
    transfer = max(0.0, min(1.0, 1.0-(FINAL_STEP[0]-obs["time"])/TRANSFER[0]))
    pelvis += transfer*(TRANSFER[1]+TRANSFER[5]*obs["torso_angle"]+TRANSFER[6]*rate)
    knee_base += transfer*TRANSFER[2]
    arm_center = -.39+transfer*TRANSFER[3]
    ankle += transfer*TRANSFER[4]
    stride += transfer*TRANSFER[7]
    return [clamp(pelvis+ramp*stride*advance),clamp(knee_base-1.65*ramp*lift*max(0,swing)),
            clamp(pelvis-ramp*stride*advance),clamp(knee_base-1.65*ramp*lift*max(0,-swing)),
            clamp(arm_center-ramp*arms*advance),clamp(arm_center+ramp*arms*advance),
            clamp(-.09091+ramp*(ankle+.09091+roll*swing)),clamp(-.09091+ramp*(ankle+.09091-roll*swing))]

FINAL_STEP = [1.198975786490498, 0.05910421115416177, -0.9]
FOLD = [0.9716016103134711, -0.7622702369553411, -0.8247103415691577, 0.8989082815048894, 0.5029317145474286, -0.0920355720128413]
REACH = [0.6534173842592712, 0.0892252297461823, -1.5050612496569433, 0.0778312916802266]
SUPPORT = [0.007724325550676376, -0.1958058058722296, -0.35, 0.05432957202923753, 0.02795959354026787]
HEIGHT_RATE = 0
PLANT = [1.0430623807549557, 0.0644218327155329]
PITCH = [0.07743773657933463, 0.9174273047243675, 0.0674874220253148, 3.0840926246670044]
BRACE = [0.517543417431088, -0.3951380159932305, -1.0470538358427306, 0.374235860947653, 0.18595196461234167]
LOW_FORM = [0.07638375486298622, 0.7, 0.22752604814349867, 0.6082653299476193, -1.5, 0.1626644117024884]
DRIVE = [0.058441612141242875, 0.9894032990399346, 0.45926592632297303, 0.5559237903789822, 1.0, 0.09544327068717667]
PUSH = [-0.6054833019961888, 0.9279497535182499, 0.37782952971551564, -0.6795253140204973]
AIR = [-0.9782824551436899, 0.04284927981575954, 0.8699097048428682, 0.5416715599096864, -1.3457928540009385]
AIM = [-0.353529550710407, 0.038099001563192875, 0.5631216258991879, 0.0224453423019393, -0.3136899013052565, 1.0183450200982456]
LAND = [0.985485544529165, -0.49635019764537297, 0.6484653208971443, 0.876292764274997]
CATCH = [-0.9934236016313689, 0.08856934106955122, -0.6804615441538031, -0.5991203321826084]
LOW_PUSH = [-0.3355844096366146, 0.7988973912040401, 0.6833207220620803, -0.7870442150421828]

SWEEP = [0.09199770150240794, 0.05288035711640386, 0.6254961504801713, -1.0, -1.0, 1.0, 0.2400114107437905, -0.06316646739915252]
stage = "APPROACH"
last_support = 0
support_count = 0
gather_start = 0.0
push_start = 0.0
high_spin = False
alternate_drive = False
SPIN_DRIVE = [0.04015242928556347, 1.0, 0.08192376673086388, 0.5143427437875494, 1.0, -0.03248051039887967]
SPIN_PUSH = [-0.9636441417063714, 0.9787091546802071, 0.4370774052215214, -0.9635236881701634]
ALT_DRIVE = [0.05235343474245174, 1.0, 1.0, 0.10156673844195852, 0.9750860309122653, -0.00787665113483113]
ALT_PUSH = [-1.0, 0.8856612562522801, 0.9156429588396096, -1.0]
plant_loaded = False
low_entry = False
descent = False
last_air_pose = None
lock_pose = None


def paired(hip, knee, shoulder, ankle):
    return [hip, knee, hip, knee, shoulder, shoulder, ankle, ankle]


def fold_pose(obs, grounded, brace):
    pitch, omega = obs["torso_angle"], obs["torso_omega"]
    if low_entry:
        hip, knee, shoulder, ankle, kp, kd = LOW_FORM
        return paired(clamp(hip + kp*pitch + kd*omega), knee, shoulder, ankle)
    hip, knee, shoulder, ankle, hip_kp, hip_kd = FOLD
    hip = clamp(hip + hip_kp*pitch + hip_kd*omega)
    if grounded:
        h, k, s, a, ak = SUPPORT
        return paired(clamp(hip+h), clamp(knee+k), clamp(shoulder+s), clamp(ankle+a+ak*pitch))
    weight = max(0.0,min(1.0,1.0-(obs["time"]-gather_start-SWEEP[0])/SWEEP[1]))
    hip += weight*(clamp(SWEEP[2]+SWEEP[6]*pitch+SWEEP[7]*omega)-hip)
    knee += weight*(SWEEP[3]-knee)
    shoulder += weight*(SWEEP[4]-shoulder)
    ankle += weight*(SWEEP[5]-ankle)
    knee = clamp(knee + REACH[0]*(pitch-.6) + REACH[1]*omega)
    shoulder = clamp(shoulder + REACH[2]*(pitch-.6) + REACH[3]*omega)
    return paired(clamp(hip + brace*BRACE[0]), clamp(knee + brace*BRACE[1]),
                  clamp(shoulder + brace*BRACE[2]), clamp(ankle + brace*BRACE[3]))


def flight_pose(obs):
    global descent, last_air_pose
    if descent:
        return last_air_pose
    pitch, omega = obs["torso_angle"], obs["torso_omega"]
    descent = obs["vy"] < AIR[4]
    if descent:
        # 下降中即固定接地姿态，跨过观测采样之间的短暂触地。
        last_air_pose = paired(clamp(LAND[0] + CATCH[0]*pitch + CATCH[1]*omega),
                               clamp(LAND[1] + CATCH[2]*pitch), LAND[2], clamp(LAND[3] + CATCH[3]*pitch))
    else:
        last_air_pose = paired(clamp(AIR[0] + AIM[0]*pitch + AIM[1]*omega), clamp(AIR[1] + AIM[4]*pitch),
                               clamp(AIR[2] + AIM[2]*pitch + AIM[3]*omega), clamp(AIR[3] + AIM[5]*pitch))
    return last_air_pose


def act(obs):
    global stage, last_support, support_count, gather_start, low_entry, plant_loaded, push_start, lock_pose
    global alternate_drive, high_spin
    if stage == "LANDING_LOCKED":
        return lock_pose
    time = obs["time"]
    contact = obs["contact"]
    grounded = any(contact.values())
    fallen = contact["torso"] or contact["hand_L"] or contact["hand_R"]
    if stage != "FLIGHT" and fallen:
        stage = "LANDING_LOCKED"
        lock_pose = [clamp(j) for j in obs["joints"]]
        return lock_pose

    if stage == "APPROACH":
        left, right = contact["foot_L"], contact["foot_R"]
        if left != right:
            side = 1 if left else -1
            if side != last_support:
                last_support = side
                support_count += 1
        elapsed = time - FINAL_STEP[0]
        ready = obs["vy"] >= FINAL_STEP[2] or elapsed >= FINAL_STEP[1]
        if grounded or support_count < 5 or elapsed < 0 or not ready:
            return walk(obs)
        stage = "GATHER"
        gather_start = time
        low_entry = obs["y"] < 1.16 and obs["vy"] < .50

    if stage == "GATHER":
        predicted_pitch = obs["torso_angle"] + PITCH[2]*obs["torso_omega"]
        brace = max(0.0, min(1.0, (predicted_pitch - PITCH[1])*PITCH[3]))
        if time - gather_start < PITCH[0]:
            brace = 0.0
        if grounded:
            plant_loaded = True
        if plant_loaded and not grounded:
            # 踏地后即使意外提前卸载，这次离地也消耗唯一的起跳机会。
            stage = "FLIGHT"
        elif grounded and obs["y"] <= PLANT[0] + HEIGHT_RATE*obs["vy"] + brace*BRACE[4]:
            stage = "TAKEOFF"
            push_start = time
            # 在同一次踏地开始时选定蹬伸姿态，整个支撑期间沿用。
            high_spin = obs["torso_omega"] > 3.9 and obs["vy"] < -.7
            alternate_drive = obs["torso_omega"] < 1.8 and obs["vy"] < -1.2
        else:
            return fold_pose(obs, grounded, brace)

    if stage == "TAKEOFF":
        if grounded:
            if low_entry:
                return paired(*LOW_PUSH)
            drive = ALT_DRIVE if alternate_drive else DRIVE
            push = ALT_PUSH if alternate_drive else PUSH
            if high_spin:
                drive, push = SPIN_DRIVE, SPIN_PUSH
            pitch = obs["torso_angle"]
            if time - push_start < drive[0]:
                return paired(clamp(drive[1] + PLANT[1]*pitch), *drive[2:5])
            return paired(clamp(push[0] + PLANT[1]*pitch + drive[5]*obs["torso_omega"]), *push[1:])
        stage = "FLIGHT"

    if not grounded:
        return flight_pose(obs)
    stage = "LANDING_LOCKED"
    lock_pose = list(last_air_pose)
    for knee in (1, 3):
        lock_pose[knee] = clamp(min(lock_pose[knee], obs["joints"][knee]))
    return lock_pose
