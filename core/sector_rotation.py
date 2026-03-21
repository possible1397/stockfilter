"""
族群資金輪動分析模組 (Sector Rotation Engine)
功能:
  - 建立台股產業分類對照表 (上市用 TWSE OpenAPI，上櫃用靜態備援)
  - 計算各族群的資金佔比與強度
  - 篩選出近期強勢族群標的
"""
import pandas as pd
import requests
import os
import json

import certifi

from utils.logger import get_logger

logger = get_logger(__name__)

_SSL_VERIFY = certifi.where()

# TWSE 官方產業代碼 → 中文名稱對照
TWSE_INDUSTRY_CODE = {
    "01": "水泥工業",   "02": "食品工業",     "03": "塑膠工業",   "04": "紡織纖維",
    "05": "電機機械",   "06": "電器電纜",     "07": "化學生技醫療","08": "玻璃陶瓷",
    "09": "造紙工業",   "10": "鋼鐵工業",     "11": "橡膠工業",   "12": "汽車工業",
    "13": "電子工業",   "14": "建材營造",     "15": "航運業",     "16": "觀光事業",
    "17": "金融保險業", "18": "貿易百貨",     "19": "綜合",       "20": "其他",
    "21": "半導體業",
    "22": "電腦及周邊設備業",
    "23": "光電業",
    "24": "半導體業",
    "25": "電子零組件業",
    "26": "電子通路業",
    "27": "通信網路業",
    "28": "其他電子業",
    "29": "文化創意業",
    "30": "農業科技業",
    "31": "電子商務業",
    "32": "觀光餐旅",
    "33": "綠能環保",
    "34": "數位雲端",
    "35": "運動休閒",
    "36": "居家生活",
}

# OTC (上櫃) 備援分類表 — 擴充版
OTC_FALLBACK_SECTOR = {
    # 半導體
    "8069.TWO": "半導體業", "6202.TWO": "半導體業", "3661.TWO": "半導體業",
    "6510.TWO": "半導體業", "6515.TWO": "半導體業", "6579.TWO": "半導體業",
    "3008.TWO": "半導體業", "6488.TWO": "半導體業", "6416.TWO": "半導體業",
    "5347.TWO": "半導體業", "6176.TWO": "半導體業", "3526.TWO": "半導體業",
    "6533.TWO": "半導體業", "6643.TWO": "半導體業", "6770.TWO": "半導體業",
    "3533.TWO": "半導體業", "6789.TWO": "半導體業",
    # AI/伺服器/散熱
    "6669.TWO": "電腦及周邊設備業", "3596.TWO": "電腦及周邊設備業",
    "3023.TWO": "其他電子業", "6277.TWO": "電腦及周邊設備業",
    "3005.TWO": "電腦及周邊設備業", "3706.TWO": "電腦及周邊設備業",
    # 面板/光電
    "3580.TWO": "光電業", "3545.TWO": "光電業", "3576.TWO": "光電業",
    "3563.TWO": "光電業", "6550.TWO": "光電業", "3703.TWO": "光電業",
    "6271.TWO": "光電業",
    # 網通/電信
    "6285.TWO": "通信網路業", "4977.TWO": "通信網路業",
    "3047.TWO": "通信網路業", "6152.TWO": "通信網路業",
    # 生技/醫療
    "4174.TWO": "生技醫療業", "4167.TWO": "生技醫療業", "6547.TWO": "生技醫療業",
    "4144.TWO": "生技醫療業", "4153.TWO": "生技醫療業", "6461.TWO": "生技醫療業",
    "4770.TWO": "生技醫療業", "4931.TWO": "生技醫療業",
    # 電子零組件
    "6179.TWO": "電子零組件業", "2392.TWO": "電子零組件業", "3017.TWO": "電子零組件業",
    "6239.TWO": "電子零組件業",
    # 金融
    "5880.TWO": "金融保險業",
    # 航運
    "2640.TWO": "航運業",
    # 建設
    "5604.TWO": "建材營造業",
    # 食品
    "1268.TWO": "食品工業",
    # 綠能/電動車
    "6451.TWO": "綠能環保", "3519.TWO": "綠能環保", "6409.TWO": "綠能環保",
    # 其他電子
    "3024.TWO": "其他電子業", "6666.TWO": "其他電子業",
}


def _get(url: str, timeout: int = 15):
    """統一 requests.get：優先 certifi，SSL 失敗時回退並警告。"""
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        return requests.get(url, headers=headers, timeout=timeout, verify=_SSL_VERIFY)
    except requests.exceptions.SSLError:
        logger.warning(f"[SectorRotation] SSL 驗證失敗，以不驗證模式重試: {url}")
        return requests.get(url, headers=headers, timeout=timeout, verify=False)


class SectorRotationEngine:
    def __init__(self, cache_file='sector_list_cache.json'):
        self.cache_file = cache_file
        self.sector_map = self._load_cache()

    def _load_cache(self):
        if os.path.exists(self.cache_file):
            with open(self.cache_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if len(data) > 10:
                    return data
        return {}

    def _save_cache(self):
        with open(self.cache_file, 'w', encoding='utf-8') as f:
            json.dump(self.sector_map, f, ensure_ascii=False, indent=2)

    def update_sector_map(self):
        """
        從正確來源取得所有股票的產業分類
        上市 (TW)：TWSE OpenAPI t187ap03_L
        上櫃 (TWO)：供應鏈資料 + 靜態備援表
        """
        logger.info("[SectorRotation] 正在更新產業分類清單...")
        new_map = {}

        # ── 上市 (TWSE) ──────────────────────────────────────────
        url_twse = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
        try:
            resp = _get(url_twse)
            if resp.status_code == 200:
                data = resp.json()
                for item in data:
                    code        = item.get('公司代號', '').strip()
                    industry_cd = item.get('產業別', '').strip()
                    industry = TWSE_INDUSTRY_CODE.get(industry_cd, industry_cd)
                    if code and industry:
                        new_map[f"{code}.TW"] = industry
                        new_map[code] = industry
                logger.info(f"  [TWSE] 成功取得 {len(new_map)} 筆上市產業分類。")
            else:
                logger.warning(f"  [TWSE] API 回應異常: HTTP {resp.status_code}")
        except Exception as e:
            logger.error(f"  [TWSE] API 抓取失敗: {e}")

        # ── 上櫃：從供應鏈資料提取 ──────────────────────────────
        otc_count = 0
        supply_chain_file = 'data/supply_chain_data.json'
        if os.path.exists(supply_chain_file):
            try:
                with open(supply_chain_file, 'r', encoding='utf-8') as f:
                    sc_data = json.load(f)
                for industry_name, sub_chain in sc_data.items():
                    if industry_name.startswith('_'):
                        continue
                    for layer_name, layer_data in sub_chain.items():
                        for tk in layer_data.get('tickers', []):
                            if tk not in new_map:
                                new_map[tk] = industry_name
                            base_tk = tk.split('.')[0]
                            if base_tk not in new_map:
                                new_map[base_tk] = industry_name
                                otc_count += 1
            except Exception as e:
                logger.error(f"  [SupplyChain] 讀取供應鏈分類失敗: {e}")

        # ── 靜態備援保底 ─────────────────────────────────────────
        for ticker, industry in OTC_FALLBACK_SECTOR.items():
            if ticker not in new_map:
                new_map[ticker] = industry
            base_ticker = ticker.split('.')[0]
            if base_ticker not in new_map:
                new_map[base_ticker] = industry
                otc_count += 1
        logger.info(f"  [OTC]  導入 {otc_count} 筆備援上櫃/供應鏈產業分類。")

        if not new_map:
            logger.warning("  [SectorRotation] 警告: 無法更新產業資料，保留舊快取。")
            return

        self.sector_map = new_map
        self._save_cache()
        logger.info(f"[SectorRotation] 更新完成，共 {len(new_map)} 筆產業資料。")

    def ensure_sector_map(self):
        """若快取筆數不足則自動更新"""
        if len(self.sector_map) < 50:
            self.update_sector_map()

    def get_sector(self, ticker: str) -> str:
        res = self.sector_map.get(ticker)
        if not res:
            base_symbol = ticker.split(".")[0]
            res = self.sector_map.get(base_symbol, "未知")
        return res

    def get_top_sectors(self, df_dict: dict, top_n: int = 5) -> pd.DataFrame:
        """三維度族群分析: 動能 + 廣度 + 加速度"""
        self.ensure_sector_map()
        logger.info(f"[SectorRotation] 正在分析 {len(df_dict)} 檔個股的族群動能 (三維度)...")

        stock_metrics = []
        for ticker, df in df_dict.items():
            if df.empty or len(df) < 20:
                continue
            sector = self.get_sector(ticker)
            if sector == "未知":
                continue
            try:
                closes = df['Close'].values.flatten()
                volumes = df['Volume'].values.flatten()

                momentum = (closes[-1] - closes[-6]) / closes[-6] * 100 if len(closes) >= 6 else 0.0
                is_up = 1 if closes[-1] > closes[-2] else 0
                recent_value = float((closes[-5:] * volumes[-5:]).mean()) if len(closes) >= 5 else 0
                avg_value = float((closes[-20:] * volumes[-20:]).mean()) if len(closes) >= 20 else recent_value
                accel = recent_value / avg_value if avg_value > 0 else 1.0

                stock_metrics.append({
                    'sector': sector, 'ticker': ticker,
                    'momentum': momentum, 'is_up': is_up, 'accel': accel,
                })
            except Exception:
                continue

        if not stock_metrics:
            return pd.DataFrame()

        df_all = pd.DataFrame(stock_metrics)
        sector_stats = df_all.groupby('sector').agg(
            momentum=('momentum', 'mean'),
            breadth=('is_up', 'mean'),
            acceleration=('accel', 'mean'),
            count=('ticker', 'count')
        ).reset_index()

        sector_stats = sector_stats[sector_stats['count'] >= 2].copy()
        if sector_stats.empty:
            return pd.DataFrame()

        def _normalize(series):
            mn, mx = series.min(), series.max()
            if mx - mn == 0:
                return pd.Series([50.0] * len(series), index=series.index)
            return (series - mn) / (mx - mn) * 100

        sector_stats['m_score'] = _normalize(sector_stats['momentum'])
        sector_stats['b_score'] = _normalize(sector_stats['breadth'])
        sector_stats['a_score'] = _normalize(sector_stats['acceleration'])
        sector_stats['composite'] = (
            sector_stats['m_score'] * 0.4 +
            sector_stats['b_score'] * 0.3 +
            sector_stats['a_score'] * 0.3
        )

        return sector_stats.sort_values('composite', ascending=False).head(top_n)

    def get_sector_score(self, ticker: str, top_sectors_df: pd.DataFrame) -> float:
        """根據個股所在產業在三維度排行中的位置，給予分數 (0-100)"""
        sector = self.get_sector(ticker)
        if sector == "未知" or top_sectors_df.empty:
            return 20.0
        try:
            match = top_sectors_df[top_sectors_df['sector'] == sector]
            if match.empty:
                return 30.0
            return float(match.iloc[0]['composite'])
        except Exception:
            return 30.0

    def filter_by_sector(self, tickers: list, top_sectors_df: pd.DataFrame) -> list:
        """只保留屬於強勢族群的股票"""
        if top_sectors_df.empty:
            return tickers
        strong_sectors = set(top_sectors_df['sector'].tolist())
        filtered = [t for t in tickers if self.get_sector(t) in strong_sectors]
        logger.info(
            f"[SectorRotation] 族群過濾: {len(tickers)} -> {len(filtered)} 檔 "
            f"(強勢族群: {', '.join(strong_sectors)})"
        )
        return filtered


if __name__ == "__main__":
    engine = SectorRotationEngine()
    engine.update_sector_map()
    print(f"族群分析模組初始化完成。目前共有 {len(engine.sector_map)} 筆產業標籤。")
    print(f"測試 2330.TW 的產業: {engine.get_sector('2330.TW')}")
    print(f"測試 3580.TWO 的產業: {engine.get_sector('3580.TWO')}")
    print(f"測試 4770.TWO 的產業: {engine.get_sector('4770.TWO')}")
