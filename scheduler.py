"""Automatyczne odświeżanie danych o 18:30 czasu polskiego (pon–sob), bez zewnętrznego crona.

Dwie warstwy, bo hosting (np. Streamlit Community Cloud) nie ma crona i usypia nieużywaną aplikację:
1. wątek w tle – póki proces serwera żyje, o RUN_AT uruchamia update_data.run_all();
2. nadrabianie – jeśli o RUN_AT aplikacja spała, pierwsze wejście po tej godzinie odświeża dane.
Każde pobranie (auto albo przycisk) zapisuje w fetch_log znacznik MARKER: ok = wszystkie źródła OK.
Termin jest „zaliczony” udanym pobraniem po ostatnim 18:30. Nieudane (np. API ORLEN nie odpowiada) jest
ponawiane co RETRY_EVERY, najwyżej MAX_ATTEMPTS razy na termin – żeby trwała awaria jednego źródła
(np. brak klucza OilPriceAPI) nie odpalała pobierania co minutę.
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
MARKER = "auto_refresh"  # wpis w fetch_log podsumowujący całe pobranie
RETRY_EVERY = timedelta(minutes=30)
MAX_ATTEMPTS = 4  # na jeden termin 18:30

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


def _server_local(dt: datetime) -> str:
    """fetch_log zapisuje naiwny czas lokalny serwera – do porównań sprowadzamy termin do tego formatu."""
    return dt.astimezone().replace(tzinfo=None).isoformat(timespec="seconds")


def is_due(now: datetime | None = None) -> bool:
    """Po ostatnim terminie 18:30 nie było udanego pobrania, a limit ponowień nie jest wyczerpany."""
    now = now or datetime.now(TZ)
    runs = db.fetches_since(MARKER, _server_local(last_slot(now)))
    if any(ok for _, ok in runs):
        return False
    if not runs:
        return True
    if len(runs) >= MAX_ATTEMPTS:
        return False
    last_try = datetime.fromisoformat(runs[-1][0]).astimezone(TZ)
    return now - last_try >= RETRY_EVERY


def _run() -> dict:
    results = update_data.run_all()
    failed = [s for s, (ok, _) in results.items() if not ok]
    db.log_fetch(MARKER, not failed, "wszystkie źródła OK" if not failed else "błąd: " + ", ".join(failed))
    st.cache_data.clear()
    return results


def run_if_due() -> dict | None:
    """Pobiera dane, jeśli termin minął i nikt inny właśnie nie pobiera. Zwraca wynik run_all() albo None."""
    if not _lock.acquire(blocking=False):
        return None
    try:
        if not is_due():
            return None
        return _run()
    finally:
        _lock.release()


def run_now() -> dict:
    """Ręczne odświeżenie (przycisk) – czeka, jeśli właśnie trwa automatyczne."""
    with _lock:
        return _run()


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
