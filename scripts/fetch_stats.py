#!/usr/bin/env python3
"""fetch_stats.py — 从titan007分析页获取H2H和近期战绩

替代：原500.com版(已废弃)

数据流:
1. 用sid从 titan007 分析页JS变量解析 H2H(v_data)+近期(h_data/a_data)
2. 转换为兼容的输出格式供 ai_analysis.py 使用
3. 提供特征提取函数 (extract_form_features, extract_h2h_features)

用法:
  python3 scripts/fetch_stats.py <sid1> [sid2 ...]
"""
import re, json, time, sys, os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
from titan007_utils import get_analysis_data


def match_result(hs, as_):
    """从比分返回文本结果: 胜/平/负"""
    if hs is None or as_ is None:
        return ''
    if hs > as_:
        return '胜'
    if hs == as_:
        return '平'
    return '负'


def fetch_match_stats(sid, max_retries=2):
    """获取单场比赛的 H2H + 近期战绩（从titan007分析页）
    
    返回兼容 old format: {
        'h2h': [{'league','date','home','away','home_score','away_score','half_score','result'}, ...],
        'home_form': [{'league','date','home','away','home_score','away_score','handicap','half_score','result','panlu','daxiao'}, ...],
        'away_form': [same],
    }
    返回 None 表示失败
    """
    sid_s = str(int(float(sid)))
    for attempt in range(max_retries + 1):
        try:
            data = get_analysis_data(sid_s)
            if data is None:
                time.sleep(0.5)
                continue

            # H2H: v_data → old h2h format
            h2h = []
            for h in data.get('h2h', []):
                hs = int(h.get('home_score', 0) or 0)
                as_ = int(h.get('away_score', 0) or 0)
                h2h.append({
                    'league': h.get('league', ''),
                    'date': h.get('date', ''),
                    'home': h.get('home', ''),
                    'away': h.get('away', ''),
                    'home_score': hs,
                    'away_score': as_,
                    'half_score': '',
                    'result': match_result(hs, as_),
                })

            # 近期战绩: h_data/a_data → old form format
            def convert_form(form_data, home_team, away_team):
                """将team-centric格式转为match-centric格式"""
                result = []
                for m in form_data:
                    team = m.get('team', '')
                    opp = m.get('opponent', '')
                    ts = int(m.get('team_score', 0) or 0)
                    os_ = int(m.get('opponent_score', 0) or 0)
                    is_home_in_match = m.get('is_home', True)
                    
                    if is_home_in_match:
                        home, away = team, opp
                        hs, as_ = ts, os_
                    else:
                        home, away = opp, team
                        hs, as_ = os_, ts
                    
                    result.append({
                        'league': m.get('league', ''),
                        'date': m.get('date', ''),
                        'home': home,
                        'away': away,
                        'home_score': hs,
                        'away_score': as_,
                        'handicap': '',
                        'half_score': '',
                        'result': match_result(hs, as_),
                        'panlu': '',
                        'daxiao': '',
                    })
                return result

            home_form = convert_form(
                data.get('home_form', []),
                data.get('home_name', ''),
                ''  # away team not needed
            )
            away_form = convert_form(
                data.get('away_form', []),
                '',  # home team not needed
                data.get('away_name', '')  # away team not needed
            )

            return {'h2h': h2h, 'home_form': home_form, 'away_form': away_form}

        except Exception as e:
            if attempt < max_retries:
                time.sleep(0.5)
                continue
            return None


# ─── 以下函数与原版完全一致（特征提取）───────────


def extract_form_features(form_data, team_is_home=True):
    """从近期战绩提取数值特征"""
    if not form_data:
        return {'win_rate': 0.5, 'draw_rate': 0.25, 'loss_rate': 0.25,
                'goals_avg': 1.0, 'goals_conceded_avg': 1.0,
                'win_streak': 0, 'panlu_win_rate': 0.5, 'over_rate': 0.5}
    n = len(form_data)
    wins = 0; draws = 0; losses = 0
    goals_f = 0; goals_a = 0
    panlu_wins = 0; panlu_losses = 0
    overs = 0; unders = 0

    for m in form_data:
        hs = m.get('home_score', 0) or 0
        as_ = m.get('away_score', 0) or 0
        result = m.get('result', '')

        if team_is_home:
            if hs > as_: wins += 1
            elif hs == as_: draws += 1
            else: losses += 1
        else:
            if as_ > hs: wins += 1
            elif hs == as_: draws += 1
            else: losses += 1
        goals_f += hs if team_is_home else as_
        goals_a += as_ if team_is_home else hs

        panlu = m.get('panlu', '')
        if panlu == '赢': panlu_wins += 1
        elif panlu == '输': panlu_losses += 1
        daxiao = m.get('daxiao', '')
        if daxiao == '大': overs += 1
        elif daxiao == '小': unders += 1

    last_result = form_data[0].get('result', '') if form_data else ''
    win_streak = 1 if last_result in ('胜', '赢') else (-1 if last_result in ('负', '输') else 0)

    return {
        'win_rate': round(wins / max(n, 1), 4),
        'draw_rate': round(draws / max(n, 1), 4),
        'loss_rate': round(losses / max(n, 1), 4),
        'goals_avg': round(goals_f / max(n, 1), 2),
        'goals_conceded_avg': round(goals_a / max(n, 1), 2),
        'win_streak': win_streak,
        'panlu_win_rate': round(panlu_wins / max(panlu_wins + panlu_losses, 1), 4),
        'over_rate': round(overs / max(overs + unders, 1), 4),
    }


def extract_h2h_features(h2h_data):
    """从历史交锋提取特征"""
    if not h2h_data:
        return {'home_win_rate': 0.35, 'draw_rate': 0.3, 'away_win_rate': 0.35,
                'home_goals_avg': 1.0, 'away_goals_avg': 1.0,
                'total_matches': 0, 'recent_trend': 0, 'h2h_advantage': 0}
    home_wins = 0; draws = 0; away_wins = 0; n = 0
    home_goals = 0; away_goals = 0
    for m in h2h_data:
        hs = m.get('home_score')
        as_ = m.get('away_score')
        if hs is None or as_ is None: continue
        home_goals += hs; away_goals += as_
        n += 1
        if hs > as_: home_wins += 1
        elif hs == as_: draws += 1
        else: away_wins += 1
    recent_trend = 0
    if h2h_data:
        last = h2h_data[0]
        hs, as_ = last.get('home_score'), last.get('away_score')
        if hs is not None and as_ is not None:
            if hs > as_: recent_trend = 1
            elif hs < as_: recent_trend = -1
    return {
        'home_win_rate': round(home_wins / max(n, 1), 4),
        'draw_rate': round(draws / max(n, 1), 4),
        'away_win_rate': round(away_wins / max(n, 1), 4),
        'home_goals_avg': round(home_goals / max(n, 1), 2),
        'away_goals_avg': round(away_goals / max(n, 1), 2),
        'total_matches': n,
        'recent_trend': recent_trend,
        'h2h_advantage': round((home_wins - away_wins) / max(n, 1), 4),
    }


# ─── 自测 ────────────────────────────────────

if __name__ == '__main__':
    sids = sys.argv[1:] if len(sys.argv) > 1 else ['2907402']
    for sid in sids:
        print(f"\n{'='*60}")
        print(f"📊 获取比赛 sid={sid}")
        print('='*60)
        stats = fetch_match_stats(sid)
        if not stats:
            print("❌ 获取失败")
            continue

        print(f"\n📋 历史交锋 (H2H): {len(stats['h2h'])} 场")
        for h in stats['h2h'][:5]:
            print(f"  {h['date']} {h['home']} {h['home_score']}:{h['away_score']} {h['away']} ({h['result']})")

        h2h_feat = extract_h2h_features(stats['h2h'])
        print(f"\n  H2H特征: 主胜率={h2h_feat['home_win_rate']:.0%} 平率={h2h_feat['draw_rate']:.0%} 客胜率={h2h_feat['away_win_rate']:.0%}")
        print(f"  主均进={h2h_feat['home_goals_avg']} 客均进={h2h_feat['away_goals_avg']}")

        print(f"\n📈 主队近期战绩: {len(stats['home_form'])} 场")
        for h in stats['home_form'][:5]:
            print(f"  {h['date']} {h['home']} {h['home_score']}:{h['away_score']} {h['away']} ({h['result']})")
        hf = extract_form_features(stats['home_form'], team_is_home=True)
        print(f"  胜率={hf['win_rate']:.0%} 均进={hf['goals_avg']} 均失={hf['goals_conceded_avg']}")

        if stats['away_form']:
            print(f"\n📈 客队近期战绩: {len(stats['away_form'])} 场")
            for h in stats['away_form'][:5]:
                print(f"  {h['date']} {h['home']} {h['home_score']}:{h['away_score']} {h['away']} ({h['result']})")
            af = extract_form_features(stats['away_form'], team_is_home=False)
            print(f"  胜率={af['win_rate']:.0%} 均进={af['goals_avg']} 均失={af['goals_conceded_avg']}")
