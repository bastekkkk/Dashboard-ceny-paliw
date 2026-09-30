"""Pobiera wszystkie serie automatyczne i zapisuje je do data/prices.db.

Uruchamianie: `python update_data.py` (ręcznie, cron lub Harmonogram zadań Windows).
Przycisk „Odśwież" w app.py wywołuje tę samą funkcję run_all().
ARA pobierane z OilPriceAPI (wymaga OILPRICEAPI_KEY); awaryjnie wpis ręczny w aplikacji.
Ceny ON w krajach UE: Weekly Oil Bulletin Komisji Europejskiej (co tydzień).
"""
from datetime import date, timedelta

import db
from sources import eu_oil_bulletin as wob
from sources import oilpriceapi, orlen, yahoo


def update_orlen() -> str:
    last = db.last_date(orlen.SERIES)
    # przy kolejnych uruchomieniach pobieramy ostatnie 14 dni (Orlen może korygować notowania)
    start = orlen.FIRST_DATE if last is None else (date.fromisoformat(last) - timedelta(days=14)).isoformat()
    df = orlen.fetch(start)
    n = db.upsert(orlen.SERIES, df, orlen.UNIT, orlen.SOURCE)
    return f"{n} notowań ({df['date'].iloc[0]} – {df['date'].iloc[-1]})"


def update_yahoo(series: str) -> str:
    full = db.last_date(series) is None
    df = yahoo.fetch(series, full=full)
    n = db.upsert(series, df, yahoo.TICKERS[series][1], yahoo.source_label(series))
    return f"{n} notowań ({df['date'].iloc[0]} – {df['date'].iloc[-1]})"


def update_ara() -> str:
    msg = ""
    if db.last_date(oilpriceapi.SERIES) is None:
        hist = oilpriceapi.fetch_history_avg()
        db.upsert(oilpriceapi.SERIES, hist, oilpriceapi.UNIT, oilpriceapi.SOURCE_AVG)
        msg = f"import {len(hist)} średnich dziennych ({hist['date'].iloc[0]} – {hist['date'].iloc[-1]}); "
    last = oilpriceapi.fetch_latest()
    db.upsert(oilpriceapi.SERIES, last, oilpriceapi.UNIT, oilpriceapi.SOURCE_LAST)
    return msg + f"ostatnia cena {last['value'].iloc[0]} USD/t z {last['date'].iloc[0]}"


def update_eu_bulletin() -> str:
    """Biuletyn KE: plik z pełną historią (~4 MB). Nowe notowanie co tydzień, więc przy świeżych danych
    nie pobieramy pliku; przy kolejnych pobraniach zapisujemy ostatnie 8 tygodni (KE koryguje dane wstecz)."""
    pl = wob.series_name("brutto", "PL")
    last = db.last_date(pl)
    if last is not None and (date.today() - date.fromisoformat(last)).days < 10:
        return f"bez zmian – ostatni biuletyn z {last}, kolejny ok. {date.fromisoformat(last) + timedelta(days=10)}"
    cutoff = None if last is None else (date.fromisoformat(last) - timedelta(weeks=8)).isoformat()
    series, fx = wob.fetch()
    n = 0
    for (variant, code), df in [*series.items(), (("fx", "PL"), fx)]:
        if cutoff:
            df = df[df["date"] >= cutoff]
        if df.empty:
            continue
        if variant == "fx":
            n += db.upsert(wob.FX_SERIES, df, wob.FX_UNIT, wob.SOURCE)
        else:
            n += db.upsert(wob.series_name(variant, code), df, wob.UNIT, wob.SOURCE)
    newest = series[("brutto", "PL")]["date"].iloc[-1]
    return f"{n} notowań, {len({c for _, c in series})} krajów/średnich, ostatni biuletyn {newest}"


def run_all() -> dict[str, tuple[bool, str]]:
    """Każde źródło osobno – błąd jednego nie zatrzymuje pozostałych."""
    jobs = {orlen.SERIES: update_orlen, oilpriceapi.SERIES: update_ara, wob.LOG_SERIES: update_eu_bulletin}
    for s in yahoo.TICKERS:
        jobs[s] = lambda s=s: update_yahoo(s)

    results = {}
    for series, job in jobs.items():
        try:
            msg = job()
            results[series] = (True, msg)
        except Exception as e:  # noqa: BLE001 – błąd ma trafić do logu i UI, nie przerwać pętli
            results[series] = (False, f"{type(e).__name__}: {e}")
        db.log_fetch(series, *results[series])
    return results


if __name__ == "__main__":
    for series, (ok, msg) in run_all().items():
        print(f"{'OK ' if ok else 'BŁĄD'} {series:16} {msg}")
