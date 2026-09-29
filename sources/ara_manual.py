"""ARA – ICE Low Sulphur Gasoil Futures (USD/t), wpisywane ręcznie raz dziennie.

Brak darmowego, niezablokowanego źródła automatycznego (zweryfikowano: Yahoo G=F -> 404,
Stooq -> challenge JS). Użytkownik przepisuje cenę zamknięcia / ostatnią cenę z
https://www.tradingview.com/symbols/ICEEUR-ULS1!/ (kontrakt ciągły front-month).
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
