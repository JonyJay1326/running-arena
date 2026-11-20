import math

fallen = False
fall_time = 0.0
previous_x = 0.0
lost_distance = 0.0
finished = False


def act(obs: dict) -> list[float]:
    global fallen, fall_time, previous_x, lost_distance, finished
    angle = (obs['torso_angle'] + math.pi) % (2 * math.pi) - math.pi
    down = obs['contact']['torso'] or obs['y'] < 0.45 or (abs(angle) >= math.radians(50) and obs['y'] < 0.7)
    if down:
        lost_distance += obs['x'] - previous_x
    previous_x = obs['x']
    if obs['x'] - max(lost_distance, 0.0) >= 18.0:
        finished = True
    if finished:
        return [0.0, 1.0, 0.0, 1.0, -0.39, -0.39]
    if obs['contact']['torso'] and not fallen:
        fallen = True
        fall_time = obs['time']
    if fallen and obs['y'] > 0.9 and abs(obs['torso_angle']) < 0.5 and obs['vx'] > 0 and not obs['contact']['torso']:
        fallen = False
    if fallen:
        recovery_period = 1.02 if obs['x'] > 15.5 else (1.3 if obs['x'] > 10.3 else 1.1)
        tuck_time = recovery_period / 2
        if (obs['time'] - fall_time) % recovery_period < tuck_time:
            arm = 0.56 if obs['x'] > 16.5 else 0.2
            return [1.0, -1.0, 1.0, -1.0, arm, arm]
        return [-1.0, 1.0, -1.0, 1.0, 1.0, 1.0]
    """
    每帧 (1/60 秒) 调用一次，返回 6 个关节的目标角度指令。

    只允许 import math 和 random；可以使用模块级全局变量保存状态（每次评测会重新加载文件）。

    参数 obs 字典包含:
      - 'time': float, 当前比赛已用时间 (秒)
      - 'x': float, 躯干水平坐标 (米, 起点 0.0, 终点 18.0)
      - 'y': float, 躯干中心离脚下地面的高度 (米, 直立约 1.28m)
      - 'vx', 'vy': float, 躯干速度 (米/秒)
      - 'torso_angle': float, 躯干倾角 (弧度, 0 为竖直, > 0 前倾, < 0 后仰)
      - 'torso_omega': float, 躯干角速度 (弧度/秒, > 0 表示正在往前倒)
      - 'joints': list[float], 6 个关节当前归一化角度 [hip_L, knee_L, hip_R, knee_R, shoulder_L, shoulder_R] (-1.0 ~ 1.0)
      - 'contact': dict, 触地传感器 {'foot_L', 'foot_R', 'hand_L', 'hand_R', 'torso': bool}
                   （foot_* 指小腿或脚掌触地，hand_* 指前臂，torso 含头）
      - 'next_bump_dist': float, 躯干到前方最近障碍的水平距离 (米, 没有障碍时为 99)
      - 'next_bump_height': float, 该障碍高度 (米; 减速带 0.08, 跨栏 0.20)

    返回值: [hip_L, knee_L, hip_R, knee_R, shoulder_L, shoulder_R]，每项范围 [-1.0, 1.0]（超出会被截断）
      - hip (髋, 大腿相对躯干): -1.0 = 向后蹬 60°, +1.0 = 向前高抬 75°；
                                 大腿与躯干对齐(竖直)约为 -0.11
      - knee (膝, 小腿相对大腿): +1.0 = 几乎伸直 (弯 5°), -1.0 = 向后折叠 110°
      - shoulder (肩, 手臂相对躯干): -1.0 = 向后摆 70°, +1.0 = 向前上举 160°；手臂自然下垂约 -0.39。
                                     肘部是固定的微弯，不可控；肩力矩比髋小（3 万 vs 35 万）。
      踝关节不可控制：脚掌随膝关节自动联动（膝弯得越多，脚尖越往上翘）。
      只返回前 4 个数也可以（手臂保持自然下垂）。
      底层为角度舵机：你给的是“目标角度”，电机会以有限力矩和速度往那里转。

    赛道 (18m): 0~5 平地 | 5~9 减速带 (高 0.08m, 每 1m 一个) | 9~11 15° 上坡 |
                11~13 15° 下坡 | 13~18 跨栏 (高 0.20m, 每 1.5m 一个, 可踢倒)
    计分：躯干/头触地、躯干中心高度 < 0.45m、或躯干前倾/后仰达到 50° 且躯干中心高度 < 0.7m，都算倒地；倒地期间水平位移不计入成绩（成绩不会超过实际所在位置）。倒地后 act 仍每帧调用，可以尝试起身。
    比赛有两条赛道：障碍赛（上面这条）和无障碍纯平地冲刺；obs['next_bump_dist'] 在无障碍赛道上恒为 99。
    """
    t = obs['time']
    period = 0.7
    phase = t % period
    swing_left = phase < period / 2
    swing_phase = phase % (period / 2)
    correction = -0.12 * (obs['torso_omega'] + 3.0 * (obs['torso_angle'] - 0.1))
    drive = 0.03
    stance_l = obs['joints'][0] + correction + drive
    stance_r = obs['joints'][2] + correction + drive
    swing_knee = 0.1 if swing_phase < period / 4 else 1.0
    swing_hip = 0.65
    arm = -0.39 + 0.5 * obs['torso_angle']
    stance_knee = 1.0
    if swing_left:
        return [swing_hip, swing_knee, stance_r, stance_knee, arm, arm]
    return [stance_l, stance_knee, swing_hip, swing_knee, arm, arm]
