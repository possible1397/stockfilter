"""Section 3 — 觀察名單 (two tabs)."""
import streamlit as st
import pandas as pd


def render(scanner_df: pd.DataFrame, diag_df: pd.DataFrame) -> None:
    st.subheader("👁 觀察名單")
    tab1, tab2 = st.tabs(["即將突破（收斂中）", "接近入選（診斷）"])

    with tab1:
        _render_converging(scanner_df)

    with tab2:
        _render_diag(diag_df)


def _render_converging(df: pd.DataFrame) -> None:
    if df.empty:
        st.info("無資料")
        return
    if "狀態" not in df.columns:
        st.info("無狀態欄位")
        return

    converging = df[df["狀態"].str.contains("收斂", na=False)].copy()
    if converging.empty:
        st.info("目前無收斂中標的")
        return

    cols = [c for c in ["股票代號", "產業別", "進場總分", "收盤價", "ATR", "主要產品"] if c in converging.columns]
    if "進場總分" in converging.columns:
        converging = converging.sort_values("進場總分", ascending=False)
    st.dataframe(converging[cols], width="stretch", hide_index=True)


def _render_diag(df: pd.DataFrame) -> None:
    if df.empty:
        st.info("今日無診斷資料")
        return

    if "watchlist_score" in df.columns:
        df = df.sort_values("watchlist_score", ascending=False)

    cols = [c for c in [
        "watchlist_score", "ticker", "name", "failure_stage",
        "vol_ratio", "convergence_ratio", "bar_span", "reason"
    ] if c in df.columns]

    # colour rows by failure_stage
    def _color(row):
        stage = str(row.get("failure_stage", ""))
        if stage == "通過":
            return ["background-color: #d5f5e3"] * len(row)
        elif "量" in stage:
            return ["background-color: #fef9e7"] * len(row)
        else:
            return ["background-color: #f9f9f9"] * len(row)

    st.dataframe(df[cols].style.apply(_color, axis=1),
                 width="stretch", hide_index=True)
