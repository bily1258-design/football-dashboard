#!/usr/bin/env python3
"""fetch_hkjc_all.py — 从titan007获取所有有HKJC赔率的比赛

替代：原500.com版(已废弃)

数据流:
1. 从 titan007 CommonInterface type=2 获取当日所有比赛
2. 逐个检查是否有HKJC赔率(cid=177)
3. 有则获取平博赔率(cid=432)
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
from titan007_utils import get_match_list, get_odds_history, fetch_url, translate_team_name

# titan007 cid映射: 177=平博, 432=HKJC
CID_PINNACLE = 177
CID_HKJC = 432


def fetch_bfdata_cn_map():
    """从bfdata_ut.js获取SID → (中文主队, 中文客队)映射，复用fetch_zqdc的解析"""
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
        if sid and h and a:
            result[sid] = (h, a)
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
        hkjc = get_odds_history(sid, CID_HKJC)
        if not hkjc:
            return None
        
        home_name = m.get('home_team', '')
        away_name = m.get('away_team', '')
        
        # 用bfdata中文名覆盖英文名
        if sid in bfdata_cn_map:
            cn_h, cn_a = bfdata_cn_map[sid]
            if cn_h and all(ord(c) < 128 for c in home_name):
                home_name = cn_h
            if cn_a and all(ord(c) < 128 for c in away_name):
                away_name = cn_a
        
        # 兜底：从1x2d.js提取中文名
        if all(ord(c) < 128 for c in home_name + away_name):
            cn_pair = _get_cn_from_1x2d(sid)
            if cn_pair:
                home_name, away_name = cn_pair
        
        # 有HKJC赔率，构建比赛记录
        match = {
            'fid': sid,  # 用sid代替fid（保持下游兼容）
            'date': date_str,
            'match_time': m.get('match_time', ''),
            'event': m.get('event', m.get('league', '')),
            'home_team': home_name,
            'away_team': away_name,
            'score': '',
            'status': '',
            'source': 'hkjc',
            'home_rank': 0,
            'away_rank': 0,
            # HKJC赔率
            'odds_hkjc_open_win': hkjc['open']['win'],
            'odds_hkjc_open_draw': hkjc['open']['draw'],
            'odds_hkjc_open_loss': hkjc['open']['loss'],
            'odds_hkjc_win': hkjc['latest']['win'],
            'odds_hkjc_draw': hkjc['latest']['draw'],
            'odds_hkjc_loss': hkjc['latest']['loss'],
            'odds_hkjc_changes': hkjc.get('changes', 1),
        }
        
        # 提取比赛时间（从OddsHistory页面的时间戳）
        if not match['match_time']:
            # 取开盘时间的日=比赛日
            open_data = hkjc.get('open', {})
            match['match_time'] = f'{date_str} 00:00'
        
        # 赔率公司名
        match['odds_hkjc_company'] = 'HKJC'
        
        # 3. 获取平博赔率
        pinnacle = get_odds_history(sid, CID_PINNACLE)
        if pinnacle:
            match['odds_pinnacle_open_win'] = pinnacle['open']['win']
            match['odds_pinnacle_open_draw'] = pinnacle['open']['draw']
            match['odds_pinnacle_open_loss'] = pinnacle['open']['loss']
            match['odds_pinnacle_win'] = pinnacle['latest']['win']
            match['odds_pinnacle_draw'] = pinnacle['latest']['draw']
            match['odds_pinnacle_loss'] = pinnacle['latest']['loss']
            match['odds_pinnacle_changes'] = pinnacle.get('changes', 1)
            match['odds_pinnacle_company'] = 'Pinnacle'
        
        # 从分析页提取比赛时间（仅当strTime日期与比赛日期一致）
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
    """从titan007 Over_日期.htm 页面获取当天所有完场比分
    
    返回: { (home_team, away_team): score_str } 字典
    """
    import re, urllib.request
    try:
        url = f'https://bf.titan007.com/football/Over_{date_str.replace("-", "")}.htm'
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        resp = urllib.request.urlopen(req, timeout=10)
        raw = resp.read()
        html = raw.decode('gb2312', errors='replace')
        
        scores = {}
        rows = re.findall(r'<tr[^>]*>.*?</tr>', html, re.DOTALL|re.IGNORECASE)
        for row in rows:
            tds = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL|re.IGNORECASE)
            if len(tds) >= 6:
                tds_clean = []
                for td in tds:
                    c = re.sub(r'<[^>]+>', ' ', td).strip()
                    c = re.sub(r'\s+', ' ', c)
                    tds_clean.append(c)
                home = tds_clean[3].strip()
                score = tds_clean[4].strip()
                away = tds_clean[5].strip()
                if re.match(r'^\d+\s*-\s*\d+$', score):
                    home_clean = re.sub(r'\s*\[[^\]]*\]', '', home).strip()
                    away_clean = re.sub(r'\s*\[[^\]]*\]', '', away).strip()
                    home_clean = re.sub(r'\([^)]*\)', '', home_clean).strip()
                    away_clean = re.sub(r'\([^)]*\)', '', away_clean).strip()
                    scores[(home_clean, away_clean)] = score
        return scores
    except Exception as e:
        print(f'[WARN] Over页面抓取失败: {e}')
        return {}


def do_backfill(fpath, date_str):
    """比分回填：titan007 Over页 → wanchang兜底"""
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
    
    # 1. titan007 Over页完整比分表 — 所有sid通用，按队名匹配
    unscored = [(fid, m) for fid, m in existing_matches.items()
                if not m.get('score') and m.get('match_time')]
    if unscored:
        scores = get_scores_from_over_page(date_str)
        if scores:
            over_ok = 0
            for fid, m in unscored:
                home = m.get('home_team', '').strip()
                away = m.get('away_team', '').strip()
                key = (home, away)
                if key in scores:
                    m['score'] = scores[key]
                    over_ok += 1
                elif (away, home) in scores:
                    m['score'] = scores[(away, home)]
                    over_ok += 1
            if over_ok:
                existing['matches'] = list(existing_matches.values())
                existing['fetched_at'] = datetime.now().isoformat()
                with open(fpath, 'w', encoding='utf-8') as f:
                    json.dump(existing, f, ensure_ascii=False, indent=2)
                print(f'[BACKFILL] titan007 Over页 → {over_ok}/{len(unscored)} 场')
            else:
                print(f'[BACKFILL] titan007 Over页 → 未匹配到比分')
        else:
            print(f'[BACKFILL] titan007 Over页 → 页面无完场数据')
    
    # 2. wanchang兜底（500.com，补Over页漏掉的旧sid）
    wc_unscored = [(fid, m) for fid, m in existing_matches.items()
                   if not m.get('score') and m.get('match_time')]
    if wc_unscored:
        print(f'[BACKFILL] wanchang.php兜底: {len(wc_unscored)} 场待补…')
        try:
            wc_html = fetch_url('https://live.500.com/wanchang.php')
            wc_scores = {}
            for pk_div in re.finditer(
                r'<div\s+class="pk">.*?<a[^>]*fid=(\d+)[^>]*>(\d+)</a>\s*<span>-</span>\s*<a[^>]*fid=\1[^>]*>(\d+)</a>',
                wc_html, re.DOTALL | re.IGNORECASE):
                wc_scores[pk_div.group(1)] = f'{pk_div.group(2)}-{pk_div.group(3)}'
            wc_updated = 0
            for fid, m in wc_unscored:
                if fid in wc_scores:
                    m['score'] = wc_scores[fid]
                    wc_updated += 1
            if wc_updated:
                existing['matches'] = list(existing_matches.values())
                existing['fetched_at'] = datetime.now().isoformat()
                with open(fpath, 'w', encoding='utf-8') as f:
                    json.dump(existing, f, ensure_ascii=False, indent=2)
                print(f'[BACKFILL] wanchang.php → {wc_updated} 场比分已更新')
            else:
                print('[BACKFILL] wanchang.php→未找到匹配比分')
        except Exception as e:
            print(f'[BACKFILL] wanchang.php抓取失败: {e}')
    
    # 已完赛比分(仍用500.com wanchang + ESPN)
    unscored = [(fid, m) for fid, m in existing_matches.items()
                if not m.get('score') and m.get('match_time')]
    if unscored:
        print(f'[BACKFILL] ESPN API兜底: {len(unscored)} 场待补…')
        LEAGUE_MAP = [
            ('美女职', 'usa.nwsl'), ('美职联', 'usa.1'), ('美乙', 'usa.2'),
            ('英超', 'eng.1'), ('英冠', 'eng.2'), ('英甲', 'eng.3'),
            ('苏超', 'sco.1'), ('苏冠', 'sco.2'),
            ('意甲', 'ita.1'), ('意乙', 'ita.2'),
            ('西甲', 'esp.1'), ('西乙', 'esp.2'),
            ('德甲', 'ger.1'), ('德乙', 'ger.2'),
            ('法甲', 'fra.1'), ('法乙', 'fra.2'),
            ('荷甲', 'ned.1'), ('葡超', 'por.1'),
            ('日职', 'jpn.1'), ('日乙', 'jpn.2'),
            ('K联赛', 'kor.1'), ('K2', 'kor.2'),
            ('澳超', 'aus.1'), ('中超', 'chn.1'),
            ('巴甲', 'bra.1'), ('阿甲', 'arg.1'),
            ('墨西联', 'mex.1'), ('比甲', 'bel.1'),
            ('瑞超', 'swe.1'), ('挪超', 'nor.1'),
            ('丹超', 'den.1'), ('土超', 'tur.1'),
            ('奥甲', 'aut.1'), ('瑞士超', 'sui.1'),
            ('乌超', 'ukr.1'), ('俄超', 'rus.1'),
        ]
        espn_updated = 0
        for fid, m in unscored:
            mt = m.get('match_time', '')
            event = m.get('event', '')
            if not mt:
                continue
            try:
                match_dt = datetime.fromisoformat(mt[:16].replace(' ', 'T'))
                match_utc = match_dt - timedelta(hours=8)
                if match_utc > datetime.utcnow():
                    continue
            except:
                continue
            
            espn_dates = set()
            for base in [match_utc, datetime.utcnow()]:
                espn_dates.add(base.strftime('%Y%m%d'))
                espn_dates.add((base - timedelta(days=1)).strftime('%Y%m%d'))
            espn_dates = sorted(espn_dates)
            
            league_ids = [lid for e, lid in LEAGUE_MAP if e in event]
            if not league_ids:
                continue
            
            found = False
            for ed in espn_dates:
                for lid in league_ids:
                    url = f'https://site.api.espn.com/apis/site/v2/sports/soccer/{lid}/scoreboard?dates={ed}'
                    try:
                        raw = urllib.request.urlopen(
                            urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=10).read()
                        api_data = json.loads(raw)
                    except:
                        continue
                    best_match = None
                    best_diff = 99999
                    for ev in api_data.get('events', []):
                        comp = ev.get('competitions', [{}])[0]
                        comps = comp.get('competitors', [])
                        if len(comps) < 2:
                            continue
                        status = comp.get('status', {}).get('type', {}).get('state', '')
                        if status != 'post':
                            continue
                        espn_date_str = comp.get('date', '')
                        try:
                            espn_utc = datetime.fromisoformat(espn_date_str.replace('Z', '+00:00'))
                            diff = abs((espn_utc - match_utc.replace(tzinfo=timezone.utc)).total_seconds())
                        except:
                            continue
                        if diff < best_diff:
                            best_diff = diff
                            best_match = comps
                    if best_match and best_diff < 3600:
                        s1 = best_match[0].get('score', '')
                        s2 = best_match[1].get('score', '')
                        if s1 and s2:
                            m['score'] = f'{s1}-{s2}'
                            espn_updated += 1
                            print(f'  ✓ {m.get("home_team","")} {s1}-{s2} (ESPN {lid}, diff={best_diff/60:.0f}min)')
                            found = True
                            break
                    if found:
                        break
                if found:
                    break
        
        if espn_updated:
            existing['matches'] = list(existing_matches.values())
            existing['fetched_at'] = datetime.now().isoformat()
            with open(fpath, 'w', encoding='utf-8') as f:
                json.dump(existing, f, ensure_ascii=False, indent=2)
            print(f'[BACKFILL] ESPN → {espn_updated} 场比分已更新')
        else:
            print('[BACKFILL] ESPN兜底→无新比分')


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
