"""Dashboard hurtowych cen paliw. Uruchom: streamlit run app.py"""
from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import db
import update_data
from sources import ara_manual, orlen, yahoo

M3_PER_T = 1.1834  # 1 t / 0,845 kg/l = 1183,4 l
PRESETS = {"7D": 7, "1M": 30, "3M": 91, "6M": 182, "1Y": 365, "MAX": None}
COLORS = {"single": "#2a78d6", "gold": "#2a78d6", "silver": "#eb6834", "usdpln": "#1baf7a"}

st.set_page_config(page_title="Ceny paliw – hurt", layout="wide")


@st.cache_data(ttl=900)
def load(series: str) -> pd.DataFrame:
    return db.read_series(series)


def source_warning(series: str) -> None:
    """Pokazuje błąd ostatniego pobrania danej serii (pozostałe sekcje działają dalej)."""
    status = db.last_fetch(series)
    if status and not status["ok"]:
        st.warning(f"Ostatnie pobranie ({status['ts']}) nieudane: {status['message']}. Pokazuję dane zapisane w bazie.")


def range_picker(key: str) -> tuple[date, date] | None:
    choice = st.radio("Zakres", [*PRESETS, "Własny"], index=2, horizontal=True, key=f"{key}_preset")
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


def metric(df: pd.DataFrame, label: str, unit: str, decimals: int = 2) -> None:
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
    st.metric(label, f"{fmt.format(last['value'])} {unit}".replace(",", " "), delta, help=help_txt)
    st.caption(f"Notowanie z **{last['date']:%Y-%m-%d}** · źródło: {last['source']} · jednostka: {unit}")
    if len(df) > 1 and df.iloc[-2]["source"] != last["source"]:
        st.caption(f"⚠️ Zmiana d/d liczona względem innej miary: {df.iloc[-2]['source']}.")


def line_chart(df: pd.DataFrame, name: str, unit: str) -> None:
    fig = go.Figure(
        go.Scatter(
            x=df["date"], y=df["value"], name=name, mode="lines", line=dict(width=2, color=COLORS["single"]),
            customdata=df["source"],
            hovertemplate="%{x|%Y-%m-%d}<br>%{y:,.2f} " + unit + "<br>%{customdata}<extra></extra>",
        )
    )
    fig.update_layout(height=380, margin=dict(l=10, r=10, t=10, b=10), yaxis_title=unit, hovermode="x unified")
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
            line_chart(part, title, unit)
    return df


# ---------------------------------------------------------------- nagłówek
st.title("Hurtowe ceny paliw")
col_btn, col_info = st.columns([1, 4])
if col_btn.button("Odśwież dane", type="primary"):
    with st.spinner("Pobieram dane…"):
        results = update_data.run_all()
    st.cache_data.clear()
    for series, (ok, msg) in results.items():
        (col_info.success if ok else col_info.error)(f"{series}: {msg}")
col_info.caption("Dane czytane z data/prices.db (cache 15 min). Przycisk uruchamia ten sam kod co `python update_data.py`.")

# ---------------------------------------------------------------- 1. Orlen
st.header("1. Ekodiesel ORLEN – cena hurtowa")
orlen_df = series_section(orlen.SERIES, "Ekodiesel ORLEN (hurt)", orlen.UNIT, 0)
if not orlen_df.empty:
    st.caption(
        f"Historia z API Orlenu od {orlen_df['date'].min():%Y-%m-%d} ({len(orlen_df)} notowań). "
        "Wg Orlenu: cena bez VAT, za paliwo w temperaturze referencyjnej 15°C; akcyza, opłata paliwowa i zapasowa "
        "wymienione jako składniki kształtujące cenę hurtową. Notowanie obowiązuje do kolejnej zmiany."
    )

# ---------------------------------------------------------------- 2. ARA
st.header("2. ARA – ICE Low Sulphur Gasoil")
st.caption(
    "Automatycznie z OilPriceAPI (kod GASOIL_USD, ICE LS Gasoil), **USD/t**, przez `update_data.py`: "
    "ostatnia transakcja dnia (uruchomienie po zamknięciu ICE ≈ cena zamknięcia; wcześniej wartość śródsesyjna). "
    "Notowania ze źródłem „średnia dzienna” pochodzą z jednorazowego importu historii – to nie są ceny zamknięcia. "
    "Źródło każdego punktu widać w dymku wykresu."
)
source_warning(ara_manual.SERIES)
with st.expander("Wpis ręczny (awaryjnie, gdy API nie działa)"), st.form("ara_form", clear_on_submit=True):
    st.caption(f"Cena z [TradingView ICEEUR:ULS1!]({ara_manual.URL}). Kolejne pobranie z API nadpisze wpis z tego samego dnia.")
    c1, c2, c3 = st.columns([2, 2, 1])
    ara_day = c1.date_input("Data notowania", value=date.today(), format="YYYY-MM-DD")
    ara_price = c2.number_input("Cena zamknięcia / ostatnia [USD/t]", min_value=0.0, step=0.25, format="%.2f")
    if c3.form_submit_button("Zapisz"):
        try:
            ara_manual.save(ara_day, ara_price)
            st.cache_data.clear()
            st.success(f"Zapisano {ara_day}: {ara_price:.2f} USD/t")
        except ValueError as e:
            st.error(str(e))

ara_df = load(ara_manual.SERIES)
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

# ---------------------------------------------------------------- 3. Złoto / srebro / USD/PLN
st.header("3. Złoto, srebro, USD/PLN")
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
        metric(df, label, unit, 4 if series == "usdpln" else 2)
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
                line=dict(width=2, color=COLORS[series]), customdata=part["value"],
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
        fig.add_hline(y=100, line=dict(width=1, dash="dot", color="gray"))
        fig.update_layout(
            height=420, margin=dict(l=10, r=10, t=10, b=10), hovermode="x unified",
            yaxis_title="Indeks (początek zakresu = 100)", legend=dict(orientation="h", y=1.08),
        )
        st.plotly_chart(fig, width="stretch")
        st.caption("Każda seria znormalizowana do 100 na swoim pierwszym notowaniu w zakresie. W dymku wartość w oryginalnej jednostce.")
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    else:
        st.info("Brak notowań w wybranym zakresie.")

# ---------------------------------------------------------------- Premia PL vs ARA
st.header("Premia PL vs ARA")
fx_df = frames.get("usdpln", pd.DataFrame())
if ara_df.empty or orlen_df.empty or fx_df.empty:
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
