# 静态站

三期榜单 + 回放的静态站，零依赖、零后端。

```bash
python site/build.py      # 生成 site/dist/
python -m http.server 8000 --directory site/dist
```

`site/dist/` 不进 git，由 CI 生成。

## 内容

| 路径 | 来源 |
|---|---|
| `/` | 三期卡片 + 赛季积分（按各期名次算 15/12/10/8/6/5/4/3/2/1） |
| `/ep1/` | `ep1-obstacle/results/leaderboard.json` + `extra.json` |
| `/ep1/replay/` | `ep1-obstacle/replays/` 原样复制（浏览器播放器 + trace） |
| `/ep2/` | `ep2-longjump/results/leaderboard.json` + `extra.json` |
| `/ep2/replay/` | `ep2-longjump/replays/` |
| `/ep3/` | `ep3-golf/results/standings.json` |
| `/ep3/replay/r1|r2/` | `ep3-golf/viewer/` + `ep3-golf/replays/` |

改任何一期的成绩或回放，重跑 `python site/build.py` 就会同步。

## 部署

`main` 分支每次 push 会触发 `.github/workflows/pages.yml`：生成 → 上传 → 发布到 GitHub Pages。

首次启用：

1. 仓库 **Settings → Pages → Build and deployment → Source** 选 **GitHub Actions**。
2. 等一次 workflow 跑完，`https://jonyjay1326.github.io/running-arena/` 应该能打开。
3. 自定义域名：Pages 里 **Custom domain** 填 `arena.saiboliang.top`（`site/dist/CNAME` 已经写好），勾上 Enforce HTTPS。
4. Cloudflare DNS 加一条记录：

   | 类型 | 名称 | 内容 | 代理 |
   |---|---|---|---|
   | CNAME | `arena` | `jonyjay1326.github.io` | 先关（DNS only），证书签好后再开 |

   “先关代理”是因为 GitHub 签 HTTPS 证书时要能直接验证；签好之后打开小黄云不影响。

## 注意

- 站点里没有角色立绘，只有榜单和回放——立绘涉及各家模型的形象，不随仓库发布。
- 第 3 期观战页用到 `DecompressionStream`（回放是 gzip 的），Chrome 80+ / Safari 16.4+ / Firefox 113+ 都可以。
- 回放文件约 10 MB，全部是静态资源，不消耗函数额度。
