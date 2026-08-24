"""
績效指標計算模組 (Backtesting Metrics)
提供: 勝率、盈虧比、期望值、最大回撤、年化報酬、Sharpe Ratio、Calmar Ratio、逐年報酬 等

優化記錄 (2026-03-28):
  - 年化報酬改用「首筆進場至末筆出場的日曆天數」計算，避免平行持倉重複計算
  - 新增 calmar_ratio (年化報酬 / 最大回撤)
  - 新增 yearly_returns (逐年報酬分解)
  - metrics_to_dict 加入新指標
"""
import numpy as np
from datetime import datetime
from dataclasses import dataclass, field


@dataclass
class BacktestMetrics:
    """回測績效指標"""
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0               # 勝率 (%)
    avg_profit_pct: float = 0.0         # 平均獲利 (%)
    avg_loss_pct: float = 0.0           # 平均虧損 (%)
    profit_loss_ratio: float = 0.0      # 盈虧比
    expectancy: float = 0.0             # 期望值 (每筆交易平均損益%)
    max_drawdown_pct: float = 0.0       # 最大回撤 (%)
    total_return_pct: float = 0.0       # 總報酬率 (%)
    annualized_return_pct: float = 0.0  # 年化報酬率 (%)
    sharpe_ratio: float = 0.0           # 夏普比率
    calmar_ratio: float = 0.0           # Calmar 比率 = 年化報酬 / 最大回撤
    profit_factor: float = 0.0          # 收益因子
    avg_hold_days: float = 0.0          # 平均持有天數
    max_consecutive_loss: int = 0       # 最大連續虧損次數
    equity_curve: list = field(default_factory=list)   # 權益曲線
    yearly_returns: dict = field(default_factory=dict) # 逐年報酬 {year: pct}


def calculate_metrics(trades: list, initial_capital: float = 1_000_000,
                      risk_free_rate: float = 0.02,
                      position_size: float = 1.0,
                      daily_equity: list = None) -> BacktestMetrics:
    """
    從交易記錄計算所有績效指標

    trades: list of dict，每筆交易需包含:
      - entry_date, exit_date
      - entry_price, exit_price
      - return_pct (含手續費扣除後的淨報酬率 %)
      - hold_days
      - position_ratio (選填，分批平倉時該筆佔原始倉位的比例，預設 1.0)

    position_size: 每筆倉位佔總資金的比例 (例如 0.2 = 20%)。
      修正: 舊版將每筆 return_pct 全額複利，等於假設每筆 all-in，
      總報酬 / MDD 被放大約 1/position_size 倍。

    daily_equity: 選填，[(date_str, equity_pct), ...] 逐日投資組合淨值 (100 = 起始)。
      提供時，總報酬 / 年化 / MDD / Sharpe / 逐年報酬改用日淨值計算 (最準確)；
      未提供時退回「逐筆貢獻複利」近似。
    """
    metrics = BacktestMetrics()

    if not trades:
        return metrics

    returns = [t['return_pct'] for t in trades]
    hold_days_list = [t.get('hold_days', 1) for t in trades]

    metrics.total_trades = len(trades)

    # 勝率
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r <= 0]
    metrics.winning_trades = len(wins)
    metrics.losing_trades = len(losses)
    metrics.win_rate = len(wins) / len(returns) * 100

    # 平均獲利/虧損
    metrics.avg_profit_pct = float(np.mean(wins)) if wins else 0.0
    metrics.avg_loss_pct = float(np.mean(losses)) if losses else 0.0

    # 盈虧比
    if metrics.avg_loss_pct != 0:
        metrics.profit_loss_ratio = abs(metrics.avg_profit_pct / metrics.avg_loss_pct)

    # 期望值 = 勝率 × 平均獲利 + 敗率 × 平均虧損
    win_pct = metrics.win_rate / 100
    metrics.expectancy = (
        win_pct * metrics.avg_profit_pct +
        (1 - win_pct) * metrics.avg_loss_pct
    )

    # Profit Factor = 總獲利 / |總虧損|
    total_profit = sum(wins) if wins else 0
    total_loss = abs(sum(losses)) if losses else 1
    metrics.profit_factor = total_profit / total_loss if total_loss > 0 else 0.0

    # 權益曲線 — 投資組合層級 (每筆貢獻 = return_pct × 倉位佔比 × 分批比例)
    sorted_trades = sorted(trades, key=lambda t: t.get('exit_date', ''))
    if daily_equity:
        # 使用引擎提供的逐日淨值 (100 = 起始)
        equity_curve = [initial_capital * eq / 100.0 for _, eq in daily_equity]
    else:
        equity = initial_capital
        equity_curve = [equity]
        for t in sorted_trades:
            contrib = (t['return_pct'] / 100) * position_size * t.get('position_ratio', 1.0)
            equity *= (1 + contrib)
            equity_curve.append(equity)
    metrics.equity_curve = equity_curve

    # 總報酬率
    metrics.total_return_pct = (equity_curve[-1] / initial_capital - 1) * 100

    # 年化報酬率 — 使用日曆天數 (首筆進場 → 末筆出場)，避免平行持倉重複計算
    try:
        if daily_equity:
            first_entry = daily_equity[0][0]
            last_exit = daily_equity[-1][0]
        else:
            first_entry = min(t.get('entry_date', '2020-01-01') for t in trades)
            last_exit = max(t.get('exit_date', '2020-01-01') for t in trades)
        span_days = (datetime.strptime(last_exit, '%Y-%m-%d') -
                     datetime.strptime(first_entry, '%Y-%m-%d')).days
        years = max(span_days / 365.25, 0.01)
        metrics.annualized_return_pct = (
            ((equity_curve[-1] / initial_capital) ** (1 / years) - 1) * 100
        )
    except Exception:
        pass

    # 最大回撤 (MDD) — 有日淨值時反映逐日浮動，比只看交易序列準確
    peak = equity_curve[0]
    max_dd = 0.0
    for val in equity_curve:
        if val > peak:
            peak = val
        dd = (peak - val) / peak * 100
        if dd > max_dd:
            max_dd = dd
    metrics.max_drawdown_pct = max_dd

    # Calmar Ratio = 年化報酬 / 最大回撤
    if metrics.max_drawdown_pct > 0:
        metrics.calmar_ratio = metrics.annualized_return_pct / metrics.max_drawdown_pct

    # Sharpe Ratio (年化)
    if daily_equity and len(equity_curve) > 2:
        # 以逐日淨值報酬率計算 (標準做法)
        eq_arr = np.array(equity_curve, dtype=float)
        daily_rets = eq_arr[1:] / eq_arr[:-1] - 1.0
        daily_rf = risk_free_rate / 252
        excess = daily_rets - daily_rf
        std = float(np.std(excess, ddof=1))
        if std > 0:
            metrics.sharpe_ratio = float(np.mean(excess)) / std * np.sqrt(252)
    elif len(returns) > 1:
        # 退回逐筆近似 (持倉重疊時會失真，僅供參考)
        daily_rf = risk_free_rate / 252
        pf_returns = [r / 100 * position_size * t.get('position_ratio', 1.0)
                      for r, t in zip(returns, trades)]
        excess_returns = [r - daily_rf for r in pf_returns]
        mean_excess = float(np.mean(excess_returns))
        std_excess = float(np.std(excess_returns, ddof=1))
        if std_excess > 0:
            avg_hold = float(np.mean(hold_days_list)) if hold_days_list else 5.0
            trades_per_year = 252 / max(avg_hold, 1)
            metrics.sharpe_ratio = mean_excess / std_excess * np.sqrt(trades_per_year)

    # 平均持有天數
    metrics.avg_hold_days = float(np.mean(hold_days_list))

    # 最大連續虧損
    max_consec = 0
    cur_consec = 0
    for r in returns:
        if r <= 0:
            cur_consec += 1
            max_consec = max(max_consec, cur_consec)
        else:
            cur_consec = 0
    metrics.max_consecutive_loss = max_consec

    # 逐年報酬分解 (投資組合層級)
    yearly_returns = {}
    if daily_equity:
        # 用日淨值計算各年度報酬 (年初基準 = 上一年度最後一天的淨值)
        year_last: dict = {}
        for (d, eq) in daily_equity:
            year_last[d[:4]] = eq
        prev = None
        for year in sorted(year_last.keys()):
            base = prev if prev is not None else daily_equity[0][1]
            yearly_returns[year] = round((year_last[year] / base - 1) * 100, 1)
            prev = year_last[year]
    else:
        yearly_buckets: dict = {}
        for t in sorted_trades:
            year = t.get('exit_date', '????')[:4]
            if year not in yearly_buckets:
                yearly_buckets[year] = []
            contrib = (t['return_pct'] / 100) * position_size * t.get('position_ratio', 1.0)
            yearly_buckets[year].append(contrib)
        for year, rets in sorted(yearly_buckets.items()):
            eq = 1.0
            for r in rets:
                eq *= (1 + r)
            yearly_returns[year] = round((eq - 1) * 100, 1)
    metrics.yearly_returns = yearly_returns

    return metrics


def metrics_to_dict(m: BacktestMetrics) -> dict:
    """將 BacktestMetrics 轉為 dict (排除 equity_curve / yearly_returns 以節省空間)"""
    return {
        '總交易次數': m.total_trades,
        '勝率 (%)': round(m.win_rate, 1),
        '平均獲利 (%)': round(m.avg_profit_pct, 2),
        '平均虧損 (%)': round(m.avg_loss_pct, 2),
        '盈虧比': round(m.profit_loss_ratio, 2),
        '期望值 (%)': round(m.expectancy, 2),
        '收益因子': round(m.profit_factor, 2),
        '總報酬 (%)': round(m.total_return_pct, 2),
        '年化報酬 (%)': round(m.annualized_return_pct, 2),
        '最大回撤 (%)': round(m.max_drawdown_pct, 2),
        'Sharpe Ratio': round(m.sharpe_ratio, 2),
        'Calmar Ratio': round(m.calmar_ratio, 2),
        '平均持有天數': round(m.avg_hold_days, 1),
        '最大連續虧損': m.max_consecutive_loss,
    }
