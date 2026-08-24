"""core/scorer.py — 綜合評分引擎"""
from dataclasses import dataclass


@dataclass
class StrategyScore:
    ticker: str
    total_score: float
    market_score: float
    sector_score: float
    pattern_score: float
    breakout_score: float
    density_score: float    # 收斂密度分數 (族群內收斂集中度)
    chain_score: float      # 供應鏈動能分數 (上下游共振)
    chip_score: float       # 籌碼動能分數 (投信/外資認養)
    opportunity_grade: str  # A/B/C 機會等級
    details: dict


class StrategyScorer:
    """
    策略綜合評分引擎 v2
    新增 density_score (收斂密度) + chain_score (供應鏈) 維度
    """
    # chip_score 超過此門檻時，給予額外加分（獎勵高籌碼，不懲罰無籌碼）
    CHIP_BONUS_THRESHOLD = 50.0
    CHIP_BONUS_POINTS    = 4.0   # 最多 +4 分

    def __init__(self, weights=None):
        # 權重分配 (總和 100%)
        # 依據 2026-04 實測 + 長期回測雙重驗證：
        #   - chain 實測呈負相關 → 大幅調降至 5%
        #   - sector 對勝率影響顯著 → 提升至 20%
        #   - chip 以「超額獎勵」方式加分，避免篩掉無法人資料的早期型態
        self.weights = weights or {
            'market':   0.10,   # 大盤環境
            'sector':   0.20,   # 族群動能 (提升: 15% → 20%)
            'density':  0.20,   # 收斂密度 (核心：預測輪動)
            'pattern':  0.15,   # 型態品質 (恢復: 10% → 15%)
            'breakout': 0.10,   # 突破即時性
            'chain':    0.05,   # 供應鏈動能 (調降: 15% → 5%，實測呈負相關)
            'chip':     0.20,   # 籌碼動能 (提升: 15% → 20%)
        }

    def calculate_total_score(self, ticker, m_score, s_score, p_score, b_score,
                              density_score=0, chain_score=0, chip_score=0,
                              opportunity_grade='C', details=None):
        """
        計算總合分
        所有分數均為 0-100 之間
        """
        # 使用 .get(k, 0) — 自訂權重缺鍵時視為 0，不拋 KeyError
        total = (
            m_score       * self.weights.get('market', 0.0) +
            s_score       * self.weights.get('sector', 0.0) +
            density_score * self.weights.get('density', 0.0) +
            p_score       * self.weights.get('pattern', 0.0) +
            b_score       * self.weights.get('breakout', 0.0) +
            chain_score   * self.weights.get('chain', 0.0) +
            chip_score    * self.weights.get('chip', 0.0)
        )

        # A 級機會加分 (最多 +5)
        grade_bonus = {'A': 5, 'B': 2, 'C': 0}.get(opportunity_grade, 0)
        total += grade_bonus

        # 高籌碼獎勵：chip > 50 給 +4，不懲罰無法人資料的早期型態
        if chip_score >= self.CHIP_BONUS_THRESHOLD:
            chip_bonus = self.CHIP_BONUS_POINTS * (chip_score - self.CHIP_BONUS_THRESHOLD) / 50.0
            total += min(chip_bonus, self.CHIP_BONUS_POINTS)

        total = min(100, total)

        return StrategyScore(
            ticker=ticker,
            total_score=round(total, 2),
            market_score=m_score,
            sector_score=s_score,
            pattern_score=p_score,
            breakout_score=b_score,
            density_score=density_score,
            chain_score=chain_score,
            chip_score=chip_score,
            opportunity_grade=opportunity_grade,
            details=details or {}
        )

    def rank_results(self, scores: list) -> list:
        """
        根據機會等級 + 總分進行排序
        A 級優先，同級別按總分排序
        """
        grade_order = {'A': 0, 'B': 1, 'C': 2}
        return sorted(scores,
                       key=lambda x: (grade_order.get(x.opportunity_grade, 9),
                                      -x.total_score))
