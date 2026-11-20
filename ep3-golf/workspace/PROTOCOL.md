# golf-v1 单人高尔夫引擎原型

已实现任务要求的独立引擎、物体/击退/罚杆、JSONL 本地执行器、工程 Bot、校准器、龙头转换和回放契约。全部为 `engineeringOnly`。**初始风切变未达到校准目标**；保留规则初值，仅给候选建议，等待主控决定。

只在 `experiments/golf-v1/` 和新建的 `output/golf-v1-*` 目录增加文件；没有修改规则文档、旧引擎、simultaneous-v1、harness、viewer 或已有产物。项目不是 Git 仓库；未安装依赖、未联网、未调用模型。

## 完成标准与证据

最终证据以本表目录为准。较早的 `smoke-01/02`、`calibration-01`、`recovery-01` 和首次失败的测试输出保留作开发记录，不作为本次验收依据。

| 项目 | 结果 / 证据 |
|---|---|
| 自动化测试 | `node --test experiments/golf-v1/*.test.mjs`：35 项全部通过；最终 TAP 与源码 sha256 清单在 [validation-01](../../output/golf-v1-validation-20261111-01/validation.json)、[tests.tap](../../output/golf-v1-validation-20261111-01/tests.tap) |
| 两 Bot × 两洞独立进程 smoke | [smoke-03/summary.json](../../output/golf-v1-smoke-20261111-03/summary.json)，四洞均真正完成、0 罚杆、进程树清理确认、回放复算通过 |
| 故意打坏首杆后恢复 | [recovery-02/summary.json](../../output/golf-v1-recovery-20261111-02/summary.json)，四个场景均完成；每场完整回放在同目录 |
| 风切变校准 | [calibration-02/report.json](../../output/golf-v1-calibration-20261111-02/report.json)、[中文 summary.md](../../output/golf-v1-calibration-20261111-02/summary.md)，60 个样本、12 次训练试射；初值未达标，如实保留失败结果 |
| 龙头地图 | [maps/dragon.json](maps/dragon.json)、[全部小碎块坐标](maps/dragon-fragments.json)；转换原始证据在 [dragon-01](../../output/golf-v1-dragon-20261111-01/fragments.json) |
| 回放约定 | [REPLAY-FORMAT.md](REPLAY-FORMAT.md)，包含字段、像素/脚底/圆心单位、绝对秒时间轴、事件顺序和复算方法 |

测试覆盖风切变数值/隐藏常量、四种弹、四种实体挡弹、遮挡、血量裂纹、支撑坠落、Bug 确定性/无风/滑行/斜坡、鸡受伤及掉出、Bug 出界、不可打、上限、三类完成条件、飞机配额与站位、观测过滤、回放篡改拒绝、八张地图加载、Python/Node 协议、超时/崩溃/坏 JSON/错 reqId、派生进程整树清理、输出拒绝覆盖、龙头重建和破坏后恢复。

| Bot | token 洞杆数 | Bug 洞杆数 | 破坏首杆后 token / Bug |
|---|---:|---:|---:|
| naive | 3 | 2 | 4 / 4 |
| fitted | 3 | 1 | 4 / 2 |

smoke 每场是一个独立 Bot 进程：fitted 每个会话在练习阶段使用 12 次试射，随后打洞；不是把两场合并声称只用 12 次。naive 无试射。恢复场景是在发球台中段用 0 力掘进弹炸坑，保留地形破坏后继续；只证明这个明确场景，不能证明任意首杆破坏都有解。

## 文件说明

| 文件 | 用途 |
|---|---|
| `config.mjs` | 原物理、四弹常量；风切变、击退、物体框；公开配置过滤隐藏参数 |
| `geometry.mjs` | 从 simultaneous-v1 复制的身体/站姿、遮挡和挖坑实现；仅导出额外的可见性函数，原文件不动 |
| `objects.mjs` | 方框/圆形距离、遮挡采样、实际格子顶面支撑、地形碰撞 |
| `physics.mjs` | 受风切变的炮弹/飞机积分、确定性细分、工程轨迹探针 |
| `sim.mjs` | GolfSim、单次动作整杆结算、观测过滤、事件回放与复算 |
| `maps.mjs` | 六冻结图只读调用原 loader，平地图及龙头 RLE/hash 校验、球洞 JSON 校验 |
| `holes/*.json` | 两个平地练习洞，含小/大 token，及 Bug/尖叫鸡；均有一架飞机 |
| `convert-dragon.py` | 标准库 PNG 解码、alpha/亮度二值化、RLE、sha256、四连通碎块列表；不删除碎块 |
| `runner.mjs` | 可信本地 Bot JSONL 会话、期限、练习试射、收据/日志/回放；复用旧进程守护代码 |
| `bots/example.mjs`, `bots/example.py` | 不带瞄准能力的最小协议示例 |
| `bots/bot-naive.mjs`, `bots/bot-fitted.mjs`, `bots/aim.mjs` | 主控工程 Bot、落点拟合、选弹/选位/飞机换边及落点修正；**不可作为选手材料** |
| `bots/fixture.mjs` | 协议失败和子进程清理测试夹具 |
| `calibrate.mjs` | 固定矩阵校准、预测误差、同目标重新瞄准误差、参数建议搜索 |
| `smoke.mjs`, `recovery.mjs` | 四场真实进程 smoke、四场首杆破坏恢复证据 |
| `verify-replay.mjs`, `validate.mjs` | 单回放复算 CLI、最终测试与八场回放核对/源码指纹 |
| `*.test.mjs` | 单元/整杆/地图/进程集成测试 |

## 运行命令

在仓库根目录执行，Node.js 22 和现有 Python 3 标准库即可。每个输出目录必须是全新路径；已有目录直接拒绝，不续跑、不覆盖。

```powershell
node --test experiments/golf-v1/*.test.mjs

$golfTag = Get-Date -Format 'yyyyMMdd-HHmmss'
node experiments/golf-v1/smoke.mjs --out "output/golf-v1-smoke-$golfTag"
node experiments/golf-v1/calibrate.mjs --out "output/golf-v1-calibration-$golfTag"
node experiments/golf-v1/recovery.mjs --out "output/golf-v1-recovery-$golfTag"
python -X utf8 experiments/golf-v1/convert-dragon.py --out "output/golf-v1-dragon-$golfTag"

node experiments/golf-v1/runner.mjs --hole experiments/golf-v1/holes/practice-bug.json --bot fitted --out "output/golf-v1-local-$golfTag"
node experiments/golf-v1/verify-replay.mjs output/golf-v1-smoke-20261111-03/fitted-bug/replay.json

node experiments/golf-v1/validate.mjs --out "output/golf-v1-validation-$golfTag" --smoke output/golf-v1-smoke-20261111-03 --calibration output/golf-v1-calibration-20261111-02 --recovery output/golf-v1-recovery-20261111-02
```

内置 Bot 名称是 `naive`、`fitted`、`node-example`、`python-example`。自定义可信程序用 JS 接口 `runHole({hole, command:{file,args,cwd?},directory,practice?})`；拒绝 shell 命令字符串。所有失败显式抛错并保存 receipt/decisions；无效动作、超时或崩溃不伪造罚杆/有效成绩。

## Bot JSONL 协议 v1

每行一个 JSON，stdout 只用于协议，诊断写 stderr。执行器每个请求带唯一递增 `reqId` 和 `deadlineMs`，响应必须原样回传 reqId。默认 init 5 秒、每次 turn 1 秒；只接受可信本地进程，不是沙箱。

初始化请求：`{type:"init",reqId,deadlineMs,...observation,config:publicConfig()}`；响应仅 `{reqId,ok:true}`。之后每杆前发送 `{type:"turn",reqId,deadlineMs,...observation}`。

观测包含：

- `hole:{id,map,par,cap,wind,goal,recycle,tee,teeActive}`；`strokes,penalties,shotsTaken,fly`。
- `terrain:{width:400,height:225,cell:4,rle}`，`actor:{x,y,groundAngle,grounded}|null`。
- `objects:[{id,type,x,y,width?,height?,radius?,hp?,stage,removed,broken,stunned,inRecycle}]`，包括已碎/晕倒物体的状态。
- `lastResult:null|{action,command,from,impacts,objectChanges,penalties}`；command/from 是 Bot 自己已知的上次动作和开炮站姿。
- `impacts:[{projectileId,kind,x,y,hit,objectId}]`；`objectChanges:[{id,before,after,damage}]`；`penalties:[{reason,objectId}]`。这是上一杆单独结果，作为下一次输入的一部分。没有轨迹点、时刻、飞行时间或隐藏物理参数。
- `finished,completed,rulesVersion,engineeringOnly`。正常执行器不会再向已结束洞发 turn，最终状态在回放/收据里读取。

三种响应互斥，不允许额外字段：

```json
{"reqId":2,"moveX":460,"attack":{"kind":"single","angle":30,"power":50,"dir":1}}
```

```json
{"reqId":3,"fly":{"angle":45,"power":50,"dir":1}}
```

```json
{"reqId":4,"unplayable":true}
```

开炮角度 0～85，飞机沿用 0～90，力度 0～100，dir 为 ±1。moveX 可省略，只有发球台仍生效时可用；必须存在合法站姿。飞机成功后 moveX 禁用。不可打仅适用于尚未入洞的 Bug。

工程试射阶段仅在 `practice:true` 时、init 之前出现。请求 `{type:"practice",remaining,lastProbe}`，lastProbe 只含上一试射落点 `{x,y,hit}`。Bot 回复 `{probe:{actor,wind,aim},prediction:{x,y}}`，或 `{ready:true,fit?}`；执行器最多接受 12 次，必须先报预测落点。每次是独立、无物体的平地练习场，站姿脚底 780，显示风力在单次试射中固定。该接口供测试 Bot 校准，不代表已冻结的选手训练协议。

## 校准结果与参数建议

当前仍为规则初值 `clamp(0.3+1.7×(780-y)/720,0.3,2.0)`。矩阵包含四个风力 −8/−4/+4/+8、24 个高抛和 36 个低平组合；左右镜像均顺风发射，高抛实际顶点 y<300，低平 y>650，全部落在地图内。误差按二维欧氏距离计算。

| Bot / 口径 | 高抛平均 / 最大 px | 低平平均 / 最大 px |
|---|---:|---:|
| naive，把均匀风预测落点作为目标 | 172.758 / 280.127 | 15.376 / 35.865 |
| fitted，同一输入预测落点与实测之差 | 0.003 / 0.004 | 0.0003 / 0.0008 |
| fitted，调整力度后瞄准 naive 的同一目标 | 0.234 / 0.708 | 0.131 / 0.461 |

- naive 高抛均值超过 150，低平连平均值也超过 10，**初值不通过**。
- fitted 仅用 12 次落点拟合为约 `0.299988 + 1.700012×(780-y)/720`；矩阵中的预测和重新瞄准均 <5 px。
- 首选数值建议：`clamp(0.95+0.4×(780-y)/720,0.95,1.35)`。同矩阵 naive 高抛均值 101.788 px，低平最大 0.660 px，同时达到 naive 两项目标。候选只改变 f(y)，未改变其他物理。
- 候选尚未应用，也未重新训练 fitted 验收；报告另含两个备选。初始模型的截断区没有被本平地训练覆盖，不能据此承诺任意地图、任意高度的 fitted 误差均 <5 px。

## 龙头转换结果

输入 `viewer/assets/video/build-world/forge-mask400.png`，400×225 一像素对应一格。此处假设为白色实心、黑色空地；规则为 alpha≥128 且整数加权亮度 `(299r+587g+114b)/1000≥128`。保留全部二值化结果，没有平滑、补边、删块。

- 实心格 **42,772**，四连通块 **44**，其中 **30** 块小于 4 格。
- 完整坐标在 `maps/dragon-fragments.json` 的 `fragments[].gridCells` 和 `worldTopLeft`；尺寸顺序及所有连通块大小也在文件中。
- 原图 sha256：`6307e91796053b7feb21ab8626a3c093d1a609b57e56d64751357da20ba621e9`。
- 网格 sha256：`d9a7f82bc7aa3c5da7e3076b6f1084a7df434a5539b38e790adfc20cef29e9bf`。
- 转换脚本可在全新输出目录重建相同 hash；已纳入自动化测试。

## 已知限制

- 这是引擎和两练习洞原型，没有正式 9/12 洞、16 人总榜、选手材料打包或观战画面。正式场的双路线、至少半数最省杆需要高抛，以及任意破坏无死局，仍须由主控设计并另验。
- 两个工程 Bot 有基本弹种/站位/落点修正和飞机换边策略；不是通用最优解器。示例 Bot 只演示协议，可能打满上限。
- 拟合只识别已探测高度上的仿射风切变；工程矩阵未覆盖逆风、全部山地图、上下截断层。不能用此次小误差替代全空间保证。
- 支撑/碰撞是冻结 4 px 格子；对象不会互相堆叠或挤开角色，只挡炮弹并受地形支撑。物体轨迹每主帧采样，精确状态转折另有子步事件。
- 角色脚下无可落地支撑时 actor 为 null；仍可在有效发球台重新选位，已用飞机离开发球台后若完全坠出则不能继续开炮。规则没有角色救援罚杆，因此显式失败，不自行新增规则。
- 单动作物理超过 20 秒显式技术失败，不将未收敛运动强行静止，也不产生有效成绩。回放复算要求同一引擎配置/实现，不提供旧试验版本兼容层。
- 当前 runner 一次跑一洞；每次启动 fitted 是独立练习会话。跨洞复用一个已训练进程及整场共享试射额度尚未封装。

## 需要主控决定的问题

1. **风切变参数**：是否采用首选 `0.95 + 0.4×(780-y)/720`、上下限 0.95/1.35？初值已实测不达标。批准后需重新校准，并加逆风/山图/边界高度矩阵；本次未改规则或初值。
2. **龙头小碎块**：30 个 <4 格四连通块是否全部保留？现已逐格列出并保留；连通性目前按四邻域，若改八邻域需主控明确。
3. **飞机与角色失足**：此处假设为“只有开炮计杆”，飞机只扣配额、不加杆；飞机出界/无合法站位时保留原站位且不退配额、不额外罚杆。角色坠出且已离开发球台的救援方式未定义，请决定；目前显式报告失败。
4. **Bug 重置后无地形支撑**：地形按规则不恢复。此处假设为重置后本动作冻结在回置点，防止同杆重复出界罚；下一物理动作重新检查支撑。不可打同样不立即二次坠落，避免一次声明连续扣分。是否需要找最近合法支撑点，应由主控决定。
5. **击退和同时事件细节**：此处假设为多爆炸速度向量累加、击退同样受地形遮挡；同刻爆炸按 projectileId 依次结算，后一颗看到前一颗挖坑后的地形。规则未明确“追加/替换速度”和同刻伤害是否应使用共同爆炸前快照；请确认后再冻结。爆心与 Bug 圆心重合时确定性取向上。
6. **支撑与重叠**：此处假设为任何脚底有效接触都可支撑，物体之间不互相支撑/碰撞；初始与地形穿透拒绝，但初始物体彼此重叠时按数组顺序归属命中。正式洞应避免重叠；如需物体堆叠，请先明确规则。
7. **试射和验收口径**：当前工程试射可在独立平地场选择显示风力/站位，单 Bot 进程合计≤12。正式训练是否限于两个 JSON 洞、是否固定风力，以及低平“<10”采用均值或最坏值，需要主控冻结。本次用低平最坏值验收，初值两种口径都失败。
