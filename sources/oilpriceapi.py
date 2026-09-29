"""ARA – ICE Low Sulphur Gasoil (USD/t) z OilPriceAPI, kod GASOIL_USD.

Wymaga klucza API w zmiennej środowiskowej OILPRICEAPI_KEY
(rejestracja: https://www.oilpriceapi.com/auth/signup). Skrypt zużywa 1 zapytanie
na uruchomienie (2 przy pierwszym).

- Codziennie: /latest = ostatnia transakcja; uruchomione po zamknięciu ICE ≈ cena zamknięcia.
- Tylko przy pustej bazie: /past_month z interval=daily = ŚREDNIE dzienne (kubełki UTC),
  bez weekendów (niedzielna sesja wieczorna ICE należy do poniedziałku). Oznaczone osobnym źródłem.
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
SOURCE_LAST = "OilPriceAPI GASOIL_USD – ostatnia cena dnia"
SOURCE_AVG = "OilPriceAPI GASOIL_USD – średnia dzienna (import historii)"
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


def _get(endpoint: str, params: dict):
    key = os.environ.get("OILPRICEAPI_KEY", "").strip()
    if not key:
        raise RuntimeError("Brak klucza API: ustaw zmienną środowiskową OILPRICEAPI_KEY (patrz README).")
    r = _session(key).get(f"{BASE_URL}/{endpoint}", params={"by_code": CODE, **params}, timeout=TIMEOUT)
    if r.status_code == 402:
        raise RuntimeError("HTTP 402: plan OilPriceAPI nie obejmuje GASOIL_USD lub wyczerpano limit (koniec triala?)")
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
    return r.json().get("data")


def fetch_latest() -> pd.DataFrame:
    """Ostatnia transakcja; data = dzień notowania wg as_of (UTC)."""
    data = _get("latest", {})
    if not isinstance(data, dict) or "price" not in data:
        raise RuntimeError(f"Nieoczekiwany format odpowiedzi: {str(data)[:200]}")
    return _parse([data])


def fetch_history_avg() -> pd.DataFrame:
    """Średnie dzienne z ostatnich ~30 dni, tylko dni pon–pt."""
    data = _get("past_month", {"interval": "daily", "per_page": 100}) or {}
    rows = data.get("prices") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise RuntimeError(f"Nieoczekiwany format odpowiedzi: {str(data)[:200]}")
    df = _parse(rows)
    return df[pd.to_datetime(df["date"]).dt.dayofweek < 5].reset_index(drop=True)
