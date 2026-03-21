"""
回測報表生成器 (Backtest Report Generator)
輸出 Markdown 格式的回測報告，含績效統計與權益曲線圖
"""
import os
from datetime import datetime
from backtest.metrics import BacktestMetrics, metrics_to_dict

try:
    import matplotlib.pyplot as plt
    HAS_MPL = True
except ImportError:
    HAS_MPL = False


def generate_report(metrics: BacktestMetrics, trades: list,
                    config=None, output_dir: str = "output") -> str:
    """
    生成 Markdown 格式的回測報告
    
    回傳: 報告檔案路徑
    """
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join(output_dir, f"backtest_report_{timestamp}.md")

    # 繪製權益曲線
    equity_chart_path = None
    if HAS_MPL and metrics.equity_curve:
        equity_chart_path = os.path.join(output_dir, f"equity_curve_{timestamp}.png")
        _plot_equity_curve(metrics.equity_curve, equity_chart_path)

    # 生成報告內容
    lines = []
    lines.append(f"# 回測績效報告")
    lines.append(f"> 生成時間: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")

    # 參數
    if config:
        lines.append("## 回測參數")
        lines.append(f"| 參數 | 值 |")
        lines.append(f"|------|------|")
        lines.append(f"| 回測期間 | {config.start_date} ~ {config.end_date} |")
        lines.append(f"| 最大持有天數 | {config.max_hold_days} |")
        lines.append(f"| 進場門檻分數 | {config.score_threshold} |")
        lines.append(f"| 停損 | {config.stop_loss_pct}% |")
        lines.append(f"| 停利 | {config.take_profit_pct}% |")
        lines.append(f"| ATR 停損倍數 | {config.atr_stop_mult}x |")
        lines.append(f"| 同時持倉上限 | {config.max_positions} |")
        lines.append("")

    # 績效摘要
    lines.append("## 績效摘要")
    lines.append(f"| 指標 | 數值 |")
    lines.append(f"|------|------|")
    for name, value in metrics_to_dict(metrics).items():
        lines.append(f"| {name} | {value} |")
    lines.append("")

    # 判定
    lines.append("## 評估判定")
    verdict = _evaluate(metrics)
    for v in verdict:
        lines.append(f"- {v}")
    lines.append("")

    # 權益曲線
    if equity_chart_path:
        lines.append("## 權益曲線")
        lines.append(f"![Equity Curve]({os.path.basename(equity_chart_path)})")
        lines.append("")

    # 出場原因統計
    if trades:
        lines.append("## 出場原因統計")
        exit_counts = {}
        for t in trades:
            reason = t.exit_reason if hasattr(t, 'exit_reason') else t.get('exit_reason', 'unknown')
            exit_counts[reason] = exit_counts.get(reason, 0) + 1
        lines.append(f"| 出場原因 | 次數 | 佔比 |")
        lines.append(f"|------|------|------|")
        for reason, count in sorted(exit_counts.items(), key=lambda x: -x[1]):
            pct = count / len(trades) * 100
            lines.append(f"| {reason} | {count} | {pct:.1f}% |")
        lines.append("")

    # 前 20 筆交易明細
    if trades:
        lines.append("## 交易明細 (前 20 筆)")
        lines.append(f"| # | Ticker | 進場 | 出場 | 報酬% | 天數 | 原因 |")
        lines.append(f"|---|--------|------|------|-------|------|------|")
        for i, t in enumerate(trades[:20]):
            ticker = t.ticker if hasattr(t, 'ticker') else t.get('ticker', '')
            entry = t.entry_date if hasattr(t, 'entry_date') else t.get('entry_date', '')
            exit_d = t.exit_date if hasattr(t, 'exit_date') else t.get('exit_date', '')
            ret = t.return_pct if hasattr(t, 'return_pct') else t.get('return_pct', 0)
            days = t.hold_days if hasattr(t, 'hold_days') else t.get('hold_days', 0)
            reason = t.exit_reason if hasattr(t, 'exit_reason') else t.get('exit_reason', '')
            color = "🟢" if ret > 0 else "🔴"
            lines.append(f"| {i+1} | {ticker} | {entry} | {exit_d} | {color} {ret:+.2f} | {days} | {reason} |")
        lines.append("")

    content = "\n".join(lines)
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(content)

    print(f"[Report] 回測報告已輸出至: {report_path}")
    return report_path


def _plot_equity_curve(equity_curve: list, save_path: str):
    """繪製權益曲線圖"""
    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(equity_curve, linewidth=1.5, color='#2196F3')
    ax.fill_between(range(len(equity_curve)), equity_curve,
                     equity_curve[0], alpha=0.1, color='#2196F3')
    ax.axhline(y=equity_curve[0], color='gray', linestyle='--', linewidth=0.8)
    ax.set_title('Equity Curve', fontsize=14)
    ax.set_xlabel('Trade #')
    ax.set_ylabel('Portfolio Value')
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(save_path, dpi=100)
    plt.close(fig)
    print(f"  [Report] 權益曲線圖已存至: {save_path}")


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
        verdicts.append(f"✅ 期望值為正 ({m.expectancy:.2f}%)")
    else:
        verdicts.append(f"❌ 期望值為負 ({m.expectancy:.2f}%)，策略不可行")
    if m.max_drawdown_pct <= 15:
        verdicts.append(f"✅ 最大回撤 {m.max_drawdown_pct:.1f}% 可控 (≤ 15%)")
    elif m.max_drawdown_pct <= 25:
        verdicts.append(f"⚠️ 最大回撤 {m.max_drawdown_pct:.1f}% 偏高")
    else:
        verdicts.append(f"❌ 最大回撤 {m.max_drawdown_pct:.1f}% 過大 (> 25%)")
    if m.sharpe_ratio >= 1.0:
        verdicts.append(f"✅ Sharpe Ratio {m.sharpe_ratio:.2f} 優秀 (≥ 1.0)")
    elif m.sharpe_ratio >= 0.5:
        verdicts.append(f"🔶 Sharpe Ratio {m.sharpe_ratio:.2f} 可接受")
    else:
        verdicts.append(f"⚠️ Sharpe Ratio {m.sharpe_ratio:.2f} 偏低 (< 0.5)")
    return verdicts
