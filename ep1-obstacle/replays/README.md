# 回放

这里的回放是**逐帧刚体姿态**，不是视频：`trace.py` 用官方引擎跑一场比赛，把每帧的躯干、头、四肢位置导出成 JSON，`viewer.html` 在浏览器里画出来。体积小（每人约 200KB），可以随便拖动、换速度、逐帧看。

## 看

```bash
# 在仓库根目录
python -m http.server 8000
# 打开 http://127.0.0.1:8000/ep1-obstacle/replays/viewer.html
```

`file://` 直接打开也能看：把 `*.json` 拖进页面即可（浏览器不允许页面自己读本地 JSON，所以右上角的下拉列表只在 http 下可用）。

## 生成

```bash
python replays/trace.py bots/Astra/bot.py           # 单个
python replays/trace.py --all                       # 全部 16 位，并重建 index.json
python replays/trace.py bots/X/bot.py --fps 60      # 更高采样率
```

## 说明

- 这是**简化渲染**：刚体位置完全真实，但没有贴图、立绘、表情和慢动作。成片里的效果由另一套渲染器产出（视频版），不在本仓库。
- 想复现视频画面：用 `engine/visualizer.py`（同一套引擎的渲染器），需要额外素材，超出本仓库范围。
- 成绩永远以 [`../results/leaderboard.json`](../results/leaderboard.json) 为准，回放只用于观察动作。
