"""Dashboard hurtowych cen paliw. Uruchom: streamlit run app.py"""
from datetime import date, timedelta
from html import escape

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import auth
import db
import login_ui
import ui
import update_data
from sources import ara_manual, oilpriceapi, orlen, yahoo
from sources import eu_oil_bulletin as wob
from ui import C, CATEGORICAL

M3_PER_T = 1.1834  # 1 t / 0,845 kg/l = 1183,4 l
PRESETS = {"7D": 7, "1M": 30, "3M": 91, "6M": 182, "1Y": 365, "MAX": None}
SERIES_COLORS = {orlen.SERIES: C["orlen"], ara_manual.SERIES: C["ara"],
                 "gold": C["orlen"], "silver": "#B8C2CC", "usdpln": C["ara"]}
PREMIUM_WINDOW_DAYS = 90
# kraje pokazywane domyślnie w rankingu „gdzie tankować” (Polska + korytarze tranzytowe)
TRANSIT = ["PL", "DE", "CZ", "SK", "LT", "LV", "AT", "HU", "NL", "BE", "LU", "FR", "IT", "ES", "DK", "SE"]

st.set_page_config(page_title="Ceny paliw – hurt i stacje", layout="wide")
ui.inject_css()
auth.require_password()


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


def line_chart(df: pd.DataFrame, name: str, unit: str, color: str) -> None:
    fig = go.Figure(
        go.Scatter(
            x=df["date"], y=df["value"], name=name, mode="lines", line=dict(width=2, color=color),
            customdata=df["source"],
            hovertemplate="%{x|%Y-%m-%d}<br>%{y:,.2f} " + unit + "<br>%{customdata}<extra></extra>",
        )
    )
    ui.style_fig(fig, 380, yaxis_title=unit, hovermode="x unified")
    st.plotly_chart(fig, width="stretch")


def series_section(series: str, title: str, unit: str, decimals: int = 2, show_warning: bool = True) -> pd.DataFrame:
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
            line_chart(part, title, unit, SERIES_COLORS.get(series, C["ara"]))
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
h_brand, h_fresh, h_btn = st.columns([2.2, 5.3, 1.5], vertical_alignment="center")
h_brand.markdown(
    f'<div class="brand">{ui.LOGO}<div><b>Ceny paliw</b><span>hurt · giełda · stacje UE</span></div></div>',
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
    + "</div>",
    unsafe_allow_html=True,
)
if h_btn.button("Odśwież dane", type="primary", icon=":material/refresh:", width="stretch",
                help="Uruchamia ten sam kod co `python update_data.py`. Dane w bazie mają cache 15 min."):
    with st.spinner("Pobieram dane…"):
        st.session_state["refresh_results"] = update_data.run_all()
    st.cache_data.clear()
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
t_over, t_orlen, t_ara, t_prem, t_eu, t_mkt = st.tabs(
    ["Przegląd", "Hurt ORLEN", "ARA", "Premia", "Stacje UE", "Rynki"]
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
            fig = go.Figure()
            if not h_part.empty:
                fig.add_trace(go.Scatter(
                    x=h_part["date"], y=h_part["ara_pln"], name="ARA przeliczona (USD/t × USD/PLN ÷ 1,1834)",
                    mode="lines", line=dict(width=2, color=C["ara"]),
                    hovertemplate="ARA %{y:,.0f} PLN/m³<extra></extra>",
                ))
                fig.add_trace(go.Scatter(  # wypełnienie premii: od ARA do ORLEN w dniach z notowaniem ARA
                    x=h_part["date"], y=h_part["orlen"], mode="lines", line=dict(width=0),
                    fill="tonexty", fillcolor="rgba(242,169,59,0.10)", hoverinfo="skip", showlegend=False,
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
                pill = ("Premia wysoka", "#3A2F17", "#F5C46E")
                msg = (f"Hurt ORLEN jest <b>o {ui.num(diff_p)} PLN/m³ drożej</b> względem giełdy niż średnio w ostatnich "
                       f"{win_days} dniach. Jeśli premia wróci do średniej, hurt ma przestrzeń do spadku – "
                       "rozważ mniejsze partie zamiast zakupu na zapas.")
            elif pct_p < -0.02:
                pill = ("Premia niska", "#173A33", "#8FE3CF")
                msg = (f"Hurt ORLEN jest <b>o {ui.num(-diff_p)} PLN/m³ taniej</b> względem giełdy niż średnio w ostatnich "
                       f"{win_days} dniach – względnie korzystny moment na większy zakup.")
            else:
                pill = ("W normie", "#252C35", C["muted"])
                msg = (f"Premia ({ui.num(now_p)} PLN/m³) jest blisko średniej z {win_days} dni "
                       f"({ui.num(avg_p)} PLN/m³). Hurt wyceniony typowo względem giełdy.")
            ui.html(f'<div class="signal"><div class="head"><b>Sygnał dnia</b>'
                    f'<span class="pill" style="background:{pill[1]};color:{pill[2]}">{pill[0]}</span></div>'
                    f"<p>{msg}</p></div>")
            fig = go.Figure(go.Scatter(
                x=win["date"], y=win["premium"], mode="lines", line=dict(width=2, color=C["orlen"]),
                hovertemplate="%{x|%Y-%m-%d}<br>%{y:,.0f} PLN/m³<extra></extra>",
            ))
            fig.add_hline(y=avg_p, line=dict(width=1, dash="dash", color=C["muted"]))
            ui.style_fig(fig, 150, showlegend=False, margin=dict(l=0, r=0, t=6, b=0),
                         xaxis=dict(showgrid=False, tickformat="%d.%m"), yaxis=dict(nticks=3))
            st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
            st.caption(f"Premia w ostatnich {win_days} dniach; przerywana = średnia {ui.num(avg_p)} PLN/m³.")

    c_rank, c_plan = st.columns([2.4, 1], gap="medium")
    eu_countries = [] if eu_last_day is None else [
        c for c in eu_now.loc[eu_last_day].dropna().index if c not in wob.AVERAGES
    ]
    with c_rank, st.container(border=True):
        if not eu_countries or "PL" not in eu_countries:
            ui.card_title("Olej napędowy na stacjach – gdzie tankować")
            st.info("Brak danych biuletynu KE w bazie. Kliknij „Odśwież dane”.")
        else:
            now_eu = eu_now.loc[eu_last_day]
            ui.card_title("Olej napędowy na stacjach – gdzie tankować",
                          f"Weekly Oil Bulletin KE z {eu_last_day:%d.%m.%Y} · EUR/l z podatkami · od najtańszego")
            show_all = st.toggle("Wszystkie kraje UE", key="rank_all")
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
                       "„vs PL” = ile więcej (+) lub mniej (−) zapłacisz za 1000 l niż w Polsce przy średniej krajowej cenie.")

    with c_plan, st.container(border=True):
        ui.card_title("Plan tankowania na trasie", "ile zaoszczędzisz, tankując w najtańszym kraju")
        if not eu_countries or "PL" not in eu_countries:
            st.info("Potrzebne ceny z biuletynu KE.")
        else:
            now_eu = eu_now.loc[eu_last_day]
            opts = sorted(eu_countries, key=lambda c: wob.COUNTRIES.get(c, c))
            fmt_c = lambda c: f"{wob.COUNTRIES.get(c, c)} ({ui.num(now_eu[c], 3)})"  # noqa: E731
            home = st.selectbox("Tankujesz przed wyjazdem w", opts, index=opts.index("PL"), format_func=fmt_c,
                                key="plan_home")
            route = st.multiselect("Kraje na trasie", [c for c in opts if c != home],
                                   default=[c for c in ["DE", "NL"] if c in opts and c != home],
                                   format_func=fmt_c, key="plan_route")
            p1, p2, p3 = st.columns(3)
            tank = p1.number_input("Bak [l]", min_value=50, max_value=2000, value=800, step=50, key="plan_tank")
            km = p2.number_input("Trasa [km]", min_value=0, max_value=10000, value=900, step=50, key="plan_km")
            cons = p3.number_input("l/100 km", min_value=5.0, max_value=80.0, value=29.0, step=0.5, format="%.1f", key="plan_cons")

            stops = [home, *route]
            chips = "".join(
                f'<span class="num" style="background:{"#173A33" if now_eu[c] <= now_eu[home] else "#2A1E1A"};'
                f'color:{"#CFEFE6" if now_eu[c] <= now_eu[home] else "#F5B6A2"}">{c} {ui.num(now_eu[c], 3)}</span>'
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
            if need > tank:
                st.caption(f"Trasa wymaga co najmniej {int(need // tank)} tankowania po drodze.")
            st.caption("Średnie krajowe ceny z podatkami z biuletynu KE. Ceny na kartach flotowych i przy autostradach "
                       "oraz odliczenie VAT mogą zmienić wynik.")

# ================================================================ HURT ORLEN
with t_orlen:
    st.subheader("Ekodiesel ORLEN – cena hurtowa")
    orlen_full = series_section(orlen.SERIES, "Ekodiesel ORLEN (hurt)", orlen.UNIT, 0)
    if not orlen_full.empty:
        st.caption(
            f"Historia z API Orlenu od {orlen_full['date'].min():%Y-%m-%d} ({len(orlen_full)} notowań). "
            "Wg Orlenu: cena bez VAT, za paliwo w temperaturze referencyjnej 15°C; akcyza, opłata paliwowa i zapasowa "
            "wymienione jako składniki kształtujące cenę hurtową. Notowanie obowiązuje do kolejnej zmiany."
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
        series_section(ara_manual.SERIES, "ICE LS Gasoil (ARA)", ara_manual.UNIT, show_warning=False)
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
    st.subheader("Premia PL vs ARA")
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
            ara_pln = ara_last["value"] * fx_row["value"] / M3_PER_T
            c1, c2, c3 = st.columns(3)
            c1.metric("ARA w PLN/m³ netto", f"{ara_pln:,.0f} PLN/m³".replace(",", " "))
            c2.metric("Orlen Ekodiesel", f"{orl_row['value']:,.0f} PLN/m³ netto".replace(",", " "))
            c3.metric("Różnica Orlen − ARA", f"{orl_row['value'] - ara_pln:+,.0f} PLN/m³".replace(",", " "))
            st.caption(
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
        change = (ranked - prev[ranked.index]) if prev is not None else ranked * float("nan")
        # wykres punktowy, nie słupkowy: oś X nie startuje od zera, więc długość słupka by przekłamywała
        fig = go.Figure(go.Scatter(
            x=ranked.values, y=names, mode="markers",
            marker=dict(size=[14 if c == "PL" else 10 for c in ranked.index],
                        color=[C["orlen"] if c == "PL" else "#6B7684" for c in ranked.index]),
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
            st.dataframe(table, hide_index=True, width="stretch")
            st.caption("„vs Polska na 1000 l” – ile więcej (+) lub mniej (−) zapłacisz za 1000 l w danym kraju niż w Polsce "
                       "przy średniej krajowej cenie.")

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
                "Zmiana": f"{last['value'] / first['value'] - 1:+.2%}",
                "Min": f"{part['value'].min():,.4f}",
                "Max": f"{part['value'].max():,.4f}",
                "Rozpiętość max/min": f"{part['value'].max() / part['value'].min() - 1:.2%}",
            })
        if rows:
            fig.add_hline(y=100, line=dict(width=1, dash="dot", color=C["muted"]))
            ui.style_fig(fig, 420, hovermode="x unified", yaxis_title="Indeks (początek zakresu = 100)",
                         legend=dict(orientation="h", y=1.08))
            st.plotly_chart(fig, width="stretch")
            st.caption("Każda seria znormalizowana do 100 na swoim pierwszym notowaniu w zakresie. W dymku wartość w oryginalnej jednostce.")
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        else:
            st.info("Brak notowań w wybranym zakresie.")

st.divider()
st.caption(f"Autor aplikacji: **{login_ui.AUTHOR}** · ID Logistics – narzędzie wewnętrzne")
