"""Automatyczne odświeżanie danych o 18:30 czasu polskiego (pon–sob), bez zewnętrznego crona.

Dwie warstwy, bo hosting (np. Streamlit Community Cloud) nie ma crona i usypia nieużywaną aplikację:
1. wątek w tle – póki proces serwera żyje, o RUN_AT uruchamia update_data.run_all();
2. nadrabianie – jeśli o RUN_AT aplikacja spała, pierwsze wejście po tej godzinie odświeża dane.
„Zrobione” = w fetch_log jest jakiekolwiek pobranie (auto albo przycisk) po ostatnim terminie.
"""
import threading
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import streamlit as st

import db
import update_data

TZ = ZoneInfo("Europe/Warsaw")
RUN_AT = time(18, 30)  # po zamknięciu ICE – ostatnia cena dnia zamiast śródsesyjnej
RUN_DAYS = {0, 1, 2, 3, 4, 5}  # pon–sob (niedziela: brak notowań)
CHECK_EVERY_S = 60

_lock = threading.Lock()  # jedno pobieranie naraz (wątek, nadrabianie, przycisk)


def _slots_back(now: datetime):
    day = now.date()
    while True:
        slot = datetime.combine(day, RUN_AT, TZ)
        if slot <= now and slot.weekday() in RUN_DAYS:
            yield slot
        day -= timedelta(days=1)


def last_slot(now: datetime | None = None) -> datetime:
    return next(_slots_back(now or datetime.now(TZ)))


def next_slot(now: datetime | None = None) -> datetime:
    now = now or datetime.now(TZ)
    day = now.date()
    while True:
        slot = datetime.combine(day, RUN_AT, TZ)
        if slot > now and slot.weekday() in RUN_DAYS:
            return slot
        day += timedelta(days=1)


def is_due() -> bool:
    """Brak pobrania od ostatniego terminu 18:30 (albo pusta baza)."""
    ts = db.last_fetch_ts()
    if ts is None:
        return True
    last = datetime.fromisoformat(ts).astimezone(TZ)  # fetch_log zapisuje czas lokalny serwera
    return last < last_slot()


def run_if_due() -> dict | None:
    """Pobiera dane, jeśli termin minął i nikt inny właśnie nie pobiera. Zwraca wynik run_all() albo None."""
    if not _lock.acquire(blocking=False):
        return None
    try:
        if not is_due():
            return None
        results = update_data.run_all()
        st.cache_data.clear()
        return results
    finally:
        _lock.release()


def run_now() -> dict:
    """Ręczne odświeżenie (przycisk) – czeka, jeśli właśnie trwa automatyczne."""
    with _lock:
        results = update_data.run_all()
    st.cache_data.clear()
    return results


def is_running() -> bool:
    return _lock.locked()


def _loop(stop: threading.Event) -> None:
    while not stop.wait(CHECK_EVERY_S):
        try:
            run_if_due()
        except Exception:  # noqa: BLE001 – błąd źródła jest już w fetch_log; wątek ma działać dalej
            pass


@st.cache_resource
def start() -> threading.Event:
    """Uruchamia wątek raz na proces serwera (cache_resource = wspólny dla wszystkich sesji)."""
    stop = threading.Event()
    threading.Thread(target=_loop, args=(stop,), name="auto-refresh-1830", daemon=True).start()
    return stop
