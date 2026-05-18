"""Section 3 — 觀察名單 (two tabs)."""
import re
import streamlit as st
import pandas as pd


def _has_chinese(text: str) -> bool:
    return bool(re.search(r'[\u4e00-\u9fff]', str(text)))


def _clean_product(text) -> str:
    s = str(text).strip()
    if not s or s == "nan" or not _has_chinese(s):
        return ""
    return s


# 英 → 中欄位名映射 (用於 diag CSV)
_DIAG_COL_MAP = {
    "watchlist_score": "觀察分數",
    "ticker": "股票代號",
    "name": "名稱",
    "failure_stage": "篩選階段",
    "vol_ratio": "量比",
    "convergence_ratio": "收斂率",
    "bar_span": "K線跨度",
    "reason": "原因",
}


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

    cols = [c for c in ["股票代號", "產業別", "進場總分", "收盤價", "ATR", "操作建議", "主要產品", "注意"] if c in converging.columns]
    if "進場總分" in converging.columns:
        converging = converging.sort_values("進場總分", ascending=False)

    # 格式化 & 清理英文描述
    display = converging[cols].copy()
    if "主要產品" in display.columns:
        display["主要產品"] = display["主要產品"].apply(_clean_product)

    # 用 column_config 控制數值格式
    col_config = {}
    if "收盤價" in display.columns:
        col_config["收盤價"] = st.column_config.NumberColumn(format="%.2f")
    if "進場總分" in display.columns:
        col_config["進場總分"] = st.column_config.NumberColumn(format="%.2f")
    if "ATR" in display.columns:
        col_config["ATR"] = st.column_config.NumberColumn(format="%.2f")

    st.dataframe(display, width="stretch", hide_index=True, column_config=col_config)


def _render_diag(df: pd.DataFrame) -> None:
    if df.empty:
        st.info("今日無診斷資料")
        return

    if "failure_stage" in df.columns:
        df = df[df["failure_stage"] != "通過"]
        if df.empty:
            st.info("今日無接近入選標的")
            return

    if "watchlist_score" in df.columns:
        df = df.sort_values("watchlist_score", ascending=False)

    cols = [c for c in [
        "watchlist_score", "ticker", "name", "failure_stage",
        "vol_ratio", "convergence_ratio", "bar_span", "reason"
    ] if c in df.columns]

    display = df[cols].copy()

    # 翻譯欄位名
    display.rename(columns=_DIAG_COL_MAP, inplace=True)

    # 暗色主題配色
    zh_stage = _DIAG_COL_MAP.get("failure_stage", "failure_stage")
    zh_vol = _DIAG_COL_MAP.get("vol_ratio", "vol_ratio")
    zh_conv = _DIAG_COL_MAP.get("convergence_ratio", "convergence_ratio")

    def _color(row):
        stage = str(row.get(zh_stage, ""))
        if stage == "通過":
            return ["background-color: rgba(39, 174, 96, 0.2); color: #e6edf3"] * len(row)
        elif "量" in stage:
            return ["background-color: rgba(243, 156, 18, 0.15); color: #e6edf3"] * len(row)
        else:
            return ["background-color: rgba(22, 27, 34, 0.6); color: #e6edf3"] * len(row)

    # 使用 style.format 強制小數位數
    fmt = {}
    if zh_vol in display.columns:
        fmt[zh_vol] = "{:.2f}"
    if zh_conv in display.columns:
        fmt[zh_conv] = "{:.2f}"
    
    zh_bar_span = _DIAG_COL_MAP.get("bar_span", "bar_span")
    if zh_bar_span in display.columns:
        fmt[zh_bar_span] = "{:.0f}"

    styled = display.style.apply(_color, axis=1)
    if fmt:
        styled = styled.format(fmt)

    st.dataframe(styled, width="stretch", hide_index=True)

