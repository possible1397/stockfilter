import unittest
import pandas as pd
from core.chip_analysis import ChipAnalyzer

class TestChipAnalyzer(unittest.TestCase):
    def setUp(self):
        # 目標: 500 張 = 500,000 股
        self.config = {
            'chip': {
                'trust_consecutive_days_mid': 5,
                'trust_consecutive_days_high': 10,
                'target_lots': 500,
                'absorption_ratio': 0.03
            }
        }
        self.analyzer = ChipAnalyzer(self.config)
        
    def test_streak_and_absorption(self):
        # 建立近 5 日的籌碼資料
        # 假設投信連續買超 5 天 (每日買 60 萬股)
        dates = pd.date_range('2024-03-01', periods=5)
        chip_df = pd.DataFrame({
            'date': dates,
            'trust_net_buy': [600000] * 5,
            'foreign_net_buy': [0] * 5,
            'dealer_net_buy': [0] * 5
        }).set_index('date')
        
        # 建立價量資料 (5 日均量)
        price_df = pd.DataFrame({
            'date': dates,
            'Volume': [1000000] * 5 # 每日 100 萬股
        }).set_index('date')
        
        result = self.analyzer.analyze('2330.TW', price_df, chip_df)
        
        # 預期得分:
        # momentum_score = 30 (連買 5 天)
        # absorption: max_buy = 600,000 >= 500,000 (+10)
        # absorption ratio = 600k / 1M = 60% > 3% (+20) => total +30
        # total = 60。無大戶持股資料 → 動態滿分 = 50 + 30 = 80
        # normalized = (60/80)*100 = 75.0

        self.assertEqual(result['details']['trust_streak'], 5)
        self.assertEqual(result['details']['momentum_score'], 30)
        self.assertEqual(result['details']['absorption_score'], 30)
        self.assertAlmostEqual(result['score'], (60/80)*100, places=1)

    def test_empty_data(self):
        result = self.analyzer.analyze('2330.TW', pd.DataFrame(), pd.DataFrame())
        self.assertEqual(result['score'], 0)

if __name__ == '__main__':
    unittest.main()
