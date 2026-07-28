#!/usr/bin/env python3
"""
生成北单多期汇总看板（HTML）
显示26077/26078/26079三期概览及对比
"""

import re, os, sys
from datetime import datetime

PROJ_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(PROJ_DIR, "docs")
TODAY = datetime.now().strftime("%Y-%m-%d %H:%M")

PERIODS = [
    {"expect": 26077, "period": "7/21 ～ 7/24"},
    {"expect": 26078, "period": "7/24 ～ 7/27"},
    {"expect": 26079, "period": "7/29 ～ 7/31"},
]


def fetch_page(playid, expect):
    from urllib.request import Request, urlopen
    url = f"https://zx.500.com/zqdc/saiguo.php?playid={playid}&expect={expect}"
    req = Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://zx.500.com/zqdc/",
        "Cookie": "sfrom=zqdc",
    })
    with urlopen(req, timeout=15) as resp:
        raw = resp.read()
    try:
        return raw.decode("gb2312")
    except:
        return raw.decode("gbk", errors="replace")


def parse_matches(text):
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', text, re.DOTALL)
    matches = []
    for r in rows:
        cells = re.findall(r'<td[^>]*>(.*?)</td>', r, re.DOTALL)
        if len(cells) < 6:
            continue
        vals = []
        for c in cells:
            clean = re.sub(r'<[^>]+>', ' ', c)
            clean = re.sub(r'&nbsp;|\s+', ' ', clean).strip()
            vals.append(clean)
        first = vals[0].strip()
        if not first.isdigit():
            continue
        matches.append({
            "num": int(first),
            "league": vals[1] if len(vals) > 1 else "",
            "time": vals[2] if len(vals) > 2 else "",
            "home": vals[3] if len(vals) > 3 else "",
            "hdcp": vals[4] if len(vals) > 4 else "",
            "away": vals[5] if len(vals) > 5 else "",
        })
    return matches


def main():
    all_data = {}
    for p in PERIODS:
        expect = p["expect"]
        print(f"🔄 抓取北单{expect}期...")
        try:
            m3 = parse_matches(fetch_page(3, expect))
            # 获取简要统计
            leagues = {}
            for m in m3:
                l = m["league"]
                leagues[l] = leagues.get(l, 0) + 1
            top_leagues = sorted(leagues.items(), key=lambda x: -x[1])[:8]
            all_data[expect] = {
                "count": len(m3),
                "leagues": top_leagues,
                "matches": m3,
                "period": p["period"],
            }
            print(f"   ✓ {len(m3)} 场, {len(leagues)} 联赛")
        except Exception as e:
            print(f"   ✗ {e}")
            all_data[expect] = {"count": 0, "leagues": [], "matches": [], "period": p["period"]}

    # 生成 HTML
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>北单看板 26077-26079期</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family:-apple-system,'Microsoft YaHei',sans-serif; background:#f5f7fa; color:#333; }}
.header {{ background:linear-gradient(135deg,#1a237e,#283593); color:white; padding:24px 20px; text-align:center; }}
.header h1 {{ font-size:22px; margin-bottom:4px; }}
.header p {{ font-size:13px; opacity:.8; }}
.container {{ max-width:1200px; margin:0 auto; padding:16px; }}
.summary-cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:12px; margin-bottom:20px; }}
.card {{ background:white; border-radius:10px; padding:16px; box-shadow:0 2px 8px rgba(0,0,0,.08); }}
.card h2 {{ font-size:16px; color:#1a237e; margin-bottom:8px; }}
.card .stat {{ display:flex; justify-content:space-between; padding:4px 0; font-size:14px; border-bottom:1px solid #eee; }}
.card .stat:last-child {{ border-bottom:none; }}
.card .label {{ color:#666; }}
.card .value {{ font-weight:600; }}
.league-tag {{ display:inline-block; background:#e8eaf6; color:#283593; border-radius:12px; padding:2px 10px; font-size:12px; margin:2px; }}
.table-wrap {{ background:white; border-radius:10px; overflow:hidden; box-shadow:0 2px 8px rgba(0,0,0,.08); margin-bottom:20px; }}
.table-wrap h3 {{ background:#283593; color:white; padding:10px 16px; font-size:14px; }}
table {{ width:100%; border-collapse:collapse; font-size:13px; }}
th {{ background:#e8eaf6; color:#283593; padding:8px 6px; text-align:center; font-weight:600; position:sticky; top:0; }}
td {{ padding:6px; text-align:center; border-bottom:1px solid #f0f0f0; }}
tr:nth-child(even) td {{ background:#fafafa; }}
.league {{ color:#666; font-size:12px; }}
.time {{ color:#888; font-size:12px; font-family:monospace; }}
.hdcp {{ font-weight:600; }}
.hdcp-neg {{ color:#2e7d32; }}
.hdcp-pos {{ color:#c62828; }}
.footer {{ text-align:center; padding:20px; color:#999; font-size:12px; }}
</style>
</head>
<body>
<div class="header">
  <h1>⚽ 北京单场看板</h1>
  <p>第26077期 ～ 第26079期 · 数据更新：{TODAY}</p>
  <p style="margin-top:6px;font-size:12px;opacity:.7">数据来源：500.com 皇冠公司历史相同亚盘</p>
</div>
<div class="container">

<div class="summary-cards">
"""

    for p in PERIODS:
        d = all_data.get(p["expect"], {"count": 0, "leagues": [], "period": p["period"]})
        league_html = "".join(
            f'<span class="league-tag">{l}({c})</span>' for l, c in d["leagues"]
        )
        xlsx = f"https://github.com/bily1258-design/football-odds-api/raw/main/beidan_{p['expect']}_dashboard.xlsx"
        html += f"""
<div class="card">
  <h2>第{p['expect']}期 <span style="font-size:13px;color:#999;font-weight:400">({d['period']})</span></h2>
  <div class="stat"><span class="label">比赛场次</span><span class="value">{d['count']}</span></div>
  <div class="stat"><span class="label">联赛分布</span><span class="value" style="font-size:13px">{len(d['leagues'])}个联赛</span></div>
  <div style="margin-top:8px">{league_html}</div>
  <div style="margin-top:10px;text-align:center">
    <a href="{xlsx}" style="display:inline-block;background:#283593;color:white;text-decoration:none;padding:6px 20px;border-radius:6px;font-size:13px">📥 下载Excel</a>
  </div>
</div>"""

    html += """
</div>
"""

    # Detailed tables
    for p in PERIODS:
        d = all_data.get(p["expect"], {"matches": [], "period": p["period"]})
        if not d["matches"]:
            continue
        rows_html = ""
        for m in d["matches"][:30]:  # 最多30场
            hdcp_class = ""
            try:
                hn = int(m["hdcp"])
                if hn < 0:
                    hdcp_class = ' class="hdcp-neg"'
                elif hn > 0:
                    hdcp_class = ' class="hdcp-pos"'
            except:
                pass
            rows_html += f"""<tr><td>{m['num']}</td><td class="league">{m['league']}</td><td class="time">{m['time']}</td><td>{m['home']}</td><td{hdcp_class}>{m['hdcp']}</td><td>{m['away']}</td></tr>
"""
        more = f'<p style="padding:10px;text-align:center;color:#999;font-size:13px">仅显示前30场，完整{d["count"]}场请下载Excel</p>' if d["count"] > 30 else ""
        html += f"""
<div class="table-wrap">
  <h3>第{p['expect']}期 比赛列表 ({d['count']}场)</h3>
  <div style="overflow-x:auto">
  <table>
    <thead><tr><th>#</th><th>联赛</th><th>时间</th><th>主队</th><th>让球</th><th>客队</th></tr></thead>
    <tbody>{rows_html}</tbody>
  </table>
  </div>
  {more}
</div>"""

    html += """
<div class="footer">
  <p>⚡ 自动生成 · data from 500.com</p>
</div>
</div>
</body>
</html>"""

    output_path = os.path.join(OUTPUT_DIR, "beidan_dashboard.html")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n✅ 汇总看板已生成: {output_path}")
    return output_path


if __name__ == "__main__":
    main()
