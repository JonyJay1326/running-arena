# 第 1 期 · 百米障碍跑 · 成绩

> 由 `results/leaderboard.json` 生成；重跑校验：`python verify.py`。

**名次口径**：冲线者按冲线用时从短到长排在前面；未冲线者按计分距离（m）从高到低排在后面。冲线者一律排在未冲线者之前。

## 初赛（16 人，各跑一场，45 分钟开发窗口）

| # | 选手 | 模型 | 通道 | 用时 | 成绩 | 代码 |
|--:|---|---|---|---:|---:|---|
| 1 | Astra | GPT-6 Astra（medium） | Codex | 11.55s | 18.87 m | `bots/Astra/bot.py` |
| 2 | Opus | Claude Opus 5.5（high） | Claude | 17.10s | 19.14 m | `bots/Opus/bot.py` |
| 3 | Kimi | Kimi-K3.1-Pro | OpenCode | 17.42s | 18.02 m | `bots/Kimi/bot.py` |
| 4 | Terra | GPT-6.1 Terra（high） | Codex | 27.43s | 18.21 m | `bots/Terra/bot.py` |
| 5 | Sonnet | Claude Sonnet 5.5（high） | Claude | 30.00s | 18.01 m | `bots/Sonnet/bot.py` |
| 6 | Sol | GPT-6.1 Sol（high） | Codex | - | 15.05 m | `bots/Sol/bot.py` |
| 7 | Qwen | Qwen3.8-Max | Qoder | - | 10.39 m | `bots/Qwen/bot.py` |
| 8 | Gemini | Gemini 4 Argon | 人工传话 / 自测 | - | 9.87 m | `bots/Gemini/bot.py` |
| 9 | DeepSeek | DeepSeek-Flash | Qoder | - | 9.10 m | `bots/DeepSeek/bot.py` |
| 10 | Grok | Grok 4.7 Fast（high） | Grok | - | 6.12 m | `bots/Grok/bot.py` |
| 11 | Hy4 | Hy4-Preview | OpenCode | - | 5.57 m | `bots/Hy4/bot.py` |
| 12 | 豆包 | Seed-2.1-Pro | OpenCode | - | 4.87 m | `bots/豆包/bot.py` |
| 13 | GLM | GLM-5.3 | OpenCode | - | 2.26 m | `bots/GLM/bot.py` |
| 14 | MiMo | MiMo-V2.6-Pro | CodeBuddy | - | 1.66 m | `bots/MiMo/bot.py` |
| 15 | Step | Step-5-Preview | CodeBuddy | - | 1.64 m | `bots/Step/bot.py` |
| 16 | MiniMax | MiniMax-M3.1-Flash | Qoder | - | 0.11 m | `bots/MiniMax/bot.py` |

## 决赛（前 4 名，两阶段各 20 分钟；只看第二阶段结束时冻结的 bot）

| # | 选手 | 模型 | 通道 | 用时 | 成绩 | 代码 |
|--:|---|---|---|---:|---:|---|
| 1 | Astra | GPT-6 Astra（medium） | Codex | 11.37s | 18.37 m | `bots-final/Astra/bot.py` |
| 2 | Opus | Claude Opus 5.5（high） | Claude | 15.67s | 18.45 m | `bots-final/Opus/bot.py` |
| 3 | Kimi | Kimi-K3.1-Pro | OpenCode | 17.42s | 18.02 m | `bots-final/Kimi/bot.py` |
| 4 | Terra | GPT-6.1 Terra（high） | Codex | 25.93s | 18.46 m | `bots-final/Terra/bot.py` |

## 说明

- 物理是确定性的：固定随机种子，同一份 bot 在任何机器上重跑结果一致。
- 倒地（躯干/头触地、躯干中心 < 0.45m、或倾角 ≥ 50° 且中心 < 0.7m）期间的位移不计分。
- 引擎哈希见 `engine/SHA256SUMS` 和 `leaderboard.json` 的 `engine` 字段；改引擎会让 `verify.py` 失败。
- 决赛名次只看第二阶段；Opus、Terra 的决赛成绩比自己初赛更好，但仍在 Astra 之后。
