#!/usr/bin/env python3
"""fetch_hkjc_all.py — 从titan007获取所有有HKJC赔率的比赛

替代：原500.com版(已废弃)

数据流:
1. 从 titan007 CommonInterface type=2 获取当日所有比赛
2. 逐个检查是否有HKJC赔率(cid=432)
3. 有则获取平博赔率(cid=177)
4. 输出到 data/matches_hkjc_{日期}.json

用法:
  python3 scripts/fetch_hkjc_all.py                                    # 今天
  python3 scripts/fetch_hkjc_all.py --date 2026-07-14
  python3 scripts/fetch_hkjc_all.py --date 2026-07-14 --merge          # 合并到已有数据
"""
import re, json, os, argparse, urllib.request, time, sys
from datetime import datetime, date, timezone, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.path.dirname(SCRIPT_DIR), "data")
DOCS_DATA_DIR = os.path.join(os.path.dirname(SCRIPT_DIR), "docs", "data")

# 导入titan007工具
sys.path.insert(0, SCRIPT_DIR)
from titan007_utils import get_match_list, get_odds_history, fetch_url, translate_team_name, fetch_1x2d_odds

# titan007 cid映射: 177=平博, 432=HKJC
CID_PINNACLE = '177'  # 平博(Pinnacle)
CID_HKJC = '432'      # 香港马会(HKJC)


def fetch_bfdata_cn_map():
    """从bfdata_ut.js获取SID → (中文主队, 中文客队, 比分, match_time)映射
    
    返回: {sid: (h_cn, a_cn, score, match_time)}
    match_time格式: 'YYYY-MM-DD HH:MM'
    """
    try:
        from fetch_zqdc import fetch_bfdata
        bf_matches = fetch_bfdata()
    except Exception as e:
        print(f'[WARN] bfdata抓取失败: {e}')
        return {}
    result = {}
    for m in bf_matches:
        sid = m.get('sid', '')
        h = m.get('hometeam', '')
        a = m.get('awayteam', '')
        f = m.get('fields', [])
        # 从fields提取比分：f[13]=状态(-1/3=完场), f[14]=主队进球, f[15]=客队进球
        score = ''
        if len(f) > 15:
            try:
                status = int(f[13])
                hs = int(f[14])
                aas = int(f[15])
                if status in (-1, 3):
                    score = f"{hs}-{aas}"
            except (ValueError, IndexError):
                pass
        # bfdata中的比赛时间（月份可能错位，6月实际为7月）
        bf_time = m.get('time', '')
        match_time = ''
        if bf_time:
            parts = bf_time.split(',')
            if len(parts) >= 5:
                y, mo, d, hh, mi = parts[0], parts[1], parts[2], parts[3], parts[4]
                raw_mt = f'{y}-{mo.zfill(2)}-{d.zfill(2)} {hh.zfill(2)}:{mi.zfill(2)}'
                # 用修正后的_fix_month修复月份错位（只修月份，保留日和时间）
                from fetch_zqdc import _fix_month
                match_time = _fix_month(raw_mt, date.today().isoformat())
        if sid and h and a:
            result[sid] = (h, a, score, match_time)  # (主队, 客队, 比分, 比赛时间)
    return result


def _get_cn_from_1x2d(sid):
    """从1x2d.js获取中文队名，作为bfdata兜底"""
    url = f'https://1x2d.titan007.com/{sid}.js'
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0', 'Referer': 'https://live.titan007.com/'
    })
    try:
        resp = urllib.request.urlopen(req, timeout=8)
        raw = resp.read()
    except Exception:
        return None
    def decode(match):
        if not match:
            return None
        try:
            return match.group(1).decode('utf-8')
        except UnicodeDecodeError:
            try:
                return match.group(1).decode('gbk')
            except Exception:
                return None
    h_cn = decode(re.search(rb'var hometeam_cn="([^"]*)"', raw))
    a_cn = decode(re.search(rb'var guestteam_cn="([^"]*)"', raw))
    if h_cn and a_cn:
        return (h_cn.strip(), a_cn.strip())
    return None


def fetch_hkjc_matches(date_str, max_matches=0, delay=0.3, workers=3):
    """核心：从titan007获取有HKJC赔率的所有比赛
    
    返回 [{sid, date, match_time, event, home_team, away_team, odds_*}]
    """
    matches = []
    
    # 1. 获取当日所有比赛
    print('[INFO] 从titan007获取比赛...')
    all_matches = get_match_list(date_str)
    print(f'[INFO] 共 {len(all_matches)} 场比赛')
    
    if max_matches > 0:
        all_matches = all_matches[:max_matches]
    
    total = len(all_matches)
    
    # 2. 并行检查HKJC赔率
    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    # 预取bfdata中文队名映射
    bfdata_cn_map = fetch_bfdata_cn_map()
    if bfdata_cn_map:
        print(f'[INFO] bfdata中文队名映射: {len(bfdata_cn_map)} 条')
    
    def check_one(m):
        sid = m['sid']
        # 用1x2d API替代OddsHistory API（后者限频严重且常返回开盘价）
        odds, match_time_1x2d = fetch_1x2d_odds(sid)
        if not odds:
            return None
        hkjc = odds.get(CID_HKJC)
        if not hkjc:
            return None
        
        home_name = m.get('home_team', '')
        away_name = m.get('away_team', '')
        
        # 用bfdata中文名覆盖英文名，并获取实时比分和比赛时间
        score_from_bfdata = ''
        bf_match_date = ''  # bfdata中的实际比赛日期，用于去重
        bf_mt = ''  # bfdata中的比赛时间
        if sid in bfdata_cn_map:
            cn_h, cn_a, sc, bf_mt_tmp = bfdata_cn_map[sid]
            if cn_h and all(ord(c) < 128 for c in home_name):
                home_name = cn_h
            if cn_a and all(ord(c) < 128 for c in away_name):
                away_name = cn_a
            if sc:
                score_from_bfdata = sc
            if bf_mt_tmp:
                bf_match_date = bf_mt_tmp[:10]
                bf_mt = bf_mt_tmp
        
        # 兜底：从1x2d.js提取中文名
        if all(ord(c) < 128 for c in home_name + away_name):
            cn_pair = _get_cn_from_1x2d(sid)
            if cn_pair:
                home_name, away_name = cn_pair
        
        # 日期过滤：允许同一天或±1天（凌晨比赛跨日）
        if bf_match_date and bf_match_date != date_str:
            try:
                bf_dt = datetime.strptime(bf_match_date, '%Y-%m-%d')
                ref_dt = datetime.strptime(date_str, '%Y-%m-%d')
                days_diff = abs((bf_dt - ref_dt).days)
                if days_diff > 1:
                    return None  # 超过1天，废弃（可能月份错位）
            except ValueError:
                return None
        
        # 确定比赛日期和时间 (优先级: 1x2d MatchTime > bfdata > get_match_list > 占位)
        if match_time_1x2d:
            mt_date = match_time_1x2d[:10]
        else:
            mt_date = bf_match_date or date_str
        mt_time = match_time_1x2d or bf_mt or m.get('match_time', '') or f'{date_str} 00:00'
        
        # 有HKJC赔率，构建比赛记录
        match = {
            'fid': sid,  # 用sid代替fid（保持下游兼容）
            'date': mt_date,  # 1x2d MatchTime日期优先
            'match_time': mt_time,  # 1x2d MatchTime时间优先
            'event': m.get('event', m.get('league', '')),
            'home_team': home_name,
            'away_team': away_name,
            'score': score_from_bfdata,
            'status': '',
            'source': 'hkjc',
            'home_rank': 0,
            'away_rank': 0,
            # HKJC赔率（来自1x2d API）
            'odds_hkjc_open_win': hkjc['init_w'],
            'odds_hkjc_open_draw': hkjc['init_d'],
            'odds_hkjc_open_loss': hkjc['init_l'],
            'odds_hkjc_win': hkjc['curr_w'],
            'odds_hkjc_draw': hkjc['curr_d'],
            'odds_hkjc_loss': hkjc['curr_l'],
            'odds_hkjc_changes': 1,
            'odds_hkjc_company': 'HKJC',
        }
        
        # 3. 获取平博赔率（同一次1x2d API调用）
        pinnacle = odds.get(CID_PINNACLE)
        if pinnacle:
            match['odds_pinnacle_open_win'] = pinnacle['init_w']
            match['odds_pinnacle_open_draw'] = pinnacle['init_d']
            match['odds_pinnacle_open_loss'] = pinnacle['init_l']
            match['odds_pinnacle_win'] = pinnacle['curr_w']
            match['odds_pinnacle_draw'] = pinnacle['curr_d']
            match['odds_pinnacle_loss'] = pinnacle['curr_l']
            match['odds_pinnacle_changes'] = 1
            match['odds_pinnacle_company'] = 'Pinnacle'
        
        # 4. 比分兜底：bfdata无分时查分析页 JavaScript 变量
        if not match['score']:
            try:
                analysis_score = get_score_from_titan007(sid)
                if analysis_score:
                    match['score'] = analysis_score
            except Exception:
                pass
        
        # 提取比赛时间（仅当strTime日期与比赛日期一致）
        # 防止世界杯跨轮时引用上一轮时间
        try:
            from titan007_utils import get_analysis_data
            ad = get_analysis_data(sid)
            if ad and ad.get('match_time'):
                mt = ad['match_time'].strip()
                if mt[:10] == match['date']:
                    match['match_time'] = mt
        except Exception:
            pass  # 保持00:00兜底

        return match
    
    hkjc_matches = []
    total_hkjc = 0
    processed = 0
    
    with ThreadPoolExecutor(max_workers=workers) as executor:
        fut_map = {executor.submit(check_one, m): m for m in all_matches}
        for fut in as_completed(fut_map):
            processed += 1
            m = fut_map[fut]
            match = fut.result()
            if match is None:
                print(f'  [{processed}/{total}] sid={m["sid"]} {m.get("home_team","?")} vs {m.get("away_team","?")}... 无HKJC盘口')
                continue
            hkjc_matches.append(match)
            total_hkjc += 1
            hw = match.get('odds_hkjc_open_win', '?')
            hd = match.get('odds_hkjc_open_draw', '?')
            hl = match.get('odds_hkjc_open_loss', '?')
            print(f'  [{processed}/{total}] ✓ {match["event"]} {match["home_team"]} vs {match["away_team"]}  HKJC: {hw}/{hd}/{hl}')
            if delay > 0:
                time.sleep(delay / workers)
    
    print(f'\n[INFO] 有HKJC赔率的比赛: {len(hkjc_matches)}/{total}')
    return hkjc_matches


def get_score_from_titan007(sid):
    """从titan007分析页提取比分"""
    import re
    try:
        url = f'https://zq.titan007.com/Analysis/{sid}.htm'
        html = fetch_url(url, timeout=10)
        home_m = re.search(r'var\s+homeScoreStr\s*=\s*\["(\d+)"\]', html)
        guest_m = re.search(r'var\s+guestScoreStr\s*=\s*\["(\d+)"\]', html)
        if home_m and guest_m:
            return f'{home_m.group(1)}-{guest_m.group(1)}'
        return None
    except Exception as e:
        return None


def get_scores_from_over_page(date_str):
    """从titan007 Over_日期.htm 页面获取比分和推迟信息
    
    返回: (scores, postponed_sids) 元组
        scores: { sid: score_str } 完赛比分
        postponed_sids: set 推迟比赛的SID集合
    """
    import re, urllib.request
    try:
        url = f'https://bf.titan007.com/football/Over_{date_str.replace("-", "")}.htm'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        resp = urllib.request.urlopen(req, timeout=10)
        raw = resp.read()
        html = raw.decode('gb2312', errors='replace')
        
        scores = {}
        postponed_sids = set()
        
        # 1. 完赛比分: <tr ... sId='3038222' ...>
        #    <td class=style1>完</td>
        #    <td align=right>主队名</td>
        #    <td ... onclick='showgoallist(3038222)'><font color=red>X</font>-<font color=blue>Y</font></td>
        for m in re.finditer(
            r"sId='(\d+)'.*?<td\s+class=style1\s+style='cursor:pointer;'\s+onclick='showgoallist\(\1\)'>"
            r"<font\s+color=red>(\d+)</font>-<font\s+color=blue>(\d+)</font></td>",
            html, re.DOTALL):
            sid, hs, gs = m.group(1), m.group(2), m.group(3)
            scores[sid] = f"{hs}-{gs}"
        
        # 2. 推迟比赛: <tr ... sId='...' ...>
        #    <td class=style1>推迟</td>  （无onclick、无比分单元格）
        for m in re.finditer(
            r"sId='(\d+)'.*?<td\s+class=style1[^>]*>(?:推迟|延期|取消)</td>",
            html, re.DOTALL):
            postponed_sids.add(m.group(1))
        
        return scores, postponed_sids
    except Exception as e:
        print(f'[WARN] Over页面抓取失败（SID匹配）: {e}')
        return {}, {}


def do_backfill(fpath, date_str):
    """比分回填：bfdata → Over页 → wanchang兜底"""
    if not os.path.exists(fpath):
        print(f'[WARN] {fpath} 不存在，跳过回填')
        return
    
    with open(fpath, encoding='utf-8') as f:
        raw = f.read()
    try:
        existing = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if m:
            existing = json.loads(m.group())
            print(f'[WARN] {fpath} 文件损坏，已修复读取')
        else:
            print(f'[WARN] {fpath} 文件严重损坏，跳过回填')
            return
    
    existing_matches = {m['fid']: m for m in existing.get('matches', [])}
    
    def _filter_unscored():
        """当天比赛只取2.5小时前的，防止回填进行中的比赛"""
        today_str = date.today().isoformat()
        now = datetime.now()
        result = []
        for fid, m in existing_matches.items():
            if m.get('score') or not m.get('match_time'):
                continue
            if date_str == today_str:
                try:
                    mt = datetime.strptime(m['match_time'][:16], '%Y-%m-%d %H:%M')
                    if (now - mt).total_seconds() < 2.5 * 3600:
                        continue
                except (ValueError, IndexError):
                    pass
            result.append((fid, m))
        return result
    
    # 0. bfdata_ut.js 按SID直接抓比分（最可靠，无需队名匹配）
    unscored = _filter_unscored()
    if unscored:
        try:
            from fetch_zqdc import fetch_bfdata
            bf_matches = fetch_bfdata()
            bf_scores = {}
            for bm in bf_matches:
                sid = bm.get('sid', '')
                f = bm.get('fields', [])
                if len(f) > 15:
                    try:
                        status = int(f[13])
                        if status in (-1, 3):
                            hs = int(f[14])
                            aas = int(f[15])
                            bf_scores[sid] = f"{hs}-{aas}"
                    except (ValueError, IndexError):
                        pass
            bf_ok = 0
            for fid, m in unscored:
                if fid in bf_scores:
                    m['score'] = bf_scores[fid]
                    bf_ok += 1
            if bf_ok:
                existing['matches'] = list(existing_matches.values())
                existing['fetched_at'] = datetime.now().isoformat()
                with open(fpath, 'w', encoding='utf-8') as f:
                    json.dump(existing, f, ensure_ascii=False, indent=2)
                print(f'[BACKFILL] bfdata_ut.js按SID → {bf_ok}/{len(unscored)} 场')
                # 重新计算unscored（已补的跳过后续步骤）
                unscored = _filter_unscored()
                if not unscored:
                    return
        except Exception as e:
            print(f'[BACKFILL] bfdata_ut.js抓取失败: {e}')
    
    # 1. titan007 Over页完整比分表 — 按SID直接匹配（无需队名翻译对齐）
    if unscored:
        scores, postponed_sids = get_scores_from_over_page(date_str)
        if scores:
            over_ok = 0
            for fid, m in unscored:
                if fid in scores:
                    m['score'] = scores[fid]
                    over_ok += 1
            if over_ok:
                existing['matches'] = list(existing_matches.values())
                existing['fetched_at'] = datetime.now().isoformat()
                with open(fpath, 'w', encoding='utf-8') as f:
                    json.dump(existing, f, ensure_ascii=False, indent=2)
                print(f'[BACKFILL] titan007 Over页按SID → {over_ok}/{len(unscored)} 场')
                unscored = _filter_unscored()
        else:
            print(f'[BACKFILL] titan007 Over页 → 页面无完场数据')
        
        # 1b. 从Over页标记推迟的比赛（"推迟"字样的才是真推迟，不瞎猜）
        if postponed_sids:
            postponed = []
            for fid, m in existing_matches.items():
                if not m.get('score') and fid in postponed_sids and not m.get('postponed'):
                    m['postponed'] = True
                    m['score'] = '推迟'
                    postponed.append((fid, m.get('home_team',''), m.get('away_team','')))
            if postponed:
                existing['matches'] = list(existing_matches.values())
                existing['fetched_at'] = datetime.now().isoformat()
                with open(fpath, 'w', encoding='utf-8') as f:
                    json.dump(existing, f, ensure_ascii=False, indent=2)
                print(f'[BACKFILL] Over页标记推迟 → {len(postponed)} 场')
                for fid, h, a in postponed[:5]:
                    print(f'  ✗ {h} vs {a} (fid={fid})')
                if len(postponed) > 5:
                    print(f'  ... 还有 {len(postponed)-5} 场')
                unscored = _filter_unscored()

    # 1.5 按队名从zqdc数据找真实titan007 FID + 比分
    if unscored:
        import glob
        zqdc_map = {}  # {(h_lower, a_lower): (real_fid, score)}
        for zfp in sorted(glob.glob(os.path.join(DATA_DIR, 'matches_*.json'))):
            try:
                with open(zfp) as f:
                    zd = json.load(f)
            except Exception:
                continue
            zlist = zd.get('matches', zd if isinstance(zd, list) else [])
            for zm in zlist:
                zfid = str(zm.get('fid', ''))
                zscore = str(zm.get('score', ''))
                if zfid and zscore:
                    key = (zm.get('home_team', '').strip().lower(),
                           zm.get('away_team', '').strip().lower())
                    if key[0] and key[1]:
                        zqdc_map[key] = (zfid, zscore)
        # 剥(中)后缀匹配
        import re as _re
        name_ok = 0
        for fid, m in unscored:
            h = m.get('home_team', '').strip().lower()
            a = m.get('away_team', '').strip().lower()
            h_clean = _re.sub(r'\s*\(?[中)]?\s*', '', h)
            a_clean = _re.sub(r'\s*\(?[中)]?\s*', '', a)
            for key, (real_fid, score) in zqdc_map.items():
                kh, ka = key
                if (h == kh and a == ka) or (h_clean == kh and a_clean == ka) or \
                   (h in kh and a in ka):
                    m['score'] = score
                    m['fid'] = real_fid
                    name_ok += 1
                    break
        if name_ok:
            existing['matches'] = list(existing_matches.values())
            existing['fetched_at'] = datetime.now().isoformat()
            with open(fpath, 'w', encoding='utf-8') as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
            print(f'[BACKFILL] 按队名配zqdc真实FID → {name_ok}/{len(unscored)} 场')
            unscored = [(m['fid'], m) for m in existing_matches.values()
                        if not m.get('score') and m.get('match_time')]

    # 2. Analysis页按SID抓比分（此时fid已可能是真实SID，universal兜底）
    if unscored:
        print(f'[BACKFILL] Analysis页抓比分: {len(unscored)} 场待补…')
        ana_ok = 0
        for fid, m in unscored:
            try:
                from titan007_utils import fetch_url
                import re
                html = fetch_url(f'https://zq.titan007.com/Analysis/{fid}.htm', timeout=8)
                home_m = re.search(r'var\s+homeScoreStr\s*=\s*\["(\d+)"\]', html)
                guest_m = re.search(r'var\s+guestScoreStr\s*=\s*\["(\d+)"\]', html)
                if home_m and guest_m:
                    s = f'{home_m.group(1)}-{guest_m.group(1)}'
                    if s != '0-0' or '完场' in html or '完赛' in html:
                        m['score'] = s
                        ana_ok += 1
            except Exception:
                pass
        if ana_ok:
            existing['matches'] = list(existing_matches.values())
            existing['fetched_at'] = datetime.now().isoformat()
            with open(fpath, 'w', encoding='utf-8') as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
            print(f'[BACKFILL] Analysis页按SID → {ana_ok}/{len(unscored)} 场')
            unscored = [(fid, m) for fid, m in existing_matches.items()
                        if not m.get('score') and m.get('match_time')]
        else:
            print(f'[BACKFILL] Analysis页→未找到比分')
    
    # 2.5 live.titan007.com/detail 页面抓比分（HKJC类FID也能用）
    if unscored:
        import re as _re2
        detail_ok = 0
        for fid, m in unscored:
            try:
                from titan007_utils import fetch_url
                html = fetch_url(f'https://live.titan007.com/detail/{fid}cn.htm', timeout=8)
                dm = _re2.search(r'比赛结束[！!]?\s*比分[：:]\s*(\d+)-(\d+)', html)
                if dm:
                    m['score'] = f'{dm.group(1)}-{dm.group(2)}'
                    detail_ok += 1
            except Exception:
                pass
        if detail_ok:
            existing['matches'] = list(existing_matches.values())
            existing['fetched_at'] = datetime.now().isoformat()
            with open(fpath, 'w', encoding='utf-8') as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
            print(f'[BACKFILL] live detail页按FID → {detail_ok}/{len(unscored)} 场')
            unscored = [(fid, m) for fid, m in existing_matches.items()
                        if not m.get('score') and m.get('match_time')]
    
    # 5. 剩余无分比赛：不猜推迟，仅留空等待下一次回填
    remaining = [m for m in existing_matches.values()
                 if not m.get('score') and m.get('match_time')]
    if remaining:
        print(f'[BACKFILL] 仍缺分 {len(remaining)} 场（未在Over页标记推迟，待下轮回填）')
        for m in remaining[:3]:
            print(f'  ? {m.get("home_team","")} vs {m.get("away_team","")}')
        if len(remaining) > 3:
            print(f'  ... 还有 {len(remaining)-3} 场')
    else:
        print('[BACKFILL] 所有已过比赛时间的比赛均已回填比分或标记推迟')


def main():
    parser = argparse.ArgumentParser(description='从titan007获取HKJC赔率比赛')
    parser.add_argument('--date', default=date.today().isoformat())
    parser.add_argument('--delay', type=float, default=0.3)
    parser.add_argument('--parallel', type=int, default=3)
    parser.add_argument('--max', type=int, default=0)
    parser.add_argument('--merge', action='store_true')
    parser.add_argument('--save', default='')
    parser.add_argument('--backfill', action='store_true')
    args = parser.parse_args()
    
    date_str = args.date
    basename = args.save or ('matches_hkjc_' + date_str.replace('-', ''))
    fpath = os.path.join(DATA_DIR, f'{basename}.json')
    
    if args.backfill:
        do_backfill(fpath, date_str)
        return
    
    # 正常抓取
    hkjc_matches = fetch_hkjc_matches(date_str, args.max, args.delay, args.parallel)
    
    # 输出
    out = {
        'date': date_str,
        'fetched_at': datetime.now().isoformat(),
        'total': len(hkjc_matches),
        'source': 'titan007',
        'matches': hkjc_matches,
    }
    
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(fpath, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f'[OK] {len(hkjc_matches)} 场 → {fpath}')
    
    # --merge
    if args.merge:
        results_path = os.path.join(DOCS_DATA_DIR, 'results.json')
        if os.path.exists(results_path):
            with open(results_path, 'r', encoding='utf-8') as f:
                raw = f.read()
            try:
                existing = json.loads(raw)
            except json.JSONDecodeError:
                m = re.search(r'\{.*\}', raw, re.DOTALL)
                if m:
                    existing = json.loads(m.group())
                    print('[WARN] results.json 文件损坏，已修复读取')
                else:
                    print('[WARN] results.json 严重损坏，重建')
                    existing = {'matches': []}
            existing_matches = existing.get('matches', [])
        else:
            existing_matches = []
            existing = {'matches': [], 'generated_at': '', 'total_matches': 0, 'date_range': '', 'daily_stats': []}
        
        existing_keys = {(m.get('home_team',''), m.get('away_team',''), m.get('date','')) for m in existing_matches}
        new_count = 0
        for m in hkjc_matches:
            key = (m.get('home_team',''), m.get('away_team',''), m.get('date',''))
            if key not in existing_keys:
                existing_matches.append(m)
                new_count += 1
        
        existing['matches'] = existing_matches
        existing['total_matches'] = len(existing_matches)
        existing['generated_at'] = datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
        
        with open(results_path, 'w', encoding='utf-8') as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
        print(f'[MERGE] 新增 {new_count} 场 → docs/data/results.json (共 {len(existing_matches)} 场)')


if __name__ == '__main__':
    main()
