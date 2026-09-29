"""Złoto, srebro, USD/PLN z Yahoo Finance (yfinance), dzienne ceny zamknięcia."""
import time

import pandas as pd
import yfinance as yf

# series -> (ticker, jednostka, opis)
TICKERS = {
    "gold": ("GC=F", "USD/oz", "Złoto (COMEX GC=F, kontrakt ciągły)"),
    "silver": ("SI=F", "USD/oz", "Srebro (COMEX SI=F, kontrakt ciągły)"),
    "usdpln": ("USDPLN=X", "PLN za 1 USD", "USD/PLN (USDPLN=X)"),
}
TIMEOUT = 30
ATTEMPTS = 3


def fetch(series: str, full: bool) -> pd.DataFrame:
    ticker, _, _ = TICKERS[series]
    period = "max" if full else "3mo"
    last_err = None
    for attempt in range(ATTEMPTS):
        try:
            hist = yf.Ticker(ticker).history(period=period, interval="1d", auto_adjust=False, timeout=TIMEOUT)
            if hist.empty:
                raise RuntimeError(f"Yahoo nie zwróciło danych dla {ticker}")
            hist = hist.dropna(subset=["Close"])
            # data notowania w strefie czasowej giełdy (bez konwersji do UTC)
            return pd.DataFrame(
                {"date": [d.strftime("%Y-%m-%d") for d in hist.index], "value": hist["Close"].to_numpy()}
            )
        except Exception as e:  # noqa: BLE001 – każdy błąd źródła ma trafić do logu
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"{ticker}: {last_err}")


def source_label(series: str) -> str:
    return f"Yahoo Finance – {TICKERS[series][0]}"
