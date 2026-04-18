"""
台股收斂選股 — 交易戰情室 Dashboard
Run: streamlit run ui/dashboard.py
"""
import sys
import os

# Allow imports from project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import streamlit as st

from ui.utils.data_loader import (
    load_scanner, load_diag, latest_scan_date,
    latest_market_score, list_chart_pngs,
    load_positions_report, load_positions_config,
    load_app_config, list_available_dates,
)
from ui.components import (
    market_banner, entry_table, watchlist,
    sector_chart, chip_panel, portfolio_panel,
)

# ── Page Config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="台股收斂選股 | 交易戰情室",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Premium Dark Theme CSS ───────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Noto+Sans+TC:wght@300;400;500;700&display=swap');

/* ── Global ── */
html, body, [class*="css"] {
    font-family: 'Inter', 'Noto Sans TC', sans-serif !important;
}

.stApp {
    background: linear-gradient(135deg, #0a0a1a 0%, #0f1629 50%, #0a0a1a 100%);
}

/* ── Sidebar ── */
section[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #0d1117 0%, #161b22 100%);
    border-right: 1px solid rgba(99, 110, 255, 0.15);
}
section[data-testid="stSidebar"] .stMarkdown h1,
section[data-testid="stSidebar"] .stMarkdown h2,
section[data-testid="stSidebar"] .stMarkdown h3 {
    color: #c9d1d9 !important;
}
section[data-testid="stSidebar"] .stMarkdown p,
section[data-testid="stSidebar"] .stMarkdown li,
section[data-testid="stSidebar"] .stMarkdown table {
    color: #e6edf3 !important;
}
section[data-testid="stSidebar"] .stMarkdown code {
    background-color: rgba(99, 110, 255, 0.15) !important;
    color: #a5d6ff !important;
}

/* ── Main title ── */
.dashboard-title {
    background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    font-size: 32px;
    font-weight: 700;
    letter-spacing: -0.5px;
    margin-bottom: 0;
}
.dashboard-subtitle {
    color: #8b949e;
    font-size: 14px;
    margin-top: -8px;
    margin-bottom: 24px;
}

/* ── Tab styling ── */
.stTabs [data-baseweb="tab-list"] {
    gap: 8px;
    background: rgba(22, 27, 34, 0.6);
    border-radius: 12px;
    padding: 4px;
    border: 1px solid rgba(99, 110, 255, 0.1);
}
.stTabs [data-baseweb="tab"] {
    border-radius: 8px;
    padding: 10px 24px;
    font-weight: 500;
    color: #8b949e;
    transition: all 0.2s ease;
    flex: 1;
    text-align: center;
}
.stTabs [data-baseweb="tab"][aria-selected="true"] {
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%) !important;
    color: white !important;
    font-weight: 600;
}
.stTabs [data-baseweb="tab-highlight"] {
    display: none;
}
.stTabs [data-baseweb="tab-border"] {
    display: none;
}

/* ── Metrics ── */
[data-testid="stMetric"] {
    background: rgba(22, 27, 34, 0.8);
    border: 1px solid rgba(99, 110, 255, 0.12);
    border-radius: 12px;
    padding: 16px 20px;
    transition: all 0.3s ease;
}
[data-testid="stMetric"]:hover {
    border-color: rgba(99, 110, 255, 0.35);
    box-shadow: 0 0 20px rgba(99, 110, 255, 0.08);
}
[data-testid="stMetric"] label {
    color: #8b949e !important;
    font-size: 13px !important;
    font-weight: 500 !important;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}
[data-testid="stMetric"] [data-testid="stMetricValue"] {
    font-size: 24px !important;
    font-weight: 700 !important;
    color: #e6edf3 !important;
}

/* ── Dataframe ── */
.stDataFrame {
    border-radius: 12px !important;
    overflow: hidden;
}
.stDataFrame [data-testid="stDataFrameResizable"] {
    border: 1px solid rgba(99, 110, 255, 0.12) !important;
    border-radius: 12px !important;
}

/* ── Expander ── */
.streamlit-expanderHeader {
    background: rgba(22, 27, 34, 0.6) !important;
    border-radius: 8px !important;
    font-weight: 500 !important;
}

/* ── Divider ── */
hr {
    border-color: rgba(99, 110, 255, 0.1) !important;
    margin: 16px 0 !important;
}

/* ── Pulse animation for alerts ── */
@keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.5; }
}

/* ── Custom scrollbar ──  */
::-webkit-scrollbar {
    width: 6px;
    height: 6px;
}
::-webkit-scrollbar-track {
    background: transparent;
}
::-webkit-scrollbar-thumb {
    background: rgba(99, 110, 255, 0.3);
    border-radius: 3px;
}
::-webkit-scrollbar-thumb:hover {
    background: rgba(99, 110, 255, 0.5);
}

/* ── Section headers ── */
.section-header {
    color: #c9d1d9;
    font-size: 20px;
    font-weight: 600;
    margin: 8px 0 16px 0;
    padding-bottom: 8px;
    border-bottom: 2px solid rgba(99, 110, 255, 0.2);
}

/* ── Info boxes ── */
.stAlert {
    border-radius: 8px !important;
}

/* ── Sub-header styling ── */
h1, h2, h3 {
    color: #e6edf3 !important;
}

/* ── Fix inner tab panel highlight (remove extra slider) ── */
.stTabs [data-baseweb="tab-panel"] {
    padding-top: 12px;
}

/* ── Force dark theme on dataframe cells ── */
.stDataFrame table {
    color: #e6edf3 !important;
}
.stDataFrame th {
    background-color: #161b22 !important;
    color: #c9d1d9 !important;
}
.stDataFrame td {
    background-color: rgba(22, 27, 34, 0.8) !important;
    color: #e6edf3 !important;
}
</style>
""", unsafe_allow_html=True)


# ── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("# 🎯 戰情室")
    st.markdown("---")

    # Date selector
    available_dates = list_available_dates()
    if available_dates:
        selected_date = st.selectbox(
            "📅 報表日期",
            options=["最新"] + available_dates[::-1],
            index=0,
            help="選擇歷史日期查看過往報表",
        )
        scan_date = None if selected_date == "最新" else selected_date
    else:
        scan_date = None

    st.markdown("---")

    # System status
    st.markdown("### ⚙️ 系統參數")
    config = load_app_config()
    if config:
        bt = config.get("backtest", {})
        sc = config.get("scanner", {})
        pt = config.get("pattern", {})
        stop_loss_pct = bt.get('stop_loss', 'N/A')
        if isinstance(stop_loss_pct, (int, float)):
            stop_loss_pct = f"{stop_loss_pct*100:.0f}%"
        partial_th = bt.get('partial_profit_threshold', 'N/A')
        if isinstance(partial_th, (int, float)):
            partial_th = f"{partial_th*100:.0f}%"
        partial_r = bt.get('partial_profit_ratio', 'N/A')
        if isinstance(partial_r, (int, float)):
            partial_r = f"{partial_r*100:.0f}%"
        st.markdown(f"""
        | 參數 | 值 |
        |:---|:---|
        | 止損比例 | `{stop_loss_pct}` |
        | 倉位比例 | `{bt.get('position_size', 'N/A')}` |
        | 最大持倉 | `{bt.get('max_positions', 'N/A')}` |
        | 減碼門檻 | `{partial_th}` |
        | 減碼比例 | `{partial_r}` |
        | 動態 ATR | `{'✅ 是' if bt.get('dynamic_atr_update') else '❌ 否'}` |
        | K線追蹤 | `{'✅ 是' if bt.get('k_line_trail') else '❌ 否'}` |
        | 大盤門檻 | `{sc.get('market_score_threshold', 'N/A')}` |
        | 收斂門檻 | `{pt.get('convergence_threshold', 'N/A')}` |
        """)

    st.markdown("---")

    # Position config display
    pos_config = load_positions_config()
    if pos_config:
        st.markdown(f"### 📋 持倉清單 ({len(pos_config)} 檔)")
        for p in pos_config:
            ratio = p.get("remaining_ratio", 1.0)
            status = f"已減碼至{ratio*100:.0f}%" if ratio < 1.0 else "全倉"
            st.markdown(f"- **{p.get('ticker', '?')}** {p.get('shares', '?')}張 @ {p.get('entry_price', '?')} [{status}]")
    else:
        st.info("尚無持倉")

    st.markdown("---")
    st.caption("台股收斂選股系統 v2.1")
    st.caption("配色: 紅漲綠跌 (台灣慣例)")


# ── Load Data ────────────────────────────────────────────────────────────────
scanner_df = load_scanner(scan_date)
diag_df = load_diag(scan_date)
scan_date_str = latest_scan_date() if scan_date is None else scan_date
market_score = latest_market_score()
chart_pngs = list_chart_pngs()
pos_df = load_positions_report(scan_date_str if scan_date else None)
pos_config = load_positions_config()

if not scanner_df.empty and "族群等級" in scanner_df.columns:
    ab_count = scanner_df[scanner_df["族群等級"].isin(["A", "B"])].shape[0]
else:
    ab_count = 0

# Check if any position needs alert
has_alert = False
if not pos_df.empty and "現價" in pos_df.columns and "建議停損" in pos_df.columns:
    try:
        for _, row in pos_df.iterrows():
            cp = float(row["現價"])
            rs = float(row["建議停損"])
            if cp > 0 and rs > 0 and cp < rs:
                has_alert = True
                break
    except (ValueError, TypeError):
        pass


# ── Title ────────────────────────────────────────────────────────────────────
st.markdown('<p class="dashboard-title">台股收斂選股 — 交易戰情室</p>', unsafe_allow_html=True)
st.markdown(f'<p class="dashboard-subtitle">報表日期: {scan_date_str} · 大盤評分: {market_score or "N/A"}</p>',
            unsafe_allow_html=True)


# ── Tabs ─────────────────────────────────────────────────────────────────────
alert_badge = " ⚠️" if has_alert else ""
tab_market, tab_portfolio = st.tabs([
    f"📊 每日戰報",
    f"💼 我的持倉{alert_badge}",
])


# ── Tab 1: Market Report ─────────────────────────────────────────────────────
with tab_market:
    # Section 1: Market Banner
    market_banner.render(market_score, scan_date_str, ab_count)
    st.markdown("---")

    # Section 2 + 3: Entry list + Watchlist
    col_left, col_right = st.columns([3, 2])
    with col_left:
        entry_table.render(scanner_df, chart_pngs)
    with col_right:
        watchlist.render(scanner_df, diag_df)

    st.markdown("---")

    # Section 4: Sector Rotation
    sector_chart.render(scanner_df)

    st.markdown("---")

    # Section 5: Chip Snapshot
    chip_panel.render(scanner_df)


# ── Tab 2: My Portfolio ──────────────────────────────────────────────────────
with tab_portfolio:
    st.markdown('<p class="section-header">💼 持倉即時監控</p>', unsafe_allow_html=True)
    portfolio_panel.render(pos_df, pos_config)


# ── Footer ───────────────────────────────────────────────────────────────────
st.markdown("---")
st.markdown("""
<div style="text-align: center; padding: 16px; color: #555;">
    <span style="color: #667eea;">台股收斂選股系統</span> ·
    資料來源：本機掃描結果 ·
    最後掃描日 {date} ·
    刷新頁面取得最新資料
</div>
""".format(date=scan_date_str), unsafe_allow_html=True)
