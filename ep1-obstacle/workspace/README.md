# 任务包（比赛时发给 AI 的原样文件）

这个目录就是把当时丢给选手的东西原封不动搬过来：`PROMPT.md` 是任务书，`bots/bot_template.py` 是带注释的模板，`engine/` 是当时那两份裁判文件。

## 怎么用

```bash
cd workspace
python engine/evaluator.py bots/bot.py     # 自测一场，看体检报告
```

改 `bots/bot.py` 里的 `act(obs)` 就行，别动 `engine/`。

## 和上层脚本的区别

| | 这里（比赛原包） | 上层（本仓库加的） |
|---|---|---|
| 自测 | `engine/evaluator.py`，带比赛时钟与日志 | `../challenge.py`，只跑一场 |
| 榜单 | 无 | `../verify.py` 重跑 22 份对榜 |
| 目的 | 还原「选手当时看到什么」 | 方便验证与挑战 |

## 把这套东西丢给你的 AI

任务书本身就是给 Agent 写的，直接把 `PROMPT.md` 的内容发给它，并把 `workspace/` 整个目录给它当工作区即可。限时自己定（正式赛是 45 分钟，决赛是两段 20 分钟）。

> 原任务书里的自测命令有「两次自测至少间隔 15 秒」的限速，那是比赛为了防止刷模拟；你自己玩可以不等。

