#!/usr/bin/env python3
"""bsd_model.py — BSD (Bookmaker Statistical Deviation) 赔率分析模型

核心能力：
1. 隐含概率计算（从赔率反向推导）
2. 庄家抽水还原（去除margin → 公平赔率/真实胜率）
3. 多机构赔率横向对比（分歧检测、套利扫描）
4. EV 计算基准

BSD 核心逻辑：
  隐含概率 = 1/赔率
  抽水率 = Σ(隐含概率) - 1
  真实胜率 = 隐含概率 / Σ(隐含概率)   ← 去抽水
  公平赔率 = 1/真实胜率               ← 无抽水下的理论赔率
"""

from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field


# ─── 基础计算 ───

def implied_prob(w: float, d: float, l: float) -> Tuple[float, float, float]:
    """从欧赔计算隐含概率（含抽水）

    Returns: (p_w, p_d, p_l) 隐含概率，和 > 1
    """
    if w <= 0 or d <= 0 or l <= 0:
        return 0.0, 0.0, 0.0
    raw_w = 1.0 / w
    raw_d = 1.0 / d
    raw_l = 1.0 / l
    return round(raw_w, 4), round(raw_d, 4), round(raw_l, 4)


def margin(w: float, d: float, l: float) -> float:
    """庄家抽水率（overround）

    margin = p_w + p_d + p_l - 1
    典型值: 竞彩 8-12%, Pinnacle 2-4%, HKJC 4-6%
    """
    p_w, p_d, p_l = implied_prob(w, d, l)
    return round(p_w + p_d + p_l - 1.0, 4)


def fair_prob(w: float, d: float, l: float) -> Tuple[float, float, float]:
    """去抽水后的真实胜率（BSD核心）

    fair_p = implied_p / (p_w + p_d + p_l)
    """
    p_w, p_d, p_l = implied_prob(w, d, l)
    total = p_w + p_d + p_l
    if total <= 0:
        return 0.0, 0.0, 0.0
    return round(p_w / total, 4), round(p_d / total, 4), round(p_l / total, 4)


def fair_odds(w: float, d: float, l: float) -> Tuple[float, float, float]:
    """公平赔率（无抽水下的理论赔率）

    fair_odds = 1 / fair_prob
    """
    fp_w, fp_d, fp_l = fair_prob(w, d, l)
    return (
        round(1.0 / fp_w, 3) if fp_w > 0 else 0.0,
        round(1.0 / fp_d, 3) if fp_d > 0 else 0.0,
        round(1.0 / fp_l, 3) if fp_l > 0 else 0.0,
    )


def ev(fair_p: float, book_odds: float) -> float:
    """期望价值

    EV = fair_p * book_odds - 1
    EV > 0 表示有价值投注
    """
    if fair_p <= 0 or book_odds <= 0:
        return 0.0
    return round(fair_p * book_odds - 1.0, 4)


# ─── 多机构对比 ───

@dataclass
class BookmakerOdds:
    """单机构赔率数据"""
    source: str          # 机构名: pinnacle, hkjc, sb, avg, jc
    odds_w: float        # 主胜赔率
    odds_d: float        # 平局赔率
    odds_l: float        # 客胜赔率
    margin_pct: float = 0.0
    fair_w: float = 0.0
    fair_d: float = 0.0
    fair_l: float = 0.0

    def __post_init__(self):
        self.margin_pct = margin(self.odds_w, self.odds_d, self.odds_l)
        self.fair_w, self.fair_d, self.fair_l = fair_prob(
            self.odds_w, self.odds_d, self.odds_l
        )


@dataclass
class OddsComparison:
    """多机构赔率对比结果"""
    match_id: str = ""
    home: str = ""
    away: str = ""
    league: str = ""
    books: Dict[str, BookmakerOdds] = field(default_factory=dict)

    # 融合后
    consensus_w: float = 0.0
    consensus_d: float = 0.0
    consensus_l: float = 0.0

    # 分歧指标
    max_spread_w: float = 0.0
    max_spread_d: float = 0.0
    max_spread_l: float = 0.0
    has_value: Dict[str, float] = field(default_factory=dict)

    def compare(self) -> None:
        """执行多机构对比"""
        if not self.books:
            return

        # 1. 融合胜率：各机构fair_prob加权平均（按1/margin加权，margin低的更可信）
        weights = {}
        total_w = 0.0
        for name, b in self.books.items():
            if b.margin_pct > 0:
                w = 1.0 / b.margin_pct
            else:
                w = 1.0
            weights[name] = w
            total_w += w

        if total_w <= 0:
            return

        cw = cd = cl = 0.0
        for name, b in self.books.items():
            wt = weights[name] / total_w
            cw += b.fair_w * wt
            cd += b.fair_d * wt
            cl += b.fair_l * wt

        total = cw + cd + cl
        self.consensus_w = round(cw / total, 4) if total > 0 else 0
        self.consensus_d = round(cd / total, 4) if total > 0 else 0
        self.consensus_l = round(cl / total, 4) if total > 0 else 0

        # 2. 分歧检测：同一方向赔率最大差值
        all_w = [b.odds_w for b in self.books.values() if b.odds_w > 1]
        all_d = [b.odds_d for b in self.books.values() if b.odds_d > 1]
        all_l = [b.odds_l for b in self.books.values() if b.odds_l > 1]
        self.max_spread_w = round(max(all_w) - min(all_w), 3) if len(all_w) >= 2 else 0
        self.max_spread_d = round(max(all_d) - min(all_d), 3) if len(all_d) >= 2 else 0
        self.max_spread_l = round(max(all_l) - min(all_l), 3) if len(all_l) >= 2 else 0

        # 3. EV扫描：用融合概率对每个机构赔率算EV
        for name, b in self.books.items():
            ev_w = ev(self.consensus_w, b.odds_w)
            ev_d = ev(self.consensus_d, b.odds_d)
            ev_l = ev(self.consensus_l, b.odds_l)
            best = max(ev_w, ev_d, ev_l)
            if best > 0:
                self.has_value[name] = round(best, 4)

    def summary(self) -> str:
        """输出对比摘要"""
        lines = [f"📊 {self.home} vs {self.away} ({self.league})"]
        lines.append(f"   融合胜率: 主{self.consensus_w:.1%} 平{self.consensus_d:.1%} 客{self.consensus_l:.1%}")

        for name, b in self.books.items():
            lines.append(
                f"   {name:10s}: {b.odds_w:.2f}/{b.odds_d:.2f}/{b.odds_l:.2f}  "
                f"抽水{b.margin_pct:.1%}  "
                f"fair={b.fair_w:.1%}/{b.fair_d:.1%}/{b.fair_l:.1%}"
            )

        if self.max_spread_w > 0.15 or self.max_spread_l > 0.15:
            lines.append(
                f"   ⚠️ 分歧: 主{self.max_spread_w:.2f} 平{self.max_spread_d:.2f} 客{self.max_spread_l:.2f}"
            )

        if self.has_value:
            for name, val in sorted(self.has_value.items(), key=lambda x: -x[1]):
                lines.append(f"   💰 {name} EV=+{val:.1%}")

        return "\n".join(lines)


# ─── 套利检测 ───

def arbitrage_scan(comparisons: List[OddsComparison]) -> List[Dict]:
    """扫描套利机会（多机构最优赔率组合）

    套利条件: 1/best_w + 1/best_d + 1/best_l < 1
    """
    opps = []
    for c in comparisons:
        if len(c.books) < 2:
            continue
        best_w = max((b.odds_w, b.source) for b in c.books.values() if b.odds_w > 1)
        best_d = max((b.odds_d, b.source) for b in c.books.values() if b.odds_d > 1)
        best_l = max((b.odds_l, b.source) for b in c.books.values() if b.odds_l > 1)

        cost = 1.0/best_w[0] + 1.0/best_d[0] + 1.0/best_l[0]
        if cost < 1.0:
            opps.append({
                'match': f"{c.home} vs {c.away}",
                'best_w': f"{best_w[0]:.2f}({best_w[1]})",
                'best_d': f"{best_d[0]:.2f}({best_d[1]})",
                'best_l': f"{best_l[0]:.2f}({best_l[1]})",
                'profit_pct': round((1.0 - cost) * 100, 2),
            })
    return opps


# ─── 演示 ───

if __name__ == '__main__':
    # 示例: 巴黎 vs 阿森纳
    print("=" * 60)
    print("BSD 模型演示 — 巴黎圣日耳曼 vs 阿森纳")
    print("=" * 60)

    # 各机构赔率
    books = {
        'avg':    BookmakerOdds('百家平均', 2.37, 3.26, 3.09),
        'pinnacle': BookmakerOdds('Pinnacle', 2.40, 3.30, 3.10),
        'hkjc':   BookmakerOdds('HKJC',     2.35, 3.20, 3.05),
        'jc':     BookmakerOdds('竞彩',      2.25, 3.10, 2.95),
    }

    comp = OddsComparison(
        match_id="demo",
        home="巴黎圣日耳曼", away="阿森纳", league="欧冠",
        books=books,
    )
    comp.compare()
    print(comp.summary())

    # 单机构BSD详解
    print("\n" + "=" * 60)
    print("BSD 单机构详解 — 百家平均")
    print("=" * 60)
    b = books['avg']
    fw, fd, fl = fair_prob(b.odds_w, b.odds_d, b.odds_l)
    fow, fod, fol = fair_odds(b.odds_w, b.odds_d, b.odds_l)
    print(f"  赔率: {b.odds_w}/{b.odds_d}/{b.odds_l}")
    print(f"  隐含概率: {1/b.odds_w:.4f}/{1/b.odds_d:.4f}/{1/b.odds_l:.4f}")
    print(f"  抽水率: {margin(b.odds_w, b.odds_d, b.odds_l):.2%}")
    print(f"  真实胜率: {fw:.2%}/{fd:.2%}/{fl:.2%}")
    print(f"  公平赔率: {fow:.3f}/{fod:.3f}/{fol:.3f}")

    # 竞彩 vs 融合概率 EV
    print(f"\n  竞彩 EV（基于融合概率）:")
    print(f"    主胜: EV={ev(fw, 2.25):.2%}")
    print(f"    平局: EV={ev(fd, 3.10):.2%}")
    print(f"    客胜: EV={ev(fl, 2.95):.2%}")
