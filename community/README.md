# 社区投稿

用你自己的 AI（或你自己）写一个 bot，来和正式赛的选手同场比。

## 怎么投

1. 读对应期的 `workspace/`，写出 `bot.py`（第 1、2 期）或 `bot.mjs`（第 3 期）。
2. 本地跑一遍挑战脚本，确认能正常出成绩：
   ```bash
   python ep1-obstacle/challenge.py my_bot.py
   ```
3. 把代码放到 `community/submissions/ep1/<你的昵称>/bot.py`，并附一个 `meta.json`：

   ```json
   {
     "name": "你的昵称",
     "episode": 1,
     "model": "你用的模型或写「手写」",
     "prompt": "你给 AI 的任务书（可选，一两句）",
     "minutes": 45,
     "score_m": 12.34,
     "finish_s": null
   }
   ```

4. 开 PR。维护者会用对应期的 `verify.py` / `verify.mjs` 重跑一遍，成绩以重跑结果为准；对不上会被打回。

## 规则

- 不许改 `engine/`、不许改别人的 bot、不许读榜单以外的隐藏数据。
- 一次提交只能有一个 bot 文件；不许在 bot 里联网、读文件、调用外部进程。
- 成绩按各期口径排名（见 [`../docs/scoring.md`](../docs/scoring.md)），不跨期比较。
- 观众榜单独排，不占用正式赛名额，也不影响正式赛成绩。

## 榜单

`community/LEADERBOARD.md` 按每期分别排，由维护者在合并后更新。没上榜通常是因为 PR 里的成绩和重跑结果不一致。

