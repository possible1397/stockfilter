"""
台股收斂選股 Dashboard
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
)
from ui.components import market_banner, entry_table, watchlist, sector_chart, chip_panel

# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="台股收斂選股 Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── CSS tweaks ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
  .block-container { padding-top: 1rem; }
  h2 { margin-top: 0.5rem; }
</style>
""", unsafe_allow_html=True)

# ── Load data ─────────────────────────────────────────────────────────────────
scanner_df = load_scanner()
diag_df = load_diag()
scan_date = latest_scan_date()
market_score = latest_market_score()
chart_pngs = list_chart_pngs()

if not scanner_df.empty and "族群等級" in scanner_df.columns:
    ab_count = scanner_df[scanner_df["族群等級"].isin(["A", "B"])].shape[0]
else:
    ab_count = 0

# ── Section 1: Market Banner ──────────────────────────────────────────────────
st.markdown("## 📈 台股收斂選股 Dashboard")
market_banner.render(market_score, scan_date, ab_count)
st.divider()

# ── Section 2 + 3: Entry list + Watchlist (side by side) ─────────────────────
col_left, col_right = st.columns([3, 2])

with col_left:
    entry_table.render(scanner_df, chart_pngs)

with col_right:
    watchlist.render(scanner_df, diag_df)

st.divider()

# ── Section 4: Sector Rotation ────────────────────────────────────────────────
sector_chart.render(scanner_df)

st.divider()

# ── Section 5: Chip Snapshot ──────────────────────────────────────────────────
chip_panel.render(scanner_df)

# ── Footer ────────────────────────────────────────────────────────────────────
st.caption(f"資料來源：本機掃描結果 · 最後掃描日 {scan_date} · 刷新頁面取得最新資料")
