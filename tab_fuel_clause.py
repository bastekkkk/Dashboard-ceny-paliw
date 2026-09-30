"""Zakładka „Korekta paliwowa”: szybki kalkulator klauzuli + umowy klientów z korektą na wybrany miesiąc."""
import io
from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import fuel_clause as fc
import ui
from ui import C

LAG_LABELS = {1: "M−1", 0: "M"}
EDITOR_COLS = {
    "client": "Klient", "index_key": "Indeks", "base_value": "Cena bazowa", "base_month": "Miesiąc bazowy",
    "fuel_share": "Udział paliwa %", "threshold": "Próg %", "mode": "Tryb progu", "lag": "Okres odniesienia",
    "rate": "Stawka bazowa", "rate_unit": "Jednostka stawki", "note": "Uwagi",
}


@st.cache_data(ttl=900)
def _monthly(key: str) -> pd.DataFrame:
    return fc.monthly(key, fc.load_index(key))


def _dec(key: str) -> int:
    return 2 if key == fc.ORLEN_KEY else 3


def _pct(x: float | None, decimals: int = 2) -> str:
    return "—" if x is None or pd.isna(x) else f"{ui.num(x * 100, decimals, sign=True)}%"


def _m(p: pd.Period) -> str:
    return p.strftime("%m.%Y")


def _explain() -> None:
    with st.expander("Jak działa klauzula paliwowa i na co uważać"):
        ui.html("""
    <div class="explain">
      <div>
        <h4>Wzór</h4>
        <p><span class="num">zmiana = średnia indeksu w okresie odniesienia ÷ cena bazowa − 1</span></p>
        <p><span class="num">korekta stawki = zmiana × udział paliwa w stawce</span></p>
        <p><span class="num">nowa stawka = stawka bazowa × (1 + korekta)</span></p>
        <p>Przykład: paliwo +10% względem bazy, udział paliwa 30% → stawka +3%.</p>
      </div>
      <div>
        <h4>Parametry umowy</h4>
        <ul>
          <li><b>Cena bazowa</b> – wartość indeksu wpisana w umowie albo średnia z miesiąca podpisania (miesiąc bazowy).</li>
          <li><b>Udział paliwa</b> – część stawki, którą stanowi paliwo; w FTL zwykle 25–35%.</li>
          <li><b>Próg</b> – zmiana indeksu, poniżej której stawka się nie zmienia. „Pełna”: po przekroczeniu
          liczy się cała zmiana; „nadwyżka”: tylko część ponad próg.</li>
          <li><b>Okres odniesienia</b> – M−1: rozliczenie za wrzesień liczone ze średniej sierpnia (pełne dane);
          M: ze średniej bieżącego miesiąca (wynik wstępny do końca miesiąca).</li>
        </ul>
      </div>
      <div>
        <h4>Indeksy i średnie</h4>
        <ul>
          <li><b>ORLEN hurt</b> – średnia z dni kalendarzowych: każdy dzień liczy się ceną obowiązującą tego dnia
          (cena obowiązuje do kolejnej zmiany). Najczęstszy indeks w umowach krajowych.</li>
          <li><b>Stacje (biuletyn KE)</b> – średnia z tygodniowych biuletynów z danego miesiąca. Miesiąc jest pełny,
          gdy jest biuletyn z jego ostatniego poniedziałku (publikacja zwykle w czwartek).</li>
          <li>Wybierz indeks i sposób liczenia średniej zgodny z brzmieniem umowy – inny indeks daje inną korektę.</li>
        </ul>
      </div>
    </div>""")


def _calculator(opts: dict[str, str]) -> None:
    with st.container(border=True):
        ui.card_title("Szybki kalkulator", "sprawdź klauzulę przed ofertą albo renegocjacją – bez zapisywania umowy")
        c1, c2, c3 = st.columns([2.2, 1.2, 1])
        key = c1.selectbox("Indeks paliwowy", list(opts), format_func=opts.get, key="fc_index")
        mon = _monthly(key)
        if mon.empty:
            st.info("Brak notowań tego indeksu w bazie. Kliknij „Odśwież dane”.")
            return
        unit, dec = fc.index_unit(key), _dec(key)
        kind = c2.segmented_control("Cena bazowa", ["Miesiąc bazowy", "Wartość z umowy"],
                                    default="Miesiąc bazowy", key="fc_base_kind") or "Miesiąc bazowy"
        full = [p for p in mon.index[::-1] if mon.at[p, "complete"]]
        if not full:
            st.info("Indeks nie ma jeszcze żadnego pełnego miesiąca notowań.")
            return
        c = {"client": "kalkulator", "index_key": key}
        if kind == "Miesiąc bazowy":
            bm = c3.selectbox("Miesiąc bazowy", full, index=min(12, len(full) - 1), format_func=_m, key="fc_base_month")
            c.update(base_value=None, base_month=str(bm))
        else:
            default = round(float(mon.loc[full[0], "avg"]), dec)
            c.update(base_value=c3.number_input(f"Cena bazowa [{unit}]", min_value=0.0, value=default,
                                                format=f"%.{dec}f", key="fc_base_value"), base_month=None)

        p1, p2, p3, p4, p5, p6 = st.columns([1, 1, 1.3, 1.5, 1, 1])
        c["fuel_share"] = p1.number_input("Udział paliwa %", 1.0, 100.0, 30.0, 1.0, key="fc_share")
        c["threshold"] = p2.number_input("Próg %", 0.0, 50.0, 0.0, 0.5, key="fc_thr")
        c["mode"] = p3.selectbox("Tryb progu", list(fc.MODES), format_func=lambda m: f"{m} – {fc.MODES[m]}",
                                 key="fc_mode")
        c["lag"] = p4.selectbox("Okres odniesienia", list(fc.LAGS), format_func=fc.LAGS.get, key="fc_lag")
        c["rate"] = p5.number_input("Stawka bazowa", 0.0, 1e7, 1.20, 0.01, format="%.2f", key="fc_rate")
        c["rate_unit"] = p6.selectbox("Jednostka", fc.RATE_UNITS, key="fc_rate_unit")

        bill = pd.Period(date.today(), "M")
        r = fc.calc(c, mon, bill)
        if r["ref"] is None:
            st.warning(f"Nie da się policzyć korekty: {r['note']}.")
            return
        ui.html(f"""
        <div class="formula">
          <div class="box"><small>Średnia indeksu {_m(r['ref_month'])}</small><b class="num">{ui.num(r['ref'], dec)}</b><span class="u">{unit}</span></div>
          <div class="op">÷</div>
          <div class="box"><small>Cena bazowa ({r['base_note']})</small><b class="num">{ui.num(r['base'], dec)}</b><span class="u">{unit}</span></div>
          <div class="op">→</div>
          <div class="box"><small>Zmiana indeksu</small><b class="num">{_pct(r['change'])}</b></div>
          <div class="op">×</div>
          <div class="box"><small>Udział paliwa (próg {ui.num(c['threshold'], 1)}%)</small><b class="num">{ui.num(c['fuel_share'], 0)}%</b></div>
          <div class="op">=</div>
          <div class="box res"><small>Korekta stawki za {_m(bill)}</small><b class="num">{_pct(r['adj'])}</b>
            <span class="u">{ui.num(c['rate'], 2)} → {ui.num(r['new_rate'], 3)} {c['rate_unit']}</span></div>
        </div>""")
        if r["note"]:
            st.caption(f"Uwaga: {r['note']}.")

        g, t = st.columns([1.2, 1], gap="medium")
        with g:
            part = mon.tail(24)
            x = part.index.to_timestamp()
            base, thr = r["base"], c["threshold"] / 100
            fig = go.Figure()
            if thr > 0:
                fig.add_hrect(y0=base * (1 - thr), y1=base * (1 + thr), fillcolor="rgba(169,191,214,0.10)",
                              line_width=0, annotation_text=f"próg ±{ui.num(c['threshold'], 1)}% – bez korekty",
                              annotation_position="top left", annotation_font_color=C["muted"])
            fig.add_hline(y=base, line=dict(width=1.2, dash="dash", color=C["muted"]),
                          annotation_text=f"baza {ui.num(base, dec)}", annotation_position="bottom left",
                          annotation_font_color=C["muted"])
            fig.add_trace(go.Scatter(
                x=x, y=part["avg"], mode="lines+markers", line=dict(width=2, color=C["orlen"]),
                marker=dict(size=7, color=[C["orlen"] if ok else C["bg"] for ok in part["complete"]],
                            line=dict(width=1.5, color=C["orlen"])),
                hovertemplate="%{x|%m.%Y}<br>średnia %{y:,." + str(dec) + "f} " + unit + "<extra></extra>",
            ))
            ui.style_fig(fig, 320, showlegend=False, yaxis_title=f"średnia miesięczna [{unit}]",
                         xaxis=dict(tickformat="%m.%Y"), hovermode="x")
            st.plotly_chart(fig, width="stretch")
            st.caption("Średnie miesięczne indeksu (24 mies.); pusty punkt = miesiąc niepełny. "
                       "Szare pasmo = próg, w którym stawka się nie zmienia.")
        with t:
            h = fc.history(c, mon, 12)
            st.dataframe(pd.DataFrame({
                "Rozliczenie": h["bill"].map(_m),
                "Średnia z": h["ref_month"].map(_m),
                "Indeks": h["ref"].round(dec),
                "Zmiana": h["change"].map(_pct),
                "Korekta": h["adj"].map(_pct),
                "Stawka": h["new_rate"].round(3),
            }), hide_index=True, width="stretch", height=455)


def _editor_view(df: pd.DataFrame, opts: dict[str, str]) -> pd.DataFrame:
    view = df.copy()
    view["index_key"] = view["index_key"].map(lambda k: opts.get(k, k))
    view["lag"] = view["lag"].map(lambda v: LAG_LABELS.get(int(v), "M−1") if pd.notna(v) else "M−1")
    for col in ("base_value", "fuel_share", "threshold", "rate"):
        view[col] = pd.to_numeric(view[col], errors="coerce").astype(float)
    for col in ("client", "index_key", "base_month", "mode", "lag", "rate_unit", "note"):
        view[col] = view[col].astype(object)
    return view


def _from_view(view: pd.DataFrame) -> pd.DataFrame:
    back = {v: k for k, v in LAG_LABELS.items()}
    return view.assign(lag=view["lag"].map(lambda v: back.get(v, v)))


def _summary(contracts: pd.DataFrame, opts: dict[str, str], bill: pd.Period) -> pd.DataFrame:
    rows = []
    for c in contracts.to_dict("records"):
        mon = _monthly(c["index_key"])
        r = fc.calc(c, mon, bill)
        dec = _dec(c["index_key"])
        rows.append({
            "Klient": c["client"],
            "Indeks": opts.get(c["index_key"], c["index_key"]),
            "Baza": None if r["base"] is None else round(r["base"], dec),
            "Średnia z": _m(r["ref_month"]),
            "Średnia indeksu": None if r["ref"] is None else round(r["ref"], dec),
            "Zmiana indeksu %": None if r["change"] is None else round(r["change"] * 100, 2),
            "Udział paliwa %": c["fuel_share"],
            "Próg %": c["threshold"],
            "Korekta stawki %": None if r["adj"] is None else round(r["adj"] * 100, 2),
            "Stawka bazowa": c["rate"],
            "Nowa stawka": None if r["new_rate"] is None else round(r["new_rate"], 3),
            "Jednostka": c["rate_unit"],
            "Uwagi": "; ".join(x for x in [r["note"], c["note"]] if isinstance(x, str) and x),
        })
    return pd.DataFrame(rows)


def _xlsx(df: pd.DataFrame, bill: pd.Period) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        df.to_excel(xw, sheet_name=f"Korekta {bill.strftime('%Y-%m')}", index=False)
        ws = xw.sheets[f"Korekta {bill.strftime('%Y-%m')}"]
        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width = min(48, max(10, *(len(str(c.value or "")) for c in col)) + 2)
        ws.freeze_panes = "B2"
    return buf.getvalue()


def _contracts(opts: dict[str, str]) -> None:
    with st.container(border=True):
        ui.card_title("Umowy klientów", "parametry klauzul w jednym miejscu · korekta dla wszystkich umów na wybrany miesiąc")
        contracts = fc.load_contracts()
        edited = st.data_editor(
            _editor_view(contracts, opts), num_rows="dynamic", hide_index=True, width="stretch", key="fc_editor",
            column_order=list(EDITOR_COLS),
            column_config={
                "client": st.column_config.TextColumn(EDITOR_COLS["client"], required=True),
                "index_key": st.column_config.SelectboxColumn(EDITOR_COLS["index_key"], options=list(opts.values()),
                                                              default=opts[fc.ORLEN_KEY], width="large"),
                "base_value": st.column_config.NumberColumn(EDITOR_COLS["base_value"], format="%.3f",
                                                            help="W jednostce indeksu. Puste = średnia z miesiąca bazowego."),
                "base_month": st.column_config.TextColumn(EDITOR_COLS["base_month"], help="RRRR-MM, np. 2026-01"),
                "fuel_share": st.column_config.NumberColumn(EDITOR_COLS["fuel_share"], min_value=0.0, max_value=100.0,
                                                            default=30.0, format="%.1f"),
                "threshold": st.column_config.NumberColumn(EDITOR_COLS["threshold"], min_value=0.0, max_value=99.0,
                                                           default=0.0, format="%.1f"),
                "mode": st.column_config.SelectboxColumn(EDITOR_COLS["mode"], options=list(fc.MODES), default="pełna"),
                "lag": st.column_config.SelectboxColumn(EDITOR_COLS["lag"], options=list(LAG_LABELS.values()),
                                                        default="M−1"),
                "rate": st.column_config.NumberColumn(EDITOR_COLS["rate"], format="%.3f"),
                "rate_unit": st.column_config.SelectboxColumn(EDITOR_COLS["rate_unit"], options=fc.RATE_UNITS,
                                                              default="EUR/km"),
                "note": st.column_config.TextColumn(EDITOR_COLS["note"]),
            },
        )
        b1, b2, b3 = st.columns([1, 1, 2.4], vertical_alignment="center")
        if b1.button("Zapisz umowy", type="primary", icon=":material/save:", width="stretch"):
            clean, errors = fc.validate(_from_view(edited))
            if errors:
                st.error("Nie zapisano – popraw:\n\n" + "\n".join(f"- {e}" for e in errors))
            else:
                fc.save_contracts(clean)
                st.session_state.pop("fc_editor", None)
                st.toast(f"Zapisano umowy: {len(clean)}")
                st.rerun()
        b2.download_button("Kopia CSV", contracts.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig"),
                           "umowy_korekta_paliwowa.csv", "text/csv", icon=":material/download:", width="stretch",
                           disabled=contracts.empty)
        b3.caption("Umowy są w bazie aplikacji. Na hostingu bez trwałego dysku (np. Streamlit Community Cloud) "
                   "baza znika przy restarcie – po zmianach pobierz kopię CSV, a po restarcie wczytaj ją niżej.")
        with st.expander("Wczytaj umowy z CSV (zastępuje listę)"):
            up = st.file_uploader("Plik CSV z kopii (separator ; lub ,)", type=["csv"], key="fc_upload")
            if up is not None:
                raw = pd.read_csv(up, sep=None, engine="python", dtype=str, encoding="utf-8-sig")
                clean, errors = fc.validate(raw.rename(columns={v: k for k, v in EDITOR_COLS.items()}))
                for e in errors:
                    st.warning(e)
                st.caption(f"Poprawnych wierszy: {len(clean)}")
                if st.button("Zastąp listę umów", disabled=clean.empty):
                    fc.save_contracts(clean)
                    st.session_state.pop("fc_editor", None)
                    st.rerun()

        if contracts.empty:
            st.info("Dodaj pierwszą umowę w tabeli powyżej (wiersz „+”) i kliknij „Zapisz umowy”.")
            return
        st.markdown("#### Korekta na miesiąc rozliczeniowy")
        cur = pd.Period(date.today(), "M")
        bill = st.selectbox("Miesiąc rozliczenia", [cur - i for i in range(13)], format_func=_m, key="fc_bill")
        summ = _summary(contracts, opts, bill)
        st.dataframe(summ, hide_index=True, width="stretch", column_config={
            "Zmiana indeksu %": st.column_config.NumberColumn(format="%+.2f"),
            "Korekta stawki %": st.column_config.NumberColumn(format="%+.2f"),
        })
        ok = summ["Korekta stawki %"].notna()
        st.caption(f"{int(ok.sum())} z {len(summ)} umów policzonych. Stawki zaokrąglone do 0,001 – przy fakturowaniu "
                   "stosuj zaokrąglenie z umowy.")
        st.download_button("Zestawienie do fakturowania (Excel)", _xlsx(summ, bill),
                           f"korekta_paliwowa_{bill.strftime('%Y-%m')}.xlsx",
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           icon=":material/table:")


def render() -> None:
    st.subheader("Korekta paliwowa – klauzula paliwowa w stawkach")
    st.caption("Ile zmienia się stawka przewozowa, gdy zmienia się cena paliwa. Indeksy z bazy dashboardu: "
               "hurt ORLEN (dziennie) i ceny ON na stacjach w krajach UE (biuletyn KE, tygodniowo).")
    _explain()
    opts = fc.index_options()
    _calculator(opts)
    _contracts(opts)
