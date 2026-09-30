"""Dashboard hurtowych cen paliw. Uruchom: streamlit run app.py"""
import math
from datetime import date, timedelta
from html import escape

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import auth
import db
import i18n
import login_ui
import scheduler
import ui
from sources import ara_manual, oilpriceapi, orlen, yahoo
from sources import eu_oil_bulletin as wob
from i18n import L
from ui import C, CATEGORICAL

M3_PER_T = 1.1834  # 1 t / 0,845 kg/l = 1183,4 l
PRESETS = {"7D": 7, "1M": 30, "3M": 91, "6M": 182, "1Y": 365, "MAX": None}
SERIES_COLORS = {orlen.SERIES: C["orlen"], ara_manual.SERIES: C["ara"],
                 "gold": C["gold"], "silver": "#C8D3DF", "usdpln": C["ara"]}
PREMIUM_WINDOW_DAYS = 90
# kraje pokazywane domyślnie w rankingu „gdzie tankować” (Polska + korytarze tranzytowe)
TRANSIT = ["PL", "DE", "CZ", "SK", "LT", "LV", "AT", "HU", "NL", "BE", "LU", "FR", "IT", "ES", "DK", "SE"]

# teksty ze źródeł zapisane w bazie po polsku -> wersja angielska do wyświetlenia
SOURCES_EN = {
    orlen.SOURCE: "ORLEN – wholesale fuel prices (tool.orlen.pl, productId=43)",
    oilpriceapi.SOURCE_LAST: "OilPriceAPI GASOIL_USD – last price of the day",
    oilpriceapi.SOURCE_AVG: "OilPriceAPI GASOIL_USD – daily average (history import)",
    ara_manual.SOURCE: "TradingView ICEEUR:ULS1! – manual entry",
    wob.SOURCE: "European Commission – Weekly Oil Bulletin",
}
UNITS_EN = {orlen.UNIT: "PLN/m³ net"}
MONTHS = {"pl": ["sty", "lut", "mar", "kwi", "maj", "cze", "lip", "sie", "wrz", "paź", "lis", "gru"],
          "en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]}


def src(source: str) -> str:
    return SOURCES_EN.get(source, source) if i18n.en() else source


def unit_lbl(unit: str) -> str:
    return UNITS_EN.get(unit, unit) if i18n.en() else unit


def country(code: str) -> str:
    return (wob.COUNTRIES_EN if i18n.en() else wob.COUNTRIES).get(code, code)


def variant_lbl(variant: str) -> str:
    return L(wob.LABELS[variant], wob.LABELS_EN[variant])


def variant_desc(variant: str) -> str:
    return L(wob.VARIANTS[variant][2], wob.DESC_EN[variant])


def ticker(series: str) -> tuple[str, str]:
    """(jednostka, opis) instrumentu z Yahoo w bieżącym języku."""
    return yahoo.TICKERS_EN[series] if i18n.en() else yahoo.TICKERS[series][1:]


def range_tip() -> str:
    return L("Zakres zmieniasz przyciskami 7D–MAX nad wykresem; „Własny” = dowolne daty.",
             "Change the range with the 7D–MAX buttons above the chart; “Custom” = any dates.")


st.set_page_config(page_title=L("Ceny paliw – hurt i stacje", "Fuel prices – wholesale and pumps"), layout="wide")
ui.inject_css()
auth.require_password()

# ---------------------------------------------------------------- auto-odświeżanie 18:30
scheduler.start()
if scheduler.is_due():
    if scheduler.is_running():
        st.info(L("Trwa automatyczne odświeżanie danych – za chwilę odśwież stronę. Poniżej dane zapisane w bazie.",
                  "Automatic data refresh in progress – reload the page in a moment. Below: data stored in the database."))
    else:
        slot = f"{scheduler.last_slot():%d.%m %H:%M}"
        with st.spinner(L(f"Automatyczne odświeżanie danych (termin {slot})…", f"Automatic data refresh (scheduled {slot})…")):
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


def wob_series(variant: str, code: str, unit: str) -> pd.Series:
    """Jedna seria z biuletynu KE (indeks = data biuletynu) w EUR/l, PLN/l albo PLN/m³ (kurs z tego samego biuletynu)."""
    wide = load_eu(variant, unit != "EUR/l")
    if wide.empty or code not in wide:
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))  # pusty, ale z indeksem dat (filtry .index.date)
    s = wide[code].dropna()
    return s * 1000 if unit == "PLN/m³" else s


def source_warning(series: str) -> None:
    """Pokazuje błąd ostatniego pobrania danej serii (pozostałe sekcje działają dalej)."""
    status = db.last_fetch(series)
    if status and not status["ok"]:
        st.warning(L(f"Ostatnie pobranie ({status['ts']}) nieudane: {status['message']}. Pokazuję dane zapisane w bazie.",
                     f"Last fetch ({status['ts']}) failed: {status['message']}. Showing data stored in the database."))


def range_picker(key: str, default: str = "3M") -> tuple[date, date] | None:
    options = [*PRESETS, "custom"]
    choice = st.segmented_control(L("Zakres", "Range"), options, default=default, key=f"{key}_preset",
                                  format_func=lambda o: L("Własny", "Custom") if o == "custom" else o,
                                  label_visibility="collapsed") or default
    today = date.today()
    if choice == "custom":
        picked = st.date_input(
            L("Własny zakres dat", "Custom date range"), value=(today - timedelta(days=90), today),
            format="YYYY-MM-DD", key=f"{key}_dates"
        )
        if len(picked) != 2:
            st.info(L("Wybierz datę początkową i końcową.", "Pick a start and an end date."))
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
    help_txt = L("Brak poprzedniego notowania.", "No previous quote.")
    if len(df) > 1:
        prev = df.iloc[-2]
        diff = last["value"] - prev["value"]
        delta = f"{fmt.format(diff)} ({diff / prev['value']:+.2%})".replace(",", " ")
        help_txt = L(f"Zmiana d/d względem poprzedniego notowania z {prev['date']:%Y-%m-%d}.",
                     f"Day-on-day change vs the previous quote of {prev['date']:%Y-%m-%d}.")
        if prev["source"] != last["source"]:
            help_txt += L(f" UWAGA: inne źródło/miara poprzedniego notowania ({src(prev['source'])}).",
                          f" NOTE: the previous quote has a different source/measure ({src(prev['source'])}).")
    st.metric(label, f"{fmt.format(last['value'])} {unit}".replace(",", " "), delta, help=help_txt,
              delta_color="inverse" if inverse else "normal")
    st.caption(L(f"Notowanie z **{last['date']:%Y-%m-%d}** · źródło: {src(last['source'])} · jednostka: {unit}",
                 f"Quote of **{last['date']:%Y-%m-%d}** · source: {src(last['source'])} · unit: {unit}"))
    if len(df) > 1 and df.iloc[-2]["source"] != last["source"]:
        st.caption(L(f"Uwaga: zmiana d/d liczona względem innej miary: {src(df.iloc[-2]['source'])}.",
                     f"Note: the d/d change is computed against a different measure: {src(df.iloc[-2]['source'])}."))


def line_chart(df: pd.DataFrame, name: str, unit: str, color: str, tips: list[str] = ()) -> None:
    ui.legend([("line", color, L(f"<b>{escape(name)}</b> – kolejne notowania, {escape(unit)}",
                                 f"<b>{escape(name)}</b> – successive quotes, {escape(unit)}"))],
              [*tips, L("Najedź kursorem na linię – dymek pokazuje datę, wartość i źródło notowania.",
                        "Hover over the line – the tooltip shows the date, value and source of the quote."), range_tip()])
    fig = go.Figure(
        go.Scatter(
            x=df["date"], y=df["value"], name=name, mode="lines", line=dict(width=2, color=color),
            customdata=df["source"].map(src),
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
        st.error(L("Brak danych w bazie dla tej serii.", "No data in the database for this series."))
        return df
    metric(df, title, unit, decimals)
    rng = range_picker(series)
    if rng:
        part = in_range(df, rng)
        if part.empty:
            st.info(L("Brak notowań w wybranym zakresie.", "No quotes in the selected range."))
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
    m["label"] = [f"{MONTHS[i18n.lang()][p.month - 1]} {p.year}" for p in m.index]
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
    note = L(" · uwaga: d/d względem innej miary", " · note: d/d vs a different measure") if prev["source"] != last["source"] else ""
    return ui.delta_html(diff, diff / prev["value"], decimals), note


# ---------------------------------------------------------------- dane wspólne dla przeglądu
orlen_df = load(orlen.SERIES)
ara_df = load(ara_manual.SERIES)
fx_df = load("usdpln")
eu_now = load_eu("brutto", False)
wob_pl = wob_series("netto", "PL", "PLN/m³")  # benchmark: stacje PL bez podatków, PLN/m³
wob_eu = wob_series("netto", "EU", "PLN/m³")
hist = premium_history(ara_df, orlen_df, fx_df) if not (ara_df.empty or orlen_df.empty or fx_df.empty) else pd.DataFrame()

# ---------------------------------------------------------------- nagłówek
h_brand, h_fresh, h_lang, h_btn, h_out = st.columns([2.2, 4.1, 0.9, 1.5, 1.1], vertical_alignment="center")
h_brand.markdown(
    f'<div class="brand"><span class="chip"><img src="{login_ui.logo_src()}" alt="ID Logistics"></span>'
    f'<div><b>{L("Ceny paliw", "Fuel prices")}</b>'
    f'<span>{L("monitoring · Polska i Europa", "monitoring · Poland and Europe")}</span></div></div>',
    unsafe_allow_html=True,
)
i18n.picker(h_lang)


def fresh_chip(label: str, df_or_date, fetch_series: str) -> str:
    status = db.last_fetch(fetch_series)
    color = C["good"] if status is None or status["ok"] else C["orlen"]
    if isinstance(df_or_date, pd.DataFrame):
        when = L("brak", "none") if df_or_date.empty else f"{df_or_date['date'].max():%d.%m}"
    else:
        when = L("brak", "none") if df_or_date is None else f"{df_or_date:%d.%m}"
    failed = L("Ostatnie pobranie nieudane", "Last fetch failed")
    title = "" if status is None or status["ok"] else f' title="{failed}: {escape(status["message"])}"'
    return f'<span{title}><i style="background:{color}"></i> {label} <span class="num">{when}</span></span>'


eu_last_day = eu_now["PL"].dropna().index.max() if not eu_now.empty and "PL" in eu_now else None
h_fresh.markdown(
    f'<div class="fresh">{L("Ostatnie notowania:", "Latest quotes:")}'
    + fresh_chip("ORLEN", orlen_df, orlen.SERIES)
    + fresh_chip("ARA", ara_df, ara_manual.SERIES)
    + fresh_chip("USD/PLN", fx_df, "usdpln")
    + fresh_chip(L("UE", "EU"), eu_last_day, wob.LOG_SERIES)
    + f'<span>· {L("auto-odświeżanie pon–sob 18:30, następne", "auto-refresh Mon–Sat 18:30, next")} '
      f'<span class="num">{scheduler.next_slot():%d.%m %H:%M}</span></span>'
    + "</div>",
    unsafe_allow_html=True,
)
if h_btn.button(L("Odśwież dane", "Refresh data"), type="primary", icon=":material/refresh:", width="stretch",
                help=L("Uruchamia ten sam kod co `python update_data.py`. Automatycznie: pon–sob o 18:30.",
                       "Runs the same code as `python update_data.py`. Automatically: Mon–Sat at 18:30.")):
    with st.spinner(L("Pobieram dane…", "Fetching data…")):
        st.session_state["refresh_results"] = scheduler.run_now()
    st.rerun()
if h_out.button(L("Wyloguj", "Sign out"), icon=":material/logout:", width="stretch",
                help=L("Zakończ sesję – wymaga ponownego podania hasła.", "End the session – the password will be required again.")):
    st.session_state.clear()
    st.rerun()

if results := st.session_state.pop("refresh_results", None):
    for series, (ok, msg) in results.items():
        if not ok:
            st.error(f"{series}: {msg}")
    n_ok = sum(ok for ok, _ in results.values())
    with st.expander(L(f"Odświeżono: {n_ok} z {len(results)} źródeł OK", f"Refreshed: {n_ok} of {len(results)} sources OK")):
        for series, (ok, msg) in results.items():
            st.markdown(f"{'✓' if ok else '✗'} **{series}** – {msg}")

# ---------------------------------------------------------------- kafelki KPI
k1, k2, k5, k3, k4 = st.columns(5)
with k1:
    if orlen_df.empty:
        ui.kpi_empty(L("Ekodiesel ORLEN · hurt", "ORLEN · wholesale"),
                     L("Kliknij „Odśwież dane”.", "Click “Refresh data”."), C["orlen"])
    else:
        last = orlen_df.iloc[-1]
        delta, note = d_d(orlen_df, 0)
        ui.kpi(L("Ekodiesel ORLEN · hurt", "ORLEN · wholesale"), ui.num(last["value"]), "PLN/m³", delta,
               ui.sparkline(orlen_df["value"].tail(30), C["orlen"]),
               L(f"netto, 15°C · od {last['date']:%d.%m.%Y}{note}", f"Ekodiesel, net, 15°C · since {last['date']:%d.%m.%Y}{note}"),
               dot=C["orlen"])
with k2:
    if ara_df.empty:
        ui.kpi_empty("ARA · ICE LS Gasoil", L("Ustaw OILPRICEAPI_KEY albo dodaj wpis ręczny w zakładce ARA.",
                                              "Set OILPRICEAPI_KEY or add a manual entry in the ARA tab."), C["ara"])
    else:
        last = ara_df.iloc[-1]
        delta, note = d_d(ara_df, 2)
        pln = (f"= {ui.num(hist.iloc[-1]['ara_pln'])} PLN/m³ · "
               if not hist.empty and hist.iloc[-1]["date"] == last["date"] else "")
        avg_src = L(" · średnia dzienna", " · daily average") if last["source"] == oilpriceapi.SOURCE_AVG else ""
        ui.kpi("ARA · ICE LS Gasoil", ui.num(last["value"], 2), "USD/t", delta,
               ui.sparkline(ara_df["value"].tail(30), C["ara"]),
               f"{pln}{last['date']:%d.%m.%Y}{avg_src}{note}", dot=C["ara"])
with k5:
    if wob_pl.empty:
        ui.kpi_empty(L("Stacje PL · bez podatków", "PL pumps · ex-tax"),
                     L("Brak biuletynu KE – kliknij „Odśwież dane”.", "No EC bulletin – click “Refresh data”."), C["wob"])
    else:
        w_day, w_val = wob_pl.index[-1], wob_pl.iloc[-1]
        delta = "" if len(wob_pl) < 2 else ui.delta_html(w_val - wob_pl.iloc[-2], w_val / wob_pl.iloc[-2] - 1, 0, L("t/t", "w/w"))
        eu_txt = ""
        if w_day in wob_eu.index:
            eu_v = wob_eu[w_day]
            eu_txt = f"{L('UE-27', 'EU-27')} {ui.num(eu_v)} ({ui.num((w_val / eu_v - 1) * 100, 1, sign=True)}%) · "
        ui.kpi(L("Stacje PL · bez podatków", "PL pumps · ex-tax"), ui.num(w_val), "PLN/m³", delta,
               ui.sparkline(wob_pl.tail(26), C["wob"]),
               f"{eu_txt}{L('biuletyn KE', 'EC bulletin')} {w_day:%d.%m.%Y}", dot=C["wob"])
with k3:
    if hist.empty:
        ui.kpi_empty(L("Premia PL vs ARA", "PL vs ARA premium"),
                     L("Wymaga notowań ARA, ORLEN i USD/PLN.", "Requires ARA, ORLEN and USD/PLN quotes."))
    else:
        win, win_days = premium_window(hist)
        now_p, avg_p = hist.iloc[-1]["premium"], win["premium"].mean()
        diff_p = now_p - avg_p
        delta = (f'<span class="delta num" style="color:{C["orlen"] if diff_p > 0 else C["down"]}">'
                 f"{ui.num(diff_p, sign=True)} {L(f'vs średnia {win_days} dni', f'vs {win_days}-day average')}</span>")
        ui.kpi(L("Premia PL vs ARA", "PL vs ARA premium"), ui.num(now_p), "PLN/m³", delta,
               ui.sparkline(hist["premium"].tail(30), C["text"]),
               L("podatki, opłaty, logistyka i marża", "taxes, levies, logistics and margin"), accent=True)
with k4:
    if fx_df.empty:
        ui.kpi_empty("USD/PLN", L("Kliknij „Odśwież dane”.", "Click “Refresh data”."))
    else:
        last = fx_df.iloc[-1]
        delta, note = d_d(fx_df, 4)
        ui.kpi("USD/PLN", ui.num(last["value"], 4), "PLN", delta,
               ui.sparkline(fx_df["value"].tail(30), C["muted"]),
               f"{last['date']:%d.%m.%Y} · {L('tańszy dolar = tańsza ARA w PLN', 'cheaper dollar = cheaper ARA in PLN')}{note}")

st.write("")
t_over, t_orlen, t_wob, t_ara, t_prem, t_eu, t_mkt, t_plan = st.tabs(
    [L("Przegląd", "Overview"), L("Hurt ORLEN", "ORLEN wholesale"), "WOB", "ARA", L("Premia", "Premium"),
     L("Stacje UE", "EU pumps"), L("Rynki", "Markets"), L("Plan tankowania", "Refuelling plan")]
)

# ================================================================ PRZEGLĄD
with t_over:
    c_chart, c_side = st.columns([2.4, 1], gap="medium")
    with c_chart, st.container(border=True):
        ui.card_title(L("Hurt ORLEN vs giełda ARA", "ORLEN wholesale vs ARA exchange"),
                      L("PLN/m³ netto · pole między liniami = premia PL · fioletowa = stacje PL bez podatków",
                        "PLN/m³ net · area between the lines = PL premium · purple = PL pumps without taxes"))
        rng = range_picker("over", default="1Y")
        if orlen_df.empty:
            st.info(L("Brak notowań ORLEN w bazie.", "No ORLEN quotes in the database."))
        elif rng:
            orl_part = in_range(orlen_df, rng)
            h_part = in_range(hist, rng) if not hist.empty else hist
            w_part = wob_pl[(wob_pl.index.date >= rng[0]) & (wob_pl.index.date <= rng[1])]
            ui.legend([
                ("step", C["orlen"], L("<b>Hurt ORLEN</b> (Ekodiesel, netto) – schodki, bo cena obowiązuje do kolejnej zmiany cennika",
                                       "<b>ORLEN wholesale</b> (Ekodiesel, net) – steps, because a price holds until the next price-list change")),
                ("line", C["ara"], L("<b>Giełda ARA</b> przeliczona na PLN/m³ (USD/t × USD/PLN ÷ 1,1834)",
                                     "<b>ARA exchange</b> converted to PLN/m³ (USD/t × USD/PLN ÷ 1.1834)")),
                ("area", C["orlen"], L("<b>Pole między liniami = premia PL</b> – o ile hurt ORLEN jest droższy od giełdy",
                                       "<b>Area between the lines = PL premium</b> – how much more ORLEN wholesale costs than the exchange")),
                ("line-markers", C["wob"], L("<b>Stacje PL bez podatków</b> – Weekly Oil Bulletin KE, co tydzień (poniedziałek)",
                                             "<b>PL pumps without taxes</b> – EC Weekly Oil Bulletin, weekly (Monday)")),
            ], [
                L("Pole się <b>rozszerza</b> – ORLEN drożeje względem giełdy (lub nie nadąża za jej spadkiem).",
                  "The area <b>widens</b> – ORLEN gets pricier relative to the exchange (or lags behind its fall)."),
                L("Pole się <b>zwęża</b> – hurt tanieje względem giełdy.",
                  "The area <b>narrows</b> – wholesale gets cheaper relative to the exchange."),
                L("ORLEN reaguje na ARA z opóźnieniem – spadek niebieskiej linii zwykle zapowiada obniżkę w hurcie.",
                  "ORLEN follows ARA with a delay – a drop in the blue line usually signals a wholesale price cut."),
                L("Fioletowa leży <b>poniżej</b> ORLEN, bo hurt zawiera akcyzę i opłatę paliwową, a cena bez podatków – nie. "
                  "Porównuj ją z <b>ARA</b> (obie bez podatków): odstęp = logistyka i marże – szczegóły w zakładce <b>WOB</b>.",
                  "The purple line sits <b>below</b> ORLEN because wholesale includes excise duty and the fuel levy, while the "
                  "price without taxes does not. Compare it with <b>ARA</b> (both without taxes): the gap = logistics and margins "
                  "– details in the <b>WOB</b> tab."),
                range_tip(),
            ])
            fig = go.Figure()
            if not h_part.empty:
                fig.add_trace(go.Scatter(
                    x=h_part["date"], y=h_part["ara_pln"], name=L("ARA przeliczona (USD/t × USD/PLN ÷ 1,1834)", "ARA converted (USD/t × USD/PLN ÷ 1.1834)"),
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
            if not w_part.empty:
                fig.add_trace(go.Scatter(
                    x=w_part.index, y=w_part.values, name=L("Stacje PL bez podatków (WOB)", "PL pumps without taxes (WOB)"),
                    mode="lines+markers", line=dict(width=1.8, color=C["wob"]), marker=dict(size=4),
                    hovertemplate=L("Stacje PL bez podatków", "PL pumps without taxes") + " %{y:,.0f} PLN/m³<extra></extra>",
                ))
            ui.style_fig(fig, 400, hovermode="x unified", yaxis_title="PLN/m³",
                         legend=dict(orientation="h", y=1.08, x=0))
            st.plotly_chart(fig, width="stretch")
            if h_part.empty:
                st.caption(L("Brak notowań ARA w tym zakresie – widać tylko ORLEN. Historia ARA w bazie zaczyna się od pierwszego pobrania z API.",
                             "No ARA quotes in this range – only ORLEN is shown. ARA history in the database starts with the first API fetch."))

    with c_side, st.container(border=True):
        if hist.empty:
            ui.html(f'<div class="signal"><div class="head"><b>{L("Sygnał dnia", "Signal of the day")}</b></div>'
                    + L("<p>Sygnał liczony z premii PL vs ARA – potrzebne notowania ARA (klucz OilPriceAPI "
                        "lub wpis ręczny w zakładce ARA).</p></div>",
                        "<p>The signal is based on the PL vs ARA premium – ARA quotes are needed (OilPriceAPI key "
                        "or a manual entry in the ARA tab).</p></div>"))
        else:
            win, win_days = premium_window(hist)
            now_p, avg_p = hist.iloc[-1]["premium"], win["premium"].mean()
            diff_p, pct_p = now_p - avg_p, now_p / avg_p - 1
            if pct_p > 0.02:
                pill = (L("Premia wysoka", "High premium"), "#3A1D26", "#FF9A8E")
                msg = L(f"Hurt ORLEN jest <b>o {ui.num(diff_p)} PLN/m³ drożej</b> względem giełdy niż średnio w ostatnich "
                        f"{win_days} dniach. Jeśli premia wróci do średniej, hurt ma przestrzeń do spadku – "
                        "rozważ mniejsze partie zamiast zakupu na zapas.",
                        f"ORLEN wholesale is <b>{ui.num(diff_p)} PLN/m³ more expensive</b> relative to the exchange than "
                        f"on average over the last {win_days} days. If the premium returns to its average, wholesale has room "
                        "to fall – consider smaller batches instead of stocking up.")
            elif pct_p < -0.02:
                pill = (L("Premia niska", "Low premium"), "#0F3A3A", "#7FE3CC")
                msg = L(f"Hurt ORLEN jest <b>o {ui.num(-diff_p)} PLN/m³ taniej</b> względem giełdy niż średnio w ostatnich "
                        f"{win_days} dniach – względnie korzystny moment na większy zakup.",
                        f"ORLEN wholesale is <b>{ui.num(-diff_p)} PLN/m³ cheaper</b> relative to the exchange than on "
                        f"average over the last {win_days} days – a relatively good moment for a larger purchase.")
            else:
                pill = (L("W normie", "Normal"), "#16365B", C["muted"])
                msg = L(f"Premia ({ui.num(now_p)} PLN/m³) jest blisko średniej z {win_days} dni "
                        f"({ui.num(avg_p)} PLN/m³). Hurt wyceniony typowo względem giełdy.",
                        f"The premium ({ui.num(now_p)} PLN/m³) is close to its {win_days}-day average "
                        f"({ui.num(avg_p)} PLN/m³). Wholesale is priced as usual relative to the exchange.")
            ui.html(f'<div class="signal"><div class="head"><b>{L("Sygnał dnia", "Signal of the day")}</b>'
                    f'<span class="pill" style="background:{pill[1]};color:{pill[2]}">{pill[0]}</span></div>'
                    f"<p>{msg}</p></div>")
            ui.legend([
                ("line", C["orlen"], L(f"<b>Premia PL vs ARA</b> z ostatnich {win_days} dni, PLN/m³",
                                       f"<b>PL vs ARA premium</b> over the last {win_days} days, PLN/m³")),
                ("dash", C["muted"], L(f"<b>Średnia premii</b> z tego okresu ({ui.num(avg_p)} PLN/m³)",
                                       f"<b>Average premium</b> for this period ({ui.num(avg_p)} PLN/m³)")),
            ], [
                L("Linia <b>nad</b> przerywaną – hurt drogi względem giełdy, nie kupuj na zapas.",
                  "Line <b>above</b> the dashed one – wholesale is expensive vs the exchange, don't stock up."),
                L("Linia <b>pod</b> przerywaną – hurt tani względem giełdy, dobry moment na większy zakup.",
                  "Line <b>below</b> the dashed one – wholesale is cheap vs the exchange, a good moment for a larger purchase."),
            ])
            fig = go.Figure(go.Scatter(
                x=win["date"], y=win["premium"], mode="lines", line=dict(width=2, color=C["orlen"]),
                hovertemplate="%{x|%Y-%m-%d}<br>%{y:,.0f} PLN/m³<extra></extra>",
            ))
            fig.add_hline(y=avg_p, line=dict(width=1, dash="dash", color=C["muted"]))
            ui.style_fig(fig, 150, showlegend=False, margin=dict(l=0, r=0, t=6, b=0),
                         xaxis=dict(showgrid=False, tickformat="%d.%m"), yaxis=dict(nticks=3))
            st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
            st.caption(L(f"Premia w ostatnich {win_days} dniach; przerywana = średnia {ui.num(avg_p)} PLN/m³. "
                         "Jak czytać premię – zakładka **Premia**.",
                         f"Premium over the last {win_days} days; dashed = average {ui.num(avg_p)} PLN/m³. "
                         "How to read the premium – see the **Premium** tab."))

    eu_countries = [] if eu_last_day is None else [
        c for c in eu_now.loc[eu_last_day].dropna().index if c not in wob.AVERAGES
    ]
    with st.container(border=True):
        if not eu_countries or "PL" not in eu_countries:
            ui.card_title(L("Olej napędowy na stacjach w UE", "Diesel at EU pumps"))
            st.info(L("Brak danych biuletynu KE w bazie. Kliknij „Odśwież dane”.",
                      "No EC bulletin data in the database. Click “Refresh data”."))
        else:
            head = st.container()
            t_var, t_all, t_leg = st.columns([2, 2, 1], vertical_alignment="center")
            rank_variant = t_var.segmented_control(
                L("Cena", "Price"), list(wob.LABELS), default="netto", key="rank_variant",
                format_func=variant_lbl, label_visibility="collapsed",
            ) or "netto"
            show_all = t_all.toggle(L("Wszystkie kraje UE", "All EU countries"), key="rank_all")
            rank_eu = load_eu(rank_variant, False)
            rank_day = rank_eu["PL"].dropna().index.max() if "PL" in rank_eu else eu_last_day
            now_eu = rank_eu.loc[rank_day] if not rank_eu.empty else eu_now.loc[eu_last_day]
            eu_countries_r = [c for c in now_eu.dropna().index if c not in wob.AVERAGES]
            with head:
                ui.card_title(L("Olej napędowy na stacjach w UE", "Diesel at EU pumps"),
                              L(f"Weekly Oil Bulletin KE z {rank_day:%d.%m.%Y} · EUR/l "
                                f"{variant_lbl(rank_variant).lower()} · od najtańszego",
                                f"EC Weekly Oil Bulletin of {rank_day:%d.%m.%Y} · EUR/l "
                                f"{variant_lbl(rank_variant).lower()} · cheapest first"))
            with t_leg:
                ui.legend([
                    ("bar", C["orlen"], L("<b>Polska</b> – punkt odniesienia", "<b>Poland</b> – reference point")),
                    ("bar", C["good"], L("Kraj <b>tańszy</b> niż Polska", "Country <b>cheaper</b> than Poland")),
                    ("bar", C["neutral"], L("Kraj <b>droższy</b> niż Polska", "Country <b>more expensive</b> than Poland")),
                ], [
                    L("Kolumna „vs PL na 1000 l”: <b style='color:#4FD1B5'>−</b> = tyle zaoszczędzisz, "
                      "<b style='color:#FF8A7A'>+</b> = tyle dopłacisz względem tankowania w Polsce.",
                      "Column “vs PL per 1000 l”: <b style='color:#4FD1B5'>−</b> = what you save, "
                      "<b style='color:#FF8A7A'>+</b> = what you pay extra compared with refuelling in Poland."),
                    L("Słupki nie startują od zera – porównuj różnice między krajami, nie długości.",
                      "Bars don't start at zero – compare the differences between countries, not the lengths."),
                    L("„Wszystkie kraje UE” – pełna lista zamiast krajów tranzytowych.",
                      "“All EU countries” – the full list instead of transit countries."),
                ])
            codes = eu_countries_r if show_all else [c for c in TRANSIT if c in eu_countries_r]
            ranked = now_eu[codes].sort_values()
            pl = now_eu["PL"]
            lo = max(0.0, (ranked.min() * 10 // 1) / 10 - 0.1)
            span = max(ranked.max() - lo, 0.01)
            rows = [f'<div class="rank"><div class="r hd"><span>{L("Kraj", "Country")}</span><span></span>'
                    f'<span class="p">EUR/l</span><span class="d">{L("vs PL na 1000 l", "vs PL per 1000 l")}</span></div>']
            for code, price in ranked.items():
                d = round((price - pl) * 1000)
                is_pl = code == "PL"
                bar = C["orlen"] if is_pl else (C["good"] if d < 0 else C["neutral"])
                d_color = C["muted"] if is_pl else (C["down"] if d < 0 else C["up"])
                d_txt = "—" if is_pl else f"{ui.num(d, sign=True)} €"
                rows.append(
                    f'<div class="r{" pl" if is_pl else ""}"><span><span class="code num">{code}</span>'
                    f"{country(code)}</span>"
                    f'<span class="track"><s style="width:{(price - lo) / span * 100:.1f}%;background:{bar}"></s></span>'
                    f'<span class="p num">{ui.num(price, 3)}</span>'
                    f'<span class="d num" style="color:{d_color}">{d_txt}</span></div>'
                )
            ui.html("".join(rows) + "</div>")
            st.caption(L(f"Słupki od {ui.num(lo, 1)} EUR/l – porównuj różnice, nie długości od zera. "
                         "„vs PL” = ile więcej (+) lub mniej (−) zapłacisz za 1000 l niż w Polsce przy średniej krajowej cenie. "
                         "Oszczędność na konkretnej trasie policzysz w zakładce **Plan tankowania**.",
                         f"Bars start at {ui.num(lo, 1)} EUR/l – compare differences, not lengths from zero. "
                         "“vs PL” = how much more (+) or less (−) you pay for 1000 l than in Poland at the national average price. "
                         "Calculate the saving on a specific route in the **Refuelling plan** tab.")
                       + (L(" Wariant „Bez podatków” porównuje samo paliwo (produkt, logistyka, marża); "
                            "ile faktycznie zapłacisz na stacji – przełącz na „Z podatkami”.",
                            " The “Without taxes” variant compares the fuel itself (product, logistics, margin); "
                            "for what you actually pay at the pump, switch to “With taxes”.") if rank_variant == "netto" else ""))

# ================================================================ HURT ORLEN
with t_orlen:
    st.subheader(L("Ekodiesel ORLEN – cena hurtowa", "Ekodiesel ORLEN – wholesale price"))
    orlen_full = series_section(orlen.SERIES, L("Ekodiesel ORLEN (hurt)", "Ekodiesel ORLEN (wholesale)"), unit_lbl(orlen.UNIT), 0,
                                tips=[L("Poziome odcinki = cena bez zmian; linia łączy kolejne zmiany cennika.",
                                        "Flat segments = unchanged price; the line connects successive price-list changes.")])
    if not orlen_full.empty:
        st.caption(L(
            f"Historia z API Orlenu od {orlen_full['date'].min():%Y-%m-%d} ({len(orlen_full)} notowań). "
            "Wg Orlenu: cena bez VAT, za paliwo w temperaturze referencyjnej 15°C; akcyza, opłata paliwowa i zapasowa "
            "wymienione jako składniki kształtujące cenę hurtową. Notowanie obowiązuje do kolejnej zmiany.",
            f"History from the Orlen API since {orlen_full['date'].min():%Y-%m-%d} ({len(orlen_full)} quotes). "
            "According to Orlen: price excl. VAT, for fuel at the 15°C reference temperature; excise duty, the fuel levy and "
            "the stockholding fee are listed as components of the wholesale price. A quote holds until the next change."
        ))

        st.markdown(L("#### Archiwum – średnie miesięczne (24 pełne miesiące + bieżący)",
                      "#### Archive – monthly averages (24 full months + current)"))
        arch = monthly_averages(orlen_full, ARCHIVE_MONTHS)
        partial = arch["partial"]
        ui.legend([
            ("bar", C["orlen"], L("<b>Średnia miesięczna</b> pełnego miesiąca, PLN/m³ netto",
                                  "<b>Monthly average</b> for a full month, PLN/m³ net")),
            ("bar-faded", C["orlen"], L("<b>Bieżący miesiąc</b> – średnia do dziś, jeszcze się zmieni",
                                        "<b>Current month</b> – average to date, will still change")),
        ], [
            L("Słupki startują od zera – długość = cena, można porównywać wprost.",
              "Bars start at zero – length = price, compare them directly."),
            L("Średnia ważona dniami: każdy dzień liczy się po cenie, która wtedy obowiązywała.",
              "Day-weighted average: each day counts at the price in force on that day."),
            L("Najedź na słupek – dymek pokazuje min, max i zmianę m/m.",
              "Hover over a bar – the tooltip shows min, max and the m/m change."),
        ])
        fig = go.Figure(go.Bar(
            x=arch["label"], y=arch["mean"], marker=dict(color=C["orlen"], opacity=[0.45 if p else 1.0 for p in partial]),
            customdata=arch[["min", "max", "diff"]].to_numpy(),
            hovertemplate="%{x}<br>" + L("średnia", "average") + " %{y:,.0f} PLN/m³<br>min %{customdata[0]:,.0f} · max %{customdata[1]:,.0f}"
                          "<br>m/m %{customdata[2]:+,.0f}<extra></extra>",
        ))
        ui.style_fig(fig, 340, yaxis=dict(title="PLN/m³", rangemode="tozero"),  # słupki od zera – długość = cena
                     xaxis=dict(type="category", tickangle=-45), showlegend=False)
        st.plotly_chart(fig, width="stretch")

        c_month, c_avg_m3, c_avg_l = L("Miesiąc", "Month"), L("Średnia [PLN/m³]", "Average [PLN/m³]"), L("Średnia [PLN/l]", "Average [PLN/l]")
        c_mm, c_mm_pct = L("Zmiana m/m [PLN/m³]", "Change m/m [PLN/m³]"), L("Zmiana m/m [%]", "Change m/m [%]")
        table = pd.DataFrame({
            c_month: arch["label"] + arch["partial"].map({True: L(" (w toku)", " (in progress)"), False: ""}),
            c_avg_m3: arch["mean"].round(0),
            c_avg_l: (arch["mean"] / 1000).round(3),
            c_mm: arch["diff"].round(0),
            c_mm_pct: (arch["pct"] * 100).round(1),
            "Min [PLN/m³]": arch["min"].round(0),
            "Max [PLN/m³]": arch["max"].round(0),
        }).iloc[::-1]
        cols_help = ui.table_legend({
            c_month: L("Miesiąc kalendarzowy. „(w toku)” = bieżący miesiąc, liczony do dziś – wynik jeszcze się zmieni.",
                       "Calendar month. “(in progress)” = current month, computed to date – the result will still change."),
            c_avg_m3: L("Średnia cena hurtowa Ekodiesel ORLEN w miesiącu, netto (bez VAT), za 1 m³ = 1000 l. "
                        "Ważona dniami: każdy dzień liczy się po cenie, która wtedy obowiązywała.",
                        "Average ORLEN Ekodiesel wholesale price in the month, net (excl. VAT), per 1 m³ = 1000 l. "
                        "Day-weighted: each day counts at the price in force on that day."),
            c_avg_l: L("Ta sama średnia za 1 litr (PLN/m³ ÷ 1000).", "The same average per 1 litre (PLN/m³ ÷ 1000)."),
            c_mm: L("m/m = miesiąc do miesiąca. O ile złotych na każde 1000 l średnia była wyższa (▲) "
                    "lub niższa (▼) niż w poprzednim miesiącu. Przykład: ▲ +724 = 1000 l droższe średnio "
                    "o 724 zł, czyli o ok. 0,72 zł na litrze.",
                    "m/m = month on month. How many PLN per 1000 l the average was higher (▲) or lower (▼) "
                    "than in the previous month. Example: ▲ +724 = 1000 l cost PLN 724 more on average, "
                    "i.e. about PLN 0.72 per litre."),
            c_mm_pct: L("Ta sama zmiana w procentach. Przykład: ▲ +11,1 = średnia wyższa o 11,1% niż miesiąc wcześniej.",
                        "The same change in percent. Example: ▲ +11.1 = the average is 11.1% higher than a month earlier."),
            "Min [PLN/m³]": L("Najniższa cena hurtowa, która obowiązywała w tym miesiącu.",
                              "The lowest wholesale price in force during the month."),
            "Max [PLN/m³]": L("Najwyższa cena hurtowa, która obowiązywała w tym miesiącu.",
                              "The highest wholesale price in force during the month."),
        }, [ui.trend_note(), L("Szybki przelicznik: zmiana w PLN/m³ ÷ 1000 = zmiana w zł na litrze; "
                               "× liczba m³ w miesiącu = wpływ na koszt paliwa floty.",
                               "Quick conversion: change in PLN/m³ ÷ 1000 = change in PLN per litre; "
                               "× m³ per month = impact on the fleet's fuel cost.")])
        st.dataframe(
            ui.trend_table(table, {c_mm: (0, ""), c_mm_pct: (1, "")},
                           {c_avg_m3: "{:.0f}", c_avg_l: "{:.3f}",
                            "Min [PLN/m³]": "{:.0f}", "Max [PLN/m³]": "{:.0f}"}),
            hide_index=True, width="stretch", height=38 + 35 * min(len(table), 12), column_config=cols_help,
        )
        st.caption(ui.trend_note())
        st.download_button(
            L("Pobierz archiwum (CSV do Excela)", "Download archive (CSV for Excel)"), icon=":material/download:",
            data=(table.to_csv(index=False, sep=",", decimal=".") if i18n.en()
                  else table.to_csv(index=False, sep=";", decimal=",")).encode("utf-8-sig"),
            file_name=L("orlen_ekodiesel_srednie_miesieczne", "orlen_ekodiesel_monthly_averages")
                      + f"_{date.today():%Y-%m-%d}.csv", mime="text/csv",
        )
        st.caption(L(
            "Średnia ważona dniami: notowanie obowiązuje do kolejnej zmiany, więc każdy dzień miesiąca liczy się "
            "po cenie, która w nim obowiązywała (nie średnia z samych zmian cennika). Ceny netto (bez VAT), PLN/m³ w 15°C. "
            "Bieżący miesiąc – do dziś, jaśniejszy słupek.",
            "Day-weighted average: a quote holds until the next change, so each day of the month counts at the price "
            "in force on that day (not an average of the price-list changes alone). Net prices (excl. VAT), PLN/m³ at 15°C. "
            "Current month – to date, lighter bar."
        ))

# ================================================================ ARA
with t_ara:
    st.subheader("ARA – ICE Low Sulphur Gasoil")
    st.caption(L(
        "Automatycznie z OilPriceAPI (kod GASOIL_USD, ICE LS Gasoil), **USD/t**, przez `update_data.py`: "
        "ostatnia transakcja dnia (uruchomienie po zamknięciu ICE ≈ cena zamknięcia; wcześniej wartość śródsesyjna). "
        "Notowania ze źródłem „średnia dzienna” pochodzą z jednorazowego importu historii – to nie są ceny zamknięcia. "
        "Źródło każdego punktu widać w dymku wykresu.",
        "Automatically from OilPriceAPI (code GASOIL_USD, ICE LS Gasoil), **USD/t**, via `update_data.py`: "
        "the last trade of the day (a run after the ICE close ≈ closing price; earlier = intraday value). "
        "Quotes with the “daily average” source come from a one-off history import – they are not closing prices. "
        "The source of each point is shown in the chart tooltip."
    ))
    last_h = None if hist.empty else hist.iloc[-1]
    a_usd = "—" if last_h is None else ui.num(last_h["ara_usd"], 2)
    a_fx = "—" if last_h is None else ui.num(last_h["fx"], 4)
    a_pln = "—" if last_h is None else ui.num(last_h["ara_pln"])
    a_day = "" if last_h is None else f" · {last_h['date']:%d.%m.%Y}"
    ui.html(f"""
    <div class="formula">
      <div class="box"><small>ICE LS Gasoil (ARA){a_day}</small><b class="num">{a_usd}</b><span class="u">USD/t</span></div>
      <div class="op">×</div>
      <div class="box"><small>{L("Kurs USD/PLN", "USD/PLN rate")}</small><b class="num">{a_fx}</b><span class="u">PLN</span></div>
      <div class="op">÷</div>
      <div class="box"><small>{L("m³ w tonie ON (0,845 kg/l)", "m³ per tonne of diesel (0.845 kg/l)")}</small><b class="num">{ui.num(M3_PER_T, 4)}</b><span class="u">m³/t</span></div>
      <div class="op">=</div>
      <div class="box res"><small>{L("ARA w PLN – do porównania z ORLEN", "ARA in PLN – to compare with ORLEN")}</small><b class="num">{a_pln}</b><span class="u">PLN/m³</span></div>
    </div>""")
    with st.expander(L("Czym jest ARA i jak to czytać", "What ARA is and how to read it")):
        ui.html(L("""
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
    </div>""", """
    <div class="explain">
      <div>
        <h4>What it is</h4>
        <p><b>ARA</b> stands for the ports of Amsterdam–Rotterdam–Antwerp – Western Europe's largest hub of
        refineries, terminals and fuel trading.</p>
        <p><b>ICE Low Sulphur Gasoil</b> is a contract for low-sulphur diesel delivered in ARA, traded on the
        ICE exchange in London, in <b>USD per tonne</b>. It is the benchmark for wholesale diesel prices in Europe.</p>
      </div>
      <div>
        <h4>How to read it</h4>
        <ul>
          <li><b>ARA rises</b> – the market cost of fuel goes up; ORLEN wholesale usually follows with a delay.</li>
          <li><b>ARA falls</b> – there is room for wholesale price cuts.</li>
          <li>The <b>dollar rate</b> matters too: a cheaper dollar lowers ARA in PLN even at the same USD price.</li>
          <li>Whether ORLEN is expensive relative to ARA – see the <b>Premium</b> tab.</li>
        </ul>
      </div>
      <div>
        <h4>Watch out for</h4>
        <ul>
          <li>This is <b>not our purchase price</b> – it is an exchange price, without excise duty, levies, logistics and margin.</li>
          <li>The price moves during the session; the app stores the <b>last price of the day</b>, fetched
          after the exchange closes (around 18:30 Polish time).</li>
          <li>The first ~30 days of history are <b>daily averages</b> from the import, not closing prices.</li>
        </ul>
      </div>
    </div>"""))
    source_warning(ara_manual.SERIES)
    with st.expander(L("Wpis ręczny (awaryjnie, gdy API nie działa)", "Manual entry (fallback when the API is down)")), \
            st.form("ara_form", clear_on_submit=True):
        st.caption(L(f"Cena z [TradingView ICEEUR:ULS1!]({ara_manual.URL}). Kolejne pobranie z API nadpisze wpis z tego samego dnia.",
                     f"Price from [TradingView ICEEUR:ULS1!]({ara_manual.URL}). The next API fetch overwrites an entry for the same day."))
        c1, c2, c3 = st.columns([2, 2, 1], vertical_alignment="bottom")
        ara_day = c1.date_input(L("Data notowania", "Quote date"), value=date.today(), format="YYYY-MM-DD")
        ara_price = c2.number_input(L("Cena zamknięcia / ostatnia [USD/t]", "Closing / last price [USD/t]"),
                                    min_value=0.0, step=0.25, format="%.2f")
        if c3.form_submit_button(L("Zapisz", "Save")):
            try:
                ara_manual.save(ara_day, ara_price)
                st.cache_data.clear()
                st.success(L(f"Zapisano {ara_day}: {ara_price:.2f} USD/t", f"Saved {ara_day}: {ara_price:.2f} USD/t"))
            except ValueError as e:
                st.error(L(str(e), "The price must be positive."))

    if ara_df.empty:
        st.error(L("Brak notowań ARA w bazie. Ustaw OILPRICEAPI_KEY i kliknij „Odśwież dane” albo użyj wpisu ręcznego.",
                   "No ARA quotes in the database. Set OILPRICEAPI_KEY and click “Refresh data”, or use a manual entry."))
    else:
        series_section(ara_manual.SERIES, "ICE LS Gasoil (ARA)", ara_manual.UNIT, show_warning=False,
                       tips=[L("Cena giełdowa w USD/t – bez przeliczenia na PLN i bez podatków.",
                               "Exchange price in USD/t – not converted to PLN and without taxes."),
                             L("Źródło „średnia dzienna” w dymku = import historii, nie cena zamknięcia.",
                               "A “daily average” source in the tooltip = history import, not a closing price.")])
        # od kiedy historia jest kompletna (dni robocze pon–pt bez luk; święta ICE liczone jako luki)
        have = set(ara_df["date"].dt.date)
        weekdays = pd.bdate_range(ara_df["date"].min(), ara_df["date"].max()).date
        missing = [d for d in weekdays if d not in have]
        complete_from = ara_df["date"].min().date() if not missing else next(
            (d for d in weekdays if d > missing[-1]), ara_df["date"].max().date()
        )
        if i18n.en():
            st.caption(
                f"History in the database since {ara_df['date'].min():%Y-%m-%d} ({len(ara_df)} quotes; the API history import "
                f"covers ~30 days, then daily snapshots). History complete (Mon–Fri) since **{complete_from}**. "
                f"Missing business days: {len(missing)}" + (f" (latest: {missing[-1]})" if missing else "")
                + ". ICE holidays are counted as gaps."
            )
        else:
            st.caption(
                f"Historia w bazie od {ara_df['date'].min():%Y-%m-%d} ({len(ara_df)} notowań; import historii z API obejmuje ~30 dni, dalej codzienne snapshoty). "
                f"Historia kompletna (pon–pt) od **{complete_from}**. Brakujące dni robocze: {len(missing)}"
                + (f" (ostatni: {missing[-1]})" if missing else "")
                + ". Dni świąteczne ICE są liczone jako braki."
            )

# ================================================================ WOB – benchmark cen na stacjach
with t_wob:
    st.subheader(L("Weekly Oil Bulletin – ceny ON na stacjach (benchmark)", "Weekly Oil Bulletin – diesel pump prices (benchmark)"))
    source_warning(wob.LOG_SERIES)
    c1, c2 = st.columns(2)
    w_variant = c1.segmented_control(L("Cena", "Price"), list(wob.LABELS), default="netto", key="wob_variant",
                                     format_func=variant_lbl) or "netto"
    w_unit = c2.segmented_control(L("Jednostka", "Unit"), ["PLN/m³", "EUR/l"], default="PLN/m³", key="wob_unit") or "PLN/m³"
    w_dec = 0 if w_unit == "PLN/m³" else 3
    w_pl, w_eu = wob_series(w_variant, "PL", w_unit), wob_series(w_variant, "EU", w_unit)
    w_last = None if w_pl.empty else w_pl.index[-1]
    w_eu_ok = w_last is not None and w_last in w_eu.index
    f_pl = "—" if w_last is None else ui.num(w_pl.iloc[-1], w_dec)
    f_eu = ui.num(w_eu[w_last], w_dec) if w_eu_ok else "—"
    f_diff = ui.num(w_pl.iloc[-1] - w_eu[w_last], w_dec, sign=True) if w_eu_ok else "—"
    f_pct = f"{ui.num((w_pl.iloc[-1] / w_eu[w_last] - 1) * 100, 1, sign=True)}%" if w_eu_ok else ""
    f_day = "" if w_last is None else f" · {w_last:%d.%m.%Y}"
    w_lbl = variant_lbl(w_variant).lower()
    ui.html(f"""
    <div class="formula">
      <div class="box"><small>{L("Polska – stacje", "Poland – pumps")}, {w_lbl}{f_day}</small><b class="num">{f_pl}</b><span class="u">{w_unit}</span></div>
      <div class="op">−</div>
      <div class="box"><small>{L("Średnia UE-27 (ważona)", "EU-27 average (weighted)")}, {w_lbl}</small><b class="num">{f_eu}</b><span class="u">{w_unit}</span></div>
      <div class="op">=</div>
      <div class="box res"><small>{L("Polska vs średnia UE", "Poland vs EU average")}</small><b class="num">{f_diff}</b><span class="u">{w_unit}</span>
        <span class="u">{f_pct}</span></div>
    </div>""")
    with st.expander(L("Czym jest Weekly Oil Bulletin i jak to czytać", "What the Weekly Oil Bulletin is and how to read it")):
        ui.html(L("""
    <div class="explain">
      <div>
        <h4>Co to jest</h4>
        <p><b>Weekly Oil Bulletin</b> (WOB) Komisji Europejskiej – oficjalne średnie krajowe ceny paliw na stacjach
        we wszystkich krajach UE-27. Stan na poniedziałek, publikacja zwykle w czwartek, historia od 2005 r.</p>
        <p>Dane pobieramy <b>bezpośrednio z pliku KE</b>. Wersję w PLN przeliczamy kursem z tego samego biuletynu;
        1 m³ = 1000 l.</p>
      </div>
      <div>
        <h4>Dlaczego domyślnie „bez podatków”</h4>
        <ul>
          <li>Akcyza i VAT są różne w każdym kraju i zmieniają się decyzją rządu, nie rynku. Cena
          <b>bez podatków</b> pokazuje sam koszt paliwa: produkt, logistykę i marże.</li>
          <li>Tylko wersja bez podatków jest porównywalna z <b>giełdą ARA</b> – obie nie zawierają podatków.
          Odstęp między nimi to koszt dostawy i marże łańcucha (rafineria → hurt → stacja).</li>
          <li><b>Z podatkami</b> = cena z pylonu, którą płaci kierowca.</li>
        </ul>
      </div>
      <div>
        <h4>Jak używać</h4>
        <ul>
          <li><b>Polska drożej niż UE bez podatków</b> – drogi jest sam produkt/logistyka w PL, a nie tylko podatki.</li>
          <li>WOB jest często wskaźnikiem w <b>klauzulach paliwowych</b> (dopłata paliwowa w umowach przewozowych)
          – historia tygodniowa od 2005 r. pozwala ją policzyć i sprawdzić.</li>
          <li><b>Stacje bez podatków vs ARA</b> (niżej) – ile łańcuch dostaw dolicza do ceny giełdowej.</li>
        </ul>
      </div>
    </div>""", """
    <div class="explain">
      <div>
        <h4>What it is</h4>
        <p>The European Commission's <b>Weekly Oil Bulletin</b> (WOB) – official national average pump prices
        in all EU-27 countries. Prices as of Monday, usually published on Thursday, history since 2005.</p>
        <p>We fetch the data <b>directly from the EC file</b>. The PLN version uses the exchange rate from the same
        bulletin; 1 m³ = 1000 l.</p>
      </div>
      <div>
        <h4>Why “without taxes” by default</h4>
        <ul>
          <li>Excise duty and VAT differ in every country and change by government decision, not by the market.
          The price <b>without taxes</b> shows the cost of the fuel itself: product, logistics and margins.</li>
          <li>Only the without-taxes version is comparable with the <b>ARA exchange</b> – neither includes taxes.
          The gap between them is the delivery cost and supply-chain margins (refinery → wholesale → pump).</li>
          <li><b>With taxes</b> = the pump price the driver pays.</li>
        </ul>
      </div>
      <div>
        <h4>How to use it</h4>
        <ul>
          <li><b>Poland pricier than the EU without taxes</b> – the product/logistics itself is expensive in PL, not just the taxes.</li>
          <li>The WOB is often the index in <b>fuel clauses</b> (fuel surcharge in transport contracts)
          – the weekly history since 2005 lets you calculate and check it.</li>
          <li><b>Pumps without taxes vs ARA</b> (below) – how much the supply chain adds to the exchange price.</li>
        </ul>
      </div>
    </div>"""))
    st.write("")

    if w_pl.empty:
        st.error(L("Brak danych biuletynu KE w bazie. Kliknij „Odśwież dane” (pierwsze pobranie ~4 MB, kilka sekund).",
                   "No EC bulletin data in the database. Click “Refresh data” (first download ~4 MB, a few seconds)."))
    else:
        st.markdown(L("#### Historia – Polska vs średnia UE-27", "#### History – Poland vs EU-27 average"))
        show_ara = w_variant == "netto" and w_unit == "PLN/m³" and not hist.empty
        rng = range_picker("wob", default="1Y")
        if rng:
            sel = lambda s: s[(s.index.date >= rng[0]) & (s.index.date <= rng[1])]  # noqa: E731
            p_pl, p_eu = sel(w_pl), sel(w_eu)
            if p_pl.empty:
                st.info(L("Brak notowań w wybranym zakresie.", "No quotes in the selected range."))
            else:
                items = [
                    ("line-markers", C["wob"], L(f"<b>Polska</b> – średnia krajowa na stacjach, {w_lbl}",
                                                 f"<b>Poland</b> – national average at the pump, {w_lbl}")),
                    ("dash", C["muted"], L(f"<b>Średnia UE-27</b> (ważona), {w_lbl}", f"<b>EU-27 average</b> (weighted), {w_lbl}")),
                ]
                if show_ara:
                    items.append(("line", C["ara"], L("<b>Giełda ARA</b> w PLN/m³ – też bez podatków; odstęp = logistyka i marże",
                                                      "<b>ARA exchange</b> in PLN/m³ – also without taxes; gap = logistics and margins")))
                ui.legend(items, [
                    L("Notowania tygodniowe (poniedziałek) – linia łączy kolejne biuletyny.",
                      "Weekly quotes (Monday) – the line connects successive bulletins."),
                    L("Fioletowa <b>nad</b> przerywaną – w Polsce drożej niż średnio w UE.",
                      "Purple <b>above</b> the dashed line – Poland is pricier than the EU average."),
                    L("ARA widać tylko w wariancie „Bez podatków” i jednostce PLN/m³ – tylko wtedy porównanie jest uczciwe.",
                      "ARA is shown only for “Without taxes” in PLN/m³ – only then is the comparison fair."),
                    range_tip(),
                ])
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=p_eu.index, y=p_eu.values, name=L("Średnia UE-27", "EU-27 average"), mode="lines",
                    line=dict(width=1.6, color=C["muted"], dash="dash"),
                    hovertemplate=L("UE-27", "EU-27") + " %{y:,." + str(w_dec) + "f} " + w_unit + "<extra></extra>",
                ))
                if show_ara:
                    h_part = in_range(hist, rng)
                    if not h_part.empty:
                        fig.add_trace(go.Scatter(
                            x=h_part["date"], y=h_part["ara_pln"], name="ARA (PLN/m³)", mode="lines",
                            line=dict(width=1.6, color=C["ara"]),
                            hovertemplate="ARA %{y:,.0f} PLN/m³<extra></extra>",
                        ))
                fig.add_trace(go.Scatter(
                    x=p_pl.index, y=p_pl.values, name=L("Polska", "Poland"), mode="lines+markers",
                    line=dict(width=2.2, color=C["wob"]), marker=dict(size=4),
                    hovertemplate=L("Polska", "Poland") + " %{y:,." + str(w_dec) + "f} " + w_unit + "<extra></extra>",
                ))
                ui.style_fig(fig, 400, yaxis_title=w_unit, hovermode="x unified",
                             legend=dict(orientation="h", y=1.08, x=0))
                st.plotly_chart(fig, width="stretch")

        with st.expander(L("Tabela – ostatnie 12 biuletynów", "Table – last 12 bulletins")):
            t = pd.DataFrame({"pl": w_pl, "eu": w_eu}).dropna(subset=["pl"]).tail(13)
            t["chg"] = t["pl"].diff()
            t["vs_eu"] = t["pl"] - t["eu"]
            t = t.iloc[1:].iloc[::-1] if len(t) > 1 else t
            c_bul, c_pl = L("Biuletyn", "Bulletin"), f"{L('Polska', 'Poland')} [{w_unit}]"
            c_chg, c_eu = f"{L('Zmiana t/t', 'Change w/w')} [{w_unit}]", f"{L('UE-27', 'EU-27')} [{w_unit}]"
            c_vs = f"{L('PL vs UE', 'PL vs EU')} [{w_unit}]"
            table = pd.DataFrame({
                c_bul: t.index.strftime("%Y-%m-%d"),
                c_pl: t["pl"].round(w_dec).values,
                c_chg: t["chg"].round(w_dec).values,
                c_eu: t["eu"].round(w_dec).values,
                c_vs: t["vs_eu"].round(w_dec).values,
            })
            cols_help = ui.table_legend({
                c_bul: L("Data notowania (poniedziałek); KE publikuje biuletyn zwykle w czwartek.",
                         "Quote date (Monday); the EC usually publishes the bulletin on Thursday."),
                c_pl: L(f"Średnia krajowa cena ON na stacjach w Polsce, {w_lbl}.",
                        f"National average diesel pump price in Poland, {w_lbl}."),
                c_chg: L("t/t = tydzień do tygodnia, względem poprzedniego biuletynu.",
                         "w/w = week on week, vs the previous bulletin."),
                c_eu: L("Średnia UE-27 ważona sprzedażą, liczona przez KE.", "Sales-weighted EU-27 average, computed by the EC."),
                c_vs: L("▲ = w Polsce drożej niż średnio w UE, ▼ = taniej.",
                        "▲ = Poland pricier than the EU average, ▼ = cheaper."),
            }, [ui.trend_note()])
            st.dataframe(ui.trend_table(
                table, {c_chg: (w_dec, ""), c_vs: (w_dec, "")},
                {c_pl: f"{{:.{w_dec}f}}", c_eu: f"{{:.{w_dec}f}}"},
            ), hide_index=True, width="stretch", column_config=cols_help)

        st.markdown(L("#### Stacje PL bez podatków vs giełda ARA", "#### PL pumps without taxes vs ARA exchange"))
        w_pl_m3 = wob_series("netto", "PL", "PLN/m³")
        spread = pd.DataFrame()
        if not hist.empty and not w_pl_m3.empty:
            spread = pd.DataFrame({"date": w_pl_m3.index, "wob": w_pl_m3.values})
            a_ = hist[["date", "ara_pln", "ara_usd", "fx"]].assign(ara_date=hist["date"]).sort_values("date")
            spread = pd.merge_asof(spread, a_, on="date", tolerance=pd.Timedelta(days=5)).dropna(subset=["ara_pln"])
            spread["spread"] = spread["wob"] - spread["ara_pln"]
        if spread.empty:
            st.info(L("Porównanie wymaga notowań ARA z dnia biuletynu (±5 dni) – klucz OilPriceAPI lub wpis ręczny w zakładce ARA. "
                      "Historia ARA w bazie zaczyna się od pierwszego pobrania z API, więc punkty dojdą z kolejnymi biuletynami.",
                      "The comparison needs ARA quotes from the bulletin day (±5 days) – OilPriceAPI key or a manual entry in the "
                      "ARA tab. ARA history in the database starts with the first API fetch, so points will be added with new bulletins."))
        else:
            s_last = spread.iloc[-1]
            ui.html(f"""
    <div class="formula">
      <div class="box"><small>{L("Stacje PL bez podatków · biuletyn", "PL pumps without taxes · bulletin")} {s_last['date']:%d.%m.%Y}</small>
        <b class="num">{ui.num(s_last['wob'])}</b><span class="u">PLN/m³</span></div>
      <div class="op">−</div>
      <div class="box"><small>{L("Giełda ARA w PLN/m³", "ARA exchange in PLN/m³")} · {s_last['ara_date']:%d.%m.%Y}</small>
        <b class="num">{ui.num(s_last['ara_pln'])}</b><span class="u">PLN/m³</span></div>
      <div class="op">=</div>
      <div class="box res"><small>{L("Odstęp: logistyka i marże łańcucha", "Gap: supply-chain logistics and margins")}</small><b class="num">{ui.num(s_last['spread'], sign=True)}</b>
        <span class="u">PLN/m³</span></div>
    </div>""")
            avg_s = spread["spread"].mean()
            ui.legend([
                ("line-markers", C["wob"], L("<b>Odstęp</b> stacje PL bez podatków − ARA, na dzień biuletynu",
                                             "<b>Gap</b> PL pumps without taxes − ARA, on the bulletin day")),
                ("dash", C["muted"], L(f"<b>Średnia</b> z dostępnych punktów ({ui.num(avg_s)} PLN/m³)",
                                       f"<b>Average</b> of available points ({ui.num(avg_s)} PLN/m³)")),
            ], [
                L("Obie ceny są <b>bez podatków</b>, więc zmiany akcyzy i VAT nie przesuwają wykresu.",
                  "Both prices are <b>without taxes</b>, so excise and VAT changes don't shift the chart."),
                L("Odstęp <b>rośnie</b> – stacje/hurt drożeją względem giełdy (lub nie nadążają za jej spadkiem).",
                  "Gap <b>widens</b> – pumps/wholesale get pricier vs the exchange (or lag behind its fall)."),
                L("Odstęp <b>maleje</b> – giełda drożeje szybciej, niż ceny dochodzą do stacji.",
                  "Gap <b>narrows</b> – the exchange rises faster than prices reach the pumps."),
            ])
            fig = go.Figure(go.Scatter(
                x=spread["date"], y=spread["spread"], mode="lines+markers",
                line=dict(width=2, color=C["wob"]), marker=dict(size=6),
                customdata=spread[["wob", "ara_pln"]].to_numpy(),
                hovertemplate="%{x|%Y-%m-%d}<br>" + L("Odstęp", "Gap") + " %{y:+,.0f} PLN/m³<br>"
                              + L("stacje", "pumps") + " %{customdata[0]:,.0f} − ARA %{customdata[1]:,.0f}<extra></extra>",
            ))
            fig.add_hline(y=avg_s, line=dict(width=1, dash="dash", color=C["muted"]))
            ui.style_fig(fig, 320, yaxis_title="PLN/m³", hovermode="closest", showlegend=False)
            st.plotly_chart(fig, width="stretch")
            st.caption(L(
                f"{len(spread)} punktów: biuletyn KE (poniedziałek) i ARA z tego dnia lub ostatniego wcześniejszego notowania "
                "(maks. 5 dni). Odstęp obejmuje transport, magazynowanie, marżę hurtu i stacji – bez akcyzy, opłat i VAT. "
                "Nie porównujemy tu z hurtem ORLEN: jego cena zawiera akcyzę i opłatę paliwową, a stawki podatków w Polsce "
                "zmieniały się w 2026 r. kilka razy, więc takie porównanie dawałoby fałszywy wynik.",
                f"{len(spread)} points: EC bulletin (Monday) and ARA from that day or the latest earlier quote (max. 5 days). "
                "The gap covers transport, storage, wholesale and pump margins – without excise duty, levies and VAT. "
                "We don't compare with ORLEN wholesale here: its price includes excise duty and the fuel levy, and Polish tax "
                "rates changed several times in 2026, so such a comparison would give a false result."
            ))

# ================================================================ PREMIA PL vs ARA
with t_prem:
    st.subheader(L("Premia PL vs ARA – czym jest i jak ją czytać", "PL vs ARA premium – what it is and how to read it"))
    last_h = None if hist.empty else hist.iloc[-1]
    f_orl = "—" if last_h is None else ui.num(last_h["orlen"])
    f_ara = "—" if last_h is None else ui.num(last_h["ara_pln"])
    f_prem = "—" if last_h is None else ui.num(last_h["premium"])
    f_day = "" if last_h is None else f" · {last_h['date']:%d.%m.%Y}"
    ui.html(f"""
    <div class="formula">
      <div class="box"><small>{L("Hurt ORLEN (Ekodiesel, netto)", "ORLEN wholesale (Ekodiesel, net)")}{f_day}</small><b class="num">{f_orl}</b><span class="u">PLN/m³</span></div>
      <div class="op">−</div>
      <div class="box"><small>{L("Giełda ARA przeliczona na PLN/m³", "ARA exchange converted to PLN/m³")}</small><b class="num">{f_ara}</b><span class="u">PLN/m³</span></div>
      <div class="op">=</div>
      <div class="box res"><small>{L("Premia PL vs ARA", "PL vs ARA premium")}</small><b class="num">{f_prem}</b><span class="u">PLN/m³</span></div>
    </div>""")
    with st.expander(L("Czym jest premia i jak ją czytać", "What the premium is and how to read it")):
        ui.html(L(f"""
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
    </div>""", f"""
    <div class="explain">
      <div>
        <h4>What it is</h4>
        <p>How much more 1 m³ of diesel costs at ORLEN wholesale than the same m³ on the ARA exchange
        (Amsterdam–Rotterdam–Antwerp) – Europe's benchmark for diesel prices.</p>
        <p>ARA is quoted in USD per tonne, so we convert it to PLN/m³:
        <span class="num">USD/t × USD/PLN rate ÷ 1.1834</span> (1 t of diesel ≈ 1,183 l).</p>
      </div>
      <div>
        <h4>How to read it</h4>
        <p>Don't look at the premium level alone – look at how it compares with <b>its average</b>
        (the dashed line on the chart):</p>
        <div class="scale">
          <div><i style="background:{C['up']}"></i><span><b>Above average</b> – ORLEN wholesale is expensive vs the market. There is room for a cut; don't stock up.</span></div>
          <div><i style="background:{C['muted']}"></i><span><b>Close to average</b> (±2%) – typical pricing.</span></div>
          <div><i style="background:{C['down']}"></i><span><b>Below average</b> – wholesale is cheap vs the market; a relatively good moment for a larger purchase.</span></div>
        </div>
      </div>
      <div>
        <h4>Watch out for</h4>
        <ul>
          <li>The premium is <b>not ORLEN's margin</b>. A large part is taxes and levies included in the wholesale price:
          excise duty, the fuel levy and the stockholding fee. Plus logistics and margin. VAT is not included.</li>
          <li>When excise duty or the fuel levy changes (usually on 1 January), the premium jumps –
          compare periods on the same side of the change.</li>
          <li>ORLEN follows the exchange with a delay, so a jump in the premium often means ORLEN “hasn't caught up” with the market yet.</li>
        </ul>
      </div>
    </div>"""))
    with st.expander(L("Słowniczek pojęć", "Glossary")):
        st.markdown(L(
            "- **Ekodiesel ORLEN (hurt)** – cena hurtowa oleju napędowego publikowana przez ORLEN, w PLN za m³, "
            "bez VAT, dla paliwa w temperaturze 15°C. Obowiązuje do kolejnej zmiany cennika.\n"
            "- **ARA** – rejon portów Amsterdam–Rotterdam–Antwerpia, główny hub paliwowy Europy Zachodniej.\n"
            "- **ICE Low Sulphur Gasoil** – notowany na giełdzie ICE kontrakt na olej napędowy z dostawą w ARA, "
            "w USD za tonę. To ta „giełda” na wykresie.\n"
            "- **Przeliczenie t → m³** – przy gęstości 0,845 kg/l tona ON to ok. 1 183 l, czyli 1,1834 m³.\n"
            "- **Średnia** – na wykresie poniżej: średnia premii w wybranym zakresie; w „Sygnale dnia” na Przeglądzie: "
            "średnia z ostatnich 90 dni.\n"
            "- **Linia kropkowana** – punkty, w których ARA to średnia dzienna z importu historii, a nie cena "
            "zamknięcia; porównuj je ostrożnie.",
            "- **Ekodiesel ORLEN (wholesale)** – ORLEN's published wholesale diesel price, in PLN per m³, "
            "excl. VAT, for fuel at 15°C. Valid until the next price-list change.\n"
            "- **ARA** – the Amsterdam–Rotterdam–Antwerp port area, Western Europe's main fuel hub.\n"
            "- **ICE Low Sulphur Gasoil** – a diesel contract with delivery in ARA traded on the ICE exchange, "
            "in USD per tonne. This is the “exchange” on the chart.\n"
            "- **t → m³ conversion** – at a density of 0.845 kg/l a tonne of diesel is about 1,183 l, i.e. 1.1834 m³.\n"
            "- **Average** – on the chart below: the average premium in the selected range; in the “Signal of the day” "
            "on the Overview: the average of the last 90 days.\n"
            "- **Dotted line** – points where ARA is a daily average from the history import rather than a closing "
            "price; compare them with caution."
        ))
    st.write("")
    if hist.empty:
        st.info(L("Premia niedostępna: brak notowań ARA, Orlen lub USD/PLN.",
                  "Premium unavailable: missing ARA, Orlen or USD/PLN quotes."))
    else:
        ara_last = ara_df.iloc[-1]
        d = ara_last["date"]
        fx_row = fx_df[fx_df["date"] <= d].iloc[-1] if (fx_df["date"] <= d).any() else None
        orl_row = orlen_df[orlen_df["date"] <= d].iloc[-1] if (orlen_df["date"] <= d).any() else None
        if fx_row is None or orl_row is None:
            st.info(L(f"Brak notowania USD/PLN lub Orlen na dzień {d:%Y-%m-%d} lub wcześniej.",
                      f"No USD/PLN or Orlen quote on {d:%Y-%m-%d} or earlier."))
        else:
            calc = (f"ARA {ara_last['value']:.2f} USD/t ({d:%Y-%m-%d}) × USD/PLN {fx_row['value']:.4f} "
                    f"({fx_row['date']:%Y-%m-%d}) ÷ {M3_PER_T} m³/t")
            st.caption(L(
                f"Wyliczenie powyżej: {calc} (gęstość 0,845 kg/l). Orlen z {orl_row['date']:%Y-%m-%d}. "
                "Różnica obejmuje m.in. podatki i opłaty (akcyza, opłata paliwowa, zapasowa), logistykę i marżę – "
                "to nie jest czysta marża rafinerii.",
                f"Calculation above: {calc} (density 0.845 kg/l). Orlen from {orl_row['date']:%Y-%m-%d}. "
                "The difference includes taxes and levies (excise duty, fuel levy, stockholding fee), logistics and margin – "
                "it is not the refinery's pure margin."
            ))

        st.markdown(L("#### Historia premii", "#### Premium history"))
        rng = range_picker("premium")
        part = in_range(hist, rng) if rng else hist.iloc[0:0]
        if rng and part.empty:
            st.info(L("Brak punktów premii w wybranym zakresie.", "No premium points in the selected range."))
        elif not part.empty:
            avg = part["premium"].mean()
            now = part.iloc[-1]
            c1, c2, c3 = st.columns(3)
            c1.metric(f"{L('Premia', 'Premium')} {now['date']:%Y-%m-%d}", f"{now['premium']:+,.0f} PLN/m³".replace(",", " "))
            c2.metric(L("Średnia w zakresie", "Average in range"), f"{avg:+,.0f} PLN/m³".replace(",", " "))
            c3.metric(L("Bieżąca vs średnia", "Current vs average"), f"{now['premium'] - avg:+,.0f} PLN/m³".replace(",", " "))

            ui.legend([
                ("line-markers", C["orlen"], L("<b>Premia</b> w dniu notowania ARA (ARA = ostatnia cena dnia / wpis ręczny)",
                                               "<b>Premium</b> on the ARA quote day (ARA = last price of the day / manual entry)")),
                ("dot", C["orlen"], L("<b>Kropkowana</b> – ARA to średnia dzienna z importu historii; porównuj ostrożnie",
                                      "<b>Dotted</b> – ARA is a daily average from the history import; compare with caution")),
                ("dash", C["muted"], L("<b>Średnia premii</b> w wybranym zakresie", "<b>Average premium</b> in the selected range")),
            ], [
                L(f"<b style='color:{C['up']}'>Nad średnią</b> – hurt ORLEN drogi względem rynku, jest przestrzeń do obniżki.",
                  f"<b style='color:{C['up']}'>Above average</b> – ORLEN wholesale is expensive vs the market, there is room for a cut."),
                L(f"<b style='color:{C['down']}'>Pod średnią</b> – hurt tani względem rynku, korzystny moment na zakup.",
                  f"<b style='color:{C['down']}'>Below average</b> – wholesale is cheap vs the market, a good moment to buy."),
                L("Skok premii 1 stycznia to zwykle zmiana akcyzy/opłaty paliwowej, nie zmiana rynku.",
                  "A premium jump on 1 January is usually an excise/fuel levy change, not a market move."),
                L("Najedź na punkt – dymek pokazuje ORLEN, ARA w PLN i kurs USD/PLN.",
                  "Hover over a point – the tooltip shows ORLEN, ARA in PLN and the USD/PLN rate."),
                range_tip(),
            ])
            fig = go.Figure()
            is_avg = part["ara_source"] == oilpriceapi.SOURCE_AVG
            for mask, label, dash in [(is_avg, L("ARA = średnia dzienna (import historii)",
                                                 "ARA = daily average (history import)"), "dot"),
                                      (~is_avg, L("ARA = ostatnia cena dnia / wpis ręczny",
                                                  "ARA = last price of the day / manual entry"), "solid")]:
                seg = part[mask]
                if seg.empty:
                    continue
                fig.add_trace(go.Scatter(
                    x=seg["date"], y=seg["premium"], name=label, mode="lines+markers",
                    line=dict(width=2, color=C["orlen"], dash=dash), marker=dict(size=7),
                    customdata=seg[["orlen", "ara_pln", "ara_usd", "fx"]].to_numpy(),
                    hovertemplate="%{x|%Y-%m-%d}<br>" + L("Premia", "Premium") + " %{y:+,.0f} PLN/m³<br>Orlen %{customdata[0]:,.0f} − "
                                  "ARA %{customdata[1]:,.0f} PLN/m³<br>(ARA %{customdata[2]:,.2f} USD/t × USD/PLN "
                                  "%{customdata[3]:.4f})<extra>" + label + "</extra>",
                ))
            fig.add_hline(y=avg, line=dict(width=1, dash="dash", color=C["muted"]),
                          annotation_text=f"{L('średnia', 'average')} {ui.num(avg)}", annotation_position="top left",
                          annotation_font_color=C["muted"])
            ui.style_fig(fig, 380, yaxis_title=L("Premia [PLN/m³]", "Premium [PLN/m³]"), hovermode="closest",
                         legend=dict(orientation="h", y=1.1))
            st.plotly_chart(fig, width="stretch")
            n_avg = int(is_avg.sum())
            if i18n.en():
                st.caption(
                    f"{len(part)} points in range"
                    + (f", of which {n_avg} computed from the ARA daily average (dotted line) – compare with caution" if n_avg else "")
                    + ". A point only on ARA quote days; Orlen and USD/PLN from that day or the latest earlier quote."
                )
            else:
                st.caption(
                    f"{len(part)} punktów w zakresie"
                    + (f", w tym {n_avg} liczonych ze średniej dziennej ARA (linia kropkowana) – porównuj je ostrożnie" if n_avg else "")
                    + ". Punkt tylko w dni z notowaniem ARA; Orlen i USD/PLN z tego dnia lub ostatniego wcześniejszego notowania."
                )

# ================================================================ STACJE UE
with t_eu:
    st.subheader(L("Olej napędowy na stacjach w krajach UE", "Diesel at the pump in EU countries"))
    st.caption(L(
        "Źródło: [Weekly Oil Bulletin Komisji Europejskiej](" + wob.PAGE_URL + ") – oficjalne średnie krajowe ceny "
        "detaliczne, notowanie tygodniowe (poniedziałek), publikacja zwykle w czwartek. Te same dane pokazuje m.in. "
        "e-petrol.pl (strona blokuje automatyczne pobieranie, więc czytamy je u źródła). Biuletyn obejmuje tylko UE-27 – "
        "bez Norwegii, Szwajcarii, Ukrainy itp.",
        "Source: [European Commission Weekly Oil Bulletin](" + wob.PAGE_URL + ") – official national average retail "
        "prices, weekly quote (Monday), usually published on Thursday. The same data is shown e.g. by e-petrol.pl "
        "(the site blocks automated downloads, so we read it at the source). The bulletin covers the EU-27 only – "
        "no Norway, Switzerland, Ukraine etc."
    ))
    source_warning(wob.LOG_SERIES)
    c1, c2 = st.columns(2)
    eu_variant = c1.segmented_control(
        L("Cena", "Price"), list(wob.LABELS), default="netto", key="eu_variant",
        format_func=lambda v: variant_desc(v)[0].upper() + variant_desc(v)[1:],
    ) or "netto"
    eu_pln = (c2.segmented_control(L("Jednostka", "Unit"), ["EUR/l", "PLN/l"], default="EUR/l", key="eu_unit") or "EUR/l") == "PLN/l"
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
        f_share, f_day = f"{ui.num((pl_b - pl_n) / pl_b * 100)}% {L('ceny', 'of price')}", f" · {tax_day:%d.%m.%Y}"
    else:
        f_b = f_n = f_t = "—"
        f_share = f_day = ""
    ui.html(f"""
    <div class="formula">
      <div class="box res"><small>{L("Polska: cena na stacji (z podatkami)", "Poland: pump price (with taxes)")}{f_day}</small><b class="num">{f_b}</b><span class="u">{eu_unit}</span></div>
      <div class="op">=</div>
      <div class="box"><small>{L("Cena bez podatków (paliwo, logistyka, marża)", "Price without taxes (fuel, logistics, margin)")}</small><b class="num">{f_n}</b><span class="u">{eu_unit}</span></div>
      <div class="op">+</div>
      <div class="box"><small>{L("Podatki i opłaty: akcyza, opłaty, VAT", "Taxes and levies: excise, levies, VAT")}</small><b class="num">{f_t}</b><span class="u">{eu_unit}</span>
        <span class="u">{f_share}</span></div>
    </div>""")
    with st.expander(L("Skąd są te dane i co znaczy „z podatkami”", "Where this data comes from and what “with taxes” means")):
        ui.html(L("""
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
    </div>""", """
    <div class="explain">
      <div>
        <h4>Where this data comes from</h4>
        <p>From the European Commission's <b>Weekly Oil Bulletin</b>. Every EU country reports the
        <b>national average</b> diesel pump price each week – as of Monday, usually published on Thursday.</p>
        <p>Prices are given in EUR; the PLN version uses the exchange rate from the same bulletin.
        These are not prices from a specific station or a fuel card.</p>
      </div>
      <div>
        <h4>“With taxes” and “without taxes”</h4>
        <ul>
          <li><b>With taxes</b> – the pump price the driver pays: fuel + excise duty and other levies + VAT.</li>
          <li><b>Without taxes</b> – the fuel price alone: product cost on the market (e.g. ARA), transport,
          storage and the station margin. No excise duty, levies or VAT.</li>
        </ul>
        <p>The “Price” switch below changes the variant on all charts.</p>
      </div>
      <div>
        <h4>Why countries differ so much</h4>
        <ul>
          <li><b>Taxes</b> – each country sets its own excise duty (the EU sets only a minimum) and its own VAT rate;
          some countries add CO₂ emission charges.</li>
          <li><b>The fuel itself</b> – supply and logistics costs (distance from refineries and ports), market
          competition and station margins.</li>
          <li>Which weighs more in a given week – see the “fuel vs taxes” chart below.</li>
        </ul>
      </div>
    </div>"""))
    st.write("")

    if eu.empty or "PL" not in eu:
        st.error(L("Brak danych biuletynu KE w bazie. Kliknij „Odśwież dane” (pierwsze pobranie ~4 MB, kilka sekund).",
                   "No EC bulletin data in the database. Click “Refresh data” (first download ~4 MB, a few seconds)."))
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
            f"{L('Polska', 'Poland')} {last_day:%Y-%m-%d}", f"{fmt.format(pl)} {eu_unit}",
            None if prev is None else f"{fmt.format(pl - prev['PL'])} {L('t/t', 'w/w')}", delta_color="inverse",
        )
        if pd.notna(eu_avg):
            m2.metric(L("Średnia UE-27 (ważona)", "EU-27 average (weighted)"), f"{fmt.format(eu_avg)} {eu_unit}")
            m3.metric(L("Polska vs średnia UE", "Poland vs EU average"), f"{pl - eu_avg:+,.{eu_dec}f} {eu_unit}",
                      f"{pl / eu_avg - 1:+.1%}", delta_color="inverse")
        pos = list(ranked.index).index("PL") + 1
        m4.metric(L("Pozycja Polski", "Poland's rank"), L(f"{pos}. z {len(ranked)}", f"{pos} of {len(ranked)}"),
                  help=L("1 = najtańszy kraj", "1 = cheapest country"))
        st.caption(L(f"Biuletyn z **{last_day:%Y-%m-%d}**", f"Bulletin of **{last_day:%Y-%m-%d}**") + f" · {variant_desc(eu_variant)}"
                   + (L(" · przeliczenie kursem EUR/PLN z tego samego biuletynu",
                        " · converted at the EUR/PLN rate from the same bulletin") if eu_pln else ""))

        names = [country(c) for c in ranked.index]
        ui.legend([
            ("marker-lg", C["orlen"], L("<b>Polska</b>", "<b>Poland</b>")),
            ("marker", "#5B7899", L("Pozostałe kraje UE – średnia krajowa cena na stacjach",
                                    "Other EU countries – national average pump price")),
            ("vline", C["muted"], L("<b>Średnia UE-27</b> (ważona)", "<b>EU-27 average</b> (weighted)")),
        ], [
            L("Im <b>bardziej w lewo</b>, tym taniej. Kraje posortowane od najtańszego.",
              "The <b>further left</b>, the cheaper. Countries sorted from the cheapest."),
            L("Oś nie zaczyna się od zera – porównuj odległości między punktami.",
              "The axis doesn't start at zero – compare the distances between points."),
            L("Najedź na punkt – dymek pokazuje zmianę t/t i różnicę do Polski.",
              "Hover over a point – the tooltip shows the w/w change and the difference to Poland."),
            L("Przełączniki „Cena” i „Jednostka” wyżej zmieniają wariant (z/bez podatków, EUR/PLN).",
              "The “Price” and “Unit” switches above change the variant (with/without taxes, EUR/PLN)."),
        ])
        change = (ranked - prev[ranked.index]) if prev is not None else ranked * float("nan")
        # wykres punktowy, nie słupkowy: oś X nie startuje od zera, więc długość słupka by przekłamywała
        fig = go.Figure(go.Scatter(
            x=ranked.values, y=names, mode="markers",
            marker=dict(size=[14 if c == "PL" else 10 for c in ranked.index],
                        color=[C["orlen"] if c == "PL" else "#5B7899" for c in ranked.index]),
            customdata=pd.DataFrame({"chg": change.values, "vs_pl": (ranked - pl).values}).to_numpy(),
            hovertemplate="<b>%{y}</b><br>%{x:." + str(eu_dec) + "f} " + eu_unit
                          + "<br>" + L("t/t", "w/w") + " %{customdata[0]:+." + str(eu_dec) + "f}"
                          + "<br>" + L("vs Polska", "vs Poland") + " %{customdata[1]:+." + str(eu_dec) + "f}<extra></extra>",
        ))
        if pd.notna(eu_avg):
            fig.add_vline(x=eu_avg, line=dict(width=1, dash="dash", color=C["muted"]),
                          annotation_text=f"{L('średnia UE', 'EU average')} {ui.num(eu_avg, eu_dec)}", annotation_position="top",
                          annotation_font_color=C["muted"])
        ui.style_fig(
            fig, max(420, 22 * len(ranked)), margin=dict(l=10, r=10, t=30, b=10), hovermode="closest",
            xaxis=dict(title=eu_unit), yaxis=dict(autorange="reversed", showgrid=True),
        )
        st.plotly_chart(fig, width="stretch")
        st.caption(L("Posortowane od najtańszego; Polska wyróżniona. Oś X nie zaczyna się od zera – porównuj odległości między punktami.",
                     "Sorted from the cheapest; Poland highlighted. The X axis doesn't start at zero – compare the distances between points."))

        with st.expander(L("Tabela – wszystkie kraje", "Table – all countries")):
            c_cty, c_price = L("Kraj", "Country"), f"{L('Cena', 'Price')} [{eu_unit}]"
            c_chg, c_vs = f"{L('Zmiana t/t', 'Change w/w')} [{eu_unit}]", f"{L('vs Polska', 'vs Poland')} [{eu_unit}]"
            c_vs1000 = f"{L('vs Polska na 1000 l', 'vs Poland per 1000 l')} [{eu_unit[:3]}]"
            table = pd.DataFrame({
                c_cty: names,
                c_price: ranked.round(eu_dec).values,
                c_chg: change.round(eu_dec).values,
                c_vs: (ranked - pl).round(eu_dec).values,
                c_vs1000: ((ranked - pl) * 1000).round(0).values,
            })
            cols_help = ui.table_legend({
                c_cty: L("Kraj UE-27 z biuletynu KE.", "EU-27 country from the EC bulletin."),
                c_price: L("Średnia krajowa cena oleju napędowego na stacjach z ostatniego biuletynu, za 1 litr "
                           "(z podatkami lub bez – wg przełącznika „Cena” wyżej).",
                           "National average diesel pump price from the latest bulletin, per 1 litre "
                           "(with or without taxes – per the “Price” switch above)."),
                c_chg: L("t/t = tydzień do tygodnia. O ile litr podrożał (▲) lub potaniał (▼) względem poprzedniego biuletynu.",
                         "w/w = week on week. How much a litre got more expensive (▲) or cheaper (▼) vs the previous bulletin."),
                c_vs: L("Różnica ceny litra względem Polski. ▼ = w tym kraju taniej niż w PL, ▲ = drożej.",
                        "Price difference per litre vs Poland. ▼ = cheaper than in PL, ▲ = more expensive."),
                c_vs1000: L("Ta sama różnica na 1000 l: ile zaoszczędzisz (▼) lub dopłacisz (▲), "
                            "tankując 1000 l w tym kraju zamiast w Polsce. Przykład: ▼ −124 = 1000 l taniej o 124.",
                            "The same difference per 1000 l: how much you save (▼) or pay extra (▲) "
                            "refuelling 1000 l in this country instead of Poland. Example: ▼ −124 = 1000 l cheaper by 124."),
            }, [ui.trend_note()])
            st.dataframe(ui.trend_table(
                table,
                {c_chg: (eu_dec, ""), c_vs: (eu_dec, ""), c_vs1000: (0, "")},
                {c_price: f"{{:.{eu_dec}f}}"},
            ), hide_index=True, width="stretch", column_config=cols_help)
            st.caption(ui.trend_note() + L(
                " „vs Polska na 1000 l” – ile więcej (+) lub mniej (−) zapłacisz za 1000 l w danym kraju niż w Polsce "
                "przy średniej krajowej cenie.",
                " “vs Poland per 1000 l” – how much more (+) or less (−) you pay for 1000 l in a given country than in Poland "
                "at the national average price."))

        if tax_day is not None:
            st.markdown(L("#### Z czego składa się cena – paliwo vs podatki", "#### What the price is made of – fuel vs taxes"))
            codes_t = [c for c in eu_b.loc[tax_day].dropna().index
                       if c not in wob.AVERAGES and c in eu_n.columns and pd.notna(eu_n.at[tax_day, c])]
            b_t = eu_b.loc[tax_day, codes_t].sort_values()
            n_t = eu_n.loc[tax_day, b_t.index]
            tax_t = b_t - n_t
            names_t = [country(c) for c in b_t.index]
            op = [1.0 if c == "PL" else 0.6 for c in b_t.index]
            share = (tax_t / b_t * 100).to_numpy()
            ui.legend([
                ("bar", C["ara"], L("<b>Paliwo bez podatków</b> – produkt, logistyka, marża stacji",
                                    "<b>Fuel without taxes</b> – product, logistics, station margin")),
                ("bar", C["orlen"], L("<b>Podatki i opłaty</b> – akcyza, opłaty, VAT", "<b>Taxes and levies</b> – excise, levies, VAT")),
                ("bar-faded", C["neutral"], L("Inne kraje przygaszone, <b>Polska</b> w pełnym kolorze",
                                              "Other countries dimmed, <b>Poland</b> in full colour")),
            ], [
                L("Cały słupek = cena na stacji z podatkami; oś od zera, długości można porównywać.",
                  "The whole bar = pump price with taxes; the axis starts at zero, so lengths can be compared."),
                L("Długi czerwony odcinek = drogo przez podatki; długi niebieski = drogie samo paliwo.",
                  "Long red segment = expensive because of taxes; long blue = the fuel itself is expensive."),
                L("Najedź na wiersz – dymek pokazuje udział podatków w cenie.",
                  "Hover over a row – the tooltip shows the tax share of the price."),
            ])
            fig = go.Figure([
                go.Bar(y=names_t, x=n_t.values, name=L("Paliwo bez podatków", "Fuel without taxes"), orientation="h",
                       marker=dict(color=C["ara"], opacity=op),
                       hovertemplate="%{y}<br>" + L("bez podatków", "without taxes") + " %{x:." + str(eu_dec) + "f} "
                                     + eu_unit + "<extra></extra>"),
                go.Bar(y=names_t, x=tax_t.values, name=L("Podatki i opłaty (akcyza, opłaty, VAT)", "Taxes and levies (excise, levies, VAT)"),
                       orientation="h", marker=dict(color=C["orlen"], opacity=op), customdata=share,
                       hovertemplate="%{y}<br>" + L("podatki", "taxes") + " %{x:." + str(eu_dec) + "f} " + eu_unit
                                     + " (%{customdata:.0f}% " + L("ceny", "of price") + ")<extra></extra>"),
            ])
            ui.style_fig(fig, max(460, 24 * len(b_t)), barmode="stack", hovermode="y unified",
                         xaxis=dict(title=f"{eu_unit} {L('z podatkami', 'with taxes')}"), yaxis=dict(autorange="reversed"),
                         legend=dict(orientation="h", y=1.04, x=0), margin=dict(l=10, r=10, t=40, b=10))
            st.plotly_chart(fig, width="stretch")
            hi, lo_ = tax_t.idxmax(), tax_t.idxmin()
            fuel_wider = (n_t.max() - n_t.min()) > (tax_t.max() - tax_t.min())
            f_hi, f_lo = f"{country(hi)} ({ui.num(tax_t[hi], eu_dec)} {eu_unit})", f"{country(lo_)} ({ui.num(tax_t[lo_], eu_dec)} {eu_unit})"
            f_nr, f_tr = f"{ui.num(n_t.max() - n_t.min(), eu_dec)} {eu_unit}", f"{ui.num(tax_t.max() - tax_t.min(), eu_dec)} {eu_unit}"
            f_pl_share = f"{ui.num(tax_t['PL'] / b_t['PL'] * 100)}%"
            st.caption(L(
                f"Biuletyn z {tax_day:%d.%m.%Y}, od najtańszego. Oś od zera, więc długości słupków można porównywać. "
                f"Najwięcej podatków: {f_hi}, najmniej: {f_lo}. Rozpiętość cen bez podatków: {f_nr}, podatków: {f_tr} – "
                "w tym tygodniu kraje bardziej różnią się "
                + ("ceną samego paliwa niż podatkami" if fuel_wider else "podatkami niż ceną samego paliwa")
                + f". Udział podatków w Polsce: {f_pl_share}. Polska wyróżniona.",
                f"Bulletin of {tax_day:%d.%m.%Y}, cheapest first. The axis starts at zero, so bar lengths can be compared. "
                f"Highest taxes: {f_hi}, lowest: {f_lo}. Spread of prices without taxes: {f_nr}, of taxes: {f_tr} – "
                "this week countries differ more "
                + ("in the fuel price itself than in taxes" if fuel_wider else "in taxes than in the fuel price itself")
                + f". Tax share in Poland: {f_pl_share}. Poland highlighted."
            ))

        st.markdown(L("#### Historia cen w wybranych krajach", "#### Price history in selected countries"))
        options = [c for c in wob.COUNTRIES if c in eu.columns]
        default = [c for c in ["PL", "DE", "CZ", "SK", "LT", "EU"] if c in options]
        picked = st.multiselect(L("Kraje (maks. 8)", "Countries (max. 8)"), options, default=default, max_selections=8,
                                format_func=country, key="eu_countries")
        rng = range_picker("eu")
        if picked and rng:
            part = eu[(eu.index.date >= rng[0]) & (eu.index.date <= rng[1])]
            if part.empty:
                st.info(L("Brak notowań w wybranym zakresie.", "No quotes in the selected range."))
            else:
                ui.legend([
                    ("line", CATEGORICAL[0], L("Linia ciągła = <b>kraj</b>; kolory w kolejności wyboru na liście "
                                               "(nazwy w legendzie nad wykresem)",
                                               "Solid line = <b>country</b>; colours in the order picked in the list "
                                               "(names in the legend above the chart)")),
                    ("dash", C["muted"], L("Linia przerywana = <b>średnia UE-27 / strefy euro</b>",
                                           "Dashed line = <b>EU-27 / euro area average</b>")),
                ], [
                    L("Notowania tygodniowe (poniedziałek) – linia łączy kolejne tygodnie.",
                      "Weekly quotes (Monday) – the line connects successive weeks."),
                    L("Klik w nazwę kraju w legendzie nad wykresem ukrywa/pokazuje jego linię.",
                      "Click a country name in the legend above the chart to hide/show its line."),
                    range_tip(),
                ])
                fig = go.Figure()
                for i, code in enumerate(picked):
                    col = part[code].dropna()
                    if col.empty:
                        continue
                    name = country(code)
                    fig.add_trace(go.Scatter(
                        x=col.index, y=col.values, name=name, mode="lines",
                        line=dict(width=2, color=CATEGORICAL[i], dash="dash" if code in wob.AVERAGES else "solid"),
                        hovertemplate="%{y:." + str(eu_dec) + "f} " + eu_unit + "<extra>" + name + "</extra>",
                    ))
                ui.style_fig(fig, 420, yaxis_title=eu_unit, hovermode="x unified",
                             legend=dict(orientation="h", y=1.1))
                st.plotly_chart(fig, width="stretch")
                st.caption(L("Notowania tygodniowe od 2005 r. Średnie UE i strefy euro linią przerywaną.",
                             "Weekly quotes since 2005. EU and euro area averages as dashed lines."))

# ================================================================ RYNKI: złoto / srebro / USD/PLN
with t_mkt:
    st.subheader(L("Złoto, srebro, USD/PLN", "Gold, silver, USD/PLN"))
    frames = {}
    cols = st.columns(3)
    for col, series in zip(cols, yahoo.TICKERS):
        unit, label = ticker(series)
        with col:
            source_warning(series)
            df = load(series)
            if df.empty:
                st.error(f"{label}: {L('brak danych w bazie.', 'no data in the database.')}")
                continue
            metric(df, label, unit, 4 if series == "usdpln" else 2, inverse=series == "usdpln")
            frames[series] = df

    rng = range_picker("cmp")
    if frames and rng:
        fig = go.Figure()
        rows, row_series = [], []
        c_ser, c_unit, c_start, c_end = L("Seria", "Series"), L("Jednostka", "Unit"), "Start", L("Koniec", "End")
        c_chg, c_rng = L("Zmiana", "Change"), L("Rozpiętość max/min", "Max/min spread")
        for series, df in frames.items():
            part = in_range(df, rng)
            if part.empty:
                continue
            unit, label = ticker(series)
            first, last = part.iloc[0], part.iloc[-1]
            fig.add_trace(
                go.Scatter(
                    x=part["date"], y=part["value"] / first["value"] * 100, name=label, mode="lines",
                    line=dict(width=2, color=SERIES_COLORS[series]), customdata=part["value"],
                    hovertemplate="%{y:.2f} (" + "%{customdata:,.4f} " + unit + ")<extra>" + label + "</extra>",
                )
            )
            row_series.append(series)
            rows.append({
                c_ser: label,
                c_unit: unit,
                c_start: f"{ui.num(first['value'], 4)} ({first['date']:%Y-%m-%d})",
                c_end: f"{ui.num(last['value'], 4)} ({last['date']:%Y-%m-%d})",
                c_chg: (last["value"] / first["value"] - 1) * 100,
                "Min": ui.num(part["value"].min(), 4),
                "Max": ui.num(part["value"].max(), 4),
                c_rng: f"{ui.num((part['value'].max() / part['value'].min() - 1) * 100, 2)}%",
            })
        if rows:
            ui.legend([
                ("line", SERIES_COLORS["gold"], L("<b>Złoto</b> (USD/oz)", "<b>Gold</b> (USD/oz)")),
                ("line", SERIES_COLORS["silver"], L("<b>Srebro</b> (USD/oz)", "<b>Silver</b> (USD/oz)")),
                ("line", SERIES_COLORS["usdpln"], L("<b>USD/PLN</b> – kurs dolara", "<b>USD/PLN</b> – dollar exchange rate")),
                ("dot", C["muted"], L("<b>100</b> = wartość na początku zakresu", "<b>100</b> = value at the start of the range")),
            ], [
                L("Wszystkie serie sprowadzone do 100 – porównujesz zmianę %, nie cenę (110 = +10%, 95 = −5%).",
                  "All series rebased to 100 – you compare the % change, not the price (110 = +10%, 95 = −5%)."),
                L("USD/PLN nad 100 = dolar podrożał, więc giełdowy diesel (ARA) w PLN też drożeje.",
                  "USD/PLN above 100 = the dollar got stronger, so exchange diesel (ARA) in PLN gets pricier too."),
                L("Dymek pokazuje wartość w oryginalnej jednostce.", "The tooltip shows the value in the original unit."),
                range_tip(),
            ])
            fig.add_hline(y=100, line=dict(width=1, dash="dot", color=C["muted"]))
            ui.style_fig(fig, 420, hovermode="x unified", yaxis_title=L("Indeks (początek zakresu = 100)", "Index (start of range = 100)"),
                         legend=dict(orientation="h", y=1.08))
            st.plotly_chart(fig, width="stretch")
            st.caption(L("Każda seria znormalizowana do 100 na swoim pierwszym notowaniu w zakresie. W dymku wartość w oryginalnej jednostce.",
                         "Each series is rebased to 100 at its first quote in the range. The tooltip shows the value in the original unit."))
            cols_help = ui.table_legend({
                c_ser: L("Instrument i jego źródło (Yahoo Finance).", "Instrument and its source (Yahoo Finance)."),
                c_unit: L("W czym podana jest cena: USD za uncję trojańską (złoto, srebro) lub PLN za 1 USD.",
                          "The price unit: USD per troy ounce (gold, silver) or PLN per 1 USD."),
                c_start: L("Pierwsze notowanie w wybranym zakresie dat (w nawiasie data).",
                           "First quote in the selected date range (date in brackets)."),
                c_end: L("Ostatnie notowanie w wybranym zakresie dat.", "Last quote in the selected date range."),
                c_chg: L("O ile procent Koniec jest wyższy (▲) lub niższy (▼) od Startu.",
                         "By how many percent End is higher (▲) or lower (▼) than Start."),
                "Min": L("Najniższe notowanie w zakresie.", "Lowest quote in the range."),
                "Max": L("Najwyższe notowanie w zakresie.", "Highest quote in the range."),
                c_rng: L("O ile % Max był wyższy od Min – miara wahań w zakresie. Im więcej, tym bardziej niestabilny rynek.",
                         "By how many % Max was above Min – a measure of volatility in the range. The higher, the more volatile the market."),
            })
            # złoto/srebro: wzrost = korzystny (turkus); USD/PLN: wzrost = droższy dolar i ARA (czerwony)
            st.dataframe(ui.trend_table(pd.DataFrame(rows), {c_chg: (2, "%")},
                                        good_up=[s != "usdpln" for s in row_series]),
                         hide_index=True, width="stretch", column_config=cols_help)
            st.caption(L("▲ wzrost · ▼ spadek w zakresie. Złoto i srebro: wzrost na turkusowo; "
                         "USD/PLN: wzrost na czerwono (droższy dolar = droższa ARA w PLN).",
                         "▲ up · ▼ down in the range. Gold and silver: a rise in teal; "
                         "USD/PLN: a rise in red (stronger dollar = pricier ARA in PLN)."))
        else:
            st.info(L("Brak notowań w wybranym zakresie.", "No quotes in the selected range."))

st.divider()
st.caption(L(f"Autor aplikacji: **{login_ui.AUTHOR}** · ID Logistics – narzędzie wewnętrzne",
             f"App author: **{login_ui.AUTHOR}** · ID Logistics – internal tool"))

# ================================================================ PLAN TANKOWANIA
with t_plan:
    st.subheader(L("Plan tankowania na trasie", "Refuelling plan along the route"))
    bul = f" {eu_last_day:%d.%m.%Y}" if eu_last_day is not None else ""
    st.caption(L(f"Ile zaoszczędzisz, tankując w najtańszym kraju na trasie. Ceny: średnie krajowe z podatkami z biuletynu KE{' z' + bul if bul else ''}.",
                 f"How much you save by refuelling in the cheapest country on the route. Prices: national averages with taxes from the EC bulletin{' of' + bul if bul else ''}."))
    if not eu_countries or "PL" not in eu_countries:
        st.info(L("Potrzebne ceny z biuletynu KE. Kliknij „Odśwież dane”.", "EC bulletin prices are needed. Click “Refresh data”."))
    else:
        now_eu = eu_now.loc[eu_last_day]
        opts = sorted(eu_countries, key=country)
        fmt_c = lambda c: f"{country(c)} ({ui.num(now_eu[c], 3)})"  # noqa: E731
        c_in, c_out = st.columns([1, 1.25], gap="medium")
        with c_in, st.container(border=True):
            ui.card_title(L("Trasa i pojazd", "Route and vehicle"))
            home = st.selectbox(L("Tankujesz przed wyjazdem w", "You refuel before departure in"), opts,
                                index=opts.index("PL"), format_func=fmt_c, key="plan_home")
            route = st.multiselect(L("Kraje na trasie", "Countries on the route"), [c for c in opts if c != home],
                                   default=[c for c in ["DE", "NL"] if c in opts and c != home],
                                   format_func=fmt_c, key="plan_route")
            p1, p2, p3 = st.columns(3)
            tank = p1.number_input(L("Bak [l]", "Tank [l]"), min_value=50, max_value=2000, value=800, step=50, key="plan_tank")
            km = p2.number_input(L("Trasa [km]", "Route [km]"), min_value=0, max_value=10000, value=900, step=50, key="plan_km")
            cons = p3.number_input("l/100 km", min_value=5.0, max_value=80.0, value=29.0, step=0.5, format="%.1f",
                                   key="plan_cons")
        with c_out, st.container(border=True):
            ui.card_title(L("Wynik", "Result"))
            stops = [home, *route]
            chips = "".join(
                f'<span class="num" style="background:{"#0F3A3A" if now_eu[c] <= now_eu[home] else "#3A1D26"};'
                f'color:{"#BDF2E5" if now_eu[c] <= now_eu[home] else "#FFC2BA"}">{c} {ui.num(now_eu[c], 3)}</span>'
                for c in stops
            )
            ui.html(f'<div class="stops">{chips}<span style="color:{C["muted"]}">EUR/l</span></div>')
            if route:
                cheapest = min(stops, key=lambda c: now_eu[c])
                name = country(cheapest)
                if cheapest == home:
                    alt = min(route, key=lambda c: now_eu[c])
                    saving = (now_eu[alt] - now_eu[home]) * tank
                    reco = L(f"Zatankuj do pełna przed wyjazdem ({name}). Na trasie tankuj tylko tyle, ile trzeba.",
                             f"Fill up before departure ({name}). On the route, refuel only as much as needed.")
                    save_txt = L(f"min. oszczędność vs {country(alt)} ({ui.num(tank)} l)",
                                 f"min. saving vs {country(alt)} ({ui.num(tank)} l)")
                    ui.html(f'<div class="reco"><span class="t">{L("Rekomendacja", "Recommendation")}</span>'
                            f'<span class="m">{reco}</span>'
                            f'<div class="s"><span class="num">{ui.num(saving)} €</span>'
                            f"<span>{save_txt}</span></div></div>")
                else:
                    saving = (now_eu[home] - now_eu[cheapest]) * tank
                    reco = L(f"Przed wyjazdem zatankuj tylko na dojazd – do pełna tankuj po drodze: "
                             f"{name} ({ui.num(now_eu[cheapest], 3)} EUR/l).",
                             f"Before departure, refuel only enough to get there – fill up on the way: "
                             f"{name} ({ui.num(now_eu[cheapest], 3)} EUR/l).")
                    ui.html(f'<div class="reco warn"><span class="t">{L("Tańsze paliwo po drodze", "Cheaper fuel on the way")}</span>'
                            f'<span class="m">{reco}</span>'
                            f'<div class="s"><span class="num">{ui.num(saving)} €</span>'
                            f"<span>{L(f'oszczędności na {ui.num(tank)} l', f'savings on {ui.num(tank)} l')}</span></div></div>")
            else:
                st.caption(L("Dodaj kraje na trasie, żeby porównać ceny.", "Add countries on the route to compare prices."))
            need = km * cons / 100
            reach = tank / cons * 100
            ui.html(f'<div class="fresh num" style="min-height:0;justify-content:space-between">'
                    f"<span>{L('Potrzeba', 'Needed')} ≈ {ui.num(need)} l</span><span>{L('Zasięg', 'Range')} ≈ {ui.num(reach)} km</span></div>")
            n_stops = max(0, math.ceil(need / tank) - 1)  # pełny bak na start + tankowania po drodze
            if n_stops:
                st.caption(L(f"Tankowania po drodze: co najmniej {n_stops} (przy pełnym baku na starcie).",
                             f"Refuelling stops on the way: at least {n_stops} (starting with a full tank)."))
            st.caption(L("Średnie krajowe ceny z podatkami z biuletynu KE. Ceny na kartach flotowych i przy autostradach "
                         "oraz odliczenie VAT mogą zmienić wynik.",
                         "National average prices with taxes from the EC bulletin. Fuel-card and motorway prices "
                         "and VAT deduction may change the result."))
