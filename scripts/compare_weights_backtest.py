"""
scripts/compare_weights_backtest.py
舊 vs 新權重回測比較
從 SQLite 子分數重新合成 total_score，用兩組權重各跑一次回測。

用法:
  python scripts/compare_weights_backtest.py
  python scripts/compare_weights_backtest.py --start 2023-01-01 --end 2026-05-01
"""
import sys
import os
import argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
import pandas as pd
import data.database as db_module
from data.cleaner import DataCleaner
from backtest.engine import BacktestEngine, BacktestConfig
from backtest.report import generate_report

parser = argparse.ArgumentParser()
parser.add_argument("--start", default="2022-01-01")
parser.add_argument("--end",   default="2026-05-07")
parser.add_argument("--config", default="config.yaml")
parser.add_argument("--max-tickers", type=int, default=1700)
args = parser.parse_args()

# ── 舊 / 新 權重 ──────────────────────────────────────────────────────────────
OLD_W = {'market': 0.10, 'sector': 0.15, 'density': 0.20,
         'pattern': 0.15, 'breakout': 0.10, 'chain': 0.15, 'chip': 0.15}
NEW_W = {'market': 0.10, 'sector': 0.20, 'density': 0.20,
         'pattern': 0.10, 'breakout': 0.10, 'chain': 0.05, 'chip': 0.25}


def synth_score(row: dict, weights: dict) -> float:
    s = (row['market_score']   * weights['market']   +
         row['sector_score']   * weights['sector']   +
         row['density_score']  * weights['density']  +
         row['pattern_score']  * weights['pattern']  +
         row['breakout_score'] * weights['breakout'] +
         row['chain_score']    * weights['chain']    +
         row['chip_score']     * weights['chip'])
    grade_bonus = {'A': 5, 'B': 2, 'C': 0}.get(row.get('grade', 'C'), 0)
    return min(100.0, round(s + grade_bonus, 2))


def load_signals_with_all_subscores(db_path: str, start: str, end: str) -> pd.DataFrame:
    with sqlite3.connect(db_path) as conn:
        df = pd.read_sql("""
            SELECT scan_date, ticker, grade, status,
                   market_score, sector_score, density_score,
                   chain_score, chip_score, pattern_score, breakout_score
            FROM scan_results
            WHERE scan_date BETWEEN ? AND ?
            ORDER BY scan_date ASC
        """, conn, params=(start, end))
    return df


def build_scan_dict(df: pd.DataFrame, weights: dict) -> dict:
    results: dict = {}
    for _, row in df.iterrows():
        d = row.to_dict()
        score = synth_score(d, weights)
        signal = {
            'ticker':         d['ticker'],
            'score':          score,
            'grade':          d.get('grade', 'C'),
            'is_breakout':    d.get('status') == 'Breakout',
            'density_score':  d.get('density_score', 0),
            'chain_score':    d.get('chain_score', 0),
            'chip_score':     d.get('chip_score', 0),
            'pattern_score':  d.get('pattern_score', 0),
            'breakout_score': d.get('breakout_score', 0),
        }
        results.setdefault(d['scan_date'], []).append(signal)
    # 每天按 score 降序排
    for date in results:
        results[date].sort(key=lambda x: x['score'], reverse=True)
    return results


def run_one(label: str, stock_data: dict, scan_dict: dict, config: BacktestConfig):
    print(f"\n{'='*55}")
    print(f"  回測: {label}")
    print(f"{'='*55}")
    engine = BacktestEngine(config)
    trades = engine.run(stock_data, scan_results_by_date=scan_dict)
    if not trades:
        print("  [!] 無交易產生")
        return None
    metrics = engine.get_metrics()
    generate_report(metrics, trades, config, output_dir="output")
    return metrics


# ── 主流程 ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    db_module.init_db()

    # 1. 股票 OHLCV
    print(f"[1] 載入股票清單 (SQLite)...")
    with sqlite3.connect(db_module.DB_PATH) as conn:
        rows = conn.execute("""
            SELECT ticker, COUNT(*) as bars FROM daily_prices
            GROUP BY ticker HAVING bars >= 200 ORDER BY bars DESC
        """).fetchall()
    print(f"    符合條件 (>=200 bars): {len(rows)} 檔")

    cleaner = DataCleaner()
    stock_data = {}
    print(f"[2] 載入前 {args.max_tickers} 檔 OHLCV...")
    for ticker, _ in rows[:args.max_tickers]:
        df = db_module.load_dataframe(ticker)
        if not df.empty and cleaner.is_valid(ticker, df):
            stock_data[ticker] = df
    print(f"    成功載入: {len(stock_data)} 檔")

    # 2. 載入子分數
    print(f"\n[3] 載入子分數 ({args.start} ~ {args.end})...")
    raw_df = load_signals_with_all_subscores(db_module.DB_PATH, args.start, args.end)
    print(f"    共 {len(raw_df)} 筆掃描記錄，橫跨 {raw_df['scan_date'].nunique()} 個交易日")

    # 顯示 chip_score 覆蓋率
    chip_ok = (raw_df['chip_score'] > 0).sum()
    print(f"    chip_score 有效覆蓋率: {chip_ok/len(raw_df)*100:.0f}%")

    # 3. 建兩組 scan_dict
    old_scan = build_scan_dict(raw_df, OLD_W)
    new_scan = build_scan_dict(raw_df, NEW_W)

    # 4. 回測設定
    config = BacktestConfig.from_yaml(args.config)
    config.start_date = args.start
    config.end_date   = args.end
    config.entry_on_next_open = True

    # 5. 分別跑回測
    m_old = run_one("舊權重 (chip15%/chain15%)", stock_data, old_scan, config)
    m_new = run_one("新權重 (chip25%/chain5%)",  stock_data, new_scan, config)

    # 6. 並排比較
    if m_old and m_new:
        print("\n" + "="*60)
        print("  ■ 結果比較")
        print("="*60)
        keys = [
            ('total_return_pct',      '總報酬率 (%)'),
            ('annualized_return_pct', '年化報酬率 (%)'),
            ('max_drawdown_pct',      '最大回撤 (%)'),
            ('win_rate',              '勝率 (%)'),
            ('profit_loss_ratio',     '盈虧比'),
            ('profit_factor',         '獲利因子'),
            ('sharpe_ratio',          'Sharpe Ratio'),
            ('calmar_ratio',          'Calmar Ratio'),
            ('expectancy',            '期望值 (%)'),
            ('total_trades',          '總交易次數'),
            ('avg_hold_days',         '平均持倉天數'),
        ]
        print(f"  {'指標':22s}  {'舊權重':>12s}  {'新權重':>12s}  {'差異':>10s}")
        print(f"  {'-'*60}")
        for k, lbl in keys:
            v_old = getattr(m_old, k, 0) or 0
            v_new = getattr(m_new, k, 0) or 0
            diff  = v_new - v_old
            sign  = '+' if diff >= 0 else ''
            if isinstance(v_old, float):
                print(f"  {lbl:22s}  {v_old:>12.2f}  {v_new:>12.2f}  {sign}{diff:>9.2f}")
            else:
                print(f"  {lbl:22s}  {v_old:>12}  {v_new:>12}  {sign}{diff:>9}")
        print("="*60)
        # 逐年報酬比較
        print("\n  ■ 逐年報酬比較 (%)")
        print(f"  {'年份':6s}  {'舊權重':>10s}  {'新權重':>10s}  {'差異':>8s}")
        print(f"  {'-'*40}")
        all_years = sorted(set(m_old.yearly_returns) | set(m_new.yearly_returns))
        for yr in all_years:
            vo = m_old.yearly_returns.get(yr, 0.0)
            vn = m_new.yearly_returns.get(yr, 0.0)
            d  = vn - vo
            sign = '+' if d >= 0 else ''
            print(f"  {yr:6s}  {vo:>10.2f}  {vn:>10.2f}  {sign}{d:>7.2f}")
        print("="*60)
