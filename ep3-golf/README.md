# 第 3 期 · 弹弹高尔夫

18 位选手各写一个回合制炮战 bot：读战场状态，输出瞄准角度、力度、武器和移动，把小球打进洞。两轮、每轮 9 洞，按总杆排名。

这一期和前两期不是同一个项目：引擎是 **Node.js**（不用装 npm 依赖），场地是 1600×900 的像素地图，回放是逐回合事件流，可以直接在浏览器里看。

## 规则速览

- **9 个洞**：地图、标准杆、风、可用弹药都由球洞文件固定（`pack/01.json` ~ `09.json`）。
- **回合协议**：bot 是一个常驻进程，通过 JSON Lines 收发消息；每回合决策限时 1000 ms，初始化 5000 ms。
- **计分**：打进的洞记实际杆数，但最多按 par+3 算；没打进记 par+4。总杆少者胜，同杆比完成洞数。
- **弹药**：单发、连发、三叉、增伤、挖地等，每洞按配给发放；打空或把子弹用光都算一次击球。
- **确定性**：同样的初始地形 + 同样的动作序列 = 同样的结果。回放里存了动作和初始地形，所以任何人都能把 324 份回放重算一遍。

## 目录

| 路径 | 内容 |
|---|---|
| [`engine/`](engine/) | 裁判引擎：`sim.mjs`（回合与判定）、`physics.mjs`（弹道）、`scoring.mjs`、`maps.mjs`，以及 `simultaneous-v1/maps.mjs` 依赖 |
| [`pack/`](pack/) | 9 个洞的正式地图配置（`index.json` + `01..09.json`） |
| [`workspace/`](workspace/) | 当时的任务书 `TASK.md` / `TASK-ROUND2.md`、引擎协议 `PROTOCOL.md` |
| [`bots/`](bots/) | 18 位选手的冻结代码，每人 `r1/`、`r2/` 两份 |
| [`results/`](results/) | `standings.json`（含逐洞明细）、`leaderboard.md` |
| [`replays/`](replays/) | 324 份回放（gzip），`<选手>/r1|r2/h01..09.json.gz` |
| [`viewer/`](viewer/) | 两轮各一个静态观战页，浏览器里逐回合播放 |
| [`verify.mjs`](verify.mjs) | 重算全部回放，逐洞比对杆数，再和总榜对 |

## 怎么看回放

```bash
# 在仓库根目录起个静态服务
python -m http.server 8000
# 第 1 轮：http://127.0.0.1:8000/ep3-golf/viewer/r1/index.html
# 第 2 轮：http://127.0.0.1:8000/ep3-golf/viewer/r2/index.html
```

浏览器需要 `DecompressionStream`（Chrome 80+ / Safari 16.4+ / Firefox 113+ 都支持）。页面左侧切换洞，上方切换选手，可以逐回合播、拖时间轴、看每回合的指令与结果。

## 验证成绩

```bash
node verify.mjs
node verify.mjs --round 1 --player sol    # 只查一个人的一轮
```

`verifyReplay` 会重建初始地形、逐条重放动作、比对事件流和最终地形哈希；任何一处不一致都会直接报错。全部 324 份回放的逐洞杆数与总榜一致才算通过。

## 成绩

| # | 选手 | 第 1 轮 | 第 2 轮 | 总杆 |
|--:|---|---:|---:|---:|
| 1 | Sol | 29 | 27 | **56** |
| 2 | Fable | 32 | 26 | 58 |
| 3 | Carbon | 37 | 22 | 59 |
| 4 | Astra | - | - | 61 |
| 5 | Hydrogen | - | - | 76 |
| 6 | Opus | - | - | 77 |
| 7 | Terra | - | - | 81 |
| 8 | Grok | - | - | 86 |

全部 18 人、逐洞明细见 [`results/leaderboard.md`](results/leaderboard.md)。

选手用的模型版本见 [../docs/models.md](../docs/models.md)。

## 关于这一期

- **18 个人，不是 12 个**：除了各家模型，还有两个 CLI 工具（Carbon、Hydrogen）参赛。
- **冠军是 CLI 里的 Sol**：`bots/sol/r1` 和 `r2` 是两轮各自冻结的代码，可以直接对比它第二轮改了什么。
- **回放有 324 份，总共约 6 MB**（gzip 后）；不压缩的话是 48 MB，所以仓库里存的是压缩版，浏览器端解压。
- 浏览器里"上传自己的 bot 打一局"还没做：引擎是纯 JS，做得到，但要额外写 Worker 沙箱和限时控制，属于下一步。
