"""落地准备片段；下降时接管，保持至结算。不含助跑或蹬伸。"""
START_X=7.0
def landing(obs):
    return [.5,.5,.5,.5,.688,.688,-.342,-.342]
def act(obs):return landing(obs)
