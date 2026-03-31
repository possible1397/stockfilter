import pandas as pd
from typing import Dict, Any

class ChipAnalyzer:
    """
    籌碼面分析引擎
    負責計算法人的「連買動能」與「吸籌比例」，並產出 chip_score (滿分 100)。
    """
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        # 預設門檻，可被 config 覆蓋
        self.streak_mid = self.config.get('chip', {}).get('trust_consecutive_days_mid', 5)
        self.streak_high = self.config.get('chip', {}).get('trust_consecutive_days_high', 10)
        
        # Target lots 500 張 = 500,000 股
        self.target_shares = self.config.get('chip', {}).get('target_lots', 500) * 1000
        self.absorption_ratio = self.config.get('chip', {}).get('absorption_ratio', 0.03)

    def analyze(self, ticker: str, price_df: pd.DataFrame, chip_df: pd.DataFrame) -> Dict[str, Any]:
        """
        分析籌碼並給出評分。
        回傳值包含 'score' (總分) 與 'details' (詳細各項加分)。
        """
        result = {
            'score': 0.0,
            'details': {
                'trust_streak': 0,
                'foreign_streak': 0,
                'trust_net_buy_latest': 0,
                'foreign_net_buy_latest': 0,
                'absorption_score': 0,
                'momentum_score': 0,
                'concentration_score': 0
            }
        }
        
        if chip_df is None or chip_df.empty or price_df is None or price_df.empty:
            return result
            
        # 確保只有近期的資料用來計算 streak (例如近 20 天)
        recent_chips = chip_df.tail(20).copy()
        if recent_chips.empty:
            return result
            
        # 1. 投信與外資的「連買天數」 (Streak)
        trust_streak = self._get_consecutive_days(recent_chips, 'trust_net_buy')
        foreign_streak = self._get_consecutive_days(recent_chips, 'foreign_net_buy')
        
        result['details']['trust_streak'] = trust_streak
        result['details']['foreign_streak'] = foreign_streak
        
        # 計算 Momentum Score (上限 50 分)
        momentum_score = 0
        # 以投信或外資兩者表現較佳的來給分，但投信優先
        best_streak = max(trust_streak, foreign_streak)
        
        if best_streak >= self.streak_high:
            momentum_score += 40
        elif best_streak >= self.streak_mid:
            momentum_score += 30
        elif best_streak >= 3: # 補充 3 天小連買
            momentum_score += 10
            
        result['details']['momentum_score'] = min(momentum_score, 50)
        
        # 2. Volume Absorption (吸籌力道)
        # 取得最近一天的籌碼與 5 日均量
        latest_chip = recent_chips.iloc[-1]
        trust_net_buy_latest = latest_chip.get('trust_net_buy', 0)
        foreign_net_buy_latest = latest_chip.get('foreign_net_buy', 0)
        # Guard against None/NaN values from sparse chip data
        if trust_net_buy_latest is None or pd.isna(trust_net_buy_latest):
            trust_net_buy_latest = 0
        if foreign_net_buy_latest is None or pd.isna(foreign_net_buy_latest):
            foreign_net_buy_latest = 0

        result['details']['trust_net_buy_latest'] = trust_net_buy_latest
        result['details']['foreign_net_buy_latest'] = foreign_net_buy_latest
        
        # 取得最近 5 日均量 (需對齊日期，簡化作法取 price_df.tail(5))
        recent_5d_vol = price_df['Volume'].tail(5).mean()
        
        absorption_score = 0
        if pd.notna(recent_5d_vol) and recent_5d_vol > 0:
            # 以買超最多的法人來算
            max_net_buy = max(trust_net_buy_latest, foreign_net_buy_latest)
            if max_net_buy > 0:
                # 條件 A: 每日穩定 500~1500 張 (500k 股)
                if max_net_buy >= self.target_shares:
                    absorption_score += 10
                
                # 條件 B: 佔 5 日均量 > 3%
                if (max_net_buy / recent_5d_vol) > self.absorption_ratio:
                    absorption_score += 20
                    
        result['details']['absorption_score'] = min(absorption_score, 30)
        
        # 3. Big Player Concentration (大戶籌碼 - 領先指標)
        concentration_score = 0
        if 'big_player_1000' in recent_chips.columns:
            # 取得最新一週與四週前的資料 (假設資料按日期排序)
            # 大戶資料通常每週更新，所以我們看趨勢比看單一數值重要
            bp_data = recent_chips['big_player_1000'].dropna()
            if len(bp_data) >= 2:
                latest_bp = bp_data.iloc[-1]
                # 尋找約 4 週前 (20 交易日) 的資料，若不夠則取最舊的
                prev_bp = bp_data.iloc[-20] if len(bp_data) >= 20 else bp_data.iloc[0]
                
                # 條件 A: 大戶持股比例絕對值高 (例如 > 40%)
                if latest_bp > 40:
                    concentration_score += 10
                
                # 條件 B: 大戶持股比例正在增加 (代表籌碼集中，大單進場)
                if latest_bp > prev_bp:
                    diff = latest_bp - prev_bp
                    if diff > 1.0: # 增加超過 1%
                        concentration_score += 20
                    else:
                        concentration_score += 10
            
        result['details']['concentration_score'] = min(concentration_score, 30)
        
        # 總分 (目前上限為 40+30+0 = 70，若滿分要 100，將依比例給予)
        total_score = result['details']['momentum_score'] + result['details']['absorption_score'] + result['details']['concentration_score']
        
        # 如果大戶資料尚未實作，滿分為 70，我們將其 normalize 到 100
        # 這樣在 scorer 中 20% weight 才能發揮效用
        normalized_score = (total_score / 70.0) * 100.0 if total_score > 0 else 0.0
        
        result['score'] = min(normalized_score, 100.0)
        return result

    def _get_consecutive_days(self, df: pd.DataFrame, col_name: str) -> int:
        """計算連續大於 0 的天數 (從最後一天往前推)"""
        if col_name not in df.columns:
            return 0
            
        streak = 0
        # 反轉 DataFrame 從最新往舊算
        for val in df[col_name][::-1]:
            if pd.isna(val) or val <= 0:
                break
            streak += 1
        return streak
