"""EP2 资格赛定稿：立定 countermovement 跳 + 飞行中后段收腿前伸 + 落地保持。

深蹲姿与蹬伸姿取自 takeoff 技能包；蓄力触发由技能包的 torso/vy 条件改为固定 0.60s
（风扰动下前扑速率有差异，固定时刻消除双模跳变）；起跳后 0.58s 收腿前伸落沙。
全程只做一次大跳，无连续蓄力大跳。START_X=9.0（合法最大值）。
"""
import math

START_X = 9.0


def pose(h, k, s, a):
    return [h, k, h, k, s, s, a, a]


CROUCH = (1.00, -0.874, 0.051, 1.00)
EXTEND = (-0.391, 0.639, 0.688, -0.342)
REACH = (0.75, 0.90, 0.60, -0.50)
LAND = (0.45, 0.60, 0.55, 0.00)
T_REACH = 0.58          # 起跳后多久收腿

_st = "crouch"
_t0 = 0.0


def act(obs):
    global _st, _t0
    t = obs["time"]
    grounded = obs["contact"]["foot_L"] or obs["contact"]["foot_R"]
    if _st == "land":
        return pose(*LAND)
    if _st == "air":
        if grounded:
            _st = "land"
            return pose(*LAND)
        if t - _t0 >= T_REACH:
            return pose(*REACH)
        return pose(*EXTEND)
    if _st == "crouch":
        e = t - _t0
        if e >= 0.60:
            _st = "ext"
            _t0 = t
        return pose(*CROUCH)
    if _st == "ext":
        e = t - _t0
        if (not grounded and e >= 0.10) or e >= 0.25:
            _st = "air"
            _t0 = t
            return pose(*EXTEND)
        return pose(*EXTEND)
    return pose(*CROUCH)
