"""Warstwa wizualna: ciemny motyw, kafelki KPI, karty HTML i wspólny styl wykresów Plotly."""
import re
from html import escape

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

C = {
    "bg": "#041A33", "card": "#0B2747", "border": "#1D416A", "grid": "#16365B",
    "text": "#F2F6FA", "muted": "#A9BFD6", "faint": "#86A0BE",
    "orlen": "#FF5A4E", "ara": "#6FB4FF", "gold": "#F2C14E", "brand": "#00417B", "red": "#E2322A", "up": "#FF8A7A", "down": "#4FD1B5", "good": "#2FB597", "neutral": "#3A5A80",
    "wob": "#C39BFF",
}
# kategorie na ciemnym tle (kraje na wykresie historii); kolor wg kolejności wyboru
CATEGORICAL = ["#FF5A4E", "#6FB4FF", "#4FD1B5", "#F2C14E", "#C39BFF", "#FF9DC8", "#9BD35A", "#D9C9A6"]

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=Barlow:wght@400;500;600;700&family=Barlow+Semi+Condensed:ital,wght@1,700;1,800&display=swap');
html, body, [data-testid="stAppViewContainer"] p, [data-testid="stAppViewContainer"] li,
[data-testid="stAppViewContainer"] label, [data-testid="stAppViewContainer"] input,
[data-testid="stAppViewContainer"] button, h1, h2, h3, h4 { font-family: 'Barlow', system-ui, sans-serif; }
[data-testid="stHeader"] { background: transparent; }
.block-container { padding-top: 1.6rem; max-width: 1440px; }
h2, h3 { letter-spacing: -0.01em; }
[data-testid="stMetricValue"] { font-family: 'IBM Plex Mono', monospace; font-variant-numeric: tabular-nums; }
.stTabs [data-baseweb="tab-list"] { gap: 4px; }
.stTabs [data-baseweb="tab"] { height: 44px; padding: 0 16px; border-radius: 8px 8px 0 0; }
.stTabs [data-baseweb="tab"] p { font-size: 15px; font-weight: 500; }
/* ostatnia zakładka (Plan tankowania) dosunięta do prawej, oddzielona od zakładek z danymi */
.stTabs [role="tablist"], .stTabs [data-baseweb="tab-list"] { width: 100%; }
.stTabs [role="tablist"] > [role="tab"]:last-child,
.stTabs [data-baseweb="tab-list"] > [data-baseweb="tab"]:last-of-type { margin-left: auto; border-left: 1px solid #1D416A; padding-left: 20px; }
.num { font-family: 'IBM Plex Mono', monospace; font-variant-numeric: tabular-nums; }
.brand { display: flex; align-items: center; gap: 12px; }
.brand .chip { background: #FFFFFF; border-radius: 10px; padding: 6px 10px; display: flex; align-items: center; }
.brand .chip img { height: 34px; width: auto; display: block; }
.brand b { display: block; font-family: 'Barlow Semi Condensed', sans-serif; font-style: italic; font-weight: 800; font-size: 22px; letter-spacing: 0.01em; text-transform: uppercase; color: #F2F6FA; line-height: 1; }
.brand span { display: block; font-size: 12px; color: #A9BFD6; margin-top: 3px; }
.fresh { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 10px; font-size: 12px; color: #A9BFD6; min-height: 44px; }
.fresh i { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
.fresh .num { color: #F2F6FA; }
.kpi { background: #0B2747; border: 1px solid #1D416A; border-radius: 16px; padding: 16px 20px; display: flex; flex-direction: column; gap: 6px; height: 100%; min-height: 172px; box-sizing: border-box; min-width: 0; }
.kpi.accent { background: #0B2747; border: 1.5px solid #E2322A; }
.kpi .top { display: flex; align-items: center; justify-content: space-between; gap: 10px; min-height: 30px; }
.kpi .top svg { flex: 0 100 110px; min-width: 36px; height: 30px; }
.kpi .lbl { flex: 0 1 auto; min-width: 0; display: flex; align-items: center; gap: 8px; font-size: 13px; color: #A9BFD6; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.kpi .lbl i { flex-shrink: 0; width: 10px; height: 10px; border-radius: 3px; display: inline-block; }
.kpi .main { display: flex; align-items: baseline; flex-wrap: nowrap; white-space: nowrap; min-width: 0; }
.kpi .val { font-size: clamp(22px, 2.1vw, 32px); font-weight: 600; letter-spacing: -0.02em; color: #F2F6FA; line-height: 1.15; white-space: nowrap; }
.kpi .unit { font-size: 13px; color: #A9BFD6; margin-left: 6px; }
.kpi .delta { font-size: 13px; white-space: nowrap; }
.kpi .foot { margin-top: auto; font-size: 12px; line-height: 1.4; color: #86A0BE; }
.kpi.empty .val { color: #86A0BE; font-size: 20px; }
.signal { display: flex; flex-direction: column; gap: 10px; }
.signal .head { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.signal .head b { font-size: 16px; color: #F2F6FA; }
.pill { padding: 4px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; white-space: nowrap; }
.signal p { margin: 0; font-size: 14px; line-height: 1.5; color: #D3DFEC; }
.rank { display: flex; flex-direction: column; gap: 2px; }
.rank .r { display: grid; grid-template-columns: minmax(120px, 170px) 1fr 64px 110px; gap: 14px; align-items: center; min-height: 32px; padding: 0 10px; border-radius: 8px; font-size: 14px; color: #F2F6FA; }
.rank .r.hd { color: #86A0BE; font-size: 12px; min-height: 22px; }
.rank .r.pl { background: #3A1D26; font-weight: 700; }
.rank .code { width: 26px; display: inline-block; font-size: 12px; color: #A9BFD6; font-weight: 400; }
.rank .track { height: 10px; background: #16365B; border-radius: 3px; position: relative; }
.rank .track s { position: absolute; left: 0; top: 0; bottom: 0; border-radius: 3px; text-decoration: none; }
.rank .p, .rank .d { text-align: right; }
.reco { background: #0C3434; border: 1px solid #1E5E57; border-radius: 12px; padding: 16px 18px; display: flex; flex-direction: column; gap: 8px; }
.reco.warn { background: #33202A; border-color: #6B2E36; }
.reco .t { font-size: 12px; font-weight: 600; color: #4FD1B5; letter-spacing: 0.04em; text-transform: uppercase; }
.reco.warn .t { color: #FF9A8E; }
.reco .m { font-size: 15px; line-height: 1.45; color: #F2F6FA; }
.reco .s { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.reco .s .num { font-size: 28px; font-weight: 600; color: #4FD1B5; }
.reco .s span:last-child { font-size: 13px; color: #A7D8CC; }
.stops { display: flex; flex-wrap: wrap; gap: 8px; font-size: 13px; }
.stops span { padding: 6px 10px; border-radius: 8px; }
.card-title { margin: 0 0 2px; font-size: 18px; font-weight: 600; color: #F2F6FA; }
.card-sub { margin: 0 0 8px; font-size: 13px; color: #A9BFD6; }
.formula { display: flex; flex-wrap: wrap; align-items: stretch; gap: 10px; margin: 4px 0 18px; }
.formula .box { flex: 1 1 180px; background: #0B2747; border: 1px solid #1D416A; border-radius: 12px; padding: 12px 16px; }
.formula .box.res { border: 1.5px solid #E2322A; }
.formula .box small { display: block; font-size: 12px; color: #A9BFD6; margin-bottom: 2px; }
.formula .box b { font-size: 22px; font-weight: 600; color: #F2F6FA; }
.formula .box span.u { font-size: 12px; color: #A9BFD6; margin-left: 4px; }
.formula .op { display: flex; align-items: center; font-size: 26px; color: #A9BFD6; padding: 0 2px; }
.explain { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; margin-bottom: 8px; }
.explain > div { background: #0B2747; border: 1px solid #1D416A; border-radius: 14px; padding: 16px 18px; }
.explain h4 { margin: 0 0 8px; padding: 0; font-size: 15px; font-weight: 700; color: #F2F6FA; }
.explain p, .explain li { margin: 0 0 6px; font-size: 14px; line-height: 1.5; color: #D3DFEC; }
.explain ul { margin: 0; padding-left: 18px; }
.scale { display: flex; flex-direction: column; gap: 8px; margin-top: 4px; }
.scale div { display: flex; gap: 10px; align-items: flex-start; font-size: 14px; line-height: 1.4; color: #D3DFEC; }
.scale i { flex-shrink: 0; margin-top: 3px; width: 12px; height: 12px; border-radius: 3px; display: inline-block; }
.lg { display: flex; flex-direction: column; gap: 8px; max-width: 440px; }
.lg .it { display: flex; gap: 10px; align-items: flex-start; font-size: 14px; line-height: 1.4; color: #D3DFEC; }
.lg .it svg { flex-shrink: 0; margin-top: 2px; }
.lg .tips { margin: 4px 0 0; padding: 8px 0 0 18px; border-top: 1px solid #1D416A; }
.lg dl { margin: 0; display: grid; grid-template-columns: max-content 1fr; gap: 6px 14px; }
.lg dt { font-size: 13px; font-weight: 600; color: #F2F6FA; white-space: nowrap; }
.lg dd { margin: 0; font-size: 13px; line-height: 1.45; color: #D3DFEC; }
.lg .tips li { margin: 0 0 4px; font-size: 13px; line-height: 1.45; color: #A9BFD6; }
@media (max-width: 900px) { .explain { grid-template-columns: 1fr; } }
@media (max-width: 640px) {
  .kpi { min-height: 0; }
  .rank .r { grid-template-columns: 1fr 56px 90px; }
  .rank .track { display: none; }
  .kpi .val { font-size: 24px; }
}
</style>
"""


def inject_css() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def html(markup: str) -> None:
    """Wstawia HTML bez wcięć (markdown traktowałby wcięte linie jako blok kodu)."""
    st.markdown(" ".join(line.strip() for line in markup.splitlines() if line.strip()), unsafe_allow_html=True)


def num(value: float, decimals: int = 0, sign: bool = False) -> str:
    """Format PL: spacja tysięcy, przecinek dziesiętny, minus typograficzny."""
    s = f"{value:{'+' if sign else ''},.{decimals}f}".replace(",", "\u00a0").replace(".", ",")  # twarda spacja: liczba się nie łamie
    return s.replace("-", "−")


def sparkline(values, color: str, w: int = 110, h: int = 30) -> str:
    v = [float(x) for x in values]
    if len(v) < 2:
        return ""
    lo, hi = min(v), max(v)
    hi = hi if hi > lo else lo + 1
    pts = " ".join(f"{i * w / (len(v) - 1):.1f},{h - 3 - (x - lo) / (hi - lo) * (h - 6):.1f}" for i, x in enumerate(v))
    return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" preserveAspectRatio="none" aria-hidden="true">'
            f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.8" stroke-linejoin="round" vector-effect="non-scaling-stroke"/></svg>')


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
      <div class="top"><div class="lbl">{dot_html}{escape(label)}</div>{spark}</div>
      <div class="main"><span class="val num">{value}</span><span class="unit">{escape(unit)}</span></div>
      {delta}
      <div class="foot">{foot}</div>
    </div>""")


def kpi_empty(label: str, msg: str, dot: str | None = None) -> None:
    dot_html = f'<i style="background:{dot}"></i>' if dot else ""
    html(f'<div class="kpi empty"><div class="top"><div class="lbl">{dot_html}{escape(label)}</div></div>'
         f'<div class="val">brak danych</div><div class="foot">{escape(msg)}</div></div>')


def card_title(title: str, sub: str = "") -> None:
    html(f'<div class="card-title">{escape(title)}</div>' + (f'<div class="card-sub">{sub}</div>' if sub else ""))


def style_fig(fig: go.Figure, height: int = 380, **layout) -> go.Figure:
    axis = dict(gridcolor=C["grid"], zerolinecolor=C["grid"], linecolor=C["border"],
                tickfont=dict(color=C["faint"], size=11), title_font=dict(color=C["muted"], size=12))
    fig.update_layout(
        height=height, margin=dict(l=10, r=10, t=10, b=10), separators=", ",
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Barlow, system-ui, sans-serif", color=C["text"], size=13),
        hoverlabel=dict(bgcolor="#16365B", bordercolor=C["border"], font=dict(color=C["text"])),
        legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(color=C["muted"])),
    )
    fig.update_xaxes(**axis)
    fig.update_yaxes(**axis)
    fig.update_layout(**layout)
    return fig


def swatch(kind: str, color: str) -> str:
    """Mini-znacznik do legendy, rysowany tak jak element na wykresie."""
    w, h = 28, 16

    def line(dash: str = "none") -> str:
        return f'<line x1="2" y1="8" x2="26" y2="8" stroke="{color}" stroke-width="2.4" stroke-dasharray="{dash}"/>'

    body = {
        "line": line(),
        "dash": line("5 3"),
        "dot": line("1.5 3"),
        "step": f'<polyline points="2,12 10,12 10,4 19,4 19,9 26,9" fill="none" stroke="{color}" stroke-width="2.2"/>',
        "area": f'<rect x="2" y="3" width="24" height="10" rx="2" fill="{color}" fill-opacity="0.3"/>',
        "bar": f'<rect x="8" y="2" width="12" height="12" rx="2" fill="{color}"/>',
        "bar-faded": f'<rect x="8" y="2" width="12" height="12" rx="2" fill="{color}" fill-opacity="0.45"/>',
        "marker": f'<circle cx="14" cy="8" r="4.5" fill="{color}"/>',
        "marker-lg": f'<circle cx="14" cy="8" r="6.5" fill="{color}"/>',
        "line-markers": line() + f'<circle cx="14" cy="8" r="3.5" fill="{color}"/>',
        "vline": f'<line x1="14" y1="1" x2="14" y2="15" stroke="{color}" stroke-width="1.6" stroke-dasharray="3 2"/>',
    }[kind]
    return f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" aria-hidden="true">{body}</svg>'


def legend(items: list[tuple[str, str, str]], tips: list[str] = ()) -> None:
    """Legenda wykresu schowana pod przyciskiem – pokazuje się dopiero po kliknięciu.
    items: (rodzaj znacznika, kolor, opis HTML); tips: wskazówki „jak czytać”."""
    with st.popover("Legenda", icon=":material/info:"):
        rows = "".join(f'<div class="it">{swatch(k, c)}<span>{t}</span></div>' for k, c, t in items)
        tips_html = f'<ul class="tips">{"".join(f"<li>{t}</li>" for t in tips)}</ul>' if tips else ""
        html(f'<div class="lg">{rows}{tips_html}</div>')


TREND_NOTE = ("▲ wzrost · ▼ spadek · ● bez zmian – czerwony = drożej / niekorzystnie, "
              "turkusowy = taniej / korzystnie.")


def _arrow(v: float, decimals: int, suffix: str) -> str:
    if pd.isna(v):
        return "—"
    if round(v, decimals) == 0:
        return f"● {0:.{decimals}f}{suffix}"
    return f"{'▲' if v > 0 else '▼'} {v:+.{decimals}f}{suffix}"


def trend_table(df: pd.DataFrame, trend: dict[str, tuple[int, str]], fmt: dict[str, str] | None = None,
                good_up=None):
    """Styler do st.dataframe: kolumny zmian z ikoną ▲/▼/● i kolorem (wzrost ceny = czerwony, jak na kafelkach).
    trend: kolumna -> (miejsca po przecinku, dopisek); fmt: format pozostałych kolumn liczbowych;
    good_up: lista bool per wiersz – True, gdy wzrost jest korzystny (np. złoto). Wartości zostają liczbami (sortowanie)."""
    good = [False] * len(df) if good_up is None else list(good_up)

    def colors(col: pd.Series) -> list[str]:
        d = trend[col.name][0]
        return [f"color: {C['muted']}" if pd.isna(v) or round(v, d) == 0
                else f"color: {C['down'] if (v > 0) == g else C['up']}" for v, g in zip(col, good)]

    formatters = {c: (lambda v, d=d, sfx=sfx: _arrow(v, d, sfx)) for c, (d, sfx) in trend.items()}
    formatters.update(fmt or {})
    return df.style.apply(colors, subset=list(trend)).format(formatters, na_rep="—")


def table_legend(columns: dict[str, str], tips: list[str] = ()) -> dict:
    """Objaśnienie kolumn tabeli pod przyciskiem (jak legenda wykresu) + te same opisy jako dymek ⓘ w nagłówkach.
    Zwraca column_config do st.dataframe."""
    with st.popover("Co oznaczają kolumny", icon=":material/help:"):
        rows = "".join(f"<dt>{escape(c)}</dt><dd>{t}</dd>" for c, t in columns.items())
        tips_html = f'<ul class="tips">{"".join(f"<li>{t}</li>" for t in tips)}</ul>' if tips else ""
        html(f'<div class="lg" style="max-width:640px"><dl>{rows}</dl>{tips_html}</div>')
    strip = lambda t: re.sub(r"<[^>]+>", "", t)  # noqa: E731 – dymek w nagłówku to czysty tekst
    return {c: st.column_config.Column(help=strip(t)) for c, t in columns.items()}
