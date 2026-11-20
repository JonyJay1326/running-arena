import math

# 起点脚尖坐标；助跑距离 = 12 - START_X（合法 2~9）
START_X = 9.0

# ---- 助跑步态参数（扫描锁定：稳定直立前行 ~0.8m/s）----
W_FREQ = 1.4
W_HIP_MIN = 0.0
W_HIP_MAX = 1.0
W_KBEND = 0.65
W_ANK0 = 0.20
W_ARM_BASE = -0.70
W_ARM_AMP = 0.15
W_SETTLE = 0.4

# ---- 起跳触发与姿态 ----
JUMP_DIST = 1.10          # dist_to_line <= 此值进入 PLANT
PLANT_TIME = 0.10         # 下蹲蓄力时长（脚仍触地）
TAKEOFF_MAX = 0.30        # 蹬伸最长持续，直到全身离地
# 蓄力：轻度屈膝、臂后摆（不倒：髋不前伸、膝不过度屈）
PLANT_POSE  = [0.25, 0.45, 0.25, 0.45, -0.95, -0.95, 0.30, 0.30]
# 蹬伸：双臂上摆、双腿蹬直、踝跖屈推地、髋略后伸(增前推)
TAKEOFF_POSE= [-0.20, 1.0, -0.20, 1.0, 1.0, 1.0, -1.0, -1.0]
# 空中：收腿、臂中性，控制后翻
FLIGHT_POSE = [0.30, 0.10, 0.55, 0.35, 0.0, 0.0, 0.25, 0.25]
# 下降：双腿前伸(髋满屈+膝伸+踝背屈脚跟先探)，争取脚前伸深落
DESC_POSE   = [1.0, 0.6, 1.0, 0.6, 0.0, 0.0, 0.5, 0.5]
# 落地锁止：低姿、腿前伸、躯干前倾压住，绝不后坐/后蹬
LOCK_POSE   = [0.55, 0.10, 0.55, 0.10, -0.30, -0.30, 0.30, 0.30]

_state = {"phase": "APPROACH", "t0": None, "plant_t": None, "to_t": None}

def _reset():
    _state["phase"] = "APPROACH"
    _state["t0"] = None
    _state["plant_t"] = None
    _state["to_t"] = None

def _feet(obs):
    c = obs["contact"]
    return bool(c.get("foot_L") or c.get("foot_R"))

def _grounded(obs):
    c = obs["contact"]
    return bool(c.get("foot_L") or c.get("foot_R") or c.get("hand_L") or c.get("hand_R") or c.get("torso"))

def _walk(obs, t):
    if t < W_SETTLE:
        return [0.0, 1.0, 0.0, 1.0, -0.5, -0.5, 0.15, 0.15]
    tg = t - W_SETTLE
    p = 2 * math.pi * W_FREQ * tg
    swL = 0.5 + 0.5 * math.sin(p)
    swR = 0.5 + 0.5 * math.sin(p + math.pi)
    hipL = W_HIP_MIN + (W_HIP_MAX - W_HIP_MIN) * swL
    hipR = W_HIP_MIN + (W_HIP_MAX - W_HIP_MIN) * swR
    kneeL = 1.0 - W_KBEND * swL
    kneeR = 1.0 - W_KBEND * swR
    shL = W_ARM_BASE - W_ARM_AMP * swL + W_ARM_AMP * swR
    shR = W_ARM_BASE - W_ARM_AMP * swR + W_ARM_AMP * swL
    return [hipL, kneeL, hipR, kneeR, shL, shR, W_ANK0, W_ANK0]

def act(obs):
    if _state["t0"] is None or obs["time"] < _state["t0"]:
        _reset(); _state["t0"] = obs["time"]
    t = obs["time"] - _state["t0"]
    d = obs["dist_to_line"]
    ph = _state["phase"]

    if ph == "APPROACH":
        if d <= JUMP_DIST:
            _state["phase"] = "PLANT"; _state["plant_t"] = t; ph = "PLANT"
        else:
            return _walk(obs, t)

    if ph == "PLANT":
        if t - _state["plant_t"] >= PLANT_TIME:
            _state["phase"] = "TAKEOFF"; _state["to_t"] = t; ph = "TAKEOFF"
        else:
            return list(PLANT_POSE)

    if ph == "TAKEOFF":
        if not _grounded(obs):
            _state["phase"] = "FLIGHT"; ph = "FLIGHT"
        elif t - _state["to_t"] >= TAKEOFF_MAX:
            # 超过最长蹬伸仍未离地：强制进入空中姿态处理（防止卡死再蹬）
            _state["phase"] = "FLIGHT"; ph = "FLIGHT"
        else:
            return list(TAKEOFF_POSE)

    if ph == "FLIGHT":
        if _grounded(obs):
            _state["phase"] = "LANDING_LOCKED"; ph = "LANDING_LOCKED"
        else:
            if obs["vy"] < -0.4 and obs["y"] < 1.25:
                return list(DESC_POSE)
            return list(FLIGHT_POSE)

    # LANDING_LOCKED：不可返回起跳
    return list(LOCK_POSE)
