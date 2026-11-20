# 回放

和第 1 期一样，这里的回放是**逐帧刚体姿态**，不是视频：`trace.py` 用官方引擎、按成绩表里记录的风种子跑那一跳，把每帧姿态导出成 JSON。

每人都有，体积几十 KB，可以拖进度、调速度、逐帧看落点和起跳角。

```bash
python -m http.server 8000        # 在仓库根目录
# 打开 http://127.0.0.1:8000/ep2-longjump/replays/viewer.html
```

`file://` 直接打开也能看：把 `*.json` 拖进页面即可（右上角的下拉列表只在 http 下可用）。

## 生成

```bash
python replays/trace.py Astra              # 他在资格赛里最好那一跳
python replays/trace.py Astra --final      # 决赛里最好那一跳
python replays/trace.py Astra --attempt 2  # 指定第几跳
python replays/trace.py --all              # 资格赛 16 人全导，并重建 index.json
```

## 说明

- 风种子取自 `results/leaderboard.json`，所以导出的就是你看到的那个成绩。
- 想要成片（1080p、慢动作、游戏风 UI），用 `engine/visualizer.py`，见上一级 README。
- 成绩以 [`../results/leaderboard.json`](../results/leaderboard.json) 为准。
