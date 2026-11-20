"""生成静态站：三期榜单 + 回放入口。

    python site/build.py

输出到 site/dist/，可以直接扔 GitHub Pages / Cloudflare Pages。
不引入任何前端依赖，榜单从各期的 results/*.json 现算，改数据重跑一次即可。
"""
import json
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DIST = os.path.join(HERE, "dist")
DOMAIN = "arena.saiboliang.top"
GH = "https://github.com/JonyJay1326/running-arena"

POINTS = [15, 12, 10, 8, 6, 5, 4, 3, 2, 1]
ALIAS = {"Doubao": "豆包", "DeepSeek-Flash": "DeepSeek", "Grok-Fast": "Grok", "Step5": "Step"}


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


def medal(rank):
    cls = "g1" if rank == 1 else "g2" if rank == 2 else "g3" if rank == 3 else ""
    return f'<span class="medal {cls}">{rank}</span>'


def table(headers, rows, numeric_from=1):
    th = "".join(f'<th class="{"n" if i >= numeric_from else ""}">{h}</th>' for i, h in enumerate(headers))
    trs = []
    for r in rows:
        tds = "".join(
            f'<td class="{"n" if i >= numeric_from else ""}">{c}</td>' for i, c in enumerate(r)
        )
        trs.append(f"<tr>{tds}</tr>")
    return (
        '<div class="tablewrap"><table><thead><tr>'
        + th
        + "</tr></thead><tbody>"
        + "".join(trs)
        + "</tbody></table></div>"
    )


def fmt_time(v):
    return "-" if v is None else f"{v:.2f}s"


def page(title, body, active, base, description):
    nav = "".join(
        f'<a class="{"on" if key == active else ""}" href="{base}{href}">{label}</a>'
        for key, label, href in (
            ("home", "首页", ""),
            ("ep1", "第 1 期", "ep1/"),
            ("ep2", "第 2 期", "ep2/"),
            ("ep3", "第 3 期", "ep3/"),
        )
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<meta name="description" content="{description}">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{description}">
<meta property="og:type" content="website">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='8' fill='%2307090d'/%3E%3Ccircle cx='16' cy='16' r='6' fill='%23f0b429'/%3E%3C/svg%3E">
<link rel="stylesheet" href="{base}style.css">
<body>
<header class="top"><div class="bar">
  <a class="brand" href="{base}"><span class="dot"></span>AI 运动会</a>
  <nav>{nav}<a class="gh" href="{GH}" target="_blank" rel="noopener">GitHub</a></nav>
</div></header>
<main>
{body}
</main>
<footer><div class="inner">
  <span>让各家大模型拿同一份任务书，各写一个 bot，在同一套引擎里比赛。</span>
  <span><a href="{GH}" target="_blank" rel="noopener">源码与材料</a> · 代码 MIT · 角色立绘不随仓库发布</span>
</div></footer>
</body>
</html>
"""


def main():
    # 第 3 期的原始记录是「工具 + 模型 id + 强度」，这里整理成可读的全名；
    # 原始字符串仍保留在 results/standings.json 与 docs/models.md 里。
    EP3_MODEL = {
        "sol": "Codex CLI · GPT-6.2 Sol · High",
        "fable": "Claude Code · Fable 5.2 · High",
        "carbon": "OpenCode · Gemini 4 Carbon · High",
        "hydrogen": "OpenCode · Gemini 4 Hydrogen · High",
        "astra": "Codex · GPT-6 Astra · High",
        "opus": "Claude Code · Opus 5.5 · High",
        "terra": "Codex · GPT-6.1 Terra · High",
        "grok": "Grok CLI · Grok 4.7 Fast · High",
        "sonnet": "Claude Code · Sonnet 5.5 · High",
        "glm": "ZCode · GLM-5.3 · High",
        "qwen": "Qoder CLI · Qwen4-Max-Preview · High",
        "kimi": "OpenCode · Kimi K3.1 Pro · High",
        "minimax": "CodeBuddy · MiniMax-M3.1-Flash · High",
        "hy4": "CodeBuddy · Hy4 · High",
        "doubao": "OpenCode · 豆包 Seed Evolving · High",
        "mimo": "CodeBuddy · MiMo-V2.6-Pro · High",
        "step5": "CodeBuddy · Step-5-Preview · High",
        "deepseek": "DeepSeek Harness · 模型未记录",
    }

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
    cname = os.environ.get("SITE_CNAME")
    if cname:
        with open(os.path.join(DIST, "CNAME"), "w", newline="\n") as f:
            f.write(cname + "\n")

    # 回放：把现成的静态播放器和数据搬进来
    shutil.copytree(os.path.join(REPO, "ep1-obstacle", "replays"), os.path.join(DIST, "ep1", "replay"))
    shutil.copytree(os.path.join(REPO, "ep2-longjump", "replays"), os.path.join(DIST, "ep2", "replay"))
    shutil.copytree(os.path.join(REPO, "ep3-golf", "viewer"), os.path.join(DIST, "ep3", "replay"))
    shutil.copytree(os.path.join(REPO, "ep3-golf", "replays"), os.path.join(DIST, "ep3", "replays"))
    for ep in ("ep1", "ep2"):
        src = os.path.join(DIST, ep, "replay", "viewer.html")
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(DIST, ep, "replay", "index.html"))

    # ---- 赛季积分 ----
    season = {}
    for row in ep1["prelim"]:
        name = ALIAS.get(row["name"], row["name"])
        season.setdefault(name, {"ep1": 0, "ep2": 0})["ep1"] = points(row["rank"])
    for row in ep2["overall"]:
        name = ALIAS.get(row, row)
        season.setdefault(name, {"ep1": 0, "ep2": 0})["ep2"] = points(ep2["overall"].index(row) + 1)
    season_rows = sorted(season.items(), key=lambda kv: (-(kv[1]["ep1"] + kv[1]["ep2"]), kv[0]))

    board_items = "".join(
        f'<li><span class="rk">{i:02d}</span><span>{name}</span>'
        f'<span class="brk">{v["ep1"] or "–"} / {v["ep2"] or "–"}</span>'
        f'<span class="pts">{v["ep1"] + v["ep2"]}</span></li>'
        for i, (name, v) in enumerate(season_rows[:5], 1)
    )
    srows = [
        [medal(i), f'<span class="who-cell">{name}</span>', v["ep1"] or "–", v["ep2"] or "–",
         f'<b>{v["ep1"] + v["ep2"]}</b>']
        for i, (name, v) in enumerate(season_rows, 1)
    ]

    home = f"""
<section class="hero">
  <div class="copy">
    <div class="eyebrow rise">AI 运动会 · 第 1–3 期</div>
    <h1>同一份任务书，<br><span>看模型自己上场。</span></h1>
    <p class="lead rise">限时、同一套物理、同一批裁判。任务书、选手代码、逐项成绩与回放全部公开，成绩可以逐行重算。</p>
    <div class="cta rise">
      <a class="btn primary" href="ep1/">从第 1 期开始</a>
      <a class="btn" href="{GH}" target="_blank" rel="noopener">下载全部材料</a>
    </div>
  </div>
  <aside class="board">
    <div class="head"><span>赛季积分</span><b>TOP 5</b></div>
    <ol>{board_items}</ol>
    <p class="small muted" style="padding:12px 0 14px">第 1、2 期合计；第 3 期为独立项目。</p>
  </aside>
</section>

<div class="stats">
  <div><b>3</b><span>期比赛</span></div>
  <div><b>50</b><span>位参赛选手</span></div>
  <div><b>424</b><span>项可重算校验</span></div>
  <div><b>0</b><span>需要后端</span></div>
</div>

<section>
  <div class="eyebrow">赛程</div>
  <h2 style="margin:14px 0 26px">三期，三种玩法</h2>
  <div class="episodes">
    <a class="ep rise" href="ep1/">
      <span class="no">EP.01</span>
      <div>
        <h3>百米障碍跑</h3>
        <p>16 位模型，18 米障碍赛道，30 秒一场。同一份物理，失误会直接扣掉距离。</p>
      </div>
      <div class="who"><b>Astra</b><span>决赛 11.37s</span></div>
      <span class="arrow">→</span>
    </a>
    <a class="ep rise" href="ep2/">
      <span class="no">EP.02</span>
      <div>
        <h3>跳远</h3>
        <p>16 位模型，选技能包、三跳取最好；风逐跳随机，种子随成绩公开。</p>
      </div>
      <div class="who"><b>Sol</b><span>决赛 6.157m</span></div>
      <span class="arrow">→</span>
    </a>
    <a class="ep rise" href="ep3/">
      <span class="no">EP.03</span>
      <div>
        <h3>弹弹高尔夫</h3>
        <p>18 位选手写回合制炮战 bot，两轮 9 洞比总杆，每回合只有 1 秒决策。</p>
      </div>
      <div class="who"><b>Sol</b><span>56 杆</span></div>
      <span class="arrow">→</span>
    </a>
  </div>
</section>

<section>
  <div class="eyebrow">赛季积分</div>
  <h2 style="margin:14px 0 10px">第 1 期 + 第 2 期</h2>
  <p class="muted small" style="margin-bottom:22px">前 10 名依次 15 / 12 / 10 / 8 / 6 / 5 / 4 / 3 / 2 / 1 分。第 3 期选手构成不同，暂不计入。</p>
  {table(["#", "选手", "第 1 期", "第 2 期", "合计"], srows, 2)}
</section>
"""
    write("index.html", page(f"AI 运动会 · {DOMAIN}", home, "home", "",
                             "三期 AI 编程对抗赛：同一份任务书、同一套引擎，榜单与回放全部公开、可重算。"))

    # ---- 第 1 期 ----
    e1rows = [
        [medal(r["rank"]), f'<span class="who-cell">{r["name"]}</span>',
         fmt_time(r["finish_s"]), f'{r["score_m"]:.3f} m',
         f'{r["model"]}' + (f' <span class="sub">{r["tier"]}</span>' if r.get("tier") else "")]
        for r in ep1["prelim"]
    ]
    e1final = [
        [medal(r["rank"]), f'<span class="who-cell">{r["name"]}</span>',
         fmt_time(r["finish_s"]), f'{r["score_m"]:.3f} m']
        for r in ep1["final"]
    ]
    e1extra = [
        [f'<span class="who-cell">{r["name"]}</span>', fmt_time(r["finish_s"]), f'{r["score_m"]:.3f} m']
        for r in ep1_extra["results"]
    ]
    top3_1 = ep1["final"][:3]
    podium1 = "".join(
        f'<div><div class="k">{"冠军" if i == 1 else "亚军" if i == 2 else "季军"}</div>'
        f'<div class="v">{r["name"]}</div><div class="s">{fmt_time(r["finish_s"])}</div></div>'
        for i, r in enumerate(top3_1, 1)
    )
    ep1_body = f"""
<section class="ep-hero">
  <div>
    <div class="no">EPISODE 01 · 障碍跑</div>
    <h1>百米障碍跑</h1>
    <p class="lead">18 米赛道：减速带、上下坡、三组栏架。45 分钟写一个控制程序，30 秒定输赢；冲线优先，倒地不计分。</p>
    <div class="cta" style="margin-top:24px">
      <a class="btn primary" href="replay/">看回放</a>
      <a class="btn" href="{GH}/tree/main/ep1-obstacle" target="_blank" rel="noopener">代码与成绩</a>
    </div>
  </div>
  <div class="meta">
    <div><span>参赛</span><b>16 人 + 2 位榜外</b></div>
    <div><span>赛制</span><b>初赛 → 决赛前 4</b></div>
    <div><span>校验</span><b>22 份 bot 重跑</b></div>
    <div><span>引擎</span><b>Python · pymunk</b></div>
  </div>
</section>

<section>
  <div class="eyebrow">决赛</div>
  <h2 style="margin:14px 0 22px">前 4 名，两个 20 分钟阶段</h2>
  <div class="podium">{podium1}</div>
</section>

<section>
  <div class="eyebrow">初赛</div>
  <h2 style="margin:14px 0 10px">16 人总榜</h2>
  <p class="muted small" style="margin-bottom:22px">冲线者按用时排前，未冲线者按计分距离排后。距终点越近不等于名次越高——先冲线才算赢。</p>
  {table(["#", "选手", "用时", "计分距离", "模型"], e1rows, 2)}
</section>

<section class="split">
  <div>
    <div class="eyebrow">决赛成绩</div>
    <h2 style="margin:14px 0 22px">只看第二阶段冻结的代码</h2>
    {table(["#", "选手", "用时", "计分距离"], e1final, 2)}
  </div>
  <div>
    <div class="eyebrow">榜外补测</div>
    <h2 style="margin:14px 0 22px">同条件，不计名次</h2>
    {table(["选手", "用时", "计分距离"], e1extra, 1)}
    <p class="note" style="margin-top:18px">正式赛之后补跑、模型版本更新，所以只作对照，不进榜单。</p>
  </div>
</section>

<section>
  <div class="eyebrow">复现</div>
  <h2 style="margin:14px 0 18px">自己跑一遍</h2>
  <pre><span class="c"># 22 份 bot 全部重跑，和榜单逐项比对</span>
git clone {GH}
cd running-arena/ep1-obstacle
pip install -r ../requirements.txt
python verify.py

<span class="c"># 拿你的 bot 挑战这 16 个人</span>
python challenge.py my_bot.py</pre>
</section>
"""
    write("ep1/index.html", page("第 1 期 · 百米障碍跑 · AI 运动会", ep1_body, "ep1", "../",
                                 "第 1 期障碍跑：16 位模型的跑步 bot，18 米赛道，成绩可重跑验证。"))

    # ---- 第 2 期 ----
    def jump_rows(rows):
        out = []
        for r in rows:
            s = {a["attempt"]: a for a in r["attempts"]}
            cells = []
            for i in (1, 2, 3):
                a = s.get(i)
                cells.append("犯规" if a and a.get("foul") else (f'{a["score"]:.3f}' if a else "-"))
            model = r.get("model") or "-"
            if r.get("effort"):
                model += f' <span class="sub">{r["effort"]}</span>'
            out.append([medal(r["rank"]), f'<span class="who-cell">{r["name"]}</span>', model,
                        *cells, f'<b>{r["best"]:.3f}</b>'])
        return table(["#", "选手", "模型", "第 1 跳", "第 2 跳", "第 3 跳", "最好"], out, 3)

    top3_2 = ep2["final"]["results"][:3]
    podium2 = "".join(
        f'<div><div class="k">{"冠军" if i == 1 else "亚军" if i == 2 else "季军"}</div>'
        f'<div class="v">{r["name"]}</div><div class="s">{r["best"]:.3f} m</div></div>'
        for i, r in enumerate(top3_2, 1)
    )
    ep2_body = f"""
<section class="ep-hero">
  <div>
    <div class="no">EPISODE 02 · 跳远</div>
    <h1>跳远</h1>
    <p class="lead">起点 2～9 米自选，越过 12 米起跳线落进沙坑。三跳取最好，前 8 进决赛重新计分；风随机，种子公开。</p>
    <div class="cta" style="margin-top:24px">
      <a class="btn primary" href="replay/">看回放</a>
      <a class="btn" href="{GH}/tree/main/ep2-longjump" target="_blank" rel="noopener">代码与成绩</a>
    </div>
  </div>
  <div class="meta">
    <div><span>参赛</span><b>16 人 + 2 位榜外</b></div>
    <div><span>赛制</span><b>3 跳 → 前 8 决赛</b></div>
    <div><span>校验</span><b>78 跳逐跳重放</b></div>
    <div><span>引擎</span><b>Python · pymunk</b></div>
  </div>
</section>

<section>
  <div class="eyebrow">决赛</div>
  <h2 style="margin:14px 0 22px">前 3 名</h2>
  <div class="podium">{podium2}</div>
</section>

<section>
  <div class="eyebrow">资格赛</div>
  <h2 style="margin:14px 0 10px">16 人 × 3 跳</h2>
  <p class="muted small" style="margin-bottom:22px">每人三跳取最好有效成绩；同分先比第二好、再比第三好。风逐跳随机，但种子随成绩记录，可以逐跳重放。</p>
  {jump_rows(ep2["qualification"]["results"])}
</section>

<section>
  <h2 style="margin-bottom:22px">决赛 · 8 人 × 3 跳</h2>
  {jump_rows(ep2["final"]["results"])}
</section>

<section class="split">
  <div>
    <div class="eyebrow">榜外补测</div>
    <h2 style="margin:14px 0 18px">同条件，不计名次</h2>
    {table(["选手", "最好成绩"], [[f'<span class="who-cell">{r["name"]}</span>', f'{r["best"]:.3f} m'] for r in ep2_extra["results"]], 1)}
    <p class="note" style="margin-top:18px">正式赛之后补跑、模型版本更新，只作对照。</p>
  </div>
  <div>
    <div class="eyebrow">复现</div>
    <h2 style="margin:14px 0 18px">自己跑一遍</h2>
    <pre><span class="c"># 78 跳逐跳重放</span>
cd running-arena/ep2-longjump
python verify.py

<span class="c"># 三跳，出最好成绩与名次</span>
python challenge.py my_bot.py</pre>
  </div>
</section>
"""
    write("ep2/index.html", page("第 2 期 · 跳远 · AI 运动会", ep2_body, "ep2", "../",
                                 "第 2 期跳远：16 位模型三跳取最好，风种子公开，可逐跳重放。"))

    # ---- 第 3 期 ----
    standings = [
        [medal(r["rank"]), f'<span class="who-cell">{r["display"]}</span>',
         f'<span class="sub">{EP3_MODEL.get(r["name"]) or r.get("model") or "未记录"}</span>',
         r["rounds"].get("r1", {}).get("total", "–"),
         r["rounds"].get("r2", {}).get("total", "–"),
         f'<b>{r["total"]}</b>', r.get("completed", "–")]
        for r in ep3["standings"]
    ]
    top3_3 = ep3["standings"][:3]
    podium3 = "".join(
        f'<div><div class="k">{"冠军" if i == 1 else "亚军" if i == 2 else "季军"}</div>'
        f'<div class="v">{r["display"]}</div><div class="s">{r["total"]} 杆</div></div>'
        for i, r in enumerate(top3_3, 1)
    )
    ep3_body = f"""
<section class="ep-hero">
  <div>
    <div class="no">EPISODE 03 · 弹弹高尔夫</div>
    <h1>弹弹高尔夫</h1>
    <p class="lead">18 位选手写回合制炮战 bot：读战场、算弹道、选武器。两轮各 9 洞，按总杆排名，每回合 1 秒决策。</p>
    <div class="cta" style="margin-top:24px">
      <a class="btn primary" href="replay/r1/">第 1 轮回放</a>
      <a class="btn" href="replay/r2/">第 2 轮回放</a>
      <a class="btn" href="{GH}/tree/main/ep3-golf" target="_blank" rel="noopener">代码与成绩</a>
    </div>
  </div>
  <div class="meta">
    <div><span>参赛</span><b>18 位</b></div>
    <div><span>赛制</span><b>两轮 × 9 洞</b></div>
    <div><span>校验</span><b>324 份回放</b></div>
    <div><span>引擎</span><b>Node.js · 纯 JS</b></div>
  </div>
</section>

<section>
  <div class="eyebrow">总榜</div>
  <h2 style="margin:14px 0 22px">前三名</h2>
  <div class="podium">{podium3}</div>
</section>

<section>
  <h2 style="margin-bottom:10px">18 人总杆</h2>
  <p class="muted small" style="margin-bottom:22px">完成的洞记实际杆数（最多 par+3），没打进记 par+4；同杆比完成洞数。点上面的回放入口可以逐回合看。</p>
  {table(["#", "选手", "模型", "第 1 轮", "第 2 轮", "总杆", "完成洞"], standings, 3)}
</section>

<section>
  <div class="eyebrow">复现</div>
  <h2 style="margin:14px 0 18px">324 份回放逐事件重算</h2>
  <pre><span class="c"># 引擎重放每一份回放，比对逐洞杆数与总榜</span>
git clone {GH}
cd running-arena/ep3-golf
node verify.mjs

<span class="c"># 只查一个人一轮</span>
node verify.mjs --player sol --round 1</pre>
</section>
"""
    write("ep3/index.html", page("第 3 期 · 弹弹高尔夫 · AI 运动会", ep3_body, "ep3", "../",
                                 "第 3 期弹弹高尔夫：18 位选手两轮 9 洞，324 份回放可在浏览器逐回合观看。"))

    total = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(DIST) for f in fs)
    print(f"dist ok: {total / 1e6:.1f} MB")


if __name__ == "__main__":
    main()


