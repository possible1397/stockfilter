"""
族群資金輪動分析模組 (Sector Rotation Engine)
功能:
  - 建立台股產業分類對照表 (上市用 TWSE OpenAPI，上櫃用 TPEX OpenAPI + 靜態備援)
  - 計算各族群的資金佔比與強度
  - 篩選出近期強勢族群標的
"""
import time
import pandas as pd
import requests
import os
import json

import certifi

from utils.logger import get_logger

logger = get_logger(__name__)

_SSL_VERIFY = certifi.where()

CACHE_TTL_DAYS = 7  # 快取有效期 7 天，到期自動重抓

# TWSE 官方產業代碼 → 中文名稱對照
# 驗證來源: TWSE OpenAPI t187ap03_L 實際公司 + Yahoo Finance 台股產業別標籤 (2026-05-13)
# 代碼 21-23: 化工/生技/油電燃氣（非電子業），原程式誤從 21 開始標電子子分類
# 代碼 24-31: 電子業子分類，從 24(半導體) 開始
# 代碼 35-38: 新興/生活類，35=綠能、36=數位雲端、37=運動休閒、38=居家生活
TWSE_INDUSTRY_CODE = {
    "01": "水泥工業",   "02": "食品工業",     "03": "塑膠工業",   "04": "紡織纖維",
    "05": "電機機械",   "06": "電器電纜",     "07": "化學生技醫療","08": "玻璃陶瓷",
    "09": "造紙工業",   "10": "鋼鐵工業",     "11": "橡膠工業",   "12": "汽車工業",
    "13": "電子工業",   "14": "建材營造業",   "15": "航運業",     "16": "觀光事業",
    "17": "金融保險業", "18": "貿易百貨",     "19": "綜合",       "20": "其他",
    # 化工 / 生技 / 能源（21-23，非電子）
    "21": "化學工業",
    "22": "生技醫療業",
    "23": "油電燃氣業",
    # 電子業子分類（24 起）
    "24": "半導體業",
    "25": "電腦及周邊設備業",
    "26": "光電業",
    "27": "通信網路業",
    "28": "電子零組件業",
    "29": "電子通路業",
    "30": "資訊服務業",
    "31": "其他電子業",
    # 新興分類（32-34 目前無上市股，保留備用）
    "32": "文化創意業",
    "33": "農業科技業",
    "34": "電子商務業",
    # 生活 / 新經濟（35-38）
    "35": "綠能環保業",
    "36": "數位雲端業",
    "37": "運動休閒業",
    "38": "居家生活業",
    "91": "存託憑證",
}

# 第二層：主題族群細分 (手動維護映射表)
# 來源: docs/sector_classification.md
THEME_SECTOR_MAP = {
    # 半導體
    "2330": ["晶圓代工", "CoWoS", "3DIC", "AI供應鏈", "Apple供應鏈"],
    "2303": ["晶圓代工", "3DIC"],
    "6770": ["晶圓代工"],
    "5347": ["晶圓代工"],
    "2454": ["IC設計", "車用電子"],
    "2379": ["IC設計", "車用電子"],
    "3034": ["IC設計"],
    "3035": ["IC設計"],
    "6415": ["IC設計"],
    "3711": ["封測", "3DIC", "車用電子"],
    "6257": ["封測", "FOPLP", "車用電子"],
    "2449": ["封測"],
    "2441": ["封測"],
    "6239": ["封測"],
    "3189": ["IC載板", "CoWoS"],
    "8046": ["IC載板", "CoWoS", "FOPLP"],
    "3037": ["IC載板", "CoWoS", "FOPLP", "PCB整合"],
    "6153": ["IC載板", "PCB軟板"],
    "6274": ["IC載板", "PCB硬板", "PCB材料"],
    "2408": ["HBM"],
    "2344": ["HBM"],
    "2337": ["HBM"],
    "6533": ["矽智財", "工業電腦"],
    "6643": ["矽智財"],
    "3661": ["ASIC"],
    "3443": ["ASIC"],
    "4174": ["ASIC", "防疫"], # 瑞基 / 昂寶-KY
    "2351": ["導線架"],
    "2219": ["導線架"],
    "8261": ["功率半導體"],
    "5425": ["功率半導體"],
    "2481": ["功率半導體"],
    "2338": ["光罩"],
    
    # 電腦/周邊
    "2382": ["AI伺服器", "筆電ODM", "AI供應鏈"],
    "2356": ["AI伺服器", "筆電ODM", "AI供應鏈"],
    "6669": ["AI伺服器", "AI供應鏈"],
    "3231": ["AI伺服器", "筆電ODM", "電子代工", "AI供應鏈"],
    "2376": ["AI伺服器"],
    "2324": ["筆電ODM"],
    "4938": ["筆電ODM", "電子代工"],
    "3324": ["散熱模組", "AI供應鏈"],
    "3017": ["散熱模組", "AI供應鏈"],
    "2313": ["散熱模組"],
    "2308": ["散熱模組", "BBU", "電源供應器", "機器人", "儲能", "電動車", "AI供應鏈"],
    "3604": ["散熱模組"],
    "2474": ["機殼", "Apple供應鏈"],
    "2354": ["機殼"],
    "8163": ["機殼"],
    "3617": ["BBU", "電源供應器"],
    "6282": ["BBU", "電源供應器"],
    
    # 光電
    "2409": ["大型面板"],
    "3481": ["大型面板"],
    "8069": ["電子紙"],
    "2393": ["LED", "MiniLED"],
    "2448": ["LED", "MiniLED"],
    "6168": ["LED"],
    "6854": ["MiniLED"],
    "4979": ["光通訊"],
    "3374": ["光通訊"],
    "3163": ["光通訊"],
    "4721": ["玻璃基板"],
    
    # 電子零組件
    "4958": ["PCB硬板", "PCB整合"],
    "3044": ["PCB硬板", "PCB整合"],
    "2383": ["PCB硬板", "PCB材料", "PCB整合"],
    "2368": ["PCB硬板"],
    "6269": ["PCB軟板"],
    "3010": ["PCB軟板"],
    "6223": ["PCB材料"],
    "4961": ["PCB材料"],
    "2327": ["被動元件"],
    "3026": ["被動元件"],
    "6173": ["被動元件"],
    "2492": ["被動元件"],
    "2392": ["連接器"],
    "5457": ["連接器"],
    "3411": ["連接器"],
    "6536": ["連接器"],
    "6412": ["電源供應器"],
    "2484": ["石英元件"],
    "3042": ["石英元件"],
    "6224": ["保護元件", "儲能"],
    "3465": ["保護元件"],
    "6165": ["濾波器"],
    "2326": ["濾波器", "低軌衛星"],
    
    # 其他電子
    "2317": ["電子代工", "Apple供應鏈"],
    "2395": ["工業電腦"],
    "6166": ["工業電腦"],
    "2049": ["機器人"],
    "4585": ["機器人"],
    "1597": ["機器人"],
    "8495": ["無人機"],
    "3265": ["無人機", "低軌衛星"],
    
    # 通信/網通
    "2412": ["電信商"],
    "3045": ["電信商"],
    "4904": ["電信商"],
    "2345": ["網通設備"],
    "2332": ["網通設備"],
    "3704": ["網通設備"],
    "4946": ["低軌衛星"],
    
    # 航運
    "2603": ["貨櫃航運"],
    "2609": ["貨櫃航運"],
    "2615": ["貨櫃航運"],
    "2637": ["散裝航運"],
    "2606": ["散裝航運"],
    "5608": ["散裝航運"],
    "2610": ["航空"],
    "2618": ["航空"],
    "6757": ["航空"],
    "2634": ["軍工國防"],
    
    # 生技
    "6446": ["新藥開發"],
    "4128": ["新藥開發"],
    "4152": ["新藥開發"],
    "1720": ["學名藥"],
    "1752": ["學名藥"],
    "1734": ["學名藥"],
    "4104": ["醫療器材"],
    "4126": ["醫療器材"],
    "4103": ["醫療器材"],
    "6589": ["CDMO"],
    "6472": ["CDMO"],
    "4138": ["醫美"],
    
    # 綠能
    "3663": ["離岸風電"],
    "3708": ["離岸風電"],
    "6244": ["太陽能"],
    "6443": ["太陽能"],
    "6477": ["太陽能"],
    "5227": ["儲能"],
    "2002": ["碳權"],
    "1101": ["水資源"],
    
    # 運動休閒
    "9921": ["自行車整車"],
    "9914": ["自行車整車"],
    "5306": ["自行車零組件"],
    "4536": ["自行車零組件"],
    "1736": ["運動器材"],
    "1598": ["運動器材"],
    "8462": ["運動器材"],
    "9910": ["運動品牌代工"],
    "9904": ["運動品牌代工"],
    "9802": ["運動品牌代工"],
    "8478": ["遊艇"],
    
    # 居家生活
    "8464": ["遮陽窗簾"],
    "9911": ["廚衛設備"],
    "9934": ["廚衛設備"],
    "1810": ["廚衛設備"],
    "9924": ["五金零組件"],
    "3557": ["五金零組件"],
    
    # 數位雲端
    "6689": ["雲端服務"],
    "8048": ["雲端服務"],
    "3130": ["人才平台"],
    "6902": ["資安"],
    "2743": ["資安"],
    "8044": ["電商"],
    "8454": ["電商"],
    "6180": ["線上遊戲"],
    "3736": ["線上遊戲"],
    
    # 跨族群
    "3008": ["Apple供應鏈"],
    "6176": ["Apple供應鏈"],
    "1536": ["電動車"],
    "1568": ["電動車"],
    "1525": ["電動車"],
    "1723": ["特用化學"],
}

# 產業名稱正規化對照表 — 消除同義詞造成的族群分裂
# 規則: 統一加「業」後綴，使 TWSE/TPEX/供應鏈/Yahoo Finance 結果一致
SECTOR_NAME_NORMALIZE = {
    # 半導體
    "半導體":           "半導體業",
    # 電腦及周邊
    "電腦及周邊":       "電腦及周邊設備業",
    "電腦週邊":         "電腦及周邊設備業",
    # 通信/通訊（Yahoo Finance 用「通訊」，TWSE 用「通信」）
    "通信網路":         "通信網路業",
    "通訊網路":         "通信網路業",
    # 電子相關
    "電子零組件":       "電子零組件業",
    "電子通路":         "電子通路業",
    "其他電子":         "其他電子業",
    "電子商務":         "電子商務業",
    # 光電
    "光電":             "光電業",
    # 化工 / 生技 / 能源
    "化學":             "化學工業",
    "生技醫療":         "生技醫療業",
    "生技":             "生技醫療業",
    "油電燃氣":         "油電燃氣業",
    # 資訊服務
    "資訊服務":         "資訊服務業",
    # 建材 / 金融
    "建材營造":         "建材營造業",
    "金融保險":         "金融保險業",
    # 新興分類
    "綠能環保":         "綠能環保業",
    "數位雲端":         "數位雲端業",
    "運動休閒":         "運動休閒業",
    "居家生活":         "居家生活業",
    "文化創意":         "文化創意業",
    "農業科技":         "農業科技業",
}


def _normalize_sector(name: str) -> str:
    """正規化產業名稱，消除同義詞造成的分裂"""
    return SECTOR_NAME_NORMALIZE.get(name, name)


# OTC (上櫃) 備援分類表 — TPEX API 無法覆蓋時的最終保底
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
    "6451.TWO": "綠能環保業", "3519.TWO": "綠能環保業", "6409.TWO": "綠能環保業",
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
    """
    族群分析引擎
    資料格式:
    self.sector_map = {
        "ticker": {
            "primary": "大分類名稱",
            "themes": ["主題1", "主題2"]
        }
    }
    """
    def __init__(self, cache_file='sector_list_cache.json'):
        self.cache_file = cache_file
        self.sector_map = self._load_cache()

    def _load_cache(self) -> dict:
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # 判斷舊格式並自動轉換
                    if data and isinstance(list(data.values())[0], str):
                        logger.info("[SectorRotation] 檢測到舊格式快取，自動升級中...")
                        return {k: {"primary": v, "themes": THEME_SECTOR_MAP.get(k.split('.')[0], [])} 
                                for k, v in data.items()}
                    return data
            except Exception as e:
                logger.error(f"[SectorRotation] 讀取快取失敗: {e}")
        return {}

    def _save_cache(self):
        with open(self.cache_file, 'w', encoding='utf-8') as f:
            json.dump(self.sector_map, f, ensure_ascii=False, indent=2)

    def _is_cache_stale(self) -> bool:
        """快取是否已超過 TTL"""
        if not os.path.exists(self.cache_file):
            return True
        age_days = (time.time() - os.path.getmtime(self.cache_file)) / 86400
        return age_days > CACHE_TTL_DAYS

    def _fetch_tpex_sectors(self) -> dict:
        """從 TWSE OpenAPI 取得上櫃 (TPEX/OTC) 股票產業分類"""
        url = "https://openapi.twse.com.tw/v1/opendata/t187ap03_O"
        new_map = {}
        try:
            resp = _get(url, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                count = 0
                for item in data:
                    code = item.get('公司代號', '').strip()
                    industry_raw = item.get('產業別', '').strip()
                    # API 可能回傳代碼 (01) 或名稱 (半導體業)，兩者都處理
                    industry = _normalize_sector(
                        TWSE_INDUSTRY_CODE.get(industry_raw, industry_raw)
                    )
                    if code and industry:
                        themes = THEME_SECTOR_MAP.get(code, [])
                        val = {"primary": industry, "themes": themes}
                        new_map[f"{code}.TWO"] = val
                        new_map[code] = val
                        count += 1
                logger.info(f"  [TPEX/OTC] 成功取得 {count} 筆上櫃產業分類。")
            else:
                logger.warning(f"  [TPEX/OTC] API 回應異常: HTTP {resp.status_code}")
        except Exception as e:
            logger.warning(f"  [TPEX/OTC] API 抓取失敗: {e}")
        return new_map

    def update_sector_map(self):
        """從官方來源更新產業分類，並注入主題族群"""
        logger.info("[SectorRotation] 正在更新產業分類清單 (包含雙層分類)...")
        new_map = {}

        # 1. 上市 (TWSE)
        url_twse = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
        try:
            resp = _get(url_twse)
            if resp.status_code == 200:
                data = resp.json()
                tw_count = 0
                for item in data:
                    code        = item.get('公司代號', '').strip()
                    industry_cd = item.get('產業別', '').strip()
                    industry = _normalize_sector(
                        TWSE_INDUSTRY_CODE.get(industry_cd, industry_cd)
                    )
                    if code and industry:
                        themes = THEME_SECTOR_MAP.get(code, [])
                        val = {"primary": industry, "themes": themes}
                        new_map[f"{code}.TW"] = val
                        new_map[code] = val
                        tw_count += 1
                logger.info(f"  [TWSE] 成功取得 {tw_count} 筆上市產業分類。")
        except Exception as e:
            logger.error(f"  [TWSE] API 抓取失敗: {e}")

        # 2. 上櫃 (TPEX)
        tpex_map = self._fetch_tpex_sectors()
        new_map.update(tpex_map)

        # 3. 靜態備援保底
        for ticker, industry in OTC_FALLBACK_SECTOR.items():
            if ticker not in new_map:
                base_code = ticker.split('.')[0]
                themes = THEME_SECTOR_MAP.get(base_code, [])
                val = {"primary": _normalize_sector(industry), "themes": themes}
                new_map[ticker] = val
                new_map[base_code] = val

        if not new_map:
            logger.warning("  [SectorRotation] 警告: 無法更新產業資料，保留舊快取。")
            return

        self.sector_map = new_map
        self._save_cache()
        logger.info(f"[SectorRotation] 更新完成，共 {len(new_map)} 筆產業資料。")

    def ensure_sector_map(self):
        """若快取不足或已過期 (>{CACHE_TTL_DAYS} 天) 則自動更新"""
        if len(self.sector_map) < 50 or self._is_cache_stale():
            self.update_sector_map()

    def get_sector(self, ticker: str) -> str:
        """取得大分類名稱"""
        res = self.sector_map.get(ticker)
        if not res:
            res = self.sector_map.get(ticker.split(".")[0])
        return res["primary"] if res else "未知"

    def get_themes(self, ticker: str) -> list:
        """取得主題族群列表"""
        res = self.sector_map.get(ticker)
        if not res:
            res = self.sector_map.get(ticker.split(".")[0])
        return res.get("themes", []) if res else []

    def get_all_sectors(self, ticker: str) -> list:
        """取得所有標籤 (大分類 + 主題)"""
        res = self.sector_map.get(ticker)
        if not res:
            res = self.sector_map.get(ticker.split(".")[0])
        if not res:
            return ["未知"]
        return [res["primary"]] + res.get("themes", [])

    def get_top_sectors(self, df_dict: dict, top_n: int = 10) -> pd.DataFrame:
        """
        三維度族群分析 (支持主題族群重複計算)
        """
        self.ensure_sector_map()
        logger.info(f"[SectorRotation] 正在分析 {len(df_dict)} 檔個股的族群動能 (包含主題)...")

        records = []
        for ticker, df in df_dict.items():
            if df.empty or len(df) < 20: continue
            
            # 取得該股所有標籤 (主要產業 + 多個主題)
            all_labels = self.get_all_sectors(ticker)
            if "未知" in all_labels and len(all_labels) == 1: continue

            try:
                closes = df['Close'].values.flatten()
                volumes = df['Volume'].values.flatten()
                
                momentum = (closes[-1] - closes[-6]) / closes[-6] * 100 if len(closes) >= 6 else 0
                is_up = 1 if closes[-1] > closes[-2] else 0
                recent_val = float((closes[-5:] * volumes[-5:]).mean()) if len(closes) >= 5 else 0
                avg_val = float((closes[-20:] * volumes[-20:]).mean()) if len(closes) >= 20 else recent_val
                accel = recent_val / avg_val if avg_val > 0 else 1.0

                for label in all_labels:
                    records.append({
                        'sector': label, 'ticker': ticker,
                        'momentum': momentum, 'is_up': is_up, 'accel': accel
                    })
            except: continue

        if not records: return pd.DataFrame()

        df_all = pd.DataFrame(records)
        stats = df_all.groupby('sector').agg(
            momentum=('momentum', 'mean'),
            breadth=('is_up', 'mean'),
            acceleration=('accel', 'mean'),
            count=('ticker', 'count')
        ).reset_index()

        # 過濾樣本數太少的族群
        stats = stats[stats['count'] >= 2].copy()
        if stats.empty: return pd.DataFrame()

        def _norm(s):
            mn, mx = s.min(), s.max()
            return (s - mn) / (mx - mn) * 100 if (mx - mn) != 0 else 50.0

        stats['m_score'] = _norm(stats['momentum'])
        stats['b_score'] = _norm(stats['breadth'])
        stats['a_score'] = _norm(stats['acceleration'])
        stats['composite'] = stats['m_score'] * 0.4 + stats['b_score'] * 0.3 + stats['a_score'] * 0.3
        
        return stats.sort_values('composite', ascending=False).head(top_n)

    def get_sector_score(self, ticker: str, top_sectors_df: pd.DataFrame) -> float:
        """
        根據個股所在族群（主產業或任一主題）在排行榜中的位置給分。
        採最高分者。
        """
        all_labels = self.get_all_sectors(ticker)
        if "未知" in all_labels or top_sectors_df.empty:
            return 20.0
            
        max_score = 30.0
        for label in all_labels:
            match = top_sectors_df[top_sectors_df['sector'] == label]
            if not match.empty:
                max_score = max(max_score, float(match.iloc[0]['composite']))
        return max_score

    def filter_by_sector(self, tickers: list, top_sectors_df: pd.DataFrame) -> list:
        """只保留屬於強勢族群（主要或主題）的股票"""
        if top_sectors_df.empty:
            return tickers
            
        strong_sectors = set(top_sectors_df['sector'].tolist())
        filtered = []
        for t in tickers:
            labels = self.get_all_sectors(t)
            if any(l in strong_sectors for l in labels):
                filtered.append(t)
                
        logger.info(
            f"[SectorRotation] 族群過濾: {len(tickers)} -> {len(filtered)} 檔 "
            f"(強勢標籤涵蓋: {len(strong_sectors)} 個)"
        )
        return filtered


if __name__ == "__main__":
    engine = SectorRotationEngine()
    engine.update_sector_map()
    print(f"族群分析模組初始化完成。目前共有 {len(engine.sector_map)} 筆產業標籤。")
    print(f"測試 2330.TW 的產業: {engine.get_sector('2330.TW')}")
    print(f"測試 3580.TWO 的產業: {engine.get_sector('3580.TWO')}")
    print(f"測試 4770.TWO 的產業: {engine.get_sector('4770.TWO')}")
