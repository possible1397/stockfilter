"""Section 1 — 大盤看板 (Market Health Banner)."""
import streamlit as st
import plotly.graph_objects as go


def _score_to_label(score: float) -> tuple[str, str, str]:
    """Return (label, color, emoji)."""
    if score >= 60:
        return "多頭積極", "#e74c3c", "🔴"   # 紅漲
    elif score >= 40:
        return "震盪觀望", "#f39c12", "🟡"
    else:
        return "空頭迴避", "#27ae60", "🟢"   # 綠跌


def render(market_score: float | None, scan_date: str, ab_count: int) -> None:
    score = market_score if market_score is not None else 0.0
    label, color, emoji = _score_to_label(score)

    # Gauge chart with premium styling
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=score,
        number={"font": {"size": 48, "color": color, "family": "Inter"}},
        gauge={
            "axis": {"range": [0, 100], "tickwidth": 1, "tickcolor": "#444",
                     "tickfont": {"color": "#888", "size": 11}},
            "bar": {"color": color, "thickness": 0.3},
            "bgcolor": "rgba(20, 20, 40, 0.5)",
            "borderwidth": 0,
            "steps": [
                {"range": [0, 40], "color": "rgba(39, 174, 96, 0.15)"},
                {"range": [40, 60], "color": "rgba(243, 156, 18, 0.15)"},
                {"range": [60, 100], "color": "rgba(231, 76, 60, 0.15)"},
            ],
            "threshold": {
                "line": {"color": color, "width": 3},
                "thickness": 0.8,
                "value": score,
            },
        },
        title={"text": "大盤評分", "font": {"size": 16, "color": "#8b949e"}},
    ))
    fig.update_layout(
        height=240,
        margin=dict(t=40, b=10, l=30, r=30),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )

    col1, col2, col3, col4 = st.columns([2.5, 1.2, 1.2, 1.2])

    with col1:
        st.plotly_chart(fig, width="stretch", config={'displayModeBar': False})

    with col2:
        st.markdown(f"""
        <div style="text-align: center; padding-top: 30px;">
            <div style="color: #8b949e; font-size: 13px; text-transform: uppercase;
                        letter-spacing: 1px; margin-bottom: 8px;">市場環境</div>
            <div style="font-size: 28px; margin-bottom: 4px;">{emoji}</div>
            <div style="color: {color}; font-size: 22px; font-weight: 700;">{label}</div>
        </div>
        """, unsafe_allow_html=True)

    with col3:
        st.markdown(f"""
        <div style="text-align: center; padding-top: 30px;">
            <div style="color: #8b949e; font-size: 13px; text-transform: uppercase;
                        letter-spacing: 1px; margin-bottom: 8px;">最後掃描</div>
            <div style="color: #e6edf3; font-size: 20px; font-weight: 600;
                        margin-top: 12px;">{scan_date}</div>
        </div>
        """, unsafe_allow_html=True)

    with col4:
        st.markdown(f"""
        <div style="text-align: center; padding-top: 30px;">
            <div style="color: #8b949e; font-size: 13px; text-transform: uppercase;
                        letter-spacing: 1px; margin-bottom: 8px;">今日 A/B 級</div>
            <div style="color: #667eea; font-size: 36px; font-weight: 700;
                        margin-top: 4px;">{ab_count}</div>
            <div style="color: #8b949e; font-size: 13px;">檔</div>
        </div>
        """, unsafe_allow_html=True)
