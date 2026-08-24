"""
回測引擎 (Backtest Engine)
功能:
  - 讀取 SQLite 歷史資料，模擬逐日掃描
  - 進場: total_score >= threshold + 收斂/突破確認
  - 出場: 持有天數達上限 / 跌破突破K低點 / ATR移動停利
  - 含手續費 0.1425% + 交易稅 0.3% + 估計滑價 0.1%

優化記錄 (2026-03-28):
  - ATR 追蹤停損改為「浮盈超過門檻後才啟動」，避免剛進場就被掃出
  - 新增 entry_on_next_open: 以次日開盤價進場，更貼近實際執行
  - 新增 require_breakout: 只進突破確認的訊號，提高勝率
  - BacktestConfig 新增 from_yaml() 讀取 config.yaml，統一參數來源
"""
import yaml
import pandas as pd
import numpy as np
import yfinance as yf
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional, Dict

from core.recognizer import PatternRecognizer
from core.market_regime import MarketRegimeDetector
from backtest.metrics import calculate_metrics, BacktestMetrics
from utils.indicators import calculate_atr
from utils.regime import regime_score_at


def _today_str() -> str:
    return datetime.today().strftime('%Y-%m-%d')


@dataclass
class BacktestConfig:
    """回測參數配置"""
    # 時間範圍
    start_date: str = "2025-01-01"
    end_date: str = ""          # 空字串 = 自動使用今日日期

    # PatternRecognizer 參數 (需與 config.yaml pattern: 區段一致)
    order: int = 5
    min_points: int = 3
    min_bars: int = 20
    convergence_threshold: float = 0.3
    long_only: bool = True
    trend_filter: bool = True
    volume_dryup: bool = True

    # 交易參數
    max_hold_days: int = 30           # 最大持有天數
    score_threshold: float = 60.0    # 最低進場總分

    # 風控參數
    stop_loss_pct: float = -7.0       # 固定停損 (%)
    take_profit_pct: float = 20.0     # 固定停利 (%)
    atr_stop_mult: float = 3.0        # ATR 追蹤停損倍數 (0 = 關閉)
    atr_activate_pct: float = 5.0     # ATR 追蹤停損啟動門檻 (浮盈 %)，避免剛進場就被掃

    # 進場邏輯
    require_breakout: bool = False    # True = 只進突破確認的訊號 (更保守、勝率較高)
    entry_on_next_open: bool = True   # True = 以次日開盤價進場 (更貼近實際)

    # 交易成本
    commission_rate: float = 0.001425  # 手續費 (買賣各一次)
    tax_rate: float = 0.003            # 交易稅 (賣出)
    slippage_rate: float = 0.001       # 滑價估計

    # 倉位
    max_positions: int = 5             # 同時最多持有檔數
    position_size_pct: float = 20.0    # 每筆佔總資金比例 (%)

    # 大盤濾網
    use_market_filter: bool = True
    market_threshold: float = 40.0
    require_index_above_ma200: bool = False  # True = 指數必須站上年線才允許進場
    use_ma20_trail: bool = False             # True = 浮盈>5%後改用月線(MA20)追蹤停損

    # 動態倉位 (依市場分數調整最大持倉數)
    dynamic_position: bool = False   # True = 啟用動態倉位
    dp_bull_threshold: float = 60.0  # >= 此分數: 滿倉 (max_positions)
    dp_mid_threshold: float = 45.0   # >= 此分數: 中倉 (dp_mid_slots)
    dp_mid_slots: int = 3            # 中等市況最大持倉數
    dp_bear_slots: int = 2           # 偏弱市況最大持倉數

    # 投資組合級停損 (整體回撤保護)
    portfolio_stop: bool = False     # True = 啟用投資組合級停損
    portfolio_stop_pct: float = 15.0 # 整體從高點回撤超過此值，暫停新進場

    # 族群濾網
    min_grade: str = ""              # "" = 不限, "B" = 只進 A/B, "A" = 只進 A

    # 流動性濾網 (進場當下檢查，避免用「今天的流動性」篩選歷史 = 前視偏差)
    min_vol_lots: int = 500          # 進場前 5 日均量最低門檻 (張)，0 = 停用

    # 進階出場策略
    partial_profit_threshold: float = 15.0   # 獲利 % 達此門檻觸發減碼 (0 = 停用)
    partial_profit_ratio: float = 0.5        # 減碼比例
    dynamic_atr_update: bool = True          # 每日重算 ATR
    k_line_trail: bool = True                # 強勢股用前日 K 線追蹤
    kline_activate_pct: float = None         # K 線追蹤獨立啟動門檻 (None = 同 atr_activate_pct)
                                             # 兩段式出場: 浮盈 8~15% 用較寬的 ATR，
                                             # 深浮盈後才切換到較緊的前日低點追蹤

    @classmethod
    def from_yaml(cls, yaml_path: str = "config.yaml") -> "BacktestConfig":
        """從 config.yaml 的 backtest: 區段讀取參數"""
        try:
            with open(yaml_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            bt = cfg.get("backtest", {})
            sc = cfg.get("scanner", {})
            pat = cfg.get("pattern", {})
            return cls(
                score_threshold=float(bt.get("score_threshold", 60.0)),
                max_positions=int(bt.get("max_positions", 5)),
                position_size_pct=float(bt.get("position_size", 0.2)) * 100,
                stop_loss_pct=-abs(float(bt.get("stop_loss", 0.07)) * 100),
                take_profit_pct=float(bt.get("take_profit", 0.20)) * 100,
                max_hold_days=int(bt.get("max_hold_days", 30)),
                atr_stop_mult=float(bt.get("atr_stop_mult", 3.0)),
                atr_activate_pct=float(bt.get("atr_activate_pct", 5.0)),
                require_breakout=bool(bt.get("require_breakout", False)),
                commission_rate=float(bt.get("commission", 0.001425)),
                tax_rate=float(bt.get("tax", 0.003)),
                slippage_rate=float(bt.get("slippage", 0.001)),
                market_threshold=float(sc.get("market_score_threshold", 40.0)),
                min_vol_lots=int(cfg.get("cleaner", {}).get("min_vol_lots", 500)),
                long_only=bool(pat.get("long_only", True)),
                trend_filter=bool(pat.get("trend_filter", True)),
                volume_dryup=bool(pat.get("volume_dryup", True)),
                # 進階出場策略
                partial_profit_threshold=float(bt.get("partial_profit_threshold", 0.15)) * 100,
                partial_profit_ratio=float(bt.get("partial_profit_ratio", 0.5)),
                dynamic_atr_update=bool(bt.get("dynamic_atr_update", True)),
                k_line_trail=bool(bt.get("k_line_trail", True)),
            )
        except FileNotFoundError:
            print(f"[BacktestConfig] 找不到 {yaml_path}，使用預設值")
            return cls()
        except Exception as e:
            print(f"[BacktestConfig] 讀取 {yaml_path} 失敗: {e}，使用預設值")
            return cls()


@dataclass
class Trade:
    """單筆交易記錄"""
    ticker: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    return_pct: float       # 扣除交易成本的淨報酬率
    hold_days: int
    exit_reason: str        # 'max_hold', 'stop_loss', 'take_profit', 'atr_stop', 'partial_profit', 'kline_stop'
    entry_score: float = 0
    sector: str = ""
    grade: str = ""
    is_partial: bool = False        # 是否為分批平倉的部分交易
    position_ratio: float = 1.0     # 此筆交易佔原始倉位的比例


class BacktestEngine:
    """
    回測引擎
    逐日模擬策略執行，記錄交易結果
    """

    def __init__(self, config: BacktestConfig = None):
        self.config = config or BacktestConfig()
        if not self.config.end_date:
            self.config.end_date = _today_str()
        self.trades: list[Trade] = []
        self.active_positions: dict = {}
        self.market_data: Dict[str, pd.DataFrame] = {}
        self.daily_equity: list = []

    def _load_market_data(self):
        """載入指數歷史資料 (TSE/OTC) — 優先讀 SQLite 快取，離線也能回測"""
        if not self.config.use_market_filter:
            return
        import data.database as db_module
        for key, ticker in (('TSE', '^TWII'), ('OTC', '^TWOII')):
            df = db_module.load_dataframe(ticker)
            if df is None or df.empty:
                print(f"[Backtest] DB 無 {ticker}，改由 yfinance 下載...")
                try:
                    df = yf.download(ticker, period="7y", progress=False)
                except Exception as e:
                    print(f"[Backtest] {ticker} 下載失敗: {e}")
                    df = None
            self.market_data[key] = df

    def _get_market_score(self, date_str: str) -> float:
        """計算特定日期的市場分數 (共用 utils.regime 實作，與實盤一致)"""
        target_ts = pd.Timestamp(date_str)
        tse_score = regime_score_at(self.market_data.get('TSE'), target_ts)
        otc_score = regime_score_at(self.market_data.get('OTC'), target_ts)
        return round(tse_score * 0.6 + otc_score * 0.4, 1)

    def _is_index_above_ma200(self, date_str: str) -> bool:
        """TSE 指數是否站上年線 (MA200)；資料不足時 fallback True"""
        target_ts = pd.Timestamp(date_str)
        df = self.market_data.get('TSE')
        if df is None or df.empty:
            return True  # 無資料不封鎖
        df_up = df[df.index <= target_ts]
        if len(df_up) < 200:
            return True  # 歷史不足 200 天，無法判斷，不封鎖
        closes = df_up['Close'].squeeze()
        if isinstance(closes, pd.DataFrame):
            closes = closes.iloc[:, 0]
        ma200 = float(closes.rolling(200).mean().iloc[-1])
        if np.isnan(ma200):
            return True  # MA200 為 NaN，不封鎖
        price = float(closes.iloc[-1])
        return price >= ma200

    def _calculate_transaction_cost(self, is_buy: bool) -> float:
        """計算交易成本比率"""
        cost = self.config.commission_rate + self.config.slippage_rate
        if not is_buy:
            cost += self.config.tax_rate
        return cost

    def _check_exit(self, _ticker: str, pos: dict, current_bar: pd.Series,
                    day_count: int, ma20: float = None,
                    df_up_to: pd.DataFrame = None) -> Optional[tuple]:
        """
        檢查是否觸發出場條件
        回傳: (exit_price, exit_reason) 或 None

        出場優先序:
          1. 固定停損 (硬停)
          2. 階梯式減碼 (獲利達門檻，平倉部分)
          3. 固定停利
          4. K 線追蹤停損 (強勢股用前日低點追蹤)
          5. MA20 / ATR 移動停損
          6. 最大持有天數
        """
        close = float(current_bar['Close'])
        high = float(current_bar['High'])
        low = float(current_bar['Low'])
        entry_price = pos['entry_price']

        current_return = (close - entry_price) / entry_price * 100

        # 0. 動態 ATR 更新 (每日重算，而非只用進場日數值)
        if self.config.dynamic_atr_update and df_up_to is not None and 'atr' in pos:
            new_atr = calculate_atr(df_up_to)
            if new_atr > 0:
                pos['atr'] = new_atr

        # 1. 固定停損
        if self.config.stop_loss_pct > -99 and current_return <= self.config.stop_loss_pct:
            return (close, 'stop_loss')

        # 2. 階梯式減碼
        if (self.config.partial_profit_threshold > 0
                and current_return >= self.config.partial_profit_threshold
                and not pos.get('partial_done', False)):
            pos['partial_done'] = True
            return (close, 'partial_profit')

        # 3. 固定停利
        if self.config.take_profit_pct > 0 and current_return >= self.config.take_profit_pct:
            return (close, 'take_profit')

        # 4 & 5. 追蹤停損 — 浮盈超過啟動門檻後才啟動
        if current_return >= self.config.atr_activate_pct:
            # 4a. K 線追蹤停損 (優先檢查)。兩段式: kline_activate_pct 可設得比
            # atr_activate_pct 高，讓浮盈初期只用較寬的 ATR 追蹤
            kline_act = (self.config.kline_activate_pct
                         if self.config.kline_activate_pct is not None
                         else self.config.atr_activate_pct)
            if (self.config.k_line_trail and current_return >= kline_act
                    and df_up_to is not None and len(df_up_to) >= 2):
                prev_bar = df_up_to.iloc[-2]
                prev_low = float(prev_bar['Low'])
                prev_close_val = float(prev_bar['Close'])
                kline_stop = min(prev_low, prev_close_val)
                # 只在 K 線止損比 ATR 止損更緊湊時使用
                current_atr_stop = pos.get('trailing_stop', 0)
                if kline_stop > current_atr_stop:
                    pos['trailing_stop'] = kline_stop
                    pos['stop_source'] = 'kline'

            if self.config.use_ma20_trail and ma20 is not None:
                # MA20 追蹤停損：收盤跌破月線出場
                if close < ma20:
                    return (close, 'ma20_stop')
            elif self.config.atr_stop_mult > 0 and 'atr' in pos:
                # ATR 追蹤停損
                highest = pos.get('highest', entry_price)
                if high > highest:
                    pos['highest'] = high
                    highest = high
                trailing_stop = highest - pos['atr'] * self.config.atr_stop_mult
                if trailing_stop > pos.get('trailing_stop', 0):
                    pos['trailing_stop'] = trailing_stop
                    pos['stop_source'] = 'atr'
                if low <= pos['trailing_stop']:
                    # 修正：跳空開低越過停損價時，實際只能成交在開盤價，
                    # 不能假設成交在停損價 (舊版高估出場價)
                    open_px = float(current_bar['Open'])
                    fill_price = min(open_px, pos['trailing_stop']) \
                        if open_px < pos['trailing_stop'] else pos['trailing_stop']
                    exit_reason = ('kline_stop'
                                   if pos.get('stop_source') == 'kline' else 'atr_stop')
                    return (fill_price, exit_reason)
        else:
            # 未達啟動門檻仍追蹤最高價，等待啟動
            if high > pos.get('highest', entry_price):
                pos['highest'] = high

        # 6. 最大持有天數
        if self.config.max_hold_days > 0 and day_count >= self.config.max_hold_days:
            return (close, 'max_hold')

        return None

    def _open_position(self, ticker: str, signal: dict,
                       entry_price: float, entry_date: str,
                       df_up_to: pd.DataFrame):
        """建立持倉記錄"""
        atr = calculate_atr(df_up_to)
        self.active_positions[ticker] = {
            'entry_date': entry_date,
            'entry_price': entry_price,
            'hold_days': 0,
            'atr': atr,
            'highest': entry_price,
            'trailing_stop': entry_price - atr * self.config.atr_stop_mult,
            'score': signal.get('score', 0),
            'sector': signal.get('sector', ''),
            'grade': signal.get('grade', ''),
        }

    def _close_position(self, ticker: str, pos: dict,
                        exit_price: float, exit_date: str, exit_reason: str,
                        is_partial: bool = False, position_ratio: float = 1.0):
        """平倉並記錄交易 (支援分批平倉)"""
        gross_return = (exit_price - pos['entry_price']) / pos['entry_price']
        buy_cost = self._calculate_transaction_cost(is_buy=True)
        sell_cost = self._calculate_transaction_cost(is_buy=False)
        net_return = (gross_return - buy_cost - sell_cost) * 100

        self.trades.append(Trade(
            ticker=ticker,
            entry_date=pos['entry_date'],
            exit_date=exit_date,
            entry_price=pos['entry_price'],
            exit_price=exit_price,
            return_pct=round(net_return, 2),
            hold_days=pos['hold_days'],
            exit_reason=exit_reason,
            entry_score=pos.get('score', 0),
            sector=pos.get('sector', ''),
            grade=pos.get('grade', ''),
            is_partial=is_partial,
            position_ratio=position_ratio,
        ))

    def run(self, stock_data: dict, scan_results_by_date: dict = None) -> list[Trade]:
        """執行回測"""
        self.trades = []
        self.active_positions = {}
        self.daily_equity: list = []  # [(date_str, equity), ...] 供 metrics 計算日淨值指標
        pending_entries: dict = {}  # ticker -> signal dict，等待次日開盤進場

        # 投資組合淨值追蹤
        equity = 100.0          # 起始資產 (%)
        peak_equity = 100.0     # 歷史最高資產
        pos_size = self.config.position_size_pct / 100.0  # 每倉佔比

        if self.config.use_market_filter:
            self._load_market_data()

        if scan_results_by_date is None:
            scan_results_by_date = self._run_daily_scan(stock_data)

        # 修正：交易日曆 = 掃描日 ∪ 全體個股交易日。
        # 舊版只走訪 scan_results 的日期，「零訊號的日子」不會檢查出場，
        # 停損/停利會延遲觸發到下一個有訊號的日子。
        calendar = set(scan_results_by_date.keys())
        start_d, end_d = self.config.start_date, self.config.end_date
        for df in stock_data.values():
            if df is None or df.empty:
                continue
            for d in df.index:
                ds = str(d.date())
                if start_d <= ds <= end_d:
                    calendar.add(ds)
        all_dates = sorted(d for d in calendar if start_d <= d <= end_d)
        print(f"[Backtest] 回測期間: {all_dates[0]} ~ {all_dates[-1]} ({len(all_dates)} 個交易日)")
        print(f"[Backtest] 參數: score>={self.config.score_threshold} | "
              f"hold<={self.config.max_hold_days}d | SL={self.config.stop_loss_pct}% | "
              f"TP={self.config.take_profit_pct}% | ATR={self.config.atr_stop_mult}x "
              f"(啟動>{self.config.atr_activate_pct}%) | "
              f"breakout_only={self.config.require_breakout} | "
              f"next_open={self.config.entry_on_next_open}")

        for date_str in all_dates:
            ts = pd.Timestamp(date_str)

            # A. 大盤濾網 + 計算當日市場分數 (同時用於即時合成 total_score)
            market_score = 60.0  # 預設值 (use_market_filter=False 時使用)
            market_is_safe = True
            if self.config.use_market_filter:
                market_score = self._get_market_score(date_str)
                market_is_safe = market_score >= self.config.market_threshold

            # 動態倉位：依市場分數決定當天最大持倉數
            if self.config.dynamic_position:
                if market_score >= self.config.dp_bull_threshold:
                    day_max_slots = self.config.max_positions          # 牛市: 滿倉
                elif market_score >= self.config.dp_mid_threshold:
                    day_max_slots = self.config.dp_mid_slots           # 中等: 3倉
                else:
                    day_max_slots = self.config.dp_bear_slots          # 偏弱: 2倉
            else:
                day_max_slots = self.config.max_positions

            # B. 處理昨日訊號的次日開盤進場 (entry_on_next_open=True)
            if self.config.entry_on_next_open and pending_entries:
                for ticker, (signal, df_signal) in list(pending_entries.items()):
                    if len(self.active_positions) >= day_max_slots:
                        break
                    if ticker in self.active_positions:
                        continue
                    # 找今日的 bar
                    df = stock_data.get(ticker)
                    if df is None or df.empty:
                        continue
                    today_bars = df[df.index == ts]
                    if today_bars.empty:
                        # 今日無資料 (停牌)，跳過
                        continue
                    entry_bar = today_bars.iloc[0]
                    entry_price = float(entry_bar['Open'])
                    df_up_to = df[df.index <= ts]
                    self._open_position(ticker, signal, entry_price, date_str, df_up_to)
                pending_entries = {}

            # C. 檢查現有持倉出場 (每天都執行，不論大盤)
            closed_tickers = []
            for ticker, pos in self.active_positions.items():
                df = stock_data.get(ticker)
                if df is None or df.empty:
                    continue

                df_up_to = df[df.index <= ts]
                if df_up_to.empty:
                    continue

                # 修正：該股今日停牌/無交易時跳過，
                # 舊版會用「幾天前的舊 K 線」重複檢查出場並累加 hold_days
                if df_up_to.index[-1] != ts:
                    continue

                current_bar = df_up_to.iloc[-1]
                pos['hold_days'] += 1

                # 計算 MA20（月線）供 MA20 追蹤停損使用
                ma20 = None
                if self.config.use_ma20_trail and len(df_up_to) >= 20:
                    closes = df_up_to['Close'].squeeze()
                    ma20_val = float(closes.rolling(20).mean().iloc[-1])
                    ma20 = ma20_val if not np.isnan(ma20_val) else None

                exit_result = self._check_exit(ticker, pos, current_bar, pos['hold_days'],
                                               ma20=ma20, df_up_to=df_up_to)
                if exit_result:
                    exit_price, exit_reason = exit_result
                    if exit_reason == 'partial_profit':
                        # 分批減碼：只平倉部分，剩餘部位繼續追蹤
                        ratio = self.config.partial_profit_ratio
                        self._close_position(ticker, pos, exit_price, date_str, exit_reason,
                                             is_partial=True, position_ratio=ratio)
                        # 不從 active_positions 移除，繼續追蹤剩餘部位
                    else:
                        # 完整出場
                        remaining = 1.0 - (self.config.partial_profit_ratio if pos.get('partial_done') else 0)
                        self._close_position(ticker, pos, exit_price, date_str, exit_reason,
                                             position_ratio=remaining)
                        closed_tickers.append(ticker)

            for t in closed_tickers:
                del self.active_positions[t]

            # E. 計算當日投資組合淨值 (已結算 + 未結算浮動盈虧)
            unrealized = 0.0
            for ticker, pos in self.active_positions.items():
                df = stock_data.get(ticker)
                if df is None:
                    continue
                df_up = df[df.index <= ts]
                if df_up.empty:
                    continue
                cur_price = float(df_up.iloc[-1]['Close'])
                # 修正：分批減碼後只剩部分倉位，浮動盈虧需按剩餘比例計算
                # (舊版按全倉計算，與已實現的 partial 交易重複計入)
                remaining_frac = (1.0 - self.config.partial_profit_ratio
                                  if pos.get('partial_done') else 1.0)
                unrealized += ((cur_price / pos['entry_price'] - 1.0)
                               * pos_size * remaining_frac * 100.0)
            # 已實現報酬 — 修正：乘上 position_ratio，分批平倉不重複計算
            realized = sum(t.return_pct * pos_size * t.position_ratio
                           for t in self.trades)
            equity = 100.0 + realized + unrealized
            peak_equity = max(peak_equity, equity)
            portfolio_drawdown = (equity - peak_equity) / peak_equity * 100.0
            self.daily_equity.append((date_str, equity))

            # D. 尋找新進場機會 (大盤不安全則不進新單)
            if not market_is_safe:
                continue

            # 年線濾網：指數必須站上 MA200 才允許進場
            if self.config.require_index_above_ma200 and not self._is_index_above_ma200(date_str):
                continue

            # 投資組合級停損：整體回撤超過門檻，暫停新進場
            if self.config.portfolio_stop and portfolio_drawdown <= -self.config.portfolio_stop_pct:
                continue

            if len(self.active_positions) + len(pending_entries) >= day_max_slots:
                continue

            day_results = scan_results_by_date.get(date_str, [])
            for signal in day_results:
                slots_used = len(self.active_positions) + len(pending_entries)
                if slots_used >= day_max_slots:
                    break

                ticker = signal.get('ticker', '')
                if ticker in self.active_positions or ticker in pending_entries:
                    continue

                score = signal.get('score', 0)
                if score < self.config.score_threshold:
                    continue

                # 突破濾網 (require_breakout=True 時只進突破確認的訊號)
                if self.config.require_breakout and not signal.get('is_breakout', False):
                    continue

                # 族群評級濾網 (min_grade="B" 表示只進 A 或 B)
                if self.config.min_grade:
                    grade_order = {'A': 0, 'B': 1, 'C': 2}
                    sig_grade = signal.get('grade', 'C')
                    if grade_order.get(sig_grade, 2) > grade_order.get(self.config.min_grade, 2):
                        continue

                df = stock_data.get(ticker)
                if df is None or df.empty:
                    continue

                df_up_to = df[df.index <= ts]
                if df_up_to.empty:
                    continue

                # 流動性檢查 — 用「訊號當下」的 5 日均量，
                # 而非回測前用最新資料一次性過濾 (那是前視偏差)
                if self.config.min_vol_lots > 0:
                    vol5_lots = float(df_up_to['Volume'].tail(5).mean()) / 1000.0
                    if vol5_lots < self.config.min_vol_lots:
                        continue

                if self.config.entry_on_next_open:
                    # 加入 pending，等次日開盤進場
                    pending_entries[ticker] = (signal, df_up_to)
                else:
                    # 以當日收盤進場
                    entry_bar = df_up_to.iloc[-1]
                    entry_price = float(entry_bar['Close'])
                    self._open_position(ticker, signal, entry_price, date_str, df_up_to)

        # 強制平倉所有剩餘持倉
        # 修正：必須以「回測結束日」當時的價格平倉。舊版用 df.iloc[-1]
        # (整份資料最後一根)，當 end_date 早於資料末端時會吃到未來報酬 (前視)。
        end_ts = pd.Timestamp(self.config.end_date)
        for ticker, pos in self.active_positions.items():
            df = stock_data.get(ticker)
            if df is None or df.empty:
                continue
            df_up = df[df.index <= end_ts]
            if df_up.empty:
                continue
            last_bar = df_up.iloc[-1]
            exit_price = float(last_bar['Close'])
            exit_date = str(df_up.index[-1].date())
            remaining = 1.0 - (self.config.partial_profit_ratio
                               if pos.get('partial_done') else 0)
            self._close_position(ticker, pos, exit_price, exit_date,
                                 'end_of_backtest', position_ratio=remaining)
        self.active_positions = {}

        print(f"[Backtest] 完成！共 {len(self.trades)} 筆交易")
        return self.trades

    def _run_daily_scan(self, stock_data: dict) -> dict:
        """
        簡化版逐日掃描 (用於沒有預計算結果時)
        注意: 這是簡化版，實際使用建議預計算
        """
        recognizer = PatternRecognizer(
            order=self.config.order,
            min_points=self.config.min_points,
            min_bars=self.config.min_bars,
            convergence_threshold=self.config.convergence_threshold,
            long_only=self.config.long_only,
            trend_filter=self.config.trend_filter,
            volume_dryup=self.config.volume_dryup,
        )

        all_dates = set()
        for df in stock_data.values():
            if not df.empty:
                for d in df.index:
                    date_str = str(d.date())
                    if self.config.start_date <= date_str <= self.config.end_date:
                        all_dates.add(date_str)

        all_dates = sorted(all_dates)
        results_by_date = {}

        print(f"[Backtest] 逐日掃描中 ({len(all_dates)} 日 × {len(stock_data)} 檔)...")

        scan_interval = 5
        for i, date_str in enumerate(all_dates):
            if i % scan_interval != 0:
                continue

            day_signals = []
            for ticker, df in stock_data.items():
                if df.empty:
                    continue

                df_up_to = df[df.index <= pd.Timestamp(date_str)]
                if len(df_up_to) < self.config.min_bars + self.config.order * 2:
                    continue

                result = recognizer.analyze(ticker, df_up_to)
                if result.is_converging:
                    day_signals.append({
                        'ticker': ticker,
                        'score': result.pattern_score + result.breakout_score,
                        'is_breakout': result.is_breakout,
                        'sector': '',
                        'grade': 'C',
                    })

            if day_signals:
                day_signals.sort(key=lambda x: x['score'], reverse=True)
                results_by_date[date_str] = day_signals

            if (i + 1) % 50 == 0:
                print(f"  掃描進度: {i+1}/{len(all_dates)}")

        return results_by_date

    def get_metrics(self) -> BacktestMetrics:
        """取得回測績效指標 (以倉位佔比加權 + 日淨值曲線計算)"""
        trade_dicts = [
            {
                'entry_date': t.entry_date,
                'exit_date': t.exit_date,
                'entry_price': t.entry_price,
                'exit_price': t.exit_price,
                'return_pct': t.return_pct,
                'hold_days': t.hold_days,
                'position_ratio': t.position_ratio,
            }
            for t in self.trades
        ]
        return calculate_metrics(
            trade_dicts,
            position_size=self.config.position_size_pct / 100.0,
            daily_equity=getattr(self, 'daily_equity', None),
        )


if __name__ == "__main__":
    import data.database as db_module

    print("=== 回測引擎測試 ===")
    db_module.init_db()

    config = BacktestConfig.from_yaml("config.yaml")
    config.start_date = "2025-06-01"

    engine = BacktestEngine(config)

    sample = ["2330.TW", "2317.TW", "2454.TW", "2308.TW", "2382.TW"]
    stock_data = {}
    for t in sample:
        df = db_module.load_dataframe(t)
        if not df.empty:
            stock_data[t] = df

    if stock_data:
        trades = engine.run(stock_data)
        metrics = engine.get_metrics()
        from backtest.metrics import metrics_to_dict
        for k, v in metrics_to_dict(metrics).items():
            print(f"  {k}: {v}")
    else:
        print("  快取中無資料，請先執行 main.py 下載")
