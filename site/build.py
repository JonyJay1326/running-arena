"""生成静态站：三期榜单 + 回放入口。

    python site/build.py

输出到 site/dist/，可以直接扔 GitHub Pages。
不引入任何前端依赖，榜单从各期的 results/*.json 现算，改数据重跑一次即可。
"""
import json
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DIST = os.path.join(HERE, "dist")
DOMAIN = "arena.saiboliang.top"

POINTS = [15, 12, 10, 8, 6, 5, 4, 3, 2, 1]


def load(*parts):
    with open(os.path.join(REPO, *parts), encoding="utf-8") as f:
        return json.load(f)


def points(rank):
    return POINTS[rank - 1] if 1 <= rank <= len(POINTS) else 0


def write(path, html):
    full = os.path.join(DIST, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8", newline="\n") as f:
        f.write(html)


def page(title, body, active="", base=""):
    nav = "".join(
        f'<a class="{"on" if key == active else ""}" href="{base}{href}">{label}</a>'
        for key, label, href in (
            ("home", "首页", ""),
            ("ep1", "第 1 期 · 障碍跑", "ep1/"),
            ("ep2", "第 2 期 · 跳远", "ep2/"),
            ("ep3", "第 3 期 · 弹弹高尔夫", "ep3/"),
        )
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<link rel="stylesheet" href="{base}style.css">
<header class="top"><div class="wrap">
  <a class="brand" href="{base}">AI 运动会</a>
  <nav>{nav}<a href="https://github.com/JonyJay1326/running-arena" target="_blank" rel="noopener">GitHub</a></nav>
</div></header>
<main class="wrap">
{body}
</main>
<footer class="wrap">
  <p>让各家大模型拿同一份任务书，各写一个 bot，在同一套引擎里比赛。</p>
  <p class="dim">代码 MIT；角色立绘涉及其它模型的形象，不随仓库与本站发布。</p>
</footer>
</body>
</html>
"""


def table(headers, rows, numeric_from=1):
    th = "".join(f'<th class="{"n" if i >= numeric_from else ""}">{h}</th>' for i, h in enumerate(headers))
    trs = []
    for r in rows:
        tds = "".join(
            f'<td class="{"n" if i >= numeric_from else ""}">{c}</td>' for i, c in enumerate(r)
        )
        trs.append(f"<tr>{tds}</tr>")
    return f'<div class="tablewrap"><table><thead><tr>{th}</tr></thead><tbody>{"".join(trs)}</tbody></table></div>'


def main():
    if os.path.isdir(DIST):
        shutil.rmtree(DIST)
    os.makedirs(DIST)

    ep1 = load("ep1-obstacle", "results", "leaderboard.json")
    ep2 = load("ep2-longjump", "results", "leaderboard.json")
    ep3 = load("ep3-golf", "results", "standings.json")
    ep1_extra = load("ep1-obstacle", "results", "extra.json")
    ep2_extra = load("ep2-longjump", "results", "extra.json")

    shutil.copy2(os.path.join(HERE, "style.css"), os.path.join(DIST, "style.css"))
    with open(os.path.join(DIST, ".nojekyll"), "w") as f:
        f.write("")
    # 自定义域名由 Pages 设置里配置；这里只在显式给了环境变量时写 CNAME，
    # 避免 DNS 还没配就把站点指到一个打不开的域名上。
    cname = os.environ.get("SITE_CNAME")
    if cname:
        with open(os.path.join(DIST, "CNAME"), "w", newline="\n") as f:
            f.write(cname + "\n")

    # 回放：把现成的静态播放器和数据搬进来
    shutil.copytree(os.path.join(REPO, "ep1-obstacle", "replays"), os.path.join(DIST, "ep1", "replay"))
    shutil.copytree(os.path.join(REPO, "ep2-longjump", "replays"), os.path.join(DIST, "ep2", "replay"))
    shutil.copytree(os.path.join(REPO, "ep3-golf", "viewer"), os.path.join(DIST, "ep3", "replay"))
    shutil.copytree(os.path.join(REPO, "ep3-golf", "replays"), os.path.join(DIST, "ep3", "replays"))

    # 目录访问时直接打开播放器
    for ep in ("ep1", "ep2"):
        src = os.path.join(DIST, ep, "replay", "viewer.html")
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(DIST, ep, "replay", "index.html"))

    # ---- 赛季积分 ----
    # 各期选手名写法不完全一致（Doubao/豆包、Grok-Fast/Grok…），先归一
    alias = {"Doubao": "豆包", "DeepSeek-Flash": "DeepSeek", "Grok-Fast": "Grok", "Step5": "Step"}
    season = {}
    for row in ep1["prelim"]:
        name = alias.get(row["name"], row["name"])
        season.setdefault(name, {"ep1": 0, "ep2": 0})["ep1"] = points(row["rank"])
    for row in ep2["overall"]:
        name = alias.get(row, row)
        season.setdefault(name, {"ep1": 0, "ep2": 0})["ep2"] = points(ep2["overall"].index(row) + 1)
    season_rows = sorted(
        season.items(), key=lambda kv: (-(kv[1]["ep1"] + kv[1]["ep2"]), kv[0])
    )
    srows = [
        [i, name, v["ep1"] or "-", v["ep2"] or "-", v["ep1"] + v["ep2"]]
        for i, (name, v) in enumerate(season_rows, 1)
    ]

    home = f"""
<section class="hero">
  <h1>AI 运动会</h1>
  <p class="lead">让一群大模型拿<b>同一份任务书</b>，在同样的限时里各写一个 bot，去操纵同一个火柴人、同一个球、同一套物理。这里是全部比赛材料：任务书、裁判引擎、原样代码、逐项成绩、回放。</p>
  <p class="actions"><a class="btn" href="ep1/">看第 1 期</a><a class="btn" href="ep2/">看第 2 期</a><a class="btn" href="ep3/">看第 3 期</a><a class="btn ghost" href="https://github.com/JonyJay1326/running-arena" target="_blank" rel="noopener">下载全部材料</a></p>
</section>

<section class="cards">
  <a class="card" href="ep1/"><span class="tag">第 1 期</span><h2>百米障碍跑</h2><p>16 位模型，18 米障碍赛道，30 秒一场；前 4 名进决赛。</p><p class="win">冠军 GPT-6 Astra · 11.37s</p></a>
  <a class="card" href="ep2/"><span class="tag">第 2 期</span><h2>跳远</h2><p>16 位模型，选技能包、三跳取最好，前 8 进决赛重新计分。</p><p class="win">冠军 GPT-6.1 Sol · 6.157m</p></a>
  <a class="card" href="ep3/"><span class="tag">第 3 期</span><h2>弹弹高尔夫</h2><p>18 位选手写回合制炮战 bot，两轮 9 洞比总杆。</p><p class="win">冠军 Sol · 56 杆</p></a>
</section>

<h2 class="sec">赛季积分</h2>
<p class="dim">第 1、2 期前 10 名依次 15 / 12 / 10 / 8 / 6 / 5 / 4 / 3 / 2 / 1 分；第 3 期是独立项目（选手构成不同），暂不计入赛季积分。</p>
{table(["#", "选手", "第 1 期", "第 2 期", "合计"], srows, 2)}
"""
    write("index.html", page(f"AI 运动会 · {DOMAIN}", home, "home"))

    # ---- 第 1 期 ----
    e1rows = [
        [r["rank"], r["name"], f'{r["finish_s"]:.2f}s' if r["finish_s"] else "-",
         f'{r["score_m"]:.3f} m', f'{r["model"]}' + (f'（{r["tier"]}）' if r.get("tier") else "")]
        for r in ep1["prelim"]
    ]
    e1final = [
        [r["rank"], r["name"], f'{r["finish_s"]:.2f}s' if r["finish_s"] else "-", f'{r["score_m"]:.3f} m']
        for r in ep1["final"]
    ]
    e1extra = [
        [r["name"], f'{r["finish_s"]:.2f}s' if r.get("finish_s") else "-", f'{r["score_m"]:.3f} m']
        for r in ep1_extra["results"]
    ]
    ep1_body = f"""
<h1>第 1 期 · 百米障碍跑</h1>
<p class="lead">同一份任务书，45 分钟，各写一个 <code>act(obs)</code> 控制 2D 火柴人跑完 18 米障碍赛道。30 秒一场，固定随机种子，成绩可被任何人重算。</p>
<p class="actions"><a class="btn" href="replay/">看回放</a><a class="btn ghost" href="https://github.com/JonyJay1326/running-arena/tree/main/ep1-obstacle">代码与成绩</a></p>

<h2 class="sec">初赛 · 16 人</h2>
<p class="dim">冲线者按用时排前，未冲线者按计分距离排后。</p>
{table(["#", "选手", "用时", "成绩", "模型"], e1rows, 2)}

<h2 class="sec">决赛 · 前 4</h2>
{table(["#", "选手", "用时", "成绩"], e1final, 2)}

<h2 class="sec">榜外补测</h2>
<p class="dim">条件相同，但在正式赛之后补跑、模型版本更新，不计名次。</p>
{table(["选手", "用时", "成绩"], e1extra, 1)}

<h2 class="sec">自己跑</h2>
<pre><code>git clone https://github.com/JonyJay1326/running-arena
cd running-arena/ep1-obstacle
pip install pymunk
python verify.py              # 重跑 22 份 bot，对榜
python challenge.py my_bot.py # 拿你的 bot 挑战</code></pre>
"""
    write("ep1/index.html", page("第 1 期 · 百米障碍跑", ep1_body, "ep1", "../"))

    # ---- 第 2 期 ----
    def jump_table(rows):
        out = []
        for r in rows:
            s = {a["attempt"]: a for a in r["attempts"]}
            cells = []
            for i in (1, 2, 3):
                a = s.get(i)
                cells.append("犯规" if a and a.get("foul") else (f'{a["score"]:.3f}' if a else "-"))
            model = r.get("model") or "-"
            if r.get("effort"):
                model += f'（{r["effort"]}）'
            out.append([r["rank"], r["name"], model, *cells, f'**{r["best"]:.3f}**'])
        return table(["#", "选手", "模型", "第 1 跳", "第 2 跳", "第 3 跳", "最好"], out, 3)

    ep2_body = f"""
<h1>第 2 期 · 跳远</h1>
<p class="lead">从 2～9 米自选起点，越过 12 米起跳线落进沙坑。三跳取最好，前 8 进决赛重新计分；每跳的风种子公开，可以逐跳重放。</p>
<p class="actions"><a class="btn" href="replay/">看回放</a><a class="btn ghost" href="https://github.com/JonyJay1326/running-arena/tree/main/ep2-longjump">代码与成绩</a></p>

<h2 class="sec">资格赛 · 16 人 × 3 跳</h2>
{jump_table(ep2["qualification"]["results"])}

<h2 class="sec">决赛 · 8 人 × 3 跳</h2>
{jump_table(ep2["final"]["results"])}

<h2 class="sec">榜外补测</h2>
{table(["选手", "最好"], [[r["name"], f'{r["best"]:.3f} m'] for r in ep2_extra["results"]], 1)}
"""
    write("ep2/index.html", page("第 2 期 · 跳远", ep2_body, "ep2", "../"))

    # ---- 第 3 期 ----
    standings = [
        [r["rank"], r["display"], r.get("model") or "-", r["rounds"].get("r1", {}).get("total", "-"),
         r["rounds"].get("r2", {}).get("total", "-"), r["total"], r.get("completed", "-")]
        for r in ep3["standings"]
    ]
    ep3_body = f"""
<h1>第 3 期 · 弹弹高尔夫</h1>
<p class="lead">18 位选手写回合制炮战 bot，每回合 1 秒决策。两轮各 9 洞，按总杆排名；完成的洞记实际杆数（最多 par+3），没打进记 par+4。</p>
<p class="actions"><a class="btn" href="replay/r1/">第 1 轮回放</a><a class="btn" href="replay/r2/">第 2 轮回放</a><a class="btn ghost" href="https://github.com/JonyJay1326/running-arena/tree/main/ep3-golf">代码与成绩</a></p>

<h2 class="sec">总榜 · 18 人</h2>
{table(["#", "选手", "模型（记录）", "第 1 轮", "第 2 轮", "总杆", "完成洞"], standings, 3)}

<h2 class="sec">自己验算</h2>
<pre><code>git clone https://github.com/JonyJay1326/running-arena
cd running-arena/ep3-golf
node verify.mjs          # 324 份回放逐事件重算，对总榜</code></pre>
<p class="dim">回放在浏览器里逐回合播放，切换到不同选手即可对比同一洞的不同打法。</p>
"""
    write("ep3/index.html", page("第 3 期 · 弹弹高尔夫", ep3_body, "ep3", "../"))

    total = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(DIST) for f in fs)
    print(f"dist ok: {total / 1e6:.1f} MB")


if __name__ == "__main__":
    main()


