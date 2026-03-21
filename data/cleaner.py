import pandas as pd
from utils.logger import get_logger

logger = get_logger(__name__)


class DataCleaner:
    """
    負責過濾不符合條件的股票，例如：
    - 成交量過低 (5 日均量 < 500 張)
    - 沒有足夠的歷史資料 (K 線筆數不足)
    """

    def __init__(self, min_vol_lots: int = 500, min_bars: int = 60):
        """
        min_vol_lots: 5 日均量的最低門檻 (單位: 張，即 1000 股)
        min_bars: 最少需要多少根 K 線才進行分析
        """
        self.min_vol_lots = min_vol_lots
        self.min_bars = min_bars

    def is_valid(self, ticker: str, df: pd.DataFrame) -> bool:
        """判斷這檔股票是否符合篩選條件，回傳 True/False"""
        if df is None or df.empty:
            logger.debug(f"  [{ticker}] 過濾: 資料為空")
            return False

        if len(df) < self.min_bars:
            logger.debug(f"  [{ticker}] 過濾: K 線筆數不足 ({len(df)} < {self.min_bars})")
            return False

        vol_col = 'Volume' if 'Volume' in df.columns else 'volume'
        avg_5d_vol = df[vol_col].tail(5).mean()
        avg_5d_lots = avg_5d_vol / 1000  # 股 → 張

        if avg_5d_lots < self.min_vol_lots:
            logger.debug(
                f"  [{ticker}] 過濾: 5日均量 {avg_5d_lots:.0f} 張 < 門檻 {self.min_vol_lots} 張"
            )
            return False

        return True

    def filter_batch(self, stock_data: dict) -> dict:
        """
        對批次下載的股票字典進行過濾
        stock_data: {ticker: DataFrame, ...}
        回傳: 過濾後的 dict
        """
        valid = {}
        for ticker, df in stock_data.items():
            if self.is_valid(ticker, df):
                valid[ticker] = df

        logger.info(f"[DataCleaner] 從 {len(stock_data)} 檔過濾後剩餘 {len(valid)} 檔合格標的")
        return valid
