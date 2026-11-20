"""腾空收腿片段；仅在自己确认已经离地后调用。不含起跳或落地。"""
START_X=7.0
def air(obs):
    return [-.930,-.975,-.930,-.975,-.225,-.225,1.,1.]
def act(obs):return air(obs)
