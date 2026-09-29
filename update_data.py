"""Pobiera wszystkie serie automatyczne i zapisuje je do data/prices.db.

Uruchamianie: `python update_data.py` (ręcznie, cron lub Harmonogram zadań Windows).
Przycisk „Odśwież" w app.py wywołuje tę samą funkcję run_all().
ARA nie jest tu pobierane – wpisuje się je ręcznie w aplikacji (patrz sources/ara_manual.py).
"""
from datetime import date, timedelta

import db
from sources import orlen, yahoo


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


def run_all() -> dict[str, tuple[bool, str]]:
    """Każde źródło osobno – błąd jednego nie zatrzymuje pozostałych."""
    jobs = {orlen.SERIES: update_orlen}
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
