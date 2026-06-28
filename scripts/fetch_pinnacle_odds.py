#!/usr/bin/env python3
"""从中国足彩网(zgzcw.com)百家指数页面抓取各家欧赔数据

【重要】数据源说明：
- 本脚本所有赔率数据来自【中国足彩网 zgzcw.com】，不是500.com，也不是中国竞彩网(sporttery.cn)
- 500.com (fetch_500com_results.py) 只抓赛果，不抓赔率
- 中国竞彩网 (sporttery.cn) 是体彩官方平台，本脚本未使用
- 中国足彩网 (zgzcw.com) 是第一视频集团旗下彩票资讯平台，提供百家指数赔率对比

数据源页面：
1. 列表页 https://plzx.zgzcw.com/bjzs - GET请求，返回百家平均欧赔
2. POST请求同一URL + company参数 - 返回指定公司赔率
   - aid=106: 真Pinnacle(平博)
   - aid=56: Betfair(必发)
   - aid=3: SB(明升)
   - aid=136: HKJC(香港马会)
3. 详情页 http://fenxi.zgzcw.com/{match_id}/bjop - 被CloudWAF拦截，需fetch_web

【HHAD让球盘问题】：
- 北单(type=bd)页面返回让球盘(HHAD)赔率而非标准盘(HAD)，数据不可信
- 让球盘特征：d值(本应为平局赔率)异常低(<2.0)，实际是让球方胜赔
- 竞彩(type=jc)页面正常，无此问题
- 处理方式：已将include_beidan默认改为False，直接禁用北单赔率抓取
- 北单场次赔率降级为百家平均或竞彩HAD
- d<2.0检测仍保留作为保险，防止有人手动开启include_beidan
"""

import re
import json
import os
import time
import subprocess
import sqlite3
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# 智能检测仓库结构
_REPO_DIR = os.path.dirname(BASE_DIR)
if os.path.isdir(os.path.join(_REPO_DIR, 'data')):
    DATA_BASE_DIR = _REPO_DIR
else:
    DATA_BASE_DIR = BASE_DIR
DB_PATH = os.path.join(DATA_BASE_DIR, "data/football.db")
DB_JINGCAI = DB_PATH  # 统一用football.db
DB_BEIDAN = DB_PATH   # 统一用football.db

BASE_URL_LIST = "https://plzx.zgzcw.com/bjzs"
BASE_URL_DETAIL = "http://fenxi.zgzcw.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml",
}


# ========== 队名映射表（oddsmagnet → DB竞彩标准名）==========
# 来源：oddsmagnet缓存 vs football.db 对比，仅收录高置信度映射
# 规则：key=oddsmagnet用名, value=DB中竞彩/体彩标准名
TEAM_NAME_ALIASES = {
    # ===== 日本J联赛 =====
    '町田泽维亚': '町田泽维',
    '神户胜利船': '神户胜利',
    '名古屋鲸鱼': '名古屋鲸',
    '长崎成功丸': '长崎航海',
    '清水心跳': '清水鼓动',
    # ===== 韩国K联赛 =====
    '安山小绿人': '安山新军',
    '坡州前线': '坡州市民',
    # ===== 瑞典超/甲 =====
    '哥德堡盖斯': '盖斯',
    '瓦斯特拉斯': '韦斯特罗',
    '布洛马波卡纳': '布鲁马波',
    '法尔肯堡': '法尔肯贝里',
    '兰斯科罗纳': '兰斯科罗',
    # ===== 丹麦超/甲 =====
    '奥胡斯费马': '奥胡斯',
    '希勒罗德': '希勒勒',
    'B93哥本哈根': 'B93哥本',
    # ===== 芬兰超 =====
    'EIF埃克纳斯': '埃克奈斯',
    'VPS瓦萨': '瓦萨',
    '奥卢': 'AC奥卢',
    'TPS图尔库': 'TPS图尔',
    'KA阿克雷里': 'KA阿古雷',
    '托尔阿克雷里': '托尔',
    'PK35万塔': '万塔',
    # ===== 冰岛超 =====
    '凯夫拉维克': '凯夫拉维',
    '维京古': '维京古尔',
    # ===== 巴西甲/乙 =====
    '布拉干蒂诺RB': '布拉干蒂诺',
    '诺瓦里桑蒂诺': '诺瓦桑蒂诺',
    '戈亚尼亚竞技': '戈亚尼亚',
    '圣贝尔纳多': '圣贝纳多',
    '米拉索': '米拉索尔',
    # ===== 阿甲 =====
    '罗萨里奥中央': '罗萨里奥',
    # ===== 其他 =====
    '安道尔FC': '安道尔',
}

# ========== 队名相似度函数 ==========

def team_name_similarity(name1, name2):
    """计算两队名的相似度 (0-1)
    
    匹配策略（按优先级）：
    0. 静态映射表 TEAM_NAME_ALIASES 精确匹配
    1. 完全匹配
    2. 包含匹配（一个包含另一个）
    3. 常见简称映射后匹配
    4. 去掉常见前缀/后缀后匹配
    5. 编辑距离模糊匹配
    """
    if not name1 or not name2:
        return 0.0
    
    # 策略0: 静态映射表查表（oddsmagnet名→DB名）
    n1_alias = TEAM_NAME_ALIASES.get(name1.strip(), name1.strip())
    n2_alias = TEAM_NAME_ALIASES.get(name2.strip(), name2.strip())
    if n1_alias == n2_alias:
        return 1.0
    if n1_alias in n2_alias or n2_alias in n1_alias:
        return 0.95
    
    # 标准化：去除空格、转小写
    n1 = name1.strip().lower()
    n2 = name2.strip().lower()
    
    # 策略1: 完全匹配
    if n1 == n2:
        return 1.0
    
    # 策略2: 包含匹配
    if n1 in n2 or n2 in n1:
        # 短名包含在长名中，得分根据长度比例
        ratio = max(len(n1), len(n2)) / min(len(n1), len(n2)) if min(len(n1), len(n2)) > 0 else 1
        # 如果比例接近1（差异小于50%），认为是同一队
        if ratio < 1.5:
            return 0.9
        elif ratio < 2.0:
            return 0.85
        else:
            return 0.75
    
    # 策略3: 常见简称映射
    abbr_map = {
        # 曼城系
        '曼城': '曼彻斯特城', '曼彻斯特城': '曼彻斯特城',
        '曼联': '曼彻斯特联', '曼彻斯特联': '曼彻斯特联',
        # 热刺系
        '热刺': '托特纳姆热刺', '托特纳姆热刺': '托特纳姆热刺',
        # 皇马系
        '皇马': '皇家马德里', '皇家马德里': '皇家马德里',
        # 巴萨系
        '巴萨': '巴塞罗那', '巴塞罗那': '巴塞罗那',
        # 马竞系
        '马竞': '马德里竞技', '马德里竞技': '马德里竞技',
        # 拜仁系
        '拜仁': '拜仁慕尼黑', '拜仁慕尼黑': '拜仁慕尼黑',
        # 国米系
        '国米': '国际米兰', '国际米兰': '国际米兰',
        # 尤文系
        '尤文': '尤文图斯', '尤文图斯': '尤文图斯',
        # 多特系
        '多特': '多特蒙德', '多特蒙德': '多特蒙德',
        # 切尔西系
        '切尔西': '切尔西',
        # 阿森纳系
        '阿森纳': '阿森纳',
        # 利物浦系
        '利物浦': '利物浦',
        # 米兰系
        'AC米兰': 'AC米兰', '米兰': 'AC米兰',
        # 罗马系
        '罗马': '罗马',
        # 那不勒斯系
        '那不勒斯': '那不勒斯',
        # 勒沃库森
        '勒沃': '勒沃库森', '勒沃库森': '勒沃库森',
        # 莱比锡
        '莱比锡': 'RB莱比锡', 'RB莱比锡': 'RB莱比锡',
        # 法甲
        '里昂': '里昂', '马赛': '马赛', '摩纳哥': '摩纳哥',
        # 中超
        '上港': '上海海港', '上海海港': '上海海港',
        '国安': '北京国安', '北京国安': '北京国安',
        '泰山': '山东泰山', '山东泰山': '山东泰山',
        '恒大': '广州恒大', '广州恒大': '广州恒大',
        '恒大': '广州队', '广州队': '广州队',
        '海港': '上海海港',
        '三镇': '武汉三镇', '武汉三镇': '武汉三镇',
        '蓉城': '成都蓉城', '成都蓉城': '成都蓉城',
        '河南': '河南队', '河南队': '河南队',
        # 日职
        '川崎': '川崎前锋', '川崎前锋': '川崎前锋',
        '横滨': '横滨水手', '横滨水手': '横滨水手',
        '鹿岛': '鹿岛鹿角', '鹿岛鹿角': '鹿岛鹿角',
        '樱花': '大阪樱花', '大阪樱花': '大阪樱花',
        '钢巴': '大阪钢巴', '大阪钢巴': '大阪钢巴',
        # 瑞超同队不同译名（映射到统一标准名）
        '佐加顿斯': '佐加顿斯', '尤尔加登': '佐加顿斯',
        '厄格里特': '厄格里特', '奥尔格里特': '厄格里特',
        '哥德堡': '哥德堡', 'IFK哥德堡': '哥德堡',
        '索尔纳': '索尔纳', 'AIK索尔纳': '索尔纳',
        # 韩K
        '全北': '全北现代', '全北现代': '全北现代',
        '蔚山': '蔚山现代', '蔚山现代': '蔚山现代',
        '水原': '水原三星', '水原三星': '水原三星',
        # 南美 - 巴西
        '弗鲁米嫩': '弗鲁米嫩塞', '弗鲁米嫩塞': '弗鲁米嫩塞',
        '弗拉门戈': '弗拉门戈', '弗拉门': '弗拉门戈',
        '帕尔梅拉斯': '帕尔梅拉斯', '帕尔梅': '帕尔梅拉斯',
        '科林蒂安': '科林蒂安', '科林蒂': '科林蒂安',
        '圣保罗': '圣保罗',
        '桑托斯': '桑托斯',
        '格雷米奥': '格雷米奥', '格雷米': '格雷米奥',
        '国际体育': '国际体育会', '国际体育会': '国际体育会',
        '巴伊亚': '巴伊亚',
        '福塔雷萨': '福塔雷萨',
        '布拉干蒂诺': '布拉干蒂诺',
        '库亚巴': '库亚巴',
        '尤文图德': '尤文图德',
        '戈亚尼亚': '戈亚尼亚竞技', '戈亚尼亚竞技': '戈亚尼亚竞技',
        '阿瓦伊': '阿瓦伊',
        # 南美 - 阿根廷
        '罗萨里奥': '罗萨里奥中央', '罗萨里奥中央': '罗萨里奥中央',
        '博卡青年': '博卡青年', '博卡': '博卡青年',
        '河床': '河床',
        '竞技俱乐部': '竞技俱乐部', '竞技': '竞技俱乐部',
        '独立': '独立队', '独立队': '独立队',
        '圣洛伦索': '圣洛伦索',
        '拉普拉塔': '拉普拉塔大学生', '拉普拉塔大学生': '拉普拉塔大学生',
        '防御与正义': '防御与正义',
        '飓风': '飓风队', '飓风队': '飓风队',
        '塔勒雷斯': '塔勒雷斯',
        '贝尔格拉诺': '贝尔格拉诺',
        '萨斯菲尔德': '萨斯菲尔德',
        '班菲尔德': '班菲尔德',
        '阿根廷青年': '阿根廷青年人', '阿根廷青年人': '阿根廷青年人',
        # 南美 - 玻利维亚/其他
        '时刻准备': '时刻准备', '拉巴斯准备': '时刻准备', '斯特朗est': '时刻准备',
        '玻利瓦尔': '玻利瓦尔',
        '奥尔良': '奥尔良',
        '威斯特曼': '威斯特曼',
        # 南美 - 解放者杯/南美杯常见
        '佩纳罗尔': '佩纳罗尔',
        '民族': '民族队', '民族队': '民族队',
        '自由': '自由队', '自由队': '自由队',
        '奥林匹亚': '奥林匹亚',
        '瓜拉尼': '瓜拉尼',
        '亚松森': '亚松森自由', '亚松森自由': '亚松森自由',
        # 墨西哥
        '美洲': '美洲队', '美洲队': '美洲队',
        '蓝十字': '蓝十字',
        '瓜达拉哈拉': '瓜达拉哈拉',
        '蒙特雷': '蒙特雷',
        '老虎大学': '老虎大学',
        # 哥伦比亚
        '百万富翁': '百万富翁',
        '卡利体育': '卡利体育',
        '国民竞技': '国民竞技',
        # 智利
        '科洛科洛': '科洛科洛',
        '天主教大学': '天主教大学',
        # 厄瓜多尔
        '基多大学': '基多大学',
        '基多民族': '基多民族',
    }
    
    n1_mapped = abbr_map.get(n1, n1)
    n2_mapped = abbr_map.get(n2, n2)
    
    # 如果简称映射后完全匹配，返回高相似度
    if n1_mapped == n2_mapped:
        return 0.95
    
    # 如果一个名称被映射后包含另一个，也返回高相似度
    if n1_mapped in n2_mapped or n2_mapped in n1_mapped:
        return 0.90
    
    # 策略4: 去掉常见前缀/后缀后匹配
    prefixes = ['fc', 'cf', 'sc', 'ac', 'rc', '体育', '足球', '俱乐部']
    suffixes = ['fc', 'cf', 'sc', 'ac', 'rc', '体育', '足球', '俱乐部', '队']
    
    def strip_prefix_suffix(name):
        n = name.lower()
        for p in prefixes:
            if n.startswith(p):
                n = n[len(p):].strip()
        for s in suffixes:
            if n.endswith(s):
                n = n[:-len(s)].strip()
        return n
    
    n1_stripped = strip_prefix_suffix(n1)
    n2_stripped = strip_prefix_suffix(n2)
    
    if n1_stripped and n2_stripped:
        if n1_stripped == n2_stripped:
            return 0.9
        if n1_stripped in n2_stripped or n2_stripped in n1_stripped:
            return 0.85
    
    # 策略5: 编辑距离模糊匹配
    dist = levenshtein_distance(n1_stripped, n2_stripped)
    max_len = max(len(n1_stripped), len(n2_stripped))
    if max_len == 0:
        return 0.0
    
    similarity = 1.0 - (dist / max_len)
    
    # 如果编辑距离相似度超过阈值，认为是同一队
    if similarity >= 0.8:
        return similarity * 0.9  # 稍微降低权重
    elif similarity >= 0.6:
        return similarity * 0.8
    else:
        return similarity * 0.5  # 低于0.6的相似度权重更低


def levenshtein_distance(s1, s2):
    """计算两个字符串的编辑距离"""
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)
    
    if len(s2) == 0:
        return len(s1)
    
    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    
    return previous_row[-1]


def fetch_page_requests(url, timeout=30):
    """用requests抓取页面，带WAF重试"""
    import requests
    import random
    max_retries = 2
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=timeout)
            # WAF检测
            if resp.status_code == 418:
                wait = 5 + random.randint(3, 8)
                print(f"[WARN] GET WAF拦截(418), 等待{wait}秒后重试({attempt+1}/{max_retries})")
                time.sleep(wait)
                continue
            resp.encoding = 'utf-8'
            if 'CloudWAF' in resp.text or '华为云' in resp.text:
                wait = 5 + random.randint(3, 8)
                print(f"[WARN] GET CloudWAF拦截, 等待{wait}秒后重试({attempt+1}/{max_retries})")
                time.sleep(wait)
                continue
            return resp.text
        except Exception as e:
            wait = 3 + random.randint(2, 5)
            print(f"[ERROR] requests {url}: {e}, 等待{wait}秒后重试({attempt+1}/{max_retries})")
            time.sleep(wait)
    print(f"[ERROR] GET {url} {max_retries}次重试均失败")
    return None


def fetch_page_fetch_web(url):
    """用fetch_web工具抓取页面（绕过CloudWAF）
    
    由于详情页被CloudWAF拦截，此函数尝试：
    1. 直接requests（可能被拦截）
    2. 读取由coze fetch_web工具预抓取的HTML缓存文件
    """
    import requests
    # 尝试1：直接请求
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)",
            "Accept": "text/html,application/xhtml+xml",
            "Referer": "https://plzx.zgzcw.com/",
        }
        resp = requests.get(url, headers=headers, timeout=30)
        resp.encoding = 'utf-8'
        if 'CloudWAF' not in resp.text and '足彩网' in resp.text:
            # 保存到缓存
            match_id = url.split('/')[-2] if '/' in url else ''
            cache_file = os.path.join(DATA_BASE_DIR, f"data/cache/bjop_{match_id}.html")
            os.makedirs(os.path.dirname(cache_file), exist_ok=True)
            with open(cache_file, 'w', encoding='utf-8') as f:
                f.write(resp.text)
            return resp.text
    except Exception as e:
        print(f"[WARN] requests {url} failed: {e}")
    
    # 尝试2：读取缓存文件（由coze fetch_web预抓取保存）
    match_id = url.split('/')[-2] if '/' in url else ''
    cache_file = os.path.join(DATA_BASE_DIR, f"data/cache/bjop_{match_id}.html")
    if os.path.exists(cache_file):
        with open(cache_file, 'r', encoding='utf-8') as f:
            content = f.read()
            if '足彩网' in content:
                return content
    
    print(f"[WARN] 详情页无法抓取且无缓存: {url}")
    return None


def parse_odds_value(text):
    """解析赔率值，去掉箭头标记。有效赔率范围1.01-50.0"""
    if not text:
        return 0.0
    text = str(text).strip().replace('↑', '').replace('↓', '').replace('→', '').replace('＊', '*')
    try:
        val = float(text)
        # 赔率合理范围：1.01~50.0，超出视为非赔率数据（如行号72）
        if val < 1.01 or val > 50.0:
            return 0.0
        return val
    except:
        return 0.0


def parse_movement(text):
    """解析赔率变动方向"""
    if not text:
        return 'stable'
    text = str(text).strip()
    if '↑' in text:
        return 'up'
    elif '↓' in text:
        return 'down'
    return 'stable'


def calc_implied_prob(win, draw, loss):
    """计算去抽水真实概率"""
    if win <= 0 or draw <= 0 or loss <= 0:
        return {'w': 0, 'd': 0, 'l': 0}, 0.0
    
    imp_w = 1.0 / win
    imp_d = 1.0 / draw
    imp_l = 1.0 / loss
    total = imp_w + imp_d + imp_l
    margin = total - 1.0
    
    true_w = imp_w / total
    true_d = imp_d / total
    true_l = imp_l / total
    
    return {'w': round(true_w, 4), 'd': round(true_d, 4), 'l': round(true_l, 4)}, round(margin, 4)


def fetch_match_list(date_str=None, page_type=None, aid=None):
    """抓取百家指数列表页，返回当日所有场次（GET方式，返回百家平均赔率）
    
    Args:
        date_str: 日期字符串
        page_type: None=竞彩, 'bd'=北单
        aid: 公司ID（注意：GET方式的aid参数实际不影响页面数据，始终返回百家平均）
    """
    url = BASE_URL_LIST
    params = []
    if date_str:
        params.append(f"date={date_str}")
    if page_type:
        params.append(f"type={page_type}")
    if params:
        url = url + "?" + "&".join(params)
    
    html = fetch_page_requests(url)
    if not html:
        return []
    
    matches = []
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL)
    
    for row in rows:
        # 提取对阵
        teams = re.findall(r'<a[^>]*class="t[12]"[^>]*>([^<]+)</a>', row)
        if len(teams) < 2:
            continue
        
        # 提取match_id
        match_ids = re.findall(r'fenxi\.zgzcw\.com/(\d+)/bjop', row)
        if not match_ids:
            continue
        
        # 提取时间
        kickoff = re.findall(r'(\d{2}-\d{2}\s+\d{2}:\d{2})', row)
        
        # 提取赔率（初盘3个+最新3个）
        # 格式: 4.420 4.250 1.700 4.37→ 4.26→ 1.68↓
        all_odds_raw = re.findall(r'>(\d+\.\d+(?:[↑↓→])?)<', row)
        
        # 也从td中提取
        td_contents = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
        
        odds_from_td = []
        for td in td_contents:
            td_clean = re.sub(r'<[^>]+>', '', td).strip()
            val = parse_odds_value(td_clean)
            if val > 0:
                odds_from_td.append((val, td_clean))
        
        # 初盘和最新赔率
        open_w = open_d = open_l = close_w = close_d = close_l = 0
        close_w_raw = close_d_raw = close_l_raw = ''
        
        if len(odds_from_td) >= 6:
            open_w = odds_from_td[0][0]
            open_d = odds_from_td[1][0]
            open_l = odds_from_td[2][0]
            close_w = odds_from_td[3][0]
            close_d = odds_from_td[4][0]
            close_l = odds_from_td[5][0]
            close_w_raw = odds_from_td[3][1]
            close_d_raw = odds_from_td[4][1]
            close_l_raw = odds_from_td[5][1]
        
        match = {
            'home': teams[0],
            'away': teams[1],
            'match_id': match_ids[0],
            'kickoff': kickoff[0] if kickoff else '',
            'avg_open': {'w': open_w, 'd': open_d, 'l': open_l},
            'avg_close': {'w': close_w, 'd': close_d, 'l': close_l},
            'avg_movement': {
                'w': parse_movement(close_w_raw),
                'd': parse_movement(close_d_raw),
                'l': parse_movement(close_l_raw),
            }
        }
        matches.append(match)
    
    return matches


def fetch_company_odds(date_str=None, page_type=None, company='106'):
    """通过POST方式抓取指定公司赔率
    
    足彩网百家指数公司ID映射（aid）:
    - 0: 平均*(百家平均), 3: SB(明升), 22: 平*(假Pinnacle), 56: 必*(Betfair)
    - 106: 平*(真Pinnacle)
    
    Args:
        date_str: 日期字符串
        page_type: None=竞彩, 'bd'=北单
        company: 公司aid，'106'=真Pinnacle, '56'=Betfair, '3'=SB
    Returns:
        dict: {match_id: {open: {w,d,l}, close: {w,d,l}, movement: {w,d,l}}}
    """
    import requests as req
    import random
    
    company_names = {'106': 'Pinnacle', '56': 'Betfair', '3': 'SB', '22': 'Pinnacle(旧)', '0': '平均', '136': 'HKJC'}
    company_name = company_names.get(company, f'aid={company}')
    
    data = {
        'type': page_type if page_type else 'jc',
        'issue': '',
        'company': company,
        'companyType': 'b',
        'date': date_str or datetime.now().strftime('%Y-%m-%d'),
        'fg': '1'
    }
    
    # WAF重试机制：最多重试3次，间隔5-8秒
    max_retries = 2
    for attempt in range(max_retries):
        try:
            # 使用Session复用连接
            session = req.Session()
            session.headers.update(HEADERS)
            # 先GET一次首页建立cookie
            if attempt == 0:
                try:
                    session.get(BASE_URL_LIST, timeout=10)
                    time.sleep(2)
                except:
                    pass
            
            resp = session.post(BASE_URL_LIST, data=data, timeout=15)
            
            # WAF检测
            if resp.status_code == 418:
                wait = 5 + random.randint(3, 8)
                print(f"[WARN] {company_name} WAF拦截(418), 等待{wait}秒后重试({attempt+1}/{max_retries})")
                time.sleep(wait)
                continue
            
            if 'CloudWAF' in resp.text or '华为云' in resp.text:
                wait = 5 + random.randint(3, 8)
                print(f"[WARN] {company_name} CloudWAF拦截, 等待{wait}秒后重试({attempt+1}/{max_retries})")
                time.sleep(wait)
                continue
            
            if resp.status_code != 200:
                print(f"[WARN] {company_name} POST请求失败: status={resp.status_code}")
                return {}
            
            html = resp.text
            if not html:
                return {}
            
            # 成功，跳出重试
            break
            
        except Exception as e:
            wait = 3 + random.randint(2, 5)
            print(f"[WARN] {company_name} POST请求异常: {e}, 等待{wait}秒后重试({attempt+1}/{max_retries})")
            time.sleep(wait)
    else:
        print(f"[ERROR] {company_name} {max_retries}次重试均失败")
        return {}
    
    result = {}
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL)
    
    for row in rows:
        teams = re.findall(r'<a[^>]*class="t[12]"[^>]*>([^<]+)</a>', row)
        if len(teams) < 2:
            continue
        
        match_ids = re.findall(r'fenxi\.zgzcw\.com/(\d+)/bjop', row)
        if not match_ids:
            continue
        
        # 提取赔率
        td_contents = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
        odds_from_td = []
        for td in td_contents:
            td_clean = re.sub(r'<[^>]+>', '', td).strip()
            val = parse_odds_value(td_clean)
            if val > 0:
                odds_from_td.append((val, td_clean))
        
        open_w = open_d = open_l = close_w = close_d = close_l = 0
        close_w_raw = close_d_raw = close_l_raw = ''
        
        if len(odds_from_td) >= 6:
            open_w = odds_from_td[0][0]
            open_d = odds_from_td[1][0]
            open_l = odds_from_td[2][0]
            close_w = odds_from_td[3][0]
            close_d = odds_from_td[4][0]
            close_l = odds_from_td[5][0]
            close_w_raw = odds_from_td[3][1]
            close_d_raw = odds_from_td[4][1]
            close_l_raw = odds_from_td[5][1]
        
        result[match_ids[0]] = {
            'home': teams[0],
            'away': teams[1],
            'open': {'w': open_w, 'd': open_d, 'l': open_l},
            'close': {'w': close_w, 'd': close_d, 'l': close_l},
            'movement': {
                'w': parse_movement(close_w_raw),
                'd': parse_movement(close_d_raw),
                'l': parse_movement(close_l_raw),
            }
        }
    
    print(f"[INFO] {company_name} POST: {len(result)} 场")
    return result




def parse_ah_value(text):
    """解析亚盘水位/盘口值，去掉箭头标记
    
    与parse_odds_value不同：
    - 水位范围0~3.0（如0.750, 1.67）
    - 盘口值可为负数（如-0.250, -1.0, 0.5）
    """
    if not text:
        return 0.0, 'stable'
    text = str(text).strip()
    movement = 'stable'
    if '↑' in text:
        movement = 'up'
    elif '↓' in text:
        movement = 'down'
    elif '→' in text:
        movement = 'stable'
    text = text.replace('↑', '').replace('↓', '').replace('→', '').replace('＊', '*').strip()
    try:
        val = float(text)
        return val, movement
    except:
        return 0.0, 'stable'


def fetch_asian_handicap(date_str=None, page_type=None, company='0'):
    """通过POST方式抓取亚盘让球盘数据 (companyType=y)
    
    亚盘数据格式与欧赔不同：
    - 欧赔: w/d/l (胜/平/负赔率)
    - 亚盘: home_water / handicap_line / away_water (主队水位/盘口/客队水位)
    
    HTML结构示例（百家平均亚盘，company=0）：
    TDs: ['', '周五003', '世界杯', '06-13 03:00', '加拿大VS波黑',
          '0.750', '-0.250', '0.990',   ← 初盘: 主水/盘口/客水
          '1.67↑', '-0.250→', '1.41↓',  ← 收盘: 主水/盘口/客水
          '欧 亚 析']
    
    Args:
        date_str: 日期字符串
        page_type: None=竞彩, 'bd'=北单
        company: '0'=百家平均, '136'=HKJC
    Returns:
        dict: {match_id: {open: {home_w, handicap, away_w}, close: {home_w, handicap, away_w}}}
    """
    import requests as req
    import random
    
    company_names = {'0': '百家平均', '136': 'HKJC', '106': 'Pinnacle'}
    company_name = company_names.get(company, f'aid={company}')
    
    data = {
        'type': page_type if page_type else 'jc',
        'issue': '',
        'company': company,
        'companyType': 'y',  # y=亚盘
        'date': date_str or datetime.now().strftime('%Y-%m-%d'),
        'fg': '1'
    }
    
    # WAF重试机制：最多重试2次
    max_retries = 2
    for attempt in range(max_retries):
        try:
            session = req.Session()
            session.headers.update(HEADERS)
            if attempt == 0:
                try:
                    session.get(BASE_URL_LIST, timeout=10)
                    time.sleep(2)
                except:
                    pass
            
            resp = session.post(BASE_URL_LIST, data=data, timeout=15)
            
            if resp.status_code == 418:
                wait = 5 + random.randint(3, 8)
                print(f"[WARN] 亚盘{company_name} WAF拦截(418), 等待{wait}秒后重试({attempt+1}/{max_retries})")
                time.sleep(wait)
                continue
            
            if 'CloudWAF' in resp.text or '华为云' in resp.text:
                wait = 5 + random.randint(3, 8)
                print(f"[WARN] 亚盘{company_name} CloudWAF拦截, 等待{wait}秒后重试({attempt+1}/{max_retries})")
                time.sleep(wait)
                continue
            
            if resp.status_code != 200:
                print(f"[WARN] 亚盘{company_name} POST请求失败: status={resp.status_code}")
                return {}
            
            html = resp.text
            if not html:
                return {}
            
            break
            
        except Exception as e:
            wait = 3 + random.randint(2, 5)
            print(f"[WARN] 亚盘{company_name} POST请求异常: {e}, 等待{wait}秒后重试({attempt+1}/{max_retries})")
            time.sleep(wait)
    else:
        print(f"[ERROR] 亚盘{company_name} {max_retries}次重试均失败")
        return {}
    
    result = {}
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL)
    
    for row in rows:
        teams = re.findall(r'<a[^>]*class="t[12]"[^>]*>([^<]+)</a>', row)
        if len(teams) < 2:
            continue
        
        match_ids = re.findall(r'fenxi\.zgzcw\.com/(\d+)/bjop', row)
        if not match_ids:
            continue
        
        # 提取TD内容
        td_contents = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
        td_values = []
        for td in td_contents:
            td_clean = re.sub(r'<[^>]+>', '', td).strip()
            td_values.append(td_clean)
        
        # 亚盘数据解析：TD中有6个赔率值（初盘3+收盘3）
        # 格式: 初盘主水, 初盘盘口, 初盘客水, 收盘主水, 收盘盘口, 收盘客水
        ah_values = []
        for tv in td_values:
            # 跳过空TD、编号、联赛名、时间、队名、操作链接等
            val, movement = parse_ah_value(tv)
            if val != 0.0 or tv.startswith('-') or tv.startswith('0.') or tv.startswith('1.') or tv.startswith('2.'):
                # 检查是否像水位或盘口值
                try:
                    float(tv.replace('↑','').replace('↓','').replace('→','').strip())
                    ah_values.append((val, movement, tv))
                except:
                    continue
        
        open_home_w = open_handicap = open_away_w = 0.0
        close_home_w = close_handicap = close_away_w = 0.0
        
        if len(ah_values) >= 6:
            open_home_w = ah_values[0][0]
            open_handicap = ah_values[1][0]
            open_away_w = ah_values[2][0]
            close_home_w = ah_values[3][0]
            close_handicap = ah_values[4][0]
            close_away_w = ah_values[5][0]
        
        # 基本校验：盘口和水位不能全为0
        if open_home_w == 0 and open_away_w == 0 and close_home_w == 0 and close_away_w == 0:
            continue
        
        result[match_ids[0]] = {
            'home': teams[0],
            'away': teams[1],
            'open': {'home_w': open_home_w, 'handicap': open_handicap, 'away_w': open_away_w},
            'close': {'home_w': close_home_w, 'handicap': close_handicap, 'away_w': close_away_w},
        }
    
    print(f"[INFO] 亚盘{company_name} POST: {len(result)} 场")
    return result


def parse_detail_page(html):
    """解析详情页，提取平博和平均欧赔
    
    HTML表格结构（从实际页面分析）：
    | 序号 | 公司 | 胜(初) | 平(初) | 负(初) | 胜(最新) | 平(最新) | 负(最新) | ...
    
    平博行示例（第25行）：
    | 25 | 平* | 4.63 | 4.34 | 1.61 | [4.59↑] | [4.42] | [1.69] | ...
    
    初盘赔率在纯文本中，最新赔率在链接中。
    """
    result = {
        'pinnacle_open': {'w': 0, 'd': 0, 'l': 0},
        'pinnacle_close': {'w': 0, 'd': 0, 'l': 0},
        'pinnacle_movement': {'w': 'stable', 'd': 'stable', 'l': 'stable'},
        'pinnacle_margin': 0,
        'implied_prob': {'w': 0, 'd': 0, 'l': 0},
        'williamhill': {'w': 0, 'd': 0, 'l': 0},
        'bet365': {'w': 0, 'd': 0, 'l': 0},
        'sb_odds': {'w': 0, 'd': 0, 'l': 0},  # SB公司最新赔率
    }
    
    if not html or 'CloudWAF' in html:
        return result
    
    # 方法：解析每行表格数据
    # HTML格式：<tr>...<td>公司名</td><td>胜(初)</td><td>平(初)</td><td>负(初)</td>...
    #                          <td>胜(最新)</td><td>平(最新)</td><td>负(最新)</td>...
    
    # 匹配表格行
    tr_pattern = r'<tr[^>]*>(.*?)</tr>'
    rows = re.findall(tr_pattern, html, re.DOTALL | re.IGNORECASE)
    
    for row in rows:
        # 提取公司ID
        company_id_match = re.search(r'company[_-]?id[=:]?\s*["\']?(\d+)', row, re.IGNORECASE)
        company_id = int(company_id_match.group(1)) if company_id_match else None
        
        # 提取公司名称（用于识别平博、威廉希尔、Bet365等）
        company_name = ''
        # 提取公司名称 - 先剥离内部HTML标签（如<font>），再匹配
        td_name_match = re.search(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
        if td_name_match:
            company_name = re.sub(r'<[^>]+>', '', td_name_match.group(1)).strip()
        else:
            company_name = ''
        
        # 判断是哪家公司
        is_pinnacle = (company_id == 106) or ('平' in company_name and '均' not in company_name)
        is_william = (company_id == 9) or '威' in company_name
        is_bet365 = (company_id == 2) or '36' in company_name
        is_avg = (company_id == 0) or '平均' in company_name
        is_sb = ('ＳＢ' in company_name or 'SB' in company_name or '沙' in company_name)
        
        if not (is_pinnacle or is_william or is_bet365 or is_avg or is_sb):
            continue
        
        # 提取该行中的所有td
        tds = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
        
        if len(tds) < 7:
            continue
        
        # 初盘赔率在td[2], td[3], td[4] (序号、公司、胜、平、负)
        # 最新赔率在td[5], td[6], td[7]
        
        # 提取初盘赔率（纯文本数字）
        open_odds = []
        for i in range(2, 5):  # td[2], td[3], td[4]
            td_text = re.sub(r'<[^>]+>', '', tds[i]).strip()
            val = parse_odds_value(td_text)
            if 1.0 <= val <= 50.0:
                open_odds.append(val)
        
        # 提取最新赔率（从链接中提取数字+方向）
        close_odds = []
        close_raw = []
        for i in range(5, 8):  # td[5], td[6], td[7]
            td_html = tds[i]
            # 先提取标签内容
            td_text = re.sub(r'<[^>]+>', '', td_html).strip()
            # 从纯文本中提取赔率数字
            match = re.search(r'(\d+\.?\d*)([↑↓])?', td_text)
            if match:
                val = float(match.group(1))
                direction = match.group(2) or ''
                if 1.0 <= val <= 50.0:
                    close_odds.append(val)
                    close_raw.append(f"{val}{direction}")
            else:
                # 尝试直接从td内容提取
                val = parse_odds_value(td_text)
                if 1.0 <= val <= 50.0:
                    close_odds.append(val)
                    close_raw.append(str(val))
        
        # 需要3个初盘 + 3个最新赔率
        if len(open_odds) >= 3 and len(close_odds) >= 3:
            open_w, open_d, open_l = open_odds[0], open_odds[1], open_odds[2]
            close_w, close_d, close_l = close_odds[0], close_odds[1], close_odds[2]
            
            if is_pinnacle:
                result['pinnacle_open'] = {'w': open_w, 'd': open_d, 'l': open_l}
                result['pinnacle_close'] = {'w': close_w, 'd': close_d, 'l': close_l}
                result['pinnacle_movement'] = {
                    'w': parse_movement(close_raw[0] if len(close_raw) > 0 else ''),
                    'd': parse_movement(close_raw[1] if len(close_raw) > 1 else ''),
                    'l': parse_movement(close_raw[2] if len(close_raw) > 2 else ''),
                }
            elif is_william:
                result['williamhill'] = {'w': open_w, 'd': open_d, 'l': open_l}
            elif is_bet365:
                result['bet365'] = {'w': open_w, 'd': open_d, 'l': open_l}
            elif is_avg and result['pinnacle_open']['w'] == 0:
                # 如果没有平博数据，用平均欧赔作为fallback
                result['pinnacle_open'] = {'w': open_w, 'd': open_d, 'l': open_l}
                result['pinnacle_close'] = {'w': close_w, 'd': close_d, 'l': close_l}
                result['pinnacle_movement'] = {
                    'w': parse_movement(close_raw[0] if len(close_raw) > 0 else ''),
                    'd': parse_movement(close_raw[1] if len(close_raw) > 1 else ''),
                    'l': parse_movement(close_raw[2] if len(close_raw) > 2 else ''),
                }
            
            # SB公司赔率保存
            if is_sb:
                result['sb_odds'] = {'w': close_w, 'd': close_d, 'l': close_l}
            
            # 无论什么公司，平均欧赔都单独保存
            if is_avg:
                result['avg_odds_open'] = {'w': open_w, 'd': open_d, 'l': open_l}
                result['avg_odds_close'] = {'w': close_w, 'd': close_d, 'l': close_l}
    
    # 如果没有平博数据，用SB赔率作为fallback（SB覆盖更全，抽水约5-8%）
    if result['pinnacle_open']['w'] == 0 and result['sb_odds']['w'] > 0:
        result['pinnacle_open'] = result['sb_odds']
        result['pinnacle_close'] = result['sb_odds']
        result['pinnacle_movement'] = {'w': 'stable', 'd': 'stable', 'l': 'stable'}
        print(f"  [SB fallback] 使用SB赔率: {result['sb_odds']}")
    
    # 计算去抽水概率
    if result['pinnacle_open']['w'] > 0:
        implied, margin = calc_implied_prob(
            result['pinnacle_open']['w'],
            result['pinnacle_open']['d'],
            result['pinnacle_open']['l']
        )
        result['implied_prob'] = implied
        result['pinnacle_margin'] = margin
    
    return result



def fetch_pinnacle_odds(date_str=None, include_beidan=True):
    """主函数：获取指定日期的赔率数据

    【纯oyzs数据源】（2026-06-24 改造）：
    - 所有赔率数据（Pinnacle/HKJC/William/Liji/Mingsheng 的 1X2+AH+OU）全部从 oyzs_ajax 获取
    - oyzs走odds.zgzcw.com域名，不受plzx.zgzcw.com的CloudWAF影响
    - 已废弃所有plzx POST请求（Pinnacle company=106, Betfair company=56, 百家平均AH）
    - 已废弃百家平均GET请求（看板不需要）
    - match_list从oddsmagnet缓存获取（含赛程信息+kickoff）
    - oddsmagnet仅作为兜底（oyzs无Pinnacle 1X2时补充）

    Args:
        date_str: 日期字符串，格式 YYYY-MM-DD，默认今天
        include_beidan: 是否同时抓北单oyzs数据（默认True）

    Returns:
        list[dict]: 每场比赛的赔率数据
    """
    if not date_str:
        date_str = datetime.now().strftime('%Y-%m-%d')
    
    print(f"[INFO] 抓取赔率数据: {date_str}")
    
    prev_day = (datetime.strptime(date_str, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
    next_day = (datetime.strptime(date_str, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
    
    # Step 1: 从oddsmagnet缓存获取match_list（赛程+kickoff+基础赔率）
    match_list = []
    for om_date in [prev_day, date_str, next_day]:
        om_file = os.path.join(DATA_BASE_DIR, "data", "raw", "oddsmagnet", f"{om_date.replace('-', '')}.json")
        if os.path.exists(om_file):
            try:
                with open(om_file, 'r', encoding='utf-8') as f:
                    om_data = json.load(f)
                om_matches = om_data.get('matches', {})
                if isinstance(om_matches, dict):
                    for key, m in om_matches.items():
                        info = m.get('info', {})
                        odds_data = m.get('odds', {})
                        avg_o = odds_data.get('avg', {})
                        pin_o = odds_data.get('pinnacle', {})
                        match_list.append({
                            'match_id': f"{info.get('home', '')}_{info.get('away', '')}",
                            'number': info.get('number', ''),
                            'league': info.get('league', ''),
                            'kickoff': info.get('kickoff', ''),
                            'home': info.get('home', ''),
                            'away': info.get('away', ''),
                            'date': om_data.get('date', om_date),
                            'pinnacle_margin': pin_o.get('margin', 0),
                        })
                    print(f"[INFO] 从{os.path.basename(om_file)}加载{len(om_matches)}场")
            except Exception as e:
                print(f"[WARN] 加载{os.path.basename(om_file)}失败: {e}")
    
    if not match_list:
        print("[WARN] 无法获取任何赛程数据")
        return []
    print(f"[INFO] match_list: {len(match_list)}场")
    
    # Step 2: 从 oyzs_ajax 获取所有赔率（Pinnacle/HKJC/William/Liji/Mingsheng 三合一）
    # oyzs走odds.zgzcw.com域名，不受plzx CloudWAF影响
    oyzs_data = {}  # key -> {home, away, companies: {pinnacle: {...}, hkjc: {...}, ...}}
    try:
        from odds_api import fetch_oyzs
        for d in [prev_day, date_str, next_day]:
            oyzs = fetch_oyzs(d)
            if oyzs:
                for key, v in oyzs.items():
                    if key not in oyzs_data:
                        oyzs_data[key] = v
            if include_beidan:
                oyzs_bd = fetch_oyzs(d, page_type='bd')
                if oyzs_bd:
                    for key, v in oyzs_bd.items():
                        if key not in oyzs_data:
                            oyzs_data[key] = v
        oyzs_stats = {'pinnacle': 0, 'hkjc': 0, 'liji': 0, 'mingsheng': 0, 'william': 0}
        for mk, mv in oyzs_data.items():
            for ck in oyzs_stats:
                if mv.get('companies', {}).get(ck):
                    oyzs_stats[ck] += 1
        print(f"[INFO] oyzs数据: {len(oyzs_data)}场 [Pin:{oyzs_stats['pinnacle']} HKJC:{oyzs_stats['hkjc']} William:{oyzs_stats['william']} 利记:{oyzs_stats['liji']} 明升:{oyzs_stats['mingsheng']}]")
    except Exception as e:
        print(f"[WARN] oyzs获取失败: {e}")
    
    # Step 2b: oyzs缓存fallback
    if not oyzs_data:
        oyzs_cache_path = os.path.join(DATA_BASE_DIR, "data", "raw", "oddsmagnet", f"oyzs_{date_str.replace('-','')}.json")
        if os.path.exists(oyzs_cache_path):
            try:
                with open(oyzs_cache_path, 'r', encoding='utf-8') as f:
                    oyzs_raw = json.load(f)
                for key, entry in oyzs_raw.items():
                    if entry.get('pin_ah') or entry.get('pin_ou'):
                        companies = {}
                        if entry.get('pin_ah') or entry.get('pin_ou') or entry.get('pin_1x2'):
                            companies['pinnacle'] = {
                                '1x2': entry.get('pin_1x2', {}),
                                'ah': entry.get('pin_ah', {}),
                                'ou': entry.get('pin_ou', {}),
                            }
                        if entry.get('hkjc_1x2') or entry.get('hkjc_ah') or entry.get('hkjc_ou'):
                            companies['hkjc'] = {
                                '1x2': entry.get('hkjc_1x2', {}),
                                'ah': entry.get('hkjc_ah', {}),
                                'ou': entry.get('hkjc_ou', {}),
                            }
                        if entry.get('liji_1x2') or entry.get('liji_ah') or entry.get('liji_ou'):
                            companies['liji'] = {
                                '1x2': entry.get('liji_1x2', {}),
                                'ah': entry.get('liji_ah', {}),
                                'ou': entry.get('liji_ou', {}),
                            }
                        if entry.get('ms_1x2') or entry.get('ms_ah') or entry.get('ms_ou'):
                            companies['mingsheng'] = {
                                '1x2': entry.get('ms_1x2', {}),
                                'ah': entry.get('ms_ah', {}),
                                'ou': entry.get('ms_ou', {}),
                            }
                        if entry.get('william_1x2') or entry.get('william_ah') or entry.get('william_ou'):
                            companies['william'] = {
                                '1x2': entry.get('william_1x2', {}),
                                'ah': entry.get('william_ah', {}),
                                'ou': entry.get('william_ou', {}),
                            }
                        oyzs_data[key] = {'home': entry.get('home', ''), 'away': entry.get('away', ''), 'companies': companies}
                print(f"[INFO] oyzs缓存: {len(oyzs_data)}场")
            except Exception as e:
                print(f"[WARN] oyzs缓存读取失败: {e}")
    
    # Step 3: oddsmagnet兜底数据（oyzs无Pinnacle 1X2时补充）
    om_fallback = {}
    om_cache_path = os.path.join(DATA_BASE_DIR, "data", "cache", "real_odds.json")
    if os.path.exists(om_cache_path):
        try:
            with open(om_cache_path, 'r', encoding='utf-8') as f:
                om_data = json.load(f)
            if isinstance(om_data, dict):
                for key, item in om_data.items():
                    match_date = item.get('matchDate', '')
                    if date_str and match_date != date_str and match_date != prev_day and match_date != next_day:
                        continue
                    parts = key.split(' vs ')
                    if len(parts) != 2: continue
                    om_fallback[f"{parts[0]}_{parts[1]}"] = item
            print(f"[INFO] oddsmagnet兜底: {len(om_fallback)}场")
        except Exception as e:
            print(f"[WARN] oddsmagnet兜底加载失败: {e}")

    # Step 4: 合并数据
    results = []
    for i, m in enumerate(match_list):
        match_id = m.get('match_id', '')
        home = m.get('home', '?')
        away = m.get('away', '?')
        print(f"[INFO] [{i+1}/{len(match_list)}] {home} vs {away}")
        
        # 查找oddsmagnet兜底数据
        om_key = f"{home}_{away}"
        om_item = om_fallback.get(om_key, {})
        if not om_item:
            for fk, fv in om_fallback.items():
                if home in fk and away in fk:
                    om_item = fv
                    break

        # oyzs数据匹配
        oyzs_key = f"{home}_{away}"
        oyzs_match = oyzs_data.get(oyzs_key, {})
        if not oyzs_match:
            for fk, fv in oyzs_data.items():
                if home in fk and away in fk:
                    oyzs_match = fv
                    break
        oyzs_companies = oyzs_match.get('companies', {})

        # === Pinnacle 1X2 (from oyzs) ===
        pin_oyzs = oyzs_companies.get('pinnacle', {})
        pin_1x2_oyzs = pin_oyzs.get('1x2', {})
        pin_1x2_open_oyzs = pin_1x2_oyzs.get('open', {})
        pin_1x2_close_oyzs = pin_1x2_oyzs.get('close', {})

        pinnacle_open = {}
        pinnacle_close = {}
        pinnacle_movement = {}

        if pin_1x2_open_oyzs.get('w', 0) > 0:
            pinnacle_open = {'w': pin_1x2_open_oyzs['w'], 'd': pin_1x2_open_oyzs.get('d', 0), 'l': pin_1x2_open_oyzs.get('l', 0)}
        if pin_1x2_close_oyzs.get('w', 0) > 0:
            pinnacle_close = {'w': pin_1x2_close_oyzs['w'], 'd': pin_1x2_close_oyzs.get('d', 0), 'l': pin_1x2_close_oyzs.get('l', 0)}
            pinnacle_movement = {'w': 'stable', 'd': 'stable', 'l': 'stable'}

        # oddsmagnet兜底
        if pinnacle_open.get('w', 0) == 0 and om_item:
            pin_ow = om_item.get('pinnacle_open_w', 0) or 0
            pin_od = om_item.get('pinnacle_open_d', 0) or 0
            pin_ol = om_item.get('pinnacle_open_l', 0) or 0
            if pin_ow > 0:
                pinnacle_open = {'w': pin_ow, 'd': pin_od, 'l': pin_ol}
                pinnacle_close = {'w': pin_ow, 'd': pin_od, 'l': pin_ol}
                pinnacle_movement = {'w': 'stable', 'd': 'stable', 'l': 'stable'}
                print(f"  Pinnacle(OM兜底): {pin_ow:.2f}/{pin_od:.2f}/{pin_ol:.2f}")

        # HHAD让球盘检测
        if pinnacle_close.get('d', 0) > 0 and pinnacle_close.get('d', 0) < 2.0:
            print(f"  [WARN] Pinnacle疑似让球盘(d={pinnacle_close['d']:.2f}<2.0)，已丢弃")
            pinnacle_open = {}
            pinnacle_close = {}
            pinnacle_movement = {}
        if pinnacle_open.get('d', 0) > 0 and pinnacle_open.get('d', 0) < 2.0:
            pinnacle_open = {}

        if pinnacle_open.get('w', 0) > 0:
            print(f"  Pinnacle初盘: {pinnacle_open['w']:.2f}/{pinnacle_open['d']:.2f}/{pinnacle_open['l']:.2f}")
            print(f"  Pinnacle最新: {pinnacle_close['w']:.2f}/{pinnacle_close['d']:.2f}/{pinnacle_close['l']:.2f}")

        # === 确定主市场参照 ===
        if pinnacle_open.get('w', 0) > 0:
            m['pinnacle_open'] = pinnacle_open
            m['pinnacle_close'] = pinnacle_close
            m['pinnacle_movement'] = pinnacle_movement
            odds_source = 'Pinnacle'
        else:
            odds_source = '无'

        # === Pinnacle AH + OU (from oyzs) ===
        pin_ah_oyzs = pin_oyzs.get('ah', {})
        pin_ou_oyzs = pin_oyzs.get('ou', {})

        if pin_ah_oyzs.get('close', {}).get('handicap', 0) != 0:
            ah_c = pin_ah_oyzs['close']
            ah_o = pin_ah_oyzs.get('open', {})
            m['pin_ah'] = {
                'handicap': ah_c.get('handicap', 0),
                'home_odd': ah_c.get('home_w', 0),
                'away_odd': ah_c.get('away_w', 0),
            }
            m['pin_ah_open'] = {
                'handicap': ah_o.get('handicap', 0),
                'home_odd': ah_o.get('home_w', 0),
                'away_odd': ah_o.get('away_w', 0),
            }
            print(f"  亚盘(Pin): 盘口{ah_c['handicap']} 主水{ah_c.get('home_w',0):.2f} 客水{ah_c.get('away_w',0):.2f}")
        else:
            m['pin_ah'] = {}
            m['pin_ah_open'] = {}
        m['pin_ou'] = pin_ou_oyzs

        if pin_ou_oyzs.get('close', {}).get('line', 0) != 0:
            ou_c = pin_ou_oyzs['close']
            print(f"  大小球(Pin): {ou_c.get('over',0):.2f}/{ou_c.get('line',0)}/{ou_c.get('under',0):.2f}")

        # === HKJC 1X2 + AH + OU (from oyzs) ===
        hkjc_oyzs = oyzs_companies.get('hkjc', {})
        hkjc_1x2 = hkjc_oyzs.get('1x2', {})
        hkjc_open = hkjc_1x2.get('open', {})
        hkjc_close = hkjc_1x2.get('close', {})

        if hkjc_close.get('d', 0) > 0 and hkjc_close.get('d', 0) < 2.0:
            print(f"  [WARN] HKJC疑似让球盘(d={hkjc_close['d']:.2f}<2.0)，已丢弃")
            hkjc_open = {}
            hkjc_close = {}

        m['hkjc_open'] = hkjc_open
        m['hkjc_close'] = hkjc_close

        if hkjc_open.get('w', 0) > 0:
            print(f"  HKJC初盘: {hkjc_open['w']:.2f}/{hkjc_open['d']:.2f}/{hkjc_open['l']:.2f}")
            print(f"  HKJC最新: {hkjc_close['w']:.2f}/{hkjc_close['d']:.2f}/{hkjc_close['l']:.2f}")

        hkjc_ah_data = hkjc_oyzs.get('ah', {})
        hkjc_ou_data = hkjc_oyzs.get('ou', {})
        m['ah_hkjc_open'] = hkjc_ah_data.get('open', {})
        m['ah_hkjc_close'] = hkjc_ah_data.get('close', {})
        m['hkjc_ou'] = hkjc_ou_data

        if hkjc_ah_data.get('close', {}).get('handicap', 0) != 0:
            ah_c = hkjc_ah_data['close']
            print(f"  亚盘(HKJC): 盘口{ah_c['handicap']} 主水{ah_c.get('home_w',0):.2f} 客水{ah_c.get('away_w',0):.2f}")
        if hkjc_ou_data.get('close', {}).get('line', 0) != 0:
            ou_c = hkjc_ou_data['close']
            print(f"  大小球(HKJC): {ou_c.get('over',0):.2f}/{ou_c.get('line',0)}/{ou_c.get('under',0):.2f}")

        # === William 1X2 + AH + OU (from oyzs) ===
        william_oyzs = oyzs_companies.get('william') or oyzs_companies.get('威廉希尔') or {}
        william_1x2 = william_oyzs.get('1x2', {})
        william_1x2_open = william_1x2.get('open', {})
        william_1x2_close = william_1x2.get('close', {})
        m['william_1x2_open'] = william_1x2_open
        m['william_1x2_close'] = william_1x2_close
        m['william_ah'] = william_oyzs.get('ah', {})
        m['william_ou'] = william_oyzs.get('ou', {})
        if william_1x2_close.get('w', 0) > 0:
            print(f"  威廉初盘: {william_1x2_open.get('w',0):.2f}/{william_1x2_open.get('d',0):.2f}/{william_1x2_open.get('l',0):.2f}")
            print(f"  威廉终盘: {william_1x2_close['w']:.2f}/{william_1x2_close['d']:.2f}/{william_1x2_close['l']:.2f}")

        # === 利记/明升 1X2 + AH + OU (from oyzs) ===
        liji_oyzs = oyzs_companies.get('liji') or oyzs_companies.get('利记') or {}
        ms_oyzs = oyzs_companies.get('mingsheng') or oyzs_companies.get('明升') or oyzs_companies.get('sb') or {}
        m['liji_1x2'] = liji_oyzs.get('1x2', {})
        m['ms_1x2'] = ms_oyzs.get('1x2', {})
        m['ou_liji'] = liji_oyzs.get('ou', {})
        m['ou_ms'] = ms_oyzs.get('ou', {})
        m['liji_ah'] = liji_oyzs.get('ah', {})
        m['ms_ah'] = ms_oyzs.get('ah', {})

        if ms_oyzs.get('ah', {}).get('close', {}).get('handicap', 0) != 0:
            ah_c = ms_oyzs['ah']['close']
            print(f"  亚盘(明升): 盘口{ah_c['handicap']} 主水{ah_c.get('home_w',0):.2f} 客水{ah_c.get('away_w',0):.2f}")

        # === 计算去抽水概率 ===
        pin_open = m.get('pinnacle_open', {})
        if pin_open.get('w', 0) > 0:
            implied, margin = calc_implied_prob(pin_open['w'], pin_open['d'], pin_open['l'])
            m['implied_prob'] = implied
            m['pinnacle_margin'] = margin
            print(f"  市场参照: {odds_source} | 抽水: {margin:.2%} | 隐含概率: {implied['w']:.1%}/{implied['d']:.1%}/{implied['l']:.1%}")
        else:
            print(f"  市场参照: {odds_source}")
        
        m['odds_source'] = odds_source
        m['date'] = date_str
        results.append(m)
    
    print(f"\n[INFO] 完成，共 {len(results)} 场")
    pin_count = sum(1 for r in results if r.get('odds_source') == 'Pinnacle')
    none_count = sum(1 for r in results if r.get('odds_source') == '无')
    print(f"[INFO] Pinnacle: {pin_count}场, 无数据: {none_count}场")

    # Step 7: 补充oddsmagnet中不在match_list里的场次
    if om_fallback:
        existing_teams = set()
        for r in results:
            h = r.get('home', '')
            a = r.get('away', '')
            existing_teams.add(f"{h}_{a}")
        
        om_added = 0
        for om_key, om_item in om_fallback.items():
            if om_key in existing_teams:
                continue
            parts = om_key.split('_')
            if len(parts) != 2:
                continue
            home, away = parts[0], parts[1]
            
            pin_ow = om_item.get('pinnacle_open_w', 0) or 0
            pin_od = om_item.get('pinnacle_open_d', 0) or 0
            pin_ol = om_item.get('pinnacle_open_l', 0) or 0
            avg_w = om_item.get('home', 0) or 0
            avg_d = om_item.get('draw', 0) or 0
            avg_l = om_item.get('away', 0) or 0
            hkjc_ow = om_item.get('hkjc_open_w', 0) or 0
            hkjc_od = om_item.get('hkjc_open_d', 0) or 0
            hkjc_ol = om_item.get('hkjc_open_l', 0) or 0
            
            if pin_ow == 0 and avg_w == 0:
                continue
            
            m = {
                'home': home, 'away': away,
                'match_id': om_item.get('matchNum', ''),
                'number': om_item.get('matchNum', ''),
                'league': om_item.get('league', ''),
                'kickoff': om_item.get('kickoff', ''),
                'date': om_item.get('matchDate', ''),
                'odds_source': 'oddsmagnet',
                'avg_open': {'w': avg_w, 'd': avg_d, 'l': avg_l},
                'avg_close': {'w': avg_w, 'd': avg_d, 'l': avg_l},
                'avg_movement': {'w': 'stable', 'd': 'stable', 'l': 'stable'},
                'pinnacle_open': {'w': pin_ow, 'd': pin_od, 'l': pin_ol},
                'pinnacle_close': {'w': pin_ow, 'd': pin_od, 'l': pin_ol},
                'pinnacle_movement': {'w': 'stable', 'd': 'stable', 'l': 'stable'},
                'hkjc_open': {'w': hkjc_ow, 'd': hkjc_od, 'l': hkjc_ol},
                'hkjc_close': {'w': hkjc_ow, 'd': hkjc_od, 'l': hkjc_ol},
                'betfair_open': {}, 'betfair_close': {},
                'avg_odds_open': {'w': avg_w, 'd': avg_d, 'l': avg_l},
                'avg_odds_close': {'w': avg_w, 'd': avg_d, 'l': avg_l},
            }
            
            if pin_ow > 0:
                implied, margin = calc_implied_prob(pin_ow, pin_od, pin_ol)
                m['implied_prob'] = implied
                m['pinnacle_margin'] = margin
                m['odds_source'] = 'Pinnacle'
            elif avg_w > 0:
                implied, margin = calc_implied_prob(avg_w, avg_d, avg_l)
                m['implied_prob'] = implied
                m['pinnacle_margin'] = margin
                m['odds_source'] = '百家平均'
            
            results.append(m)
            om_added += 1
        
        if om_added > 0:
            print(f"[INFO] oddsmagnet补充{om_added}场缺失场次")
            # 重新计数
            pin_count = sum(1 for r in results if r.get('odds_source') == 'Pinnacle')
            none_count = sum(1 for r in results if r.get('odds_source') == '无')

    print(f"[INFO] Pinnacle: {pin_count}场, 无数据: {none_count}场")
    
    # 保存缓存文件
    cache_dir = os.path.join(DATA_BASE_DIR, "data", "cache")
    os.makedirs(cache_dir, exist_ok=True)
    now_str = datetime.now().strftime('%H%M')
    cache_path = os.path.join(cache_dir, f"pinnacle_odds_{date_str.replace('-','')}_{now_str}.json")
    cache_data = {
        'date': date_str,
        'fetch_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'summary': {
            'total': len(results),
            'pinnacle': pin_count,
                        'none': none_count,
        },
        'matches': results
    }
    with open(cache_path, 'w', encoding='utf-8') as f:
        json.dump(cache_data, f, ensure_ascii=False, indent=2, default=str)
    print(f"[INFO] 缓存已保存: {cache_path}")
    
    return results


def load_oddsmagnet_fallback(date_str=None):
    """当zgzcw.com被WAF拦截时，从oddsmagnet缓存(real_odds.json)提取赔率数据
    
    Returns:
        list: 与fetch_odds_data格式兼容的match列表
    """
    cache_path = os.path.join(DATA_BASE_DIR, "data", "cache", "real_odds.json")
    if not os.path.exists(cache_path):
        print("[WARN] oddsmagnet缓存不存在，无法fallback")
        return []
    
    try:
        with open(cache_path, 'r', encoding='utf-8') as f:
            odds_data = json.load(f)
    except Exception as e:
        print(f"[WARN] 读取oddsmagnet缓存失败: {e}")
        return []
    
    if not isinstance(odds_data, dict):
        print("[WARN] oddsmagnet缓存格式异常")
        return []
    
    results = []
    for key, item in odds_data.items():
        # 过滤日期
        match_date = item.get('matchDate', '')
        if date_str and match_date != date_str:
            # 也包含前一天+次日（48小时范围，覆盖12:00边界比赛如6-21 12:00突尼斯vs日本）
            try:
                from datetime import datetime, timedelta
                target = datetime.strptime(date_str, '%Y-%m-%d')
                prev_day = (target - timedelta(days=1)).strftime('%Y-%m-%d')
                next_day = (target + timedelta(days=1)).strftime('%Y-%m-%d')
                if match_date != prev_day and match_date != next_day:
                    continue
            except:
                continue
        
        # 从key解析队名 "主队 vs 客队"
        parts = key.split(' vs ')
        if len(parts) != 2:
            continue
        home = parts[0].strip()
        away = parts[1].strip()
        
        # 提取Pinnacle赔率
        pin_ow = item.get('pinnacle_open_w', 0) or 0
        pin_od = item.get('pinnacle_open_d', 0) or 0
        pin_ol = item.get('pinnacle_open_l', 0) or 0
        
        # 提取百家平均赔率
        avg_w = item.get('home', 0) or 0
        avg_d = item.get('draw', 0) or 0
        avg_l = item.get('away', 0) or 0
        
        # 提取HKJC赔率
        hkjc_ow = item.get('hkjc_open_w', 0) or 0
        hkjc_od = item.get('hkjc_open_d', 0) or 0
        hkjc_ol = item.get('hkjc_open_l', 0) or 0
        
        match = {
            'home': home,
            'away': away,
            'match_id': item.get('matchNum', ''),
            'number': item.get('matchNum', ''),
            'league': item.get('league', ''),
            'kickoff': item.get('kickoff', ''),
            'date': match_date,
            'odds_source': 'oddsmagnet_fallback',
            # 百家平均
            'avg_open': {'w': avg_w, 'd': avg_d, 'l': avg_l},
            'avg_close': {'w': avg_w, 'd': avg_d, 'l': avg_l},
            'avg_movement': {'w': 'stable', 'd': 'stable', 'l': 'stable'},
            # Pinnacle
            'pinnacle_open': {'w': pin_ow, 'd': pin_od, 'l': pin_ol},
            'pinnacle_close': {'w': pin_ow, 'd': pin_od, 'l': pin_ol},
            'pinnacle_movement': {'w': 'stable', 'd': 'stable', 'l': 'stable'},
            'pinnacle_margin': item.get('avg_margin', 0),
            # HKJC
            'hkjc_open': {'w': hkjc_ow, 'd': hkjc_od, 'l': hkjc_ol},
            'hkjc_close': {'w': hkjc_ow, 'd': hkjc_od, 'l': hkjc_ol},
        }
        
        # 确定主市场参照
        if pin_ow > 0:
            implied, margin = calc_implied_prob(pin_ow, pin_od, pin_ol)
            match['implied_prob'] = implied
            match['pinnacle_margin'] = margin
            match['odds_source'] = 'Pinnacle'
        elif avg_w > 0:
            implied, margin = calc_implied_prob(avg_w, avg_d, avg_l)
            match['implied_prob'] = implied
            match['pinnacle_margin'] = margin
            match['odds_source'] = '百家平均'
        else:
            match['implied_prob'] = {'w': 0, 'd': 0, 'l': 0}
        
        results.append(match)
    
    print(f"[INFO] oddsmagnet fallback: {len(results)} 场 (含Pinnacle: {sum(1 for r in results if r.get('odds_source')=='Pinnacle')})")
    return results


def save_to_db(matches, db_path, date_str=None):
    """将赔率数据写入数据库

    匹配策略（v2）：
    1. 按开赛时间分组，同时间段内做队名匹配（容忍不同中文译名）
    2. 队名匹配：正向+反向，阈值0.3（跨数据源中文译名差异大）
    3. 时间精确匹配优先，时间±30分钟兜底
    """
    if not matches:
        return 0

    def _or_none(d, *check_keys, k):
        """通用 helper：dict 空或所有 check_keys 字段都 0 → None；否则返回 d.get(k)。

        用于亚盘/OU 段写入：dict 三个相关字段全 0（视作"没抓到"）→ 返回 None，
        COALESCE(?, col) 在 SQL 端会保留 DB 旧值；真数据则正常返回写入。
        """
        if not d:
            return None
        if all((d.get(ck, 0) or 0) == 0 for ck in check_keys):
            return None
        return d.get(k)

    # 兼容 oddsmagnet/oyzs 缓存格式：将嵌套的 {open:{...}, close:{...}} 转为扁平结构
    # fetch_pinnacle_odds() 正常执行时会产出扁平格式，但 --fetch-only 保存的缓存
    # 和 odds_api.py 保存的 oyzs 缓存保留了原始嵌套格式
    def _flatten_oyzs_match(m):
        """将 oddsmagnet/oyzs 格式的 match dict 转为 save_to_db 期望的扁平格式"""
        # 检测是否是 oddsmagnet 格式（有 info + odds 顶层键）
        if 'info' in m and 'odds' in m:
            info = m.get('info', {})
            odds = m.get('odds', {})
            pin_odds = odds.get('pinnacle', {})
            avg_odds = odds.get('avg', {})
            hkjc_odds = odds.get('hkjc', {})

            m['home'] = m.get('home') or info.get('home', '')
            m['away'] = m.get('away') or info.get('away', '')
            m['kickoff'] = m.get('kickoff') or info.get('kickoff', '')
            m['league'] = m.get('league') or info.get('league', '')

            if pin_odds.get('odds_w', 0) > 0:
                m['pinnacle_open'] = {'w': pin_odds.get('open_w', 0), 'd': pin_odds.get('open_d', 0), 'l': pin_odds.get('open_l', 0)}
                m['pinnacle_close'] = {'w': pin_odds.get('odds_w', 0), 'd': pin_odds.get('odds_d', 0), 'l': pin_odds.get('odds_l', 0)}
                m['pinnacle_margin'] = pin_odds.get('margin', 0)
                m['implied_prob'] = {'w': pin_odds.get('implied_prob_w', 0), 'd': pin_odds.get('implied_prob_d', 0), 'l': pin_odds.get('implied_prob_l', 0)}
                m['odds_source'] = 'Pinnacle'

            if avg_odds.get('odds_w', 0) > 0:
                m['avg_odds_open'] = {'w': avg_odds.get('open_w', 0), 'd': avg_odds.get('open_d', 0), 'l': avg_odds.get('open_l', 0)}
                m['avg_odds_close'] = {'w': avg_odds.get('odds_w', 0), 'd': avg_odds.get('odds_d', 0), 'l': avg_odds.get('odds_l', 0)}

            if hkjc_odds.get('odds_w', 0) > 0:
                m['hkjc_open'] = {'w': hkjc_odds.get('open_w', 0), 'd': hkjc_odds.get('open_d', 0), 'l': hkjc_odds.get('open_l', 0)}
                m['hkjc_close'] = {'w': hkjc_odds.get('odds_w', 0), 'd': hkjc_odds.get('odds_d', 0), 'l': hkjc_odds.get('odds_l', 0)}

            will_odds = odds.get('william', odds.get('威廉希尔', {}))
            if will_odds.get('odds_w', 0) > 0:
                m['william_1x2_open'] = {'w': will_odds.get('open_w', 0), 'd': will_odds.get('open_d', 0), 'l': will_odds.get('open_l', 0)}
                m['william_1x2_close'] = {'w': will_odds.get('odds_w', 0), 'd': will_odds.get('odds_d', 0), 'l': will_odds.get('odds_l', 0)}

        # 检测 AH 字段是否为 {open:{...}, close:{...}} 嵌套格式并展平
        # pin_ah: save_to_db 用 m.get('pin_ah', {}) 取值，期望扁平 {handicap, home_odd, away_odd}
        pin_ah_val = m.get('pin_ah')
        if isinstance(pin_ah_val, dict) and 'close' in pin_ah_val and 'open' in pin_ah_val:
            close = pin_ah_val['close']
            opn = pin_ah_val['open']
            hc = close.get('handicap', 0)
            if hc != 0:
                m['pin_ah'] = {'handicap': hc, 'home_odd': close.get('home_w', 0), 'away_odd': close.get('away_w', 0)}
                m['pin_ah_open'] = {'handicap': opn.get('handicap', 0), 'home_odd': opn.get('home_w', 0), 'away_odd': opn.get('away_w', 0)}

        # hkjc_ah: save_to_db 用 m.get('ah_hkjc_close', {}) 和 m.get('ah_hkjc_open', {})
        hkjc_ah_val = m.get('hkjc_ah')
        if isinstance(hkjc_ah_val, dict) and 'close' in hkjc_ah_val and 'open' in hkjc_ah_val:
            m['ah_hkjc_close'] = hkjc_ah_val['close']
            m['ah_hkjc_open'] = hkjc_ah_val['open']

        # liji_ah, ms_ah, william_ah 保持嵌套格式，save_to_db 用 .get('close', {}) 取值

        # 缓存key到save_to_db期望key的映射
        key_map = {
            'liji_ou': 'ou_liji',
            'ms_ou': 'ou_ms',
            'hkjc_ou': 'hkjc_ou',  # same
        }
        for src_key, dst_key in key_map.items():
            if src_key in m and dst_key not in m:
                m[dst_key] = m[src_key]

        return m

    matches = [_flatten_oyzs_match(m) for m in matches]

    # 确保目录存在
    os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else '.', exist_ok=True)
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    updated = 0
    
    # 确保亚盘字段存在
    for col, ctype in [('ah_handicap', 'REAL'), ('ah_home_water', 'REAL'), ('ah_away_water', 'REAL'), ('ah_source', 'TEXT')]:
        try:
            cursor.execute(f"ALTER TABLE poisson_predictions ADD COLUMN {col} {ctype}")
        except:
            pass  # 列已存在

    # 确保Pinnacle亚盘/大小球 + 百家平均/利记/明升大小球字段存在
    new_columns = [
        ('pin_ah_handicap', 'REAL'), ('pin_ah_home_water', 'REAL'), ('pin_ah_away_water', 'REAL'),
        ('pin_ou_line', 'REAL'), ('pin_ou_over', 'REAL'), ('pin_ou_under', 'REAL'),
        ('ou_over', 'REAL'), ('ou_line', 'REAL'), ('ou_under', 'REAL'),
        ('ou_open_over', 'REAL'), ('ou_open_line', 'REAL'), ('ou_open_under', 'REAL'),
        ('liji_ou_over', 'REAL'), ('liji_ou_line', 'REAL'), ('liji_ou_under', 'REAL'),
        ('liji_ou_open_over', 'REAL'), ('liji_ou_open_line', 'REAL'), ('liji_ou_open_under', 'REAL'),
        ('ms_ou_over', 'REAL'), ('ms_ou_line', 'REAL'), ('ms_ou_under', 'REAL'),
        ('ms_ou_open_over', 'REAL'), ('ms_ou_open_line', 'REAL'), ('ms_ou_open_under', 'REAL'),
        # 利记/明升 1X2 欧赔
        ('liji_1x2_w', 'REAL'), ('liji_1x2_d', 'REAL'), ('liji_1x2_l', 'REAL'),
        ('liji_1x2_open_w', 'REAL'), ('liji_1x2_open_d', 'REAL'), ('liji_1x2_open_l', 'REAL'),
        ('ms_1x2_w', 'REAL'), ('ms_1x2_d', 'REAL'), ('ms_1x2_l', 'REAL'),
        ('ms_1x2_open_w', 'REAL'), ('ms_1x2_open_d', 'REAL'), ('ms_1x2_open_l', 'REAL'),
    ]
    for col, ctype in new_columns:
        try:
            cursor.execute(f"ALTER TABLE poisson_predictions ADD COLUMN {col} {ctype}")
        except:
            pass  # 列已存在
    
    # 获取目标日期，使用时间窗口匹配（12:00~次日11:59）
    target_date = date_str or (matches[0].get('date') if matches else None)
    if not target_date:
        conn.close()
        return 0
    
    # 计算时间窗口：前一天12:00 ~ 次日12:06（覆盖12:00整点边界比赛，如世界杯6-21 12:00突尼斯vs日本）
    prev_day = (datetime.strptime(target_date, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
    next_day = (datetime.strptime(target_date, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
    window_start = f"{prev_day} 12:00"
    window_end = f"{next_day} 12:06"  # 2026-06-20 改造：11:59 -> 12:06，覆盖12:00整点边界
    
    # 从数据库读取时间窗口内的所有记录
    cursor.execute("""
        SELECT id, home_team, away_team, kickoff_time 
        FROM poisson_predictions 
        WHERE kickoff_time >= ? AND kickoff_time <= ?
    """, (window_start, window_end))
    db_records = cursor.fetchall()
    
    if not db_records:
        print(f"[WARN] 数据库中没有 {target_date} 或 {next_day} 的记录")
        conn.close()
        return 0
    
    print(f"[INFO] 数据库中 {window_start}~{window_end} 共有 {len(db_records)} 条记录")
    
    # 已匹配的DB记录ID，防止重复匹配
    matched_db_ids = set()
    
    for m in matches:
        home = m.get('home', '')
        away = m.get('away', '')
        kickoff = m.get('kickoff', '')
        
        # 解析web页面的开赛时间 -> 标准datetime
        web_time = None
        if kickoff:
            try:
                # 格式: "05-21 01:00"
                web_time = datetime.strptime(f"{target_date[:4]}-{kickoff}", "%Y-%m-%d %H:%M")
            except:
                pass
        
        # 策略1: 精确时间匹配 + 弱队名验证
        best_match = None
        best_score = 0
        match_method = ''
        
        for record in db_records:
            record_id, db_home, db_away, db_time = record[:4]
            if record_id in matched_db_ids:
                continue
            
            # 计算队名相似度
            sim_home = team_name_similarity(home, db_home)
            sim_away = team_name_similarity(away, db_away)
            avg_sim = (sim_home + sim_away) / 2
            
            # 反向匹配
            sim_home_rev = team_name_similarity(home, db_away)
            sim_away_rev = team_name_similarity(away, db_home)
            avg_sim_rev = (sim_home_rev + sim_away_rev) / 2
            
            name_sim = max(avg_sim, avg_sim_rev)
            
            # 时间匹配度
            time_match = 0
            if web_time and db_time:
                try:
                    db_dt = datetime.strptime(db_time, "%Y-%m-%d %H:%M")
                    diff_minutes = abs((web_time - db_dt).total_seconds()) / 60
                    if diff_minutes <= 5:
                        time_match = 1.0  # 精确
                    elif diff_minutes <= 30:
                        time_match = 0.7  # 接近
                    elif diff_minutes <= 60:
                        time_match = 0.3  # 较远
                except:
                    pass
            
            # 综合评分：时间权重高，队名验证
            if time_match >= 1.0 and name_sim >= 0.3:
                score = 0.6 * time_match + 0.4 * name_sim
                if score > best_score:
                    best_score = score
                    best_match = record
                    match_method = f'时间精确+队名{name_sim:.2f}'
            elif time_match >= 0.7 and name_sim >= 0.2:
                score = 0.6 * time_match + 0.4 * name_sim
                if score > best_score:
                    best_score = score
                    best_match = record
                    match_method = f'时间近+队名{name_sim:.2f}'
            elif name_sim >= 0.6:
                # 队名强匹配（旧逻辑兜底）
                score = 0.3 * time_match + 0.7 * name_sim
                if score > best_score:
                    best_score = score
                    best_match = record
                    match_method = f'队名强{name_sim:.2f}'
        
        # 兜底：宽松队名匹配（sim>=0.4），无需时间匹配
        if not best_match or best_score < 0.3:
            for record in db_records:
                record_id, db_home, db_away, db_time = record[:4]
                if record_id in matched_db_ids:
                    continue
                sim_home = team_name_similarity(home, db_home)
                sim_away = team_name_similarity(away, db_away)
                avg_sim = (sim_home + sim_away) / 2
                # 反向
                sim_home_rev = team_name_similarity(home, db_away)
                sim_away_rev = team_name_similarity(away, db_home)
                avg_sim_rev = (sim_home_rev + sim_away_rev) / 2
                name_sim = max(avg_sim, avg_sim_rev)
                if name_sim >= 0.4 and name_sim > best_score:
                    best_score = name_sim
                    best_match = record
                    match_method = f'队名宽松{name_sim:.2f}'
        
        if not best_match or best_score < 0.3:
            print(f"[WARN] {home} vs {away} ({kickoff}) 无匹配，跳过")
            continue
        
        record_id = best_match[0]
        db_home = best_match[1]
        db_away = best_match[2]
        matched_db_ids.add(record_id)

        print(f"[MATCH] {home} vs {away} -> DB {db_home} vs {db_away} [{match_method}] 评分={best_score:.2f}")

        pin_open = m.get('pinnacle_open', {})
        pin_close = m.get('pinnacle_close', {})
        pin_movement = m.get('pinnacle_movement', {})

        implied = m.get('implied_prob', {})
        margin = m.get('pinnacle_margin', 0)
        
        # 百家平均欧赔
        avg_open = m.get('avg_odds_open', m.get('avg_open', {}))
        avg_close = m.get('avg_odds_close', m.get('avg_close', {}))
        
        # 香港马会欧赔 (from oyzs)
        hkjc_open = m.get('hkjc_open', {})
        hkjc_close = m.get('hkjc_close', {})

        # 亚盘让球盘（百家平均）
        ah_close = m.get('ah_avg_close') or {}
        ah_source = 'avg' if m.get('ah_avg_close') else ''

        # Pinnacle亚盘+大小球 (from oyzs)
        pin_ah = m.get('pin_ah', {})
        pin_ah_open = m.get('pin_ah_open', {})
        pin_ou = m.get('pin_ou', {})

        # HKJC AH+OU (from oyzs)
        hkjc_ah = m.get('hkjc_ou', {}).get('close', {}) and m.get('ah_hkjc_close', {}) or {}
        hkjc_ah_open_data = m.get('ah_hkjc_open', {})
        hkjc_ah_close_data = m.get('ah_hkjc_close', {})
        hkjc_ou_data = m.get('hkjc_ou', {})

        # 利记/明升 大小球 (from oyzs)
        ou_liji_m = m.get('ou_liji', {})
        ou_ms_m = m.get('ou_ms', {})

        # 利记/明升 亚盘 (from oyzs)
        liji_ah_data = m.get('liji_ah', {})
        ms_ah_data = m.get('ms_ah', {})

        # 威廉希尔 1X2 + AH (from oyzs)
        william_1x2_open = m.get('william_1x2_open', {})
        william_1x2_close = m.get('william_1x2_close', {})
        william_ah_data = m.get('william_ah', {})
        # 用close写入william_1x2_w/d/l（与fetch_data.py一致）
        w1x2_w = william_1x2_close.get('w') or None
        w1x2_d = william_1x2_close.get('d') or None
        w1x2_l = william_1x2_close.get('l') or None

        # 利记/明升 1X2 (from oyzs)
        liji_1x2 = m.get('liji_1x2', {})
        liji_1x2_open = liji_1x2.get('open', {})
        liji_1x2_close = liji_1x2.get('close', {})
        ms_1x2 = m.get('ms_1x2', {})
        ms_1x2_open = ms_1x2.get('open', {})
        ms_1x2_close = ms_1x2.get('close', {})

        # 提取大小球即时/初盘数据
        liji_ou_close = ou_liji_m.get('close', {})
        liji_ou_open = ou_liji_m.get('open', {})
        ms_ou_close = ou_ms_m.get('close', {})
        ms_ou_open = ou_ms_m.get('open', {})

        # HKJC OU
        hkjc_ou_close = hkjc_ou_data.get('close', {})
        hkjc_ou_open = hkjc_ou_data.get('open', {})

        # 保护已有非零ah数据：只在DB原值为0/NULL时才写入新值（防止WAF拦截0值覆盖Termux完整数据）
        ah_hc_val = _or_none(ah_close, 'handicap', 'home_w', 'away_w', k='handicap')
        ah_hw_val = _or_none(ah_close, 'handicap', 'home_w', 'away_w', k='home_w')
        ah_aw_val = _or_none(ah_close, 'handicap', 'home_w', 'away_w', k='away_w')
        pin_ah_hc_val = _or_none(pin_ah, 'handicap', 'home_odd', 'away_odd', k='handicap')
        pin_ah_hw_val = _or_none(pin_ah, 'handicap', 'home_odd', 'away_odd', k='home_odd')
        pin_ah_aw_val = _or_none(pin_ah, 'handicap', 'home_odd', 'away_odd', k='away_odd')
        # Pinnacle AH open
        pin_ah_open_hc_val = _or_none(pin_ah_open, 'handicap', 'home_odd', 'away_odd', k='handicap')
        pin_ah_open_hw_val = _or_none(pin_ah_open, 'handicap', 'home_odd', 'away_odd', k='home_odd')
        pin_ah_open_aw_val = _or_none(pin_ah_open, 'handicap', 'home_odd', 'away_odd', k='away_odd')
        # Pinnacle OU
        pin_ou_close = pin_ou.get('close', {})
        pin_ou_open_data = pin_ou.get('open', {})
        pin_ou_line_val = _or_none(pin_ou_close, 'line', 'over', 'under', k='line')
        pin_ou_over_val = _or_none(pin_ou_close, 'line', 'over', 'under', k='over')
        pin_ou_under_val = _or_none(pin_ou_close, 'line', 'over', 'under', k='under')
        pin_ou_open_line_val = _or_none(pin_ou_open_data, 'line', 'over', 'under', k='line')
        pin_ou_open_over_val = _or_none(pin_ou_open_data, 'line', 'over', 'under', k='over')
        pin_ou_open_under_val = _or_none(pin_ou_open_data, 'line', 'over', 'under', k='under')
        # HKJC AH
        hkjc_ah_hc_val = _or_none(hkjc_ah_close_data, 'handicap', 'home_w', 'away_w', k='handicap')
        hkjc_ah_hw_val = _or_none(hkjc_ah_close_data, 'handicap', 'home_w', 'away_w', k='home_w')
        hkjc_ah_aw_val = _or_none(hkjc_ah_close_data, 'handicap', 'home_w', 'away_w', k='away_w')
        hkjc_ah_open_hc_val = _or_none(hkjc_ah_open_data, 'handicap', 'home_w', 'away_w', k='handicap')
        hkjc_ah_open_hw_val = _or_none(hkjc_ah_open_data, 'handicap', 'home_w', 'away_w', k='home_w')
        hkjc_ah_open_aw_val = _or_none(hkjc_ah_open_data, 'handicap', 'home_w', 'away_w', k='away_w')
        # HKJC OU
        hkjc_ou_line_val = _or_none(hkjc_ou_close, 'line', 'over', 'under', k='line')
        hkjc_ou_over_val = _or_none(hkjc_ou_close, 'line', 'over', 'under', k='over')
        hkjc_ou_under_val = _or_none(hkjc_ou_close, 'line', 'over', 'under', k='under')
        hkjc_ou_open_line_val = _or_none(hkjc_ou_open, 'line', 'over', 'under', k='line')
        hkjc_ou_open_over_val = _or_none(hkjc_ou_open, 'line', 'over', 'under', k='over')
        hkjc_ou_open_under_val = _or_none(hkjc_ou_open, 'line', 'over', 'under', k='under')
        # 利记 OU
        liji_ou_close_o = _or_none(liji_ou_close, 'over', 'line', 'under', k='over')
        liji_ou_close_l = _or_none(liji_ou_close, 'over', 'line', 'under', k='line')
        liji_ou_close_u = _or_none(liji_ou_close, 'over', 'line', 'under', k='under')
        liji_ou_open_o = _or_none(liji_ou_open, 'over', 'line', 'under', k='over')
        liji_ou_open_l = _or_none(liji_ou_open, 'over', 'line', 'under', k='line')
        liji_ou_open_u = _or_none(liji_ou_open, 'over', 'line', 'under', k='under')
        # 明升 OU
        ms_ou_close_o = _or_none(ms_ou_close, 'over', 'line', 'under', k='over')
        ms_ou_close_l = _or_none(ms_ou_close, 'over', 'line', 'under', k='line')
        ms_ou_close_u = _or_none(ms_ou_close, 'over', 'line', 'under', k='under')
        ms_ou_open_o = _or_none(ms_ou_open, 'over', 'line', 'under', k='over')
        ms_ou_open_l = _or_none(ms_ou_open, 'over', 'line', 'under', k='line')
        ms_ou_open_u = _or_none(ms_ou_open, 'over', 'line', 'under', k='under')
        # 利记/明升 AH
        liji_ah_hc_val = _or_none(liji_ah_data.get('close', {}), 'handicap', 'home_w', 'away_w', k='handicap')
        liji_ah_hw_val = _or_none(liji_ah_data.get('close', {}), 'handicap', 'home_w', 'away_w', k='home_w')
        liji_ah_aw_val = _or_none(liji_ah_data.get('close', {}), 'handicap', 'home_w', 'away_w', k='away_w')
        liji_ah_open_hc_val = _or_none(liji_ah_data.get('open', {}), 'handicap', 'home_w', 'away_w', k='handicap')
        liji_ah_open_hw_val = _or_none(liji_ah_data.get('open', {}), 'handicap', 'home_w', 'away_w', k='home_w')
        liji_ah_open_aw_val = _or_none(liji_ah_data.get('open', {}), 'handicap', 'home_w', 'away_w', k='away_w')
        ms_ah_hc_val = _or_none(ms_ah_data.get('close', {}), 'handicap', 'home_w', 'away_w', k='handicap')
        ms_ah_hw_val = _or_none(ms_ah_data.get('close', {}), 'handicap', 'home_w', 'away_w', k='home_w')
        ms_ah_aw_val = _or_none(ms_ah_data.get('close', {}), 'handicap', 'home_w', 'away_w', k='away_w')
        ms_ah_open_hc_val = _or_none(ms_ah_data.get('open', {}), 'handicap', 'home_w', 'away_w', k='handicap')
        ms_ah_open_hw_val = _or_none(ms_ah_data.get('open', {}), 'handicap', 'home_w', 'away_w', k='home_w')
        ms_ah_open_aw_val = _or_none(ms_ah_data.get('open', {}), 'handicap', 'home_w', 'away_w', k='away_w')
        # 威廉希尔 AH
        william_ah_hc_val = _or_none(william_ah_data.get('close', {}), 'handicap', 'home_w', 'away_w', k='handicap')
        william_ah_hw_val = _or_none(william_ah_data.get('close', {}), 'handicap', 'home_w', 'away_w', k='home_w')
        william_ah_aw_val = _or_none(william_ah_data.get('close', {}), 'handicap', 'home_w', 'away_w', k='away_w')
        william_ah_open_hc_val = _or_none(william_ah_data.get('open', {}), 'handicap', 'home_w', 'away_w', k='handicap')
        william_ah_open_hw_val = _or_none(william_ah_data.get('open', {}), 'handicap', 'home_w', 'away_w', k='home_w')
        william_ah_open_aw_val = _or_none(william_ah_data.get('open', {}), 'handicap', 'home_w', 'away_w', k='away_w')

        cursor.execute("""
            UPDATE poisson_predictions SET
                pinnacle_open_w = ?, pinnacle_open_d = ?, pinnacle_open_l = ?,
                pinnacle_close_w = ?, pinnacle_close_d = ?, pinnacle_close_l = ?,
                pinnacle_movement = ?, pinnacle_margin = ?,
                implied_prob_w = ?, implied_prob_d = ?, implied_prob_l = ?,
                avg_odds_open_w = ?, avg_odds_open_d = ?, avg_odds_open_l = ?,
                avg_odds_close_w = ?, avg_odds_close_d = ?, avg_odds_close_l = ?,
                hkjc_open_w = ?, hkjc_open_d = ?, hkjc_open_l = ?,
                hkjc_close_w = ?, hkjc_close_d = ?, hkjc_close_l = ?,
                odds_source = ?,
                ah_handicap = COALESCE(?, ah_handicap),
                ah_home_water = COALESCE(?, ah_home_water),
                ah_away_water = COALESCE(?, ah_away_water),
                ah_source = COALESCE(?, ah_source),
                pin_ah_handicap = COALESCE(?, pin_ah_handicap),
                pin_ah_home_water = COALESCE(?, pin_ah_home_water),
                pin_ah_away_water = COALESCE(?, pin_ah_away_water),
                pin_ah_open_handicap = COALESCE(?, pin_ah_open_handicap),
                pin_ah_open_home_water = COALESCE(?, pin_ah_open_home_water),
                pin_ah_open_away_water = COALESCE(?, pin_ah_open_away_water),
                pin_ou_line = COALESCE(?, pin_ou_line),
                pin_ou_over = COALESCE(?, pin_ou_over),
                pin_ou_under = COALESCE(?, pin_ou_under),
                pin_ou_open_line = COALESCE(?, pin_ou_open_line),
                pin_ou_open_over = COALESCE(?, pin_ou_open_over),
                pin_ou_open_under = COALESCE(?, pin_ou_open_under),
                hkjc_ah_handicap = COALESCE(?, hkjc_ah_handicap),
                hkjc_ah_home_water = COALESCE(?, hkjc_ah_home_water),
                hkjc_ah_away_water = COALESCE(?, hkjc_ah_away_water),
                hkjc_ah_open_handicap = COALESCE(?, hkjc_ah_open_handicap),
                hkjc_ah_open_home_water = COALESCE(?, hkjc_ah_open_home_water),
                hkjc_ah_open_away_water = COALESCE(?, hkjc_ah_open_away_water),
                hkjc_ou_line = COALESCE(?, hkjc_ou_line),
                hkjc_ou_over = COALESCE(?, hkjc_ou_over),
                hkjc_ou_under = COALESCE(?, hkjc_ou_under),
                hkjc_ou_open_line = COALESCE(?, hkjc_ou_open_line),
                hkjc_ou_open_over = COALESCE(?, hkjc_ou_open_over),
                hkjc_ou_open_under = COALESCE(?, hkjc_ou_open_under),
                liji_handicap = COALESCE(?, liji_handicap),
                liji_home_water = COALESCE(?, liji_home_water),
                liji_away_water = COALESCE(?, liji_away_water),
                liji_open_handicap = COALESCE(?, liji_open_handicap),
                liji_open_home_water = COALESCE(?, liji_open_home_water),
                liji_open_away_water = COALESCE(?, liji_open_away_water),
                ms_handicap = COALESCE(?, ms_handicap),
                ms_home_water = COALESCE(?, ms_home_water),
                ms_away_water = COALESCE(?, ms_away_water),
                ms_open_handicap = COALESCE(?, ms_open_handicap),
                ms_open_home_water = COALESCE(?, ms_open_home_water),
                ms_open_away_water = COALESCE(?, ms_open_away_water),
                liji_ou_over = COALESCE(?, liji_ou_over), liji_ou_line = COALESCE(?, liji_ou_line), liji_ou_under = COALESCE(?, liji_ou_under),
                liji_ou_open_over = COALESCE(?, liji_ou_open_over), liji_ou_open_line = COALESCE(?, liji_ou_open_line), liji_ou_open_under = COALESCE(?, liji_ou_open_under),
                ms_ou_over = COALESCE(?, ms_ou_over), ms_ou_line = COALESCE(?, ms_ou_line), ms_ou_under = COALESCE(?, ms_ou_under),
                ms_ou_open_over = COALESCE(?, ms_ou_open_over), ms_ou_open_line = COALESCE(?, ms_ou_open_line), ms_ou_open_under = COALESCE(?, ms_ou_open_under),
                william_1x2_w = COALESCE(?, william_1x2_w), william_1x2_d = COALESCE(?, william_1x2_d), william_1x2_l = COALESCE(?, william_1x2_l),
                liji_1x2_w = COALESCE(?, liji_1x2_w), liji_1x2_d = COALESCE(?, liji_1x2_d), liji_1x2_l = COALESCE(?, liji_1x2_l),
                liji_1x2_open_w = COALESCE(?, liji_1x2_open_w), liji_1x2_open_d = COALESCE(?, liji_1x2_open_d), liji_1x2_open_l = COALESCE(?, liji_1x2_open_l),
                ms_1x2_w = COALESCE(?, ms_1x2_w), ms_1x2_d = COALESCE(?, ms_1x2_d), ms_1x2_l = COALESCE(?, ms_1x2_l),
                ms_1x2_open_w = COALESCE(?, ms_1x2_open_w), ms_1x2_open_d = COALESCE(?, ms_1x2_open_d), ms_1x2_open_l = COALESCE(?, ms_1x2_open_l),
                william_ah_handicap = COALESCE(?, william_ah_handicap),
                william_ah_home_water = COALESCE(?, william_ah_home_water),
                william_ah_away_water = COALESCE(?, william_ah_away_water),
                william_ah_open_handicap = COALESCE(?, william_ah_open_handicap),
                william_ah_open_home_water = COALESCE(?, william_ah_open_home_water),
                william_ah_open_away_water = COALESCE(?, william_ah_open_away_water)
            WHERE id = ?
        """, (
            pin_open.get('w', 0), pin_open.get('d', 0), pin_open.get('l', 0),
            pin_close.get('w', 0), pin_close.get('d', 0), pin_close.get('l', 0),
            json.dumps(pin_movement, ensure_ascii=False),
            margin,
            implied.get('w', 0), implied.get('d', 0), implied.get('l', 0),
            avg_open.get('w', 0), avg_open.get('d', 0), avg_open.get('l', 0),
            avg_close.get('w', 0), avg_close.get('d', 0), avg_close.get('l', 0),
            hkjc_open.get('w', 0), hkjc_open.get('d', 0), hkjc_open.get('l', 0),
            hkjc_close.get('w', 0), hkjc_close.get('d', 0), hkjc_close.get('l', 0),
            m.get('odds_source', ''),
            ah_hc_val, ah_hw_val, ah_aw_val, ah_source,
            pin_ah_hc_val, pin_ah_hw_val, pin_ah_aw_val,
            pin_ah_open_hc_val, pin_ah_open_hw_val, pin_ah_open_aw_val,
            pin_ou_line_val, pin_ou_over_val, pin_ou_under_val,
            pin_ou_open_line_val, pin_ou_open_over_val, pin_ou_open_under_val,
            hkjc_ah_hc_val, hkjc_ah_hw_val, hkjc_ah_aw_val,
            hkjc_ah_open_hc_val, hkjc_ah_open_hw_val, hkjc_ah_open_aw_val,
            hkjc_ou_line_val, hkjc_ou_over_val, hkjc_ou_under_val,
            hkjc_ou_open_line_val, hkjc_ou_open_over_val, hkjc_ou_open_under_val,
            liji_ah_hc_val, liji_ah_hw_val, liji_ah_aw_val,
            liji_ah_open_hc_val, liji_ah_open_hw_val, liji_ah_open_aw_val,
            ms_ah_hc_val, ms_ah_hw_val, ms_ah_aw_val,
            ms_ah_open_hc_val, ms_ah_open_hw_val, ms_ah_open_aw_val,
            liji_ou_close_o, liji_ou_close_l, liji_ou_close_u,
            liji_ou_open_o, liji_ou_open_l, liji_ou_open_u,
            ms_ou_close_o, ms_ou_close_l, ms_ou_close_u,
            ms_ou_open_o, ms_ou_open_l, ms_ou_open_u,
            w1x2_w, w1x2_d, w1x2_l,
            liji_1x2_close.get('w') or None, liji_1x2_close.get('d') or None, liji_1x2_close.get('l') or None,
            liji_1x2_open.get('w') or None, liji_1x2_open.get('d') or None, liji_1x2_open.get('l') or None,
            ms_1x2_close.get('w') or None, ms_1x2_close.get('d') or None, ms_1x2_close.get('l') or None,
            ms_1x2_open.get('w') or None, ms_1x2_open.get('d') or None, ms_1x2_open.get('l') or None,
            william_ah_hc_val, william_ah_hw_val, william_ah_aw_val,
            william_ah_open_hc_val, william_ah_open_hw_val, william_ah_open_aw_val,
            record_id
        ))
        updated += 1
    
    # 第二遍：用oddsmagnet缓存补充未匹配的DB记录
    om_cache_path = os.path.join(DATA_BASE_DIR, "data", "cache", "real_odds.json")
    unmatched_records = [r for r in db_records if r[0] not in matched_db_ids]
    
    if unmatched_records and os.path.exists(om_cache_path):
        try:
            with open(om_cache_path, 'r', encoding='utf-8') as f:
                om_data = json.load(f)
            if isinstance(om_data, dict):
                # 构建oddsmagnet的match列表
                om_matches = []
                for key, item in om_data.items():
                    match_date = item.get('matchDate', '')
                    if target_date and match_date != target_date and match_date != prev_day and match_date != next_day:
                        continue
                    parts = key.split(' vs ')
                    if len(parts) != 2: continue
                    om_matches.append({
                        'home': parts[0].strip(),
                        'away': parts[1].strip(),
                        'pinnacle_open': {'w': item.get('pinnacle_open_w', 0) or 0, 'd': item.get('pinnacle_open_d', 0) or 0, 'l': item.get('pinnacle_open_l', 0) or 0},
                        'pinnacle_close': {'w': item.get('pinnacle_open_w', 0) or 0, 'd': item.get('pinnacle_open_d', 0) or 0, 'l': item.get('pinnacle_open_l', 0) or 0},
                        'pinnacle_movement': {'w': 'stable', 'd': 'stable', 'l': 'stable'},
                        'implied_prob': {},
                        'pinnacle_margin': item.get('avg_margin', 0),
                        'avg_odds_open': {'w': item.get('home', 0) or 0, 'd': item.get('draw', 0) or 0, 'l': item.get('away', 0) or 0},
                        'avg_odds_close': {'w': item.get('home', 0) or 0, 'd': item.get('draw', 0) or 0, 'l': item.get('away', 0) or 0},
                        'hkjc_open': {'w': item.get('hkjc_open_w', 0) or 0, 'd': item.get('hkjc_open_d', 0) or 0, 'l': item.get('hkjc_open_l', 0) or 0},
                        'hkjc_close': {'w': item.get('hkjc_open_w', 0) or 0, 'd': item.get('hkjc_open_d', 0) or 0, 'l': item.get('hkjc_open_l', 0) or 0},
                        'odds_source': 'Pinnacle' if (item.get('pinnacle_open_w', 0) or 0) > 0 else '百家平均',
                    })
                
                om_matched = 0
                for record in unmatched_records:
                    record_id, db_home, db_away, db_time = record[:4]
                    best_match = None
                    best_sim = 0
                    for om_m in om_matches:
                        sim_h = team_name_similarity(om_m['home'], db_home)
                        sim_a = team_name_similarity(om_m['away'], db_away)
                        avg_sim = (sim_h + sim_a) / 2
                        if avg_sim > best_sim:
                            best_sim = avg_sim
                            best_match = om_m
                    
                    if best_match and best_sim >= 0.4:
                        pin_open = best_match.get('pinnacle_open', {})
                        pin_close = best_match.get('pinnacle_close', {})

                        implied = best_match.get('implied_prob', {})
                        margin = best_match.get('pinnacle_margin', 0)
                        avg_open = best_match.get('avg_odds_open', {})
                        avg_close = best_match.get('avg_odds_close', {})
                        hkjc_open = best_match.get('hkjc_open', {})
                        hkjc_close = best_match.get('hkjc_close', {})
                        
                        # 亚盘让球盘（仅百家平均，HKJC已取消）
                        ah_close = best_match.get('ah_avg_close') or {}
                        ah_source = 'avg' if best_match.get('ah_avg_close') else ''

                        # Pinnacle亚盘/大小球和大小球数据 — oddsmagnet fallback无此数据
                        pin_ah = {}
                        pin_ou = {}
                        ou_close = {}
                        ou_open = {}
                        liji_ou_close = {}
                        liji_ou_open = {}
                        ms_ou_close = {}
                        ms_ou_open = {}

                        # 保护已有非零ah数据：只在DB原值为0/NULL时才写入新值
                        om_ah_hc = _or_none(ah_close, 'handicap', 'home_w', 'away_w', k='handicap')
                        om_ah_hw = _or_none(ah_close, 'handicap', 'home_w', 'away_w', k='home_w')
                        om_ah_aw = _or_none(ah_close, 'handicap', 'home_w', 'away_w', k='away_w')

                        cursor.execute("""
                            UPDATE poisson_predictions SET
                                pinnacle_open_w = ?, pinnacle_open_d = ?, pinnacle_open_l = ?,
                                pinnacle_close_w = ?, pinnacle_close_d = ?, pinnacle_close_l = ?,
                                pinnacle_movement = ?, pinnacle_margin = ?,
                                implied_prob_w = ?, implied_prob_d = ?, implied_prob_l = ?,
                                avg_odds_open_w = ?, avg_odds_open_d = ?, avg_odds_open_l = ?,
                                avg_odds_close_w = ?, avg_odds_close_d = ?, avg_odds_close_l = ?,
                                hkjc_open_w = ?, hkjc_open_d = ?, hkjc_open_l = ?,
                                hkjc_close_w = ?, hkjc_close_d = ?, hkjc_close_l = ?,
                                odds_source = ?,
                                ah_handicap = COALESCE(?, ah_handicap),
                                ah_home_water = COALESCE(?, ah_home_water),
                                ah_away_water = COALESCE(?, ah_away_water),
                                ah_source = COALESCE(?, ah_source),
                                pin_ah_handicap = COALESCE(?, pin_ah_handicap),
                                pin_ah_home_water = COALESCE(?, pin_ah_home_water),
                                pin_ah_away_water = COALESCE(?, pin_ah_away_water),
                                pin_ou_line = COALESCE(?, pin_ou_line),
                                pin_ou_over = COALESCE(?, pin_ou_over),
                                pin_ou_under = COALESCE(?, pin_ou_under),
                                ou_over = COALESCE(?, ou_over), ou_line = COALESCE(?, ou_line), ou_under = COALESCE(?, ou_under),
                                ou_open_over = COALESCE(?, ou_open_over), ou_open_line = COALESCE(?, ou_open_line), ou_open_under = COALESCE(?, ou_open_under),
                                liji_ou_over = COALESCE(?, liji_ou_over), liji_ou_line = COALESCE(?, liji_ou_line), liji_ou_under = COALESCE(?, liji_ou_under),
                                liji_ou_open_over = COALESCE(?, liji_ou_open_over), liji_ou_open_line = COALESCE(?, liji_ou_open_line), liji_ou_open_under = COALESCE(?, liji_ou_open_under),
                                ms_ou_over = COALESCE(?, ms_ou_over), ms_ou_line = COALESCE(?, ms_ou_line), ms_ou_under = COALESCE(?, ms_ou_under),
                                ms_ou_open_over = COALESCE(?, ms_ou_open_over), ms_ou_open_line = COALESCE(?, ms_ou_open_line), ms_ou_open_under = COALESCE(?, ms_ou_open_under)
                            WHERE id = ?
                        """, (
                            pin_open.get('w', 0), pin_open.get('d', 0), pin_open.get('l', 0),
                            pin_close.get('w', 0), pin_close.get('d', 0), pin_close.get('l', 0),
                            json.dumps(best_match.get('pinnacle_movement', {}), ensure_ascii=False),
                            margin,
                            implied.get('w', 0), implied.get('d', 0), implied.get('l', 0),
                            avg_open.get('w', 0), avg_open.get('d', 0), avg_open.get('l', 0),
                            avg_close.get('w', 0), avg_close.get('d', 0), avg_close.get('l', 0),
                            0, 0, 0,  # hkjc_open — 已取消，值全为0
                            0, 0, 0,  # hkjc_close — 已取消，值全为0
                            best_match.get('odds_source', ''),
                            om_ah_hc, om_ah_hw, om_ah_aw, ah_source,
                            None, None, None,  # pin_ah — OM fallback无此数据，COALESCE 保留 DB 旧值
                            None, None, None,  # pin_ou — OM fallback无此数据，COALESCE 保留 DB 旧值
                            None, None, None,  # ou close — OM fallback无此数据，COALESCE 保留 DB 旧值
                            None, None, None,  # ou open
                            None, None, None,  # liji_ou close
                            None, None, None,  # liji_ou open
                            None, None, None,  # ms_ou close
                            None, None, None,  # ms_ou open
                            record_id
                        ))
                        om_matched += 1
                        updated += 1
                        if pin_open.get('w', 0) > 0:
                            print(f"  [OM] {db_home} vs {db_away} <- {best_match['home']} vs {best_match['away']} (sim={best_sim:.2f}) Pin={pin_open['w']:.2f}/{pin_open['d']:.2f}/{pin_open['l']:.2f}")
                
                if om_matched > 0:
                    print(f"[INFO] oddsmagnet补充写入: {om_matched} 条")
        except Exception as e:
            print(f"[WARN] oddsmagnet补充失败: {e}")
    
    conn.commit()
    conn.close()
    print(f"[INFO] 写入完成，更新 {updated} 条记录")
    return updated




# ========== 缓存读取 + DB写入 ==========

def load_odds_cache(date_str, latest=True):
    """加载赔率缓存文件
    
    Args:
        date_str: 日期字符串 YYYY-MM-DD
        latest: True=返回最新的缓存，False=返回所有缓存（按时间排序）
    
    Returns:
        latest=True: 单个缓存dict或None
        latest=False: 缓存列表
    """
    cache_dir = os.path.join(DATA_BASE_DIR, "data", "cache")
    date_tag = date_str.replace('-', '')
    
    # 查找所有匹配的缓存文件（兼容带_HHMM后缀和不带后缀的文件名）
    cache_files = []
    if os.path.exists(cache_dir):
        for f in os.listdir(cache_dir):
            if f.startswith(f"pinnacle_odds_{date_tag}") and f.endswith(".json"):
                cache_files.append(os.path.join(cache_dir, f))
    
    if not cache_files:
        return None if latest else []
    
    # 按文件名排序（含时间戳，最新的在后面）
    cache_files.sort(reverse=True)
    
    if latest:
        # 返回pinnacle场次最多的缓存（最新缓存可能0场，旧缓存有数据）
        best_data = None
        best_pin = -1
        for cf in cache_files:
            with open(cf, 'r', encoding='utf-8') as f:
                data = json.load(f)
            # 2026-06-24 修复：fallback cache 现在也用 list 格式存 matches，不再跳过
            # （之前 fallback 存 dict 格式导致 save_to_db 不兼容，已修正为 list）
            pin_count = data.get('summary', {}).get('pinnacle', 0)
            total_count = data.get('summary', {}).get('total', 0)
            if pin_count > best_pin or (pin_count == best_pin and total_count > (best_data.get('summary',{}).get('total',0) if best_data else 0)):
                best_data = data
                best_pin = pin_count
                data['_cache_file'] = cf
        return best_data
    else:
        results = []
        for cf in cache_files:
            with open(cf, 'r', encoding='utf-8') as f:
                d = json.load(f)
            d['_cache_file'] = cf
            results.append(d)
        return results


def apply_odds_to_db(db_path, date_str=None, cache_path=None):
    """从缓存读取赔率数据并写入DB
    
    Args:
        db_path: 数据库路径
        date_str: 日期字符串（自动查找最新缓存）
        cache_path: 指定缓存文件路径（优先级高于date_str）
    
    Returns:
        int: 更新的记录数
    """
    if cache_path:
        # 指定缓存文件
        with open(cache_path, 'r', encoding='utf-8') as f:
            cache_data = json.load(f)
        matches = cache_data.get('matches', [])
        date_str = cache_data.get('date', date_str)
    elif date_str:
        # 自动查找最新缓存
        cache_data = load_odds_cache(date_str, latest=True)
        if not cache_data:
            print(f"[WARN] 未找到 {date_str} 的赔率缓存")
            return 0
        matches = cache_data.get('matches', [])
        # 兼容旧 fallback 缓存（matches 为 dict 格式）
        if isinstance(matches, dict):
            matches = list(matches.values())
        print(f"[INFO] 使用缓存: {cache_data.get('_cache_file', '?')} (拉取时间: {cache_data.get('fetch_time', '?')})")
    else:
        print("[ERROR] 必须指定 date_str 或 cache_path")
        return 0
    
    return save_to_db(matches, db_path, date_str)

if __name__ == '__main__':
    import sys
    import argparse
    parser = argparse.ArgumentParser(description="赔率数据拉取与写入")
    parser.add_argument('--date', help='目标日期 YYYY-MM-DD，默认今天')
    parser.add_argument('--fetch-only', action='store_true', help='仅拉取保存缓存，不写DB')
    parser.add_argument('--apply', help='从缓存写入DB，指定日期 YYYY-MM-DD')
    parser.add_argument('--cache', help='从指定缓存文件写入DB')
    parser.add_argument('--db', help='指定DB路径（配合--apply使用）')
    parser.add_argument('--list-cache', help='列出指定日期的所有缓存')
    args = parser.parse_args()
    
    if args.list_cache:
        caches = load_odds_cache(args.list_cache, latest=False)
        if not caches:
            print(f"未找到 {args.list_cache} 的缓存")
        else:
            for c in caches:
                s = c.get('summary', {})
                print(f"  {c.get('_cache_file', '?').split('/')[-1]} | 拉取: {c.get('fetch_time', '?')} | "
                      f"共{s.get('total',0)}场 pin:{s.get('pinnacle',0)} sb:{s.get('sb',0)}")
    elif args.apply or args.cache:
        db = args.db or DB_JINGCAI
        updated = apply_odds_to_db(db, date_str=args.apply, cache_path=args.cache)
        print(f"✅ 写入完成: {updated} 条")
    elif args.fetch_only:
        results = fetch_pinnacle_odds(args.date)
        print(f"✅ 拉取完成: {len(results)} 场（仅缓存，未写DB）")
    else:
        # 默认：拉取+缓存+写DB（兼容旧用法）
        date_str = args.date
        results = fetch_pinnacle_odds(date_str)
        
        # 写DB
        if results:
            date_for_db = date_str or datetime.now().strftime('%Y-%m-%d')
            jc_updated = apply_odds_to_db(DB_JINGCAI, date_str=date_for_db)
            print(f"✅ 竞彩DB写入: {jc_updated} 条")
            bd_updated = apply_odds_to_db(DB_BEIDAN, date_str=date_for_db)
            print(f"✅ 北单DB写入: {bd_updated} 条")
