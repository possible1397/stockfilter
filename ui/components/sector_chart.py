"""Section 4 — 板塊輪動熱度 (Horizontal bar with grade breakdown)."""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go


def render(scanner_df: pd.DataFrame) -> None:
    st.subheader("📊 板塊輪動熱度")

    if scanner_df.empty or "產業別" not in scanner_df.columns:
        st.info("無板塊資料")
        return

    df = scanner_df.copy()
    grade_col = "族群等級" if "族群等級" in df.columns else None
    score_col = "進場總分" if "進場總分" in df.columns else None

    if grade_col:
        # 統計每個產業各等級數量
        grouped = df.groupby(["產業別", grade_col]).size().reset_index(name="count")
        pivot = grouped.pivot(index="產業別", columns=grade_col, values="count").fillna(0)

        # 計算重要指標
        summary = df.groupby("產業別").agg(
            標的數=("股票代號", "count"),
        )
        if score_col:
            summary["最高分"] = df.groupby("產業別")[score_col].max()
            summary["平均分"] = df.groupby("產業別")[score_col].mean().round(1)

        # A/B count
        for grade in ["A", "B", "C"]:
            if grade in pivot.columns:
                summary[f"{grade}級"] = pivot[grade].astype(int)
            else:
                summary[f"{grade}級"] = 0

        summary = summary.sort_values("標的數", ascending=True)  # ascending for horizontal bar

        # 水平堆疊條形圖
        colors = {"A": "#e74c3c", "B": "#3498db", "C": "#555555"}
        fig = go.Figure()

        for grade in ["C", "B", "A"]:  # Stack order: C at bottom, A at top
            col_name = f"{grade}級"
            if col_name in summary.columns:
                fig.add_trace(go.Bar(
                    name=f"{grade} 級",
                    y=summary.index,
                    x=summary[col_name],
                    orientation="h",
                    marker_color=colors[grade],
                    text=summary[col_name].apply(lambda v: str(int(v)) if v > 0 else ""),
                    textposition="inside",
                    textfont=dict(size=12, color="white"),
                ))

        fig.update_layout(
            barmode="stack",
            height=max(200, len(summary) * 45 + 60),
            margin=dict(t=10, b=20, l=10, r=20),
            legend=dict(
                orientation="h", yanchor="bottom", y=1.02,
                font=dict(color="#c9d1d9", size=12),
            ),
            xaxis=dict(
                title="標的數量", color="#888",
                gridcolor="rgba(99, 110, 255, 0.08)",
            ),
            yaxis=dict(color="#c9d1d9", automargin=True),
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig, width="stretch")

        # 簡潔的統計表格
        if score_col:
            display_cols = ["標的數", "A級", "B級", "C級", "最高分", "平均分"]
        else:
            display_cols = ["標的數", "A級", "B級", "C級"]
        display_cols = [c for c in display_cols if c in summary.columns]
        table = summary[display_cols].sort_values("標的數", ascending=False)

        st.dataframe(table, width="stretch")
    else:
        # Fallback: simple count
        counts = df["產業別"].value_counts().head(12)
        fig = go.Figure(go.Bar(
            y=counts.index,
            x=counts.values,
            orientation="h",
            marker_color="#667eea",
            text=counts.values,
            textposition="inside",
            textfont=dict(color="white"),
        ))
        fig.update_layout(
            height=max(200, len(counts) * 40 + 60),
            margin=dict(t=10, b=20, l=10, r=20),
            xaxis=dict(title="標的數量", color="#888"),
            yaxis=dict(color="#c9d1d9"),
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig, width="stretch")
