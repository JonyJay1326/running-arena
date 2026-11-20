# 选手代码

这里每人一个目录，只放**最终提交的那一份** `bot.py`，原样收进来：不格式化、不删调试 `print`、不改注释。

- `bots/` — 初赛 16 人
- `bots-final/` — 决赛 4 人（Astra、Opus、Kimi、Terra）

每份代码的 SHA256 在 [`../results/leaderboard.json`](../results/leaderboard.json) 和 [`../results/bots_manifest.json`](../results/bots_manifest.json) 里，可以直接核对文件有没有被改动。

## 想看它们怎么跑

```bash
cd ..
python verify.py                                        # 全部重跑对榜
python challenge.py bots/Astra/bot.py                   # 单独跑一份
python replays/trace.py bots/Astra/bot.py               # 导出回放
```

## 读代码的建议

1. 先看 `obs` 怎么用：躯干倾角、角速度、触地状态、下一个障碍的距离。
2. 再看它们怎么处理**倒地恢复**——这一期大部分差距不在「跑得多快」，而在「摔了能不能起来」。
3. 注意这是 45 分钟里写出来的，很多是启发式状态机，不是控制论意义上的最优解。

## 命名

目录名是选手名（和视频里一致），模型型号与通道见 `results/leaderboard.md`。
