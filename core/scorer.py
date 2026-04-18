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
    def __init__(self, weights=None):
        # 權重分配 (總和 100%)
        self.weights = weights or {
            'market':   0.10,   # 大盤環境
            'sector':   0.05,   # 族群動能
            'density':  0.20,   # 收斂密度 (核心：預測輪動)
            'pattern':  0.15,   # 型態品質
            'breakout': 0.10,   # 突破即時性
            'chain':    0.15,   # 供應鏈動能
            'chip':     0.25,   # 籌碼動能 (含大戶領先指標)
        }

    def calculate_total_score(self, ticker, m_score, s_score, p_score, b_score,
                              density_score=0, chain_score=0, chip_score=0,
                              opportunity_grade='C', details=None):
        """
        計算總合分
        所有分數均為 0-100 之間
        """
        total = (
            m_score       * self.weights['market'] +
            s_score       * self.weights['sector'] +
            density_score * self.weights['density'] +
            p_score       * self.weights['pattern'] +
            b_score       * self.weights['breakout'] +
            chain_score   * self.weights['chain'] +
            chip_score    * self.weights['chip']
        )

        # A 級機會加分 (最多 +5)
        grade_bonus = {'A': 5, 'B': 2, 'C': 0}.get(opportunity_grade, 0)
        total = min(100, total + grade_bonus)

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
