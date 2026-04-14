"""Section 1 — 大盤看板 (Market Health Banner)."""
import streamlit as st
import plotly.graph_objects as go


def _score_to_label(score: float) -> tuple[str, str]:
    if score >= 60:
        return "多頭積極", "#27ae60"
    elif score >= 40:
        return "震盪觀望", "#f39c12"
    else:
        return "空頭迴避", "#e74c3c"


def render(market_score: float | None, scan_date: str, ab_count: int) -> None:
    score = market_score if market_score is not None else 0.0
    label, color = _score_to_label(score)

    # Gauge chart
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=score,
        number={"font": {"size": 40, "color": color}},
        gauge={
            "axis": {"range": [0, 100], "tickwidth": 1},
            "bar": {"color": color},
            "steps": [
                {"range": [0, 40], "color": "#fadbd8"},
                {"range": [40, 60], "color": "#fef9e7"},
                {"range": [60, 100], "color": "#d5f5e3"},
            ],
            "threshold": {
                "line": {"color": "black", "width": 3},
                "thickness": 0.8,
                "value": score,
            },
        },
        title={"text": "大盤評分", "font": {"size": 18}},
    ))
    fig.update_layout(height=220, margin=dict(t=30, b=10, l=20, r=20))

    col1, col2, col3, col4 = st.columns([2, 1.2, 1.2, 1.2])
    with col1:
        st.plotly_chart(fig, width="stretch")
    with col2:
        st.markdown("**市場環境**")
        st.markdown(f"<h2 style='color:{color}'>{label}</h2>", unsafe_allow_html=True)
    with col3:
        st.markdown("**最後掃描**")
        st.markdown(f"<h3>{scan_date}</h3>", unsafe_allow_html=True)
    with col4:
        st.markdown("**今日 A/B 級**")
        st.markdown(f"<h2 style='color:#2980b9'>{ab_count} 檔</h2>", unsafe_allow_html=True)
