"""验证榜单：用官方引擎重跑 bots/ 与 bots-final/，和 results/leaderboard.json 逐项比对。

    python verify.py            # 校验初赛 16 份 + 决赛 4 份
    python verify.py --prelim   # 只校验初赛

任何一份对不上就以非 0 退出，CI 里可以直接当门禁。
"""
import argparse
import contextlib
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "engine"))

try:
    import evaluator  # noqa: E402
except ModuleNotFoundError as exc:  # 缺依赖时给一句人话
    if exc.name == "pymunk":
        sys.exit("缺少 pymunk。先运行：pip install -r ../requirements.txt（或 pip install pymunk）")
    raise

TOL_FINISH = 1e-3
TOL_SCORE = 2e-3


def run(path):
    act = evaluator.load_bot(path)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):          # 选手代码可能 print 调试信息
        res = evaluator.simulate(act)
    return res["finish_time"], round(res["score"], 3), res["errors"]


def check(rows, tag):
    bad = 0
    print(f"\n== {tag} ==")
    print(f"{'#':>3}  {'选手':<16}{'用时':>9} {'成绩(m)':>10}  结果")
    for i, r in enumerate(rows, 1):
        path = os.path.join(HERE, r["bot"])
        rank = r.get("rank", "-")
        if not os.path.exists(path):
            print(f"{rank!s:>3}  {r['name']:<16}{'':>9} {'':>10}  缺少文件 {r['bot']}")
            bad += 1
            continue
        finish, score, errors = run(path)
        ok_f = (finish is None and r["finish_s"] is None) or \
               (finish is not None and r["finish_s"] is not None and abs(finish - r["finish_s"]) <= TOL_FINISH)
        ok_s = abs(score - r["score_m"]) <= TOL_SCORE
        ok = ok_f and ok_s
        bad += 0 if ok else 1
        fs = "-" if finish is None else f"{finish:.2f}s"
        print(f"{rank!s:>3}  {r['name']:<16}{fs:>9} {score:>10.3f}  {'OK' if ok else 'MISMATCH'}")
        if not ok:
            print(f"     榜单记录：用时 {r['finish_s']} / 成绩 {r['score_m']}")
        if errors:
            print(f"     bot 运行时报错 {len(errors)} 次（已按规则沿用上一个动作）")
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prelim", action="store_true")
    a = ap.parse_args()

    with open(os.path.join(HERE, "results", "leaderboard.json"), encoding="utf-8") as f:
        board = json.load(f)

    bad = check(board["prelim"], "初赛 16 人")
    if not a.prelim:
        bad += check(board["final"], "决赛 4 人")
        extra_path = os.path.join(HERE, "results", "extra.json")
        if os.path.exists(extra_path):
            with open(extra_path, encoding="utf-8") as f:
                extra = json.load(f)
            bad += check(extra["results"], "榜外补测（不计名次）")

    print("\n" + ("全部一致。" if bad == 0 else f"{bad} 项不一致，检查引擎版本或 bot 文件是否被改动。"))
    sys.exit(0 if bad == 0 else 1)


if __name__ == "__main__":
    main()
