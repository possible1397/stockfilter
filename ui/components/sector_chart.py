"""Section 4 — 板塊輪動熱度."""
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

    if grade_col:
        grouped = df.groupby(["產業別", grade_col]).size().reset_index(name="count")
        pivot = grouped.pivot(index="產業別", columns=grade_col, values="count").fillna(0)
        # sort by total
        pivot["_total"] = pivot.sum(axis=1)
        pivot = pivot.sort_values("_total", ascending=False).head(12).drop(columns="_total")

        colors = {"A": "#27ae60", "B": "#2980b9", "C": "#bdc3c7"}
        fig = go.Figure()
        for grade in ["A", "B", "C"]:
            if grade in pivot.columns:
                fig.add_trace(go.Bar(
                    name=f"{grade} 級",
                    x=pivot.index,
                    y=pivot[grade],
                    marker_color=colors[grade],
                ))
        fig.update_layout(
            barmode="stack",
            height=320,
            margin=dict(t=10, b=80, l=20, r=20),
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
            xaxis_tickangle=-35,
        )
        st.plotly_chart(fig, width="stretch")

        # Summary table
        summary = df.groupby("產業別").agg(
            標的數=("股票代號", "count"),
            最高分=("進場總分", "max") if "進場總分" in df.columns else ("股票代號", "count"),
        ).sort_values("標的數", ascending=False).head(12)
        st.dataframe(summary, width="stretch")
    else:
        counts = df["產業別"].value_counts().head(12)
        st.bar_chart(counts)
