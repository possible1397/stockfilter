"""
產業供應鏈關係模組 (Supply Chain Module)
功能:
  - 載入 supply_chain_data.json 的上/中/下游分類
  - 查詢個股的供應鏈位置
  - 計算供應鏈動能 (上下游同步上漲 = 強化信號)
  - 偵測 Leader-Laggard 效應 (領頭股已突破 → 落後股機會)
"""
import json
import os
from datetime import datetime, date
from typing import Optional

import numpy as np

from utils.logger import get_logger

logger = get_logger(__name__)

_DATA_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          'data', 'supply_chain_data.json')
_STALE_DAYS = 90  # 超過此天數未更新則發出警告


class SupplyChainEngine:
    def __init__(self, data_file: str = _DATA_FILE):
        self.chain_data = self._load_data(data_file)
        self.ticker_index = self._build_index()

    def _load_data(self, data_file: str) -> dict:
        if not os.path.exists(data_file):
            logger.warning(f"[SupplyChain] 找不到 {data_file}")
            return {}
        with open(data_file, 'r', encoding='utf-8') as f:
            raw = json.load(f)

        # 版本標記檢查
        metadata = raw.get('_metadata', {})
        industries = metadata.get('total_industries', '?')
        last_updated = metadata.get('last_updated', metadata.get('updated_at', ''))
        logger.info(f"[SupplyChain] 載入供應鏈資料 ({industries} 條產業鏈，更新時間: {last_updated or '未記錄'})")
        if last_updated:
            try:
                # 支援 "YYYY-MM-DD" 及 "YYYY-MM-DD HH:MM:SS" 兩種格式
                updated_date = datetime.strptime(last_updated[:10], '%Y-%m-%d').date()
                days_old = (date.today() - updated_date).days
                if days_old > _STALE_DAYS:
                    logger.warning(
                        f"[SupplyChain] 供應鏈資料已 {days_old} 天未更新 (超過 {_STALE_DAYS} 天)，"
                        f"建議執行 scripts/update_supply_chain.py 更新。"
                    )
            except ValueError:
                pass

        return {k: v for k, v in raw.items() if not k.startswith('_')}

    def _build_index(self) -> dict:
        """建立 ticker → [(供應鏈名, 上/中/下游位置)] 的反查索引"""
        index = {}
        for chain_name, levels in self.chain_data.items():
            for level_name, level_info in levels.items():
                for ticker in level_info.get('tickers', []):
                    if ticker not in index:
                        index[ticker] = []
                    index[ticker].append((chain_name, level_name))
                    symbol = ticker.split('.')[0]
                    if symbol not in index:
                        index[symbol] = []
                    if (chain_name, level_name) not in index[symbol]:
                        index[symbol].append((chain_name, level_name))
        return index

    def _get_positions(self, ticker: str) -> list:
        pos = self.ticker_index.get(ticker)
        if not pos:
            symbol = ticker.split('.')[0]
            pos = self.ticker_index.get(symbol)
        return pos or []

    def _get_price_df(self, ticker: str, price_data: dict):
        if ticker in price_data:
            return price_data[ticker]
        symbol = ticker.split('.')[0]
        tw_df = price_data.get(f"{symbol}.TW")
        if tw_df is not None:
            return tw_df
        return price_data.get(f"{symbol}.TWO")

    def get_position(self, ticker: str) -> Optional[str]:
        """查詢個股的供應鏈位置，回傳 "半導體/中游" 或 None"""
        positions = self._get_positions(ticker)
        if not positions:
            return None
        chain, level = positions[0]
        return f"{chain}/{level}"

    def get_all_positions(self, ticker: str) -> list:
        """查詢個股的所有供應鏈位置 (一股多鏈情況)"""
        return self._get_positions(ticker)

    @staticmethod
    def _has_chinese(text: str) -> bool:
        """檢查字串是否包含中文字元"""
        return any('\u4e00' <= ch <= '\u9fff' for ch in text)

    def get_product_description(self, ticker: str) -> str:
        """取得個股主要產品描述，優先使用鉅亨網爬取的資料（僅中文）"""
        # 1. 優先用鉅亨網批次爬取的產品描述
        product_file = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'data', 'product_descriptions.json'
        )
        if os.path.exists(product_file):
            try:
                with open(product_file, encoding='utf-8') as f:
                    products = json.load(f)

                # 嘗試多種 key 格式: 原始 ticker → 純數字.TW → 純數字.TWO → 純數字
                symbol = ticker.split('.')[0]
                candidates = [ticker, f"{symbol}.TW", f"{symbol}.TWO", symbol]
                for key in candidates:
                    desc = products.get(key, '')
                    if desc and self._has_chinese(desc):
                        return desc
            except Exception:
                pass

        # 2. fallback: 供應鏈 JSON 裡的 description 欄位
        positions = self._get_positions(ticker)
        if not positions:
            return ""
        chain_name, level_name = positions[0]
        desc = self.chain_data.get(chain_name, {}).get(level_name, {}).get('description', '')
        if '頁面自動彙整' in desc or not self._has_chinese(desc):
            return ""
        return desc

    def get_related_tickers(self, ticker: str) -> dict:
        """取得同供應鏈上下游的所有 ticker"""
        positions = self._get_positions(ticker)
        if not positions:
            return {}
        chain_name = positions[0][0]
        chain = self.chain_data.get(chain_name, {})
        return {
            level_name: [t for t in level_info.get('tickers', []) if t != ticker]
            for level_name, level_info in chain.items()
        }

    def get_chain_momentum(self, ticker: str, price_data: dict) -> float:
        """計算整條供應鏈的動能分數 (0-100)"""
        related = self.get_related_tickers(ticker)
        if not related:
            return 50.0

        all_returns = []
        for tickers in related.values():
            for t in tickers:
                df = self._get_price_df(t, price_data)
                if df is None or df.empty or len(df) < 6:
                    continue
                closes = df['Close'].values.flatten()
                ret_5d = (closes[-1] - closes[-6]) / closes[-6] * 100
                all_returns.append(ret_5d)

        if not all_returns:
            return 50.0

        avg_return = np.mean(all_returns)
        up_ratio = sum(1 for r in all_returns if r > 0) / len(all_returns)
        return_score = max(0, min(60, 30 + avg_return * 6))
        sync_score = up_ratio * 40
        return round(return_score + sync_score, 1)

    def detect_chain_leaders(self, ticker: str, price_data: dict,
                             breakout_check_fn=None) -> dict:
        """偵測供應鏈中的領頭股 (已突破的) 和落後股 (仍在收斂的)"""
        positions = self.ticker_index.get(ticker)
        if not positions:
            return {'chain': None, 'position': None, 'leaders': [], 'has_leader': False}

        chain_name, my_level = positions[0]
        chain = self.chain_data.get(chain_name, {})
        leaders = []

        for level_name, level_info in chain.items():
            for t in level_info.get('tickers', []):
                if t.split('.')[0] == ticker.split('.')[0]:
                    continue
                df = self._get_price_df(t, price_data)
                if df is None or df.empty or len(df) < 6:
                    continue
                closes = df['Close'].values.flatten()
                ret_5d = (closes[-1] - closes[-6]) / closes[-6] * 100
                if ret_5d > 3.0:
                    leaders.append({'ticker': t, 'level': level_name, 'return_5d': round(ret_5d, 2)})

        return {
            'chain': chain_name,
            'position': my_level,
            'leaders': sorted(leaders, key=lambda x: x['return_5d'], reverse=True),
            'has_leader': len(leaders) > 0
        }

    def get_chain_score(self, ticker: str, price_data: dict) -> float:
        """綜合供應鏈分數 (0-100): 動能 + 領頭股效應"""
        momentum = self.get_chain_momentum(ticker, price_data)
        leader_info = self.detect_chain_leaders(ticker, price_data)
        leader_bonus = min(20, len(leader_info['leaders']) * 7) if leader_info['has_leader'] else 0
        return min(100, momentum * 0.8 + leader_bonus)

    def get_all_chains(self) -> list:
        return list(self.chain_data.keys())

    def get_chain_tickers(self, chain_name: str) -> list:
        chain = self.chain_data.get(chain_name, {})
        all_tickers = []
        for level_info in chain.values():
            all_tickers.extend(level_info.get('tickers', []))
        return list(set(all_tickers))


if __name__ == "__main__":
    engine = SupplyChainEngine()
    print(f"=== 產業供應鏈模組 ===")
    print(f"涵蓋 {len(engine.chain_data)} 條產業鏈")
    print(f"索引涵蓋 {len(engine.ticker_index)} 檔個股")
    print()
    test_tickers = ['2330.TW', '2454.TW', '3580.TWO', '1723.TW', '6179.TWO']
    for t in test_tickers:
        pos = engine.get_position(t)
        related = engine.get_related_tickers(t)
        print(f"  {t:10} → 位置: {pos or '無資料':15} | "
              f"上游: {len(related.get('上游', []))} "
              f"中游: {len(related.get('中游', []))} "
              f"下游: {len(related.get('下游', []))}")
