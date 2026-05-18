"""Section 4 — 板塊收斂熱度 (Treemap: tile size = count, color = avg score)."""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go


def render(scanner_df: pd.DataFrame, market_df: pd.DataFrame | None = None) -> None:
    st.subheader("🗺️ 全市場資金熱力圖")

    # 熱力圖資料來源：優先用全市場快照，回退到掃描結果
    heatmap_df = market_df if (market_df is not None and not market_df.empty) else scanner_df

    required_cols = {"產業別", "股票代號", "漲跌幅", "成交金額", "收盤價"}
    if heatmap_df.empty or not required_cols.issubset(heatmap_df.columns):
        st.info("無板塊資料")
        return

    heatmap_df = heatmap_df.copy()

    # 過濾未知產業
    heatmap_df = heatmap_df[heatmap_df["產業別"] != "未知"]

    # 掃描結果的進場總分 lookup（用於 tooltip 標注）
    score_lookup: dict = {}
    if not scanner_df.empty and "進場總分" in scanner_df.columns and "股票代號" in scanner_df.columns:
        score_lookup = dict(zip(scanner_df["股票代號"], scanner_df["進場總分"]))

    # 聚合：每個產業的總成交金額、平均漲跌幅
    summary = heatmap_df.groupby("產業別").agg(
        總成交金額=("成交金額", "sum"),
        平均漲跌幅=("漲跌幅", "mean"),
        標的數=("股票代號", "count")
    ).reset_index()

    labels = []
    parents = []
    values = []
    colors = []
    custom_text = []

    # 建立板塊 (第一層)
    for _, row in summary.iterrows():
        sector = row["產業別"]
        labels.append(sector)
        parents.append("")
        values.append(max(1, row["總成交金額"]))
        colors.append(row["平均漲跌幅"])
        turnover_str = f"{row['總成交金額']/1e8:.2f} 億" if row['總成交金額'] >= 1e8 else f"{row['總成交金額']/1e4:.0f} 萬"
        parts = [
            f"<b>{sector}</b>",
            f"標的數: {int(row['標的數'])}",
            f"總成交金額: {turnover_str}",
            f"平均漲跌幅: {row['平均漲跌幅']:.2f}%"
        ]
        custom_text.append("<br>".join(parts))

    # 建立個股 (第二層)
    for _, row in heatmap_df.iterrows():
        ticker = row["股票代號"]
        sector = row["產業別"]
        stock_name = row["名稱"] if ("名稱" in heatmap_df.columns and str(row["名稱"]).strip()) else ""
        display_label = f"{ticker}<br>{stock_name}<br>{row['漲跌幅']}%" if stock_name else f"{ticker}<br>{row['漲跌幅']}%"
        labels.append(display_label)
        parents.append(sector)
        values.append(max(0.1, row["成交金額"]))
        colors.append(row["漲跌幅"])
        turnover_str = f"{row['成交金額']/1e8:.2f} 億" if row['成交金額'] >= 1e8 else f"{row['成交金額']/1e4:.0f} 萬"
        parts = [
            f"<b>{ticker}{' ' + stock_name if stock_name else ''}</b>",
            f"漲跌幅: {row['漲跌幅']:.2f}%",
            f"成交金額: {turnover_str}",
            f"收盤價: {row['收盤價']:.2f}"
        ]
        if ticker in score_lookup:
            parts.append(f"⭐ 進場總分: {score_lookup[ticker]:.1f}")
        custom_text.append("<br>".join(parts))

    fig = go.Figure(go.Treemap(
        labels=labels,
        parents=parents,
        values=values,
        text=custom_text,
        hovertemplate="%{text}<extra></extra>",
        texttemplate="<b>%{label}</b>",
        branchvalues="total",
        pathbar=dict(visible=False),
        tiling=dict(pad=2),
        textfont=dict(size=14, color="white"),
        marker=dict(
            colors=colors,
            colorscale=[
                [0.0, "#008a5c"], # 綠色 (跌)
                [0.5, "#333333"], # 黑色 (平)
                [1.0, "#e74c3c"], # 紅色 (漲)
            ],
            cmid=0, # 強制 0 為中間色 (黑色)
            showscale=True,
            line=dict(width=1, color="rgba(255,255,255,0.2)"),
            colorbar=dict(
                title=dict(text="漲跌幅 (%)", font=dict(color="#888", size=12)),
                thickness=15,
                len=0.8,
                tickfont=dict(color="#888", size=11),
                ticksuffix="%"
            ),
        ),
    ))

    fig.update_layout(
        height=450,
        margin=dict(t=10, b=10, l=10, r=10),
        paper_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

    # 說明文字
    st.caption("磚塊越大 = 成交金額越高；顏色越紅 = 漲幅越高，顏色越綠 = 跌幅越高。")

    # 明細表：顯示通過 VCP 篩選的進場清單
    detail_df = scanner_df if not scanner_df.empty else heatmap_df
    detail_cols = [c for c in ["股票代號", "產業別", "收盤價", "漲跌幅", "成交金額", "進場總分"] if c in detail_df.columns]
    if detail_cols:
        table = detail_df[detail_cols].sort_values("成交金額", ascending=False).reset_index(drop=True)
        table["成交金額"] = table["成交金額"].apply(lambda x: f"{x/1e8:.2f} 億" if x >= 1e8 else f"{x/1e4:.0f} 萬")
        st.dataframe(table, use_container_width=True, hide_index=True)
