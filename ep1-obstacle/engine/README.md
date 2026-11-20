# engine

| 文件 | 能不能改 |
|---|---|
| `physics.py`、`evaluator.py` | **不能。** 这两个文件决定成绩，哈希写在 `SHA256SUMS` 里，改了就复现不出榜单。 |
| `visualizer.py`、`viz_*.py` | 能。只读逐帧姿态、负责出画面，改它不影响任何成绩。 |

渲染用法见上一级 README 的「渲染成片」。缺 `assets/` 时渲染器会自动回退成程序绘制。
