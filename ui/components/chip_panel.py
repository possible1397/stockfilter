"""Section 5 — 籌碼快照 (Chip Snapshot)."""
import streamlit as st
import pandas as pd


def render(scanner_df: pd.DataFrame) -> None:
    with st.expander("💰 籌碼快照（法人動向 Top 10）", expanded=False):
        if scanner_df.empty or "籌碼分" not in scanner_df.columns:
            st.info("無籌碼資料")
            return

        cols = [c for c in ["股票代號", "產業別", "籌碼分", "法人連續買超天數", "收盤價", "主要產品"]
                if c in scanner_df.columns]
        top10 = scanner_df[cols].sort_values("籌碼分", ascending=False).head(10)

        def _bar(val):
            try:
                pct = float(val) / 100
                return f"background: linear-gradient(90deg, #aed6f1 {pct*100:.0f}%, #fff {pct*100:.0f}%)"
            except Exception:
                return ""

        if "籌碼分" in top10.columns:
            styled = top10.style.map(_bar, subset=["籌碼分"])
            st.dataframe(styled, width="stretch", hide_index=True)
        else:
            st.dataframe(top10, width="stretch", hide_index=True)
