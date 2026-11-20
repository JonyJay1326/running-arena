"""用你自己的 bot 挑战第 1 期，直接告诉你排第几。

    python challenge.py my_bot.py

规则和正式赛完全一样：同一份 engine/、30 秒、固定随机种子。你的 bot 只需要实现
`act(obs)`（返回 6 个数），可参考 workspace/bot_template.py。
"""
import contextlib
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "engine"))

try:
    import evaluator  # noqa: E402
except ModuleNotFoundError as exc:
    if exc.name == "pymunk":
        sys.exit("缺少 pymunk。先运行：pip install -r ../requirements.txt（或 pip install pymunk）")
    raise


def run(path):
    act = evaluator.load_bot(path)
    with contextlib.redirect_stdout(io.StringIO()):
        res = evaluator.simulate(act)
    return res["finish_time"], round(res["score"], 3)


def sort_key(finish, score):
    return (0, finish, -score) if finish is not None else (1, 0, -score)


def main():
    if len(sys.argv) < 2:
        sys.exit("用法：python challenge.py 你的bot.py")
    path = sys.argv[1]
    if not os.path.exists(path):
        sys.exit(f"找不到文件：{path}")

    with open(os.path.join(HERE, "results", "leaderboard.json"), encoding="utf-8") as f:
        board = json.load(f)

    finish, score = run(path)
    mine = sort_key(finish, score)
    rows = [(sort_key(r["finish_s"], r["score_m"]), r["name"]) for r in board["prelim"]]
    beaten = [name for k, name in rows if k > mine]
    lost_to = [name for k, name in rows if k < mine]
    rank = len(lost_to) + 1

    print()
    print(f"你的成绩：{'冲线 ' + format(finish, '.2f') + 's' if finish else '未冲线'}，{score:.3f} m")
    print(f"初赛 16 人里的名次：第 {rank} 名（共 17 人）")
    print()
    print(f"  · 赢过 {len(beaten)} 位：" + ("、".join(beaten) if beaten else "无"))
    print(f"  · 输给 {len(lost_to)} 位：" + ("、".join(lost_to) if lost_to else "无"))
    print()
    if rank == 1:
        print("冠军。可以来 community/ 投稿了。")
    elif rank <= 4:
        print("进决赛水平（正式赛前 4 名）。")
    else:
        print("对照 bots/ 里的代码看看别人怎么处理落地和跨栏。")

    extra_path = os.path.join(HERE, "results", "extra.json")
    if os.path.exists(extra_path):
        with open(extra_path, encoding="utf-8") as f:
            extra = json.load(f)["results"]
        print("\n榜外参考（补测，不计名次）：")
        for r in extra:
            fs = "未冲线" if r["finish_s"] is None else f'{r["finish_s"]:.2f}s'
            print(f"  {r['name']:<12} {fs:>8}  {r['score_m']:.3f} m")


if __name__ == "__main__":
    main()
