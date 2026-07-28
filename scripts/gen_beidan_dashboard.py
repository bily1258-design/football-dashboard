#!/usr/bin/env python3
"""生成北单多期汇总看板（HTML），含皇冠历史相同亚盘概率"""
import re, os, sys
from datetime import datetime
from urllib.request import Request, urlopen

PROJ_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR = os.path.join(PROJ_DIR, "docs")
TODAY = datetime.now().strftime("%Y-%m-%d %H:%M")

PERIODS = [
    {"expect": 26077, "period": "7/21 ～ 7/24"},
    {"expect": 26078, "period": "7/24 ～ 7/27"},
    {"expect": 26079, "period": "7/29 ～ 7/31"},
]


def fetch_page(playid, expect):
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


def parse_matches_p3(text):
    """playid=3: 让球胜平负. prob cols: [9]胜% [11]平% [13]负%"""
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', text, re.DOTALL)
    matches = {}
    for r in rows:
        cells = re.findall(r'<td[^>]*>(.*?)</td>', r, re.DOTALL)
        if len(cells) < 14:
            continue
        vals = []
        for c in cells:
            clean = re.sub(r'<[^>]+>', ' ', c)
            clean = re.sub(r'&nbsp;|\s+', ' ', clean).strip()
            vals.append(clean)
        first = vals[0].strip()
        if not first.isdigit():
            continue
        num = int(first)
        probs = [v.rstrip("%") for v in [vals[9], vals[11], vals[13]] if "%" in v]
        matches[num] = {
            "num": num, "league": vals[1], "time": vals[2],
            "home": vals[3], "handicap": vals[4], "away": vals[5],
            "score": vals[6], "result": vals[7],
            "p3_h": int(probs[0]) if len(probs) > 0 else -1,
            "p3_d": int(probs[1]) if len(probs) > 1 else -1,
            "p3_a": int(probs[2]) if len(probs) > 2 else -1,
        }
    return matches


def parse_matches_p0(text):
    """playid=0: 胜负过关. prob cols: [10]胜% [12]平% [14]负%, [8]亚盘"""
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', text, re.DOTALL)
    matches = {}
    for r in rows:
        cells = re.findall(r'<td[^>]*>(.*?)</td>', r, re.DOTALL)
        if len(cells) < 15:
            continue
        vals = []
        for c in cells:
            clean = re.sub(r'<[^>]+>', ' ', c)
            clean = re.sub(r'&nbsp;|\s+', ' ', clean).strip()
            vals.append(clean)
        first = vals[0].strip()
        if not first.isdigit():
            continue
        num = int(first)
        probs = [v.rstrip("%") for v in [vals[10], vals[12], vals[14]] if "%" in v]
        matches[num] = {
            "num": num, "ah_desc": vals[8],
            "h_prob": int(probs[0]) if len(probs) > 0 else -1,
            "d_prob": int(probs[1]) if len(probs) > 1 else -1,
            "a_prob": int(probs[2]) if len(probs) > 2 else -1,
        }
    return matches


def merge_matches(m3, m0):
    """Merge by match number, using playid=3 as base + probabilities from both pages"""
    merged = []
    for num in sorted(m3):
        b = m3[num]
        o = m0.get(num, {})
        merged.append({
            "num": b["num"], "league": b["league"], "time": b["time"],
            "home": b["home"], "handicap": b["handicap"], "away": b["away"],
            "score": b.get("score", "-"), "result": b.get("result", ""),
            "ah_desc": o.get("ah_desc", ""),
            # 亚盘历史概率 (playid=0 胜负过关)
            "h_prob": o.get("h_prob", -1),
            "d_prob": o.get("d_prob", -1),
            "a_prob": o.get("a_prob", -1),
            # 单场历史赛果概率 (playid=3 让球胜平负)
            "p3_h": b.get("p3_h", -1),
            "p3_d": b.get("p3_d", -1),
            "p3_a": b.get("p3_a", -1),
        })
    return merged


def prob_bar(p):
    """Generate inline CSS bar for probability"""
    p = max(0, int(p))
    color = "#c62828" if p >= 40 else "#e65100" if p >= 30 else "#1565c0"
    return f'<div style="background:#e0e0e0;border-radius:8px;height:14px;width:50px;display:inline-block;vertical-align:middle"><div style="background:{color};width:{p}%;height:14px;border-radius:8px;font-size:9px;line-height:14px;color:#fff;text-align:center">{p}%</div></div>'


def main():
    all_data = {}
    for p in PERIODS:
        expect = p["expect"]
        print(f"🔄 抓取北单{expect}期...")
        try:
            m3 = parse_matches_p3(fetch_page(3, expect))
            m0 = parse_matches_p0(fetch_page(0, expect))
            merged = merge_matches(m3, m0)
            leagues = {}
            for m in merged:
                l = m["league"]
                leagues[l] = leagues.get(l, 0) + 1
            top_leagues = sorted(leagues.items(), key=lambda x: -x[1])[:8]
            all_data[expect] = {
                "count": len(merged),
                "leagues": top_leagues,
                "matches": merged,
                "period": p["period"],
            }
            print(f"   ✓ {len(merged)} 场, {len(leagues)} 联赛, 含概率数据 ✓")
        except Exception as e:
            print(f"   ✗ {e}")
            import traceback
            traceback.print_exc()
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
.container {{ max-width:1400px; margin:0 auto; padding:16px; }}
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
table {{ width:100%; border-collapse:collapse; font-size:12px; }}
th {{ background:#e8eaf6; color:#283593; padding:6px 4px; text-align:center; font-weight:600; position:sticky; top:0; white-space:nowrap; }}
td {{ padding:5px 4px; text-align:center; border-bottom:1px solid #f0f0f0; }}
tr:nth-child(even) td {{ background:#fafafa; }}
.league {{ color:#666; font-size:11px; }}
.time {{ color:#888; font-size:11px; font-family:monospace; }}
.hdcp {{ font-weight:600; }}
.hdcp-neg {{ color:#2e7d32; }}
.hdcp-pos {{ color:#c62828; }}
.prob-h {{ color:#c62828; font-weight:600; }}
.prob-d {{ color:#e65100; font-weight:600; }}
.prob-a {{ color:#1565c0; font-weight:600; }}
.prob-bar {{ display:inline-block; width:60px; height:14px; background:#eee; border-radius:7px; vertical-align:middle; position:relative; overflow:hidden; }}
.prob-fill {{ height:100%; border-radius:7px; line-height:14px; font-size:9px; color:#fff; text-align:center; }}
.ah-desc {{ color:#555; font-size:11px; }}
.score {{ font-family:monospace; font-weight:600; color:#333; font-size:13px; }}
.result {{ font-weight:600; color:#1565c0; }}
.sub-hdr {{ font-weight:400; font-size:10px; color:#78909c; }}
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
        for m in d["matches"][:40]:  # 最多40场（更多列，少些行）
            hdcp_class = ""
            try:
                hn = int(m["handicap"])
                if hn < 0:
                    hdcp_class = ' class="hdcp-neg"'
                elif hn > 0:
                    hdcp_class = ' class="hdcp-pos"'
            except:
                pass

            def bar(val, high_color="#c62828", mid_color="#e65100"):
                if val < 0:
                    return '-'
                c = high_color if val >= 40 else mid_color if val >= 30 else "#1565c0" if val >= 20 else "#999"
                return f'<div class="prob-bar"><div class="prob-fill" style="width:{val}%;background:{c}">{val}%</div></div>'

            # 亚盘历史概率 (playid=0)
            bar_ah_h = bar(m["h_prob"])
            bar_ah_d = bar(m["d_prob"])
            bar_ah_a = bar(m["a_prob"], mid_color="#c62828")

            # 单场历史赛果概率 (playid=3)
            bar_p3_h = bar(m["p3_h"])
            bar_p3_d = bar(m["p3_d"])
            bar_p3_a = bar(m["p3_a"], mid_color="#c62828")

            rows_html += f"""<tr>
  <td>{m['num']}</td>
  <td class="league">{m['league']}</td>
  <td class="time">{m['time']}</td>
  <td>{m['home']}</td>
  <td{hdcp_class}>{m['handicap']}</td>
  <td>{m['away']}</td>
  <td class="score">{m['score']}</td>
  <td class="result">{m['result']}</td>
  <td class="ah-desc">{m['ah_desc']}</td>
  <td>{bar_ah_h}</td>
  <td>{bar_ah_d}</td>
  <td>{bar_ah_a}</td>
  <td>{bar_p3_h}</td>
  <td>{bar_p3_d}</td>
  <td>{bar_p3_a}</td>
</tr>
"""
        show = min(40, d["count"])
        more = f'<p style="padding:10px;text-align:center;color:#999;font-size:13px">仅显示前{show}场，完整{d["count"]}场请下载Excel</p>' if d["count"] > 50 else ""
        html += f"""
<div class="table-wrap">
  <h3>第{p['expect']}期 比赛列表 ({d['count']}场) · 皇冠历史相同亚盘概率 + 赛果出现概率</h3>
  <div style="overflow-x:auto">
  <table>
    <thead><tr>
      <th>#</th><th>联赛</th><th>时间</th><th>主队</th><th>让球</th><th>客队</th>
      <th>比分</th><th>赛果</th>
      <th>亚盘</th><th>主胜%<br><span class="sub-hdr">亚盘</span></th><th>平%<br><span class="sub-hdr">亚盘</span></th><th>客负%<br><span class="sub-hdr">亚盘</span></th>
      <th>胜/3<br><span class="sub-hdr">赛果</span></th><th>平/1<br><span class="sub-hdr">赛果</span></th><th>负/0<br><span class="sub-hdr">赛果</span></th>
    </tr></thead>
    <tbody>{rows_html}</tbody>
  </table>
  </div>
  {more}
</div>"""

    html += """
<div class="footer">
  <p>⚡ 自动生成 · 概率数据来自500.com 皇冠历史相同亚盘</p>
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
