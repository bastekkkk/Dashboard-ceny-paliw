"""Warstwa wizualna: ciemny motyw, kafelki KPI, karty HTML i wspólny styl wykresów Plotly."""
from html import escape

import plotly.graph_objects as go
import streamlit as st

C = {
    "bg": "#0E1216", "card": "#161B21", "border": "#252C35", "grid": "#222931",
    "text": "#E9EDF1", "muted": "#98A3B0", "faint": "#7E8996",
    "orlen": "#F2A93B", "ara": "#5AA2F0", "up": "#F59A7E", "down": "#5FD4BA", "good": "#3CC6A8", "neutral": "#4A5563",
}
# kategorie na ciemnym tle (kraje na wykresie historii); kolor wg kolejności wyboru
CATEGORICAL = ["#F2A93B", "#5AA2F0", "#3CC6A8", "#E87BA4", "#B59CFF", "#F07A5A", "#9BD35A", "#D9C9A6"]

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600;700&display=swap');
html, body, [data-testid="stAppViewContainer"] p, [data-testid="stAppViewContainer"] li,
[data-testid="stAppViewContainer"] label, [data-testid="stAppViewContainer"] input,
[data-testid="stAppViewContainer"] button, h1, h2, h3, h4 { font-family: 'IBM Plex Sans', system-ui, sans-serif; }
[data-testid="stHeader"] { background: transparent; }
.block-container { padding-top: 1.6rem; max-width: 1440px; }
h2, h3 { letter-spacing: -0.01em; }
[data-testid="stMetricValue"] { font-family: 'IBM Plex Mono', monospace; font-variant-numeric: tabular-nums; }
.stTabs [data-baseweb="tab-list"] { gap: 4px; }
.stTabs [data-baseweb="tab"] { height: 44px; padding: 0 16px; border-radius: 8px 8px 0 0; }
.stTabs [data-baseweb="tab"] p { font-size: 15px; font-weight: 500; }
.num { font-family: 'IBM Plex Mono', monospace; font-variant-numeric: tabular-nums; }
.brand { display: flex; align-items: center; gap: 12px; }
.brand b { display: block; font-size: 19px; font-weight: 700; color: #E9EDF1; }
.brand span { display: block; font-size: 12px; color: #98A3B0; }
.fresh { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 10px; font-size: 12px; color: #98A3B0; min-height: 44px; }
.fresh i { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.fresh .num { color: #E9EDF1; }
.kpi { background: #161B21; border: 1px solid #252C35; border-radius: 16px; padding: 18px 20px; display: flex; flex-direction: column; gap: 8px; height: 100%; box-sizing: border-box; }
.kpi.accent { background: #1B1F1A; border-color: #4A3D22; }
.kpi .lbl { display: flex; align-items: center; gap: 8px; font-size: 13px; color: #98A3B0; }
.kpi .lbl i { width: 10px; height: 10px; border-radius: 3px; display: inline-block; }
.kpi .row { display: flex; align-items: flex-end; justify-content: space-between; gap: 10px; }
.kpi .val { font-size: 30px; font-weight: 600; letter-spacing: -0.02em; color: #E9EDF1; line-height: 1.15; }
.kpi .unit { font-size: 13px; color: #98A3B0; margin-left: 6px; }
.kpi .delta { font-size: 13px; white-space: nowrap; }
.kpi .row svg { flex-shrink: 1; min-width: 60px; }
.kpi .foot { font-size: 12px; color: #7E8996; }
.kpi.empty .val { color: #7E8996; font-size: 20px; }
.signal { display: flex; flex-direction: column; gap: 10px; }
.signal .head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.signal .head b { font-size: 16px; color: #E9EDF1; }
.pill { padding: 4px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; white-space: nowrap; }
.signal p { margin: 0; font-size: 14px; line-height: 1.5; color: #C9D1DA; }
.rank { display: flex; flex-direction: column; gap: 2px; }
.rank .r { display: grid; grid-template-columns: minmax(120px, 170px) 1fr 64px 110px; gap: 14px; align-items: center; min-height: 32px; padding: 0 10px; border-radius: 8px; font-size: 14px; color: #E9EDF1; }
.rank .r.hd { color: #7E8996; font-size: 12px; min-height: 22px; }
.rank .r.pl { background: #231E14; font-weight: 700; }
.rank .code { width: 26px; display: inline-block; font-size: 12px; color: #98A3B0; font-weight: 400; }
.rank .track { height: 10px; background: #1E242B; border-radius: 3px; position: relative; }
.rank .track s { position: absolute; left: 0; top: 0; bottom: 0; border-radius: 3px; text-decoration: none; }
.rank .p, .rank .d { text-align: right; }
.reco { background: #12251F; border: 1px solid #1F4A3E; border-radius: 12px; padding: 16px 18px; display: flex; flex-direction: column; gap: 8px; }
.reco.warn { background: #1E1A12; border-color: #4A3D22; }
.reco .t { font-size: 12px; font-weight: 600; color: #5FD4BA; letter-spacing: 0.04em; text-transform: uppercase; }
.reco.warn .t { color: #F5C46E; }
.reco .m { font-size: 15px; line-height: 1.45; color: #E9EDF1; }
.reco .s { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.reco .s .num { font-size: 28px; font-weight: 600; color: #5FD4BA; }
.reco .s span:last-child { font-size: 13px; color: #A7C9BF; }
.stops { display: flex; flex-wrap: wrap; gap: 8px; font-size: 13px; }
.stops span { padding: 6px 10px; border-radius: 8px; }
.card-title { margin: 0 0 2px; font-size: 18px; font-weight: 600; color: #E9EDF1; }
.card-sub { margin: 0 0 8px; font-size: 13px; color: #98A3B0; }
@media (max-width: 640px) {
  .rank .r { grid-template-columns: 1fr 56px 90px; }
  .rank .track { display: none; }
  .kpi .val { font-size: 24px; }
}
</style>
"""

LOGO = (
    '<svg width="36" height="36" viewBox="0 0 36 36" fill="none" aria-hidden="true">'
    '<rect width="36" height="36" rx="9" fill="#F2A93B"/>'
    '<path d="M18 8c4 5 7 8.6 7 12a7 7 0 0 1-14 0c0-3.4 3-7 7-12z" stroke="#0E1216" stroke-width="2.4" stroke-linejoin="round"/>'
    "</svg>"
)


def inject_css() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def html(markup: str) -> None:
    """Wstawia HTML bez wcięć (markdown traktowałby wcięte linie jako blok kodu)."""
    st.markdown("".join(line.strip() for line in markup.splitlines()), unsafe_allow_html=True)


def num(value: float, decimals: int = 0, sign: bool = False) -> str:
    """Format PL: spacja tysięcy, przecinek dziesiętny, minus typograficzny."""
    s = f"{value:{'+' if sign else ''},.{decimals}f}".replace(",", " ").replace(".", ",")
    return s.replace("-", "−")


def sparkline(values, color: str, w: int = 120, h: int = 36) -> str:
    v = [float(x) for x in values]
    if len(v) < 2:
        return ""
    lo, hi = min(v), max(v)
    hi = hi if hi > lo else lo + 1
    pts = " ".join(f"{i * w / (len(v) - 1):.1f},{h - 3 - (x - lo) / (hi - lo) * (h - 6):.1f}" for i, x in enumerate(v))
    return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" aria-hidden="true">'
            f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.8" stroke-linejoin="round"/></svg>')


def delta_html(diff: float, pct: float | None, decimals: int, suffix: str = "d/d") -> str:
    """Wzrost ceny = gorzej dla kupującego (pomarańczowy ▲), spadek = lepiej (turkusowy ▼)."""
    if diff == 0:
        return f'<span class="delta num" style="color:{C["muted"]}">bez zmian {suffix}</span>'
    up = diff > 0
    pct_txt = f" ({num(pct * 100, 1, sign=True)}%)" if pct is not None else ""
    return (f'<span class="delta num" style="color:{C["up"] if up else C["down"]}">'
            f'{"▲" if up else "▼"} {num(diff, decimals, sign=True)}{pct_txt} {suffix}</span>')


def kpi(label: str, value: str, unit: str, delta: str = "", spark: str = "", foot: str = "",
        dot: str | None = None, accent: bool = False) -> None:
    dot_html = f'<i style="background:{dot}"></i>' if dot else ""
    html(f"""
    <div class="kpi{' accent' if accent else ''}">
      <div class="lbl">{dot_html}{escape(label)}</div>
      <div class="row"><div><div><span class="val num">{value}</span><span class="unit">{escape(unit)}</span></div>{delta}</div>{spark}</div>
      <div class="foot">{foot}</div>
    </div>""")


def kpi_empty(label: str, msg: str, dot: str | None = None) -> None:
    dot_html = f'<i style="background:{dot}"></i>' if dot else ""
    html(f'<div class="kpi empty"><div class="lbl">{dot_html}{escape(label)}</div>'
         f'<div class="val">brak danych</div><div class="foot">{escape(msg)}</div></div>')


def card_title(title: str, sub: str = "") -> None:
    html(f'<div class="card-title">{escape(title)}</div>' + (f'<div class="card-sub">{sub}</div>' if sub else ""))


def style_fig(fig: go.Figure, height: int = 380, **layout) -> go.Figure:
    axis = dict(gridcolor=C["grid"], zerolinecolor=C["grid"], linecolor=C["border"],
                tickfont=dict(color=C["faint"], size=11), title_font=dict(color=C["muted"], size=12))
    fig.update_layout(
        height=height, margin=dict(l=10, r=10, t=10, b=10), separators=", ",
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="IBM Plex Sans, system-ui, sans-serif", color=C["text"], size=13),
        hoverlabel=dict(bgcolor="#1E242B", bordercolor=C["border"], font=dict(color=C["text"])),
        legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(color=C["muted"])),
    )
    fig.update_xaxes(**axis)
    fig.update_yaxes(**axis)
    fig.update_layout(**layout)
    return fig
