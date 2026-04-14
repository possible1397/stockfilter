"""Section 2 — 明日進場名單."""
import streamlit as st
import pandas as pd


_GRADE_COLOR = {"A": "#d5f5e3", "B": "#d6eaf8", "C": "#f9f9f9"}
_STATUS_STAR = {"突破進場": "★ 突破進場", "收斂觀察": "收斂觀察"}


def _style_row(row: pd.Series) -> list[str]:
    grade = str(row.get("族群等級", "C")).strip()
    bg = _GRADE_COLOR.get(grade, "#ffffff")
    return [f"background-color: {bg}"] * len(row)


def render(df: pd.DataFrame, chart_pngs: dict[str, str]) -> None:
    st.subheader("🏆 明日進場名單")

    if df.empty:
        st.info("今日尚無掃描結果，請先執行 `python main.py`")
        return

    # Filter A/B only
    entry_df = df[df["族群等級"].isin(["A", "B"])].copy() if "族群等級" in df.columns else df.copy()

    if entry_df.empty:
        st.info("今日無 A/B 級標的")
        return

    # Format status with star
    if "狀態" in entry_df.columns:
        entry_df["狀態"] = entry_df["狀態"].map(lambda s: _STATUS_STAR.get(str(s).strip(), s))

    display_cols = [c for c in [
        "族群等級", "股票代號", "產業別", "狀態",
        "進場總分", "收盤價", "停損價", "目標價",
        "籌碼分", "法人連續買超天數", "主要產品", "操作建議"
    ] if c in entry_df.columns]

    styled = entry_df[display_cols].style.apply(_style_row, axis=1)
    st.dataframe(styled, width="stretch", hide_index=True)

    # Chart viewer
    if chart_pngs:
        tickers = entry_df["股票代號"].dropna().tolist() if "股票代號" in entry_df.columns else []
        matched = {t: p for name, p in chart_pngs.items()
                   for t in tickers if str(t).split(".")[0] in name}
        if matched:
            chosen = st.selectbox("查看K線圖", ["— 選擇股票 —"] + list(matched.keys()))
            if chosen != "— 選擇股票 —":
                st.image(matched[chosen], width="stretch")
