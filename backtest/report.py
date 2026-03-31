"""
回測報表生成器 (Backtest Report Generator)
輸出 Markdown 格式的回測報告，含績效統計與圖表

優化記錄 (2026-03-28):
  - 新增逐年報酬分解表
  - 新增 Calmar Ratio 顯示
  - 權益曲線圖新增回撤子圖 (drawdown subplot)
  - 新增 benchmark 比較段落 (可選)
"""
import os
from datetime import datetime
from backtest.metrics import BacktestMetrics, metrics_to_dict

try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker
    HAS_MPL = True
except ImportError:
    HAS_MPL = False


def generate_report(metrics: BacktestMetrics, trades: list,
                    config=None, output_dir: str = "output",
                    benchmark_return: float = None) -> str:
    """
    生成 Markdown 格式的回測報告

    Args:
        metrics: BacktestMetrics 績效指標
        trades: 交易記錄列表
        config: BacktestConfig (可選，用於顯示參數)
        output_dir: 輸出目錄
        benchmark_return: benchmark 總報酬率 % (可選，用於比較)

    回傳: 報告檔案路徑
    """
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join(output_dir, f"backtest_report_{timestamp}.md")

    # 繪製圖表
    equity_chart_path = None
    if HAS_MPL and metrics.equity_curve:
        equity_chart_path = os.path.join(output_dir, f"equity_curve_{timestamp}.png")
        _plot_equity_and_drawdown(metrics.equity_curve, equity_chart_path)

    lines = []
    lines.append("# 回測績效報告")
    lines.append(f"> 生成時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")

    # 回測參數
    if config:
        lines.append("## 回測參數")
        lines.append("| 參數 | 值 |")
        lines.append("|------|------|")
        lines.append(f"| 回測期間 | {config.start_date} ~ {config.end_date} |")
        lines.append(f"| 最大持有天數 | {config.max_hold_days} |")
        lines.append(f"| 進場門檻分數 | {config.score_threshold} |")
        lines.append(f"| 停損 | {config.stop_loss_pct}% |")
        lines.append(f"| 停利 | {config.take_profit_pct}% |")
        lines.append(f"| ATR 停損倍數 | {config.atr_stop_mult}x |")
        lines.append(f"| ATR 啟動門檻 | 浮盈 ≥ {config.atr_activate_pct}% |")
        lines.append(f"| 只進突破訊號 | {config.require_breakout} |")
        lines.append(f"| 次日開盤進場 | {config.entry_on_next_open} |")
        lines.append(f"| 同時持倉上限 | {config.max_positions} |")
        lines.append(f"| 大盤濾網門檻 | {config.market_threshold} |")
        lines.append("")

    # 績效摘要
    lines.append("## 績效摘要")
    lines.append("| 指標 | 數值 |")
    lines.append("|------|------|")
    for name, value in metrics_to_dict(metrics).items():
        lines.append(f"| {name} | {value} |")
    lines.append("")

    # benchmark 比較 (可選)
    if benchmark_return is not None:
        alpha = metrics.total_return_pct - benchmark_return
        alpha_str = f"+{alpha:.1f}%" if alpha >= 0 else f"{alpha:.1f}%"
        lines.append("## Benchmark 比較 (^TWII 買進持有)")
        lines.append("| 指標 | 策略 | Benchmark | Alpha |")
        lines.append("|------|------|-----------|-------|")
        lines.append(f"| 總報酬 | {metrics.total_return_pct:.1f}% "
                     f"| {benchmark_return:.1f}% | {alpha_str} |")
        lines.append("")

    # 逐年報酬分解
    if metrics.yearly_returns:
        lines.append("## 逐年報酬")
        lines.append("| 年度 | 報酬率 | 判定 |")
        lines.append("|------|--------|------|")
        for year, ret in sorted(metrics.yearly_returns.items()):
            icon = "✅" if ret > 0 else "❌"
            lines.append(f"| {year} | {ret:+.1f}% | {icon} |")
        lines.append("")

    # 評估判定
    lines.append("## 評估判定")
    verdict = _evaluate(metrics)
    for v in verdict:
        lines.append(f"- {v}")
    lines.append("")

    # 權益曲線 + 回撤圖
    if equity_chart_path:
        lines.append("## 權益曲線與回撤")
        lines.append(f"![Equity & Drawdown]({os.path.basename(equity_chart_path)})")
        lines.append("")

    # 出場原因統計
    if trades:
        lines.append("## 出場原因統計")
        exit_counts: dict = {}
        for t in trades:
            reason = t.exit_reason if hasattr(t, 'exit_reason') else t.get('exit_reason', 'unknown')
            exit_counts[reason] = exit_counts.get(reason, 0) + 1
        lines.append("| 出場原因 | 次數 | 佔比 |")
        lines.append("|------|------|------|")
        for reason, count in sorted(exit_counts.items(), key=lambda x: -x[1]):
            pct = count / len(trades) * 100
            lines.append(f"| {reason} | {count} | {pct:.1f}% |")
        lines.append("")

    # 前 20 筆交易明細
    if trades:
        lines.append("## 交易明細 (前 20 筆)")
        lines.append("| # | Ticker | 進場 | 出場 | 報酬% | 天數 | 原因 |")
        lines.append("|---|--------|------|------|-------|------|------|")
        for i, t in enumerate(trades[:20]):
            ticker = t.ticker if hasattr(t, 'ticker') else t.get('ticker', '')
            entry = t.entry_date if hasattr(t, 'entry_date') else t.get('entry_date', '')
            exit_d = t.exit_date if hasattr(t, 'exit_date') else t.get('exit_date', '')
            ret = t.return_pct if hasattr(t, 'return_pct') else t.get('return_pct', 0)
            days = t.hold_days if hasattr(t, 'hold_days') else t.get('hold_days', 0)
            reason = t.exit_reason if hasattr(t, 'exit_reason') else t.get('exit_reason', '')
            color = "🟢" if ret > 0 else "🔴"
            lines.append(f"| {i+1} | {ticker} | {entry} | {exit_d} "
                         f"| {color} {ret:+.2f} | {days} | {reason} |")
        lines.append("")

    content = "\n".join(lines)
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(content)

    print(f"[Report] 回測報告已輸出至: {report_path}")
    return report_path


def _plot_equity_and_drawdown(equity_curve: list, save_path: str):
    """繪製權益曲線 + 回撤子圖"""
    # 計算回撤序列
    peak = equity_curve[0]
    drawdown = []
    for val in equity_curve:
        if val > peak:
            peak = val
        drawdown.append((peak - val) / peak * 100)

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8),
                                    gridspec_kw={'height_ratios': [3, 1]},
                                    sharex=True)
    fig.subplots_adjust(hspace=0.05)

    # 上圖：權益曲線
    x = range(len(equity_curve))
    ax1.plot(x, equity_curve, linewidth=1.5, color='#2196F3', label='Portfolio')
    ax1.fill_between(x, equity_curve, equity_curve[0],
                     where=[v >= equity_curve[0] for v in equity_curve],
                     alpha=0.15, color='#2196F3')
    ax1.fill_between(x, equity_curve, equity_curve[0],
                     where=[v < equity_curve[0] for v in equity_curve],
                     alpha=0.2, color='#F44336')
    ax1.axhline(y=equity_curve[0], color='gray', linestyle='--', linewidth=0.8)
    ax1.set_title('Equity Curve & Drawdown', fontsize=14)
    ax1.set_ylabel('Portfolio Value (NTD)')
    ax1.yaxis.set_major_formatter(mticker.FuncFormatter(
        lambda v, _: f'{v/1e6:.2f}M' if v >= 1e6 else f'{v:,.0f}'
    ))
    ax1.grid(True, alpha=0.3)
    ax1.legend(loc='upper left', fontsize=9)

    # 最大回撤標記
    max_dd = max(drawdown)
    max_dd_idx = drawdown.index(max_dd)
    ax1.annotate(f'Max DD: -{max_dd:.1f}%',
                 xy=(max_dd_idx, equity_curve[max_dd_idx]),
                 xytext=(max_dd_idx, equity_curve[max_dd_idx] * 1.05),
                 fontsize=8, color='#F44336',
                 arrowprops=dict(arrowstyle='->', color='#F44336', lw=1.0))

    # 下圖：回撤
    ax2.fill_between(x, 0, [-d for d in drawdown], alpha=0.6, color='#F44336')
    ax2.set_ylabel('Drawdown (%)')
    ax2.set_xlabel('Trade #')
    ax2.set_ylim(top=0)
    ax2.grid(True, alpha=0.3)
    ax2.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f'{v:.0f}%'))

    fig.tight_layout()
    fig.savefig(save_path, dpi=100, bbox_inches='tight')
    plt.close(fig)
    print(f"  [Report] 圖表已存至: {save_path}")


def _evaluate(m: BacktestMetrics) -> list:
    """根據績效指標給出判定"""
    verdicts = []
    if m.total_trades < 30:
        verdicts.append("⚠️ 交易次數不足 30 筆，統計顯著性不夠")
    if m.win_rate >= 50:
        verdicts.append(f"✅ 勝率 {m.win_rate:.1f}% 良好 (> 50%)")
    else:
        verdicts.append(f"⚠️ 勝率 {m.win_rate:.1f}% 偏低，需搭配高盈虧比")
    if m.profit_loss_ratio >= 1.5:
        verdicts.append(f"✅ 盈虧比 {m.profit_loss_ratio:.2f} 優秀 (> 1.5)")
    elif m.profit_loss_ratio >= 1.0:
        verdicts.append(f"🔶 盈虧比 {m.profit_loss_ratio:.2f} 可接受")
    else:
        verdicts.append(f"❌ 盈虧比 {m.profit_loss_ratio:.2f} 不合格 (< 1.0)")
    if m.expectancy > 0:
        verdicts.append(f"✅ 期望值為正 ({m.expectancy:.2f}%)，策略具可行性")
    else:
        verdicts.append(f"❌ 期望值為負 ({m.expectancy:.2f}%)，策略不可行")
    if m.max_drawdown_pct <= 15:
        verdicts.append(f"✅ 最大回撤 {m.max_drawdown_pct:.1f}% 可控 (≤ 15%)")
    elif m.max_drawdown_pct <= 25:
        verdicts.append(f"⚠️ 最大回撤 {m.max_drawdown_pct:.1f}% 偏高 (> 15%)")
    else:
        verdicts.append(f"❌ 最大回撤 {m.max_drawdown_pct:.1f}% 過大 (> 25%)")
    if m.sharpe_ratio >= 1.0:
        verdicts.append(f"✅ Sharpe Ratio {m.sharpe_ratio:.2f} 優秀 (≥ 1.0)")
    elif m.sharpe_ratio >= 0.5:
        verdicts.append(f"🔶 Sharpe Ratio {m.sharpe_ratio:.2f} 可接受 (≥ 0.5)")
    else:
        verdicts.append(f"⚠️ Sharpe Ratio {m.sharpe_ratio:.2f} 偏低 (< 0.5)")
    if m.calmar_ratio >= 0.5:
        verdicts.append(f"✅ Calmar Ratio {m.calmar_ratio:.2f} 良好 (≥ 0.5)")
    elif m.calmar_ratio > 0:
        verdicts.append(f"🔶 Calmar Ratio {m.calmar_ratio:.2f} 偏低")
    else:
        verdicts.append(f"❌ Calmar Ratio {m.calmar_ratio:.2f} 為負，策略不可行")
    return verdicts
