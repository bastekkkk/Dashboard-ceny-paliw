"""Dashboard hurtowych cen paliw. Uruchom: streamlit run app.py"""
import math
from datetime import date, timedelta
from html import escape

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import auth
import db
import login_ui
import scheduler
import ui
from sources import ara_manual, oilpriceapi, orlen, yahoo
from sources import eu_oil_bulletin as wob
from ui import C, CATEGORICAL

M3_PER_T = 1.1834  # 1 t / 0,845 kg/l = 1183,4 l
PRESETS = {"7D": 7, "1M": 30, "3M": 91, "6M": 182, "1Y": 365, "MAX": None}
SERIES_COLORS = {orlen.SERIES: C["orlen"], ara_manual.SERIES: C["ara"],
                 "gold": C["gold"], "silver": "#C8D3DF", "usdpln": C["ara"]}
PREMIUM_WINDOW_DAYS = 90
RANGE_TIP = "Zakres zmieniasz przyciskami 7D–MAX nad wykresem; „Własny” = dowolne daty."
# kraje pokazywane domyślnie w rankingu „gdzie tankować” (Polska + korytarze tranzytowe)
TRANSIT = ["PL", "DE", "CZ", "SK", "LT", "LV", "AT", "HU", "NL", "BE", "LU", "FR", "IT", "ES", "DK", "SE"]

st.set_page_config(page_title="Ceny paliw – hurt i stacje", layout="wide")
ui.inject_css()
auth.require_password()

# ---------------------------------------------------------------- auto-odświeżanie 18:30
scheduler.start()
if scheduler.is_due():
    if scheduler.is_running():
        st.info("Trwa automatyczne odświeżanie danych – za chwilę odśwież stronę. Poniżej dane zapisane w bazie.")
    else:
        with st.spinner(f"Automatyczne odświeżanie danych (termin {scheduler.last_slot():%d.%m %H:%M})…"):
            if auto_results := scheduler.run_if_due():
                st.session_state["refresh_results"] = auto_results
                st.rerun()


@st.cache_data(ttl=900)
def load(series: str) -> pd.DataFrame:
    return db.read_series(series)


@st.cache_data(ttl=900)
def load_eu(variant: str, pln: bool) -> pd.DataFrame:
    """Tabela szeroka: indeks = data biuletynu, kolumny = kody krajów/średnich; EUR/l lub PLN/l."""
    prefix = wob.series_name(variant, "")
    df = db.read_series_like(prefix)
    if df.empty:
        return pd.DataFrame()
    df["code"] = df["series"].str[len(prefix):]
    wide = df.pivot(index="date", columns="code", values="value").sort_index()
    if pln:
        fx = db.read_series(wob.FX_SERIES).set_index("date")["value"]  # EUR za 1 PLN, z tego samego biuletynu
        wide = wide.div(fx.reindex(wide.index), axis=0).dropna(how="all")
    return wide


def source_warning(series: str) -> None:
    """Pokazuje błąd ostatniego pobrania danej serii (pozostałe sekcje działają dalej)."""
    status = db.last_fetch(series)
    if status and not status["ok"]:
        st.warning(f"Ostatnie pobranie ({status['ts']}) nieudane: {status['message']}. Pokazuję dane zapisane w bazie.")


def range_picker(key: str, default: str = "3M") -> tuple[date, date] | None:
    options = [*PRESETS, "Własny"]
    choice = st.segmented_control("Zakres", options, default=default, key=f"{key}_preset",
                                  label_visibility="collapsed") or default
    today = date.today()
    if choice == "Własny":
        picked = st.date_input(
            "Własny zakres dat", value=(today - timedelta(days=90), today), format="YYYY-MM-DD", key=f"{key}_dates"
        )
        if len(picked) != 2:
            st.info("Wybierz datę początkową i końcową.")
            return None
        return picked[0], picked[1]
    days = PRESETS[choice]
    return (date(1900, 1, 1) if days is None else today - timedelta(days=days)), today


def in_range(df: pd.DataFrame, rng: tuple[date, date]) -> pd.DataFrame:
    return df[(df["date"].dt.date >= rng[0]) & (df["date"].dt.date <= rng[1])]


def metric(df: pd.DataFrame, label: str, unit: str, decimals: int = 2, inverse: bool = True) -> None:
    """inverse: wzrost na pomarańczowo/czerwono (dla cen paliw i USD/PLN wzrost jest niekorzystny)."""
    last = df.iloc[-1]
    fmt = f"{{:,.{decimals}f}}"
    delta = None
    help_txt = "Brak poprzedniego notowania."
    if len(df) > 1:
        prev = df.iloc[-2]
        diff = last["value"] - prev["value"]
        delta = f"{fmt.format(diff)} ({diff / prev['value']:+.2%})".replace(",", " ")
        help_txt = f"Zmiana d/d względem poprzedniego notowania z {prev['date']:%Y-%m-%d}."
        if prev["source"] != last["source"]:
            help_txt += f" UWAGA: inne źródło/miara poprzedniego notowania ({prev['source']})."
    st.metric(label, f"{fmt.format(last['value'])} {unit}".replace(",", " "), delta, help=help_txt,
              delta_color="inverse" if inverse else "normal")
    st.caption(f"Notowanie z **{last['date']:%Y-%m-%d}** · źródło: {last['source']} · jednostka: {unit}")
    if len(df) > 1 and df.iloc[-2]["source"] != last["source"]:
        st.caption(f"Uwaga: zmiana d/d liczona względem innej miary: {df.iloc[-2]['source']}.")


def line_chart(df: pd.DataFrame, name: str, unit: str, color: str, tips: list[str] = ()) -> None:
    ui.legend([("line", color, f"<b>{escape(name)}</b> – kolejne notowania, {escape(unit)}")],
              [*tips, "Najedź kursorem na linię – dymek pokazuje datę, wartość i źródło notowania.", RANGE_TIP])
    fig = go.Figure(
        go.Scatter(
            x=df["date"], y=df["value"], name=name, mode="lines", line=dict(width=2, color=color),
            customdata=df["source"],
            hovertemplate="%{x|%Y-%m-%d}<br>%{y:,.2f} " + unit + "<br>%{customdata}<extra></extra>",
        )
    )
    ui.style_fig(fig, 380, yaxis_title=unit, hovermode="x unified")
    st.plotly_chart(fig, width="stretch")


def series_section(series: str, title: str, unit: str, decimals: int = 2, show_warning: bool = True,
                   tips: list[str] = ()) -> pd.DataFrame:
    """Wspólny układ: ostrzeżenie źródła, metric, selektor zakresu, wykres."""
    if show_warning:
        source_warning(series)
    df = load(series)
    if df.empty:
        st.error("Brak danych w bazie dla tej serii.")
        return df
    metric(df, title, unit, decimals)
    rng = range_picker(series)
    if rng:
        part = in_range(df, rng)
        if part.empty:
            st.info("Brak notowań w wybranym zakresie.")
        else:
            line_chart(part, title, unit, SERIES_COLORS.get(series, C["ara"]), tips)
    return df


def premium_history(ara: pd.DataFrame, orl: pd.DataFrame, fx: pd.DataFrame) -> pd.DataFrame:
    """Premia na każdy dzień notowania ARA. Orlen i USD/PLN = ostatnie notowanie z tego dnia lub wcześniej
    (Orlen obowiązuje do kolejnej zmiany). Bez notowania w oknie tolerancji punkt jest pomijany, nie uzupełniany."""
    base = ara[["date", "value", "source"]].rename(columns={"value": "ara_usd", "source": "ara_source"})
    fx_ = fx[["date", "value"]].rename(columns={"value": "fx"}).assign(fx_date=fx["date"])
    orl_ = orl[["date", "value"]].rename(columns={"value": "orlen"}).assign(orlen_date=orl["date"])
    df = pd.merge_asof(base.sort_values("date"), fx_.sort_values("date"), on="date", tolerance=pd.Timedelta(days=5))
    df = pd.merge_asof(df, orl_.sort_values("date"), on="date", tolerance=pd.Timedelta(days=7))
    df = df.dropna(subset=["fx", "orlen"])
    df["ara_pln"] = df["ara_usd"] * df["fx"] / M3_PER_T
    df["premium"] = df["orlen"] - df["ara_pln"]
    return df


ARCHIVE_MONTHS = 24
MONTHS_PL = ["sty", "lut", "mar", "kwi", "maj", "cze", "lip", "sie", "wrz", "paź", "lis", "gru"]


def monthly_averages(df: pd.DataFrame, months: int) -> pd.DataFrame:
    """Średnie miesięczne ważone dniami: cena obowiązuje od notowania do kolejnej zmiany (ffill na każdy dzień
    do dziś). Ostatnie `months` pełnych miesięcy + bieżący (partial=True)."""
    s = df.set_index("date")["value"].sort_index()
    s = s[~s.index.duplicated(keep="last")]
    today = pd.Timestamp(date.today())
    daily = s.reindex(pd.date_range(s.index.min(), max(today, s.index.max()), freq="D")).ffill()
    this_month = today.to_period("M")
    start = (this_month - months - 1).to_timestamp()  # +1 miesiąc wstecz tylko do zmiany m/m
    daily = daily[daily.index >= start]
    m = daily.groupby(daily.index.to_period("M")).agg(["mean", "min", "max"])
    m["diff"] = m["mean"].diff()
    m["pct"] = m["mean"].pct_change()
    m = m.iloc[1:]
    m["partial"] = m.index == this_month
    m["label"] = [f"{MONTHS_PL[p.month - 1]} {p.year}" for p in m.index]
    return m.reset_index(drop=True)


def premium_window(hist: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Punkty premii z ostatnich PREMIUM_WINDOW_DAYS dni i faktyczna liczba dni, które obejmują."""
    last = hist["date"].max()
    win = hist[hist["date"] >= last - pd.Timedelta(days=PREMIUM_WINDOW_DAYS)]
    return win, max(1, (last - win["date"].min()).days)


def d_d(df: pd.DataFrame, decimals: int) -> tuple[str, str]:
    """Zmiana względem poprzedniego notowania (HTML) + dopisek, gdy poprzednie ma inne źródło."""
    if len(df) < 2:
        return "", ""
    last, prev = df.iloc[-1], df.iloc[-2]
    diff = last["value"] - prev["value"]
    note = " · uwaga: d/d względem innej miary" if prev["source"] != last["source"] else ""
    return ui.delta_html(diff, diff / prev["value"], decimals), note


# ---------------------------------------------------------------- dane wspólne dla przeglądu
orlen_df = load(orlen.SERIES)
ara_df = load(ara_manual.SERIES)
fx_df = load("usdpln")
eu_now = load_eu("brutto", False)
hist = premium_history(ara_df, orlen_df, fx_df) if not (ara_df.empty or orlen_df.empty or fx_df.empty) else pd.DataFrame()

# ---------------------------------------------------------------- nagłówek
h_brand, h_fresh, h_btn, h_out = st.columns([2.2, 4.4, 1.5, 1.1], vertical_alignment="center")
h_brand.markdown(
    f'<div class="brand"><span class="chip"><img src="{login_ui.logo_src()}" alt="ID Logistics"></span>'
    '<div><b>Ceny paliw</b><span>monitoring · Polska i Europa</span></div></div>',
    unsafe_allow_html=True,
)


def fresh_chip(label: str, df_or_date, fetch_series: str) -> str:
    status = db.last_fetch(fetch_series)
    color = C["good"] if status is None or status["ok"] else C["orlen"]
    if isinstance(df_or_date, pd.DataFrame):
        when = "brak" if df_or_date.empty else f"{df_or_date['date'].max():%d.%m}"
    else:
        when = "brak" if df_or_date is None else f"{df_or_date:%d.%m}"
    title = "" if status is None or status["ok"] else f' title="Ostatnie pobranie nieudane: {escape(status["message"])}"'
    return f'<span{title}><i style="background:{color}"></i> {label} <span class="num">{when}</span></span>'


eu_last_day = eu_now["PL"].dropna().index.max() if not eu_now.empty and "PL" in eu_now else None
h_fresh.markdown(
    '<div class="fresh">Ostatnie notowania:'
    + fresh_chip("ORLEN", orlen_df, orlen.SERIES)
    + fresh_chip("ARA", ara_df, ara_manual.SERIES)
    + fresh_chip("USD/PLN", fx_df, "usdpln")
    + fresh_chip("UE", eu_last_day, wob.LOG_SERIES)
    + f'<span>· auto-odświeżanie pon–sob 18:30, następne <span class="num">{scheduler.next_slot():%d.%m %H:%M}</span></span>'
    + "</div>",
    unsafe_allow_html=True,
)
if h_btn.button("Odśwież dane", type="primary", icon=":material/refresh:", width="stretch",
                help="Uruchamia ten sam kod co `python update_data.py`. Automatycznie: pon–sob o 18:30."):
    with st.spinner("Pobieram dane…"):
        st.session_state["refresh_results"] = scheduler.run_now()
    st.rerun()
if h_out.button("Wyloguj", icon=":material/logout:", width="stretch", help="Zakończ sesję – wymaga ponownego podania hasła."):
    st.session_state.clear()
    st.rerun()

if results := st.session_state.pop("refresh_results", None):
    for series, (ok, msg) in results.items():
        if not ok:
            st.error(f"{series}: {msg}")
    with st.expander(f"Odświeżono: {sum(ok for ok, _ in results.values())} z {len(results)} źródeł OK"):
        for series, (ok, msg) in results.items():
            st.markdown(f"{'✓' if ok else '✗'} **{series}** – {msg}")

# ---------------------------------------------------------------- kafelki KPI
k1, k2, k3, k4 = st.columns(4)
with k1:
    if orlen_df.empty:
        ui.kpi_empty("Ekodiesel ORLEN · hurt", "Kliknij „Odśwież dane”.", C["orlen"])
    else:
        last = orlen_df.iloc[-1]
        delta, note = d_d(orlen_df, 0)
        ui.kpi("Ekodiesel ORLEN · hurt", ui.num(last["value"]), "PLN/m³", delta,
               ui.sparkline(orlen_df["value"].tail(30), C["orlen"]),
               f"netto, 15°C · od {last['date']:%d.%m.%Y}{note}", dot=C["orlen"])
with k2:
    if ara_df.empty:
        ui.kpi_empty("ARA · ICE LS Gasoil", "Ustaw OILPRICEAPI_KEY albo dodaj wpis ręczny w zakładce ARA.", C["ara"])
    else:
        last = ara_df.iloc[-1]
        delta, note = d_d(ara_df, 2)
        pln = (f"= {ui.num(hist.iloc[-1]['ara_pln'])} PLN/m³ · "
               if not hist.empty and hist.iloc[-1]["date"] == last["date"] else "")
        avg_src = " · średnia dzienna" if last["source"] == oilpriceapi.SOURCE_AVG else ""
        ui.kpi("ARA · ICE LS Gasoil", ui.num(last["value"], 2), "USD/t", delta,
               ui.sparkline(ara_df["value"].tail(30), C["ara"]),
               f"{pln}{last['date']:%d.%m.%Y}{avg_src}{note}", dot=C["ara"])
with k3:
    if hist.empty:
        ui.kpi_empty("Premia PL vs ARA", "Wymaga notowań ARA, ORLEN i USD/PLN.")
    else:
        win, win_days = premium_window(hist)
        now_p, avg_p = hist.iloc[-1]["premium"], win["premium"].mean()
        diff_p = now_p - avg_p
        delta = (f'<span class="delta num" style="color:{C["orlen"] if diff_p > 0 else C["down"]}">'
                 f"{ui.num(diff_p, sign=True)} vs średnia {win_days} dni</span>")
        ui.kpi("Premia PL vs ARA", ui.num(now_p), "PLN/m³", delta,
               ui.sparkline(hist["premium"].tail(30), C["text"]),
               "podatki, opłaty, logistyka i marża", accent=True)
with k4:
    if fx_df.empty:
        ui.kpi_empty("USD/PLN", "Kliknij „Odśwież dane”.")
    else:
        last = fx_df.iloc[-1]
        delta, note = d_d(fx_df, 4)
        ui.kpi("USD/PLN", ui.num(last["value"], 4), "PLN", delta,
               ui.sparkline(fx_df["value"].tail(30), C["muted"]),
               f"{last['date']:%d.%m.%Y} · tańszy dolar = tańsza ARA w PLN{note}")

st.write("")
t_over, t_orlen, t_ara, t_prem, t_eu, t_mkt, t_plan = st.tabs(
    ["Przegląd", "Hurt ORLEN", "ARA", "Premia", "Stacje UE", "Rynki", "Plan tankowania"]
)

# ================================================================ PRZEGLĄD
with t_over:
    c_chart, c_side = st.columns([2.4, 1], gap="medium")
    with c_chart, st.container(border=True):
        ui.card_title("Hurt ORLEN vs giełda ARA", "PLN/m³ netto · pole między liniami = premia PL")
        rng = range_picker("over", default="1Y")
        if orlen_df.empty:
            st.info("Brak notowań ORLEN w bazie.")
        elif rng:
            orl_part = in_range(orlen_df, rng)
            h_part = in_range(hist, rng) if not hist.empty else hist
            ui.legend([
                ("step", C["orlen"], "<b>Hurt ORLEN</b> (Ekodiesel, netto) – schodki, bo cena obowiązuje do kolejnej zmiany cennika"),
                ("line", C["ara"], "<b>Giełda ARA</b> przeliczona na PLN/m³ (USD/t × USD/PLN ÷ 1,1834)"),
                ("area", C["orlen"], "<b>Pole między liniami = premia PL</b> – o ile hurt ORLEN jest droższy od giełdy"),
            ], [
                "Pole się <b>rozszerza</b> – ORLEN drożeje względem giełdy (lub nie nadąża za jej spadkiem).",
                "Pole się <b>zwęża</b> – hurt tanieje względem giełdy.",
                "ORLEN reaguje na ARA z opóźnieniem – spadek niebieskiej linii zwykle zapowiada obniżkę w hurcie.",
                RANGE_TIP,
            ])
            fig = go.Figure()
            if not h_part.empty:
                fig.add_trace(go.Scatter(
                    x=h_part["date"], y=h_part["ara_pln"], name="ARA przeliczona (USD/t × USD/PLN ÷ 1,1834)",
                    mode="lines", line=dict(width=2, color=C["ara"]),
                    hovertemplate="ARA %{y:,.0f} PLN/m³<extra></extra>",
                ))
                fig.add_trace(go.Scatter(  # wypełnienie premii: od ARA do ORLEN w dniach z notowaniem ARA
                    x=h_part["date"], y=h_part["orlen"], mode="lines", line=dict(width=0),
                    fill="tonexty", fillcolor="rgba(255,90,78,0.12)", hoverinfo="skip", showlegend=False,
                ))
            fig.add_trace(go.Scatter(
                x=orl_part["date"], y=orl_part["value"], name="Ekodiesel ORLEN", mode="lines", line_shape="hv",
                line=dict(width=2.2, color=C["orlen"]), hovertemplate="ORLEN %{y:,.0f} PLN/m³<extra></extra>",
            ))
            ui.style_fig(fig, 400, hovermode="x unified", yaxis_title="PLN/m³",
                         legend=dict(orientation="h", y=1.08, x=0))
            st.plotly_chart(fig, width="stretch")
            if h_part.empty:
                st.caption("Brak notowań ARA w tym zakresie – widać tylko ORLEN. Historia ARA w bazie zaczyna się od pierwszego pobrania z API.")

    with c_side, st.container(border=True):
        if hist.empty:
            ui.html('<div class="signal"><div class="head"><b>Sygnał dnia</b></div>'
                    "<p>Sygnał liczony z premii PL vs ARA – potrzebne notowania ARA (klucz OilPriceAPI "
                    "lub wpis ręczny w zakładce ARA).</p></div>")
        else:
            win, win_days = premium_window(hist)
            now_p, avg_p = hist.iloc[-1]["premium"], win["premium"].mean()
            diff_p, pct_p = now_p - avg_p, now_p / avg_p - 1
            if pct_p > 0.02:
                pill = ("Premia wysoka", "#3A1D26", "#FF9A8E")
                msg = (f"Hurt ORLEN jest <b>o {ui.num(diff_p)} PLN/m³ drożej</b> względem giełdy niż średnio w ostatnich "
                       f"{win_days} dniach. Jeśli premia wróci do średniej, hurt ma przestrzeń do spadku – "
                       "rozważ mniejsze partie zamiast zakupu na zapas.")
            elif pct_p < -0.02:
                pill = ("Premia niska", "#0F3A3A", "#7FE3CC")
                msg = (f"Hurt ORLEN jest <b>o {ui.num(-diff_p)} PLN/m³ taniej</b> względem giełdy niż średnio w ostatnich "
                       f"{win_days} dniach – względnie korzystny moment na większy zakup.")
            else:
                pill = ("W normie", "#16365B", C["muted"])
                msg = (f"Premia ({ui.num(now_p)} PLN/m³) jest blisko średniej z {win_days} dni "
                       f"({ui.num(avg_p)} PLN/m³). Hurt wyceniony typowo względem giełdy.")
            ui.html(f'<div class="signal"><div class="head"><b>Sygnał dnia</b>'
                    f'<span class="pill" style="background:{pill[1]};color:{pill[2]}">{pill[0]}</span></div>'
                    f"<p>{msg}</p></div>")
            ui.legend([
                ("line", C["orlen"], f"<b>Premia PL vs ARA</b> z ostatnich {win_days} dni, PLN/m³"),
                ("dash", C["muted"], f"<b>Średnia premii</b> z tego okresu ({ui.num(avg_p)} PLN/m³)"),
            ], [
                "Linia <b>nad</b> przerywaną – hurt drogi względem giełdy, nie kupuj na zapas.",
                "Linia <b>pod</b> przerywaną – hurt tani względem giełdy, dobry moment na większy zakup.",
            ])
            fig = go.Figure(go.Scatter(
                x=win["date"], y=win["premium"], mode="lines", line=dict(width=2, color=C["orlen"]),
                hovertemplate="%{x|%Y-%m-%d}<br>%{y:,.0f} PLN/m³<extra></extra>",
            ))
            fig.add_hline(y=avg_p, line=dict(width=1, dash="dash", color=C["muted"]))
            ui.style_fig(fig, 150, showlegend=False, margin=dict(l=0, r=0, t=6, b=0),
                         xaxis=dict(showgrid=False, tickformat="%d.%m"), yaxis=dict(nticks=3))
            st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
            st.caption(f"Premia w ostatnich {win_days} dniach; przerywana = średnia {ui.num(avg_p)} PLN/m³. "
                       "Jak czytać premię – zakładka **Premia**.")

    eu_countries = [] if eu_last_day is None else [
        c for c in eu_now.loc[eu_last_day].dropna().index if c not in wob.AVERAGES
    ]
    with st.container(border=True):
        if not eu_countries or "PL" not in eu_countries:
            ui.card_title("Olej napędowy na stacjach – gdzie tankować")
            st.info("Brak danych biuletynu KE w bazie. Kliknij „Odśwież dane”.")
        else:
            now_eu = eu_now.loc[eu_last_day]
            ui.card_title("Olej napędowy na stacjach – gdzie tankować",
                          f"Weekly Oil Bulletin KE z {eu_last_day:%d.%m.%Y} · EUR/l z podatkami · od najtańszego")
            t_all, t_leg = st.columns([4, 1], vertical_alignment="center")
            show_all = t_all.toggle("Wszystkie kraje UE", key="rank_all")
            with t_leg:
                ui.legend([
                    ("bar", C["orlen"], "<b>Polska</b> – punkt odniesienia"),
                    ("bar", C["good"], "Kraj <b>tańszy</b> niż Polska"),
                    ("bar", C["neutral"], "Kraj <b>droższy</b> niż Polska"),
                ], [
                    "Kolumna „vs PL na 1000 l”: <b style='color:#4FD1B5'>−</b> = tyle zaoszczędzisz, "
                    "<b style='color:#FF8A7A'>+</b> = tyle dopłacisz względem tankowania w Polsce.",
                    "Słupki nie startują od zera – porównuj różnice między krajami, nie długości.",
                    "„Wszystkie kraje UE” – pełna lista zamiast krajów tranzytowych.",
                ])
            codes = eu_countries if show_all else [c for c in TRANSIT if c in eu_countries]
            ranked = now_eu[codes].sort_values()
            pl = now_eu["PL"]
            lo = max(0.0, (ranked.min() * 10 // 1) / 10 - 0.1)
            span = max(ranked.max() - lo, 0.01)
            rows = ['<div class="rank"><div class="r hd"><span>Kraj</span><span></span>'
                    '<span class="p">EUR/l</span><span class="d">vs PL na 1000 l</span></div>']
            for code, price in ranked.items():
                d = round((price - pl) * 1000)
                is_pl = code == "PL"
                bar = C["orlen"] if is_pl else (C["good"] if d < 0 else C["neutral"])
                d_color = C["muted"] if is_pl else (C["down"] if d < 0 else C["up"])
                d_txt = "—" if is_pl else f"{ui.num(d, sign=True)} €"
                rows.append(
                    f'<div class="r{" pl" if is_pl else ""}"><span><span class="code num">{code}</span>'
                    f"{wob.COUNTRIES.get(code, code)}</span>"
                    f'<span class="track"><s style="width:{(price - lo) / span * 100:.1f}%;background:{bar}"></s></span>'
                    f'<span class="p num">{ui.num(price, 3)}</span>'
                    f'<span class="d num" style="color:{d_color}">{d_txt}</span></div>'
                )
            ui.html("".join(rows) + "</div>")
            st.caption(f"Słupki od {ui.num(lo, 1)} EUR/l – porównuj różnice, nie długości od zera. "
                       "„vs PL” = ile więcej (+) lub mniej (−) zapłacisz za 1000 l niż w Polsce przy średniej krajowej cenie. "
                       "Oszczędność na konkretnej trasie policzysz w zakładce **Plan tankowania**.")

# ================================================================ HURT ORLEN
with t_orlen:
    st.subheader("Ekodiesel ORLEN – cena hurtowa")
    orlen_full = series_section(orlen.SERIES, "Ekodiesel ORLEN (hurt)", orlen.UNIT, 0,
                                tips=["Poziome odcinki = cena bez zmian; linia łączy kolejne zmiany cennika."])
    if not orlen_full.empty:
        st.caption(
            f"Historia z API Orlenu od {orlen_full['date'].min():%Y-%m-%d} ({len(orlen_full)} notowań). "
            "Wg Orlenu: cena bez VAT, za paliwo w temperaturze referencyjnej 15°C; akcyza, opłata paliwowa i zapasowa "
            "wymienione jako składniki kształtujące cenę hurtową. Notowanie obowiązuje do kolejnej zmiany."
        )

        st.markdown("#### Archiwum – średnie miesięczne (24 pełne miesiące + bieżący)")
        arch = monthly_averages(orlen_full, ARCHIVE_MONTHS)
        partial = arch["partial"]
        ui.legend([
            ("bar", C["orlen"], "<b>Średnia miesięczna</b> pełnego miesiąca, PLN/m³ netto"),
            ("bar-faded", C["orlen"], "<b>Bieżący miesiąc</b> – średnia do dziś, jeszcze się zmieni"),
        ], [
            "Słupki startują od zera – długość = cena, można porównywać wprost.",
            "Średnia ważona dniami: każdy dzień liczy się po cenie, która wtedy obowiązywała.",
            "Najedź na słupek – dymek pokazuje min, max i zmianę m/m.",
        ])
        fig = go.Figure(go.Bar(
            x=arch["label"], y=arch["mean"], marker=dict(color=C["orlen"], opacity=[0.45 if p else 1.0 for p in partial]),
            customdata=arch[["min", "max", "diff"]].to_numpy(),
            hovertemplate="%{x}<br>średnia %{y:,.0f} PLN/m³<br>min %{customdata[0]:,.0f} · max %{customdata[1]:,.0f}"
                          "<br>m/m %{customdata[2]:+,.0f}<extra></extra>",
        ))
        ui.style_fig(fig, 340, yaxis=dict(title="PLN/m³", rangemode="tozero"),  # słupki od zera – długość = cena
                     xaxis=dict(type="category", tickangle=-45), showlegend=False)
        st.plotly_chart(fig, width="stretch")

        table = pd.DataFrame({
            "Miesiąc": arch["label"] + arch["partial"].map({True: " (w toku)", False: ""}),
            "Średnia [PLN/m³]": arch["mean"].round(0),
            "Średnia [PLN/l]": (arch["mean"] / 1000).round(3),
            "Zmiana m/m [PLN/m³]": arch["diff"].round(0),
            "Zmiana m/m [%]": (arch["pct"] * 100).round(1),
            "Min [PLN/m³]": arch["min"].round(0),
            "Max [PLN/m³]": arch["max"].round(0),
        }).iloc[::-1]
        st.dataframe(
            ui.trend_table(table, {"Zmiana m/m [PLN/m³]": (0, ""), "Zmiana m/m [%]": (1, "")},
                           {"Średnia [PLN/m³]": "{:.0f}", "Średnia [PLN/l]": "{:.3f}",
                            "Min [PLN/m³]": "{:.0f}", "Max [PLN/m³]": "{:.0f}"}),
            hide_index=True, width="stretch", height=38 + 35 * min(len(table), 12),
        )
        st.caption(ui.TREND_NOTE)
        st.download_button(
            "Pobierz archiwum (CSV do Excela)", icon=":material/download:",
            data=table.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig"),
            file_name=f"orlen_ekodiesel_srednie_miesieczne_{date.today():%Y-%m-%d}.csv", mime="text/csv",
        )
        st.caption(
            "Średnia ważona dniami: notowanie obowiązuje do kolejnej zmiany, więc każdy dzień miesiąca liczy się "
            "po cenie, która w nim obowiązywała (nie średnia z samych zmian cennika). Ceny netto (bez VAT), PLN/m³ w 15°C. "
            "Bieżący miesiąc – do dziś, jaśniejszy słupek."
        )

# ================================================================ ARA
with t_ara:
    st.subheader("ARA – ICE Low Sulphur Gasoil")
    st.caption(
        "Automatycznie z OilPriceAPI (kod GASOIL_USD, ICE LS Gasoil), **USD/t**, przez `update_data.py`: "
        "ostatnia transakcja dnia (uruchomienie po zamknięciu ICE ≈ cena zamknięcia; wcześniej wartość śródsesyjna). "
        "Notowania ze źródłem „średnia dzienna” pochodzą z jednorazowego importu historii – to nie są ceny zamknięcia. "
        "Źródło każdego punktu widać w dymku wykresu."
    )
    last_h = None if hist.empty else hist.iloc[-1]
    a_usd = "—" if last_h is None else ui.num(last_h["ara_usd"], 2)
    a_fx = "—" if last_h is None else ui.num(last_h["fx"], 4)
    a_pln = "—" if last_h is None else ui.num(last_h["ara_pln"])
    a_day = "" if last_h is None else f" · {last_h['date']:%d.%m.%Y}"
    ui.html(f"""
    <div class="formula">
      <div class="box"><small>ICE LS Gasoil (ARA){a_day}</small><b class="num">{a_usd}</b><span class="u">USD/t</span></div>
      <div class="op">×</div>
      <div class="box"><small>Kurs USD/PLN</small><b class="num">{a_fx}</b><span class="u">PLN</span></div>
      <div class="op">÷</div>
      <div class="box"><small>m³ w tonie ON (0,845 kg/l)</small><b class="num">1,1834</b><span class="u">m³/t</span></div>
      <div class="op">=</div>
      <div class="box res"><small>ARA w PLN – do porównania z ORLEN</small><b class="num">{a_pln}</b><span class="u">PLN/m³</span></div>
    </div>""")
    with st.expander("Czym jest ARA i jak to czytać"):
        ui.html("""
    <div class="explain">
      <div>
        <h4>Co to jest</h4>
        <p><b>ARA</b> to porty Amsterdam–Rotterdam–Antwerpia – największy w Europie Zachodniej węzeł
        rafinerii, terminali i handlu paliwami.</p>
        <p><b>ICE Low Sulphur Gasoil</b> to kontrakt na olej napędowy o niskiej zawartości siarki
        z dostawą w ARA, notowany na giełdzie ICE w Londynie, w <b>USD za tonę</b>. To punkt odniesienia
        dla hurtowych cen diesla w Europie.</p>
      </div>
      <div>
        <h4>Jak czytać</h4>
        <ul>
          <li><b>ARA rośnie</b> – rośnie koszt paliwa na rynku; hurt ORLEN zwykle idzie w górę z opóźnieniem.</li>
          <li><b>ARA spada</b> – jest przestrzeń do obniżek w hurcie.</li>
          <li>Liczy się też <b>kurs dolara</b>: tańszy dolar obniża ARA w PLN nawet przy tej samej cenie w USD.</li>
          <li>Czy ORLEN jest drogi względem ARA – pokazuje zakładka <b>Premia</b>.</li>
        </ul>
      </div>
      <div>
        <h4>Na co uważać</h4>
        <ul>
          <li>To <b>nie jest cena zakupu</b> dla nas – to cena giełdowa, bez akcyzy, opłat, logistyki i marży.</li>
          <li>Cena zmienia się w trakcie sesji; aplikacja zapisuje <b>ostatnią cenę dnia</b>, pobieraną
          po zamknięciu giełdy (ok. 18:30 czasu polskiego).</li>
          <li>Pierwsze ok. 30 dni historii to <b>średnie dzienne</b> z importu, nie ceny zamknięcia.</li>
        </ul>
      </div>
    </div>""")
    source_warning(ara_manual.SERIES)
    with st.expander("Wpis ręczny (awaryjnie, gdy API nie działa)"), st.form("ara_form", clear_on_submit=True):
        st.caption(f"Cena z [TradingView ICEEUR:ULS1!]({ara_manual.URL}). Kolejne pobranie z API nadpisze wpis z tego samego dnia.")
        c1, c2, c3 = st.columns([2, 2, 1], vertical_alignment="bottom")
        ara_day = c1.date_input("Data notowania", value=date.today(), format="YYYY-MM-DD")
        ara_price = c2.number_input("Cena zamknięcia / ostatnia [USD/t]", min_value=0.0, step=0.25, format="%.2f")
        if c3.form_submit_button("Zapisz"):
            try:
                ara_manual.save(ara_day, ara_price)
                st.cache_data.clear()
                st.success(f"Zapisano {ara_day}: {ara_price:.2f} USD/t")
            except ValueError as e:
                st.error(str(e))

    if ara_df.empty:
        st.error("Brak notowań ARA w bazie. Ustaw OILPRICEAPI_KEY i kliknij „Odśwież dane” albo użyj wpisu ręcznego.")
    else:
        series_section(ara_manual.SERIES, "ICE LS Gasoil (ARA)", ara_manual.UNIT, show_warning=False,
                       tips=["Cena giełdowa w USD/t – bez przeliczenia na PLN i bez podatków.",
                             "Źródło „średnia dzienna” w dymku = import historii, nie cena zamknięcia."])
        # od kiedy historia jest kompletna (dni robocze pon–pt bez luk; święta ICE liczone jako luki)
        have = set(ara_df["date"].dt.date)
        weekdays = pd.bdate_range(ara_df["date"].min(), ara_df["date"].max()).date
        missing = [d for d in weekdays if d not in have]
        complete_from = ara_df["date"].min().date() if not missing else next(
            (d for d in weekdays if d > missing[-1]), ara_df["date"].max().date()
        )
        st.caption(
            f"Historia w bazie od {ara_df['date'].min():%Y-%m-%d} ({len(ara_df)} notowań; import historii z API obejmuje ~30 dni, dalej codzienne snapshoty). "
            f"Historia kompletna (pon–pt) od **{complete_from}**. Brakujące dni robocze: {len(missing)}"
            + (f" (ostatni: {missing[-1]})" if missing else "")
            + ". Dni świąteczne ICE są liczone jako braki."
        )

# ================================================================ PREMIA PL vs ARA
with t_prem:
    st.subheader("Premia PL vs ARA – czym jest i jak ją czytać")
    last_h = None if hist.empty else hist.iloc[-1]
    f_orl = "—" if last_h is None else ui.num(last_h["orlen"])
    f_ara = "—" if last_h is None else ui.num(last_h["ara_pln"])
    f_prem = "—" if last_h is None else ui.num(last_h["premium"])
    f_day = "" if last_h is None else f" · {last_h['date']:%d.%m.%Y}"
    ui.html(f"""
    <div class="formula">
      <div class="box"><small>Hurt ORLEN (Ekodiesel, netto){f_day}</small><b class="num">{f_orl}</b><span class="u">PLN/m³</span></div>
      <div class="op">−</div>
      <div class="box"><small>Giełda ARA przeliczona na PLN/m³</small><b class="num">{f_ara}</b><span class="u">PLN/m³</span></div>
      <div class="op">=</div>
      <div class="box res"><small>Premia PL vs ARA</small><b class="num">{f_prem}</b><span class="u">PLN/m³</span></div>
    </div>""")
    with st.expander("Czym jest premia i jak ją czytać"):
        ui.html(f"""
    <div class="explain">
      <div>
        <h4>Co to jest</h4>
        <p>Ile więcej kosztuje 1 m³ oleju napędowego w hurcie ORLEN niż ten sam m³ na giełdzie ARA
        (Amsterdam–Rotterdam–Antwerpia) – europejskim punkcie odniesienia dla cen diesla.</p>
        <p>ARA notowana jest w USD za tonę, więc przeliczamy ją na PLN/m³:
        <span class="num">USD/t × kurs USD/PLN ÷ 1,1834</span> (1 t ON ≈ 1 183 l).</p>
      </div>
      <div>
        <h4>Jak czytać</h4>
        <p>Nie patrz na samą wysokość premii, tylko na to, jak ma się do <b>swojej średniej</b>
        (przerywana linia na wykresie):</p>
        <div class="scale">
          <div><i style="background:{C['up']}"></i><span><b>Powyżej średniej</b> – hurt ORLEN drogi względem rynku. Jest przestrzeń do obniżki; nie kupuj dużo na zapas.</span></div>
          <div><i style="background:{C['muted']}"></i><span><b>Blisko średniej</b> (±2%) – wycena typowa.</span></div>
          <div><i style="background:{C['down']}"></i><span><b>Poniżej średniej</b> – hurt tani względem rynku; względnie korzystny moment na większy zakup.</span></div>
        </div>
      </div>
      <div>
        <h4>Na co uważać</h4>
        <ul>
          <li>Premia to <b>nie jest marża ORLEN-u</b>. Duża część to podatki i opłaty zawarte w cenie hurtowej:
          akcyza, opłata paliwowa i zapasowa. Do tego logistyka i marża. VAT nie jest wliczony.</li>
          <li>Przy zmianie akcyzy lub opłaty paliwowej (zwykle od 1 stycznia) premia przesuwa się skokowo –
          porównuj okresy po tej samej stronie zmiany.</li>
          <li>ORLEN reaguje na giełdę z opóźnieniem, więc skok premii często oznacza, że ORLEN „jeszcze nie nadążył” za rynkiem.</li>
        </ul>
      </div>
    </div>""")
    with st.expander("Słowniczek pojęć"):
        st.markdown(
            "- **Ekodiesel ORLEN (hurt)** – cena hurtowa oleju napędowego publikowana przez ORLEN, w PLN za m³, "
            "bez VAT, dla paliwa w temperaturze 15°C. Obowiązuje do kolejnej zmiany cennika.\n"
            "- **ARA** – rejon portów Amsterdam–Rotterdam–Antwerpia, główny hub paliwowy Europy Zachodniej.\n"
            "- **ICE Low Sulphur Gasoil** – notowany na giełdzie ICE kontrakt na olej napędowy z dostawą w ARA, "
            "w USD za tonę. To ta „giełda” na wykresie.\n"
            "- **Przeliczenie t → m³** – przy gęstości 0,845 kg/l tona ON to ok. 1 183 l, czyli 1,1834 m³.\n"
            "- **Średnia** – na wykresie poniżej: średnia premii w wybranym zakresie; w „Sygnale dnia” na Przeglądzie: "
            "średnia z ostatnich 90 dni.\n"
            "- **Linia kropkowana** – punkty, w których ARA to średnia dzienna z importu historii, a nie cena "
            "zamknięcia; porównuj je ostrożnie."
        )
    st.write("")
    if hist.empty:
        st.info("Premia niedostępna: brak notowań ARA, Orlen lub USD/PLN.")
    else:
        ara_last = ara_df.iloc[-1]
        d = ara_last["date"]
        fx_row = fx_df[fx_df["date"] <= d].iloc[-1] if (fx_df["date"] <= d).any() else None
        orl_row = orlen_df[orlen_df["date"] <= d].iloc[-1] if (orlen_df["date"] <= d).any() else None
        if fx_row is None or orl_row is None:
            st.info(f"Brak notowania USD/PLN lub Orlen na dzień {d:%Y-%m-%d} lub wcześniej.")
        else:
            st.caption(
                "Wyliczenie powyżej: "
                f"ARA {ara_last['value']:.2f} USD/t ({d:%Y-%m-%d}) × USD/PLN {fx_row['value']:.4f} ({fx_row['date']:%Y-%m-%d}) "
                f"÷ {M3_PER_T} m³/t (gęstość 0,845 kg/l). Orlen z {orl_row['date']:%Y-%m-%d}. "
                "Różnica obejmuje m.in. podatki i opłaty (akcyza, opłata paliwowa, zapasowa), logistykę i marżę – "
                "to nie jest czysta marża rafinerii."
            )

        st.markdown("#### Historia premii")
        rng = range_picker("premium")
        part = in_range(hist, rng) if rng else hist.iloc[0:0]
        if rng and part.empty:
            st.info("Brak punktów premii w wybranym zakresie.")
        elif not part.empty:
            avg = part["premium"].mean()
            now = part.iloc[-1]
            c1, c2, c3 = st.columns(3)
            c1.metric(f"Premia {now['date']:%Y-%m-%d}", f"{now['premium']:+,.0f} PLN/m³".replace(",", " "))
            c2.metric("Średnia w zakresie", f"{avg:+,.0f} PLN/m³".replace(",", " "))
            c3.metric("Bieżąca vs średnia", f"{now['premium'] - avg:+,.0f} PLN/m³".replace(",", " "))

            ui.legend([
                ("line-markers", C["orlen"], "<b>Premia</b> w dniu notowania ARA (ARA = ostatnia cena dnia / wpis ręczny)"),
                ("dot", C["orlen"], "<b>Kropkowana</b> – ARA to średnia dzienna z importu historii; porównuj ostrożnie"),
                ("dash", C["muted"], "<b>Średnia premii</b> w wybranym zakresie"),
            ], [
                f"<b style='color:{C['up']}'>Nad średnią</b> – hurt ORLEN drogi względem rynku, jest przestrzeń do obniżki.",
                f"<b style='color:{C['down']}'>Pod średnią</b> – hurt tani względem rynku, korzystny moment na zakup.",
                "Skok premii 1 stycznia to zwykle zmiana akcyzy/opłaty paliwowej, nie zmiana rynku.",
                "Najedź na punkt – dymek pokazuje ORLEN, ARA w PLN i kurs USD/PLN.",
                RANGE_TIP,
            ])
            fig = go.Figure()
            is_avg = part["ara_source"] == oilpriceapi.SOURCE_AVG
            for mask, label, dash in [(is_avg, "ARA = średnia dzienna (import historii)", "dot"),
                                      (~is_avg, "ARA = ostatnia cena dnia / wpis ręczny", "solid")]:
                seg = part[mask]
                if seg.empty:
                    continue
                fig.add_trace(go.Scatter(
                    x=seg["date"], y=seg["premium"], name=label, mode="lines+markers",
                    line=dict(width=2, color=C["orlen"], dash=dash), marker=dict(size=7),
                    customdata=seg[["orlen", "ara_pln", "ara_usd", "fx"]].to_numpy(),
                    hovertemplate="%{x|%Y-%m-%d}<br>Premia %{y:+,.0f} PLN/m³<br>Orlen %{customdata[0]:,.0f} − "
                                  "ARA %{customdata[1]:,.0f} PLN/m³<br>(ARA %{customdata[2]:,.2f} USD/t × USD/PLN "
                                  "%{customdata[3]:.4f})<extra>" + label + "</extra>",
                ))
            fig.add_hline(y=avg, line=dict(width=1, dash="dash", color=C["muted"]),
                          annotation_text=f"średnia {avg:,.0f}".replace(",", " "), annotation_position="top left",
                          annotation_font_color=C["muted"])
            ui.style_fig(fig, 380, yaxis_title="Premia [PLN/m³]", hovermode="closest",
                         legend=dict(orientation="h", y=1.1))
            st.plotly_chart(fig, width="stretch")
            n_avg = int(is_avg.sum())
            st.caption(
                f"{len(part)} punktów w zakresie"
                + (f", w tym {n_avg} liczonych ze średniej dziennej ARA (linia kropkowana) – porównuj je ostrożnie" if n_avg else "")
                + ". Punkt tylko w dni z notowaniem ARA; Orlen i USD/PLN z tego dnia lub ostatniego wcześniejszego notowania."
            )

# ================================================================ STACJE UE
with t_eu:
    st.subheader("Olej napędowy na stacjach w krajach UE")
    st.caption(
        "Źródło: [Weekly Oil Bulletin Komisji Europejskiej](" + wob.PAGE_URL + ") – oficjalne średnie krajowe ceny "
        "detaliczne, notowanie tygodniowe (poniedziałek), publikacja zwykle w czwartek. Te same dane pokazuje m.in. "
        "e-petrol.pl (strona blokuje automatyczne pobieranie, więc czytamy je u źródła). Biuletyn obejmuje tylko UE-27 – "
        "bez Norwegii, Szwajcarii, Ukrainy itp."
    )
    source_warning(wob.LOG_SERIES)
    c1, c2 = st.columns(2)
    eu_variant = c1.segmented_control(
        "Cena", list(wob.VARIANTS), default="brutto", key="eu_variant",
        format_func=lambda v: wob.VARIANTS[v][2][0].upper() + wob.VARIANTS[v][2][1:],
    ) or "brutto"
    eu_pln = (c2.segmented_control("Jednostka", ["EUR/l", "PLN/l"], default="EUR/l", key="eu_unit") or "EUR/l") == "PLN/l"
    eu_unit = "PLN/l" if eu_pln else "EUR/l"
    eu_dec = 2 if eu_pln else 3
    eu = load_eu(eu_variant, eu_pln)

    # ---- objaśnienie: z czego składa się cena na stacji (brutto = netto + podatki), liczby z biuletynu
    eu_b, eu_n = load_eu("brutto", eu_pln), load_eu("netto", eu_pln)
    both_days = (eu_b.index.intersection(eu_n.index) if not (eu_b.empty or eu_n.empty) else pd.DatetimeIndex([]))
    both_days = [d for d in both_days if pd.notna(eu_b.at[d, "PL"]) and pd.notna(eu_n.at[d, "PL"])] \
        if "PL" in eu_b and "PL" in eu_n else []
    tax_day = max(both_days) if both_days else None
    if tax_day is not None:
        pl_b, pl_n = eu_b.at[tax_day, "PL"], eu_n.at[tax_day, "PL"]
        f_b, f_n, f_t = ui.num(pl_b, eu_dec), ui.num(pl_n, eu_dec), ui.num(pl_b - pl_n, eu_dec)
        f_share, f_day = f"{ui.num((pl_b - pl_n) / pl_b * 100)}% ceny", f" · {tax_day:%d.%m.%Y}"
    else:
        f_b = f_n = f_t = "—"
        f_share = f_day = ""
    ui.html(f"""
    <div class="formula">
      <div class="box res"><small>Polska: cena na stacji (z podatkami){f_day}</small><b class="num">{f_b}</b><span class="u">{eu_unit}</span></div>
      <div class="op">=</div>
      <div class="box"><small>Cena bez podatków (paliwo, logistyka, marża)</small><b class="num">{f_n}</b><span class="u">{eu_unit}</span></div>
      <div class="op">+</div>
      <div class="box"><small>Podatki i opłaty: akcyza, opłaty, VAT</small><b class="num">{f_t}</b><span class="u">{eu_unit}</span>
        <span class="u">{f_share}</span></div>
    </div>""")
    with st.expander("Skąd są te dane i co znaczy „z podatkami”"):
        ui.html("""
    <div class="explain">
      <div>
        <h4>Skąd są te dane</h4>
        <p>Z <b>Weekly Oil Bulletin</b> Komisji Europejskiej. Każdy kraj UE co tydzień raportuje
        <b>średnią krajową</b> cenę oleju napędowego na stacjach – stan na poniedziałek, publikacja zwykle w czwartek.</p>
        <p>Ceny podawane są w EUR; wersję w PLN przeliczamy kursem z tego samego biuletynu.
        To nie są ceny z konkretnej stacji ani z karty flotowej.</p>
      </div>
      <div>
        <h4>„Z podatkami” i „bez podatków”</h4>
        <ul>
          <li><b>Z podatkami</b> – cena z pylonu, którą płaci kierowca: paliwo + akcyza i inne opłaty + VAT.</li>
          <li><b>Bez podatków</b> – sama cena paliwa: koszt produktu z rynku (np. ARA), transport,
          magazynowanie i marża stacji. Bez akcyzy, opłat i VAT.</li>
        </ul>
        <p>Przełącznik „Cena” poniżej zmienia wariant na wszystkich wykresach.</p>
      </div>
      <div>
        <h4>Dlaczego kraje tak się różnią</h4>
        <ul>
          <li><b>Podatki</b> – każdy kraj ma własną akcyzę (UE wyznacza tylko minimum) i własną stawkę VAT;
          część krajów dolicza opłaty za emisję CO₂.</li>
          <li><b>Samo paliwo</b> – koszt dostaw i logistyki (odległość od rafinerii i portów), konkurencja
          na rynku i marże stacji.</li>
          <li>Co waży więcej w danym tygodniu – pokazuje wykres „paliwo vs podatki” niżej.</li>
        </ul>
      </div>
    </div>""")
    st.write("")

    if eu.empty or "PL" not in eu:
        st.error("Brak danych biuletynu KE w bazie. Kliknij „Odśwież dane” (pierwsze pobranie ~4 MB, kilka sekund).")
    else:
        last_day = eu["PL"].dropna().index.max()
        prev_day = eu.index[eu.index < last_day].max()
        now, prev = eu.loc[last_day], eu.loc[prev_day] if pd.notna(prev_day) else None
        countries = [c for c in now.dropna().index if c not in wob.AVERAGES]
        ranked = now[countries].sort_values()
        pl, eu_avg = now["PL"], now.get("EU")
        fmt = f"{{:,.{eu_dec}f}}"

        m1, m2, m3, m4 = st.columns(4)
        m1.metric(
            f"Polska {last_day:%Y-%m-%d}", f"{fmt.format(pl)} {eu_unit}",
            None if prev is None else f"{fmt.format(pl - prev['PL'])} t/t", delta_color="inverse",
        )
        if pd.notna(eu_avg):
            m2.metric("Średnia UE-27 (ważona)", f"{fmt.format(eu_avg)} {eu_unit}")
            m3.metric("Polska vs średnia UE", f"{pl - eu_avg:+,.{eu_dec}f} {eu_unit}", f"{pl / eu_avg - 1:+.1%}",
                      delta_color="inverse")
        m4.metric("Pozycja Polski", f"{list(ranked.index).index('PL') + 1}. z {len(ranked)}", help="1 = najtańszy kraj")
        st.caption(f"Biuletyn z **{last_day:%Y-%m-%d}** · {wob.VARIANTS[eu_variant][2]}"
                   + (" · przeliczenie kursem EUR/PLN z tego samego biuletynu" if eu_pln else ""))

        names = [wob.COUNTRIES.get(c, c) for c in ranked.index]
        ui.legend([
            ("marker-lg", C["orlen"], "<b>Polska</b>"),
            ("marker", "#5B7899", "Pozostałe kraje UE – średnia krajowa cena na stacjach"),
            ("vline", C["muted"], "<b>Średnia UE-27</b> (ważona)"),
        ], [
            "Im <b>bardziej w lewo</b>, tym taniej. Kraje posortowane od najtańszego.",
            "Oś nie zaczyna się od zera – porównuj odległości między punktami.",
            "Najedź na punkt – dymek pokazuje zmianę t/t i różnicę do Polski.",
            "Przełączniki „Cena” i „Jednostka” wyżej zmieniają wariant (z/bez podatków, EUR/PLN).",
        ])
        change = (ranked - prev[ranked.index]) if prev is not None else ranked * float("nan")
        # wykres punktowy, nie słupkowy: oś X nie startuje od zera, więc długość słupka by przekłamywała
        fig = go.Figure(go.Scatter(
            x=ranked.values, y=names, mode="markers",
            marker=dict(size=[14 if c == "PL" else 10 for c in ranked.index],
                        color=[C["orlen"] if c == "PL" else "#5B7899" for c in ranked.index]),
            customdata=pd.DataFrame({"chg": change.values, "vs_pl": (ranked - pl).values}).to_numpy(),
            hovertemplate="<b>%{y}</b><br>%{x:." + str(eu_dec) + "f} " + eu_unit
                          + "<br>t/t %{customdata[0]:+." + str(eu_dec) + "f}"
                          + "<br>vs Polska %{customdata[1]:+." + str(eu_dec) + "f}<extra></extra>",
        ))
        if pd.notna(eu_avg):
            fig.add_vline(x=eu_avg, line=dict(width=1, dash="dash", color=C["muted"]),
                          annotation_text=f"średnia UE {fmt.format(eu_avg)}", annotation_position="top",
                          annotation_font_color=C["muted"])
        ui.style_fig(
            fig, max(420, 22 * len(ranked)), margin=dict(l=10, r=10, t=30, b=10), hovermode="closest",
            xaxis=dict(title=eu_unit), yaxis=dict(autorange="reversed", showgrid=True),
        )
        st.plotly_chart(fig, width="stretch")
        st.caption("Posortowane od najtańszego; Polska wyróżniona. Oś X nie zaczyna się od zera – porównuj odległości między punktami.")

        with st.expander("Tabela – wszystkie kraje"):
            table = pd.DataFrame({
                "Kraj": names,
                f"Cena [{eu_unit}]": ranked.round(eu_dec).values,
                f"Zmiana t/t [{eu_unit}]": change.round(eu_dec).values,
                f"vs Polska [{eu_unit}]": (ranked - pl).round(eu_dec).values,
                f"vs Polska na 1000 l [{eu_unit[:3]}]": ((ranked - pl) * 1000).round(0).values,
            })
            st.dataframe(ui.trend_table(
                table,
                {f"Zmiana t/t [{eu_unit}]": (eu_dec, ""), f"vs Polska [{eu_unit}]": (eu_dec, ""),
                 f"vs Polska na 1000 l [{eu_unit[:3]}]": (0, "")},
                {f"Cena [{eu_unit}]": f"{{:.{eu_dec}f}}"},
            ), hide_index=True, width="stretch")
            st.caption(ui.TREND_NOTE + " „vs Polska na 1000 l” – ile więcej (+) lub mniej (−) zapłacisz za 1000 l w danym kraju niż w Polsce "
                       "przy średniej krajowej cenie.")

        if tax_day is not None:
            st.markdown("#### Z czego składa się cena – paliwo vs podatki")
            codes_t = [c for c in eu_b.loc[tax_day].dropna().index
                       if c not in wob.AVERAGES and c in eu_n.columns and pd.notna(eu_n.at[tax_day, c])]
            b_t = eu_b.loc[tax_day, codes_t].sort_values()
            n_t = eu_n.loc[tax_day, b_t.index]
            tax_t = b_t - n_t
            names_t = [wob.COUNTRIES.get(c, c) for c in b_t.index]
            op = [1.0 if c == "PL" else 0.6 for c in b_t.index]
            share = (tax_t / b_t * 100).to_numpy()
            ui.legend([
                ("bar", C["ara"], "<b>Paliwo bez podatków</b> – produkt, logistyka, marża stacji"),
                ("bar", C["orlen"], "<b>Podatki i opłaty</b> – akcyza, opłaty, VAT"),
                ("bar-faded", C["neutral"], "Inne kraje przygaszone, <b>Polska</b> w pełnym kolorze"),
            ], [
                "Cały słupek = cena na stacji z podatkami; oś od zera, długości można porównywać.",
                "Długi czerwony odcinek = drogo przez podatki; długi niebieski = drogie samo paliwo.",
                "Najedź na wiersz – dymek pokazuje udział podatków w cenie.",
            ])
            fig = go.Figure([
                go.Bar(y=names_t, x=n_t.values, name="Paliwo bez podatków", orientation="h",
                       marker=dict(color=C["ara"], opacity=op),
                       hovertemplate="%{y}<br>bez podatków %{x:." + str(eu_dec) + "f} " + eu_unit + "<extra></extra>"),
                go.Bar(y=names_t, x=tax_t.values, name="Podatki i opłaty (akcyza, opłaty, VAT)", orientation="h",
                       marker=dict(color=C["orlen"], opacity=op), customdata=share,
                       hovertemplate="%{y}<br>podatki %{x:." + str(eu_dec) + "f} " + eu_unit
                                     + " (%{customdata:.0f}% ceny)<extra></extra>"),
            ])
            ui.style_fig(fig, max(460, 24 * len(b_t)), barmode="stack", hovermode="y unified",
                         xaxis=dict(title=f"{eu_unit} z podatkami"), yaxis=dict(autorange="reversed"),
                         legend=dict(orientation="h", y=1.04, x=0), margin=dict(l=10, r=10, t=40, b=10))
            st.plotly_chart(fig, width="stretch")
            hi, lo_ = tax_t.idxmax(), tax_t.idxmin()
            st.caption(
                f"Biuletyn z {tax_day:%d.%m.%Y}, od najtańszego. Oś od zera, więc długości słupków można porównywać. "
                f"Najwięcej podatków: {wob.COUNTRIES.get(hi, hi)} ({ui.num(tax_t[hi], eu_dec)} {eu_unit}), "
                f"najmniej: {wob.COUNTRIES.get(lo_, lo_)} ({ui.num(tax_t[lo_], eu_dec)} {eu_unit}). "
                f"Rozpiętość cen bez podatków: {ui.num(n_t.max() - n_t.min(), eu_dec)} {eu_unit}, "
                f"podatków: {ui.num(tax_t.max() - tax_t.min(), eu_dec)} {eu_unit} – w tym tygodniu kraje bardziej różnią się "
                + ("ceną samego paliwa niż podatkami" if (n_t.max() - n_t.min()) > (tax_t.max() - tax_t.min())
                   else "podatkami niż ceną samego paliwa")
                + f". Udział podatków w Polsce: {ui.num(tax_t['PL'] / b_t['PL'] * 100)}%. Polska wyróżniona."
            )

        st.markdown("#### Historia cen w wybranych krajach")
        options = [c for c in wob.COUNTRIES if c in eu.columns]
        default = [c for c in ["PL", "DE", "CZ", "SK", "LT", "EU"] if c in options]
        picked = st.multiselect("Kraje (maks. 8)", options, default=default, max_selections=8,
                                format_func=lambda c: wob.COUNTRIES.get(c, c), key="eu_countries")
        rng = range_picker("eu")
        if picked and rng:
            part = eu[(eu.index.date >= rng[0]) & (eu.index.date <= rng[1])]
            if part.empty:
                st.info("Brak notowań w wybranym zakresie.")
            else:
                ui.legend([
                    ("line", CATEGORICAL[0], "Linia ciągła = <b>kraj</b>; kolory w kolejności wyboru na liście "
                                             "(nazwy w legendzie nad wykresem)"),
                    ("dash", C["muted"], "Linia przerywana = <b>średnia UE-27 / strefy euro</b>"),
                ], [
                    "Notowania tygodniowe (poniedziałek) – linia łączy kolejne tygodnie.",
                    "Klik w nazwę kraju w legendzie nad wykresem ukrywa/pokazuje jego linię.",
                    RANGE_TIP,
                ])
                fig = go.Figure()
                for i, code in enumerate(picked):
                    col = part[code].dropna()
                    if col.empty:
                        continue
                    name = wob.COUNTRIES.get(code, code)
                    fig.add_trace(go.Scatter(
                        x=col.index, y=col.values, name=name, mode="lines",
                        line=dict(width=2, color=CATEGORICAL[i], dash="dash" if code in wob.AVERAGES else "solid"),
                        hovertemplate="%{y:." + str(eu_dec) + "f} " + eu_unit + "<extra>" + name + "</extra>",
                    ))
                ui.style_fig(fig, 420, yaxis_title=eu_unit, hovermode="x unified",
                             legend=dict(orientation="h", y=1.1))
                st.plotly_chart(fig, width="stretch")
                st.caption("Notowania tygodniowe od 2005 r. Średnie UE i strefy euro linią przerywaną.")

# ================================================================ RYNKI: złoto / srebro / USD/PLN
with t_mkt:
    st.subheader("Złoto, srebro, USD/PLN")
    frames = {}
    cols = st.columns(3)
    for col, series in zip(cols, yahoo.TICKERS):
        _, unit, label = yahoo.TICKERS[series]
        with col:
            source_warning(series)
            df = load(series)
            if df.empty:
                st.error(f"{label}: brak danych w bazie.")
                continue
            metric(df, label, unit, 4 if series == "usdpln" else 2, inverse=series == "usdpln")
            frames[series] = df

    rng = range_picker("cmp")
    if frames and rng:
        fig = go.Figure()
        rows = []
        for series, df in frames.items():
            part = in_range(df, rng)
            if part.empty:
                continue
            _, unit, label = yahoo.TICKERS[series]
            first, last = part.iloc[0], part.iloc[-1]
            fig.add_trace(
                go.Scatter(
                    x=part["date"], y=part["value"] / first["value"] * 100, name=label, mode="lines",
                    line=dict(width=2, color=SERIES_COLORS[series]), customdata=part["value"],
                    hovertemplate="%{y:.2f} (" + "%{customdata:,.4f} " + unit + ")<extra>" + label + "</extra>",
                )
            )
            rows.append({
                "Seria": label,
                "Jednostka": unit,
                "Start": f"{first['value']:,.4f} ({first['date']:%Y-%m-%d})",
                "Koniec": f"{last['value']:,.4f} ({last['date']:%Y-%m-%d})",
                "Zmiana": (last["value"] / first["value"] - 1) * 100,
                "Min": f"{part['value'].min():,.4f}",
                "Max": f"{part['value'].max():,.4f}",
                "Rozpiętość max/min": f"{part['value'].max() / part['value'].min() - 1:.2%}",
            })
        if rows:
            ui.legend([
                ("line", SERIES_COLORS["gold"], "<b>Złoto</b> (USD/oz)"),
                ("line", SERIES_COLORS["silver"], "<b>Srebro</b> (USD/oz)"),
                ("line", SERIES_COLORS["usdpln"], "<b>USD/PLN</b> – kurs dolara"),
                ("dot", C["muted"], "<b>100</b> = wartość na początku zakresu"),
            ], [
                "Wszystkie serie sprowadzone do 100 – porównujesz zmianę %, nie cenę (110 = +10%, 95 = −5%).",
                "USD/PLN nad 100 = dolar podrożał, więc giełdowy diesel (ARA) w PLN też drożeje.",
                "Dymek pokazuje wartość w oryginalnej jednostce.",
                RANGE_TIP,
            ])
            fig.add_hline(y=100, line=dict(width=1, dash="dot", color=C["muted"]))
            ui.style_fig(fig, 420, hovermode="x unified", yaxis_title="Indeks (początek zakresu = 100)",
                         legend=dict(orientation="h", y=1.08))
            st.plotly_chart(fig, width="stretch")
            st.caption("Każda seria znormalizowana do 100 na swoim pierwszym notowaniu w zakresie. W dymku wartość w oryginalnej jednostce.")
            # złoto/srebro: wzrost = korzystny (turkus); USD/PLN: wzrost = droższy dolar i ARA (czerwony)
            st.dataframe(ui.trend_table(pd.DataFrame(rows), {"Zmiana": (2, "%")},
                                        good_up=[r["Seria"] != yahoo.TICKERS["usdpln"][2] for r in rows]),
                         hide_index=True, width="stretch")
            st.caption("▲ wzrost · ▼ spadek w zakresie. Złoto i srebro: wzrost na turkusowo; "
                       "USD/PLN: wzrost na czerwono (droższy dolar = droższa ARA w PLN).")
        else:
            st.info("Brak notowań w wybranym zakresie.")

st.divider()
st.caption(f"Autor aplikacji: **{login_ui.AUTHOR}** · ID Logistics – narzędzie wewnętrzne")

# ================================================================ PLAN TANKOWANIA
with t_plan:
    st.subheader("Plan tankowania na trasie")
    st.caption("Ile zaoszczędzisz, tankując w najtańszym kraju na trasie. Ceny: średnie krajowe z podatkami "
               + (f"z biuletynu KE z {eu_last_day:%d.%m.%Y}." if eu_last_day is not None else "z biuletynu KE."))
    if not eu_countries or "PL" not in eu_countries:
        st.info("Potrzebne ceny z biuletynu KE. Kliknij „Odśwież dane”.")
    else:
        now_eu = eu_now.loc[eu_last_day]
        opts = sorted(eu_countries, key=lambda c: wob.COUNTRIES.get(c, c))
        fmt_c = lambda c: f"{wob.COUNTRIES.get(c, c)} ({ui.num(now_eu[c], 3)})"  # noqa: E731
        c_in, c_out = st.columns([1, 1.25], gap="medium")
        with c_in, st.container(border=True):
            ui.card_title("Trasa i pojazd")
            home = st.selectbox("Tankujesz przed wyjazdem w", opts, index=opts.index("PL"), format_func=fmt_c,
                                key="plan_home")
            route = st.multiselect("Kraje na trasie", [c for c in opts if c != home],
                                   default=[c for c in ["DE", "NL"] if c in opts and c != home],
                                   format_func=fmt_c, key="plan_route")
            p1, p2, p3 = st.columns(3)
            tank = p1.number_input("Bak [l]", min_value=50, max_value=2000, value=800, step=50, key="plan_tank")
            km = p2.number_input("Trasa [km]", min_value=0, max_value=10000, value=900, step=50, key="plan_km")
            cons = p3.number_input("l/100 km", min_value=5.0, max_value=80.0, value=29.0, step=0.5, format="%.1f",
                                   key="plan_cons")
        with c_out, st.container(border=True):
            ui.card_title("Wynik")
            stops = [home, *route]
            chips = "".join(
                f'<span class="num" style="background:{"#0F3A3A" if now_eu[c] <= now_eu[home] else "#3A1D26"};'
                f'color:{"#BDF2E5" if now_eu[c] <= now_eu[home] else "#FFC2BA"}">{c} {ui.num(now_eu[c], 3)}</span>'
                for c in stops
            )
            ui.html(f'<div class="stops">{chips}<span style="color:{C["muted"]}">EUR/l</span></div>')
            if route:
                cheapest = min(stops, key=lambda c: now_eu[c])
                name = wob.COUNTRIES.get(cheapest, cheapest)
                if cheapest == home:
                    alt = min(route, key=lambda c: now_eu[c])
                    saving = (now_eu[alt] - now_eu[home]) * tank
                    ui.html(f'<div class="reco"><span class="t">Rekomendacja</span>'
                            f'<span class="m">Zatankuj do pełna przed wyjazdem ({name}). Na trasie tankuj tylko tyle, ile trzeba.</span>'
                            f'<div class="s"><span class="num">{ui.num(saving)} €</span>'
                            f"<span>min. oszczędność vs {wob.COUNTRIES.get(alt, alt)} ({ui.num(tank)} l)</span></div></div>")
                else:
                    saving = (now_eu[home] - now_eu[cheapest]) * tank
                    ui.html(f'<div class="reco warn"><span class="t">Tańsze paliwo po drodze</span>'
                            f'<span class="m">Przed wyjazdem zatankuj tylko na dojazd – do pełna tankuj po drodze: '
                            f"{name} ({ui.num(now_eu[cheapest], 3)} EUR/l).</span>"
                            f'<div class="s"><span class="num">{ui.num(saving)} €</span>'
                            f"<span>oszczędności na {ui.num(tank)} l</span></div></div>")
            else:
                st.caption("Dodaj kraje na trasie, żeby porównać ceny.")
            need = km * cons / 100
            reach = tank / cons * 100
            ui.html(f'<div class="fresh num" style="min-height:0;justify-content:space-between">'
                    f"<span>Potrzeba ≈ {ui.num(need)} l</span><span>Zasięg ≈ {ui.num(reach)} km</span></div>")
            n_stops = max(0, math.ceil(need / tank) - 1)  # pełny bak na start + tankowania po drodze
            if n_stops:
                st.caption(f"Tankowania po drodze: co najmniej {n_stops} (przy pełnym baku na starcie).")
            st.caption("Średnie krajowe ceny z podatkami z biuletynu KE. Ceny na kartach flotowych i przy autostradach "
                       "oraz odliczenie VAT mogą zmienić wynik.")
