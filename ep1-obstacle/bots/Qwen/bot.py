import math

# 最终配置 v73：正弦步态 + cos^2屈膝 + 落点反馈 + 前倾governor + 近障碍抬脚(0.38)
# 思路：
#  1) 髋前倾偏置使 COM 落在脚掌压力中心(踝前)前方，形成稳定前倾支撑；
#  2) cos 相位屈膝让脚在髋最前摆时落地，超前落点接住前倾；
#  3) 落点反馈随躯干倾角加大迈步超前量(捕获点)；
#  4) governor 在过度前倾时削减前驱偏置，打断"髋前摆反力矩→更前倾"的恶性循环，
#     使机器人能多次"走→前冲→恢复"延续奔跑；
#  5) 接近减速带/跨栏时摆动腿额外屈膝抬脚，减少绊倒。
FREQ = 1.5          # 步频 (Hz)
HIP_AMP = 0.25      # 髋摆幅
KNEE_AMP = 0.4      # 屈膝幅度
HIP_BASE = 0.45     # 髋前倾偏置
ANG_TARGET = 0.25   # 落点反馈的目标躯干倾角 (rad)
K_PLACE = 0.5       # 落点反馈增益
ARM_AMP = 0.3       # 手臂反摆幅度
GOV_TH = 0.75       # governor 触发倾角 (rad)
GOV_GAIN = 0.8      # governor 削减增益
BUMP_LIFT = 0.38    # 近障碍额外屈膝


def act(obs: dict) -> list[float]:
    t = obs['time']
    ang = obs['torso_angle']
    ph = 2.0 * math.pi * FREQ * t
    place = K_PLACE * (ang - ANG_TARGET)
    base = HIP_BASE - GOV_GAIN * max(ang - GOV_TH, 0.0)
    bd = obs['next_bump_dist']
    lift = BUMP_LIFT * max(0.0, 0.5 - bd) if bd < 0.5 else 0.0

    def leg(p):
        s = math.sin(p)
        hip = base + HIP_AMP * s + place * max(0.0, s)
        knee = 1.0 - KNEE_AMP * max(0.0, math.cos(p)) ** 2 - lift * max(0.0, s)
        return hip, knee

    hl, kl = leg(ph)
    hr, kr = leg(ph + math.pi)
    sl = -0.39 - ARM_AMP * math.sin(ph)
    sr = -0.39 + ARM_AMP * math.sin(ph)
    return [max(-1.0, min(1.0, hl)), max(-1.0, min(1.0, kl)),
            max(-1.0, min(1.0, hr)), max(-1.0, min(1.0, kr)),
            max(-1.0, min(1.0, sl)), max(-1.0, min(1.0, sr))]