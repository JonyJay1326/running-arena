# 第 2 期 · 跳远

16 位模型，同一个身体、同一份任务书：在 45 分钟里写出 `act(obs)`，从 2～9 米之间的起点自由助跑，越过 12 米起跳线，跳进沙坑。资格赛每人 3 跳取最好，前 8 进决赛；决赛重新计分，再跳 3 跳。

## 规则速览

- **场地**：0～12 米助跑道，12 米处是踏跳线，12～27 米是深 3 米的沙坑。起点 `START_X` 由代码自己定，合法范围 2～9 米。
- **接口**：`act(obs)` 返回 6 个数 `[hip_L, knee_L, hip_R, knee_R, shoulder_L, shoulder_R]`；踝关节可控时可以返回 8 个（末尾加 `ankle_L, ankle_R`）。范围 `-1 ~ 1`。
- **计分**：落点**最靠后的真实触地点**到起跳线的距离；踩线、连跳、出界都按犯规/0 计。
- **风**：每跳随机抽种子，强度固定 1.5；种子随成绩一起公开，所以每一跳都能逐帧重放。
- **名次**：3 跳取最好有效成绩；同分先比第二好、再比第三好成绩；前 8 进决赛，决赛成绩不带入资格赛。

完整条文见 [`workspace/TASK.md`](workspace/TASK.md)（当时发给选手的原文，`{STAGE}`/`{MINUTES}`/`{PACKAGES}` 是赛时按选手替换的占位符）。

## 目录

| 路径 | 内容 |
|---|---|
| [`engine/`](engine/) | 正式引擎 `physics.py` + `evaluator.py` + 连跳审计 `charge_audit.py`，哈希见 `SHA256SUMS` |
| [`workspace/`](workspace/) | 任务书、选包规则、四个技能包（`packages/`）、起手模板、引擎副本 |
| [`bots/`](bots/) | 资格赛 16 份冻结代码 |
| [`bots-final/`](bots-final/) | 决赛 8 份冻结代码 |
| [`extra/`](extra/) | 榜外补测：6.2 Astra、Fable 5.2 |
| [`results/`](results/) | `leaderboard.json`（含每跳风种子与反馈上下文）、`leaderboard.md`、`bots_manifest.json` |
| [`replays/`](replays/) | `trace.py` 导出逐帧姿态 → `viewer.html` 播放（沙坑版） |
| [`verify.py`](verify.py) | 按记录的风种子逐跳重放，比对成绩 |
| [`challenge.py`](challenge.py) | 用你的 bot 跳三跳，看排第几 |

## 三条命令

```bash
python verify.py                                    # 78 跳全部重放对榜
python challenge.py my_bot.py                       # 三跳，出最好成绩与名次
python replays/trace.py Astra                       # 导出回放，再开 replays/viewer.html
```

回放：仓库根目录起 `python -m http.server`，打开 `/ep2-longjump/replays/viewer.html`，右上角切换 16 位选手。

## 渲染成片（可选）

官方渲染器在 `engine/visualizer.py`，和比赛引擎共用同一套物理。需要 `pygame` 和 `ffmpeg`：

```bash
pip install pygame-ce
cd ep2-longjump
python engine/visualizer.py bots/Sol/bot.py --track jump --upscale 1080 --quality high --save sol.mp4
```

- `--track jump` 走跳远场地（助跑道 + 沙坑），单份 bot 时自动跑多轮试跳。
- `--upscale 1080` / `1440` 用 lanczos 放大，`--quality high` 是 crf12 + slow。
- `--auto-slowmo` 在落地前自动慢放。
- 这一期的渲染器是程序绘制画面（没有立绘贴图），动作、镜头、成绩都是真实模拟结果。

## 成绩

资格赛前 8（每人 3 跳，取最好）：

| # | 选手 | 第 1 跳 | 第 2 跳 | 第 3 跳 | 最好 |
|--:|---|---:|---:|---:|---:|
| 1 | Astra | 4.754 | 4.441 | 5.187 | **5.187** |
| 2 | Hy4 | 4.619 | 4.466 | 4.579 | **4.619** |
| 3 | Sol | 2.134 | 3.640 | 4.465 | **4.465** |
| 4 | Terra | 4.000 | 3.168 | 3.836 | **4.000** |
| 5 | Sonnet | 3.772 | 3.800 | 3.404 | **3.800** |
| 6 | Opus | 3.356 | 3.703 | 3.072 | **3.703** |
| 7 | Grok | 3.266 | 2.292 | 3.207 | **3.266** |
| 8 | GLM | 2.815 | 2.006 | 2.800 | **2.815** |

决赛前 4：

| # | 选手 | 最好 | 说明 |
|--:|---|---:|---|
| 1 | Sol | **6.157** | 资格赛第 3，决赛第 3 跳反超 |
| 2 | Astra | 5.599 | 资格赛第 1，决赛第 1 跳就到位 |
| 3 | Hy4 | 5.109 | |
| 4 | Opus | 5.016 | |

全部 16 人 + 决赛明细见 [`results/leaderboard.md`](results/leaderboard.md)。

## 榜外：两位补测

| 选手 | 模型 | 最好 | 备注 |
|---|---|---:|---|
| Fable 5.2 | claude-fable-5-2（medium） | **6.457** | 起点 3 米，三跳全部有效 |
| Astra 6.2 | gpt-6.2-astra（high） | **6.256** | 三跳全部有效 |

条件同一套：同一引擎、同一跳远规则、三跳取最好。但它们在正式赛之后补跑、模型版本也更新，所以**只进 `extra/`，不进正式榜**。代码在 [`extra/`](extra/)，`verify.py` 也会一起重放。

选手用的模型版本见 [../docs/models.md](../docs/models.md)。

## 关于这一期

- **Kimi 有一跳犯规**：第 2 跳踩线，按 0 计；它靠第 3 跳的 0.978 米保住第 14 名。
- **决赛才出现 6 米**：资格赛全场最好 5.187 米，决赛 Sol 跳到 6.157 米——多出来的 30 分钟和第二轮风况让成绩整体上了一个台阶。
