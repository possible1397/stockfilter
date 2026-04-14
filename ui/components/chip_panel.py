"""Section 5 — 籌碼快照 (Chip Snapshot)."""
import re
import streamlit as st
import pandas as pd


def _has_chinese(text: str) -> bool:
    """檢查是否包含中文字元"""
    return bool(re.search(r'[\u4e00-\u9fff]', str(text)))


def _clean_product(text) -> str:
    """移除英文描述，只保留中文"""
    s = str(text).strip()
    if not s or s == "nan" or not _has_chinese(s):
        return ""
    return s


def render(scanner_df: pd.DataFrame) -> None:
    with st.expander("💰 籌碼快照（法人動向 Top 10）", expanded=False):
        if scanner_df.empty or "籌碼分" not in scanner_df.columns:
            st.info("無籌碼資料")
            return

        cols = [c for c in ["股票代號", "產業別", "籌碼分", "法人連續買超天數", "收盤價", "主要產品"]
                if c in scanner_df.columns]
        top10 = scanner_df[cols].sort_values("籌碼分", ascending=False).head(10).copy()

        # 清理英文產品描述
        if "主要產品" in top10.columns:
            top10["主要產品"] = top10["主要產品"].apply(_clean_product)

        def _bar(val):
            try:
                pct = float(val) / 100
                return f"background: linear-gradient(90deg, #aed6f1 {pct*100:.0f}%, transparent {pct*100:.0f}%)"
            except Exception:
                return ""

        # 使用 style.format 強制小數位數
        fmt = {}
        if "籌碼分" in top10.columns:
            fmt["籌碼分"] = "{:.2f}"
        if "收盤價" in top10.columns:
            fmt["收盤價"] = "{:.2f}"

        if "籌碼分" in top10.columns:
            styled = top10.style.map(_bar, subset=["籌碼分"])
            if fmt:
                styled = styled.format(fmt)
            st.dataframe(styled, width="stretch", hide_index=True)
        else:
            st.dataframe(top10, width="stretch", hide_index=True)
