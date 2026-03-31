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
                      risk_free_rate: float = 0.02) -> BacktestMetrics:
    """
    從交易記錄計算所有績效指標

    trades: list of dict，每筆交易需包含:
      - entry_date, exit_date
      - entry_price, exit_price
      - return_pct (含手續費扣除後的淨報酬率 %)
      - hold_days
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

    # 權益曲線 (按出場日期時序累計)
    sorted_trades = sorted(trades, key=lambda t: t.get('exit_date', ''))
    equity = initial_capital
    equity_curve = [equity]
    for t in sorted_trades:
        equity *= (1 + t['return_pct'] / 100)
        equity_curve.append(equity)
    metrics.equity_curve = equity_curve

    # 總報酬率
    metrics.total_return_pct = (equity_curve[-1] / initial_capital - 1) * 100

    # 年化報酬率 — 使用日曆天數 (首筆進場 → 末筆出場)，避免平行持倉重複計算
    try:
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

    # 最大回撤 (MDD)
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
    if len(returns) > 1:
        daily_rf = risk_free_rate / 252
        excess_returns = [r / 100 - daily_rf for r in returns]
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

    # 逐年報酬分解
    yearly_buckets: dict = {}
    for t in sorted_trades:
        year = t.get('exit_date', '????')[:4]
        if year not in yearly_buckets:
            yearly_buckets[year] = []
        yearly_buckets[year].append(t['return_pct'])

    yearly_returns = {}
    for year, rets in sorted(yearly_buckets.items()):
        eq = 1.0
        for r in rets:
            eq *= (1 + r / 100)
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
