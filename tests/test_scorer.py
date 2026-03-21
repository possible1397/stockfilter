"""tests/test_scorer.py — StrategyScorer 單元測試"""
import pytest
from core.scorer import StrategyScorer, StrategyScore


class TestStrategyScorer:
    def setup_method(self):
        self.scorer = StrategyScorer()

    def test_basic_score_calculation(self):
        result = self.scorer.calculate_total_score(
            ticker="2330.TW",
            m_score=70, s_score=60, p_score=80, b_score=50,
            density_score=90, chain_score=70,
            opportunity_grade="A",
        )
        assert isinstance(result, StrategyScore)
        assert 0 <= result.total_score <= 100

    def test_grade_a_bonus(self):
        base = self.scorer.calculate_total_score(
            "TEST", 50, 50, 50, 0, density_score=50, chain_score=50,
            opportunity_grade="C",
        )
        grade_a = self.scorer.calculate_total_score(
            "TEST", 50, 50, 50, 0, density_score=50, chain_score=50,
            opportunity_grade="A",
        )
        assert grade_a.total_score > base.total_score
        assert grade_a.total_score - base.total_score == pytest.approx(5.0, abs=0.1)

    def test_grade_b_bonus(self):
        base = self.scorer.calculate_total_score(
            "TEST", 50, 50, 50, 0, density_score=50, chain_score=50,
            opportunity_grade="C",
        )
        grade_b = self.scorer.calculate_total_score(
            "TEST", 50, 50, 50, 0, density_score=50, chain_score=50,
            opportunity_grade="B",
        )
        assert grade_b.total_score - base.total_score == pytest.approx(2.0, abs=0.1)

    def test_score_capped_at_100(self):
        result = self.scorer.calculate_total_score(
            "TEST", 100, 100, 100, 100, density_score=100, chain_score=100,
            opportunity_grade="A",
        )
        assert result.total_score <= 100

    def test_zero_inputs(self):
        result = self.scorer.calculate_total_score(
            "TEST", 0, 0, 0, 0, density_score=0, chain_score=0,
        )
        assert result.total_score == pytest.approx(0.0)

    def test_details_stored(self):
        result = self.scorer.calculate_total_score(
            "TEST", 50, 50, 50, 50,
            details={"sector": "半導體業", "custom": 123},
        )
        assert result.details["sector"] == "半導體業"
        assert result.details["custom"] == 123

    def test_rank_results_order(self):
        a = self.scorer.calculate_total_score("A", 80, 80, 80, 80, density_score=80, chain_score=80, opportunity_grade="A")
        b = self.scorer.calculate_total_score("B", 60, 60, 60, 60, density_score=60, chain_score=60, opportunity_grade="B")
        c = self.scorer.calculate_total_score("C", 40, 40, 40, 40, density_score=40, chain_score=40, opportunity_grade="C")
        ranked = self.scorer.rank_results([c, b, a])
        assert ranked[0].opportunity_grade == "A"
        assert ranked[1].opportunity_grade == "B"
        assert ranked[2].opportunity_grade == "C"

    def test_custom_weights(self):
        scorer = StrategyScorer(weights={
            'market': 1.0, 'density': 0.0, 'pattern': 0.0,
            'breakout': 0.0, 'chain': 0.0,
        })
        result = scorer.calculate_total_score(
            "TEST", m_score=75, s_score=0, p_score=0, b_score=0,
            density_score=0, chain_score=0,
        )
        assert result.total_score == pytest.approx(75.0, abs=0.1)
