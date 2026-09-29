"""ARA – awaryjny wpis ręczny (USD/t), gdy OilPriceAPI nie działa (główne źródło: sources/oilpriceapi.py).

Użytkownik przepisuje cenę zamknięcia / ostatnią cenę z
https://www.tradingview.com/symbols/ICEEUR-ULS1!/ (kontrakt ciągły front-month).
TradingView nie jest pobierany automatycznie – regulamin zabrania "machine-driven" użycia danych.
"""
from datetime import date

import pandas as pd

import db

SERIES = "ara_gasoil"
UNIT = "USD/t"
SOURCE = "TradingView ICEEUR:ULS1! – wpis ręczny"
URL = "https://www.tradingview.com/symbols/ICEEUR-ULS1!/"


def save(day: date, price_usd_t: float) -> None:
    if price_usd_t <= 0:
        raise ValueError("Cena musi być dodatnia.")
    db.upsert(SERIES, pd.DataFrame({"date": [day.isoformat()], "value": [price_usd_t]}), UNIT, SOURCE)
    db.log_fetch(SERIES, True, f"wpis ręczny {day.isoformat()}: {price_usd_t} {UNIT}")
