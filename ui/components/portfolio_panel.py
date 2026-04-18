"""Portfolio Panel — 持倉監控面板 (Streamlit-native)."""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go


def render(pos_df: pd.DataFrame, positions_config: list[dict]) -> None:
    """Render the portfolio monitoring panel."""
    if pos_df.empty:
        st.markdown(
            '<div style="text-align:center;padding:60px 20px;color:#888;">'
            '<div style="font-size:48px;margin-bottom:16px;">📭</div>'
            '<h3 style="color:#aaa;">尚無持倉監控資料</h3>'
            '<p>請先使用 CLI 新增持倉後執行掃描：</p>'
            '<code style="background:#1a1a2e;padding:8px 16px;border-radius:6px;color:#e94560;">'
            'python main.py --add-position 1717.TW 2026-04-08 67.1 2</code></div>',
            unsafe_allow_html=True,
        )
        return

    # Summary metrics row
    total_positions = len(pos_df)
    profit_count = len(pos_df[pos_df["損益%"] > 0]) if "損益%" in pos_df.columns else 0
    loss_count = total_positions - profit_count

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("持倉檔數", f"{total_positions} 檔")
    with col2:
        st.metric("獲利中", f"{profit_count} 檔",
                  delta=f"{profit_count}" if profit_count > 0 else None)
    with col3:
        st.metric("虧損中", f"{loss_count} 檔",
                  delta=f"-{loss_count}" if loss_count > 0 else "0",
                  delta_color="inverse")

    st.markdown("---")

    # Individual position cards
    for _, row in pos_df.iterrows():
        _render_position_card(row)


def _render_position_card(row: pd.Series) -> None:
    """Render a single position card using Streamlit-native components."""
    ticker = row.get("股票代號", "???")
    entry_price = row.get("買進均價", 0)
    current_price = row.get("現價", 0)
    profit_pct = row.get("損益%", 0)
    remaining = row.get("剩餘比例", "100%")
    hard_stop = row.get("硬停損", 0)
    atr_stop = row.get("ATR追蹤停損", 0)
    kline_stop = row.get("K線追蹤停損", 0)
    recommended_stop = row.get("建議停損", 0)
    highest = row.get("持倉最高價", 0)
    action = row.get("操作建議", "")

    # Safe numeric conversion
    try:
        profit_val = float(profit_pct)
    except (ValueError, TypeError):
        profit_val = 0.0
    try:
        current_val = float(current_price)
    except (ValueError, TypeError):
        current_val = 0.0
    try:
        entry_val = float(entry_price)
    except (ValueError, TypeError):
        entry_val = 0.0
    try:
        recommended_val = float(recommended_stop)
    except (ValueError, TypeError):
        recommended_val = 0.0
    try:
        highest_val = float(highest)
    except (ValueError, TypeError):
        highest_val = 0.0

    # Determine status (台股: 紅漲綠跌)
    if profit_val > 10:
        status_emoji = "🔴"
    elif profit_val > 0:
        status_emoji = "🟠"
    elif profit_val > -5:
        status_emoji = "⚪"
    else:
        status_emoji = "🟢"

    is_danger = current_val > 0 and recommended_val > 0 and current_val < recommended_val
    danger_text = " ‼️ 跌破停損" if is_danger else ""

    # --- Card using st.container + native components ---
    with st.container(border=True):
        # Header row
        h_col1, h_col2 = st.columns([3, 1])
        with h_col1:
            st.subheader(f"{status_emoji} {ticker}{danger_text}")
        with h_col2:
            delta_val = current_val - entry_val if entry_val > 0 else 0
            st.metric(
                "損益",
                f"{profit_val:+.1f}%",
                delta=f"{delta_val:+.2f} 元",
                delta_color="normal" if profit_val >= 0 else "inverse",
            )

        # Price info row
        p1, p2, p3, p4 = st.columns(4)
        with p1:
            st.metric("買進均價", f"{entry_val:.2f}")
        with p2:
            st.metric("現價", f"{current_val:.2f}")
        with p3:
            st.metric("持倉最高", f"{highest_val:.2f}")
        with p4:
            st.metric("剩餘比例", f"{remaining}")

        # Stop-loss row
        s1, s2, s3, s4 = st.columns(4)
        with s1:
            st.metric("硬停損", f"{hard_stop}", help="買進價 x (1 - 止損比例)")
        with s2:
            st.metric("ATR 追蹤停損", f"{atr_stop}", help="最高價 - ATR*倍數")
        with s3:
            st.metric("K線追蹤停損", f"{kline_stop}", help="前一日最低價")
        with s4:
            st.metric("★ 建議停損", f"{recommended_stop}", help="取 ATR 與 K線 的較高者")

        # Stop-loss visualization
        _render_stop_gauge(ticker, current_val,
                           float(hard_stop or 0),
                           float(atr_stop or 0),
                           float(kline_stop or 0),
                           entry_val, highest_val)

        # Action advice
        if action:
            if is_danger:
                st.error(f"**操作建議：** {action}")
            elif profit_val > 10:
                st.warning(f"**操作建議：** {action}")
            else:
                st.info(f"**操作建議：** {action}")


def _render_stop_gauge(ticker, current, hard, atr, kline, entry, highest):
    """Render a vertical price-level chart showing all stop levels optionally with K-lines."""
    if highest <= 0 or entry <= 0:
        return

    # Try to load K-line data for context
    try:
        from data.database import load_dataframe
        df = load_dataframe(ticker)
        has_kline = not df.empty
    except Exception:
        has_kline = False

    all_prices = [p for p in [hard, atr, kline, entry, current, highest] if p > 0]
    if not all_prices:
        return
    min_p = min(all_prices) * 0.95
    max_p = max(all_prices) * 1.05

    fig = go.Figure()

    # Line markers with better styling
    line_styles = [
        (hard, "🔽 硬停損", "#27ae60", "dot", 2),
        (kline, "🔸 K線停損", "#f39c12", "dash", 2),
        (atr, "🔹 ATR停損", "#3498db", "dash", 2),
        (entry, "⚪ 買進價", "#95a5a6", "solid", 1.5),
        (current, "⭐ 現價", "#ecf0f1", "solid", 3),
        (highest, "🔺 最高價", "#e74c3c", "dot", 2),
    ]

    if has_kline:
        view_df = df.tail(60)  # last 60 trading days
        x_dates = view_df.index.strftime('%Y-%m-%d')
        k_min = view_df['Low'].min() * 0.95
        k_max = view_df['High'].max() * 1.05
        min_p = min(min_p, k_min)
        max_p = max(max_p, k_max)

        # Draw Candlesticks
        fig.add_trace(go.Candlestick(
            x=x_dates,
            open=view_df['Open'], high=view_df['High'],
            low=view_df['Low'], close=view_df['Close'],
            name="K線",
            increasing_line_color='#ef5350', decreasing_line_color='#26a69a',
            showlegend=False
        ))

        # Horizontal level lines
        for price, name, color, dash, width in line_styles:
            if price > 0:
                fig.add_hline(
                    y=price, line_dash=dash, line_color=color, line_width=width
                )
                fig.add_annotation(
                    x=1.02, y=price, xref="paper", yref="y",
                    text=f"{name} {price:.1f}",
                    showarrow=False,
                    xanchor="left", yanchor="middle",
                    font=dict(size=12, color=color, weight="bold" if "現價" in name else "normal")
                )

        fig.update_layout(
            xaxis_rangeslider_visible=False,
            xaxis=dict(type='category', showgrid=False, nticks=10, color="#555")
        )
    else:
        # Fallback to horizontal segments if no K-line
        for price, name, color, dash, width in line_styles:
            if price > 0:
                fig.add_trace(go.Scatter(
                    x=[0.1, 0.9], y=[price, price], 
                    mode="lines+text",
                    line=dict(color=color, width=width, dash=dash),
                    text=["", f"{name} {price:.1f}"],
                    textposition="middle right",
                    textfont=dict(size=13, color=color),
                    name=name, showlegend=False,
                    hovertemplate=f"{name}: {price:.2f}<extra></extra>",
                ))
        fig.update_layout(xaxis=dict(visible=False, range=[0, 1]))

    # Highlight zones (hrect works for both x-axis types since it anchors to y)
    if hard > 0:
        fig.add_hrect(
            y0=min_p, y1=hard, fillcolor="rgba(39, 174, 96, 0.08)", line_width=0,
            annotation_text="危險區", annotation_position="bottom left",
            annotation_font=dict(size=10, color="rgba(39, 174, 96, 0.5)"), layer="below"
        )
    if atr > 0:
        fig.add_hrect(
            y0=atr, y1=max_p, fillcolor="rgba(231, 76, 60, 0.04)", line_width=0,
            layer="below"
        )

    fig.update_layout(
        height=350,
        margin=dict(t=15, b=25, l=10, r=100),
        yaxis=dict(
            range=[min_p, max_p], showgrid=False,
            tickformat=".1f", color="#888",
            title=dict(text="價格", font=dict(size=10, color="#555")),
        ),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False}, key=f"gauge_{ticker}")
