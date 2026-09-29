"""ARA – ICE Low Sulphur Gasoil (USD/t) z OilPriceAPI, kod GASOIL_USD.

Wymaga darmowego klucza API w zmiennej środowiskowej OILPRICEAPI_KEY
(rejestracja: https://www.oilpriceapi.com/auth/signup). Plan Free: 50 zapytań/dzień,
skrypt zużywa 1 zapytanie na uruchomienie (2 przy pierwszym).
Warunki OilPriceAPI: notowania giełdowe (ICE) tylko do użytku wewnętrznego –
bez publicznego wyświetlania i redystrybucji.
"""
import os

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

SERIES = "ara_gasoil"  # ta sama seria co wpis ręczny (sources/ara_manual.py)
UNIT = "USD/t"
SOURCE = "OilPriceAPI GASOIL_USD (ICE LS Gasoil)"
CODE = "GASOIL_USD"
BASE_URL = "https://api.oilpriceapi.com/v1/prices"
TIMEOUT = 30


def _session(key: str) -> requests.Session:
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504])
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers.update({"Authorization": f"Token {key}", "Accept": "application/json"})
    return s


def _parse(rows: list[dict]) -> pd.DataFrame:
    """Jeden wiersz na dzień notowania; pomija wiersze 'synthetic' (przeniesione, nie notowane)."""
    out = []
    for row in rows:
        if row.get("code") not in (None, CODE):
            raise RuntimeError(f"Nieoczekiwany kod w odpowiedzi: {row.get('code')}")
        if row.get("currency") not in (None, "USD"):
            raise RuntimeError(f"Nieoczekiwana waluta: {row.get('currency')}")
        unit = (row.get("unit") or "").lower()
        if unit and "ton" not in unit:
            raise RuntimeError(f"Nieoczekiwana jednostka: {row.get('unit')} (oczekiwano USD/t)")
        if row.get("synthetic"):
            continue
        stamp = row.get("source_date") or row.get("as_of") or row.get("observed_at") or row.get("created_at")
        if not stamp or row.get("price") is None:
            continue
        out.append({"date": stamp[:10], "stamp": stamp, "value": float(row["price"])})
    if not out:
        raise RuntimeError("OilPriceAPI nie zwróciło żadnego rzeczywistego notowania GASOIL_USD")
    df = pd.DataFrame(out).sort_values("stamp")
    return df.drop_duplicates("date", keep="last")[["date", "value"]].reset_index(drop=True)


def fetch(full: bool) -> pd.DataFrame:
    key = os.environ.get("OILPRICEAPI_KEY", "").strip()
    if not key:
        raise RuntimeError("Brak klucza API: ustaw zmienną środowiskową OILPRICEAPI_KEY (patrz README).")
    endpoint = "past_month" if full else "past_week"
    r = _session(key).get(
        f"{BASE_URL}/{endpoint}", params={"by_code": CODE, "interval": "daily", "per_page": 100}, timeout=TIMEOUT
    )
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
    data = r.json().get("data") or {}
    rows = data.get("prices") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise RuntimeError(f"Nieoczekiwany format odpowiedzi: {str(data)[:200]}")
    return _parse(rows)
